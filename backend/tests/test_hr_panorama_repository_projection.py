from contextlib import contextmanager
from uuid import uuid4

from app.hr.panorama_repository import PanoramaRepository


class Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def execute(self, query, parameters=()):
        self.calls.append((query, parameters))
        return Result(self.row)


def _repository(row):
    connection = Connection(row)

    @contextmanager
    def connect():
        yield connection

    return PanoramaRepository(connection=connect), connection


def test_current_context_projection_does_not_select_large_analysis_payload() -> None:
    row = {"bundle_id": uuid4(), "agent_chunk_index": []}
    repository, connection = _repository(row)

    assert repository.current_context_bundle() == row

    query, parameters = connection.calls[0]
    assert parameters == ()
    assert "select *" not in query.casefold()
    assert "agent_chunk_index" in query
    assert "analysis" not in query


def test_document_projection_does_not_select_large_analysis_payload() -> None:
    bundle_id = uuid4()
    row = {"bundle_id": bundle_id, "agent_document_index": {}}
    repository, connection = _repository(row)

    assert repository.bundle_document_metadata(bundle_id) == row

    query, parameters = connection.calls[0]
    assert parameters == (bundle_id,)
    assert "select *" not in query.casefold()
    assert "agent_document_index" in query
    assert "analysis" not in query
