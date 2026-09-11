"""AB review regressions: durable identity, conservative metering and fail closed."""

from uuid import uuid4

import pytest
from app.hr_agent.repository import HrAgentRepository
from app.hr_agent.types import HrAgentProblem, ModelReply, Usage
from tests import test_hr_agent_repository as support

repo = support.repo
database = support.database


def test_uuid_case_retry_returns_same_work(repo):
    owner, key = uuid4(), str(uuid4())
    first = repo.submit(owner, support.request(), key.upper())
    replay = repo.submit(owner, support.request(), key.lower())
    assert replay["work_id"] == first["work_id"]
    with repo.transaction() as c:
        c.execute(
            "SELECT count(*) AS n FROM platform_hr_agent.works WHERE owner_id=%s",
            (owner,),
        )
        assert c.fetchone()["n"] == 1


def test_zero_provider_usage_cannot_refund_input_estimate(repo):
    owner = uuid4()
    work = repo.submit(owner, support.request(), uuid4())
    fence = repo.claim("meter-review", 60)
    attempt = repo.prepare_model(fence, support.model_context(tokens=700))
    repo.mark_model_sending(fence, attempt.attempt_id)
    repo.commit_model(
        fence,
        attempt.attempt_id,
        ModelReply("answer", (), "stop", Usage(None, 0, 0, "reported")),
    )
    view = repo.get_work(owner, work["work_id"])
    assert view["budget"]["charged_tokens"] >= 700
    with repo.transaction() as c:
        c.execute(
            "SELECT usage_quality FROM platform_hr_agent.model_attempts WHERE attempt_id=%s",
            (attempt.attempt_id,),
        )
        assert c.fetchone()["usage_quality"] == "estimated"


def test_missing_scope_validator_rejects_empty_scope(repo):
    unconfigured = HrAgentRepository(repo.connection_factory, repo.codec)
    with pytest.raises(HrAgentProblem) as caught:
        unconfigured._scope(uuid4(), [], [])
    assert caught.value.http_status == 503


def test_standard_conflict_is_validated_on_emission(monkeypatch):
    from app.hr_agent import standards

    called = []
    validate = standards.validate_contract

    def observe(name, value):
        called.append(name)
        return validate(name, value)

    monkeypatch.setattr(standards, "validate_contract", observe)
    revision = uuid4()
    with pytest.raises(HrAgentProblem) as caught:
        standards.StandardService._conflict("standard_revision", revision)
    assert "ConfirmError" in called
    assert caught.value.problem["details"]["current_revision"] == str(revision)


def test_tool_error_inherits_frozen_input_objects(repo):
    from app.hr_agent.types import ToolCall, problem

    owner = uuid4()
    objects = [{"kind": "candidate", "id": str(uuid4())}]
    work = repo.submit(owner, support.request(objects=objects), uuid4())
    fence = repo.claim("tool-review", 60)
    attempt = repo.prepare_model(fence, support.model_context())
    repo.mark_model_sending(fence, attempt.attempt_id)
    operation = repo.commit_model(
        fence, attempt.attempt_id, support.reply("", [ToolCall("bad", "ask_user", {})])
    )[0]
    repo.fail_tool(fence, operation, problem("invalid_input"))
    with repo.transaction() as c:
        c.execute(
            "SELECT objects FROM platform_hr_agent.entries WHERE work_id=%s AND kind='tool'",
            (work["work_id"],),
        )
        assert c.fetchone()["objects"] == objects


def test_budget_extension_cannot_exceed_service_ceiling(repo):
    from types import SimpleNamespace

    owner = uuid4()
    work = repo.submit(owner, support.request(), uuid4())
    repo.settings = SimpleNamespace(
        budget_profile={"limits": dict(work["budget"]["limits"])}
    )
    with pytest.raises(HrAgentProblem) as caught:
        repo.extend_budget(
            owner,
            work["work_id"],
            {
                "expected_budget_revision": 1,
                "addition": {"model_calls": 1, "total_tokens": 0, "active_seconds": 0},
                "reason": "over service ceiling",
            },
            uuid4(),
        )
    assert caught.value.http_status == 422
    assert repo.get_work(owner, work["work_id"])["budget"]["revision"] == 1
