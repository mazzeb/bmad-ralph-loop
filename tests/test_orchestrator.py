"""Integration tests for run_stories.orchestrator with mocked subprocess."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from run_stories.models import (
    MarkerEvent,
    MarkerType,
    SessionConfig,
    StepKind,
    StepResult,
)
from run_stories.orchestrator import run_stories
from run_stories.tui import TUI


@pytest.fixture
def tmp_project(tmp_path):
    """Create a minimal project directory with required files."""
    # Sprint status
    status_dir = tmp_path / "_bmad-output" / "implementation-artifacts"
    status_dir.mkdir(parents=True)
    log_dir = status_dir / "logs"
    log_dir.mkdir()

    sprint_status = status_dir / "sprint-status.yaml"
    sprint_status.write_text(
        "generated: 2026-02-21\n"
        "project: test\n"
        "development_status:\n"
        "  epic-1: in-progress\n"
        "  1-1-done-story: done\n"
        "  1-2-next-story: backlog\n"
        "  1-3-another-story: backlog\n"
    )

    # Prompt files
    for name in ["PROMPT-create-story.md", "PROMPT-dev-story.md", "PROMPT-code-review.md"]:
        (tmp_path / name).write_text(f"# {name}\nDo the thing.")

    return tmp_path


@pytest.fixture
def config(tmp_project):
    return SessionConfig(
        project_dir=tmp_project,
        max_stories=1,
        max_turns_cs=10,
        max_turns_ds=10,
        max_turns_cr=10,
        max_review_rounds=3,
    )


@pytest.fixture
def tui():
    return TUI(show_thinking=False)


def _make_step_result(kind: StepKind, story_key: str, markers: list[MarkerEvent] | None = None) -> StepResult:
    return StepResult(
        kind=kind,
        story_key=story_key,
        duration_ms=10000,
        num_turns=5,
        cost_usd=1.0,
        markers_detected=markers or [],
        success=True,
    )


def _update_status(path: Path, old: str, new: str):
    """Helper to update sprint status file."""
    content = path.read_text()
    path.write_text(content.replace(old, new))


class TestHappyPath:
    """CS → DS → CR all succeed, story done, commit runs."""

    @pytest.mark.asyncio
    async def test_full_story_cycle(self, config, tui, tmp_project):
        sprint_status = tmp_project / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml"
        story_file = tmp_project / "_bmad-output" / "implementation-artifacts" / "1-2-next-story.md"

        call_count = 0

        async def mock_run_session(**kwargs):
            nonlocal call_count
            call_count += 1
            step_kind = kwargs.get("step_kind")
            # Simulate status updates
            if step_kind == StepKind.CS:
                _update_status(sprint_status, "1-2-next-story: backlog", "1-2-next-story: ready-for-dev")
                story_file.write_text("# Story 1.2")
                return _make_step_result(StepKind.CS, "1-2-next-story", [
                    MarkerEvent(marker_type=MarkerType.CREATE_STORY_COMPLETE, payload="1-2-next-story"),
                ])
            elif step_kind == StepKind.DS:
                _update_status(sprint_status, "1-2-next-story: ready-for-dev", "1-2-next-story: review")
                return _make_step_result(StepKind.DS, "1-2-next-story", [
                    MarkerEvent(marker_type=MarkerType.DEV_STORY_COMPLETE, payload="1-2-next-story"),
                ])
            elif step_kind == StepKind.CR:
                _update_status(sprint_status, "1-2-next-story: review", "1-2-next-story: done")
                return _make_step_result(StepKind.CR, "1-2-next-story", [
                    MarkerEvent(marker_type=MarkerType.CODE_REVIEW_APPROVED, payload="1-2-next-story"),
                ])
            return _make_step_result(step_kind or StepKind.CS, kwargs.get("story_key", ""))

        async def mock_commit_session(**kwargs):
            return _make_step_result(StepKind.COMMIT, kwargs.get("story_key", ""))

        with patch("run_stories.orchestrator.run_claude_session", side_effect=mock_run_session), \
             patch("run_stories.orchestrator.run_commit_session", side_effect=mock_commit_session):
            count = await run_stories(config, tui)

        assert count == 1
        assert call_count == 3  # CS, DS, CR


class TestCRRejectionLoop:
    """CR returns in-progress, DS runs again, CR approves on round 2."""

    @pytest.mark.asyncio
    async def test_retry_on_rejection(self, config, tui, tmp_project):
        sprint_status = tmp_project / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml"
        story_file = tmp_project / "_bmad-output" / "implementation-artifacts" / "1-2-next-story.md"
        config.max_review_rounds = 3

        round_count = {"ds": 0, "cr": 0}

        async def mock_run_session(**kwargs):
            step_kind = kwargs.get("step_kind")
            if step_kind == StepKind.CS:
                _update_status(sprint_status, "1-2-next-story: backlog", "1-2-next-story: ready-for-dev")
                story_file.write_text("# Story 1.2")
                return _make_step_result(StepKind.CS, "1-2-next-story")
            elif step_kind == StepKind.DS:
                round_count["ds"] += 1
                _update_status(sprint_status, "1-2-next-story: ready-for-dev", "1-2-next-story: review")
                return _make_step_result(StepKind.DS, "1-2-next-story")
            elif step_kind == StepKind.CR:
                round_count["cr"] += 1
                if round_count["cr"] == 1:
                    # First round: reject
                    _update_status(sprint_status, "1-2-next-story: review", "1-2-next-story: ready-for-dev")
                    return _make_step_result(StepKind.CR, "1-2-next-story", [
                        MarkerEvent(marker_type=MarkerType.CODE_REVIEW_ISSUES, payload="1-2-next-story"),
                    ])
                else:
                    # Second round: approve
                    _update_status(sprint_status, "1-2-next-story: review", "1-2-next-story: done")
                    return _make_step_result(StepKind.CR, "1-2-next-story", [
                        MarkerEvent(marker_type=MarkerType.CODE_REVIEW_APPROVED, payload="1-2-next-story"),
                    ])
            return _make_step_result(step_kind or StepKind.CS, kwargs.get("story_key", ""))

        async def mock_commit_session(**kwargs):
            return _make_step_result(StepKind.COMMIT, kwargs.get("story_key", ""))

        with patch("run_stories.orchestrator.run_claude_session", side_effect=mock_run_session), \
             patch("run_stories.orchestrator.run_commit_session", side_effect=mock_commit_session):
            count = await run_stories(config, tui)

        assert count == 1
        assert round_count["ds"] == 2
        assert round_count["cr"] == 2


class TestHaltDuringDS:
    """HALT marker detected during DS → loop breaks, no commit."""

    @pytest.mark.asyncio
    async def test_halt_stops_execution(self, config, tui, tmp_project):
        sprint_status = tmp_project / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml"
        story_file = tmp_project / "_bmad-output" / "implementation-artifacts" / "1-2-next-story.md"
        commit_called = False

        async def mock_run_session(**kwargs):
            step_kind = kwargs.get("step_kind")
            if step_kind == StepKind.CS:
                _update_status(sprint_status, "1-2-next-story: backlog", "1-2-next-story: ready-for-dev")
                story_file.write_text("# Story 1.2")
                return _make_step_result(StepKind.CS, "1-2-next-story")
            elif step_kind == StepKind.DS:
                return _make_step_result(StepKind.DS, "1-2-next-story", [
                    MarkerEvent(marker_type=MarkerType.HALT, payload="Missing dependency"),
                ])
            return _make_step_result(step_kind or StepKind.CS, kwargs.get("story_key", ""))

        async def mock_commit_session(**kwargs):
            nonlocal commit_called
            commit_called = True
            return _make_step_result(StepKind.COMMIT, kwargs.get("story_key", ""))

        with patch("run_stories.orchestrator.run_claude_session", side_effect=mock_run_session), \
             patch("run_stories.orchestrator.run_commit_session", side_effect=mock_commit_session):
            count = await run_stories(config, tui)

        assert count == 0
        assert commit_called is False


class TestNoBacklogStories:
    """next_backlog_story() returns None → clean exit."""

    @pytest.mark.asyncio
    async def test_no_stories(self, tui, tmp_project):
        # Override sprint status to have no backlog stories
        sprint_status = tmp_project / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml"
        sprint_status.write_text(
            "development_status:\n"
            "  1-1-done: done\n"
            "  1-2-also-done: done\n"
        )

        config = SessionConfig(project_dir=tmp_project, max_stories=5)

        with patch("run_stories.orchestrator.run_claude_session") as mock_cs:
            count = await run_stories(config, tui)

        assert count == 0
        mock_cs.assert_not_called()


class TestMaxReviewRoundsExhausted:
    """Max review rounds reached → warning and break."""

    @pytest.mark.asyncio
    async def test_max_rounds(self, config, tui, tmp_project):
        sprint_status = tmp_project / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml"
        story_file = tmp_project / "_bmad-output" / "implementation-artifacts" / "1-2-next-story.md"
        config.max_review_rounds = 2

        async def mock_run_session(**kwargs):
            step_kind = kwargs.get("step_kind")
            if step_kind == StepKind.CS:
                _update_status(sprint_status, "1-2-next-story: backlog", "1-2-next-story: ready-for-dev")
                story_file.write_text("# Story 1.2")
                return _make_step_result(StepKind.CS, "1-2-next-story")
            elif step_kind == StepKind.DS:
                content = sprint_status.read_text()
                if "ready-for-dev" in content:
                    _update_status(sprint_status, "1-2-next-story: ready-for-dev", "1-2-next-story: review")
                elif "in-progress" in content:
                    _update_status(sprint_status, "1-2-next-story: in-progress", "1-2-next-story: review")
                return _make_step_result(StepKind.DS, "1-2-next-story")
            elif step_kind == StepKind.CR:
                _update_status(sprint_status, "1-2-next-story: review", "1-2-next-story: in-progress")
                return _make_step_result(StepKind.CR, "1-2-next-story", [
                    MarkerEvent(marker_type=MarkerType.CODE_REVIEW_ISSUES, payload="1-2-next-story"),
                ])
            return _make_step_result(step_kind or StepKind.CS, kwargs.get("story_key", ""))

        async def mock_commit_session(**kwargs):
            return _make_step_result(StepKind.COMMIT, kwargs.get("story_key", ""))

        with patch("run_stories.orchestrator.run_claude_session", side_effect=mock_run_session), \
             patch("run_stories.orchestrator.run_commit_session", side_effect=mock_commit_session):
            count = await run_stories(config, tui)

        # Story not completed due to max rounds
        assert count == 0


class TestDryRun:
    """Dry run doesn't launch Claude sessions."""

    @pytest.mark.asyncio
    async def test_dry_run(self, tui, tmp_project):
        config = SessionConfig(project_dir=tmp_project, max_stories=2, dry_run=True)

        with patch("run_stories.orchestrator.run_claude_session") as mock_cs:
            count = await run_stories(config, tui)

        # Dry run still counts stories
        assert count >= 1
        mock_cs.assert_not_called()
