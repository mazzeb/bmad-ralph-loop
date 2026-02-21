---
title: 'Crash-Resilient Story Orchestration'
slug: 'crash-resilient-orchestration'
created: '2026-02-21'
status: 'ready-for-dev'
stepsCompleted: [1, 2, 3, 4]
tech_stack: ['python3.10+', 'asyncio', 'pyyaml', 'pytest']
files_to_modify:
  - run_stories/sprint_status.py (modify — add next_actionable_story, find_done_stories)
  - run_stories/orchestrator.py (modify — refactor main loop, add pre-loop commit recovery, add resume entry points)
  - tests/test_sprint_status.py (new — unit tests for new sprint_status functions)
  - tests/test_orchestrator.py (modify — add crash-resume integration tests, update mocks for tuple return)
code_patterns:
  - 'priority-based story lookup: in-progress > review > ready-for-dev > backlog'
  - 'git status --porcelain for commit-gap detection via asyncio subprocess'
  - 'step-resume via status string mapping in orchestrator'
  - 'existing tui.handle_event(TextEvent(...)) for warnings'
  - 'STORY_PATH always passed when resuming DS to prevent prompt <NO_READY_STORIES/> bail-out'
test_patterns:
  - 'existing: tmp_project fixture with mocked run_claude_session/run_commit_session'
  - 'existing: _update_status() helper to simulate YAML state changes'
  - 'new: parametrized tests with pre-set YAML states simulating each crash point'
  - 'new: mock _check_git_dirty() for commit-gap tests'
  - 'new: dedicated test_sprint_status.py for pure function tests'
---

# Tech-Spec: Crash-Resilient Story Orchestration

**Created:** 2026-02-21

## Overview

### Problem Statement

When `run-stories` crashes or is canceled mid-pipeline (CS→DS→CR→commit), rerunning it skips any story that progressed past `backlog` status. The root cause is `next_backlog_story()` in `sprint_status.py`, which only looks for stories with status `backlog`. Stories in `ready-for-dev`, `review`, or `in-progress` are abandoned forever. Additionally, stories marked `done` before the commit step leave uncommitted code changes in the working tree, causing the next story's session to start with dirty state.

### Solution

Replace the single `next_backlog_story()` lookup with a priority-based `next_actionable_story()` that finds the highest-priority in-progress story and tells the orchestrator which pipeline step to resume from. Add commit-gap detection for `done`-but-uncommitted stories. Add a user warning when partial code changes are detected during DS resume.

### Scope

**In Scope:**
- New `next_actionable_story()` function returning `(story_key, status_string)` by priority: `in-progress` > `review` > `ready-for-dev` > `backlog`
- Separate `find_done_stories()` returning all `done` story keys for commit-gap detection
- Orchestrator refactor to resume from the correct step based on status
- Commit-gap detection via `git status --porcelain` for stories already marked `done`
- Warning + brief countdown when partial working tree changes detected during DS resume
- Auto-resume of DS→CR loop for `in-progress` stories
- Always pass `STORY_PATH` when resuming at DS to prevent prompt bail-out
- Tests for all resume scenarios

**Out of Scope:**
- Changes to the BMAD prompt templates (CS/DS/CR prompts)
- Changes to the sprint-status.yaml schema or format
- Interactive mode / manual intervention UI beyond TUI warnings + countdown
- Rollback or undo of partial changes

## Context for Development

### Codebase Patterns

