"""Tests for run_stories.tui (ActivityLog, Dashboard, TUI)."""

from __future__ import annotations

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
    def test_tool_use_event(self):
        log = ActivityLog()
        log.add_event(ToolUseEvent(tool_name="Read", input_summary="/foo/bar.py"))
        text = render_to_text(log.render())
        assert "Read" in text
        assert "/foo/bar.py" in text

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
