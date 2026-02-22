"""CLI entry point for run-stories."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .models import SessionConfig
from .tui import TUI, StoryRunnerApp


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
    parser.add_argument(
        "--session-timeout", type=int, default=30,
        help="Timeout in minutes for each Claude CLI session (default: 30)",
    )
    parser.add_argument(
        "--test-cmd", default="",
        help="Test command to run after dev-story for verification (e.g. 'pytest tests/ -v')",
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
        session_timeout_minutes=args.session_timeout,
        test_cmd=args.test_cmd,
    )

    return config, args.show_thinking


def _run(config: SessionConfig, show_thinking: bool) -> int:
    """Set up TUI, run Textual app with orchestrator, return exit code."""
    tui = TUI(show_thinking=show_thinking)
    app = StoryRunnerApp(tui=tui, config=config)
    app.run()
    return getattr(app, '_exit_code', 1)


def main(argv: list[str] | None = None) -> None:
    """Entry point."""
    config, show_thinking = parse_args(argv)
    exit_code = _run(config, show_thinking)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
