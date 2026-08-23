from __future__ import annotations

import json

import pytest

from app.control_plane.dingtalk import DingTalkDepartment, DingTalkMember
from app.control_plane.gender_probe import GenderProbeError, collect_gender_coverage


class FakeDingTalkClient:
    def __init__(
        self,
        *,
        fail: bool = False,
        conflict: bool = False,
        members_by_department: dict[int, list[DingTalkMember]] | None = None,
    ) -> None:
        self.fail = fail
        self.conflict = conflict
        self.members_by_department = members_by_department
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True

    async def iter_departments(self):
        yield DingTalkDepartment(2, 1, "Engineering")
        yield DingTalkDepartment(3, 1, "Operations")

    async def iter_department_members(self, department_id: int):
        if self.fail:
            raise RuntimeError("provider response must not be rendered")
        if self.members_by_department is not None:
            for member in self.members_by_department.get(department_id, []):
                yield member
            return
        if department_id == 1:
            yield DingTalkMember("employee-1", "union-1", "One", True, (1,), "male", "valid")
            yield DingTalkMember("employee-4", "union-4", "Inactive", False, (1,), None, "invalid")
        if department_id == 2:
            yield DingTalkMember("employee-1", "union-1", "One", True, (1,), "male", "valid")
            yield DingTalkMember("employee-2", "union-2", "Two", True, (2,), None, "missing")
        if department_id == 3:
            if self.conflict:
                yield DingTalkMember("employee-1", "union-1", "Changed", True, (1,), "male", "valid")
            yield DingTalkMember("employee-3", "union-3", "Three", True, (3,), None, "invalid")


class FakeSettings:
    app_key = "test-app-key"
    app_secret = "test-app-secret"
    corp_id = "test-corp"


@pytest.mark.asyncio
async def test_collect_gender_coverage_counts_unique_active_members_only() -> None:
    fake_client = FakeDingTalkClient()

    coverage = await collect_gender_coverage(fake_client)

    assert coverage.as_public_dict() == {
        "attribute_name": "性别",
        "active_employee_count": 3,
        "valid_count": 1,
        "missing_count": 1,
        "invalid_count": 1,
        "permission_readable": True,
        "ready": False,
    }
    assert "employee-1" not in json.dumps(coverage.as_public_dict(), ensure_ascii=False)


@pytest.mark.asyncio
async def test_collect_gender_coverage_rejects_conflicting_duplicate_snapshots() -> None:
    with pytest.raises(GenderProbeError, match="member_conflict"):
        await collect_gender_coverage(FakeDingTalkClient(conflict=True))


@pytest.mark.asyncio
async def test_collect_gender_coverage_closes_client_after_provider_failure() -> None:
    fake_client = FakeDingTalkClient(fail=True)

    with pytest.raises(GenderProbeError, match="provider_failed"):
        await collect_gender_coverage(fake_client)

    assert fake_client.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("member", "permission_readable", "ready"),
    [
        (
            DingTalkMember("employee-1", "union-1", "One", True, (1,), "male", "valid"),
            True,
            True,
        ),
        (
            DingTalkMember("employee-1", "union-1", "One", True, (1,), None, "missing"),
            False,
            False,
        ),
        (
            DingTalkMember("employee-1", "union-1", "One", True, (1,), None, "invalid"),
            True,
            False,
        ),
    ],
)
async def test_collect_gender_coverage_requires_every_active_member_to_be_valid(
    member, permission_readable, ready
) -> None:
    coverage = await collect_gender_coverage(
        FakeDingTalkClient(members_by_department={1: [member]})
    )

    assert coverage.permission_readable is permission_readable
    assert coverage.ready is ready


@pytest.mark.asyncio
async def test_collect_gender_coverage_does_not_mark_zero_population_ready() -> None:
    coverage = await collect_gender_coverage(
        FakeDingTalkClient(members_by_department={})
    )

    assert coverage.active_employee_count == 0
    assert coverage.permission_readable is False
    assert coverage.ready is False


@pytest.mark.asyncio
async def test_collect_gender_coverage_enforces_directory_bounds(monkeypatch) -> None:
    from app.control_plane import gender_probe

    monkeypatch.setattr(gender_probe, "MAX_DEPARTMENTS", 1)
    with pytest.raises(GenderProbeError, match="department_count_bound"):
        await collect_gender_coverage(FakeDingTalkClient())

    monkeypatch.setattr(gender_probe, "MAX_DEPARTMENTS", 20_000)
    monkeypatch.setattr(gender_probe, "MAX_MEMBERS", 1)
    with pytest.raises(GenderProbeError, match="member_count_bound"):
        await collect_gender_coverage(FakeDingTalkClient())


@pytest.mark.parametrize(
    "member",
    [
        DingTalkMember("employee-1", "union-1", "One", True, (1,), None, "missing"),
        DingTalkMember("employee-1", "union-1", "One", True, (1,), None, "invalid"),
        None,
    ],
)
def test_main_returns_nonzero_with_coverage_and_fixed_marker_when_not_ready(
    monkeypatch, capsys, member
) -> None:
    from app.control_plane import gender_probe

    members = {} if member is None else {1: [member]}
    monkeypatch.setattr(gender_probe, "load_worker_settings", lambda service: FakeSettings())
    monkeypatch.setattr(
        gender_probe,
        "DingTalkClient",
        lambda **kwargs: FakeDingTalkClient(members_by_department=members),
    )

    assert gender_probe.main() == 1
    captured = capsys.readouterr()
    assert set(json.loads(captured.out)) == {
        "attribute_name",
        "active_employee_count",
        "valid_count",
        "missing_count",
        "invalid_count",
        "permission_readable",
        "ready",
    }
    assert captured.err == "DINGTALK_GENDER_PROBE_FAILED\n"


def test_main_writes_only_a_fixed_safe_marker_for_probe_failure(monkeypatch, capsys) -> None:
    from app.control_plane import gender_probe

    monkeypatch.setattr(gender_probe, "load_worker_settings", lambda service: FakeSettings())
    monkeypatch.setattr(gender_probe, "DingTalkClient", lambda **kwargs: FakeDingTalkClient(fail=True))

    assert gender_probe.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "DINGTALK_GENDER_PROBE_FAILED\n"
