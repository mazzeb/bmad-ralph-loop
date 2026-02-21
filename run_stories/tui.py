"""Two-pane TUI using rich: activity log (top 70%) + orchestration dashboard (bottom 30%)."""

from __future__ import annotations

import shutil
import time
from datetime import datetime, timezone

from rich.console import Group, RenderableType
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text

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
    """Manages a scrollable list of rendered one-liners."""

    def __init__(self, max_lines: int = 2000) -> None:
        self._lines: list[Text] = []
        self._max_lines = max_lines
        self.auto_scroll = True
        self.scroll_offset = 0  # lines from bottom

    def add_event(self, event: StreamEvent, show_thinking: bool = False) -> None:
        line = self._render_event(event, show_thinking)
        if line is not None:
            self._lines.append(line)
            if len(self._lines) > self._max_lines:
                self._lines = self._lines[-self._max_lines:]
            if self.auto_scroll:
                self.scroll_offset = 0

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

    def render(self, height: int = 30) -> RenderableType:
        if not self._lines:
            return Text("Waiting for events...", style="dim italic")

        end = len(self._lines) - self.scroll_offset
        start = max(0, end - height)
        end = max(start, end)
        visible = self._lines[start:end]
        return Group(*visible)

    def scroll_up(self, lines: int = 3) -> None:
        self.auto_scroll = False
        max_offset = max(0, len(self._lines) - 5)
        self.scroll_offset = min(self.scroll_offset + lines, max_offset)

    def scroll_down(self, lines: int = 3) -> None:
        self.scroll_offset = max(0, self.scroll_offset - lines)
        if self.scroll_offset == 0:
            self.auto_scroll = True


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

            # Mini-history: show the last result for each step kind
            all_kinds = [StepKind.CS, StepKind.DS, StepKind.CR, StepKind.COMMIT]
            # Use last result per kind to handle multi-round DS/CR
            last_by_kind: dict[StepKind, StepResult] = {}
            for r in ss.step_results:
                last_by_kind[r.kind] = r

            for kind in all_kinds:
                label = _STEP_LABELS[kind]
                if kind in last_by_kind:
                    r = last_by_kind[kind]
                    line = Text(f"  ✓ {label:6s}  {r.num_turns} turns  {_format_duration(r.duration_ms)}  {_format_cost(r.cost_usd)}", style="green")
                    lines.append(line)
                elif kind == ss.current_step:
                    extra = ""
                    if kind in (StepKind.DS, StepKind.CR) and ss.current_round > 0:
                        extra = f"  [round {ss.current_round}]"
                    line = Text(f"  ● {label:6s}  {_format_elapsed(self.step_elapsed)}{extra}", style="bold white")
                    lines.append(line)
                else:
                    lines.append(Text(f"  ○ {label}", style="dim"))

            lines.append(Text(""))

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
    """Top-level TUI manager composing activity log and dashboard."""

    def __init__(self, show_thinking: bool = False) -> None:
        self.activity_log = ActivityLog()
        self.dashboard = Dashboard()
        self.show_thinking = show_thinking
        self._layout = Layout()
        self._layout.split_column(
            Layout(name="activity", ratio=7),
            Layout(name="dashboard", ratio=3),
        )

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

    def update_timers(
        self,
        step_elapsed: float,
        story_elapsed: float,
        total_elapsed: float,
    ) -> None:
        if self.dashboard.story_state is not None:
            self.dashboard.step_elapsed = step_elapsed
            self.dashboard.story_elapsed = story_elapsed
        self.dashboard.total_elapsed = total_elapsed

    def get_renderable(self) -> RenderableType:
        term_h = shutil.get_terminal_size().lines
        activity_h = max(5, int(term_h * 7 / 10) - 2)
        self._layout["activity"].update(
            Panel(self.activity_log.render(height=activity_h), title="Activity Log", border_style="blue")
        )
        self._layout["dashboard"].update(
            Panel(self.dashboard.render(), title="Dashboard", border_style="green")
        )
        return self._layout
