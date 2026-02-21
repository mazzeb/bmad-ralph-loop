---
title: 'Activity Pane Scrolling via Textual Migration'
slug: 'activity-pane-scrolling-textual'
created: '2026-02-21'
status: 'ready-for-dev'
stepsCompleted: [1, 2, 3, 4]
tech_stack: [python-3.10+, textual->=0.80.0, rich->=13.0, asyncio, pytest, pytest-asyncio]
files_to_modify: [run_stories/tui.py, run_stories/cli.py, pyproject.toml, tests/test_tui.py]
code_patterns: [dataclass-models, event-driven-parsing, async-orchestration, periodic-refresh-4hz, tui-as-data-coordinator]
test_patterns: [render-to-text-helper, pytest-classes, mock-based-integration]
---

# Tech-Spec: Activity Pane Scrolling via Textual Migration

**Created:** 2026-02-21

## Overview

### Problem Statement

The activity pane in the TUI has no user interaction — there is no way to scroll through history. The `ActivityLog` class already has `scroll_up()`, `scroll_down()`, and `auto_scroll` logic, but nothing calls these methods because Rich's `Live` display cannot capture keyboard or mouse input.

### Solution

Migrate the TUI rendering layer from Rich `Live` to Textual, gaining native keyboard and mouse input handling. This enables:
- Page Up/Down and arrow key scrolling
- Mouse wheel scrolling
- Auto-scroll pause when user scrolls up, resume when scrolled back to bottom
- A "new lines" indicator when auto-scroll is paused

### Scope

**In Scope:**
- Replace Rich `Live` rendering in `cli.py` with a Textual `App`
- Create Textual widgets wrapping existing `ActivityLog` and `Dashboard` data classes
- Keyboard bindings: Page Up/Down (jump), Arrow Up/Down (single line)
- Mouse wheel scrolling on the activity pane
- Auto-scroll pause/resume logic (already exists in `ActivityLog`)
- Scroll indicator showing count of new lines when not at bottom
- Adapt `pyproject.toml` dependencies
- Adapt test suite for new rendering path

**Out of Scope:**
- Changes to `orchestrator.py` or `claude_session.py` interfaces
- Changes to `stream_parser.py` or `models.py`
- New dashboard features or layout changes
- Changes to orchestration logic

## Context for Development

### Codebase Patterns

- **Data/state classes stay pure**: `ActivityLog` and `Dashboard` are data + render classes that return Rich renderables. They have no dependency on the display layer. This separation is preserved — Textual widgets delegate rendering to these classes.
- **Event flow is one-way**: `orchestrator.py` and `claude_session.py` call `tui.handle_event(event)` and mutate `tui.dashboard.*` properties. This interface is unchanged.
- **TUI as data coordinator**: The `TUI` class coordinates event dispatch between `ActivityLog` and `Dashboard`. The Textual `App` wraps `TUI` rather than replacing it, keeping the orchestrator integration intact.
- **Periodic refresh**: Currently 4 Hz via `asyncio.sleep(0.25)` loop. Textual equivalent: `set_interval(0.25, callback)` on widgets/app.

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `run_stories/tui.py` | ActivityLog, Dashboard, TUI classes (304 lines) — primary modification target |
| `run_stories/cli.py` | Entry point, Rich Live setup (123 lines) — swap Live for Textual App |
| `run_stories/orchestrator.py` | Calls `tui.handle_event()` and `tui.dashboard.*` (286 lines) — interface must remain stable |
| `run_stories/claude_session.py` | Calls `tui.handle_event()` (190 lines) — interface must remain stable |
| `run_stories/models.py` | StreamEvent types, StoryState, SessionConfig (168 lines) — no changes |
| `tests/test_tui.py` | 35 tests for TUI layer (306 lines) — adapt for Textual |
| `tests/test_orchestrator.py` | 6 integration tests (287 lines) — verify still pass |
| `pyproject.toml` | Dependencies — add textual |

### Technical Decisions

1. **Keep `TUI` as data coordinator**: The orchestrator and claude_session import `TUI` and call `tui.handle_event()` / `tui.dashboard.*`. Rather than changing this interface, the Textual `App` wraps the `TUI` instance. Zero changes to orchestrator/claude_session. Confirmed: `TUI` is imported in 4 source files + 2 test files.

2. **Textual `Static` widgets with periodic refresh**: The activity log widget extends `Static` and renders by calling `ActivityLog.render(height)` on each refresh cycle. This reuses the existing render logic (line buffer, visible window calculation) and adds keyboard/mouse handlers on top.

3. **App-level key bindings**: Key bindings (Page Up/Down, arrows) are defined at the `App` level so they work without explicit focus management. Mouse scroll events (`on_mouse_scroll_up`/`on_mouse_scroll_down`) are handled on the activity widget directly.

