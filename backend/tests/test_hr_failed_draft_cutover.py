"""Disposition of a failed historical draft is distinct from execution drain.

The failed row is an explicit historical fixture, not a fabricated successful
parse. All subsequent user repository operations use the real application role.
"""
from uuid import uuid4

import pytest
from app.hr.candidate_models import RetryCandidateDraft
from app.hr.candidate_repository import CandidateRepository, CandidateUnavailable
from tests.hr_agent_support import hr_agent_database
from tests.test_hr_candidate_database import _seed_candidate_scope


@pytest.fixture
def failed_draft():
    with hr_agent_database(cutover_phase="legacy") as database:
        ids = _seed_candidate_scope({"admin": database.admin_dsn})
        with database.admin_connection() as connection:
            connection.execute(
                "update platform_hr.candidate_drafts set state='failed', "
                "extracted_facts='{}',error_code='fixture_parse_failed' where draft_id=%s",
                (ids["draft"],),
            )
        yield database, CandidateRepository(database.dsn), ids


def drain(database):
    with database.admin_connection() as connection:
        connection.execute(
            "select * from platform_control.transition_hr_execution_cutover_v102('draining_legacy',%s)",
            (uuid4(),),
        )


def test_failed_draft_can_be_explicitly_dismissed_during_legacy_drain(failed_draft):
    database, repository, ids = failed_draft
    drain(database)
    result = repository.dismiss_draft(RetryCandidateDraft(ids["owner"], ids["draft"], uuid4(), 2))
    assert result.state == "dismissed"
    assert result.error_code is None
    with database.admin_connection() as connection:
        assert connection.execute("select count(*) from platform_hr.candidates").fetchone() == (0,)
        assert connection.execute(
            "select mutation_kind from platform_hr.candidate_draft_mutation_events where draft_id=%s",
            (ids["draft"],),
        ).fetchall() == [("dismiss",)]


def test_failed_draft_kept_read_only_cannot_retry_during_drain_or_mutate_after_cloud(failed_draft):
    database, repository, ids = failed_draft
    drain(database)
    command = RetryCandidateDraft(ids["owner"], ids["draft"], uuid4(), 2)
    with pytest.raises(CandidateUnavailable):
        repository.retry_draft(command)
    with database.admin_connection() as connection:
        assert connection.execute("select * from platform_control.hr_execution_cutover_counts_v102()").fetchone() == (0, 0)
        connection.execute(
            "select * from platform_control.transition_hr_execution_cutover_v102('cloud',%s)", (uuid4(),)
        )
    for operation in (repository.retry_draft, repository.dismiss_draft):
        with pytest.raises(CandidateUnavailable):
            operation(command)
    actual = repository.draft_for_owner(ids["owner"], ids["draft"])
    assert actual.state == "failed"
    assert actual.error_code == "fixture_parse_failed"
    assert actual.row_version == 2
    with database.admin_connection() as connection:
        assert connection.execute("select count(*) from platform_hr.candidate_draft_mutation_events").fetchone() == (0,)
        assert connection.execute("select count(*) from platform_hr.candidate_draft_processing_attempts").fetchone() == (0,)


def test_failed_draft_retry_is_available_before_legacy_drain(failed_draft):
    database, repository, ids = failed_draft
    result = repository.retry_draft(RetryCandidateDraft(ids["owner"], ids["draft"], uuid4(), 2))
    assert result.state == "pending"
    assert result.attachment_id == ids["attachment"]
    with database.admin_connection() as connection:
        assert connection.execute("select count(*) from platform_hr.candidate_drafts").fetchone() == (1,)
        assert connection.execute("select legacy_nonterminal from platform_control.hr_execution_cutover_counts_v102()").fetchone()[0] == 1
