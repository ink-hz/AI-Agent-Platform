from __future__ import annotations

from typing import Protocol

from .models import BackfillReport, NegativeFeedbackGroup


BASELINE_NEGATIVE_ROWS = 51
BASELINE_NEGATIVE_TURNS = 50


class BackfillRepository(Protocol):
    def list_negative_feedback_groups(self) -> list[NegativeFeedbackGroup]: ...

    def backfill_negative_group(
        self,
        group: NegativeFeedbackGroup,
        *,
        actor: str,
    ) -> tuple[bool, bool, bool]: ...


def backfill_negative_feedback(
    repository: BackfillRepository,
    *,
    actor: str,
) -> BackfillReport:
    groups = repository.list_negative_feedback_groups()
    created_issues = 0
    created_links = 0
    created_events = 0
    for group in groups:
        issue_created, link_created, event_created = (
            repository.backfill_negative_group(group, actor=actor)
        )
        created_issues += int(issue_created)
        created_links += int(link_created)
        created_events += int(event_created)

    live_rows = sum(len(group.feedback_keys) for group in groups)
    live_turns = len(groups)
    return BackfillReport(
        baseline_negative_rows=BASELINE_NEGATIVE_ROWS,
        baseline_negative_turns=BASELINE_NEGATIVE_TURNS,
        live_negative_rows=live_rows,
        live_negative_turns=live_turns,
        delta_negative_rows=live_rows - BASELINE_NEGATIVE_ROWS,
        delta_negative_turns=live_turns - BASELINE_NEGATIVE_TURNS,
        created_issues=created_issues,
        created_links=created_links,
        created_events=created_events,
        linked_feedback_keys=live_rows,
    )