4. **`set_interval` for refresh**: A 0.25s interval calls `refresh()` on both widgets, matching the current 4 Hz rate. Dashboard timers tick live because `Dashboard.render()` recomputes from monotonic anchors on every call.

5. **Orchestrator as async worker**: The orchestrator runs via `app.run_worker(coro, thread=False)` — same asyncio event loop as Textual, safe for direct method calls on `TUI`. No race conditions since asyncio is cooperative single-threaded.

6. **Dead code removal**: `TUI.get_renderable()` is only called from `cli.py` (being rewritten). `TUI.update_timers()` is never called externally. Both removed along with `TUI._layout`.

7. **Signal handling**: Move `cleanup_subprocess()` to `StoryRunnerApp.on_unmount()`. Textual manages SIGINT/SIGTERM internally; our cleanup runs on app shutdown.

8. **Install script compatibility**: `install.sh` copies `run_stories/` + `pyproject.toml`. Adding textual to deps is picked up automatically by `uv run`.

## Implementation Plan

### Tasks

- [ ] **Task 1: Add `textual` dependency**
  - File: `pyproject.toml`
  - Action: Add `"textual>=0.80.0"` to the `dependencies` list, after `"rich>=13.0"`
  - Run: `uv sync` to install
  - Notes: Textual depends on Rich internally, so version compatibility is guaranteed by Textual's own constraints.

- [ ] **Task 2: Add scroll indicator and new-lines counter to `ActivityLog`**
  - File: `run_stories/tui.py`
  - Action: Modify the `ActivityLog` class (lines 64–142):
    1. In `__init__()` (line 67), add: `self._new_lines_since_pause: int = 0`
    2. In `add_event()` (line 73), after appending to `self._lines`: if `not self.auto_scroll` and the line was visible (not None), increment `self._new_lines_since_pause += 1`
    3. In `scroll_down()` (line 139), when `self.scroll_offset == 0` sets `self.auto_scroll = True`, also reset: `self._new_lines_since_pause = 0`
    4. In `render()` (line 124), add indicator logic:
       - Compute `show_indicator = not self.auto_scroll and self._new_lines_since_pause > 0`
       - If `show_indicator`, reduce content height by 1: `content_height = height - 1`
       - After slicing visible lines, append: `Text(f"▼ {self._new_lines_since_pause} new lines", style="bold yellow")`
  - Notes: The indicator consumes one line of the visible area. The `_new_lines_since_pause` counter tracks lines added while the user is scrolled up — more informative than `scroll_offset` which just measures distance from bottom.

- [ ] **Task 3: Create Textual widgets and App class**
  - File: `run_stories/tui.py`
  - Action: Add new imports and classes after the existing `TUI` class:
    1. **Add imports** at top of file:
       ```python
       from textual.app import App, ComposeResult
       from textual.binding import Binding
       from textual.events import MouseScrollDown, MouseScrollUp
       from textual.widgets import Static
       ```
    2. **`ActivityLogWidget(Static)`** — wraps `ActivityLog` for display with mouse scroll support:
       - Constructor takes `activity_log: ActivityLog` and stores as `self._log`
       - `DEFAULT_CSS`: `height: 7fr; border: solid blue; border-title-align: center;`
       - `BORDER_TITLE = "Activity Log"`
       - `render()` returns `self._log.render(height=max(1, self.size.height - 2))` — subtracts 2 for border lines
       - `on_mouse_scroll_up(self, event: MouseScrollUp)` → `self._log.scroll_up(lines=3); self.refresh()`
       - `on_mouse_scroll_down(self, event: MouseScrollDown)` → `self._log.scroll_down(lines=3); self.refresh()`
    3. **`DashboardWidget(Static)`** — wraps `Dashboard` for display:
       - Constructor takes `dashboard: Dashboard` and stores as `self._dash`
       - `DEFAULT_CSS`: `height: 3fr; border: solid green; border-title-align: center;`
       - `BORDER_TITLE = "Dashboard"`
       - `render()` returns `self._dash.render()`
    4. **`StoryRunnerApp(App)`** — top-level app composing both widgets:
       - Constructor takes `tui: TUI` and `config: SessionConfig`, stores both
       - `CSS = "Screen { layout: vertical; }"`
       - `BINDINGS`:
         - `Binding("pageup", "scroll_activity(-20)", "Page Up", show=False)`
         - `Binding("pagedown", "scroll_activity(20)", "Page Down", show=False)`
         - `Binding("up", "scroll_activity(-1)", "Up", show=False)`
         - `Binding("down", "scroll_activity(1)", "Down", show=False)`
         - `Binding("q", "quit", "Quit", show=False)`
       - `compose()` → yields `ActivityLogWidget(self._tui.activity_log)` then `DashboardWidget(self._tui.dashboard)`
       - `on_mount()`:
         - `self.set_interval(0.25, self._refresh_widgets)` for 4 Hz refresh
         - `self.run_worker(self._run_orchestrator, thread=False)` to start the orchestrator
       - `_refresh_widgets()`:
         - Call `self.query_one(ActivityLogWidget).refresh()`
         - Call `self.query_one(DashboardWidget).refresh()`
         - Update `ActivityLogWidget.border_subtitle` — if `_log.auto_scroll` is False and `_log._new_lines_since_pause > 0`, set to `f"▼ {N} new lines"`; otherwise clear to `""`
       - `async _run_orchestrator(self)`:
         - `from .orchestrator import run_stories as _run_stories` (lazy import to avoid circular)
         - `story_count = await _run_stories(self._config, self._tui)`
         - `self._exit_code = 0 if story_count > 0 else 1`
         - `await asyncio.sleep(2)` (brief pause so user can see final state)
         - `self.exit()`
       - `action_scroll_activity(self, delta: int)`:
         - If `delta < 0`: `self._tui.activity_log.scroll_up(lines=abs(delta))`
         - If `delta > 0`: `self._tui.activity_log.scroll_down(lines=delta)`
         - `self.query_one(ActivityLogWidget).refresh()`
       - `on_unmount()`: call `cleanup_subprocess()` from `claude_session` for graceful shutdown
  - Notes: Using a single parameterized action `scroll_activity(delta)` avoids duplicating 4 methods. Negative delta = scroll up, positive = scroll down. The lazy import of `run_stories` in `_run_orchestrator` avoids a circular import since `orchestrator.py` imports `TUI` from `tui.py`.

