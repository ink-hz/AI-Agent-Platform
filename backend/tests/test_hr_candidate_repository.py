from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest

from app.hr.candidate_models import CreateCandidateDraftBatch
from app.hr.candidate_repository import (
    CandidateConflict,
    CandidateNotFound,
    CandidateRepository,
    CandidateUnavailable,
)

NOW = datetime.now(UTC)


def _draft_row(owner_id, position_id, attachment_id, batch_id, request_id):
    return {
        "draft_id": uuid4(),
        "owner_internal_user_id": owner_id,
        "position_id": position_id,
        "attachment_id": attachment_id,
        "batch_request_id": batch_id,
        "client_request_id": request_id,
        "state": "pending",
        "extracted_facts": {},
        "identity_candidates": [],
        "error_code": None,
        "row_version": 1,
        "created_at": NOW,
        "updated_at": NOW,
    }


class FakeResult:
    def __init__(self, *, one=None, all_rows=()):
        self.one = one
        self.all_rows = all_rows

    def fetchone(self):
        return self.one

    def fetchall(self):
        return list(self.all_rows)


class FakeConnection:
    def __init__(self, results=(), error=None):
        self.results = list(results)
        self.error = error
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def execute(self, sql, parameters=()):
        self.calls.append((" ".join(sql.split()), parameters))
        if self.error is not None:
            raise self.error
        return self.results.pop(0)


def _repository(connection):
    connect_calls = []

    def connect(database_url, **kwargs):
        connect_calls.append((database_url, kwargs))
        return connection

    return CandidateRepository("postgresql://candidate", connect=connect), connect_calls


def test_repository_creates_owner_scoped_draft_through_v69_function() -> None:
    owner_id, position_id, attachment_id, batch_id, request_id = (
        uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    )
    row = _draft_row(owner_id, position_id, attachment_id, batch_id, request_id)
    connection = FakeConnection((FakeResult(one=row),))
    repository, connect_calls = _repository(connection)
    command = CreateCandidateDraftBatch(
        owner_id, position_id, (attachment_id,), batch_id
    )

    draft = repository.create_draft(row["draft_id"], request_id, command, attachment_id)

    assert draft.owner_id == owner_id
    assert draft.attachment_id == attachment_id
    assert "platform_hr.create_candidate_draft_v69" in connection.calls[0][0]
    assert connection.calls[0][1] == (
        row["draft_id"], owner_id, position_id, attachment_id, batch_id, request_id
    )
    assert connect_calls[0][1]["options"] == "-c statement_timeout=10000"


def test_repository_lists_batch_with_explicit_owner_and_stable_order() -> None:
    owner_id, position_id, batch_id = uuid4(), uuid4(), uuid4()
    rows = [
        _draft_row(owner_id, position_id, uuid4(), batch_id, uuid4()),
        _draft_row(owner_id, position_id, uuid4(), batch_id, uuid4()),
    ]
    connection = FakeConnection((FakeResult(all_rows=rows),))
    repository, _ = _repository(connection)

    result = repository.list_drafts(owner_id, position_id, batch_request_id=batch_id)

    assert len(result) == 2
    sql, parameters = connection.calls[0]
    assert "owner_internal_user_id=%s" in sql
    assert "position_id=%s" in sql
    assert "batch_request_id=%s" in sql
    assert "order by created_at,draft_id" in sql
    assert parameters == (owner_id, position_id, batch_id)


@pytest.mark.parametrize(
    ("error", "expected"),
    (
        (psycopg.errors.NoDataFound(), CandidateNotFound),
        (psycopg.errors.UniqueViolation(), CandidateConflict),
        (psycopg.errors.SerializationFailure(), CandidateConflict),
        (psycopg.OperationalError(), CandidateUnavailable),
    ),
)
def test_repository_conceals_database_failures(error, expected) -> None:
    repository, _ = _repository(FakeConnection(error=error))

    with pytest.raises(expected):
        repository.draft_for_owner(uuid4(), uuid4())


def test_repository_refuses_missing_or_malformed_rows() -> None:
    empty, _ = _repository(FakeConnection((FakeResult(one=None),)))
    malformed, _ = _repository(FakeConnection((FakeResult(one={"draft_id": uuid4()}),)))

    with pytest.raises(CandidateNotFound):
        empty.draft_for_owner(uuid4(), uuid4())
    with pytest.raises(CandidateUnavailable):
        malformed.draft_for_owner(uuid4(), uuid4())
