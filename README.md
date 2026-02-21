# BMAD Ralph Loop — Autonomous Story Runner

Runs the full BMAD story implementation cycle unattended using Claude Code CLI sessions. Each story goes through **Create Story -> Dev Story -> Code Review** in fresh sessions, with an automatic fix loop if the review finds issues.

## Files

| File | Purpose |
|---|---|
| `run-stories` | Wrapper script — delegates to the Python package via `uv` |
| `run_stories/` | Python TUI Story Runner package |
| `pyproject.toml` | Python project config (dependencies, entry point, build system) |
| `PROMPT-create-story.md` | Prompt for the Create Story (CS) step |
| `PROMPT-dev-story.md` | Prompt for the Dev Story (DS) step |
| `PROMPT-code-review.md` | Prompt for the Code Review (CR) step |

## Prerequisites

1. **Claude Code CLI** installed and on your PATH (`claude` command available)
2. **[uv](https://docs.astral.sh/uv/) >= 0.5** installed (`uv` command available)
3. **BMAD framework** installed in your project (`_bmad/` directory with the `bmm` module)
4. **BMAD planning phases completed** (see below)

## Required BMAD Steps Before Running

You must complete these BMAD workflows first — they produce the planning artifacts that stories are built from:

| Phase | Workflow | Command | Required |
|---|---|---|---|
| 2-planning | Create PRD | `/bmad-bmm-create-prd` | Yes |
| 2-planning | Validate PRD | `/bmad-bmm-validate-prd` | No |
| 2-planning | Create UX | `/bmad-bmm-create-ux-design` | No |
| 3-solutioning | Create Architecture | `/bmad-bmm-create-architecture` | Yes |
| 3-solutioning | Create Epics & Stories | `/bmad-bmm-create-epics-and-stories` | Yes |
| 3-solutioning | Check Readiness | `/bmad-bmm-check-implementation-readiness` | Yes |
| 4-implementation | Sprint Planning | `/bmad-bmm-sprint-planning` | Yes |

After Sprint Planning, you should have:
- `_bmad-output/planning-artifacts/` — PRD, architecture, epics, etc.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — stories in `backlog` status

## Setup

Clone or copy this repository into your project. The `pyproject.toml` defines the runner's dependencies and entry point — if your project already has its own `pyproject.toml`, copy the files into a subdirectory instead and run from there.

```bash
# Option A: clone into project
git clone <this-repo> /path/to/your-project/bmad-ralph-loop
cd /path/to/your-project/bmad-ralph-loop

# Option B: copy files alongside your project
cp run-stories PROMPT-create-story.md PROMPT-dev-story.md PROMPT-code-review.md /path/to/your-project/
cp -r run_stories/ pyproject.toml /path/to/your-project/
chmod +x /path/to/your-project/run-stories
```

> **Migrating from `run-stories.sh`?** The old bash script has been replaced by a Python package. Use `uv run run-stories` (or `./run-stories`) instead of `./run-stories.sh`. All CLI flags are the same.

## Usage

```bash
# Run all remaining backlog stories
uv run run-stories

# Run just the next story
uv run run-stories --max-stories 1

# Use different models for dev vs review
uv run run-stories --dev-model opus --review-model sonnet

# Preview what would run without executing
uv run run-stories --dry-run

# Or use the wrapper script (runs uv run run-stories under the hood)
./run-stories --max-stories 1
```

### Options

| Flag | Default | Description |
|---|---|---|
| `--max-stories N` | unlimited | Stop after N stories |
| `--max-turns-ds N` | 200 | Max turns for dev-story sessions |
| `--max-review-rounds N` | 3 | Max dev->review fix rounds per story |
| `--dev-model MODEL` | system default | Model for create-story and dev-story |
| `--review-model MODEL` | system default | Model for code review (different model recommended) |
| `--dry-run` | off | Show plan without executing |

## How It Works

For each story in `backlog` status:

1. **Create Story** — Runs the BMAD create-story workflow, producing a detailed story file and moving status to `ready-for-dev`
2. **Dev Story** — Implements the story (code, tests, config), moves status to `review`
3. **Code Review** — Adversarial review that auto-fixes issues. If approved, status moves to `done`. If issues remain, loops back to step 2 (up to `--max-review-rounds` times)
4. **Git Commit** — Auto-commits all changes with a `feat(story-X.Y)` message

Each step runs in a **fresh Claude Code session** with `--dangerously-skip-permissions` for unattended execution.

Logs are saved to `_bmad-output/implementation-artifacts/logs/`.

## Tips

- Use `--review-model` to run code review with a different model than dev — this catches more issues
- Start with `--max-stories 1` to verify everything works before running the full backlog
- Check `sprint-status.yaml` between runs to see progress
- Logs are timestamped per step — check them if a story fails
