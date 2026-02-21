---
title: 'Python TUI Story Runner'
slug: 'python-tui-story-runner'
created: '2026-02-21'
status: 'completed'
stepsCompleted: [1, 2, 3, 4]
tech_stack: ['python3.10+', 'rich', 'asyncio', 'pyyaml', 'argparse', 'pytest']
files_to_modify:
  - run_stories/__init__.py (new)
  - run_stories/cli.py (new)
  - run_stories/orchestrator.py (new)
  - run_stories/claude_session.py (new)
  - run_stories/stream_parser.py (new)
  - run_stories/tui.py (new)
  - run_stories/models.py (new)
  - tests/test_stream_parser.py (new)
  - tests/test_tui.py (new)
  - tests/test_orchestrator.py (new)
  - pyproject.toml (new)
code_patterns:
  - 'asyncio subprocess for Claude CLI execution'
  - 'dataclasses for typed event models'
  - 'rich Live + Layout for TUI rendering'
  - 'tee pattern: simultaneous log write + parse'
  - 'PyYAML for sprint-status.yaml parsing'
test_patterns:
  - 'real log file fixtures from plutus project'
  - 'unit tests: parser (JSON line → Event), renderer (Event → rich renderable)'
  - 'integration tests: mock subprocess, verify orchestration flow'
---

# Tech-Spec: Python TUI Story Runner

**Created:** 2026-02-21

## Overview

### Problem Statement

The current `run-stories.sh` (379 lines) runs Claude Code sessions blind — only summary lines appear after each step completes. There is no visibility into what Claude is doing during a session, no live cost or timing data, and the bash script is hard to test or extend.

### Solution

Rewrite as a Python package (`run_stories/`) using `rich` for a two-pane TUI. Top pane shows a minimalistic activity log of the live Claude session (tool calls, text, rate limits). Bottom pane shows an orchestration dashboard (step progress, timers, costs). Same CS→DS→CR→commit flow, same CLI flags, but with live visibility and a testable, modular architecture.

### Scope

**In Scope:**
- Full port of `run-stories.sh` orchestration logic (CS→DS→CR→commit loop)
- Stream parser for `--output-format stream-json` (typed dataclasses)
- Two-pane TUI: activity log (top 70%) + orchestration dashboard (bottom 30%)
- Color-coded one-liner activity log (tool calls gray, text white, warnings yellow, completions green)
- Tee pattern: simultaneous log file write + TUI display
- Real-time marker detection (`<HALT>`, `<CREATE_STORY_COMPLETE>`, `<CODE_REVIEW_APPROVED>`, etc.)
- Mini-history in dashboard (completed step stats: turns, duration, cost)
- Three-level timers (step elapsed, story elapsed, total elapsed) — all ticking live
- Running cost display (per-step from `result` event + running total)
- Session init one-liner (`Started: opus, 19 tools, bypassPermissions`)
- Rate limit countdown (yellow warning with live countdown from `resetsAt` timestamp)
- `--show-thinking` opt-in flag (thinking blocks hidden by default)
- Auto-scrolling activity log with scroll-back support (auto-scroll by default, pauses on manual scroll-up)
- Commit step visible as step 4 in orchestration pane
- Same CLI flags: `--max-stories`, `--max-turns-ds`, `--max-review-rounds`, `--dev-model`, `--review-model`, `--dry-run`
- PyYAML for sprint-status.yaml parsing
- Test suite with real log fixtures + unit tests for parser and renderer separately
- `asyncio` for subprocess I/O + timer updates + TUI refresh

**Out of Scope:**
- Interactive input to Claude sessions (sessions run in `-p` print mode)
- Parallel story execution
- Web UI or remote access
- Configuration file (CLI flags only)

## Context for Development

### Codebase Patterns

