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


def next_backlog_story(data: dict) -> str | None:
    """Return the first story key with status 'backlog', or None."""
    dev_status = data.get("development_status", {})
    for key, status in dev_status.items():
        if re.match(r"\d+-\d+-", str(key)) and str(status) == "backlog":
            return str(key)
    return None


def story_id_from_key(key: str) -> str:
    """Extract epic.story from key (e.g., '1-3-stock-search' → '1.3')."""
    parts = key.split("-")
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return key
