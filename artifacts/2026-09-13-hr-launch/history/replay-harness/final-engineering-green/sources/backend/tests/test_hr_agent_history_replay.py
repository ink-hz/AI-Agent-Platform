"""Historical-intent adaptations: engineering default; real model requires opt-in."""

import importlib.util
import json
from itertools import pairwise
from pathlib import Path

import pytest

CORPUS = Path(__file__).parents[2] / "artifacts/2026-09-13-hr-launch/history/replay-v2"


def harness():
    assert importlib.util.find_spec("tests.helpers.hr_history_replay") is not None, (
        "history replay harness missing"
    )
    from tests.helpers import hr_history_replay

    return hr_history_replay


def test_manifest_bytes_are_required_before_replay(tmp_path):
    h = harness()
    (tmp_path / "context.txt").write_text("changed")
    with pytest.raises(ValueError, match="asset integrity"):
        h.asset_bytes(
            tmp_path,
            {
                "assets": [
                    {
                        "path": "context.txt",
                        "sha256": "0" * 64,
                        "version": "synthetic-v2.1",
                    }
                ]
            },
            "context.txt",
        )


def test_missing_previous_result_never_becomes_fabricated_reference():
    h = harness()
    with pytest.raises(ValueError, match="previous result absent"):
        h.previous_refs(
            {"depends_on_turn": 1, "prior_result_binding": "exact"},
            {1: {"state": "completed", "result_refs": []}},
        )


def test_real_run_requires_separate_explicit_profile_and_evidence(tmp_path):
    h = harness()
    with pytest.raises(ValueError, match="explicit real replay"):
        h.real_configuration({"HR_HISTORY_REAL_MODEL": "1"})


@pytest.mark.postgres
@pytest.mark.parametrize("case_id", ["H01", "H03", "H06", "H13"])
def test_scripted_replay_retains_http_identity_material_and_result_boundaries(
    tmp_path, case_id
):
    import os

    evidence = (
        Path(os.environ["HR_HISTORY_SCRIPT_EVIDENCE_DIR"]) / case_id
        if os.getenv("HR_HISTORY_SCRIPT_EVIDENCE_DIR")
        else tmp_path / "evidence"
    )
    report = harness().replay_case(CORPUS, case_id, tmp_path / "runtime", evidence)
    assert report["status"] == "completed", report
    turns = report["turns"]
    assert turns[0]["request"]["thread_id"] is None
    assert all(
        turn["http"]["submit"] == (201 if index == 0 else 202)
        and turn["http"]["replay"] == (200 if index == 0 else 202)
        for index, turn in enumerate(turns)
    )
    assert len({turn["persisted"]["work"]["thread_id"] for turn in turns}) == 1
    assert len({turn["persisted"]["work"]["work_id"] for turn in turns}) == 1
    assert [turn["persisted"]["work"]["input_revision"] for turn in turns] == list(
        range(1, len(turns) + 1)
    )
    if case_id == "H01":
        assert not turns[0]["persisted"]["work"]["result_refs"]
        assert not turns[1]["persisted"]["work"]["result_refs"]
        assert {item["kind"] for item in turns[-1]["persisted"]["results"]} >= {
            "jd",
            "requirements",
        }
    else:
        assert all(turn["persisted"]["work"]["result_refs"] for turn in turns)
    for previous, turn in pairwise(turns):
        assert all(
            ref in turn["request"]["references"]
            for ref in previous["persisted"]["work"]["result_refs"]
        )
    if case_id in {"H06", "H13"}:
        assert (
            turns[0]["materials"]
            and turns[0]["materials"][0]["ref"]["kind"] == "material"
        )
    assert report["security"] == {
        "anonymous": 401,
        "csrf": 403,
        "origin": 403,
        "wrong_owner": 404,
    }
    assert (evidence / "report.json").is_file()


