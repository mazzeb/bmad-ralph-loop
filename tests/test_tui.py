"""Tests for run_stories.tui (ActivityLog, Dashboard, TUI)."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from io import StringIO

from rich.console import Console

from run_stories.models import (
    InitEvent,
    MarkerEvent,
    MarkerType,
    RateLimitEvent,
    ResultEvent,
    StepKind,
    StepResult,
    StoryState,
    SystemEvent,
    TextEvent,
    ToolResultEvent,
    ToolUseEvent,
    UnknownEvent,
)
from run_stories.tui import ActivityLog, Dashboard, StoryRunnerApp, TUI, _format_duration, _format_elapsed


# --- Helper ---


def render_to_text(renderable) -> str:
    """Render a rich renderable to plain text."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=120)
    console.print(renderable)
    return buf.getvalue()


# --- Format helpers ---


class TestFormatDuration:
    def test_seconds_only(self):
        assert _format_duration(45000) == "45s"

    def test_minutes_seconds(self):
        assert _format_duration(345993) == "5m45s"

    def test_zero(self):
        assert _format_duration(0) == "0s"

    def test_exact_minute(self):
        assert _format_duration(60000) == "1m00s"


class TestFormatElapsed:
    def test_seconds(self):
        assert _format_elapsed(45.0) == "45s"

    def test_minutes(self):
        assert _format_elapsed(125.0) == "2m05s"

    def test_zero(self):
        assert _format_elapsed(0) == "0s"


# --- ActivityLog tests ---