- **Current implementation:** `run-stories.sh` — a single 379-line bash script at project root
- **Orchestration flow:** Sequential CS→DS→CR per story, with a CR→DS retry loop (max rounds configurable)
- **Claude invocation:** `claude -p <prompt> --verbose --max-turns N --output-format stream-json --dangerously-skip-permissions` piped to log file
- **Sprint status:** YAML file at `_bmad-output/implementation-artifacts/sprint-status.yaml`, story keys like `1-2-some-title` with statuses: `backlog`, `ready-for-dev`, `in-progress`, `review`, `done`
- **Prompt files:** `PROMPT-create-story.md`, `PROMPT-dev-story.md`, `PROMPT-code-review.md` at project root
- **Log files:** Saved to `_bmad-output/implementation-artifacts/logs/` with naming pattern `{timestamp}_{story-key}_{step}.log`
- **Commit message generation:** Separate `claude -p` call after successful story completion
- **No existing Python code** — this is a greenfield Python package

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `run-stories.sh` | Current bash implementation — the source of truth for orchestration logic |
| `PROMPT-create-story.md` | Prompt file for Create Story step |
| `PROMPT-dev-story.md` | Prompt file for Dev Story step |
| `PROMPT-code-review.md` | Prompt file for Code Review step |
| `_bmad-output/implementation-artifacts/sprint-status.yaml` | Sprint status YAML (story statuses) |
| `_bmad-output/brainstorming/brainstorming-session-2026-02-21.md` | Brainstorming session with design decisions |

### Technical Decisions

| Decision | Choice | Rationale |
| -------- | ------ | --------- |
| TUI Framework | `rich` (Live + Layout) | Lightweight, styled text + progress + panels out of the box |
| Layout | Top/Bottom 70/30 | Full-width stream lines don't get truncated |
| Stream Reading | `asyncio` subprocess | Concurrent stream reading + timer updates + TUI refresh |
| Process Management | Sequential (one subprocess at a time) | Stories are sequential; asyncio only for I/O within each step |
| Sprint Status Parsing | PyYAML | Proper YAML parsing instead of fragile grep/sed |
| CLI | `argparse` | Zero additional deps, same flags as bash script |
| Testing | Real log fixtures + unit tests | Parse actual log files as fixtures; unit test parser and renderer separately |
| Marker Detection | Parse from stream in real-time, but no early session termination | Markers update TUI display only; sessions complete naturally |

### Orchestration Markers (from prompt files)

Each step outputs XML markers that the orchestrator must detect from the stream:

**Create Story (CS):**
- `<CREATE_STORY_COMPLETE>story-key</CREATE_STORY_COMPLETE>` — success, story created
- `<NO_BACKLOG_STORIES/>` — no backlog stories left, stop
- `<HALT>reason</HALT>` — unrecoverable error

**Dev Story (DS):**
- `<DEV_STORY_COMPLETE>story-key</DEV_STORY_COMPLETE>` — success, story developed
- `<NO_READY_STORIES/>` — no ready-for-dev stories, stop
- `<HALT>reason</HALT>` — unrecoverable error

**Code Review (CR):**
- `<CODE_REVIEW_APPROVED>story-key</CODE_REVIEW_APPROVED>` — approved, story is done
- `<CODE_REVIEW_ISSUES>story-key</CODE_REVIEW_ISSUES>` — issues remain, needs another DS→CR round
- `<HALT>reason</HALT>` — unrecoverable error

**Detection approach:** Parse markers from assistant text content as they stream in. Display in TUI activity log. After session completes, use the detected markers + sprint-status.yaml to determine next action (same logic as bash script).

### Sprint Status YAML Structure

```yaml
generated: 2026-02-21
project: plutus
project_key: NOKEY
tracking_system: file-system
story_location: "_bmad-output/implementation-artifacts"

development_status:
  epic-1: in-progress
  1-1-project-scaffold-docker-deployment: done
  1-2-stock-data-fetching-from-onvista: done
  1-3-stock-search-with-autocomplete: backlog
  # ...
```

**Parsing:** `yaml.safe_load(path)` → `data['development_status'][story_key]`. Story keys match pattern `\d+-\d+-.*`, statuses are plain strings.

### Stream-JSON Format Reference

From analysis of real log files (plutus project, story 1-3):

**Message types:** `system` (init, hooks, task_started), `assistant` (text, thinking, tool_use), `user` (tool_result, text), `result`, `rate_limit_event`

