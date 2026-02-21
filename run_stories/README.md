# Python TUI Story Runner

Replaces `run-stories.sh` with a Python package providing a two-pane TUI for live visibility into Claude Code sessions during the BMAD story cycle (Create Story → Dev Story → Code Review → commit).

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Usage

```bash
run-stories                                    # Run all remaining stories
run-stories --max-stories 1                    # Run just the next story
run-stories --dev-model opus --review-model sonnet  # Different models
run-stories --dry-run                          # Show what would run
run-stories --show-thinking                    # Display thinking blocks
```

### CLI Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--max-stories N` | 999 | Stop after N stories |
| `--max-turns-cs N` | 100 | Max turns for create-story |
| `--max-turns-ds N` | 200 | Max turns for dev-story |
| `--max-turns-cr N` | 150 | Max turns for code-review |
| `--max-review-rounds N` | 3 | Max dev→review retry rounds |
| `--dev-model MODEL` | system default | Model for CS and DS steps |
| `--review-model MODEL` | system default | Model for CR step |
| `--dry-run` | false | Print plan without executing |
| `--show-thinking` | false | Show thinking blocks in activity log |

## TUI Layout

```
┌─ Activity Log ──────────────────────────────────┐
│ Started: opus, 19 tools, bypassPermissions      │
│ ● Read _bmad-output/.../sprint-status.yaml      │
│ ● Write _bmad-output/.../1-3-stock-search.md    │
│ ◆ Story created successfully.                   │
│ ▶ CREATE_STORY_COMPLETE: 1-3-stock-search       │
│ ✓ Done: 8 turns, 45s, $1.24                     │
├─ Dashboard ─────────────────────────────────────┤
│ Story 1: 1-3-stock-search (1.3)                 │
│   ✓ CS      8 turns  45s   $1.24               │
│   ● DS      2m14s  [round 1]                    │
│   ○ CR                                          │
│   ○ Commit                                      │
│ Step: 2m14s  |  Story: 2m59s  |  Total: 2m59s  │
│ Cost: $1.24 (step) / $1.24 (total)              │
└─────────────────────────────────────────────────┘
```

## Architecture

```
run_stories/
  cli.py             # argparse entry point
  models.py          # Typed dataclasses for events and state
  stream_parser.py   # JSON line → StreamEvent (pure, stateless)
  sprint_status.py   # YAML load and query helpers
  claude_session.py  # Async subprocess with tee pattern
  tui.py             # ActivityLog + Dashboard + TUI (rich)
  orchestrator.py    # Main CS→DS→CR→commit loop
```

## Tests

```bash
pytest tests/ -v
```

73 tests covering stream parser, TUI renderer, and orchestrator logic with mocked subprocesses.

## Requirements

- Python >= 3.10
- `claude` CLI installed and authenticated
- Project with `sprint-status.yaml` (prompt templates are bundled in this package)
