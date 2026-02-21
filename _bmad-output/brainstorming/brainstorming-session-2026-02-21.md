---
stepsCompleted: [1, 2, 3, 4]
inputDocuments: []
session_topic: 'Python rewrite of run-stories.sh with two-pane TUI — live interactive Claude session view + orchestration status pane'
session_goals: 'Interactive feel showing Claude sessions live, orchestration status overlay, better testability via Python, real-time stream-json parsing'
selected_approach: 'ai-recommended'
techniques_used: ['question-storming', 'morphological-analysis', 'scamper']
ideas_generated: [18]
context_file: ''
session_active: false
workflow_completed: true
---

# Brainstorming Session Results

**Facilitator:** Matthias
**Date:** 2026-02-21

## Session Overview

**Topic:** Python rewrite of run-stories.sh with two-pane TUI
**Goals:** Interactive-feeling Claude session view + orchestration status, better testability, real-time stream parsing

### Context

Current system: `run-stories.sh` (379-line bash script) runs Claude Code sessions with `--output-format stream-json`, saves logs to files, shows only summary lines (story key, step, status) to terminal. Goal is a Python rewrite that shows the Claude session live as a minimalistic activity log, alongside an orchestration status dashboard.

### Stream-JSON Format Reference

From analysis of real log files (`plutus` project):
- **Message types:** `system` (init, hooks), `assistant` (text, thinking, tool_use), `user` (tool_result, text), `result`, `rate_limit_event`
- **Session profile:** ~170-340 JSON lines per step. 85%+ are tool_use/tool_result. Few text messages.
- **Result event:** Contains `duration_ms`, `num_turns`, `total_cost_usd`, `modelUsage`
- **Rate limit event:** Contains `status` ("allowed"/"limited"), `resetsAt` timestamp, `rateLimitType`
- **Init event:** Contains `model`, `tools` list, `permissionMode`

## Technique Selection

**Approach:** AI-Recommended Techniques

- **Question Storming:** Mapped the full design space — 17 questions covering display style, framework choice, stream parsing, layout, rate limits, testing, and cost tracking
- **Morphological Analysis:** Systematically evaluated TUI framework (rich vs textual vs curses), layout (top/bottom vs left/right), stream reading (asyncio vs threads), CLI (argparse vs click vs typer)
- **SCAMPER:** Stress-tested design — surfaced tee pattern, mini-history, decoupled parser, elimination of post-processing jq step

## Design Decisions

| Parameter | Decision | Rationale |
|---|---|---|
| TUI Framework | `rich` (Live + Layout) | Lightweight, styled text + progress + panels out of the box |
| Layout | Top/Bottom 70/30 | Full-width stream lines don't get truncated |
| Stream Reading | `asyncio` subprocess | Concurrent stream reading + timer updates + TUI refresh |
| Process Mgmt | Sequential (one subprocess at a time) | Stories are sequential; asyncio only for I/O within each step |
| Testing | Real log fixtures + unit tests | Parse actual log files as fixtures; unit test parser and renderer separately |
| CLI | `argparse` | Zero deps, same flags as bash script |

## Ideas — Organized by Theme

### Theme 1: Architecture & Modularity

1. **Decoupled stream parser** — Generic JSON-line parser producing typed Python objects. Reusable for any `claude --output-format stream-json` session.
2. **Tee pattern for log + display** — Every JSON line feeds both the log file (append) and the TUI (parse + render) simultaneously. No post-processing.
3. **Real-time marker detection** — Detect `<HALT>`, `<CREATE_STORY_COMPLETE>`, `<CODE_REVIEW_APPROVED>` etc. as they stream in, not after session ends.
4. **Sequential orchestration, async I/O** — One subprocess at a time (CS→DS→CR), but asyncio for concurrent stream reading + timer updates + TUI refresh.

### Theme 2: TUI Display Design

5. **Top/Bottom 70/30 layout** — Stream pane (top, full width) + orchestration pane (bottom, compact dashboard). `rich.live.Live` + `rich.layout.Layout`.
6. **Minimalistic activity log** — Compact one-liners: `● Read file.py`, `● Bash pytest`, `◆ Assistant text...`, `⚠ Rate limited`.
7. **Color coding by event type** — Tool calls: dim/gray. Assistant text: white. Warnings/rate limits: yellow. Completions: green.
8. **Mini-history in orchestration pane** — Completed steps show stats: `✓ CS  24 turns  8m17s  $2.62` / `● DS  12/200  2m14s  $0.84  [round 1/3]` / `○ CR`
9. **Three-level timers** — Step elapsed, story elapsed, total elapsed — all ticking live.
10. **Running cost display** — Per-step cost from `result` event + running total across the sprint.
11. **Session init one-liner** — `Started: opus, 19 tools, bypassPermissions` from the `init` event.
12. **Rate limit countdown** — Yellow `⚠ Rate limited — resets in 4m32s` with live countdown from `resetsAt` timestamp.

### Theme 3: CLI & Configuration

13. **Same flags as bash script** — `--max-stories`, `--max-turns-ds`, `--max-review-rounds`, `--dev-model`, `--review-model`, `--dry-run` via argparse.
14. **`--show-thinking` opt-in flag** — Thinking blocks hidden by default, visible with flag.
15. **Future: `--scroll-back`** — Auto-scroll default, scroll-back as later enhancement.

### Theme 4: Testability

16. **Real log fixtures** — Use actual log files as test fixtures — parse them, verify display output.
17. **Unit test the parser separately** — JSON line → typed event object. Pure function, easy to test.
18. **Unit test the renderer separately** — Event object → rich renderable. Mock the Layout, assert output strings.

## Prioritization

**Top priority (the core):**
1. Stream parser (decoupled, tested against real logs)
2. TUI layout with rich (top/bottom, activity log + dashboard)
3. Orchestration logic (port the bash CS→DS→CR→commit loop)

**High value, low effort:**
4. Tee pattern (log + display simultaneously)
5. Real-time marker detection
6. Color coding

**Nice polish:**
7. Rate limit countdown
8. Three-level timers + cost
9. `--show-thinking` flag

## Action Plan — Module Structure

```
run_stories/
  __init__.py
  cli.py              # argparse, entry point
  orchestrator.py      # story loop: CS → DS → CR → commit
  claude_session.py    # asyncio subprocess runner, tee to log + parser
  stream_parser.py     # JSON line → Event dataclass (pure, testable)
  tui.py               # rich Live + Layout, renders events
  models.py            # Event types, StoryState, SessionStats
tests/
  fixtures/            # real log files
  test_stream_parser.py
  test_tui.py
  test_orchestrator.py
```

## Session Insights

- The stream-json output is 85%+ tool calls — a chat-style UI would be mostly noise. The minimalistic activity log format is the right abstraction.
- The `result` event contains rich metadata (cost, turns, duration) that makes the orchestration pane genuinely useful, not just decorative.
- Decoupling parser from renderer from orchestrator gives three independently testable layers.
- The tee pattern (simultaneous log + display) is cleaner than the bash approach of writing to file then parsing after.
