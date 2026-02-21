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


# Shorthand for the git-dirty mock used by all tests with done stories in fixture
_PATCH_GIT_CLEAN = patch("run_stories.orchestrator._check_git_dirty", return_value=False, new_callable=AsyncMock)


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
             patch("run_stories.orchestrator.run_commit_session", side_effect=mock_commit_session), \
             _PATCH_GIT_CLEAN:
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
             patch("run_stories.orchestrator.run_commit_session", side_effect=mock_commit_session), \
             _PATCH_GIT_CLEAN:
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
             patch("run_stories.orchestrator.run_commit_session", side_effect=mock_commit_session), \
             _PATCH_GIT_CLEAN:
            count = await run_stories(config, tui)

        assert count == 0
        assert commit_called is False


class TestNoActionableStories:
    """next_actionable_story() returns None → clean exit."""

    @pytest.mark.asyncio
    async def test_no_stories(self, tui, tmp_project):
        # Override sprint status to have no actionable stories
        sprint_status = tmp_project / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml"
        sprint_status.write_text(
            "development_status:\n"
            "  1-1-done: done\n"
            "  1-2-also-done: done\n"
        )

        config = SessionConfig(project_dir=tmp_project, max_stories=5)

        with patch("run_stories.orchestrator.run_claude_session") as mock_cs, \
             _PATCH_GIT_CLEAN:
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
             patch("run_stories.orchestrator.run_commit_session", side_effect=mock_commit_session), \
             _PATCH_GIT_CLEAN:
            count = await run_stories(config, tui)

        # Story not completed due to max rounds
        assert count == 0


class TestDryRun:
    """Dry run doesn't launch Claude sessions."""

    @pytest.mark.asyncio
    async def test_dry_run(self, tui, tmp_project):
        config = SessionConfig(project_dir=tmp_project, max_stories=2, dry_run=True)

        with patch("run_stories.orchestrator.run_claude_session") as mock_cs, \
             _PATCH_GIT_CLEAN:
            count = await run_stories(config, tui)

        # Dry run still counts stories
        assert count >= 1
        mock_cs.assert_not_called()


# ---- Crash-resume tests ----


class TestResumeFromReadyForDev:
    """YAML has ready-for-dev story → DS runs (no CS), then CR, then COMMIT."""

    @pytest.mark.asyncio
    async def test_skips_cs_passes_story_path(self, tui, tmp_project):
        sprint_status = tmp_project / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml"
        sprint_status.write_text(
            "development_status:\n"
            "  1-1-alpha: ready-for-dev\n"
        )
        story_file = tmp_project / "_bmad-output" / "implementation-artifacts" / "1-1-alpha.md"
        story_file.write_text("# Story 1.1")

        config = SessionConfig(project_dir=tmp_project, max_stories=1)
        steps_called = []
        extra_prompts = []

        async def mock_run_session(**kwargs):
            step_kind = kwargs.get("step_kind")
            steps_called.append(step_kind)
            extra_prompts.append(kwargs.get("extra_prompt"))
            if step_kind == StepKind.DS:
                _update_status(sprint_status, "1-1-alpha: ready-for-dev", "1-1-alpha: review")
                return _make_step_result(StepKind.DS, "1-1-alpha")
            elif step_kind == StepKind.CR:
                _update_status(sprint_status, "1-1-alpha: review", "1-1-alpha: done")
                return _make_step_result(StepKind.CR, "1-1-alpha")
            return _make_step_result(step_kind or StepKind.CS, "1-1-alpha")

        async def mock_commit_session(**kwargs):
            return _make_step_result(StepKind.COMMIT, "1-1-alpha")

        with patch("run_stories.orchestrator.run_claude_session", side_effect=mock_run_session), \
             patch("run_stories.orchestrator.run_commit_session", side_effect=mock_commit_session), \
             _PATCH_GIT_CLEAN:
            count = await run_stories(config, tui)

        assert count == 1
        assert StepKind.CS not in steps_called
        assert StepKind.DS in steps_called
        assert StepKind.CR in steps_called
        # STORY_PATH must be passed to DS
        ds_extra = extra_prompts[steps_called.index(StepKind.DS)]
        assert "STORY_PATH:" in ds_extra
        assert "1-1-alpha.md" in ds_extra


class TestResumeFromReview:
    """YAML has review story → CR runs (no CS/DS), then COMMIT."""

    @pytest.mark.asyncio
    async def test_skips_cs_and_ds(self, tui, tmp_project):
        sprint_status = tmp_project / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml"
        sprint_status.write_text(
            "development_status:\n"
            "  1-1-alpha: review\n"
        )
        story_file = tmp_project / "_bmad-output" / "implementation-artifacts" / "1-1-alpha.md"
        story_file.write_text("# Story 1.1")

        config = SessionConfig(project_dir=tmp_project, max_stories=1)
        steps_called = []

        async def mock_run_session(**kwargs):
            step_kind = kwargs.get("step_kind")
            steps_called.append(step_kind)
            if step_kind == StepKind.CR:
                _update_status(sprint_status, "1-1-alpha: review", "1-1-alpha: done")
                return _make_step_result(StepKind.CR, "1-1-alpha")
            return _make_step_result(step_kind or StepKind.CS, "1-1-alpha")

        async def mock_commit_session(**kwargs):
            return _make_step_result(StepKind.COMMIT, "1-1-alpha")

        with patch("run_stories.orchestrator.run_claude_session", side_effect=mock_run_session), \
             patch("run_stories.orchestrator.run_commit_session", side_effect=mock_commit_session), \
             _PATCH_GIT_CLEAN:
            count = await run_stories(config, tui)

        assert count == 1
        assert StepKind.CS not in steps_called
        assert StepKind.DS not in steps_called
        assert StepKind.CR in steps_called


