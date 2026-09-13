import hashlib
import json

import pytest
from app.hr_agent.config import load_hr_agent_settings
from app.hr_agent.types import canonical_json
from tests.test_hr_agent_preflight import _environment, _write_json
from tools.hr_agent.preflight import build_report


def configured_policy(tmp_path):
    env = _environment(tmp_path / "runtime")
    settings = load_hr_agent_settings(env)
    profiles = {"provider": settings.provider_profile, "budget": settings.budget_profile,
                "diagnostic": settings.diagnostic_profile}
    policy = {"version": 1, "scope": "public-only",
              "authorization_ref": "synthetic-operator-review",
              "configuration_sha256": hashlib.sha256(canonical_json(profiles).encode()).hexdigest(),
              "usage_accounting": "conservative_estimate_not_invoice"}
    path = _write_json(tmp_path / "release-policy.json", policy)
    env["PLATFORM_HR_AGENT_RELEASE_POLICY_FILE"] = str(path)
    return env, path, policy


def test_explicit_policy_binds_loaded_configuration_and_scope(tmp_path):
    env, _, policy = configured_policy(tmp_path)
    loaded = load_hr_agent_settings(env)
    assert loaded.release_policy == policy
    report = build_report(env, env, launch_scope="public-only")
    assert report["limitations"]["d7_product_approved"] is True
    assert "d7_product_approval_absent" not in report["blockers"]
    assert report["ok"] is False  # no database check, no production certification
    assert report["full_candidate_ready"] is False
    full = build_report(env, env, launch_scope="full-candidate")
    assert "personal_processing_authorizer_absent" in full["blockers"]


@pytest.mark.parametrize("mutation", ["budget", "provider", "diagnostic", "mode", "scope", "empty_ref"])
def test_policy_cannot_outlive_reviewed_profiles_or_enable_personal_data(tmp_path, mutation):
    env, path, policy = configured_policy(tmp_path)
    if mutation in {"budget", "provider", "diagnostic"}:
        target = env[f"PLATFORM_HR_AGENT_{mutation.upper()}_PROFILE_FILE"]
        data = json.loads(__import__("pathlib").Path(target).read_text())
        if mutation == "budget":
            data["limits"]["model_calls"] += 1
        elif mutation == "provider":
            data["model"] = "another-model"
        else:
            data["review_revision"] = "different"
        _write_json(__import__("pathlib").Path(target), data)
    elif mutation == "mode":
        path.chmod(0o644)
    else:
        policy["scope" if mutation == "scope" else "authorization_ref"] = (
            "full-candidate" if mutation == "scope" else ""
        )
        _write_json(path, policy)
    with pytest.raises(ValueError, match="HR configuration invalid"):
        load_hr_agent_settings(env)