- **sprint_status.py** — Pure functions for YAML queries. No file writing (Claude sessions update YAML). All functions take a `data: dict` parameter from `load_status()`. Pattern: regex key matching on `development_status` dict entries. **Zero imports from the rest of the package** — only `re`, `Path`, and `yaml`. This boundary must be preserved.
- **orchestrator.py** — Main state machine: linear CS→DS→CR→COMMIT per story, wrapped in `for i in range(1, config.max_stories + 1)` loop. Each step has: (a) set `story_state.current_step`, (b) reset dashboard, (c) log message, (d) `await run_claude_session(...)`, (e) verify status in YAML.
- **claude_session.py** — `run_claude_session()` spawns `claude -p ... --output-format stream-json` as async subprocess. `run_commit_session()` does `git add -A` then `git commit`. Both return `StepResult`.
- **DS prompt behavior** — The DS prompt (`PROMPT-dev-story.md`) internally looks for `ready-for-dev` stories in YAML. If no `ready-for-dev` stories exist AND no `STORY_PATH` is provided, it outputs `<NO_READY_STORIES/>` and stops. **CRITICAL: When resuming a story in `in-progress` or `ready-for-dev` status at DS, `STORY_PATH` MUST always be passed** to prevent the prompt from bailing out.
- **TUI** — `tui.handle_event(TextEvent(text=..., is_thinking=False))` is the way to display messages. No interactive prompting — just display. User can press `q` to quit.
- **Tests** — `test_orchestrator.py` uses `tmp_project` fixture (creates sprint-status.yaml + prompt files in tmp_path), mocks `run_claude_session`/`run_commit_session` via `unittest.mock.patch`, uses `_update_status()` helper to simulate YAML state changes.

### Files to Reference

| File | Purpose |
| ---- | ------- |
| `run_stories/sprint_status.py` | YAML query functions — `next_backlog_story()` to replace |
| `run_stories/orchestrator.py` | Main pipeline loop — needs resume logic |
| `run_stories/claude_session.py` | Subprocess runner — `run_commit_session()` reusable for commit-gap |
| `run_stories/models.py` | `StepKind` enum, `StoryState`, `SessionConfig` dataclasses |
| `run_stories/PROMPT-dev-story.md` | DS prompt — has `STORY_PATH` + `NO_READY_STORIES` logic |
| `tests/test_orchestrator.py` | 6 existing tests — pattern to follow |

### Technical Decisions

1. **`next_actionable_story()` returns `(story_key, status_string)` NOT `StepKind`**: To preserve the architectural boundary where `sprint_status.py` has zero imports from the package, the function returns the raw status string (e.g., `"in-progress"`, `"review"`). The orchestrator maps the status string to the appropriate `StepKind` entry point. This avoids coupling the YAML query module to orchestration models. _(Addresses F10)_

2. **`find_done_stories()` returns ALL done story keys sorted by numeric prefix**: Returns `list[str]` not `str | None`. Sorted by `(epic_num, story_num)` descending so the most recent (highest-numbered) story is first. The orchestrator iterates and attempts commit recovery for each. This handles the edge case of multiple uncommitted `done` stories. _(Addresses F4, F5, F13)_

3. **Pre-loop commit-gap recovery loops over all done stories**: For each `done` story, the orchestrator checks if a commit referencing that story key exists in `git log --oneline --grep`. If not found AND git is dirty, run `run_commit_session()`. This avoids the false-positive of committing unrelated files under the wrong story name. _(Addresses F6)_

4. **Always pass `STORY_PATH` when resuming at DS**: When `resume_step != StepKind.CS`, the orchestrator always sets `extra = f"STORY_PATH: {story_file}"` for the DS session, regardless of `round_num`. This prevents the DS prompt from bailing out with `<NO_READY_STORIES/>` when the story is `in-progress`. _(Addresses F1, F2)_

5. **Story file existence guard**: Before resuming at DS or CR, verify that the story file (`impl_dir / f"{story_key}.md"`) exists. If not, log an error and fall back to running CS first. _(Addresses F15)_

6. **`review` status crash ambiguity is self-healing**: When resuming a `review` story, CR may review incomplete code (if DS crashed after setting status to `review` but before finishing). CR will detect incomplete work, reject it, and the DS→CR loop re-runs DS. This wastes one review round but self-heals without additional complexity. _(Addresses F3 — accepted limitation)_

7. **Existing tests need mock updates**: Since `next_actionable_story()` returns `(str, str)` instead of `next_backlog_story()` returning `str | None`, existing test mocks that depend on the return type need updating. The existing tests should continue to work because the orchestrator now calls `next_actionable_story()` which returns `(key, "backlog")` for the normal case. _(Addresses F16)_

8. **Updated user-facing messages**: The "No more backlog stories" message should be updated to "No more actionable stories" to reflect the new function name. _(Addresses F12)_