class TestResumeFromInProgress:
    """YAML has in-progress story → DS runs with STORY_PATH, then CR, then COMMIT."""

    @pytest.mark.asyncio
    async def test_resumes_ds_with_story_path(self, tui, tmp_project):
        sprint_status = tmp_project / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml"
        sprint_status.write_text(
            "development_status:\n"
            "  1-1-alpha: in-progress\n"
        )
        story_file = tmp_project / "_bmad-output" / "implementation-artifacts" / "1-1-alpha.md"
        story_file.write_text("# Story 1.1")

        config = SessionConfig(project_dir=tmp_project, max_stories=1)
        steps_called = []
        extra_prompts = []

        async def mock_run_session(**kwargs):
            step_kind = kwargs.get("step_kind")
            steps_called.append(step_kind)
            extra_prompts.append(kwargs.get("extra_prompt"))
            if step_kind == StepKind.DS:
                _update_status(sprint_status, "1-1-alpha: in-progress", "1-1-alpha: review")
                return _make_step_result(StepKind.DS, "1-1-alpha")
            elif step_kind == StepKind.CR:
                _update_status(sprint_status, "1-1-alpha: review", "1-1-alpha: done")
                return _make_step_result(StepKind.CR, "1-1-alpha")
            return _make_step_result(step_kind or StepKind.CS, "1-1-alpha")

        async def mock_commit_session(**kwargs):
            return _make_step_result(StepKind.COMMIT, "1-1-alpha")

        with patch("run_stories.orchestrator.run_claude_session", side_effect=mock_run_session), \
             patch("run_stories.orchestrator.run_commit_session", side_effect=mock_commit_session), \
             _PATCH_GIT_CLEAN:
            count = await run_stories(config, tui)

        assert count == 1
        assert StepKind.CS not in steps_called
        ds_extra = extra_prompts[steps_called.index(StepKind.DS)]
        assert "STORY_PATH:" in ds_extra


class TestCommitGapRecovery:
    """YAML has done story + dirty git + story not in git log → commit runs before main loop."""

    @pytest.mark.asyncio
    async def test_recovers_uncommitted_done_story(self, tui, tmp_project):
        sprint_status = tmp_project / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml"
        sprint_status.write_text(
            "development_status:\n"
            "  1-1-alpha: done\n"
            "  1-2-beta: backlog\n"
        )

        config = SessionConfig(project_dir=tmp_project, max_stories=1)
        commit_keys = []

        async def mock_run_session(**kwargs):
            step_kind = kwargs.get("step_kind")
            story_file = tmp_project / "_bmad-output" / "implementation-artifacts" / "1-2-beta.md"
            if step_kind == StepKind.CS:
                _update_status(sprint_status, "1-2-beta: backlog", "1-2-beta: ready-for-dev")
                story_file.write_text("# Story 1.2")
                return _make_step_result(StepKind.CS, "1-2-beta")
            elif step_kind == StepKind.DS:
                _update_status(sprint_status, "1-2-beta: ready-for-dev", "1-2-beta: review")
                return _make_step_result(StepKind.DS, "1-2-beta")
            elif step_kind == StepKind.CR:
                _update_status(sprint_status, "1-2-beta: review", "1-2-beta: done")
                return _make_step_result(StepKind.CR, "1-2-beta")
            return _make_step_result(step_kind or StepKind.CS, kwargs.get("story_key", ""))

        async def mock_commit_session(**kwargs):
            commit_keys.append(kwargs.get("story_key"))
            return _make_step_result(StepKind.COMMIT, kwargs.get("story_key", ""))

        with patch("run_stories.orchestrator.run_claude_session", side_effect=mock_run_session), \
             patch("run_stories.orchestrator.run_commit_session", side_effect=mock_commit_session), \
             patch("run_stories.orchestrator._check_git_dirty", return_value=True, new_callable=AsyncMock), \
             patch("run_stories.orchestrator._check_story_committed", return_value=False, new_callable=AsyncMock):
            count = await run_stories(config, tui)

        # First commit is recovery for 1-1-alpha, second is for 1-2-beta
        assert "1-1-alpha" in commit_keys
        assert count == 1


