"""Main orchestration loop: CS → DS → CR → commit.

Direct port of run-stories.sh (lines 189-366), using Python
asyncio + the stream parser + TUI instead of bare shell.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from datetime import datetime
from pathlib import Path

from .claude_session import run_claude_session, run_commit_session
from .models import (
    MarkerType,
    SessionConfig,
    StepKind,
    StoryState,
    TextEvent,
)
from .sprint_status import (
    count_epics,
    count_stories,
    find_done_stories,
    get_story_status,
    load_status,
    next_actionable_story,
    story_id_from_key,
)
from .tui import TUI


async def _check_git_dirty(project_dir: Path, tui: TUI) -> bool:
    """Return True if the working tree has uncommitted changes."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "status", "--porcelain",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_dir,
        )
        stdout, _ = await proc.communicate()
        return bool(stdout and stdout.strip())
    except (OSError, FileNotFoundError):
        tui.handle_event(TextEvent(
            text="WARNING: Could not check git status",
            is_thinking=False,
        ))
        return False


async def _check_story_committed(project_dir: Path, story_key: str) -> bool:
    """Return True if a commit referencing this story exists in git log.

    Searches for the story-ID pattern (e.g. 'story-1.2') used in commit
    subjects, since the full story_key may not appear in the message.
    """
    search_term = f"story-{story_id_from_key(story_key)}"
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "log", "--all", "--oneline", f"--grep={search_term}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_dir,
        )
        stdout, _ = await proc.communicate()
        return bool(stdout and stdout.strip())
    except (OSError, FileNotFoundError):
        return False