class TestActivityLog:
    def test_tool_use_event_hidden_by_default(self):
        log = ActivityLog()
        log.add_event(ToolUseEvent(tool_name="Read", input_summary="/foo/bar.py"))
        assert len(log._lines) == 1  # stored
        text = render_to_text(log.render())
        assert "Read" not in text  # but hidden in render

    def test_tool_use_event_shown_when_enabled(self):
        log = ActivityLog()
        log.show_tools = True
        log.add_event(ToolUseEvent(tool_name="Read", input_summary="/foo/bar.py"))
        text = render_to_text(log.render())
        assert "Read" in text
        assert "/foo/bar.py" in text

    def test_tool_use_toggle_at_runtime(self):
        log = ActivityLog()
        log.add_event(ToolUseEvent(tool_name="Read", input_summary="/foo/bar.py"))
        log.add_event(TextEvent(text="Some output", is_thinking=False))
        # Tools hidden by default
        text = render_to_text(log.render())
        assert "Read" not in text
        assert "Some output" in text
        # Toggle on
        log.show_tools = True
        text = render_to_text(log.render())
        assert "Read" in text
        assert "Some output" in text
        # Toggle off again
        log.show_tools = False
        text = render_to_text(log.render())
        assert "Read" not in text
        assert "Some output" in text

    def test_tool_use_new_lines_counter_excludes_hidden(self):
        log = ActivityLog()
        for i in range(10):
            log.add_event(TextEvent(text=f"line {i}", is_thinking=False))
        log.scroll_up()
        # Add a mix of tool and non-tool events while scrolled
        log.add_event(ToolUseEvent(tool_name="Read", input_summary="/a.py"))
        log.add_event(TextEvent(text="visible", is_thinking=False))
        log.add_event(ToolUseEvent(tool_name="Write", input_summary="/b.py"))
        # Only the text event should count (tools hidden by default)
        assert log._new_lines_since_pause == 1

    def test_toggle_resets_scroll_to_bottom(self):
        log = ActivityLog()
        for i in range(50):
            log.add_event(TextEvent(text=f"line {i}", is_thinking=False))
        log.scroll_up(lines=20)
        assert log.auto_scroll is False
        assert log.scroll_offset > 0
        # Toggling should snap to bottom
        log.show_tools = True
        assert log.auto_scroll is True
        assert log.scroll_offset == 0

    def test_constructor_accepts_show_tools(self):
        log = ActivityLog(show_tools=True)
        assert log.show_tools is True
        log2 = ActivityLog(show_tools=False)
        assert log2.show_tools is False

    def test_tool_result_skipped(self):
        log = ActivityLog()
        log.add_event(ToolResultEvent(tool_use_id="tu_01", content_summary="content"))
        assert len(log._lines) == 0

    def test_text_event(self):
        log = ActivityLog()
        log.add_event(TextEvent(text="Let me analyze this.", is_thinking=False))
        text = render_to_text(log.render())
        assert "Let me analyze this." in text

    def test_thinking_hidden_by_default(self):
        log = ActivityLog()
        log.add_event(TextEvent(text="Thinking...", is_thinking=True), show_thinking=False)
        assert len(log._lines) == 0

    def test_thinking_shown_when_enabled(self):
        log = ActivityLog()
        log.add_event(TextEvent(text="Thinking...", is_thinking=True), show_thinking=True)
        assert len(log._lines) == 1
        text = render_to_text(log.render())
        assert "Thinking..." in text

    def test_marker_event_green(self):
        log = ActivityLog()
        log.add_event(MarkerEvent(marker_type=MarkerType.CREATE_STORY_COMPLETE, payload="1-3-foo"))
        assert len(log._lines) == 1
        text = render_to_text(log.render())
        assert "CREATE_STORY_COMPLETE" in text
        assert "1-3-foo" in text

    def test_marker_halt_red(self):
        log = ActivityLog()
        log.add_event(MarkerEvent(marker_type=MarkerType.HALT, payload="error"))
        text = render_to_text(log.render())
        assert "HALT" in text

    def test_init_event(self):
        log = ActivityLog()
        log.add_event(InitEvent(model="opus", tools=["Read", "Write"], permission_mode="bypass", session_id="s1"))
        text = render_to_text(log.render())
        assert "opus" in text
        assert "2 tools" in text

    def test_result_event_success(self):
        log = ActivityLog()
        log.add_event(ResultEvent(duration_ms=60000, num_turns=10, is_error=False, subtype="success", cost_usd=2.50))
        text = render_to_text(log.render())
        assert "10 turns" in text
        assert "1m00s" in text
        assert "$2.50" in text

    def test_result_event_error(self):
        log = ActivityLog()
        log.add_event(ResultEvent(duration_ms=5000, num_turns=1, is_error=True, subtype="error"))
        text = render_to_text(log.render())
        assert "Error" in text

    def test_system_event_task_started(self):
        log = ActivityLog()
        log.add_event(SystemEvent(subtype="task_started"))
        assert len(log._lines) == 1

    def test_system_event_hook_skipped(self):
        log = ActivityLog()
        log.add_event(SystemEvent(subtype="hook_started"))
        assert len(log._lines) == 0

    def test_unknown_event_skipped(self):
        log = ActivityLog()
        log.add_event(UnknownEvent(raw_data={}))
        assert len(log._lines) == 0

    def test_text_truncation(self):
        """Long text is truncated by Rich's overflow='ellipsis', not manual slicing."""
        log = ActivityLog()
        long_text = "x" * 200
        log.add_event(TextEvent(text=long_text, is_thinking=False))
        text = render_to_text(log.render())
        # Rich adds an ellipsis character when overflow="ellipsis" truncates
        assert "…" in text
        # Output should be bounded by the console width (120), not contain full 200 chars
        assert long_text not in text

    def test_rate_limit_hidden_when_allowed(self):
        log = ActivityLog()
        log.add_event(RateLimitEvent(status="allowed", resets_at=None, rate_limit_type="token"))
        assert len(log._lines) == 0


# --- Scroll behavior ---


