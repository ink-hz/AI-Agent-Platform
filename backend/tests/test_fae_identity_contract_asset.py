"""The FAE identity contract asset is executable, frozen, and actually served.

The AI FAE Agent pins this repository's `contracts/fae_identity_v1` subtree by
commit and digest. Everything the Agent is allowed to rely on has to be provable
here: the checked-in schema, the checked-in fixtures, the digest algorithm, and
the live private back-channel responses.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import tomllib
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts" / "fae_identity_v1"
PROJECT = CONTRACT / "pyproject.toml"
SCHEMA = CONTRACT / "schema" / "fae-identity-v1.schema.json"
FIXTURES = CONTRACT / "fixtures"
CONTRACT_VERSION = "orbbec-fae-identity/v1"
EXCHANGE_FIELDS = frozenset(
    {
        "contract_version",
        "subject_id",
        "subject_type",
        "internal_user_id",
        "identity_binding_id",
        "agent_id",
        "display_name",
        "partner_display_name",
    }
)
VALIDATE_FIELDS = EXCHANGE_FIELDS | {"active"}

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
LAUNCH_CODE = "contract-frozen-launch-code-000000000"
ENTERPRISE_SUBJECT = UUID("3f0d5c62-9b1e-4a7d-8c15-2ad4e6f70b11")
ENTERPRISE_BINDING = UUID("7c4e1b90-52a8-4d6f-9e03-1b8d5a24c7f6")
ENTERPRISE_SESSION = UUID("6d2f8a41-0c73-4e59-b1d8-9f45a3e62c07")
PARTNER_SUBJECT = UUID("a1d93f47-6c28-4b05-9e71-3f8c50d2a9b4")
PARTNER_BINDING = UUID("0b62e8d5-1f34-4a97-8d20-6c5b9e1743af")


def _contract_module():
    sys.path.insert(0, str(CONTRACT))
    try:
        import orbbec_fae_identity_contract as module
    finally:
        sys.path.pop(0)
    return module


def _launch_module():
    sys.path.insert(0, str(ROOT / "backend"))
    try:
        from app.control_plane import agent_launch
    finally:
        sys.path.pop(0)
    return agent_launch


class _FakeSecrets:
    key_version = 3

    @staticmethod
    def random_token() -> str:
        return LAUNCH_CODE

    @staticmethod
    def digest(purpose: str, value: str) -> bytes:
        assert purpose == "agent-launch"
        assert value == LAUNCH_CODE
        return b"d" * 32


class _FakeAuthorization:
    def decide_for_user_id(self, internal_user_id: UUID, agent_id: str):
        from app.agent_brain.authorization import AgentUseDecision

        assert agent_id == "ai-fae-agent"
        return AgentUseDecision(True, (), uuid4())


class _FakeRepository:
    """One frozen launch row, shaped exactly like the v57 control functions."""

    def __init__(
        self,
        *,
        subject_id: UUID,
        subject_type: str,
        binding_id: UUID,
        internal_user_id: UUID | None,
        display_name: str | None,
    ) -> None:
        self._row = (
            subject_id,
            subject_type,
            binding_id,
            "ai-fae-agent",
            internal_user_id,
            display_name,
        )
        self.issued: list[dict[str, object]] = []

    def issue(self, **values) -> datetime:
        self.issued.append(values)
        return NOW + timedelta(seconds=60)

    def exchange(self, **_values):
        return self._row

    def validate_binding(self, **_values):
        return (*self._row, True)

    def revoke_binding(self, **_values) -> None:
        raise AssertionError("contract conformance must not revoke a live binding")


class _FakePartnerService:
    def __init__(self, *, display_name: str, partner_display_name: str) -> None:
        self._display_name = display_name
        self._partner_display_name = partner_display_name

    async def require_active_fae_subject(self, subject_id: UUID, provider):
        assert provider is not None
        return SimpleNamespace(
            subject_id=subject_id,
            display_name=self._display_name,
            partner_display_name=self._partner_display_name,
        )


def _client(repository, partner_service=None) -> TestClient:
    agent_launch = _launch_module()
    from app.control_plane.models import AuthContext, Role

    service = agent_launch.AgentLaunchService(
        repository=repository,
        secrets=_FakeSecrets(),
        authorization=_FakeAuthorization(),
        partner_service=partner_service,
        partner_provider=SimpleNamespace(kind="fixture"),
        clock=lambda: NOW,
    )
    app = FastAPI()

    @app.middleware("http")
    async def identity(request: Request, call_next):
        request.state.auth_context = AuthContext(
            ENTERPRISE_SUBJECT, Role.MEMBER, ENTERPRISE_SESSION, False
        )
        return await call_next(request)

    app.include_router(agent_launch.build_agent_launch_router(service))
    return TestClient(app, client=("127.0.0.1", 52100))


def _enterprise_repository(display_name: str | None = "Contract Example Member"):
    return _FakeRepository(
        subject_id=ENTERPRISE_SUBJECT,
        subject_type="enterprise_member",
        binding_id=ENTERPRISE_BINDING,
        internal_user_id=ENTERPRISE_SUBJECT,
        display_name=display_name,
    )


def _partner_repository():
    return _FakeRepository(
        subject_id=PARTNER_SUBJECT,
        subject_type="partner_operator",
        binding_id=PARTNER_BINDING,
        internal_user_id=None,
        display_name=None,
    )


# --- the asset itself -------------------------------------------------------


def test_contract_asset_declares_python_311_and_pins_its_validator() -> None:
    project = tomllib.loads(PROJECT.read_text("utf-8"))

    assert project["project"]["name"] == "orbbec-fae-identity-contract"
    assert project["project"]["requires-python"] == ">=3.11"
    assert "jsonschema==4.26.0" in project["project"]["dependencies"]
    assert project["tool"]["setuptools"]["packages"] == ["orbbec_fae_identity_contract"]
    assert project["tool"]["pytest"]["ini_options"]["testpaths"] == ["tests"]


def test_backend_requirements_pin_the_contract_validator() -> None:
    requirement = "jsonschema==4.26.0"
    lines = (ROOT / "backend" / "requirements.txt").read_text("utf-8").splitlines()

    assert requirement in lines
    assert [line for line in lines if line.startswith("jsonschema")] == [requirement]


def test_schema_closes_both_back_channel_messages_and_the_public_capability() -> None:
    schema = json.loads(SCHEMA.read_text("utf-8"))
    defs = schema["$defs"]

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert defs["contract_version"]["const"] == CONTRACT_VERSION
    assert defs["agent_id"]["const"] == "ai-fae-agent"
    assert defs["subject_type"]["enum"] == ["enterprise_member", "partner_operator"]
    for name in ("enterprise_exchange", "partner_exchange"):
        assert set(defs[name]["required"]) == EXCHANGE_FIELDS
        assert set(defs[name]["properties"]) == EXCHANGE_FIELDS
        assert defs[name]["additionalProperties"] is False
    for name in ("enterprise_validation", "partner_validation"):
        assert set(defs[name]["required"]) == VALIDATE_FIELDS
        assert set(defs[name]["properties"]) == VALIDATE_FIELDS
        assert defs[name]["additionalProperties"] is False
        assert defs[name]["properties"]["active"]["const"] is True
    # The public capability response carries the Task 9 boolean and nothing else.
    capabilities = defs["capabilities_response"]
    assert capabilities["additionalProperties"] is False
    assert set(capabilities["properties"]) == {"partner_login_available"}
    assert capabilities["properties"]["partner_login_available"]["type"] == "boolean"
    assert "contract_version" not in capabilities["properties"]


@pytest.mark.parametrize("fixture", ["enterprise.json", "partner.json"])
def test_every_fixture_example_validates_against_the_checked_in_schema(
    fixture: str,
) -> None:
    module = _contract_module()
    document = json.loads((FIXTURES / fixture).read_text("utf-8"))

    assert set(document) == {"exchange", "validate", "capabilities"}
    module.validator("contract_document").validate(document)
    module.validator("exchange_response").validate(document["exchange"])
    module.validator("validate_response").validate(document["validate"])
    module.validator("capabilities_response").validate(document["capabilities"])
    module.check_subject_invariants(document["exchange"])
    module.check_subject_invariants(document["validate"])
    assert document["validate"]["active"] is True


def test_enterprise_and_partner_fixtures_pin_the_two_generic_subject_shapes() -> None:
    module = _contract_module()
    enterprise = module.load_fixture("enterprise.json")["exchange"]
    partner = module.load_fixture("partner.json")["exchange"]

    assert set(enterprise) == EXCHANGE_FIELDS
    assert enterprise["contract_version"] == CONTRACT_VERSION
    assert enterprise["subject_type"] == "enterprise_member"
    assert enterprise["subject_id"] == enterprise["internal_user_id"]
    assert enterprise["partner_display_name"] is None
    assert set(partner) == EXCHANGE_FIELDS
    assert partner["subject_type"] == "partner_operator"
    assert partner["internal_user_id"] is None
    assert partner["agent_id"] == "ai-fae-agent"


@pytest.mark.parametrize(
    "mutation",
    [
        {"contract_version": "orbbec-fae-identity/v2"},
        {"agent_id": "another-agent"},
        {"subject_type": "public_customer"},
        {"subject_id": "not-a-uuid"},
        {"department": "R&D"},
        {"role": "member"},
        {"provider_subject": "dingtalk-staff-1"},
        {"csrf_token": "t"},
        {"session_token": "t"},
        {"display_name": "line\nbreak"},
        {"display_name": "n" * 65},
    ],
)
def test_schema_rejects_forbidden_or_malformed_exchange_material(
    mutation: dict,
) -> None:
    from jsonschema import ValidationError

    module = _contract_module()
    payload = {**module.load_fixture("enterprise.json")["exchange"], **mutation}

    with pytest.raises(ValidationError):
        module.validator("exchange_response").validate(payload)


def test_schema_rejects_a_validate_response_that_is_not_active() -> None:
    from jsonschema import ValidationError

    module = _contract_module()
    payload = {**module.load_fixture("partner.json")["validate"], "active": False}

    with pytest.raises(ValidationError):
        module.validator("validate_response").validate(payload)


# --- live conformance -------------------------------------------------------


def test_live_enterprise_responses_conform_to_the_frozen_contract() -> None:
    module = _contract_module()
    client = _client(_enterprise_repository())

    exchange = client.post(
        "/api/v1/internal/agent-launch/exchange", json={"code": LAUNCH_CODE}
    )
    validation = client.post(
        f"/api/v1/internal/agent-bindings/{ENTERPRISE_BINDING}/validate",
        json={"agent_id": "ai-fae-agent"},
    )

    assert exchange.status_code == 200
    assert validation.status_code == 200
    module.validator("exchange_response").validate(exchange.json())
    module.validator("validate_response").validate(validation.json())
    assert exchange.json() == {
        "contract_version": CONTRACT_VERSION,
        "subject_id": str(ENTERPRISE_SUBJECT),
        "subject_type": "enterprise_member",
        "internal_user_id": str(ENTERPRISE_SUBJECT),
        "identity_binding_id": str(ENTERPRISE_BINDING),
        "agent_id": "ai-fae-agent",
        "display_name": "Contract Example Member",
        "partner_display_name": None,
    }
    assert validation.json() == {**exchange.json(), "active": True}
    serialized = (exchange.text + validation.text).lower()
    for forbidden in ("department", "role", "dingtalk", "csrf", "token", "cookie"):
        assert forbidden not in serialized


def test_live_partner_responses_conform_to_the_frozen_contract() -> None:
    module = _contract_module()
    client = _client(
        _partner_repository(),
        _FakePartnerService(
            display_name="Contract Example Operator",
            partner_display_name="Contract Example Partner",
        ),
    )

    exchange = client.post(
        "/api/v1/internal/agent-launch/exchange", json={"code": LAUNCH_CODE}
    )
    validation = client.post(
        f"/api/v1/internal/agent-bindings/{PARTNER_BINDING}/validate",
        json={"agent_id": "ai-fae-agent"},
    )

    assert exchange.status_code == 200
    assert validation.status_code == 200
    module.validator("exchange_response").validate(exchange.json())
    module.validator("validate_response").validate(validation.json())
    assert exchange.json() == {
        "contract_version": CONTRACT_VERSION,
        "subject_id": str(PARTNER_SUBJECT),
        "subject_type": "partner_operator",
        "internal_user_id": None,
        "identity_binding_id": str(PARTNER_BINDING),
        "agent_id": "ai-fae-agent",
        "display_name": "Contract Example Operator",
        "partner_display_name": "Contract Example Partner",
    }
    assert validation.json() == {**exchange.json(), "active": True}


def test_live_responses_project_names_within_the_frozen_safe_bounds() -> None:
    module = _contract_module()
    raw = "  Ctrl\x07Name " + "n" * 80
    client = _client(_enterprise_repository(raw))

    exchange = client.post(
        "/api/v1/internal/agent-launch/exchange", json={"code": LAUNCH_CODE}
    )

    payload = exchange.json()
    module.validator("exchange_response").validate(payload)
    assert payload["display_name"] == ("CtrlName " + "n" * 80)[:64]
    assert len(payload["display_name"]) == 64
    assert "\x07" not in exchange.text
    assert "\\u0007" not in exchange.text


def test_live_responses_drop_a_name_that_projects_to_nothing() -> None:
    module = _contract_module()
    client = _client(_enterprise_repository("    "))

    payload = client.post(
        "/api/v1/internal/agent-launch/exchange", json={"code": LAUNCH_CODE}
    ).json()

    module.validator("exchange_response").validate(payload)
    assert payload["display_name"] is None


def test_display_projections_never_leave_the_two_back_channel_responses(
    caplog,
) -> None:
    repository = _enterprise_repository()
    client = _client(repository)

    with caplog.at_level(logging.DEBUG):
        issue = client.post("/api/v1/agents/ai-fae-agent/launch")
        client.post(
            "/api/v1/internal/agent-launch/exchange", json={"code": LAUNCH_CODE}
        )
        client.post(
            f"/api/v1/internal/agent-bindings/{ENTERPRISE_BINDING}/validate",
            json={"agent_id": "ai-fae-agent"},
        )

    assert issue.status_code == 200
    assert set(issue.json()) == {"launch_url", "expires_at"}
    # The launch code and the redirect URL carry no projection at all.
    assert "Contract Example Member" not in issue.text
    assert "Contract Example Member" not in LAUNCH_CODE
    assert issue.json()["launch_url"].startswith(
        "https://fae.orbbec.com.cn/app/#platform_launch="
    )
    # The persisted launch/binding row is an id-only audit projection.
    persisted = json.dumps(repository.issued[0], default=str)
    assert "Contract Example Member" not in persisted
    assert "display_name" not in persisted
    # Nothing the request logs (the access-log projection) carries a name.
    for record in caplog.records:
        assert "Contract Example Member" not in record.getMessage()


# --- frozen digest ----------------------------------------------------------


def _seed_contract(root: Path) -> None:
    (root / "schema").mkdir(parents=True)
    (root / "fixtures" / "nested").mkdir(parents=True)
    (root / "schema" / "s.json").write_bytes(b"S")
    (root / "fixtures" / "a.json").write_bytes(b"A")
    (root / "fixtures" / "nested" / "b.json").write_bytes(b"B")


def test_contract_digest_is_a_frozen_path_and_bytes_chain(tmp_path: Path) -> None:
    module = _contract_module()
    _seed_contract(tmp_path)

    expected = sha256(
        b"fixtures/a.json\0A\0fixtures/nested/b.json\0B\0schema/s.json\0S\0"
    ).hexdigest()

    assert module.contract_digest(tmp_path) == expected
    assert module.DIGEST_DIRECTORIES == ("fixtures", "schema")


def test_contract_digest_covers_only_the_schema_and_fixture_bytes(
    tmp_path: Path,
) -> None:
    module = _contract_module()
    _seed_contract(tmp_path)
    pinned = module.contract_digest(tmp_path)

    (tmp_path / "pyproject.toml").write_text("[project]\n", "utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_contract.py").write_text("def test(): ...\n", "utf-8")
    (tmp_path / "orbbec_fae_identity_contract").mkdir()
    (tmp_path / "orbbec_fae_identity_contract" / "__init__.py").write_text("", "utf-8")

    assert module.contract_digest(tmp_path) == pinned

    (tmp_path / "fixtures" / "a.json").write_bytes(b"A2")

    assert module.contract_digest(tmp_path) != pinned


def test_contract_digest_requires_both_frozen_directories(tmp_path: Path) -> None:
    module = _contract_module()
    (tmp_path / "schema").mkdir()

    with pytest.raises(ValueError, match="fae_identity_contract_incomplete"):
        module.contract_digest(tmp_path)


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def test_committed_contract_digest_survives_later_unrelated_commits(
    tmp_path: Path,
) -> None:
    module = _contract_module()
    repository = tmp_path / "platform"
    source = repository / "contracts" / "fae_identity_v1"
    _seed_contract(source)
    _git(repository.parent, "init", "--quiet", str(repository))
    _git(repository, "config", "user.email", "contract@example.invalid")
    _git(repository, "config", "user.name", "Contract Fixture")
    _git(repository, "add", "--all")
    _git(repository, "commit", "--quiet", "-m", "freeze contract")
    commit = _git(repository, "rev-parse", "HEAD")
    pinned = module.contract_digest(source)

    assert module.archive_digest(repository, commit) == pinned

    (repository / "README.md").write_text("unrelated\n", "utf-8")
    _git(repository, "add", "--all")
    _git(repository, "commit", "--quiet", "-m", "unrelated change")

    assert module.archive_digest(repository, commit) == pinned
    assert module.contract_digest(source) == pinned

    (source / "schema" / "s.json").write_bytes(b"S2")

    assert module.contract_digest(source) != module.archive_digest(repository, commit)


def test_archive_digest_rejects_a_commit_without_the_contract(tmp_path: Path) -> None:
    module = _contract_module()
    repository = tmp_path / "platform"
    repository.mkdir()
    (repository / "README.md").write_text("no contract\n", "utf-8")
    _git(repository.parent, "init", "--quiet", str(repository))
    _git(repository, "config", "user.email", "contract@example.invalid")
    _git(repository, "config", "user.name", "Contract Fixture")
    _git(repository, "add", "--all")
    _git(repository, "commit", "--quiet", "-m", "no contract")
    commit = _git(repository, "rev-parse", "HEAD")

    with pytest.raises(ValueError, match="fae_identity_contract_missing_at_commit"):
        module.archive_digest(repository, commit)
