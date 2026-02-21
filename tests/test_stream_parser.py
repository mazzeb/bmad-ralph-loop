"""Tests for run_stories.stream_parser."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from run_stories.models import (
    InitEvent,
    MarkerEvent,
    MarkerType,
    RateLimitEvent,
    ResultEvent,
    SystemEvent,
    TextEvent,
    ToolResultEvent,
    ToolUseEvent,
    UnknownEvent,
)
from run_stories.stream_parser import parse_line

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# --- Individual event type tests ---


class TestParseInit:
    def test_init_event(self):
        line = json.dumps({
            "type": "system",
            "subtype": "init",
            "model": "claude-sonnet-4-20250514",
            "tools": [{"name": "Read"}, {"name": "Write"}, {"name": "Bash"}],
            "permissionMode": "bypassPermissions",
            "session_id": "sess_001",
        })
        events = parse_line(line)
        assert len(events) == 1
        e = events[0]
        assert isinstance(e, InitEvent)
        assert e.model == "claude-sonnet-4-20250514"
        assert e.tools == ["Read", "Write", "Bash"]
        assert e.permission_mode == "bypassPermissions"
        assert e.session_id == "sess_001"


class TestParseSystemEvent:
    def test_hook_started(self):
        line = json.dumps({"type": "system", "subtype": "hook_started", "hook_name": "init_hook"})
        events = parse_line(line)
        assert len(events) == 1
        assert isinstance(events[0], SystemEvent)
        assert events[0].subtype == "hook_started"

    def test_task_started(self):
        line = json.dumps({"type": "system", "subtype": "task_started"})
        events = parse_line(line)
        assert isinstance(events[0], SystemEvent)
        assert events[0].subtype == "task_started"


class TestParseToolUse:
    def test_read_tool(self):
        line = json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "id": "tu_01", "name": "Read", "input": {"file_path": "/foo/bar.py"}},
            ]},
        })
        events = parse_line(line)
        assert len(events) == 1
        e = events[0]
        assert isinstance(e, ToolUseEvent)
        assert e.tool_name == "Read"
        assert e.input_summary == "/foo/bar.py"

    def test_bash_tool(self):
        line = json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "id": "tu_02", "name": "Bash", "input": {"command": "npm test"}},
            ]},
        })
        events = parse_line(line)
        assert isinstance(events[0], ToolUseEvent)
        assert events[0].tool_name == "Bash"
        assert events[0].input_summary == "npm test"

    def test_grep_tool(self):
        line = json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "id": "tu_03", "name": "Grep", "input": {"pattern": "TODO|FIXME"}},
            ]},
        })
        events = parse_line(line)
        assert isinstance(events[0], ToolUseEvent)
        assert events[0].input_summary == "TODO|FIXME"


class TestParseToolResult:
    def test_tool_result_string_content(self):
        line = json.dumps({
            "type": "user",
            "message": {"content": [
                {"type": "tool_result", "tool_use_id": "tu_01", "content": "File contents here..."},
            ]},
        })
        events = parse_line(line)
        assert len(events) == 1
        assert isinstance(events[0], ToolResultEvent)
        assert events[0].tool_use_id == "tu_01"
        assert events[0].content_summary == "File contents here..."

    def test_tool_result_list_content(self):
        line = json.dumps({
            "type": "user",
            "message": {"content": [
                {"type": "tool_result", "tool_use_id": "tu_02", "content": [
                    {"type": "text", "text": "result text"},
                ]},
            ]},
        })
        events = parse_line(line)
        assert isinstance(events[0], ToolResultEvent)
        assert events[0].content_summary == "result text"


class TestParseText:
    def test_assistant_text(self):
        line = json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "text", "text": "Let me analyze this code."},
            ]},
        })
        events = parse_line(line)
        assert len(events) == 1
        assert isinstance(events[0], TextEvent)
        assert events[0].text == "Let me analyze this code."
        assert events[0].is_thinking is False

    def test_thinking_event(self):
        line = json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "thinking", "thinking": "I need to check the imports..."},
            ]},
        })
        events = parse_line(line)
        assert len(events) == 1
        assert isinstance(events[0], TextEvent)
        assert events[0].is_thinking is True
        assert events[0].text == "I need to check the imports..."

    def test_user_text(self):
        line = json.dumps({
            "type": "user",
            "message": {"content": [
                {"type": "text", "text": "system reminder text"},
            ]},
        })
        events = parse_line(line)
        assert isinstance(events[0], TextEvent)


class TestParseResult:
    def test_success_result(self):
        line = json.dumps({
            "type": "result",
            "subtype": "success",
            "duration_ms": 345993,
            "num_turns": 35,
            "is_error": False,
            "total_cost_usd": 8.47,
        })
        events = parse_line(line)
        assert len(events) == 1
        e = events[0]
        assert isinstance(e, ResultEvent)
        assert e.duration_ms == 345993
        assert e.num_turns == 35
        assert e.is_error is False
        assert e.cost_usd == 8.47
        assert e.subtype == "success"

    def test_error_result(self):
        line = json.dumps({
            "type": "result",
            "subtype": "error",
            "duration_ms": 5000,
            "num_turns": 1,
            "is_error": True,
        })
        events = parse_line(line)
        e = events[0]
        assert isinstance(e, ResultEvent)
        assert e.is_error is True
        assert e.cost_usd is None

    def test_result_no_cost(self):
        line = json.dumps({
            "type": "result",
            "subtype": "success",
            "duration_ms": 10000,
            "num_turns": 5,
            "is_error": False,
        })
        events = parse_line(line)
        assert isinstance(events[0], ResultEvent)
        assert events[0].cost_usd is None


class TestParseRateLimit:
    def test_rate_limited(self):
        line = json.dumps({
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "rate_limited",
                "resetsAt": 1771686300,
                "rateLimitType": "token",
            },
        })
        events = parse_line(line)
        assert len(events) == 1
        e = events[0]
        assert isinstance(e, RateLimitEvent)
        assert e.status == "rate_limited"
        assert e.resets_at is not None
        assert e.rate_limit_type == "token"

    def test_rate_limit_allowed(self):
        line = json.dumps({
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "allowed",
                "resetsAt": None,
                "rateLimitType": "token",
            },
        })
        events = parse_line(line)
        e = events[0]
        assert isinstance(e, RateLimitEvent)
        assert e.status == "allowed"
        assert e.resets_at is None


# --- Marker detection tests ---


class TestMarkerDetection:
    def test_halt_marker(self):
        line = json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "text", "text": "Something went wrong.\n\n<HALT>Cannot find sprint status file</HALT>"},
            ]},
        })
        events = parse_line(line)
        assert len(events) == 2
        assert isinstance(events[0], TextEvent)
        assert isinstance(events[1], MarkerEvent)
        assert events[1].marker_type == MarkerType.HALT
        assert events[1].payload == "Cannot find sprint status file"

    def test_create_story_complete(self):
        line = json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "text", "text": "Done!\n\n<CREATE_STORY_COMPLETE>1-3-stock-search</CREATE_STORY_COMPLETE>"},
            ]},
        })
        events = parse_line(line)
        markers = [e for e in events if isinstance(e, MarkerEvent)]
        assert len(markers) == 1
        assert markers[0].marker_type == MarkerType.CREATE_STORY_COMPLETE
        assert markers[0].payload == "1-3-stock-search"

    def test_dev_story_complete(self):
        line = json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "text", "text": "<DEV_STORY_COMPLETE>1-3-foo</DEV_STORY_COMPLETE>"},
            ]},
        })
        events = parse_line(line)
        markers = [e for e in events if isinstance(e, MarkerEvent)]
        assert markers[0].marker_type == MarkerType.DEV_STORY_COMPLETE
        assert markers[0].payload == "1-3-foo"

    def test_code_review_approved(self):
        line = json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "text", "text": "Approved!\n<CODE_REVIEW_APPROVED>1-3-foo</CODE_REVIEW_APPROVED>"},
            ]},
        })
        events = parse_line(line)
        markers = [e for e in events if isinstance(e, MarkerEvent)]
        assert markers[0].marker_type == MarkerType.CODE_REVIEW_APPROVED

    def test_code_review_issues(self):
        line = json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "text", "text": "<CODE_REVIEW_ISSUES>1-3-foo</CODE_REVIEW_ISSUES>"},
            ]},
        })
        events = parse_line(line)
        markers = [e for e in events if isinstance(e, MarkerEvent)]
        assert markers[0].marker_type == MarkerType.CODE_REVIEW_ISSUES

    def test_no_backlog_stories(self):
        line = json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "text", "text": "No stories found.\n<NO_BACKLOG_STORIES/>"},
            ]},
        })
        events = parse_line(line)
        markers = [e for e in events if isinstance(e, MarkerEvent)]
        assert len(markers) == 1
        assert markers[0].marker_type == MarkerType.NO_BACKLOG_STORIES
        assert markers[0].payload == ""

    def test_no_ready_stories(self):
        line = json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "text", "text": "<NO_READY_STORIES/>"},
            ]},
        })
        events = parse_line(line)
        markers = [e for e in events if isinstance(e, MarkerEvent)]
        assert markers[0].marker_type == MarkerType.NO_READY_STORIES

    def test_marker_mid_sentence(self):
        line = json.dumps({
            "type": "assistant",
            "message": {"content": [
                {"type": "text", "text": "Story created <CREATE_STORY_COMPLETE>key</CREATE_STORY_COMPLETE> successfully."},
            ]},
        })
        events = parse_line(line)
        markers = [e for e in events if isinstance(e, MarkerEvent)]
        assert len(markers) == 1
        assert markers[0].payload == "key"


# --- Error handling tests ---


class TestErrorHandling:
    def test_malformed_json(self):
        events = parse_line("{invalid json")
        assert len(events) == 1
        assert isinstance(events[0], UnknownEvent)

    def test_empty_line(self):
        events = parse_line("")
        assert len(events) == 1
        assert isinstance(events[0], UnknownEvent)

    def test_whitespace_line(self):
        events = parse_line("   \n  ")
        assert isinstance(events[0], UnknownEvent)

    def test_unknown_type(self):
        line = json.dumps({"type": "something_new", "data": "value"})
        events = parse_line(line)
        assert isinstance(events[0], UnknownEvent)


# --- Fixture integration tests ---


class TestFixtureIntegration:
    """Parse every line of each fixture file, verify no exceptions."""

    @pytest.fixture
    def fixture_files(self):
        return {
            "create-story": FIXTURES_DIR / "create-story.log",
            "dev-story": FIXTURES_DIR / "dev-story.log",
            "code-review": FIXTURES_DIR / "code-review.log",
        }

    def _count_types(self, fixture_path: Path) -> dict[str, int]:
        counts: dict[str, int] = {}
        with open(fixture_path) as f:
            for line in f:
                events = parse_line(line)
                for event in events:
                    name = type(event).__name__
                    counts[name] = counts.get(name, 0) + 1
        return counts

    def test_create_story_fixture(self, fixture_files):
        counts = self._count_types(fixture_files["create-story"])
        assert counts.get("UnknownEvent", 0) == 0
        assert "InitEvent" in counts
        assert "ToolUseEvent" in counts
        assert "ResultEvent" in counts
        assert "MarkerEvent" in counts  # CREATE_STORY_COMPLETE marker

    def test_dev_story_fixture(self, fixture_files):
        counts = self._count_types(fixture_files["dev-story"])
        assert counts.get("UnknownEvent", 0) == 0
        assert counts.get("ToolUseEvent", 0) >= 10
        assert counts.get("ToolResultEvent", 0) >= 10
        assert "RateLimitEvent" in counts
        assert "MarkerEvent" in counts  # DEV_STORY_COMPLETE marker

    def test_code_review_fixture(self, fixture_files):
        counts = self._count_types(fixture_files["code-review"])
        assert counts.get("UnknownEvent", 0) == 0
        assert "InitEvent" in counts
        assert "ResultEvent" in counts
        assert "MarkerEvent" in counts  # CODE_REVIEW_APPROVED marker

    def test_no_exceptions_any_fixture(self, fixture_files):
        """Parse every line of every fixture without any exceptions."""
        for name, path in fixture_files.items():
            with open(path) as f:
                for line_num, line in enumerate(f, 1):
                    try:
                        events = parse_line(line)
                        assert len(events) >= 1, f"No events for {name}:{line_num}"
                    except Exception as exc:
                        pytest.fail(f"Exception parsing {name}:{line_num}: {exc}")