9. **`_check_git_dirty()` logs a warning on subprocess failure**: Instead of silently returning `False`, log a TUI warning: "WARNING: Could not check git status". _(Addresses F14)_

10. **Remove `next_backlog_story()` entirely**: Since nothing outside the orchestrator calls it and `next_actionable_story()` fully replaces it, remove the dead code. _(Addresses F21)_

## Implementation Plan

### Tasks

- [ ] Task 1: Add `next_actionable_story()` to sprint_status.py
  - File: `run_stories/sprint_status.py`
  - Action: Add new function after `next_backlog_story()`. Scans `development_status` dict in priority order with 4 separate passes: `in-progress`, `review`, `ready-for-dev`, `backlog`. Returns `tuple[str, str] | None` where the second element is the status string.
  - Notes: NO imports from `.models` — return raw status strings. Use same `re.match(r"\d+-\d+-", str(key))` pattern as `next_backlog_story()`. 4 separate passes ensure highest priority wins regardless of dict ordering.

- [ ] Task 2: Add `find_done_stories()` to sprint_status.py
  - File: `run_stories/sprint_status.py`
  - Action: Add new function. Scans `development_status` for ALL story keys with status `done`. Returns `list[str]` sorted by `(epic_num, story_num)` descending (most recent first).
  - Notes: Parse epic/story numbers from key using existing `story_id_from_key()` logic for sort key. Empty list if no done stories.

- [ ] Task 3: Remove `next_backlog_story()` from sprint_status.py
  - File: `run_stories/sprint_status.py`
  - Action: Delete `next_backlog_story()` function. Update any remaining references.
  - Notes: Fully replaced by `next_actionable_story()`. No external callers.

- [ ] Task 4: Add `_check_git_dirty()` and `_check_story_committed()` helpers to orchestrator.py
  - File: `run_stories/orchestrator.py`
  - Action: Add two async helper functions:
    - `_check_git_dirty(project_dir: Path) -> bool` — runs `git status --porcelain`, returns `True` if non-empty. Logs TUI warning on subprocess failure.
    - `_check_story_committed(project_dir: Path, story_key: str) -> bool` — runs `git log --oneline --grep=story_key`, returns `True` if output contains a match.
  - Notes: Both use `asyncio.create_subprocess_exec`. Handle errors gracefully with TUI warnings.

- [ ] Task 5: Add pre-loop commit-gap recovery to orchestrator.py
  - File: `run_stories/orchestrator.py`
  - Action: After preflight checks and before the main `for` loop, add commit-gap recovery:
    1. Load sprint status, call `find_done_stories()`
    2. If any done stories exist, call `_check_git_dirty()`
    3. If dirty, iterate done stories (most recent first): for each, call `_check_story_committed()`
    4. For the first uncommitted story found, log "Recovering uncommitted story: {key}" and call `run_commit_session()`
    5. If all done stories are committed or git is clean, skip
  - Notes: Import `find_done_stories` from `.sprint_status`. Uses git log grep to avoid false attribution.

- [ ] Task 6: Replace `next_backlog_story()` with `next_actionable_story()` in orchestrator main loop
  - File: `run_stories/orchestrator.py`
  - Action: Change the story-picking logic. Replace `story_key = next_backlog_story(status_data)` with `result = next_actionable_story(status_data)`. Unpack to `(story_key, story_status)`. Map status to `StepKind`: `"in-progress"` / `"ready-for-dev"` → `StepKind.DS`, `"review"` → `StepKind.CR`, `"backlog"` → `StepKind.CS`. Update `None` check. Update "No more backlog stories" message to "No more actionable stories."
  - Notes: Import `next_actionable_story` from `.sprint_status`. Remove `next_backlog_story` import.

