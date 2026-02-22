"""Event models and data types for the stream-json parser and orchestration state."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


# --- Enums ---


class StepKind(Enum):
    CS = "create-story"
    DS = "dev-story"
    CR = "code-review"
    COMMIT = "commit"


class MarkerType(Enum):
    HALT = "HALT"
    CREATE_STORY_COMPLETE = "CREATE_STORY_COMPLETE"
    DEV_STORY_COMPLETE = "DEV_STORY_COMPLETE"
    CODE_REVIEW_APPROVED = "CODE_REVIEW_APPROVED"
    CODE_REVIEW_ISSUES = "CODE_REVIEW_ISSUES"
    NO_BACKLOG_STORIES = "NO_BACKLOG_STORIES"
    NO_READY_STORIES = "NO_READY_STORIES"


# --- Stream event dataclasses (frozen/immutable) ---


@dataclass(frozen=True)
class InitEvent:
    model: str
    tools: list[str]
    permission_mode: str
    session_id: str


@dataclass(frozen=True)
class ToolUseEvent:
    tool_name: str
    input_summary: str


@dataclass(frozen=True)
class ToolResultEvent:
    tool_use_id: str
    content_summary: str


@dataclass(frozen=True)
class TextEvent:
    text: str
    is_thinking: bool = False


@dataclass(frozen=True)
class ResultEvent:
    duration_ms: int
    num_turns: int
    is_error: bool
    subtype: str
    cost_usd: float | None = None


@dataclass(frozen=True)
class RateLimitEvent:
    status: str
    resets_at: datetime | None
    rate_limit_type: str


@dataclass(frozen=True)
class SystemEvent:
    subtype: str


@dataclass(frozen=True)
class MarkerEvent:
    marker_type: MarkerType
    payload: str


@dataclass(frozen=True)
class UnknownEvent:
    raw_data: dict | str


StreamEvent = (
    InitEvent
    | ToolUseEvent
    | ToolResultEvent
    | TextEvent
    | ResultEvent
    | RateLimitEvent
    | SystemEvent
    | MarkerEvent
    | UnknownEvent
)


# --- Orchestration state dataclasses ---


@dataclass
class StepResult:
    kind: StepKind
    story_key: str
    duration_ms: int = 0
    num_turns: int = 0
    cost_usd: float | None = None
    markers_detected: list[MarkerEvent] = field(default_factory=list)
    success: bool = False


@dataclass
class StoryState:
    story_key: str
    story_id: str
    current_step: StepKind | None = None
    current_round: int = 1
    step_results: list[StepResult] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))


@dataclass
class SessionConfig:
    project_dir: Path
    max_stories: int = 999
    max_turns_cs: int = 100
    max_turns_ds: int = 200
    max_turns_cr: int = 150
    max_review_rounds: int = 3
    dev_model: str = ""
    review_model: str = ""
    dry_run: bool = False
    show_thinking: bool = False
    session_timeout_minutes: int = 30
    test_cmd: str = ""


# --- Helpers ---

# Regex for extracting tool input summaries
_TOOL_INPUT_KEYS = {
    "Read": "file_path",
    "Edit": "file_path",
    "Write": "file_path",
    "Glob": "pattern",
    "Grep": "pattern",
    "Bash": "command",
    "WebFetch": "url",
    "WebSearch": "query",
}


def summarize_tool_input(tool_name: str, input_data: dict) -> str:
    """Extract a short summary from tool input data."""
    key = _TOOL_INPUT_KEYS.get(tool_name)
    if key and key in input_data:
        return str(input_data[key])
    # Fallback: first string value
    for v in input_data.values():
        if isinstance(v, str):
            return v
    return ""
