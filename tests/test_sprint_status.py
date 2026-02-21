"""Tests for run_stories.sprint_status helpers."""

from __future__ import annotations

from run_stories.sprint_status import (
    count_epics,
    count_stories,
    find_done_stories,
    next_actionable_story,
)


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
        data = {"development_status": {
            "epic-1": "backlog",
            "1-1-foo": "backlog",
            "epic-2": "in-progress",
            "2-1-bar": "review",
        }}
        total, done = count_epics(data)
        assert total == 2
        assert done == 0

    def test_epic_done_when_all_stories_done_despite_epic_status(self):
        """Epic counts as done when all its stories are done, even if epic-N is not 'done'."""
        data = {"development_status": {
            "epic-1": "in-progress",
            "1-1-auth": "done",
            "1-2-mgmt": "done",
            "epic-1-retrospective": "optional",
        }}
        total, done = count_epics(data)
        assert total == 1
        assert done == 1

    def test_epic_not_done_when_some_stories_incomplete(self):
        """Epic with a mix of done and non-done stories is not counted as done."""
        data = {"development_status": {
            "epic-1": "in-progress",
            "1-1-auth": "done",
            "1-2-mgmt": "review",
        }}
        total, done = count_epics(data)
        assert total == 1
        assert done == 0

    def test_epic_not_done_with_no_stories(self):
        """Epic with no stories should not count as done."""
        data = {"development_status": {"epic-1": "done"}}
        total, done = count_epics(data)
        assert total == 1
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


class TestNextActionableStory:

    def test_returns_in_progress_story(self):
        data = {"development_status": {
            "1-1-alpha": "in-progress",
            "1-2-beta": "backlog",
        }}
        assert next_actionable_story(data) == ("1-1-alpha", "in-progress")

    def test_returns_review_story(self):
        data = {"development_status": {
            "1-1-alpha": "review",
            "1-2-beta": "backlog",
        }}
        assert next_actionable_story(data) == ("1-1-alpha", "review")

    def test_returns_ready_for_dev_story(self):
        data = {"development_status": {
            "1-1-alpha": "ready-for-dev",
            "1-2-beta": "backlog",
        }}
        assert next_actionable_story(data) == ("1-1-alpha", "ready-for-dev")

    def test_returns_backlog_story(self):
        data = {"development_status": {"1-1-alpha": "backlog"}}
        assert next_actionable_story(data) == ("1-1-alpha", "backlog")

    def test_in_progress_wins_over_backlog(self):
        data = {"development_status": {
            "1-1-alpha": "backlog",
            "1-2-beta": "in-progress",
        }}
        assert next_actionable_story(data) == ("1-2-beta", "in-progress")

    def test_review_wins_over_ready_for_dev(self):
        data = {"development_status": {
            "1-1-alpha": "ready-for-dev",
            "1-2-beta": "review",
        }}
        assert next_actionable_story(data) == ("1-2-beta", "review")

    def test_all_done_returns_none(self):
        data = {"development_status": {
            "1-1-alpha": "done",
            "1-2-beta": "done",
        }}
        assert next_actionable_story(data) is None

    def test_empty_dict_returns_none(self):
        assert next_actionable_story({}) is None
        assert next_actionable_story({"development_status": {}}) is None

    def test_skips_epic_keys(self):
        data = {"development_status": {
            "epic-1": "in-progress",
            "1-1-alpha": "backlog",
        }}
        assert next_actionable_story(data) == ("1-1-alpha", "backlog")

    def test_uses_sample_status(self):
        # 2-2-chat-interface is "review" which beats "backlog" stories
        assert next_actionable_story(SAMPLE_STATUS) == ("2-2-chat-interface", "review")


class TestFindDoneStories:

    def test_returns_done_stories_sorted_descending(self):
        data = {"development_status": {
            "1-1-alpha": "done",
            "2-3-gamma": "done",
            "1-2-beta": "done",
        }}
        result = find_done_stories(data)
        assert result == ["2-3-gamma", "1-2-beta", "1-1-alpha"]

    def test_excludes_non_done_stories(self):
        data = {"development_status": {
            "1-1-alpha": "done",
            "1-2-beta": "backlog",
            "1-3-gamma": "in-progress",
        }}
        assert find_done_stories(data) == ["1-1-alpha"]

    def test_excludes_epic_keys(self):
        data = {"development_status": {
            "epic-1": "done",
            "1-1-alpha": "done",
        }}
        assert find_done_stories(data) == ["1-1-alpha"]

    def test_empty_returns_empty_list(self):
        assert find_done_stories({}) == []
        assert find_done_stories({"development_status": {}}) == []

    def test_no_done_returns_empty_list(self):
        data = {"development_status": {"1-1-alpha": "backlog"}}
        assert find_done_stories(data) == []

    def test_uses_sample_status(self):
        result = find_done_stories(SAMPLE_STATUS)
        assert "2-1-personality" in result
        assert "1-3-plant-data" in result
        assert len(result) == 4
        # Most recent first
        assert result[0] == "2-1-personality"