- [ ] Task 7: Add step-resume conditional logic to orchestrator main loop
  - File: `run_stories/orchestrator.py`
  - Action: Based on `resume_step` from Task 6:
    - If `StepKind.CS`: run CS, then DS→CR loop (current behavior, no changes)
    - If `StepKind.DS`: skip CS. **Verify story file exists** — if not, fall back to CS. Always pass `STORY_PATH` as `extra_prompt` for DS (not just `round_num > 1`). Run DS→CR loop.
    - If `StepKind.CR`: skip CS and DS. **Verify story file exists** — if not, fall back to CS. Enter DS→CR loop but skip first DS run (start with CR directly).
    - When resuming (not `StepKind.CS`), log "RESUMING story {key} at {step}" via TUI.
    - Ensure `story_start`, `step_start`, and `tui.dashboard.set_timer_anchors()` are correctly initialized for all resume paths.
  - Notes: Story file check: `if not story_file.exists():` → log warning, set `resume_step = StepKind.CS`.

- [ ] Task 8: Add partial-work warning with countdown
  - File: `run_stories/orchestrator.py`
  - Action: When `resume_step == StepKind.DS`, before running DS:
    1. Run `git diff --stat` via `asyncio.create_subprocess_exec`
    2. If output is non-empty, display TUI warning: "WARNING: Partial changes detected in working tree. Resuming DS in 10s... (press q to abort)"
    3. Run a 10-second countdown using `dashboard.countdown_message` + `asyncio.sleep(1)`
    4. Clear `countdown_message` after countdown
  - Notes: Informational only — DS handles incremental work naturally. The warning gives users a chance to press `q` to abort if they want to inspect first.

- [ ] Task 9: Update dry-run logic for resume awareness
  - File: `run_stories/orchestrator.py`
  - Action: Update the dry-run message to show which step would be resumed: `"[DRY RUN] Would resume: {story_key} at step {resume_step.value}"`.
  - Notes: Minor change — interpolate `resume_step` into existing dry-run log line.

- [ ] Task 10: Add unit tests for new sprint_status functions
  - File: `tests/test_sprint_status.py` (new file)
  - Action: Add parametrized tests:
    - `next_actionable_story()`: returns `(key, "in-progress")`, `(key, "review")`, `(key, "ready-for-dev")`, `(key, "backlog")` for respective states. Mixed states: `in-progress` wins over `backlog`. All `done`: returns `None`. Empty dict: returns `None`.
    - `find_done_stories()`: returns all done keys sorted descending. Returns empty list when none. Excludes non-story keys (epic-N).
  - Notes: Pure function tests — construct `data` dicts directly, no mocks needed. ~10 test cases total.

- [ ] Task 11: Add orchestrator integration tests for crash-resume scenarios
  - File: `tests/test_orchestrator.py`
  - Action: Add test classes:
    - `TestResumeFromReadyForDev`: YAML has `ready-for-dev` story → DS runs (no CS), then CR, then COMMIT. Verify `STORY_PATH` is passed to DS.
    - `TestResumeFromReview`: YAML has `review` story → CR runs (no CS/DS), then COMMIT
    - `TestResumeFromInProgress`: YAML has `in-progress` story → DS runs with `STORY_PATH`, then CR, then COMMIT
    - `TestCommitGapRecovery`: YAML has `done` story + mocked dirty git + story not in git log → commit runs before main loop
    - `TestCleanDoneNoRecovery`: YAML has `done` story + mocked clean git → no commit, next actionable story
    - `TestStoryFileMissing`: YAML has `ready-for-dev` but no story file → falls back to CS
  - Notes: Mock `_check_git_dirty()` and `_check_story_committed()` for git tests. Update existing test mocks to handle `next_actionable_story()` tuple return (verify existing tests still pass).

- [ ] Task 12: Update existing test mocks for new return type
  - File: `tests/test_orchestrator.py`
  - Action: Existing tests mock the call path that calls `next_backlog_story()`. Since the orchestrator now calls `next_actionable_story()` (which returns `(str, str)` instead of `str | None`), verify existing tests still work. The mock for `run_claude_session` is unchanged, but the YAML state in `tmp_project` now goes through `next_actionable_story()` so the initial `backlog` state should be picked up as `(key, "backlog")` and proceed through CS as before.
  - Notes: May need to mock `_check_git_dirty` to return `False` in existing tests to prevent false commit-gap recovery triggering (since the fixture includes `1-1-done-story: done`).

### Acceptance Criteria