- [ ] **Task 4: Clean up `TUI` class — remove dead code**
  - File: `run_stories/tui.py`
  - Action: Remove from the `TUI` class (lines 256–303):
    1. Remove `import shutil` (line 3) — only used by `get_renderable()` for terminal size
    2. Remove `from rich.layout import Layout` import (line 11) — no longer needed
    3. Remove `from rich.panel import Panel` import (line 12) — no longer needed
    4. Remove `self._layout` setup from `__init__()` (lines 263–267)
    5. Remove `update_timers()` method (lines 283–293) — never called externally
    6. Remove `get_renderable()` method (lines 294–303) — replaced by Textual app
  - Notes: Keep `TUI.__init__()` with `activity_log`, `dashboard`, `show_thinking`. Keep `handle_event()`. The class interface used by orchestrator/claude_session is preserved.

- [ ] **Task 5: Update `cli.py` entry point**
  - File: `run_stories/cli.py`
  - Action:
    1. Remove imports: `from rich.console import Console`, `from rich.live import Live` (lines 11–12)
    2. Add import: `from .tui import StoryRunnerApp`
    3. Replace `async def _run()` (lines 84–111) with a sync function:
       ```python
       def _run(config: SessionConfig, show_thinking: bool) -> int:
           tui = TUI(show_thinking=show_thinking)
           app = StoryRunnerApp(tui=tui, config=config)
           app.run()
           return getattr(app, '_exit_code', 1)
       ```
    4. Replace `main()` (lines 114–118):
       ```python
       def main(argv: list[str] | None = None) -> None:
           config, show_thinking = parse_args(argv)
           exit_code = _run(config, show_thinking)
           sys.exit(exit_code)
       ```
    5. Remove `import asyncio` and `import signal` (lines 4–5) — no longer needed in cli.py
    6. Remove `from .claude_session import cleanup_subprocess` (line 15) — moved to `StoryRunnerApp.on_unmount()`
  - Notes: `app.run()` is synchronous — Textual manages its own event loop internally. The `_exit_code` attribute is set by `_run_orchestrator()` in the app before it calls `self.exit()`.

