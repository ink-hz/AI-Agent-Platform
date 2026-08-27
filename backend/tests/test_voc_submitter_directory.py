from __future__ import annotations

from uuid import UUID

import psycopg
import pytest

from app.voc_extension.directory import (
    VocDirectoryUnavailable,
    VocSubmitterDirectory,
)

ALICE_ID = UUID("11111111-1111-4111-8111-111111111111")
BOB_ID = UUID("22222222-2222-4222-8222-222222222222")
DSN = "postgresql://platform_control_app@localhost/agent_platform_control"


class Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class Connection:
    def __init__(self, rows=None, *, error: Exception | None = None):
        self.rows = rows or []
        self.error = error
        self.calls: list[tuple[str, tuple | None]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=None):
        self.calls.append((query, params))
        if self.error is not None:
            raise self.error
        return Result(self.rows)


class Connect:
    def __init__(self, connection: Connection):
        self.connection = connection
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __call__(self, dsn, **kwargs):
        self.calls.append((dsn, kwargs))
        return self.connection


def test_names_for_batches_unique_ids_into_one_parameterized_query() -> None:
    connection = Connection(
        [
            {"internal_user_id": ALICE_ID, "display_name": "苍渊"},
            {"internal_user_id": BOB_ID, "display_name": "林川"},
        ]
    )
    connect = Connect(connection)
    directory = VocSubmitterDirectory(DSN, connect=connect)

    names = directory.names_for(frozenset({ALICE_ID, BOB_ID}))

    assert names == {ALICE_ID: "苍渊", BOB_ID: "林川"}
    assert len(connection.calls) == 1
    query, params = connection.calls[0]
    assert "where internal_user_id = any(%s)" in query.lower()
    assert set(params[0]) == {ALICE_ID, BOB_ID}
    assert connect.calls[0][1]["connect_timeout"] == 3


def test_names_for_rejects_more_than_one_hundred_without_querying() -> None:
    connect = Connect(Connection())
    directory = VocSubmitterDirectory(DSN, connect=connect)
    ids = frozenset(UUID(int=value) for value in range(1, 102))

    with pytest.raises(ValueError, match="too many VOC submitters"):
        directory.names_for(ids)

    assert connect.calls == []


def test_list_submitters_returns_immutable_public_options_in_database_order() -> None:
    connection = Connection(
        [
            {"internal_user_id": ALICE_ID, "display_name": "苍渊"},
            {"internal_user_id": BOB_ID, "display_name": "林川"},
        ]
    )
    directory = VocSubmitterDirectory(DSN, connect=Connect(connection))

    options = directory.list_submitters()

    assert isinstance(options, tuple)
    assert [(item.internal_user_id, item.display_name) for item in options] == [
        (ALICE_ID, "苍渊"),
        (BOB_ID, "林川"),
    ]
    assert "order by display_name, internal_user_id" in connection.calls[0][0].lower()


def test_database_error_becomes_safe_directory_unavailable() -> None:
    directory = VocSubmitterDirectory(
        DSN,
        connect=Connect(Connection(error=psycopg.OperationalError("secret SQL"))),
    )

    with pytest.raises(VocDirectoryUnavailable) as raised:
        directory.names_for(frozenset({ALICE_ID}))

    assert str(raised.value) == "VOC directory unavailable"
    assert "secret" not in str(raised.value)