async def run_stories(config: SessionConfig, tui: TUI) -> int:
    """Run the main story loop. Returns the number of completed stories."""

    project_dir = config.project_dir
    sprint_status_path = project_dir / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml"
    impl_dir = project_dir / "_bmad-output" / "implementation-artifacts"
    log_dir = impl_dir / "logs"

    pkg_dir = Path(__file__).resolve().parent
    prompt_cs = pkg_dir / "PROMPT-create-story.md"
    prompt_ds = pkg_dir / "PROMPT-dev-story.md"
    prompt_cr = pkg_dir / "PROMPT-code-review.md"

    # --- Preflight checks ---
    if not shutil.which("claude"):
        tui.handle_event(TextEvent(text="ERROR: 'claude' command not found.", is_thinking=False))
        return 0

    for f in [prompt_cs, prompt_ds, prompt_cr, sprint_status_path]:
        if not f.exists():
            tui.handle_event(TextEvent(text=f"ERROR: Required file not found: {f}", is_thinking=False))
            return 0

    log_dir.mkdir(parents=True, exist_ok=True)

    story_count = 0
    total_start = time.monotonic()

    # Initial sprint stats
    _refresh_sprint_stats(sprint_status_path, tui)

    # --- Pre-loop: commit-gap recovery for done-but-uncommitted stories ---
    status_data = load_status(sprint_status_path)
    done_keys = find_done_stories(status_data)
    if done_keys and await _check_git_dirty(project_dir, tui):
        for dk in done_keys:
            if not await _check_story_committed(project_dir, dk):
                dk_id = story_id_from_key(dk)
                dk_file = impl_dir / f"{dk}.md"
                tui.handle_event(TextEvent(
                    text=f"Recovering uncommitted story: {dk}",
                    is_thinking=False,
                ))
                await run_commit_session(
                    story_id=dk_id,
                    story_key=dk,
                    story_file=dk_file,
                    project_dir=project_dir,
                    tui=tui,
                )
                _refresh_sprint_stats(sprint_status_path, tui)
                break  # one recovery per restart

    for i in range(1, config.max_stories + 1):
        # --- Find next story ---
        status_data = load_status(sprint_status_path)
        result = next_actionable_story(status_data)

        if result is None:
            tui.handle_event(TextEvent(
                text="No more actionable stories. All stories have been created or completed.",
                is_thinking=False,
            ))
            break

        story_key, story_status = result

        # Map status to resume step
        _STATUS_TO_STEP = {
            "in-progress": StepKind.DS,
            "ready-for-dev": StepKind.DS,
            "review": StepKind.CR,
            "backlog": StepKind.CS,
        }
        resume_step = _STATUS_TO_STEP.get(story_status, StepKind.CS)

        story_id = story_id_from_key(story_key)
        story_file = impl_dir / f"{story_key}.md"
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

        # Story file existence guard for DS/CR resume
        if resume_step in (StepKind.DS, StepKind.CR) and not story_file.exists():
            tui.handle_event(TextEvent(
                text=f"WARNING: Story file missing for {story_key}, falling back to Create Story",
                is_thinking=False,
            ))
            resume_step = StepKind.CS

        story_state = StoryState(
            story_key=story_key,
            story_id=story_id,
        )

        tui.dashboard.update_state(
            story_state=story_state,
            story_number=i,
            step_elapsed=0,
            story_elapsed=0,
            total_elapsed=time.monotonic() - total_start,
            total_cost=tui.dashboard.total_cost,
        )

        tui.handle_event(TextEvent(
            text=f"Story {i}: {story_key} ({story_id})",
            is_thinking=False,
        ))

        if config.dry_run:
            tui.handle_event(TextEvent(
                text=f"[DRY RUN] Would resume: {story_key} at step {resume_step.value}",
                is_thinking=False,
            ))
            story_count += 1
            continue

        if resume_step != StepKind.CS:
            tui.handle_event(TextEvent(
                text=f"RESUMING story {story_key} at {resume_step.value}",
                is_thinking=False,
            ))

        story_start = time.monotonic()

        # ---- STEP 1: CREATE STORY (CS) — skipped if resuming ----
        if resume_step == StepKind.CS:
            story_state.current_step = StepKind.CS
            tui.dashboard.step_cost = None
            step_start = time.monotonic()
            tui.dashboard.set_timer_anchors(step_start, story_start, total_start)

            tui.handle_event(TextEvent(text=f"--- Step 1: Create Story ({story_key}) ---", is_thinking=False))
            log_cs = log_dir / f"{timestamp}_{story_key}_1-create-story.log"

            cs_result = await run_claude_session(
                prompt_file=prompt_cs,
                log_file=log_cs,
                max_turns=config.max_turns_cs,
                model=config.dev_model,
                extra_prompt=None,
                tui=tui,
                project_dir=project_dir,
                step_kind=StepKind.CS,
                story_key=story_key,
            )
            story_state.step_results.append(cs_result)

            if not cs_result.success:
                tui.handle_event(TextEvent(
                    text=f"ERROR: Create Story failed. Check log: {log_cs}",
                    is_thinking=False,
                ))
                break

            # Verify story was created
            status_data = load_status(sprint_status_path)
            status = get_story_status(status_data, story_key)
            if status != "ready-for-dev":
                tui.handle_event(TextEvent(
                    text=f"ERROR: Expected status 'ready-for-dev' after create-story, got '{status}'",
                    is_thinking=False,
                ))
                break
            _refresh_sprint_stats(sprint_status_path, tui)

        # ---- STEP 2-3: DEV STORY + CODE REVIEW LOOP ----
        story_done = False

        # Determine starting round — skip first DS if resuming at CR
        start_round = 1
        skip_first_ds = resume_step == StepKind.CR

        # Partial-work warning when resuming at DS
        if resume_step == StepKind.DS:
            try:
                diff_proc = await asyncio.create_subprocess_exec(
                    "git", "diff", "--stat",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=project_dir,
                )
                diff_out, _ = await diff_proc.communicate()
                if diff_out and diff_out.strip():
                    tui.handle_event(TextEvent(
                        text="WARNING: Partial changes detected in working tree. Resuming DS in 10s... (press q to abort)",
                        is_thinking=False,
                    ))
                    for sec in range(10, 0, -1):
                        tui.dashboard.countdown_message = f"Resuming in {sec}s..."
                        await asyncio.sleep(1)
                    tui.dashboard.countdown_message = None
            except (OSError, FileNotFoundError):
                pass

        for round_num in range(start_round, config.max_review_rounds + 1):
            story_state.current_round = round_num

            # --- Dev Story (DS) — skipped on first round if resuming at CR ---
            if skip_first_ds and round_num == start_round:
                pass  # jump straight to CR
            else:
                story_state.current_step = StepKind.DS
                tui.dashboard.step_cost = None
                step_start = time.monotonic()
                tui.dashboard.set_timer_anchors(step_start, story_start, total_start)

                tui.handle_event(TextEvent(
                    text=f"--- Step 2: Dev Story ({story_key}) [round {round_num}/{config.max_review_rounds}] ---",
                    is_thinking=False,
                ))
                log_ds = log_dir / f"{timestamp}_{story_key}_2-dev-story-r{round_num}.log"

                # Always pass STORY_PATH when resuming (not just round > 1)
                extra = None
                if round_num > 1 or resume_step in (StepKind.DS, StepKind.CR):
                    extra = f"STORY_PATH: {story_file}"

                ds_result = await run_claude_session(
                    prompt_file=prompt_ds,
                    log_file=log_ds,
                    max_turns=config.max_turns_ds,
                    model=config.dev_model,
                    extra_prompt=extra,
                    tui=tui,
                    project_dir=project_dir,
                    step_kind=StepKind.DS,
                    story_key=story_key,
                )
                story_state.step_results.append(ds_result)

                if not ds_result.success:
                    tui.handle_event(TextEvent(text=f"ERROR: Dev Story failed. Check log: {log_ds}", is_thinking=False))
                    break

                # Check for HALT
                if any(m.marker_type == MarkerType.HALT for m in ds_result.markers_detected):
                    tui.handle_event(TextEvent(text=f"Dev Story HALTed. Check log: {log_ds}", is_thinking=False))
                    story_done = False
                    break

                # Verify status moved to review
                status_data = load_status(sprint_status_path)
                status = get_story_status(status_data, story_key)
                if status != "review":
                    tui.handle_event(TextEvent(
                        text=f"WARNING: Expected status 'review' after dev-story, got '{status}'",
                        is_thinking=False,
                    ))

            # --- Code Review (CR) ---
            story_state.current_step = StepKind.CR
            tui.dashboard.step_cost = None
            step_start = time.monotonic()
            tui.dashboard.set_timer_anchors(step_start, story_start, total_start)

            tui.handle_event(TextEvent(
                text=f"--- Step 3: Code Review ({story_key}) [round {round_num}/{config.max_review_rounds}] ---",
                is_thinking=False,
            ))
            log_cr = log_dir / f"{timestamp}_{story_key}_3-code-review-r{round_num}.log"

            cr_result = await run_claude_session(
                prompt_file=prompt_cr,
                log_file=log_cr,
                max_turns=config.max_turns_cr,
                model=config.review_model,
                extra_prompt=f"STORY_PATH: {story_file}",
                tui=tui,
                project_dir=project_dir,
                step_kind=StepKind.CR,
                story_key=story_key,
            )
            story_state.step_results.append(cr_result)

            # Check review outcome
            status_data = load_status(sprint_status_path)
            status = get_story_status(status_data, story_key)

            if status == "done":
                story_done = True
                break

            if round_num < config.max_review_rounds:
                tui.handle_event(TextEvent(
                    text=f"Code review found issues. Running dev-story again (round {round_num + 1})...",
                    is_thinking=False,
                ))
            else:
                tui.handle_event(TextEvent(
                    text=f"WARNING: Max review rounds ({config.max_review_rounds}) reached. Story not fully approved.",
                    is_thinking=False,
                ))

        # ---- STEP 4: COMMIT ----
        if story_done:
            story_state.current_step = StepKind.COMMIT
            tui.dashboard.step_cost = None
            step_start = time.monotonic()
            tui.dashboard.set_timer_anchors(step_start, story_start, total_start)

            tui.handle_event(TextEvent(
                text=f"--- Committing: Story {story_id} ({story_key}) ---",
                is_thinking=False,
            ))

            commit_result = await run_commit_session(
                story_id=story_id,
                story_key=story_key,
                story_file=story_file,
                project_dir=project_dir,
                tui=tui,
            )
            story_state.step_results.append(commit_result)
            story_count += 1
            _refresh_sprint_stats(sprint_status_path, tui)
        else:
            status_data = load_status(sprint_status_path)
            status = get_story_status(status_data, story_key)
            tui.handle_event(TextEvent(
                text=f"Story {story_key} is NOT done (status: {status}). Stopping.",
                is_thinking=False,
            ))
            break

        # Pause between stories
        if i < config.max_stories:
            for sec in range(5, 0, -1):
                tui.dashboard.countdown_message = f"Next story in {sec}s..."
                await asyncio.sleep(1)
            tui.dashboard.countdown_message = None

    # Session summary
    tui.handle_event(TextEvent(
        text=f"Session Complete. Stories completed: {story_count}. Logs: {log_dir}",
        is_thinking=False,
    ))

    return story_count


def _refresh_sprint_stats(sprint_status_path: Path, tui: TUI) -> None:
    """Load sprint status and update dashboard counters. Failures are non-fatal."""
    try:
        data = load_status(sprint_status_path)
        total_epics, done_epics = count_epics(data)
        total_stories, done_stories = count_stories(data)
        tui.dashboard.update_sprint_stats(total_epics, done_epics, total_stories, done_stories)
    except Exception:
        pass  # Sprint stats are cosmetic; don't crash the orchestrator
