from __future__ import annotations

import json
from collections import Counter

import httpx
import pytest
import respx

from app.control_plane.dingtalk import DingTalkClient, DingTalkDepartment, DingTalkMember
from app.control_plane.gender_probe import GenderProbeError, collect_gender_coverage


API = "https://api.test.invalid"
OAPI = "https://oapi.test.invalid"


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
        self.detail_calls: Counter[str] = Counter()

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

    async def get_member(self, userid: str) -> DingTalkMember:
        self.detail_calls[userid] += 1
        for members in (
            self.members_by_department or {
                1: [
                    DingTalkMember("employee-1", "union-1", "One", True, (1,), "male", "valid"),
                    DingTalkMember("employee-4", "union-4", "Inactive", False, (1,), None, "invalid"),
                ],
                2: [
                    DingTalkMember("employee-1", "union-1", "One", True, (1,), "male", "valid"),
                    DingTalkMember("employee-2", "union-2", "Two", True, (2,), None, "missing"),
                ],
                3: [
                    DingTalkMember("employee-3", "union-3", "Three", True, (3,), None, "invalid"),
                ],
            }
        ).values():
            for member in members:
                if member.userid == userid:
                    return member
        raise RuntimeError("provider detail unavailable")

    async def get_member_genders(self, userids: tuple[str, ...]):
        result = {}
        for userid in userids:
            for members in (
                self.members_by_department or {
                    1: [
                        DingTalkMember("employee-1", "union-1", "One", True, (1,), "male", "valid"),
                        DingTalkMember("employee-4", "union-4", "Inactive", False, (1,), None, "invalid"),
                    ],
                    2: [
                        DingTalkMember("employee-1", "union-1", "One", True, (1,), "male", "valid"),
                        DingTalkMember("employee-2", "union-2", "Two", True, (2,), None, "missing"),
                    ],
                    3: [
                        DingTalkMember("employee-3", "union-3", "Three", True, (3,), None, "invalid"),
                    ],
                }
            ).values():
                match = next((member for member in members if member.userid == userid), None)
                if match is not None:
                    result[userid] = (match.gender, match.gender_attribute_status)
                    break
        return result


class FakeSettings:
    app_key = "test-app-key"
    app_secret = "test-app-secret"
    corp_id = "test-corp"
    agent_id = 123456


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
    assert fake_client.detail_calls == Counter(
        {
            "employee-1": 1,
            "employee-2": 1,
            "employee-3": 1,
            "employee-4": 1,
        }
    )


@pytest.mark.asyncio
@respx.mock
async def test_probe_uses_authoritative_detail_for_identity_and_hrm_for_gender() -> None:
    respx.post(f"{API}/v1.0/oauth2/accessToken").mock(
        return_value=httpx.Response(
            200, json={"accessToken": "provider-token", "expireIn": 7200}
        )
    )
    departments = respx.post(f"{OAPI}/topapi/v2/department/listsub").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "errcode": 0,
                    "errmsg": "ok",
                    "result": [{"dept_id": 2, "parent_id": 1, "name": "Team"}],
                },
            ),
            httpx.Response(200, json={"errcode": 0, "errmsg": "ok", "result": []}),
        ]
    )
    list_payload = {
        "userid": "employee-1",
        "unionid": "union-1",
        "name": "Employee",
        "active": True,
        "dept_id_list": [1, 2],
    }
    member_lists = respx.post(f"{OAPI}/topapi/v2/user/list").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "errcode": 0,
                    "errmsg": "ok",
                    "result": {
                        "has_more": False,
                        "next_cursor": 0,
                        "list": [list_payload],
                    },
                },
            ),
            httpx.Response(
                200,
                json={
                    "errcode": 0,
                    "errmsg": "ok",
                    "result": {
                        "has_more": False,
                        "next_cursor": 0,
                        "list": [list_payload],
                    },
                },
            ),
        ]
    )
    details = respx.post(f"{OAPI}/topapi/v2/user/get").mock(
        return_value=httpx.Response(
            200,
            json={
                "errcode": 0,
                "errmsg": "ok",
                "result": {
                    **list_payload,
                    "extension": {"性别": "男"},
                },
            },
        )
    )
    metadata = respx.post(f"{OAPI}/topapi/smartwork/hrm/roster/meta/get").mock(
        return_value=httpx.Response(200, json={
            "errcode": 0,
            "errmsg": "ok",
            "result": [{
                "field_meta_info_list": [{
                    "field_code": "sys00-gender",
                    "field_name": "性别",
                    "field_type": "DDSelectField",
                    "option_text": json.dumps([
                        {"label": "男", "value": "0"},
                        {"label": "女", "value": "1"},
                    ]),
                }],
            }],
        })
    )
    roster = respx.post(f"{OAPI}/topapi/smartwork/hrm/employee/v2/list").mock(
        return_value=httpx.Response(200, json={
            "errcode": 0,
            "errmsg": "ok",
            "result": [{
                "userid": "employee-1",
                "field_data_list": [{
                    "field_code": "sys00-gender",
                    "field_name": "性别",
                    "field_value_list": [{"item_index": 0, "label": "女", "value": "1"}],
                }],
            }],
        })
    )
    client = DingTalkClient(
        app_key="test-app-key",
        app_secret="test-app-secret",
        corp_id="test-corp",
        agent_id=123456,
        login_flow="in_client",
        api_base_url=API,
        oapi_base_url=OAPI,
    )

    coverage = await collect_gender_coverage(client)

    assert coverage.active_employee_count == coverage.valid_count == 1
    assert coverage.ready is True
    assert departments.call_count == 2
    assert member_lists.call_count == 2
    assert details.call_count == 1
    assert metadata.call_count == 1
    assert roster.call_count == 1
    assert all(
        "extension" not in json.loads(call.request.content)
        for call in member_lists.calls
    )
    assert json.loads(details.calls[0].request.content)["userid"] == "employee-1"
    assert json.loads(roster.calls[0].request.content) == {
        "agentid": 123456,
        "userid_list": "employee-1",
        "field_filter_list": "sys00-gender",
    }


@pytest.mark.asyncio
async def test_probe_fails_closed_when_authoritative_detail_conflicts_with_list() -> None:
    class ConflictingDetailClient(FakeDingTalkClient):
        async def get_member(self, userid: str) -> DingTalkMember:
            member = await super().get_member(userid)
            if userid == "employee-1":
                return DingTalkMember(
                    member.userid,
                    "different-union",
                    member.display_name,
                    member.active,
                    member.department_ids,
                    member.gender,
                    member.gender_attribute_status,
                )
            return member

    with pytest.raises(GenderProbeError, match="member_conflict"):
        await collect_gender_coverage(ConflictingDetailClient())


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