def test_shared_real_port_budget_denies_the_97th_call():
    h = harness()
    from types import SimpleNamespace

    from app.hr_agent.model import ModelTransportError

    class Port:
        def stream(self, _request):
            return iter(())

    bounded = h.BoundedPort(Port())
    request = SimpleNamespace(
        attempt_id="test", max_output_tokens=16384, deadline_seconds=300
    )
    for _ in range(96):
        list(bounded.stream(request))
    with pytest.raises(ModelTransportError):
        list(bounded.stream(request))
    assert bounded.calls == 96


@pytest.mark.postgres
def test_opt_in_real_history_replay(tmp_path):
    import os

    if os.getenv("HR_HISTORY_REAL_MODEL") != "1":
        pytest.skip("real configured provider requires explicit HR_HISTORY_* opt-in")
    h = harness()
    profile, output = h.real_configuration(os.environ)
    from app.hr_agent.model import ConfiguredHttpModelPort

    port = h.BoundedPort(ConfiguredHttpModelPort.from_mapping(profile))
    failures = []
    for case_id in ("H01", "H03", "H06", "H13"):
        report = h.replay_case(
            CORPUS,
            case_id,
            tmp_path / case_id,
            output / case_id,
            profile=profile,
            port=port,
        )
        if report["status"] != "completed":
            failures.append(case_id)
    (output / "summary.json").write_text(
        json.dumps(
            {
                "failed_cases": failures,
                "generation_calls": port.calls,
                "observations": port.observations,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    assert not failures, failures


@pytest.mark.postgres
def test_waiting_user_continuation_uses_real_question_and_revision(tmp_path):
    from uuid import uuid4

    from app.hr_agent.runtime import run_work
    from tests.test_hr_agent_runtime import ScriptModel, tool

    h = harness()
    with h.environment(tmp_path / "runtime") as env:
        client, repo = env["client"], env["repo"]
        configuration = client.get("/api/hr/agent/configuration").json()
        response = client.post(
            "/api/hr/agent/works",
            json={
                "thread_id": None,
                "text": "合成材料分轮接收",
                "objects": [],
                "references": [],
                "budget_profile": configuration["budget_profile"],
            },
            headers={**env["headers"], "Idempotency-Key": str(uuid4())},
        )
        assert response.status_code == 201
        work = response.json()
        done = run_work(
            repo,
            ScriptModel(
                [
                    tool(
                        "ask_user",
                        {"question": "请提供下一份合成材料。", "options": []},
                    )
                ]
            ),
            env["resources"],
            repo.claim("owned-waiting", 60),
        )
        assert done["state"] == "waiting_user" and done["pending_question_id"]
        request = {
            "expected_input_revision": done["input_revision"],
            "question_id": None,
            "text": "下一份合成材料",
            "objects": [],
            "references": [],
        }
        endpoint = "/api/hr/agent/works/" + work["work_id"] + "/inputs"
        assert (
            client.post(
                endpoint,
                json=request,
                headers={**env["headers"], "Idempotency-Key": str(uuid4())},
            ).status_code
            == 409
        )
        request["question_id"] = done["pending_question_id"]
        key = str(uuid4())
        response = client.post(
            endpoint, json=request, headers={**env["headers"], "Idempotency-Key": key}
        )
        assert response.status_code == 202 and response.json()["input_revision"] == 2
        assert (
            client.post(
                endpoint,
                json=request,
                headers={**env["headers"], "Idempotency-Key": key},
            ).json()
            == response.json()
        )


@pytest.mark.parametrize("output,seconds", [(16385, 300), (16384, 301)])
def test_real_port_rejects_per_call_limits_before_transport(output, seconds):
    from types import SimpleNamespace

    from app.hr_agent.model import ModelTransportError

    class ForbiddenPort:
        def stream(self, _request):
            pytest.fail("transport invoked outside explicit call limits")

    bounded = harness().BoundedPort(ForbiddenPort())
    with pytest.raises(ModelTransportError):
        list(
            bounded.stream(
                SimpleNamespace(
                    attempt_id="bounded",
                    max_output_tokens=output,
                    deadline_seconds=seconds,
                )
            )
        )
    assert bounded.calls == 0