**Session profile (dev-story, 368 lines):**
- assistant: 202, user: 159, system: 5, rate_limit_event: 1, result: 1
- assistant/tool_use: 157, assistant/text: 38, assistant/thinking: 7
- user/tool_result: 157, user/text: 2
- 85%+ are tool_use/tool_result — confirms minimalistic activity log is the right abstraction

**Key event structures:**
- `init`: `{type: "system", subtype: "init", model, tools[], permissionMode, session_id}`
- `tool_use`: `{type: "assistant", message: {content: [{type: "tool_use", name, input}]}}`
- `tool_result`: `{type: "user", message: {content: [{type: "tool_result", tool_use_id, content}]}}`
- `result`: `{type: "result", duration_ms, num_turns, result, is_error, subtype: "success"|"error"}`
- `rate_limit_event`: `{type: "rate_limit_event", rate_limit_info: {status, resetsAt, rateLimitType}}`

**Additional event types observed:**
- `system/hook_started` + `system/hook_response` — session hooks (init sequence)
- `system/task_started` — subagent spawned (e.g., explore agent)
- `assistant/thinking` — thinking blocks (hidden by default, `--show-thinking` to display)
- `user/text` — rare, only 2 in 368-line session (system reminders injected by Claude Code)

## Implementation Plan

### Tasks

Tasks are ordered by dependency (lowest-level, no-dependency modules first).

- [x] Task 1: Create project scaffold and pyproject.toml
  - File: `pyproject.toml` (new)
  - File: `run_stories/__init__.py` (new)
  - Action: Create `pyproject.toml` with project metadata, dependencies (`rich`, `pyyaml`), dev dependencies (`pytest`), and a `[project.scripts]` entry point `run-stories = "run_stories.cli:main"`. Create `run_stories/__init__.py` as empty package init.
  - Notes: Use `[build-system] requires = ["setuptools>=68.0"]`. Target Python `>=3.10`. Use `pip install -e .` for development.

- [x] Task 2: Define event models and data types
  - File: `run_stories/models.py` (new)
  - Action: Define dataclasses for all stream-json event types and orchestration state:
    - `InitEvent`: model, tools (list[str]), permission_mode, session_id
    - `ToolUseEvent`: tool_name, input_summary (truncated first arg or file path)
    - `ToolResultEvent`: tool_use_id, content_summary (truncated)
    - `TextEvent`: text, is_thinking (bool)
    - `ResultEvent`: duration_ms, num_turns, is_error, cost_usd (float | None), subtype
    - `RateLimitEvent`: status, resets_at (datetime), rate_limit_type
    - `SystemEvent`: subtype (hook_started, hook_response, task_started, etc.)
    - `MarkerEvent`: marker_type (enum: HALT, CREATE_STORY_COMPLETE, DEV_STORY_COMPLETE, CODE_REVIEW_APPROVED, CODE_REVIEW_ISSUES, NO_BACKLOG_STORIES, NO_READY_STORIES), payload (str)
    - `UnknownEvent`: raw_data (dict) — fallback for unrecognized lines
    - Union type: `StreamEvent = InitEvent | ToolUseEvent | ... | UnknownEvent`
    - `StepKind` enum: CS, DS, CR, COMMIT
    - `StepResult` dataclass: kind, story_key, duration_ms, num_turns, cost_usd, markers_detected (list[MarkerEvent]), success (bool)
    - `StoryState` dataclass: story_key, story_id, current_step, current_round, step_results (list[StepResult]), started_at (datetime)
    - `SessionConfig` dataclass: project_dir, max_stories, max_turns_cs, max_turns_ds, max_turns_cr, max_review_rounds, dev_model, review_model, dry_run
  - Notes: Use `@dataclass(frozen=True)` for event types (immutable). Use `@dataclass` (mutable) for `StoryState`. Extract `cost_usd` from `result` event's `total_cost_usd` field if present. For `ToolUseEvent.input_summary`, extract `file_path` from Read/Edit/Write/Glob inputs, `command` from Bash, `pattern` from Grep, etc. — keep it short (max ~60 chars).