class TestScrollBehavior:
    def test_auto_scroll_default(self):
        log = ActivityLog()
        assert log.auto_scroll is True

    def test_scroll_up_disables_auto(self):
        log = ActivityLog()
        for i in range(50):
            log.add_event(TextEvent(text=f"line {i}", is_thinking=False))
        log.scroll_up()
        assert log.auto_scroll is False
        assert log.scroll_offset > 0

    def test_scroll_down_to_bottom_resumes_auto(self):
        log = ActivityLog()
        for i in range(50):
            log.add_event(TextEvent(text=f"line {i}", is_thinking=False))
        log.scroll_up(lines=10)
        assert log.auto_scroll is False
        log.scroll_down(lines=10)
        assert log.auto_scroll is True
        assert log.scroll_offset == 0

    def test_scroll_shows_older_lines(self):
        log = ActivityLog()
        for i in range(100):
            log.add_event(TextEvent(text=f"line-{i:03d}", is_thinking=False))
        # Default shows latest
        text = render_to_text(log.render(height=5))
        assert "line-099" in text
        # Scroll up
        log.scroll_up(lines=50)
        text = render_to_text(log.render(height=5))
        assert "line-099" not in text

    def test_new_lines_counter_increments(self):
        log = ActivityLog()
        for i in range(50):
            log.add_event(TextEvent(text=f"line {i}", is_thinking=False))
        log.scroll_up()
        for i in range(5):
            log.add_event(TextEvent(text=f"new {i}", is_thinking=False))
        assert log._new_lines_since_pause == 5

    def test_new_lines_counter_resets_on_resume(self):
        log = ActivityLog()
        for i in range(50):
            log.add_event(TextEvent(text=f"line {i}", is_thinking=False))
        log.scroll_up(lines=5)
        for i in range(3):
            log.add_event(TextEvent(text=f"new {i}", is_thinking=False))
        assert log._new_lines_since_pause == 3
        log.scroll_down(lines=5)
        assert log._new_lines_since_pause == 0

    def test_scroll_indicator_in_render(self):
        log = ActivityLog()
        for i in range(50):
            log.add_event(TextEvent(text=f"line {i}", is_thinking=False))
        log.scroll_up()
        for i in range(3):
            log.add_event(TextEvent(text=f"new {i}", is_thinking=False))
        text = render_to_text(log.render(height=10))
        assert "new lines" in text

    def test_scroll_indicator_absent_when_at_bottom(self):
        log = ActivityLog()
        for i in range(50):
            log.add_event(TextEvent(text=f"line {i}", is_thinking=False))
        text = render_to_text(log.render(height=10))
        assert "new lines" not in text

    def test_scroll_up_fewer_lines_than_height_is_noop(self):
        """Regression: scrolling up when all lines fit on screen should not remove lines."""
        log = ActivityLog()
        for i in range(10):
            log.add_event(TextEvent(text=f"line-{i:03d}", is_thinking=False))
        # Screen height larger than content — all 10 lines visible
        text_before = render_to_text(log.render(height=30))
        assert "line-000" in text_before
        assert "line-009" in text_before
        # Scroll up repeatedly
        for _ in range(10):
            log.scroll_up(lines=3)
        text_after = render_to_text(log.render(height=30))
        # All lines should still be visible
        assert "line-000" in text_after
        assert "line-009" in text_after

    def test_scroll_up_stops_at_first_line(self):
        """Scrolling up with many lines stops when first line reaches top."""
        log = ActivityLog()
        for i in range(100):
            log.add_event(TextEvent(text=f"line-{i:03d}", is_thinking=False))
        # Scroll all the way up
        for _ in range(50):
            log.scroll_up(lines=10)
        text = render_to_text(log.render(height=20))
        # First line must be visible
        assert "line-000" in text
        # Should show exactly 20 lines (height worth)
        assert "line-019" in text


# --- Idle indicator tests ---


class TestIdleIndicator:
    def test_no_indicator_when_session_inactive(self):
        log = ActivityLog()
        log.add_event(TextEvent(text="hello", is_thinking=False))
        text = render_to_text(log.render(height=10))
        assert "Processing" not in text

    def test_no_indicator_when_idle_below_threshold(self):
        log = ActivityLog()
        log.set_session_active(True)
        log.add_event(TextEvent(text="hello", is_thinking=False))
        # Immediately after event — well below 15s threshold
        text = render_to_text(log.render(height=10))
        assert "Processing" not in text

    def test_indicator_appears_when_idle_exceeds_threshold(self):
        log = ActivityLog()
        log.set_session_active(True)
        log.add_event(TextEvent(text="hello", is_thinking=False))
        # Simulate 20 seconds of idle by backdating _last_event_at
        log._last_event_at = time.monotonic() - 20
        text = render_to_text(log.render(height=10))
        assert "Processing" in text
        assert "since last output" in text

    def test_indicator_disappears_on_new_event(self):
        log = ActivityLog()
        log.set_session_active(True)
        log.add_event(TextEvent(text="hello", is_thinking=False))
        log._last_event_at = time.monotonic() - 20
        # Indicator should be present
        text = render_to_text(log.render(height=10))
        assert "Processing" in text
        # New event arrives — resets the timestamp
        log.add_event(TextEvent(text="world", is_thinking=False))
        text = render_to_text(log.render(height=10))
        assert "Processing" not in text

    def test_indicator_disappears_when_session_ends(self):
        log = ActivityLog()
        log.set_session_active(True)
        log.add_event(TextEvent(text="hello", is_thinking=False))
        log._last_event_at = time.monotonic() - 20
        assert "Processing" in render_to_text(log.render(height=10))
        # Session ends
        log.set_session_active(False)
        text = render_to_text(log.render(height=10))
        assert "Processing" not in text

    def test_set_session_active_initializes_timestamp(self):
        log = ActivityLog()
        assert log._last_event_at == 0
        log.set_session_active(True)
        assert log._last_event_at > 0

    def test_indicator_shows_elapsed_time(self):
        log = ActivityLog()
        log.set_session_active(True)
        log.add_event(TextEvent(text="hello", is_thinking=False))
        log._last_event_at = time.monotonic() - 125  # 2m05s
        text = render_to_text(log.render(height=10))
        assert "2m05s" in text


