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
    get_story_status,
    load_status,
    next_backlog_story,
    story_id_from_key,
)
from .tui import TUI


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

    for i in range(1, config.max_stories + 1):
        # --- Find next story ---
        status_data = load_status(sprint_status_path)
        story_key = next_backlog_story(status_data)

        if story_key is None:
            tui.handle_event(TextEvent(
                text="No more backlog stories. All stories have been created or completed.",
                is_thinking=False,
            ))
            break

        story_id = story_id_from_key(story_key)
        story_file = impl_dir / f"{story_key}.md"
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

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
                text=f"[DRY RUN] Would execute: 1. Create Story  2. Dev Story  3. Code Review  4. Git commit",
                is_thinking=False,
            ))
            story_count += 1
            continue

        story_start = time.monotonic()

        # ---- STEP 1: CREATE STORY (CS) ----
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

        for round_num in range(1, config.max_review_rounds + 1):
            story_state.current_round = round_num

            # --- Dev Story (DS) ---
            story_state.current_step = StepKind.DS
            tui.dashboard.step_cost = None
            step_start = time.monotonic()
            tui.dashboard.set_timer_anchors(step_start, story_start, total_start)

            tui.handle_event(TextEvent(
                text=f"--- Step 2: Dev Story ({story_key}) [round {round_num}/{config.max_review_rounds}] ---",
                is_thinking=False,
            ))
            log_ds = log_dir / f"{timestamp}_{story_key}_2-dev-story-r{round_num}.log"

            extra = None
            if round_num > 1:
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