- [x] Task 3: Implement stream parser
  - File: `run_stories/stream_parser.py` (new)
  - Action: Implement a pure, stateless parser:
    - `parse_line(raw: str) -> StreamEvent`: Parse a single JSON line into the appropriate event dataclass. Use `json.loads`, then dispatch on `type` field → `system`, `assistant`, `user`, `result`, `rate_limit_event`.
    - For `assistant` messages: iterate `content[]`, dispatch on content `type` → `text`, `tool_use`, `thinking`. For `text` content, scan for XML markers using regex `<(HALT|CREATE_STORY_COMPLETE|DEV_STORY_COMPLETE|CODE_REVIEW_APPROVED|CODE_REVIEW_ISSUES)>(.*?)</\1>|<(NO_BACKLOG_STORIES|NO_READY_STORIES)/>` and emit both a `TextEvent` and a `MarkerEvent` if found.
    - For `user` messages: dispatch on content `type` → `tool_result`, `text`.
    - For `system`: create `InitEvent` if subtype is `init`, otherwise `SystemEvent`.
    - For `result`: create `ResultEvent`, extracting cost from the JSON if present.
    - For `rate_limit_event`: create `RateLimitEvent`, converting `resetsAt` (unix timestamp) to `datetime`.
    - Return `UnknownEvent` for anything that doesn't match.
    - `parse_line` should never raise — wrap in try/except and return `UnknownEvent` on parse failure.
  - Notes: This is the most testable module. Every function is pure: bytes in → dataclass out. The marker regex must handle markers appearing anywhere in assistant text, including mid-sentence.

- [x] Task 4: Implement sprint status helpers
  - File: `run_stories/sprint_status.py` (new)
  - Action: Implement sprint status YAML operations:
    - `load_status(path: Path) -> dict`: `yaml.safe_load` the file, return the full dict.
    - `get_story_status(data: dict, key: str) -> str`: Return `data['development_status'][key]`, default `"unknown"`.
    - `next_backlog_story(data: dict) -> str | None`: Iterate `development_status` items, return first key matching `\d+-\d+-.*` with value `"backlog"`, or `None`.
    - `story_id_from_key(key: str) -> str`: Extract epic.story from key (e.g., `"1-3-stock-search"` → `"1.3"`).
  - Notes: Keep this module simple and pure. The bash script's `grep`/`sed` approach is replaced by proper YAML parsing. No file writing — the Claude sessions update sprint-status.yaml themselves.