- [ ] **Task 6: Adapt tests**
  - File: `tests/test_tui.py`
  - Action:
    1. **Unchanged tests (30 of 35)** — these test `ActivityLog`, `Dashboard`, and `TUI` data classes directly. They call `render_to_text(log.render())` or `render_to_text(dash.render())` and check output. Since these classes are preserved, no changes needed:
       - `TestFormatDuration` (4 tests)
       - `TestFormatElapsed` (3 tests)
       - `TestActivityLog` (14 tests) — all use `ActivityLog()` directly
       - `TestScrollBehavior` (4 tests) — test `scroll_up()`/`scroll_down()`/`auto_scroll`
       - `TestDashboard` (4 tests) — use `Dashboard()` directly + `render_to_text()`
       - `TestTUI.test_handle_result_event_updates_cost` — tests `TUI.handle_event()`
       - `TestTUI.test_handle_rate_limit_event` — tests `TUI.handle_event()`
       - `TestTUI.test_handle_rate_limit_allowed` — tests `TUI.handle_event()`
    2. **Remove 1 test:**
       - `TestTUI.test_get_renderable` — `get_renderable()` is removed. Delete this test.
    3. **Update import line** (line 25): remove `_format_duration, _format_elapsed` from the `tui` import if they were made private, or keep if still exported. Also verify import still works after `TUI` class cleanup.
    4. **Add new tests in `TestScrollBehavior` class:**
       - `test_new_lines_counter_increments`: Create ActivityLog, add 50 lines, `scroll_up()`, add 5 more lines → assert `_new_lines_since_pause == 5`
       - `test_new_lines_counter_resets_on_resume`: After incrementing, `scroll_down()` to bottom → assert `_new_lines_since_pause == 0`
       - `test_scroll_indicator_in_render`: Create ActivityLog, scroll up, add lines, render → assert `"new lines"` in rendered text
       - `test_scroll_indicator_absent_when_at_bottom`: Create ActivityLog at bottom → render → assert `"new lines"` NOT in rendered text
  - Notes: The 14 `TestActivityLog` tests all pass `height` to `render()`. If the indicator takes 1 line, the visible content height reduces by 1 — but only when scrolled up with new lines. Since these tests don't scroll up, the indicator won't appear and tests pass unchanged. The `TestScrollBehavior.test_scroll_shows_older_lines` test scrolls up but doesn't add new lines after scrolling, so no indicator appears — also unchanged.

### Acceptance Criteria

- [ ] **AC1:** Given the TUI is running with 50+ activity log lines, when the user presses Page Up, then the activity pane scrolls up by ~20 lines and auto-scroll is paused.
- [ ] **AC2:** Given the TUI is running with activity log content, when the user presses the Up arrow key, then the activity pane scrolls up by 1 line.
- [ ] **AC3:** Given the TUI is running with activity log content, when the user scrolls the mouse wheel up over the activity pane, then the pane scrolls up by 3 lines and auto-scroll pauses.
- [ ] **AC4:** Given the user has scrolled up (auto-scroll is paused), when the user scrolls down until reaching the bottom (offset = 0), then auto-scroll resumes and new lines appear automatically.
- [ ] **AC5:** Given the user has scrolled up and auto-scroll is paused, when new events arrive in the activity log, then a "▼ N new lines" indicator appears at the bottom of the visible activity pane.
- [ ] **AC6:** Given the scroll indicator is showing "▼ N new lines", when the user scrolls back to the bottom (auto-scroll resumes), then the indicator disappears and the `_new_lines_since_pause` counter resets to 0.
- [ ] **AC7:** Given the new Textual TUI is running, then the layout has two panes: activity pane (70% height, blue border, title "Activity Log") on top and dashboard (30% height, green border, title "Dashboard") on bottom.
- [ ] **AC8:** Given the migration is complete, when running `pytest tests/ -v`, then all tests pass including adapted TUI tests and unchanged orchestrator integration tests.

## Additional Context

### Dependencies

- **Add:** `textual>=0.80.0` — Textual TUI framework (built on Rich, same maintainers at Textualize)
- **Keep:** `rich>=13.0` — still used for `Text`, `Group` renderables inside widgets and data classes
- **Keep:** `pyyaml>=6.0` — unchanged

### Testing Strategy

1. **Unit tests** for ActivityLog (event rendering, scroll indicator, new-lines counter) — pure Python, no Textual dependency needed
2. **Unit tests** for Dashboard — unchanged, tests Rich renderables directly
3. **Unit tests** for TUI data coordinator — unchanged, tests `handle_event()` dispatch
4. **Integration tests** for orchestrator — unchanged (TUI class interface stable, `test_orchestrator.py` passes as-is)
5. **Smoke test**: `./run-stories --dry-run` to verify the Textual app starts, renders both panes, and exits cleanly

### Notes

- **Risk: Textual CSS rendering differences.** Textual borders may look slightly different from Rich `Panel` borders. Minor visual delta is acceptable — the structure (70/30 split, colored borders, titles) is what matters.
- **Risk: Textual event loop interaction.** The orchestrator is purely async (no blocking calls). Running it as a Textual worker with `thread=False` keeps it in the same event loop — safe for calling `tui.handle_event()` without synchronization.
- **Future:** Once on Textual, adding features like click-to-select-story, filter toggles, or status bar becomes straightforward. Out of scope for this spec.