# --- Dashboard tests ---


class TestDashboard:
    def test_no_story_state(self):
        dash = Dashboard()
        text = render_to_text(dash.render())
        assert "No active story" in text

    def test_with_story_state(self):
        dash = Dashboard()
        state = StoryState(story_key="1-3-foo", story_id="1.3")
        state.current_step = StepKind.DS
        state.step_results = [
            StepResult(kind=StepKind.CS, story_key="1-3-foo", duration_ms=345993, num_turns=35, cost_usd=2.62, success=True),
        ]
        dash.update_state(
            story_state=state,
            story_number=3,
            step_elapsed=134.0,
            story_elapsed=480.0,
            total_elapsed=1471.0,
            total_cost=12.80,
        )
        text = render_to_text(dash.render())
        assert "1-3-foo" in text
        assert "1.3" in text
        assert "CS" in text
        assert "35 turns" in text
        assert "5m45s" in text

    def test_timer_format(self):
        dash = Dashboard()
        state = StoryState(story_key="1-1-test", story_id="1.1")
        state.current_step = StepKind.CS
        dash.update_state(
            story_state=state,
            story_number=1,
            step_elapsed=125.0,
            story_elapsed=125.0,
            total_elapsed=300.0,
            total_cost=0,
        )
        text = render_to_text(dash.render())
        assert "Step: 2m05s" in text
        assert "Total: 5m00s" in text

    def test_cost_display(self):
        dash = Dashboard()
        dash.step_cost = 3.46
        dash.total_cost = 12.80
        state = StoryState(story_key="1-1-test", story_id="1.1")
        state.current_step = StepKind.CS
        dash.update_state(
            story_state=state,
            story_number=1,
            step_elapsed=0,
            story_elapsed=0,
            total_elapsed=0,
            total_cost=12.80,
        )
        text = render_to_text(dash.render())
        assert "$3.46" in text
        assert "$12.80" in text

    def test_countdown_message(self):
        dash = Dashboard()
        dash.countdown_message = "Next story in 3s..."
        text = render_to_text(dash.render())
        assert "Next story in 3s..." in text

    def test_sprint_stats_displayed(self):
        dash = Dashboard()
        dash.update_sprint_stats(total_epics=5, done_epics=2, total_stories=30, done_stories=8)
        text = render_to_text(dash.render())
        assert "2/5 epics" in text
        assert "8/30 stories done" in text

    def test_sprint_stats_hidden_when_no_stories(self):
        dash = Dashboard()
        text = render_to_text(dash.render())
        assert "Sprint:" not in text

    def test_sprint_stats_zero_done(self):
        dash = Dashboard()
        dash.update_sprint_stats(total_epics=3, done_epics=0, total_stories=15, done_stories=0)
        text = render_to_text(dash.render())
        assert "0/3 epics" in text
        assert "0/15 stories done" in text

    def test_sprint_stats_epics_only_no_stories(self):
        dash = Dashboard()
        dash.update_sprint_stats(total_epics=2, done_epics=0, total_stories=0, done_stories=0)
        text = render_to_text(dash.render())
        assert "Sprint:" in text
        assert "0/2 epics" in text

    def test_multi_round_ds_cr_shows_all_rounds(self):
        """Each DS/CR round should appear as its own line with r1/r2 labels."""
        dash = Dashboard()
        state = StoryState(story_key="1-1-auth", story_id="1.1")
        state.current_step = StepKind.COMMIT
        state.current_round = 2
        state.step_results = [
            StepResult(kind=StepKind.CS, story_key="1-1-auth", duration_ms=23000, num_turns=5, cost_usd=0.05, success=True),
            StepResult(kind=StepKind.DS, story_key="1-1-auth", duration_ms=120000, num_turns=12, cost_usd=0.20, success=True),
            StepResult(kind=StepKind.CR, story_key="1-1-auth", duration_ms=50000, num_turns=6, cost_usd=0.10, success=True),
            StepResult(kind=StepKind.DS, story_key="1-1-auth", duration_ms=90000, num_turns=8, cost_usd=0.15, success=True),
            StepResult(kind=StepKind.CR, story_key="1-1-auth", duration_ms=45000, num_turns=4, cost_usd=0.08, success=True),
        ]
        dash.update_state(story_state=state, story_number=1, step_elapsed=5.0, story_elapsed=400.0, total_elapsed=400.0, total_cost=0.58)
        text = render_to_text(dash.render())
        # CS appears once without round label
        assert "CS" in text
        # DS/CR appear with round labels
        assert "DS r1" in text
        assert "DS r2" in text
        assert "CR r1" in text
        assert "CR r2" in text
        # Each round shows its own timing
        assert "12 turns" in text  # DS r1
        assert "8 turns" in text   # DS r2
        assert "2m00s" in text     # DS r1 = 120s
        assert "1m30s" in text     # DS r2 = 90s

    def test_single_round_no_round_label(self):
        """When DS/CR only run once, no round suffix should appear."""
        dash = Dashboard()
        state = StoryState(story_key="1-2-api", story_id="1.2")
        state.current_step = StepKind.COMMIT
        state.step_results = [
            StepResult(kind=StepKind.CS, story_key="1-2-api", duration_ms=20000, num_turns=4, cost_usd=0.04, success=True),
            StepResult(kind=StepKind.DS, story_key="1-2-api", duration_ms=100000, num_turns=10, cost_usd=0.18, success=True),
            StepResult(kind=StepKind.CR, story_key="1-2-api", duration_ms=40000, num_turns=5, cost_usd=0.07, success=True),
        ]
        dash.update_state(story_state=state, story_number=1, step_elapsed=0, story_elapsed=0, total_elapsed=0, total_cost=0.29)
        text = render_to_text(dash.render())
        assert "DS" in text
        assert "DS r" not in text
        assert "CR r" not in text

    def test_active_step_during_second_round(self):
        """Active DS in round 2 should show round label and live timer."""
        dash = Dashboard()
        state = StoryState(story_key="1-1-auth", story_id="1.1")
        state.current_step = StepKind.DS
        state.current_round = 2
        state.step_results = [
            StepResult(kind=StepKind.CS, story_key="1-1-auth", duration_ms=23000, num_turns=5, cost_usd=0.05, success=True),
            StepResult(kind=StepKind.DS, story_key="1-1-auth", duration_ms=120000, num_turns=12, cost_usd=0.20, success=True),
            StepResult(kind=StepKind.CR, story_key="1-1-auth", duration_ms=50000, num_turns=6, cost_usd=0.10, success=True),
        ]
        dash.update_state(story_state=state, story_number=1, step_elapsed=45.0, story_elapsed=300.0, total_elapsed=300.0, total_cost=0.35)
        text = render_to_text(dash.render())
        # Completed DS r1 should be visible
        assert "DS r1" in text
        assert "12 turns" in text
        # Active DS r2 with live timer
        assert "DS r2" in text
        assert "45s" in text

    def test_pending_commit_after_multi_round(self):
        """COMMIT should show as pending (○) when DS/CR rounds are in progress."""
        dash = Dashboard()
        state = StoryState(story_key="1-1-auth", story_id="1.1")
        state.current_step = StepKind.CR
        state.current_round = 2
        state.step_results = [
            StepResult(kind=StepKind.CS, story_key="1-1-auth", duration_ms=23000, num_turns=5, cost_usd=0.05, success=True),
            StepResult(kind=StepKind.DS, story_key="1-1-auth", duration_ms=120000, num_turns=12, cost_usd=0.20, success=True),
            StepResult(kind=StepKind.CR, story_key="1-1-auth", duration_ms=50000, num_turns=6, cost_usd=0.10, success=True),
            StepResult(kind=StepKind.DS, story_key="1-1-auth", duration_ms=90000, num_turns=8, cost_usd=0.15, success=True),
        ]
        dash.update_state(story_state=state, story_number=1, step_elapsed=20.0, story_elapsed=400.0, total_elapsed=400.0, total_cost=0.50)
        text = render_to_text(dash.render())
        # CR r2 should be active
        assert "CR r2" in text
        # Commit should be pending with ○ marker
        assert "○ Commit" in text

    def test_initial_state_cs_active_rest_pending(self):
        """At story start, CS should be active and DS/CR/COMMIT pending."""
        dash = Dashboard()
        state = StoryState(story_key="1-1-init", story_id="1.1")
        state.current_step = StepKind.CS
        dash.update_state(story_state=state, story_number=1, step_elapsed=10.0, story_elapsed=10.0, total_elapsed=10.0, total_cost=0)
        text = render_to_text(dash.render())
        assert "● CS" in text
        assert "○ DS" in text
        assert "○ CR" in text
        assert "○ Commit" in text

    def test_failed_step_shows_red_cross(self):
        """A step result with success=False should display ✗ in red, not ✓ green."""
        dash = Dashboard()
        state = StoryState(story_key="1-1-fail", story_id="1.1")
        state.current_step = StepKind.DS
        state.current_round = 2
        state.step_results = [
            StepResult(kind=StepKind.CS, story_key="1-1-fail", duration_ms=20000, num_turns=4, cost_usd=0.04, success=True),
            StepResult(kind=StepKind.DS, story_key="1-1-fail", duration_ms=60000, num_turns=8, cost_usd=0.12, success=False),
        ]
        dash.update_state(story_state=state, story_number=1, step_elapsed=0, story_elapsed=0, total_elapsed=0, total_cost=0.16)
        text = render_to_text(dash.render())
        assert "✗" in text
        assert "✓ CS" in text


