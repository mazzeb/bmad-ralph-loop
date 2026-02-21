"""Sprint status YAML operations.

Pure functions for loading and querying sprint-status.yaml.
No file writing — Claude sessions update the YAML themselves.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml


def load_status(path: Path) -> dict:
    """Load sprint-status.yaml and return the full dict."""
    with open(path) as f:
        return yaml.safe_load(f) or {}


def get_story_status(data: dict, key: str) -> str:
    """Return the status string for a story key, or 'unknown'."""
    dev_status = data.get("development_status", {})
    return str(dev_status.get(key, "unknown"))


def next_actionable_story(data: dict) -> tuple[str, str] | None:
    """Return the highest-priority in-progress story as (key, status_string).

    Priority order: in-progress > review > ready-for-dev > backlog.
    Returns None if no actionable stories exist.
    """
    dev_status = data.get("development_status", {})
    for target_status in ("in-progress", "review", "ready-for-dev", "backlog"):
        for key, status in dev_status.items():
            if re.match(r"\d+-\d+-", str(key)) and str(status) == target_status:
                return (str(key), target_status)
    return None


def find_done_stories(data: dict) -> list[str]:
    """Return all story keys with status 'done', sorted by (epic, story) descending."""
    dev_status = data.get("development_status", {})
    done_keys: list[str] = []
    for key, status in dev_status.items():
        if re.match(r"\d+-\d+-", str(key)) and str(status) == "done":
            done_keys.append(str(key))

    def _sort_key(k: str) -> tuple[int, int]:
        parts = k.split("-")
        try:
            return (int(parts[0]), int(parts[1]))
        except (ValueError, IndexError):
            return (0, 0)

    done_keys.sort(key=_sort_key, reverse=True)
    return done_keys


def story_id_from_key(key: str) -> str:
    """Extract epic.story from key (e.g., '1-3-stock-search' → '1.3')."""
    parts = key.split("-")
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return key


def count_epics(data: dict) -> tuple[int, int]:
    """Return (total_epics, done_epics) from sprint status data."""
    dev_status = data.get("development_status", {})
    total = 0
    done = 0
    for key, status in dev_status.items():
        # $ anchor excludes "epic-1-retrospective" keys
        if re.match(r"epic-\d+$", str(key)):
            total += 1
            if str(status) == "done":
                done += 1
    return total, done


def count_stories(data: dict) -> tuple[int, int]:
    """Return (total_stories, done_stories) from sprint status data."""
    dev_status = data.get("development_status", {})
    total = 0
    done = 0
    for key, status in dev_status.items():
        if re.match(r"\d+-\d+-.+", str(key)):
            total += 1
            if str(status) == "done":
                done += 1
    return total, done
