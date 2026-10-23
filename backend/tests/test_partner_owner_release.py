from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.control_plane.partner_release import (
    evaluate_partner_release,
    validate_partner_release,
)

NOW = datetime(2026, 10, 23, 4, 0, tzinfo=UTC)


def document(**changes):
    result = {
        "contract_version": "orbbec-fae-partner-provider/v2",
        "provider_kind": "dingtalk_external",
        "release_basis": "project_owner_authorized",
        "authorized_at": "2026-10-23T03:00:00Z",
        "evidence_sha256": "a" * 64,
        "real_account_acceptance": "pending",
    }
    result.update(changes)
    return result


def config(tmp_path, body=None):
    path = tmp_path / "owner-release.json"
    path.write_text(json.dumps(body if body is not None else document()))
    path.chmod(0o600)
    return SimpleNamespace(
        environment="production", partner_identity_enabled=True,
        partner_provider_kind="dingtalk_external",
        partner_provider_release_file=str(path),
        partner_provider_release_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def test_owner_rollout_is_authorized_without_claiming_real_account_acceptance(tmp_path):
    settings = config(tmp_path)
    result = validate_partner_release(settings, now=NOW)
    assert result.provider_kind == "dingtalk_external"
    assert result.dev_real_account_tested_at is None
    assert result.authorized_at == datetime(2026, 10, 23, 3, 0, tzinfo=UTC)
    assert result.release_basis == "project_owner_authorized"
    assert result.real_account_acceptance == "pending"
    status = evaluate_partner_release(settings, now=NOW)
    assert status.partner_login_available
    assert status.reason == "partner_release_owner_authorized"


@pytest.mark.parametrize("changes", [
    {"release_basis": "automatic"},
    {"real_account_acceptance": "verified"},
    {"real_account_acceptance": True},
    {"provider_kind": "reference"},
    {"provider_kind": "other-provider"},
    {"stable_subject_verified": True},
    {"two_distinct_subjects_verified": True},
    {"dev_real_account_tested_at": "2026-10-23T03:00:00Z"},
    {"unknown": "value"},
    {"evidence_sha256": "invalid"},
    {"authorized_at": "2026-10-23T03:00:00"},
    {"authorized_at": "2026-02-30T03:00:00Z"},
    {"authorized_at": "2026-10-24T03:00:00Z"},
    {"authorized_at": (NOW-timedelta(days=181)).isoformat()},
])
def test_owner_release_rejects_invented_acceptance_and_malformed_or_stale_authorization(tmp_path, changes):
    with pytest.raises(ValueError):
        validate_partner_release(config(tmp_path, document(**changes)), now=NOW)


@pytest.mark.parametrize("field", list(document()))
def test_owner_release_requires_complete_closed_document(tmp_path, field):
    body = document()
    del body[field]
    with pytest.raises(ValueError):
        validate_partner_release(config(tmp_path, body), now=NOW)


@pytest.mark.parametrize("mutation", ["digest", "permissions", "disabled", "environment", "kind", "symlink"])
def test_owner_release_retains_existing_security_checks(tmp_path, mutation):
    settings = config(tmp_path)
    path = Path(settings.partner_provider_release_file)
    if mutation == "digest":
        settings.partner_provider_release_sha256 = "b" * 64
    elif mutation == "permissions":
        path.chmod(0o644)
    elif mutation == "disabled":
        settings.partner_identity_enabled = False
    elif mutation == "environment":
        settings.environment = "development"
    elif mutation == "kind":
        settings.partner_provider_kind = "reference"
    elif mutation == "symlink":
        link = tmp_path / "symlink.json"
        link.symlink_to(path)
        settings.partner_provider_release_file = str(link)
    with pytest.raises(ValueError):
        validate_partner_release(settings, now=NOW)


def test_owner_release_schema_has_separate_closed_authorization_contract():
    path = Path(__file__).resolve().parents[2] / "deploy/cloud/fae-partner-provider.owner-release.schema.json"
    schema = json.loads(path.read_text())
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(document())
    assert schema["properties"]["contract_version"]["const"] == "orbbec-fae-partner-provider/v2"
    assert schema["properties"]["real_account_acceptance"]["const"] == "pending"
