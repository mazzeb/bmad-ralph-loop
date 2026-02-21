# CLAUDE.md — BMAD Ralph Loop

## What This Is

Autonomous story runner that executes the full BMAD implementation cycle (Create Story → Dev Story → Code Review → Git Commit) unattended via Claude Code CLI sessions. Features a two-pane Rich TUI with activity log and dashboard.

## Tech Stack

- Python 3.10+, async/await throughout
- `rich` for TUI, `pyyaml` for sprint status
- `uv` for package management, `hatchling` for builds
- Claude Code CLI as the execution engine

## Project Layout

```
run_stories/          # Main package
  cli.py              # Entry point, argparse, signal handlers
  orchestrator.py     # CS→DS→CR→commit state machine
  claude_session.py   # Async subprocess runner, stream tee
  stream_parser.py    # Pure stateless JSON stream parser
  tui.py              # ActivityLog + Dashboard (rich)
  models.py           # Typed dataclasses, enums
  sprint_status.py    # YAML query helpers
  PROMPT-*.md         # Prompt templates (CS, DS, CR)
tests/                # 73 tests (pytest)
  test_stream_parser.py  # 32 tests — parser logic
  test_tui.py            # 35 tests — TUI rendering
  test_orchestrator.py   # 6 tests — orchestration
  fixtures/              # Sample log files
install.sh            # Copies package to target project
run-stories           # Bash wrapper (delegates to uv)
```

## Commands

```bash
# Install dependencies
uv sync

# Run all tests
pytest tests/ -v

# Run the story runner
./run-stories
./run-stories --max-stories 1 --dry-run

# Install into a parent project
./install.sh /path/to/target
```

## Key Patterns

- **Marker-based orchestration**: XML markers in Claude output (`<CREATE_STORY_COMPLETE>`, `<HALT>`, `<CODE_REVIEW_APPROVED>`, etc.) drive state transitions
- **Event-driven parsing**: `stream_parser.parse_line()` is pure/stateless — takes JSON, returns typed `StreamEvent` dataclasses
- **Fresh sessions per step**: Each CS/DS/CR step runs in its own Claude CLI subprocess with `--output-format stream-json`
- **Sprint status as state store**: `_bmad-output/implementation-artifacts/sprint-status.yaml` tracks story progression (`backlog` → `ready-for-dev` → `review` → `done`)
- **Story keys**: Follow `{epic-num}-{story-num}-{slug}` pattern (e.g., `1-2-user-auth`)

## Style Conventions

- Type hints on all function signatures
- Dataclasses for structured data (not dicts)
- Docstrings on public functions
- Conventional commits: `feat:`, `fix:`, `refactor:`, `docs:`, `test:`
