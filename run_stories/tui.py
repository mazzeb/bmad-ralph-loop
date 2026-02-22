"""Two-pane TUI using Textual: activity log (top, fills remaining space) + orchestration dashboard (bottom, auto-sized to content)."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from rich.console import Group, RenderableType
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.events import MouseScrollDown, MouseScrollUp
from textual.widgets import Static

from .models import (
    InitEvent,
    MarkerEvent,
    MarkerType,
    RateLimitEvent,
    ResultEvent,
    StepKind,
    StepResult,
    StoryState,
    StreamEvent,
    SystemEvent,
    TextEvent,
    ToolResultEvent,
    ToolUseEvent,
    UnknownEvent,
)


def _format_duration(ms: int) -> str:
    """Format milliseconds as 'Xm YYs'."""
    total_s = ms // 1000
    m, s = divmod(total_s, 60)
    if m > 0:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def _format_elapsed(seconds: float) -> str:
    """Format seconds as 'Xm YYs'."""
    total_s = int(seconds)
    m, s = divmod(total_s, 60)
    if m > 0:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def _format_cost(cost: float | None) -> str:
    if cost is None:
        return "N/A"
    return f"${cost:.2f}"


_STEP_LABELS = {
    StepKind.CS: "CS",
    StepKind.DS: "DS",
    StepKind.CR: "CR",
    StepKind.COMMIT: "Commit",
}


class ActivityLog:
    """Manages a scrollable list of rendered one-liners.

    Note: ``show_tools`` filters at render-time (not storage-time) so tool
    events can be toggled on/off at runtime.  This differs from
    ``show_thinking`` which filters at storage-time in ``_render_event``.
    """

    _IDLE_THRESHOLD: float = 15.0  # seconds before showing idle indicator

    def __init__(self, max_lines: int = 2000, show_tools: bool = False) -> None:
        self._lines: list[tuple[Text, bool]] = []  # (rendered_text, is_tool_use)
        self._max_lines = max_lines
        self.auto_scroll = True
        self.scroll_offset = 0  # lines from bottom
        self._new_lines_since_pause: int = 0
        self._show_tools: bool = show_tools
        self._visible_cache: list[Text] | None = None
        self._session_active: bool = False
        self._last_event_at: float = 0  # monotonic timestamp

    @property
    def show_tools(self) -> bool:
        return self._show_tools

    @show_tools.setter
    def show_tools(self, value: bool) -> None:
        if value != self._show_tools:
            self._show_tools = value
            self._visible_cache = None
            # Snap to bottom on toggle to avoid disorienting scroll jumps
            self.scroll_offset = 0
            self.auto_scroll = True
            self._new_lines_since_pause = 0

    def set_session_active(self, active: bool) -> None:
        """Signal whether a Claude subprocess is currently running."""
        self._session_active = active
        if active:
            self._last_event_at = time.monotonic()

    def add_event(self, event: StreamEvent, show_thinking: bool = False) -> None:
        line = self._render_event(event, show_thinking)
        if line is not None:
            is_tool = isinstance(event, ToolUseEvent)
            self._lines.append((line, is_tool))
            self._last_event_at = time.monotonic()
            self._visible_cache = None
            if len(self._lines) > self._max_lines:
                self._lines = self._lines[-self._max_lines:]
                self._visible_cache = None
            if self.auto_scroll:
                self.scroll_offset = 0
            else:
                # Only count visible events for the "new lines" indicator
                if self._show_tools or not is_tool:
                    self._new_lines_since_pause += 1

    def _render_event(self, event: StreamEvent, show_thinking: bool) -> Text | None:
        _kw = {"no_wrap": True, "overflow": "ellipsis"}
        match event:
            case ToolUseEvent(tool_name=name, input_summary=summary):
                return Text(f"● {name} {summary}", style="dim", **_kw)
            case ToolResultEvent():
                return None  # skip, too noisy
            case TextEvent(text=text, is_thinking=True):
                if not show_thinking:
                    return None
                return Text(f"💭 {text}", style="dim italic", **_kw)
            case TextEvent(text=text, is_thinking=False):
                return Text(f"◆ {text}", style="white", **_kw)
            case MarkerEvent(marker_type=mt, payload=payload):
                style = "bold red" if mt == MarkerType.HALT else "bold green"
                return Text(f"▶ {mt.value}: {payload}", style=style, **_kw)
            case InitEvent(model=model, tools=tools, permission_mode=pm):
                return Text(f"Started: {model}, {len(tools)} tools, {pm}", style="cyan", **_kw)
            case ResultEvent(num_turns=turns, duration_ms=dur, cost_usd=cost, is_error=err):
                if err:
                    return Text(f"✗ Error: {turns} turns, {_format_duration(dur)}", style="bold red", **_kw)
                return Text(
                    f"✓ Done: {turns} turns, {_format_duration(dur)}, {_format_cost(cost)}",
                    style="bold green",
                    **_kw,
                )
            case RateLimitEvent(status=status, resets_at=resets_at):
                if status == "allowed":
                    return None
                if resets_at:
                    delta = (resets_at - datetime.now(timezone.utc)).total_seconds()
                    countdown = _format_elapsed(max(0, delta))
                    return Text(f"⚠ Rate limited — resets in {countdown}", style="bold yellow", **_kw)
                return Text("⚠ Rate limited", style="bold yellow", **_kw)
            case SystemEvent(subtype=st):
                if st == "task_started":
                    return Text(f"⚙ {st}", style="dim", **_kw)
                return None  # skip hooks
            case UnknownEvent():
                return None
        return None

    def _visible_lines(self) -> list[Text]:
        """Return lines filtered by current show_tools setting (cached)."""
        if self._visible_cache is not None:
            return self._visible_cache
        if self._show_tools:
            result = [text for text, _ in self._lines]
        else:
            result = [text for text, is_tool in self._lines if not is_tool]
        self._visible_cache = result
        return result

    def render(self, height: int = 30) -> RenderableType:
        if not self._lines:
            return Text("Waiting for events...", style="dim italic")

        lines = self._visible_lines()
        if not lines:
            return Text("Waiting for events...", style="dim italic")

        show_indicator = not self.auto_scroll and self._new_lines_since_pause > 0
        show_idle = (
            self._session_active
            and self._last_event_at > 0
            and (time.monotonic() - self._last_event_at) >= self._IDLE_THRESHOLD
        )
        reserved = (1 if show_indicator else 0) + (1 if show_idle else 0)
        content_height = max(1, height - reserved)

        # Clamp scroll_offset so we never scroll past the first line
        max_offset = max(0, len(lines) - content_height)
        self.scroll_offset = min(self.scroll_offset, max_offset)

        end = len(lines) - self.scroll_offset
        start = max(0, end - content_height)
        end = max(start, end)
        visible = list(lines[start:end])

        if show_indicator:
            visible.append(Text(f"▼ {self._new_lines_since_pause} new lines", style="bold yellow"))

        # Idle indicator: show when a session is running but no events arrived recently
        if show_idle:
            idle = time.monotonic() - self._last_event_at
            visible.append(Text(f"⏳ Processing... ({_format_elapsed(idle)} since last output)", style="dim italic"))

        return Group(*visible)

    def scroll_up(self, lines: int = 3) -> None:
        self.auto_scroll = False
        # Soft cap: render() does the real clamping based on visible height
        max_offset = max(0, len(self._visible_lines()) - 1)
        self.scroll_offset = min(self.scroll_offset + lines, max_offset)

    def scroll_down(self, lines: int = 3) -> None:
        self.scroll_offset = max(0, self.scroll_offset - lines)
        if self.scroll_offset == 0:
            self.auto_scroll = True
            self._new_lines_since_pause = 0


class Dashboard:
    """Renders the orchestration status pane."""

    def __init__(self) -> None:
        self.story_state: StoryState | None = None
        self.story_number: int = 0
        self.step_elapsed: float = 0
        self.story_elapsed: float = 0
        self.total_elapsed: float = 0
        self.step_cost: float | None = None
        self.total_cost: float = 0
        self.rate_limit_active: bool = False
        self.rate_limit_resets_at: datetime | None = None
        self.countdown_message: str | None = None
        # Timer anchors (monotonic timestamps) for live ticking
        self._step_start: float = 0
        self._story_start: float = 0
        self._total_start: float = 0
        # Sprint overview stats
        self.total_epics: int = 0
        self.done_epics: int = 0
        self.total_stories: int = 0
        self.done_stories: int = 0

    def update_state(
        self,
        story_state: StoryState,
        story_number: int,
        step_elapsed: float,
        story_elapsed: float,
        total_elapsed: float,
        total_cost: float,
    ) -> None:
        self.story_state = story_state
        self.story_number = story_number
        self.step_elapsed = step_elapsed
        self.story_elapsed = story_elapsed
        self.total_elapsed = total_elapsed
        self.total_cost = total_cost

    def set_timer_anchors(self, step_start: float, story_start: float, total_start: float) -> None:
        """Set monotonic timestamp anchors for live timer computation."""
        self._step_start = step_start
        self._story_start = story_start
        self._total_start = total_start

    def freeze_timers(self) -> None:
        """Freeze all timers at their current values by clearing anchors."""
        now = time.monotonic()
        if self._step_start > 0:
            self.step_elapsed = now - self._step_start
        if self._story_start > 0:
            self.story_elapsed = now - self._story_start
        if self._total_start > 0:
            self.total_elapsed = now - self._total_start
        self._step_start = 0
        self._story_start = 0
        self._total_start = 0

    def update_sprint_stats(
        self,
        total_epics: int,
        done_epics: int,
        total_stories: int,
        done_stories: int,
    ) -> None:
        """Update sprint overview counters."""
        self.total_epics = total_epics
        self.done_epics = done_epics
        self.total_stories = total_stories
        self.done_stories = done_stories

    def update_rate_limit(self, active: bool, resets_at: datetime | None = None) -> None:
        self.rate_limit_active = active
        self.rate_limit_resets_at = resets_at

    def render(self) -> RenderableType:
        # Compute live timer values from anchors
        now = time.monotonic()
        if self._step_start > 0:
            self.step_elapsed = now - self._step_start
        if self._story_start > 0:
            self.story_elapsed = now - self._story_start
        if self._total_start > 0:
            self.total_elapsed = now - self._total_start

        lines: list[Text] = []

        if self.story_state is None:
            lines.append(Text("No active story", style="dim"))
        else:
            ss = self.story_state
            lines.append(Text(f"Story {self.story_number}: {ss.story_key} ({ss.story_id})", style="bold"))
            lines.append(Text(""))

            # Mini-history: show every result with per-round timing
            all_kinds = [StepKind.CS, StepKind.DS, StepKind.CR, StepKind.COMMIT]

            # Determine if the current step is still in-progress
            active_in_progress = False
            if ss.current_step is not None:
                n_done = sum(1 for r in ss.step_results if r.kind == ss.current_step)
                if ss.current_step in (StepKind.DS, StepKind.CR):
                    active_in_progress = ss.current_round > n_done
                else:
                    active_in_progress = n_done == 0

            # Count completed results per kind & decide which need round labels
            kind_counts: dict[StepKind, int] = {}
            for r in ss.step_results:
                kind_counts[r.kind] = kind_counts.get(r.kind, 0) + 1
            needs_round: set[StepKind] = {k for k, c in kind_counts.items() if c > 1}
            if active_in_progress and ss.current_step in kind_counts:
                needs_round.add(ss.current_step)

            # 1) Completed results — one line per result, chronological
            round_idx: dict[StepKind, int] = {}
            for r in ss.step_results:
                round_idx[r.kind] = round_idx.get(r.kind, 0) + 1
                label = _STEP_LABELS[r.kind]
                if r.kind in needs_round:
                    label = f"{label} r{round_idx[r.kind]}"
                marker = "✗" if not r.success else "✓"
                style = "red" if not r.success else "green"
                line = Text(
                    f"  {marker} {label:8s}  {r.num_turns} turns  {_format_duration(r.duration_ms)}  {_format_cost(r.cost_usd)}",
                    style=style,
                )
                lines.append(line)

            # 2) Currently active step
            if active_in_progress:
                label = _STEP_LABELS[ss.current_step]
                if ss.current_step in needs_round:
                    label = f"{label} r{ss.current_round}"
                line = Text(f"  ● {label:8s}  {_format_elapsed(self.step_elapsed)}", style="bold white")
                lines.append(line)

            # 3) Pending steps (kinds not yet seen and not active)
            seen = set(kind_counts)
            if ss.current_step is not None:
                seen.add(ss.current_step)
            for kind in all_kinds:
                if kind not in seen:
                    lines.append(Text(f"  ○ {_STEP_LABELS[kind]}", style="dim"))

            lines.append(Text(""))

        # Sprint overview
        if self.total_epics > 0 or self.total_stories > 0:
            sprint = f"Sprint: {self.done_epics}/{self.total_epics} epics | {self.done_stories}/{self.total_stories} stories done"
            lines.append(Text(sprint, style="cyan"))

        # Timers
        timers = f"Step: {_format_elapsed(self.step_elapsed)}  |  Story: {_format_elapsed(self.story_elapsed)}  |  Total: {_format_elapsed(self.total_elapsed)}"
        lines.append(Text(timers))

        # Cost
        step_cost_str = _format_cost(self.step_cost)
        total_cost_str = _format_cost(self.total_cost)
        lines.append(Text(f"Cost: {step_cost_str} (step) / {total_cost_str} (total)"))

        # Rate limit
        if self.rate_limit_active and self.rate_limit_resets_at:
            delta = (self.rate_limit_resets_at - datetime.now(timezone.utc)).total_seconds()
            countdown = _format_elapsed(max(0, delta))
            lines.append(Text(f"⚠ Rate limited — resets in {countdown}", style="bold yellow"))

        # Countdown between stories
        if self.countdown_message:
            lines.append(Text(""))
            lines.append(Text(self.countdown_message, style="yellow"))

        return Group(*lines)


class TUI:
    """Top-level TUI data coordinator — dispatches events to ActivityLog and Dashboard."""

    def __init__(self, show_thinking: bool = False, show_tools: bool = False) -> None:
        self.activity_log = ActivityLog(show_tools=show_tools)
        self.dashboard = Dashboard()
        self.show_thinking = show_thinking

    def handle_event(self, event: StreamEvent) -> None:
        self.activity_log.add_event(event, self.show_thinking)

        match event:
            case ResultEvent(cost_usd=cost):
                if cost is not None:
                    self.dashboard.step_cost = cost
                    self.dashboard.total_cost += cost
            case RateLimitEvent(status=status, resets_at=resets_at):
                self.dashboard.update_rate_limit(
                    active=(status != "allowed"),
                    resets_at=resets_at,
                )


# --- Textual widgets and App ---


class ActivityLogWidget(Static):
    """Textual widget wrapping ActivityLog for display with mouse scroll support."""

    DEFAULT_CSS = "ActivityLogWidget { height: 1fr; border: solid blue; border-title-align: center; }"
    BORDER_TITLE = "Activity Log"

    def __init__(self, activity_log: ActivityLog) -> None:
        super().__init__()
        self._log = activity_log

    def render(self) -> RenderableType:
        return self._log.render(height=max(1, self.size.height - 2))

    def on_mouse_scroll_up(self, event: MouseScrollUp) -> None:
        self._log.scroll_up(lines=3)
        self.refresh()

    def on_mouse_scroll_down(self, event: MouseScrollDown) -> None:
        self._log.scroll_down(lines=3)
        self.refresh()


class DashboardWidget(Static):
    """Textual widget wrapping Dashboard for display."""

    DEFAULT_CSS = "DashboardWidget { height: auto; max-height: 50%; border: solid green; border-title-align: center; }"
    BORDER_TITLE = "Dashboard"

    def __init__(self, dashboard: Dashboard) -> None:
        super().__init__()
        self._dash = dashboard

    def render(self) -> RenderableType:
        return self._dash.render()


class StoryRunnerApp(App):
    """Top-level Textual app composing ActivityLogWidget and DashboardWidget."""

    CSS = "Screen { layout: vertical; }"

    BINDINGS = [
        Binding("pageup", "scroll_activity(-20)", "Page Up", show=False),
        Binding("pagedown", "scroll_activity(20)", "Page Down", show=False),
        Binding("up", "scroll_activity(-1)", "Up", show=False),
        Binding("down", "scroll_activity(1)", "Down", show=False),
        Binding("t", "toggle_tools", "Toggle tools", show=False),
        Binding("q", "quit", "Quit", show=False),
        Binding("enter", "close_if_finished", "Close", show=False),
    ]

    def __init__(self, tui: TUI, config: "SessionConfig") -> None:
        super().__init__()
        self._tui = tui
        self._config = config
        self._exit_code = 1
        self._finished = False

    def compose(self) -> ComposeResult:
        yield ActivityLogWidget(self._tui.activity_log)
        yield DashboardWidget(self._tui.dashboard)

    def on_mount(self) -> None:
        self.set_interval(0.25, self._refresh_widgets)
        self.run_worker(self._run_orchestrator, thread=False)

    def _refresh_widgets(self) -> None:
        activity_widget = self.query_one(ActivityLogWidget)
        activity_widget.refresh()
        # layout=True: height:auto needs a layout pass to resize when content changes
        self.query_one(DashboardWidget).refresh(layout=True)
        # Update border title to reflect tools visibility
        log = activity_widget._log
        activity_widget.border_title = "Activity Log [T]" if log.show_tools else "Activity Log"
        # Update scroll indicator in border subtitle
        if not log.auto_scroll and log._new_lines_since_pause > 0:
            activity_widget.border_subtitle = f"▼ {log._new_lines_since_pause} new lines"
        else:
            activity_widget.border_subtitle = ""

    async def _run_orchestrator(self) -> None:
        from .orchestrator import run_stories as _run_stories

        try:
            story_count = await _run_stories(self._config, self._tui)
            self._exit_code = 0 if story_count > 0 else 1
        except Exception as exc:
            self._tui.handle_event(TextEvent(
                text=f"FATAL: Orchestrator crashed: {exc}",
                is_thinking=False,
            ))
            self._exit_code = 1
        finally:
            self._finished = True
            self._tui.dashboard.freeze_timers()
            self._tui.dashboard.countdown_message = "Finished -- press Enter to close"

    def action_toggle_tools(self) -> None:
        self._tui.activity_log.show_tools = not self._tui.activity_log.show_tools
        self.query_one(ActivityLogWidget).refresh()

    def action_close_if_finished(self) -> None:
        if self._finished:
            self.exit()

    def action_scroll_activity(self, delta: int) -> None:
        if delta < 0:
            self._tui.activity_log.scroll_up(lines=abs(delta))
        elif delta > 0:
            self._tui.activity_log.scroll_down(lines=delta)
        self.query_one(ActivityLogWidget).refresh()

    def on_unmount(self) -> None:
        from .claude_session import cleanup_subprocess

        cleanup_subprocess()
