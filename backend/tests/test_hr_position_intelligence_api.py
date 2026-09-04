from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.hr.position_intelligence_models import (
    OfficialPositionVersion,
    PositionContextVersion,
)
from app.hr.position_intelligence_repository import (
    PositionContextConflict,
    PositionContextNotFound,
)
from app.hr.position_intelligence_routes import build_position_intelligence_router


def _context(owner_id, position_id, state="confirmed"):
    now = datetime.now(UTC)
    confirmed = state != "draft"
    return PositionContextVersion(
        uuid4(), owner_id, position_id, 1, state, {"mission": {"text": "Build"}},
        "Position context", None, None, None, None, None, (), None, None,
        owner_id, owner_id if confirmed else None, now, now if confirmed else None, 1,
    )


class Service:
    def __init__(self, owner_id, position_id):
        self.current_record = _context(owner_id, position_id)
        self.draft_record = _context(owner_id, position_id, "draft")
        now = datetime.now(UTC)
        self.official_record = OfficialPositionVersion(
            uuid4(), owner_id, position_id, "J11014", "算法工程师", "机器人",
            ("深圳",), "研发", "算法", 1, "本科", "全职", "20K-30K",
            "Build.", "Test.", "sync-v1", now, "a" * 64, now, now,
            "active", "published", {"snapshot": "sync-v1"}, now,
        )
        self.calls = []
        self.error = None

    def _result(self, value):
        if self.error:
            raise self.error
        return value

    def current(self, *args):
        self.calls.append(("current", args))
        return self._result(self.current_record)

    def history(self, *args):
        self.calls.append(("history", args))
        return self._result((self.current_record, self.draft_record))

    def drafts(self, *args):
        self.calls.append(("drafts", args))
        return self._result((self.draft_record,))

    def create_draft(self, **kwargs):
        self.calls.append(("create", kwargs))
        return self._result(self.draft_record)

    def confirm_modules(self, **kwargs):
        self.calls.append(("confirm", kwargs))
        return self._result(self.current_record)

    def compare(self, owner_id, position_id, left, right):
        self.calls.append(("compare", owner_id, position_id, left, right))
        return self._result({
            "left_version_id": left, "right_version_id": right,
            "changed_modules": ("mission",),
            "left": {"mission": {}}, "right": {"mission": {"text": "Build"}},
        })

    def official_versions(self, *args):
        self.calls.append(("official", args))
        return self._result((self.official_record,))

    def official_version(self, *args):
        self.calls.append(("official_detail", args))
        return self._result(self.official_record)


def _client():
    owner_id, position_id = uuid4(), uuid4()
    service = Service(owner_id, position_id)

    async def require_hr_access(_request, *, writable=False):
        service.calls.append(("access", writable))
        return owner_id

    app = FastAPI()
    app.include_router(build_position_intelligence_router(service, require_hr_access))
    return TestClient(app), service, owner_id, position_id


def test_context_api_returns_current_history_drafts_and_diff() -> None:
    client, service, owner_id, position_id = _client()

    current = client.get(f"/api/hr/positions/{position_id}/context")
    history = client.get(f"/api/hr/positions/{position_id}/context/versions")
    compared = client.get(
        f"/api/hr/positions/{position_id}/context/compare",
        params={"left": service.current_record.context_version_id,
                "right": service.draft_record.context_version_id},
    )

    assert current.status_code == history.status_code == compared.status_code == 200
    assert current.json()["current"]["state"] == "confirmed"
    assert current.json()["drafts"][0]["state"] == "draft"
    assert len(history.json()["items"]) == 2
    assert compared.json()["changed_modules"] == ["mission"]
    assert ("current", (owner_id, position_id)) in service.calls
    assert current.headers["cache-control"] == "private, no-store"


def test_official_fact_api_returns_list_and_exact_version_detail() -> None:
    client, service, _, position_id = _client()

    listed = client.get(f"/api/hr/positions/{position_id}/official-versions")
    detail = client.get(
        f"/api/hr/positions/{position_id}/official-versions/"
        f"{service.official_record.official_position_version_id}"
    )

    assert listed.status_code == detail.status_code == 200
    assert listed.json()["items"][0]["duty"] == "Build."
    assert detail.json()["requirement"] == "Test."


def test_context_api_creates_and_human_confirms_selected_modules() -> None:
    client, service, owner_id, position_id = _client()
    request_id = str(uuid4())
    draft = client.post(
        f"/api/hr/positions/{position_id}/context/drafts",
        headers={"Idempotency-Key": request_id},
        json={
            "base_context_version_id": str(service.current_record.context_version_id),
            "official_version_id": None,
            "modules": {"jd": {"duty": "Build"}},
            "summary": "Updated JD",
            "source_material_attachment_ids": [],
        },
    )
    confirmed = client.post(
        f"/api/hr/positions/{position_id}/context/drafts/"
        f"{service.draft_record.context_version_id}/confirm",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_current_context_version_id": str(
                service.current_record.context_version_id
            ),
            "expected_draft_row_version": 1,
            "module_names": ["mission"],
        },
    )

    assert draft.status_code == confirmed.status_code == 200
    create_call = next(call for call in service.calls if call[0] == "create")
    assert create_call[1]["owner_id"] == owner_id
    confirm_call = next(call for call in service.calls if call[0] == "confirm")
    assert confirm_call[1]["confirmed_by"] == owner_id
    assert confirm_call[1]["module_names"] == ("mission",)


def test_context_api_maps_scope_and_conflict_failures_without_leaking() -> None:
    client, service, _, position_id = _client()
    service.error = PositionContextNotFound("hidden")
    not_found = client.get(f"/api/hr/positions/{position_id}/context")
    service.error = PositionContextConflict("stale internal baseline")
    conflict = client.get(f"/api/hr/positions/{position_id}/context")

    assert not_found.status_code == 404
    assert not_found.json() == {"detail": "HR position context not found"}
    assert conflict.status_code == 409
    assert "internal" not in conflict.text
