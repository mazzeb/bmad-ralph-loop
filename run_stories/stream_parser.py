"""Stream parser for Claude CLI stream-json output.

Pure, stateless: each call to parse_line() takes a raw JSON string
and returns the appropriate StreamEvent dataclass. Never raises.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from .models import (
    InitEvent,
    MarkerEvent,
    MarkerType,
    RateLimitEvent,
    ResultEvent,
    SystemEvent,
    TextEvent,
    ToolResultEvent,
    ToolUseEvent,
    UnknownEvent,
    StreamEvent,
    summarize_tool_input,
)

# Matches paired markers like <HALT>reason</HALT> and self-closing like <NO_BACKLOG_STORIES/>
_MARKER_PAIRED_RE = re.compile(
    r"<(HALT|CREATE_STORY_COMPLETE|DEV_STORY_COMPLETE|CODE_REVIEW_APPROVED|CODE_REVIEW_ISSUES)>(.*?)</\1>",
    re.DOTALL,
)
_MARKER_SELF_RE = re.compile(r"<(NO_BACKLOG_STORIES|NO_READY_STORIES)\s*/>")


def parse_line(raw: str) -> list[StreamEvent]:
    """Parse a single JSON line into one or more StreamEvent objects.

    Returns a list because a single assistant text block may contain both
    a TextEvent and a MarkerEvent. Returns [UnknownEvent] on any failure.
    """
    raw = raw.strip()
    if not raw:
        return [UnknownEvent(raw_data="")]

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return [UnknownEvent(raw_data=raw)]

    if not isinstance(data, dict):
        return [UnknownEvent(raw_data=data)]

    msg_type = data.get("type", "")

    try:
        match msg_type:
            case "system":
                return _parse_system(data)
            case "assistant":
                return _parse_assistant(data)
            case "user":
                return _parse_user(data)
            case "result":
                return _parse_result(data)
            case "rate_limit_event":
                return _parse_rate_limit(data)
            case _:
                return [UnknownEvent(raw_data=data)]
    except Exception:
        return [UnknownEvent(raw_data=data)]


def _parse_system(data: dict) -> list[StreamEvent]:
    subtype = data.get("subtype", "")
    if subtype == "init":
        return [
            InitEvent(
                model=data.get("model", ""),
                tools=[t.get("name", t) if isinstance(t, dict) else str(t) for t in data.get("tools", [])],
                permission_mode=data.get("permissionMode", ""),
                session_id=data.get("session_id", ""),
            )
        ]
    return [SystemEvent(subtype=subtype)]


def _parse_assistant(data: dict) -> list[StreamEvent]:
    events: list[StreamEvent] = []
    message = data.get("message", {})
    content_list = message.get("content", [])

    for block in content_list:
        block_type = block.get("type", "")
        match block_type:
            case "text":
                text = block.get("text", "")
                events.append(TextEvent(text=text, is_thinking=False))
                events.extend(_detect_markers(text))
            case "tool_use":
                name = block.get("name", "")
                input_data = block.get("input", {})
                summary = summarize_tool_input(name, input_data) if isinstance(input_data, dict) else str(input_data)[:60]
                events.append(ToolUseEvent(tool_name=name, input_summary=summary))
            case "thinking":
                text = block.get("thinking", "")
                events.append(TextEvent(text=text, is_thinking=True))

    return events if events else [UnknownEvent(raw_data=data)]


def _parse_user(data: dict) -> list[StreamEvent]:
    events: list[StreamEvent] = []
    message = data.get("message", {})
    content_list = message.get("content", [])

    for block in content_list:
        block_type = block.get("type", "")
        match block_type:
            case "tool_result":
                tool_use_id = block.get("tool_use_id", "")
                content = block.get("content", "")
                if isinstance(content, list):
                    # content can be a list of blocks
                    parts = []
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            parts.append(part.get("text", ""))
                    summary = " ".join(parts)[:60]
                elif isinstance(content, str):
                    summary = content[:60]
                else:
                    summary = str(content)[:60]
                events.append(ToolResultEvent(tool_use_id=tool_use_id, content_summary=summary))
            case "text":
                text = block.get("text", "")
                events.append(TextEvent(text=text, is_thinking=False))

    return events if events else [UnknownEvent(raw_data=data)]


def _parse_result(data: dict) -> list[StreamEvent]:
    cost = data.get("total_cost_usd")
    if cost is None:
        cost = data.get("cost_usd")
    return [
        ResultEvent(
            duration_ms=data.get("duration_ms", 0),
            num_turns=data.get("num_turns", 0),
            is_error=data.get("is_error", False),
            subtype=data.get("subtype", ""),
            cost_usd=float(cost) if cost is not None else None,
        )
    ]


def _parse_rate_limit(data: dict) -> list[StreamEvent]:
    info = data.get("rate_limit_info", {})
    resets_at_raw = info.get("resetsAt")
    resets_at = None
    if resets_at_raw is not None:
        try:
            resets_at = datetime.fromtimestamp(float(resets_at_raw), tz=timezone.utc)
        except (ValueError, TypeError, OSError):
            pass
    return [
        RateLimitEvent(
            status=info.get("status", ""),
            resets_at=resets_at,
            rate_limit_type=info.get("rateLimitType", ""),
        )
    ]


def _detect_markers(text: str) -> list[MarkerEvent]:
    """Scan text for XML orchestration markers."""
    markers: list[MarkerEvent] = []

    for match in _MARKER_PAIRED_RE.finditer(text):
        tag = match.group(1)
        payload = match.group(2)
        try:
            markers.append(MarkerEvent(marker_type=MarkerType(tag), payload=payload))
        except ValueError:
            pass

    for match in _MARKER_SELF_RE.finditer(text):
        tag = match.group(1)
        try:
            markers.append(MarkerEvent(marker_type=MarkerType(tag), payload=""))
        except ValueError:
            pass

    return markers