class TestCleanDoneNoRecovery:
    """YAML has done story + clean git → no commit recovery, next actionable story."""

    @pytest.mark.asyncio
    async def test_skips_committed_done_story(self, tui, tmp_project):
        sprint_status = tmp_project / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml"
        sprint_status.write_text(
            "development_status:\n"
            "  1-1-alpha: done\n"
            "  1-2-beta: backlog\n"
        )

        config = SessionConfig(project_dir=tmp_project, max_stories=1)
        commit_keys = []

        async def mock_run_session(**kwargs):
            step_kind = kwargs.get("step_kind")
            story_file = tmp_project / "_bmad-output" / "implementation-artifacts" / "1-2-beta.md"
            if step_kind == StepKind.CS:
                _update_status(sprint_status, "1-2-beta: backlog", "1-2-beta: ready-for-dev")
                story_file.write_text("# Story 1.2")
                return _make_step_result(StepKind.CS, "1-2-beta")
            elif step_kind == StepKind.DS:
                _update_status(sprint_status, "1-2-beta: ready-for-dev", "1-2-beta: review")
                return _make_step_result(StepKind.DS, "1-2-beta")
            elif step_kind == StepKind.CR:
                _update_status(sprint_status, "1-2-beta: review", "1-2-beta: done")
                return _make_step_result(StepKind.CR, "1-2-beta")
            return _make_step_result(step_kind or StepKind.CS, kwargs.get("story_key", ""))

        async def mock_commit_session(**kwargs):
            commit_keys.append(kwargs.get("story_key"))
            return _make_step_result(StepKind.COMMIT, kwargs.get("story_key", ""))

        with patch("run_stories.orchestrator.run_claude_session", side_effect=mock_run_session), \
             patch("run_stories.orchestrator.run_commit_session", side_effect=mock_commit_session), \
             _PATCH_GIT_CLEAN:
            count = await run_stories(config, tui)

        # No recovery commit — only the main-loop commit for 1-2-beta
        assert "1-1-alpha" not in commit_keys
        assert count == 1


class TestStoryFileMissing:
    """YAML has ready-for-dev but no story file → falls back to CS."""

    @pytest.mark.asyncio
    async def test_fallback_to_cs(self, tui, tmp_project):
        sprint_status = tmp_project / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml"
        sprint_status.write_text(
            "development_status:\n"
            "  1-1-alpha: ready-for-dev\n"
        )
        # Deliberately do NOT create story file

        config = SessionConfig(project_dir=tmp_project, max_stories=1)
        steps_called = []

        async def mock_run_session(**kwargs):
            step_kind = kwargs.get("step_kind")
            steps_called.append(step_kind)
            story_file = tmp_project / "_bmad-output" / "implementation-artifacts" / "1-1-alpha.md"
            if step_kind == StepKind.CS:
                _update_status(sprint_status, "1-1-alpha: ready-for-dev", "1-1-alpha: ready-for-dev")
                story_file.write_text("# Story 1.1")
                return _make_step_result(StepKind.CS, "1-1-alpha")
            elif step_kind == StepKind.DS:
                _update_status(sprint_status, "1-1-alpha: ready-for-dev", "1-1-alpha: review")
                return _make_step_result(StepKind.DS, "1-1-alpha")
            elif step_kind == StepKind.CR:
                _update_status(sprint_status, "1-1-alpha: review", "1-1-alpha: done")
                return _make_step_result(StepKind.CR, "1-1-alpha")
            return _make_step_result(step_kind or StepKind.CS, "1-1-alpha")

        async def mock_commit_session(**kwargs):
            return _make_step_result(StepKind.COMMIT, "1-1-alpha")

        with patch("run_stories.orchestrator.run_claude_session", side_effect=mock_run_session), \
             patch("run_stories.orchestrator.run_commit_session", side_effect=mock_commit_session), \
             _PATCH_GIT_CLEAN:
            count = await run_stories(config, tui)

        # CS should be called because story file was missing
        assert StepKind.CS in steps_called
        assert count == 1


class TestDryRunShowsResumeStep:
    """Dry run with intermediate state shows which step would be resumed."""

    @pytest.mark.asyncio
    async def test_dry_run_resume_message(self, tmp_project):
        sprint_status = tmp_project / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml"
        sprint_status.write_text(
            "development_status:\n"
            "  1-1-alpha: review\n"
        )
        story_file = tmp_project / "_bmad-output" / "implementation-artifacts" / "1-1-alpha.md"
        story_file.write_text("# Story 1.1")

        config = SessionConfig(project_dir=tmp_project, max_stories=1, dry_run=True)
        mock_tui = MagicMock(spec=TUI)
        mock_tui.dashboard = MagicMock()
        mock_tui.dashboard.total_cost = 0.0

        with patch("run_stories.orchestrator.run_claude_session") as mock_cs, \
             _PATCH_GIT_CLEAN:
            count = await run_stories(config, mock_tui)

        assert count == 1
        mock_cs.assert_not_called()
        # Verify the TUI received the resume dry-run message
        messages = [call.args[0].text for call in mock_tui.handle_event.call_args_list
                    if hasattr(call.args[0], "text")]
        assert any("code-review" in msg for msg in messages)