# --- TUI integration ---


class TestTUI:
    def test_handle_result_event_updates_cost(self):
        tui = TUI(show_thinking=False)
        tui.handle_event(ResultEvent(duration_ms=10000, num_turns=5, is_error=False, subtype="success", cost_usd=2.50))
        assert tui.dashboard.step_cost == 2.50
        assert tui.dashboard.total_cost == 2.50

    def test_handle_rate_limit_event(self):
        tui = TUI()
        resets_at = datetime(2026, 2, 21, 12, 0, 0, tzinfo=timezone.utc)
        tui.handle_event(RateLimitEvent(status="rate_limited", resets_at=resets_at, rate_limit_type="token"))
        assert tui.dashboard.rate_limit_active is True

    def test_handle_rate_limit_allowed(self):
        tui = TUI()
        tui.handle_event(RateLimitEvent(status="allowed", resets_at=None, rate_limit_type="token"))
        assert tui.dashboard.rate_limit_active is False

    def test_handle_event_dispatches_to_activity_log(self):
        tui = TUI()
        tui.handle_event(TextEvent(text="hello", is_thinking=False))
        assert len(tui.activity_log._lines) == 1

    def test_show_tools_default_off(self):
        tui = TUI()
        assert tui.activity_log.show_tools is False

    def test_show_tools_from_constructor(self):
        tui = TUI(show_tools=True)
        assert tui.activity_log.show_tools is True


