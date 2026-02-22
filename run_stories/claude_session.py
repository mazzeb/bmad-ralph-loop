"""Async subprocess runner for Claude CLI sessions with tee pattern.

Each session runs `claude -p ... --output-format stream-json`, streaming
stdout line by line. Every line is simultaneously written to the log file
and parsed into events fed to the TUI.
"""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path

from .models import (
    MarkerEvent,
    ResultEvent,
    StepKind,
    StepResult,
    StreamEvent,
)
from .stream_parser import parse_line
from .tui import TUI

# Track active subprocess for signal-based cleanup
_active_process: asyncio.subprocess.Process | None = None


async def run_claude_session(
    prompt_file: Path,
    log_file: Path,
    max_turns: int,
    model: str,
    extra_prompt: str | None,
    tui: TUI,
    project_dir: Path,
    step_kind: StepKind | None = None,
    story_key: str = "",
    timeout_minutes: int = 30,
) -> StepResult:
    """Run a Claude CLI session with stream-json output.

    Tees every line to both the log file and the TUI parser.
    Returns a StepResult with collected markers and result data.
    """
    global _active_process

    prompt_content = prompt_file.read_text()
    if extra_prompt:
        prompt_content = f"{prompt_content}\n\n{extra_prompt}"

    cmd = [
        "claude",
        "-p", prompt_content,
        "--verbose",
        "--max-turns", str(max_turns),
        "--output-format", "stream-json",
        "--dangerously-skip-permissions",
    ]
    if model:
        cmd.extend(["--model", model])

    markers: list[MarkerEvent] = []
    result_event: ResultEvent | None = None

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=project_dir,
        limit=10 * 1024 * 1024,  # 10 MB – stream-json lines can be very long
    )
    _active_process = proc
    tui.activity_log.set_session_active(True)

    timed_out = False
    try:
        async def _stream_and_collect() -> int:
            with open(log_file, "w", buffering=1) as log_fh:  # line-buffered
                if proc.stdout is None:
                    raise RuntimeError("Failed to capture subprocess stdout")
                async for raw_line in proc.stdout:
                    line = raw_line.decode("utf-8", errors="replace")
                    log_fh.write(line)

                    events = parse_line(line)
                    for event in events:
                        tui.handle_event(event)
                        if isinstance(event, MarkerEvent):
                            markers.append(event)
                        elif isinstance(event, ResultEvent):
                            nonlocal result_event
                            result_event = event

            return await proc.wait()

        timeout_secs = timeout_minutes * 60
        try:
            exit_code = await asyncio.wait_for(_stream_and_collect(), timeout=timeout_secs)
        except asyncio.TimeoutError:
            timed_out = True
            from .models import TextEvent as _TE
            tui.handle_event(_TE(
                text=f"SESSION TIMEOUT: {timeout_minutes}m exceeded. Terminating subprocess.",
                is_thinking=False,
            ))
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
            exit_code = -1
    finally:
        _active_process = None
        tui.activity_log.set_session_active(False)

    if timed_out:
        return StepResult(
            kind=step_kind or StepKind.CS,
            story_key=story_key,
            duration_ms=timeout_minutes * 60 * 1000,
            num_turns=result_event.num_turns if result_event else 0,
            cost_usd=result_event.cost_usd if result_event else None,
            markers_detected=markers,
            success=False,
        )

    # Determine success: check both exit code and result event
    if result_event is not None:
        success = not result_event.is_error and exit_code == 0
    else:
        success = exit_code == 0

    return StepResult(
        kind=step_kind or StepKind.CS,
        story_key=story_key,
        duration_ms=result_event.duration_ms if result_event else 0,
        num_turns=result_event.num_turns if result_event else 0,
        cost_usd=result_event.cost_usd if result_event else None,
        markers_detected=markers,
        success=success,
    )


async def run_commit_session(
    story_id: str,
    story_key: str,
    story_file: Path,
    project_dir: Path,
    tui: TUI,
) -> StepResult:
    """Generate a commit message via Claude and create the git commit.

    Falls back to a generic message if Claude generation fails.
    """
    prompt = f"""Generate a git commit message for this story implementation.

Story ID: {story_id}
Story key: {story_key}
Story file: {story_file}

Rules:
- First line: feat(story-{story_id}): <concise description of what was built>
- Empty line, then 3-6 bullet points summarizing the key changes
- End with: Co-Authored-By: Claude <noreply@anthropic.com>
- Keep it factual — describe what was implemented, not the process
- Max 20 words for the first line

Read the story file at {story_file} and run 'git diff --cached --stat' to understand what changed.
Output ONLY the commit message, nothing else."""

    # git add -A
    add_proc = await asyncio.create_subprocess_exec(
        "git", "add", "-A",
        cwd=project_dir,
    )
    await add_proc.wait()

    # Generate commit message via Claude
    commit_msg = ""
    tui.activity_log.set_session_active(True)
    try:
        gen_proc = await asyncio.create_subprocess_exec(
            "claude", "-p", prompt,
            "--max-turns", "5",
            "--dangerously-skip-permissions",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_dir,
        )
        stdout, _ = await gen_proc.communicate()
        if gen_proc.returncode == 0 and stdout:
            commit_msg = stdout.decode("utf-8", errors="replace").strip()
    except (OSError, FileNotFoundError):
        pass
    finally:
        tui.activity_log.set_session_active(False)

    # Fallback
    if not commit_msg:
        commit_msg = f"feat(story-{story_id}): implement {story_key}\n\nCo-Authored-By: Claude <noreply@anthropic.com>"

    # git commit
    commit_proc = await asyncio.create_subprocess_exec(
        "git", "commit", "-m", commit_msg,
        cwd=project_dir,
    )
    await commit_proc.wait()

    success = commit_proc.returncode == 0
    from .models import TextEvent
    tui.handle_event(TextEvent(
        text=f"{'Committed' if success else 'Commit failed'}: story-{story_id}",
        is_thinking=False,
    ))

    return StepResult(
        kind=StepKind.COMMIT,
        story_key=story_key,
        success=success,
    )


def cleanup_subprocess() -> None:
    """Terminate active subprocess on signal. Called from signal handler."""
    if _active_process is not None and _active_process.returncode is None:
        _active_process.terminate()
