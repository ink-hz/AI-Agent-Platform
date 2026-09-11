"""Explicit research timeout remains bounded by profile and persisted work budget."""

import json
from dataclasses import replace

import pytest
from tests.test_hr_agent_runtime import context, database, setup

_FIXTURES = (database, setup)


def test_research_profile_accepts_explicit_300_and_fingerprints_it(tmp_path):
    from hr_agent_support import make_hr_settings

    initial = make_hr_settings(tmp_path / "initial")
    profile = dict(initial.provider_profile, timeout_seconds=300)
    path = tmp_path / "research-profile.json"
    path.write_text(json.dumps(profile))
    path.chmod(0o600)
    research = make_hr_settings(
        tmp_path / "research", PLATFORM_HR_AGENT_PROVIDER_PROFILE_FILE=str(path)
    )
    assert research.provider_profile["timeout_seconds"] == 300
    assert research.configuration_revision != initial.configuration_revision
    profile["timeout_seconds"] = 601
    path.write_text(json.dumps(profile))
    with pytest.raises(ValueError):
        make_hr_settings(
            tmp_path / "invalid", PLATFORM_HR_AGENT_PROVIDER_PROFILE_FILE=str(path)
        )


@pytest.mark.parametrize("remaining,expected", [(900, 300), (20, 20)])
def test_transport_uses_research_profile_and_smaller_work_deadline(
    tmp_path, monkeypatch, remaining, expected
):
    from app.hr_agent import model
    from tests.test_hr_agent_model_io import openai_profile, request, write_credential

    credential = tmp_path / "credential"
    write_credential(credential)
    port = model.ConfiguredHttpModelPort(
        replace(
            openai_profile("http://127.0.0.1:1/v1/chat/completions", credential),
            timeout_seconds=300,
        )
    )
    deadlines = []
    monkeypatch.setattr(model.time, "monotonic", lambda: 10.0)

    def lines(endpoint, headers, body, deadline):
        deadlines.append(deadline)
        yield 'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}'
        yield ""
        yield "data: [DONE]"
        yield ""

    monkeypatch.setattr(model, "_http_lines", lines)
    assert (
        model.collect_reply(port.stream(request(deadline_seconds=remaining))).text
        == "ok"
    )
    assert deadlines == [10 + expected]


def test_repository_refreshes_research_deadline_from_remaining_budget(setup):
    repo, _, _, fence = setup
    repo.settings = replace(
        repo.settings,
        provider_profile={**repo.settings.provider_profile, "timeout_seconds": 300},
    )
    prepared = repo.prepare_model(fence, context(repo, None, fence))
    assert prepared.deadline_seconds == 300
    with repo.transaction() as c:
        work = repo._fence(c, fence)
        budget = repo._unseal("works", work["work_id"], "sealed_budget", work)
        budget["active_seconds"] = 720
        repo._save_budget(c, work, budget)
    refreshed = repo.resume_prepared(fence, prepared.attempt_id)
    assert 0 < refreshed.deadline_seconds <= 180