- [x] Task 5: Implement TUI renderer
  - File: `run_stories/tui.py` (new)
  - Action: Implement the two-pane TUI using `rich`:
    - `class ActivityLog`: Manages a scrollable list of rendered one-liners. Methods:
      - `add_event(event: StreamEvent, show_thinking: bool)`: Render event to a styled one-liner and append to the log. Rendering rules:
        - `ToolUseEvent` → `dim` style: `"● {tool_name} {input_summary}"`
        - `ToolResultEvent` → skip (don't display, too noisy)
        - `TextEvent` (not thinking) → `white`: `"◆ {text[:120]}..."` (truncate long text)
        - `TextEvent` (thinking, only if show_thinking) → `dim italic`: `"💭 {text[:80]}..."`
        - `MarkerEvent` → `bold green` or `bold red` depending on type: `"▶ {marker_type}: {payload}"`
        - `InitEvent` → `cyan`: `"Started: {model}, {len(tools)} tools, {permission_mode}"`
        - `ResultEvent` → `bold green`/`bold red`: `"✓ Done: {num_turns} turns, {duration}, ${cost}"` or `"✗ Error: ..."`
        - `RateLimitEvent` → `bold yellow`: `"⚠ Rate limited — resets in {countdown}"`
        - `SystemEvent` → `dim`: `"⚙ {subtype}"` (only show task_started, skip hooks)
        - `UnknownEvent` → skip
      - `render() -> RenderableType`: Return the current log as a `rich.text.Text` or `Group` of lines, respecting scroll position.
      - Scroll state: track `auto_scroll: bool` (default True), `scroll_offset: int`. When `auto_scroll` is True, always show latest lines. When user scrolls up, set `auto_scroll = False`. When user scrolls back to bottom, set `auto_scroll = True`.
    - `class Dashboard`: Renders the orchestration status pane. Methods:
      - `update_state(story_state: StoryState, step_elapsed: float, story_elapsed: float, total_elapsed: float, total_cost: float)`: Update internal state.
      - `render() -> RenderableType`: Return a `rich.table.Table` or `Columns` showing:
        - Current story: `"Story 3: 1-3-stock-search (1.3)"`
        - Step progress as mini-history: `"✓ CS  35 turns  5m46s  $2.62"` / `"● DS  12 turns  2m14s  $0.84  [round 1/3]"` / `"○ CR"` / `"○ Commit"`
        - Three timers: `"Step: 2m14s  |  Story: 8m00s  |  Total: 24m31s"`
        - Running cost: `"Cost: $3.46 (step) / $12.80 (total)"`
        - Rate limit status (if active): `"⚠ Rate limited — resets in 4m32s"`
    - `class TUI`: Top-level TUI manager. Methods:
      - `__init__(show_thinking: bool)`: Create `ActivityLog`, `Dashboard`, `rich.layout.Layout` (top/bottom 70/30 ratio).
      - `handle_event(event: StreamEvent)`: Route event to `ActivityLog.add_event()`. If `ResultEvent`, also update dashboard stats.
      - `update_timers(step_elapsed, story_elapsed, total_elapsed, total_cost)`: Push timer values to `Dashboard`.
      - `get_renderable() -> RenderableType`: Return the composed `Layout` with activity log panel (top) and dashboard panel (bottom).
      - Scroll input: Listen for up/down arrow keys to control `ActivityLog` scroll offset. Use `rich.live.Live` with `screen=True` for full-screen rendering.
    - `run_tui(tui: TUI) -> None`: Async function. Start `rich.live.Live` context, refresh at ~4Hz. Run in the asyncio event loop alongside stream reading.
  - Notes: The scroll-back feature requires intercepting keyboard input while `rich.live.Live` is running. Use Python's `termios`/`tty` for raw key reading in a separate asyncio task, or use `rich`'s `Console.input` in a non-blocking way. Auto-scroll resumes when the user scrolls past the last line. The 4Hz refresh rate keeps timers ticking smoothly without excessive CPU.

- [x] Task 6: Implement Claude session runner
  - File: `run_stories/claude_session.py` (new)
  - Action: Implement the async subprocess runner with tee pattern:
    - `async def run_claude_session(prompt_file: Path, log_file: Path, max_turns: int, model: str | None, extra_prompt: str | None, tui: TUI, project_dir: Path) -> StepResult`:
      1. Read `prompt_file` content. Append `extra_prompt` if provided.
      2. Build command: `["claude", "-p", prompt_content, "--verbose", "--max-turns", str(max_turns), "--output-format", "stream-json", "--dangerously-skip-permissions"]`. Append `["--model", model]` if model is set.
      3. Start `asyncio.create_subprocess_exec(*cmd, stdout=PIPE, stderr=STDOUT, cwd=project_dir)`.
      4. Open `log_file` for writing.
      5. Read stdout line by line (`async for line in process.stdout`):
         - Write raw line to log file (tee).
         - Parse with `parse_line(line)`.
         - Send parsed event to `tui.handle_event(event)`.
         - Collect `MarkerEvent`s and `ResultEvent` for the return value.
      6. `await process.wait()` for exit code.
      7. Return `StepResult` with collected data.
    - `async def run_commit_session(story_id: str, story_key: str, story_file: Path, project_dir: Path, tui: TUI) -> StepResult`:
      1. Build the commit message generation prompt (same as bash script lines 323-340).
      2. Run `claude -p <prompt> --max-turns 5 --dangerously-skip-permissions` (no stream-json needed, just capture stdout).
      3. If generation fails, use fallback message: `"feat(story-{story_id}): implement {story_key}"`.
      4. Run `git add -A` then `git commit -m <message>` via `asyncio.create_subprocess_exec`.
      5. Update TUI with commit status.
      6. Return `StepResult`.
  - Notes: The tee pattern is the core innovation over bash — every JSON line feeds both the log file and the TUI simultaneously. The log file gets the raw JSON (identical to what bash produced), while the TUI gets parsed events. `stderr` is merged into stdout via `STDOUT` to capture any Claude CLI errors.

- [x] Task 7: Implement orchestrator
  - File: `run_stories/orchestrator.py` (new)
  - Action: Port the main loop from `run-stories.sh` (lines 189-366):
    - `async def run_stories(config: SessionConfig, tui: TUI) -> int`:
      1. Preflight checks: verify `claude` command exists, verify prompt files exist, verify sprint-status.yaml exists. Create log directory if missing.
      2. Main story loop (up to `config.max_stories`):
         a. Load sprint status, find next backlog story via `next_backlog_story()`.
         b. If no story found, break with "No more backlog stories" message.
         c. Initialize `StoryState` with story key, story ID, timestamps.
         d. **Step 1: Create Story (CS)** — Run `run_claude_session()` with CS prompt, `max_turns_cs`, `dev_model`. Check sprint status after — expect `ready-for-dev`. If not, break with error.
         e. **Step 2-3: Dev + Review loop** (up to `max_review_rounds`):
            - **Dev Story (DS)** — Run with DS prompt, `max_turns_ds`, `dev_model`. On round > 1, pass `STORY_PATH: {story_file}` as extra prompt. Check for `<HALT>` marker → break both loops. Check sprint status for `review`.
            - **Code Review (CR)** — Run with CR prompt, `max_turns_cr`, `review_model`, pass `STORY_PATH: {story_file}`. Check sprint status: if `done`, mark story done and break inner loop. If not done and rounds remain, continue loop.
         f. **Step 4: Commit** — If story done, run `run_commit_session()`. Increment story count.
         g. If story NOT done, break with warning.
         h. Pause 5 seconds before next story (display countdown in TUI).
      3. Print session summary: stories completed, log directory.
      4. Return story count.
  - Notes: This is a direct port of the bash logic. The flow must match exactly — same status checks, same error handling, same retry logic. The only differences: PyYAML instead of grep/sed, async subprocess instead of direct execution, and TUI updates at each step transition.

- [x] Task 8: Implement CLI entry point
  - File: `run_stories/cli.py` (new)
  - Action: Implement the CLI using argparse:
    - `def parse_args() -> SessionConfig`: Define arguments matching bash script:
      - `--max-stories` (default: 999)
      - `--max-turns-ds` (default: 200)
      - `--max-review-rounds` (default: 3)
      - `--dev-model` (default: "")
      - `--review-model` (default: "")
      - `--dry-run` (flag)
      - `--show-thinking` (flag)
      - `-h`/`--help`
    - Also add `--max-turns-cs` (default: 100) and `--max-turns-cr` (default: 150) — these are configurable in the bash script as constants but worth exposing.
    - Resolve `project_dir` as the directory containing the script (or cwd).
    - `def main()`: Parse args, create `SessionConfig`, create `TUI`, run `asyncio.run(run_stories(config, tui))`.
  - Notes: Help text should match the bash script's format. The `--show-thinking` flag is new (not in bash). Exit code: 0 if at least one story completed, 1 if none completed or error.

- [x] Task 9: Copy test fixtures
  - File: `tests/fixtures/` (new directory)
  - Action: Copy the three plutus log files as test fixtures:
    - `tests/fixtures/create-story.log` ← from `20260221-111447_1-3-stock-search-with-autocomplete_1-create-story.log`
    - `tests/fixtures/dev-story.log` ← from `20260221-111447_1-3-stock-search-with-autocomplete_2-dev-story-r1.log`
    - `tests/fixtures/code-review.log` ← from `20260221-111447_1-3-stock-search-with-autocomplete_3-code-review-r1.log`
  - Notes: These are real Claude session logs. They serve as ground truth for parser tests.

- [x] Task 10: Write stream parser tests
  - File: `tests/test_stream_parser.py` (new)
  - Action: Unit tests for `stream_parser.parse_line()`:
    - Test init event → `InitEvent` with model, tools, permission_mode
    - Test tool_use event → `ToolUseEvent` with name and input summary
    - Test tool_result event → `ToolResultEvent`
    - Test assistant text event → `TextEvent`
    - Test thinking event → `TextEvent(is_thinking=True)`
    - Test result event → `ResultEvent` with duration, turns, cost
    - Test rate_limit_event → `RateLimitEvent` with status, resets_at
    - Test marker detection: text containing `<HALT>reason</HALT>` → both `TextEvent` and `MarkerEvent`
    - Test marker detection: `<CREATE_STORY_COMPLETE>1-3-foo</CREATE_STORY_COMPLETE>` → `MarkerEvent(marker_type=CREATE_STORY_COMPLETE, payload="1-3-foo")`
    - Test malformed JSON → `UnknownEvent`
    - Test empty line → `UnknownEvent`
    - **Fixture integration test:** Parse every line of each fixture file, assert no exceptions, count event types and verify they match expected distribution (e.g., dev-story: ~157 ToolUseEvent, ~38 TextEvent)
  - Notes: Fixture integration tests are the highest-value tests — they prove the parser handles real-world data.

- [x] Task 11: Write TUI renderer tests
  - File: `tests/test_tui.py` (new)
  - Action: Unit tests for `tui.ActivityLog` and `tui.Dashboard`:
    - Test `ActivityLog.add_event()` with each event type → verify the rendered one-liner text and style
    - Test `ActivityLog` scroll behavior: add 100 events, verify auto-scroll shows latest; set scroll offset, verify older lines shown; scroll back to bottom, verify auto-scroll resumes
    - Test `Dashboard.render()` with a `StoryState` containing completed CS step and in-progress DS step → verify mini-history format
    - Test `Dashboard` timer display format (e.g., 125 seconds → "2m05s")
    - Test `Dashboard` cost display format
  - Notes: Use `rich.console.Console(file=StringIO())` to capture rendered output for assertions.

- [x] Task 12: Write orchestrator tests
  - File: `tests/test_orchestrator.py` (new)
  - Action: Integration tests with mocked subprocess:
    - Test happy path: CS→DS→CR all succeed, story reaches `done`, commit runs → verify 4 steps executed in order
    - Test CR rejection loop: CR returns `in-progress`, DS runs again, CR approves → verify 2 rounds
    - Test HALT during DS: `<HALT>` marker detected → verify loop breaks, no commit
    - Test no backlog stories: `next_backlog_story()` returns None → verify clean exit
    - Test max review rounds exhausted → verify warning and break
    - Mock `asyncio.create_subprocess_exec` to feed fixture log data line by line. Mock `sprint_status.load_status` to return appropriate status at each check point.
  - Notes: These tests verify the orchestration state machine without running real Claude sessions.

### Acceptance Criteria

- [ ] AC 1: Given a valid project directory with sprint-status.yaml containing backlog stories, when `run-stories` is executed, then it processes stories sequentially through CS→DS→CR→commit, identical to the bash script flow.

- [ ] AC 2: Given a running Claude session producing stream-json output, when each JSON line is emitted, then the TUI activity log displays a color-coded one-liner within the same refresh cycle (~250ms).

- [ ] AC 3: Given a tool_use event for `Read` with `file_path: "/foo/bar.py"`, when parsed and rendered, then the activity log shows `● Read /foo/bar.py` in dim/gray style.

- [ ] AC 4: Given an assistant text event containing `<CREATE_STORY_COMPLETE>1-3-stock-search</CREATE_STORY_COMPLETE>`, when parsed, then both a `TextEvent` and a `MarkerEvent(marker_type=CREATE_STORY_COMPLETE, payload="1-3-stock-search")` are produced, and the TUI shows the marker in bold green.

- [ ] AC 5: Given a `result` event with `duration_ms: 345993`, `num_turns: 35`, when rendered in the dashboard, then the completed step shows `✓ CS  35 turns  5m46s  $X.XX`.

- [ ] AC 6: Given a `rate_limit_event` with `status: "allowed"` and `resetsAt: 1771686000`, when the rate limit is active (`status` != `"allowed"`), then the TUI shows a yellow `⚠ Rate limited — resets in Xm XXs` with a live countdown. When status is `"allowed"`, no warning is shown.

- [ ] AC 7: Given the TUI is running with auto-scroll active, when the user presses the up arrow key, then auto-scroll pauses and older activity log entries become visible. When the user scrolls back to the bottom, then auto-scroll resumes.

- [ ] AC 8: Given `--show-thinking` is NOT passed, when a thinking event is received, then it is not displayed in the activity log. Given `--show-thinking` IS passed, then thinking events appear in dim italic style.

- [ ] AC 9: Given `--dry-run` is passed, when `run-stories` executes, then it prints what would run for each story without launching any Claude sessions, matching the bash script's dry-run output.

- [ ] AC 10: Given the orchestrator completes a story (status `done`), when the commit step runs, then it appears as step 4 in the dashboard mini-history, and a git commit is created with a Claude-generated message (or fallback message on failure).

- [ ] AC 11: Given the three real log fixture files from the plutus project, when parsed line by line through `stream_parser.parse_line()`, then zero exceptions are raised and the event type distribution matches the expected counts (e.g., dev-story: ~157 ToolUseEvent, ~38 TextEvent, 1 ResultEvent).

- [ ] AC 12: Given the code review returns `in-progress` status, when the retry loop runs, then dev-story executes again with `STORY_PATH` passed as extra prompt, up to `max_review_rounds` times, matching the bash script's retry behavior.

## Additional Context

### Dependencies

**Runtime:**
- `rich>=13.0` — TUI rendering (Live, Layout, Panel, Text, Table, Columns)
- `pyyaml>=6.0` — Sprint status YAML parsing
- Python `>=3.10` (match/case, dataclasses, asyncio improvements, `|` union syntax)

**Development:**
- `pytest>=7.0` — Test runner
- `pytest-asyncio>=0.21` — Async test support for orchestrator tests

### Testing Strategy

- **Real log fixtures:** Copy 3 sample logs from plutus project (CS: 142 lines, DS: 368 lines, CR: 200 lines) as `tests/fixtures/`.
- **Unit test parser:** JSON line → typed Event dataclass. Pure functions, no I/O, no mocks needed. Test each event type individually + full fixture file integration.
- **Unit test renderer:** Event → rich renderable. Use `Console(file=StringIO())` to capture output. Test each event type's one-liner format + scroll behavior.
- **Integration test orchestrator:** Mock `asyncio.create_subprocess_exec` to feed fixture data. Mock `sprint_status.load_status` to simulate status transitions. Verify step sequence, retry logic, error handling.
- **No end-to-end tests** — running real Claude sessions in tests is impractical. The fixture-based integration tests provide sufficient confidence.

### Module Structure

```
run_stories/
  __init__.py              # Package init
  cli.py                   # argparse, entry point, main()
  models.py                # Event dataclasses, StoryState, SessionConfig, enums
  stream_parser.py         # parse_line(): JSON string → StreamEvent (pure, stateless)
  sprint_status.py         # YAML load, story status queries (pure)
  claude_session.py        # Async subprocess runner, tee to log + parser
  tui.py                   # ActivityLog, Dashboard, TUI classes (rich rendering)
  orchestrator.py          # Main story loop: CS → DS → CR → commit
tests/
  fixtures/
    create-story.log       # Real CS log (142 lines)
    dev-story.log          # Real DS log (368 lines)
    code-review.log        # Real CR log (200 lines)
  test_stream_parser.py    # Parser unit tests + fixture integration
  test_tui.py              # Renderer unit tests + scroll behavior
  test_orchestrator.py     # Orchestration flow integration tests
pyproject.toml             # Project config, deps, entry point
```

### Notes

- The `run-stories.sh` script remains as-is — the Python version is a parallel replacement, not an in-place edit.
- Auto-scroll behavior: activity log scrolls automatically; scrolling up pauses auto-scroll; scrolling back to bottom resumes it.
- The `result` event may or may not contain `total_cost_usd` depending on the Claude CLI version. Handle its absence gracefully (display "N/A" for cost).
- Marker detection happens during streaming but does NOT terminate sessions early — sessions always run to completion. Markers are used after session ends to determine next orchestration action.
- The 5-second pause between stories (line 365 of bash script) should show a countdown in the TUI dashboard.
