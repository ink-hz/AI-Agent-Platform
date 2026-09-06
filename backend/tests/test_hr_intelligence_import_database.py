from datetime import datetime, timezone
from uuid import uuid4

from app.hr.intelligence_import import DatabaseIntelligenceImportRepository


class Cursor:
    def __init__(self, row) -> None:
        self.row = row
        self.calls = []

    def execute(self, query, parameters):
        self.calls.append((query, parameters))
        return self

    def fetchone(self):
        return self.row


class Connection(Cursor):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_database_repository_uses_only_v85_import_function() -> None:
    owner_id = uuid4()
    bundle_id = uuid4()
    row = {
        "bundle_id": bundle_id,
        "owner_internal_user_id": owner_id,
        "manifest_sha256": "a" * 64,
    }
    connection = Connection(row)
    repository = DatabaseIntelligenceImportRepository(lambda: connection)
    bundle = type(
        "Verified",
        (),
        {
            "bundle_id": bundle_id,
            "manifest_sha256": "a" * 64,
            "generated_at": datetime(2026, 9, 6, 8, tzinfo=timezone.utc),
            "manifest": {"bundle_id": str(bundle_id)},
            "catalog": {"companies": []},
            "coverage": {"companies": []},
            "jobs": (),
            "aggregates": {},
            "analysis": (),
            "usage": (),
            "evidence_index": (),
            "bundle_locator": f"bundles/{bundle_id}",
        },
    )()

    assert repository.import_verified_bundle(owner_id, bundle) == row
    assert len(connection.calls) == 1
    query, parameters = connection.calls[0]
    assert "platform_hr.import_intelligence_bundle_v85" in query
    assert parameters[0] == owner_id
    assert parameters[1] == bundle_id