# --- StoryRunnerApp finished behavior ---


class TestStoryRunnerAppFinished:
    def _make_app(self):
        from pathlib import Path
        from run_stories.models import SessionConfig

        config = SessionConfig(project_dir=Path("/tmp"))
        tui = TUI()
        app = StoryRunnerApp(tui=tui, config=config)
        return app

    def test_initial_finished_is_false(self):
        app = self._make_app()
        assert app._finished is False

    def test_close_if_finished_does_nothing_when_not_finished(self):
        app = self._make_app()
        # Should not raise or call exit
        app.action_close_if_finished()
        # App should still exist (no exit called)
        assert app._finished is False

    def test_close_if_finished_exits_when_finished(self):
        app = self._make_app()
        app._finished = True
        exited = False
        original_exit = app.exit

        def mock_exit(*args, **kwargs):
            nonlocal exited
            exited = True

        app.exit = mock_exit
        app.action_close_if_finished()
        assert exited is True

    def test_enter_binding_exists(self):
        app = self._make_app()
        actions = [b.action for b in app.BINDINGS]
        assert "close_if_finished" in actions

    def test_toggle_tools_binding_exists(self):
        app = self._make_app()
        actions = [b.action for b in app.BINDINGS]
        assert "toggle_tools" in actions

    def test_toggle_tools_action_flips_flag(self):
        app = self._make_app()
        assert app._tui.activity_log.show_tools is False
        # Mock query_one to avoid needing mounted widgets
        mock_widget = type("W", (), {"refresh": lambda self: None})()
        app.query_one = lambda cls: mock_widget
        app.action_toggle_tools()
        assert app._tui.activity_log.show_tools is True
        app.action_toggle_tools()
        assert app._tui.activity_log.show_tools is False


# --- CLI parse_args ---


class TestParseArgs:
    def test_show_tools_flag(self):
        from run_stories.cli import parse_args

        config, _ = parse_args(["--show-tools"])
        assert config.show_tools is True

    def test_show_tools_default_off(self):
        from run_stories.cli import parse_args

        config, _ = parse_args([])
        assert config.show_tools is False
