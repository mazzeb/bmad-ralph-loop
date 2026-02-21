"""Tests for run_stories.sprint_status helpers."""

from __future__ import annotations

from run_stories.sprint_status import count_epics, count_stories


SAMPLE_STATUS = {
    "development_status": {
        "epic-1": "done",
        "1-1-user-auth": "done",
        "1-2-account-mgmt": "done",
        "1-3-plant-data": "done",
        "epic-1-retrospective": "optional",
        "epic-2": "in-progress",
        "2-1-personality": "done",
        "2-2-chat-interface": "review",
        "2-3-llm-integration": "backlog",
        "epic-2-retrospective": "optional",
        "epic-3": "backlog",
        "3-1-dashboard": "backlog",
        "3-2-analytics": "backlog",
    }
}


class TestCountEpics:
    def test_counts_total_and_done(self):
        total, done = count_epics(SAMPLE_STATUS)
        assert total == 3
        assert done == 1

    def test_empty_status(self):
        total, done = count_epics({})
        assert total == 0
        assert done == 0

    def test_no_done_epics(self):
        data = {"development_status": {"epic-1": "backlog", "epic-2": "in-progress"}}
        total, done = count_epics(data)
        assert total == 2
        assert done == 0


class TestCountStories:
    def test_counts_total_and_done(self):
        total, done = count_stories(SAMPLE_STATUS)
        assert total == 8
        assert done == 4

    def test_empty_status(self):
        total, done = count_stories({})
        assert total == 0
        assert done == 0

    def test_no_done_stories(self):
        data = {"development_status": {"1-1-foo": "backlog", "2-1-bar": "review"}}
        total, done = count_stories(data)
        assert total == 2
        assert done == 0
