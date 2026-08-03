from dataclasses import dataclass, field

from app.review.backfill import backfill_negative_feedback
from app.review.models import NegativeFeedbackGroup


@dataclass
class FakeRepository:
    groups: list[NegativeFeedbackGroup]
    issues: set[tuple[str, str]] = field(default_factory=set)
    links: set[tuple[str, str]] = field(default_factory=set)
    events: set[tuple[str, str]] = field(default_factory=set)

    def list_negative_feedback_groups(self):
        return self.groups

    def backfill_negative_group(self, group, *, actor):
        key = (group.agent_id, group.turn_key)
        issue_created = key not in self.issues
        link_created = key not in self.links
        event_created = key not in self.events
        self.issues.add(key)
        self.links.add(key)
        self.events.add(key)
        return issue_created, link_created, event_created


def _groups():
    groups = [
        NegativeFeedbackGroup(
            agent_id="ai-fae-agent",
            turn_key=f"fae:turn-{index}",
            question=f"问题 {index}",
            feedback_keys=(f"fae:feedback-{index}",),
        )
        for index in range(50)
    ]
    groups[0] = NegativeFeedbackGroup(
        agent_id="ai-fae-agent",
        turn_key="fae:turn-0",
        question="重复点踩的问题",
        feedback_keys=("fae:feedback-0", "fae:feedback-duplicate"),
    )
    return groups


def test_backfill_groups_feedback_by_negative_turn():
    repository = FakeRepository(_groups())

    report = backfill_negative_feedback(repository, actor="codex")

    assert report.baseline_negative_rows == 51
    assert report.baseline_negative_turns == 50
    assert report.live_negative_rows == 51
    assert report.live_negative_turns == 50
    assert report.created_issues == 50
    assert report.created_links == 50
    assert report.created_events == 50
    assert report.linked_feedback_keys == 51


def test_backfill_is_idempotent():
    repository = FakeRepository(_groups())
    backfill_negative_feedback(repository, actor="codex")

    second = backfill_negative_feedback(repository, actor="codex")

    assert second.created_issues == 0
    assert second.created_links == 0
    assert second.created_events == 0
