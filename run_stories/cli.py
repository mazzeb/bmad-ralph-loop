"""CLI entry point for run-stories."""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.live import Live

from .claude_session import cleanup_subprocess
from .models import SessionConfig
from .orchestrator import run_stories
from .tui import TUI


def parse_args(argv: list[str] | None = None) -> tuple[SessionConfig, bool]:
    """Parse CLI arguments and return (config, show_thinking)."""
    parser = argparse.ArgumentParser(
        prog="run-stories",
        description="BMAD Story Runner — runs the full story cycle: Create Story → Dev Story → Code Review → commit",
    )
    parser.add_argument(
        "--max-stories", type=int, default=999,
        help="Stop after N stories (default: unlimited)",
    )
    parser.add_argument(
        "--max-turns-cs", type=int, default=100,
        help="Max turns for create-story sessions (default: 100)",
    )
    parser.add_argument(
        "--max-turns-ds", type=int, default=200,
        help="Max turns for dev-story sessions (default: 200)",
    )
    parser.add_argument(
        "--max-turns-cr", type=int, default=150,
        help="Max turns for code-review sessions (default: 150)",
    )
    parser.add_argument(
        "--max-review-rounds", type=int, default=3,
        help="Max dev→review rounds per story (default: 3)",
    )
    parser.add_argument(
        "--dev-model", default="",
        help="Model for create-story and dev-story (default: system default)",
    )
    parser.add_argument(
        "--review-model", default="",
        help="Model for code review — use a DIFFERENT model (default: system default)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would run without executing",
    )
    parser.add_argument(
        "--show-thinking", action="store_true",
        help="Display thinking blocks in the activity log",
    )

    args = parser.parse_args(argv)

    project_dir = Path.cwd()

    config = SessionConfig(
        project_dir=project_dir,
        max_stories=args.max_stories,
        max_turns_cs=args.max_turns_cs,
        max_turns_ds=args.max_turns_ds,
        max_turns_cr=args.max_turns_cr,
        max_review_rounds=args.max_review_rounds,
        dev_model=args.dev_model,
        review_model=args.review_model,
        dry_run=args.dry_run,
        show_thinking=args.show_thinking,
    )

    return config, args.show_thinking


async def _run(config: SessionConfig, show_thinking: bool) -> int:
    """Async main: set up TUI, run orchestrator, return exit code."""
    tui = TUI(show_thinking=show_thinking)
    console = Console()

    # Register signal handler for graceful subprocess cleanup
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, cleanup_subprocess)

    with Live(console=console, refresh_per_second=4, screen=True) as live:
        async def _refresh_loop() -> None:
            """Periodically push fresh renderable to Live."""
            while True:
                live.update(tui.get_renderable())
                await asyncio.sleep(0.25)

        refresh_task = asyncio.create_task(_refresh_loop())
        try:
            story_count = await run_stories(config, tui)
        finally:
            refresh_task.cancel()
            try:
                await refresh_task
            except asyncio.CancelledError:
                pass

    return 0 if story_count > 0 else 1


def main(argv: list[str] | None = None) -> None:
    """Entry point."""
    config, show_thinking = parse_args(argv)
    exit_code = asyncio.run(_run(config, show_thinking))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