- [ ] AC 1: Given a story in `ready-for-dev` status (crash after CS), when `run-stories` is restarted, then it resumes at DS (skips CS), passes `STORY_PATH` to the DS prompt, and completes the full DS→CR→COMMIT pipeline.

- [ ] AC 2: Given a story in `review` status (crash after DS), when `run-stories` is restarted, then it resumes at CR (skips CS and DS) and completes CR→COMMIT.

- [ ] AC 3: Given a story in `in-progress` status (CR found issues, then crashed), when `run-stories` is restarted, then it resumes at DS with `STORY_PATH` and runs the full DS→CR loop.

- [ ] AC 4: Given a story in `done` status with uncommitted changes and no matching commit in git log, when `run-stories` is restarted, then it commits the pending changes before starting the main story loop.

- [ ] AC 5: Given a story in `done` status with a clean working tree (or commit already in git log), when `run-stories` is restarted, then it skips that story and moves to the next actionable story.

- [ ] AC 6: Given multiple stories in different intermediate states (one `in-progress`, one `backlog`), when `run-stories` is restarted, then it picks the highest-priority intermediate story (`in-progress`) first. On the next iteration, it picks the `backlog` story.

- [ ] AC 7: Given a resume at DS with partial code changes in the working tree, when `run-stories` detects dirty git state, then it displays a warning in the TUI and waits 10 seconds before proceeding, allowing the user to quit.

- [ ] AC 8: Given `--dry-run` with a story in an intermediate state, when `run-stories` runs, then it shows which step would be resumed without executing anything.

- [ ] AC 9: Given all stories are `done` and git is clean, when `run-stories` runs, then it exits cleanly with "No more actionable stories" message.

- [ ] AC 10: Given the existing happy-path (all stories `backlog`), when `run-stories` runs, then it behaves identically to the current implementation (no regression). All existing 6 tests pass.

- [ ] AC 11: Given a resume at DS or CR where the story file does not exist (crash after YAML update but before file write), when `run-stories` detects the missing file, then it falls back to running CS first.

## Additional Context

### Dependencies

- No new dependencies required — uses existing `asyncio.create_subprocess_exec` for git commands
- Depends on existing `run_commit_session()` from `claude_session.py` (no changes needed)

### Testing Strategy

- **Unit tests** (Task 10): Pure function tests for `next_actionable_story()` and `find_done_stories()` in `tests/test_sprint_status.py`. ~10 test cases using directly constructed `data` dicts. No mocks needed.
- **Integration tests** (Task 11): 6 orchestrator test cases using existing mock pattern. Pre-set YAML states, mock git helpers. Verify correct steps are skipped/run and `STORY_PATH` is passed.
- **Regression** (Task 12): Existing 6 tests must pass. May need mock for `_check_git_dirty()` since the `tmp_project` fixture includes a `done` story.
- **Manual testing**: Run `./run-stories --dry-run` with manually edited sprint-status.yaml (set a story to `ready-for-dev`) to verify resume detection and dry-run output.

### Known Limitations & Risks

1. **`review` status ambiguity (F3)**: Cannot distinguish "DS completed successfully, set review" from "DS crashed mid-work after setting review." CR may review incomplete code. Self-heals via DS→CR loop at the cost of one wasted review round. Accepted.

2. **`git add -A` scope (F6 mitigated)**: `run_commit_session()` uses `git add -A` which stages ALL files. For commit-gap recovery, we first check `git log --grep` to verify the story wasn't already committed. For normal runs, the repo should be clean before `run-stories` starts. If unrelated dirty files exist, they get committed under the story's name. The `git log --grep` check prevents the worst case (committing under the wrong story).

3. **Manual git cleanup (F8)**: If someone runs `git checkout .` on a `done` story, the code is lost but the status stays `done`. No mechanism detects this — the story is considered properly committed. This is user error and out of scope.

4. **Countdown not configurable (F18)**: The 10-second warning countdown is hardcoded. Can be made configurable later if needed (add to `SessionConfig`).

### Future Considerations

- A `committed` status in sprint-status.yaml would eliminate git-state heuristics entirely
- A `--skip-resume` CLI flag could force fresh-start behavior
- Per-story file tracking in YAML would enable more precise dirty-state detection
