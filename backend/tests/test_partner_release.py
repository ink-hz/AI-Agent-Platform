from __future__ import annotations

import importlib
import json
import os
import stat
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from itertools import count
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from app.config import load_config

ROOT = Path(__file__).parents[2]
SCHEMA = ROOT / "deploy" / "cloud" / "fae-partner-provider.release.schema.json"
COMPOSE = ROOT / "deploy" / "cloud" / "compose.yaml"
ACCEPT = ROOT / "deploy" / "cloud" / "accept.sh"
RUNBOOK = ROOT / "docs" / "runbooks" / "fae-partner-provider-probe.md"
CONTRACT_VERSION = "orbbec-fae-partner-provider/v1"
PROVIDER_KIND = "pilot-partner-adapter"
UNRELEASED_KIND = "probe-only-partner-adapter"
ALTERNATE_KIND = "alternate-partner-adapter"
NOW = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)
TESTED_AT = "2026-08-30T11:20:05Z"
EVIDENCE_SHA256 = "3b" + "0" * 62
RELEASE_FIELDS = {
    "contract_version",
    "provider_kind",
    "stable_subject_verified",
    "two_distinct_subjects_verified",
    "active_status_or_local_revocation_verified",
    "shared_password_forbidden",
    "state_and_callback_replay_verified",
    "dev_real_account_tested_at",
    "evidence_sha256",
}
THRESHOLD_FIELDS = (
    "stable_subject_verified",
    "two_distinct_subjects_verified",
    "active_status_or_local_revocation_verified",
    "shared_password_forbidden",
    "state_and_callback_replay_verified",
)
_ABSENT = object()
_UNSET = object()


def _release_module() -> ModuleType:
    try:
        return importlib.import_module("app.control_plane.partner_release")
    except ModuleNotFoundError:
        pytest.fail("partner Provider release gate is not implemented")


def _provider_module() -> ModuleType:
    return importlib.import_module("app.control_plane.partner_provider")


def validate_partner_release(config, **kwargs):
    kwargs.setdefault("now", NOW)
    return _release_module().validate_partner_release(config, **kwargs)


def evaluate_partner_release(config, **kwargs):
    kwargs.setdefault("now", NOW)
    return _release_module().evaluate_partner_release(config, **kwargs)


class _FakeProvider:
    def __init__(self, kind: str) -> None:
        self.kind = kind

    def begin_auth(self, state: str) -> str:  # pragma: no cover - never called
        return "/partner-auth/fixture"

    async def finish_auth(self, callback):  # pragma: no cover - never called
        raise AssertionError("release validation must not authenticate")

    async def check_subject(self, provider_subject: str):  # pragma: no cover
        raise AssertionError("release validation must not authenticate")


@pytest.fixture(autouse=True)
def registered_providers():
    module = _provider_module()
    module.register_partner_provider(
        PROVIDER_KIND,
        lambda: _FakeProvider(PROVIDER_KIND),
        production_release=True,
    )
    module.register_partner_provider(
        UNRELEASED_KIND,
        lambda: _FakeProvider(UNRELEASED_KIND),
        production_release=False,
    )
    module.register_partner_provider(
        ALTERNATE_KIND,
        lambda: _FakeProvider(ALTERNATE_KIND),
        production_release=True,
    )
    try:
        yield
    finally:
        module.unregister_partner_provider(PROVIDER_KIND)
        module.unregister_partner_provider(UNRELEASED_KIND)
        module.unregister_partner_provider(ALTERNATE_KIND)


@pytest.fixture
def signed_release(tmp_path):
    """Write a Provider evidence release file the way the runbook does."""

    serial = count()

    def _signed(*, raw: bytes | None = None, mode: int = 0o600, **overrides):
        document = {
            "contract_version": CONTRACT_VERSION,
            "provider_kind": PROVIDER_KIND,
            "stable_subject_verified": True,
            "two_distinct_subjects_verified": True,
            "active_status_or_local_revocation_verified": True,
            "shared_password_forbidden": True,
            "state_and_callback_replay_verified": True,
            "dev_real_account_tested_at": TESTED_AT,
            "evidence_sha256": EVIDENCE_SHA256,
        }
        for key, value in overrides.items():
            if value is _ABSENT:
                document.pop(key, None)
            else:
                document[key] = value
        body = (
            raw
            if raw is not None
            else json.dumps(document, separators=(",", ":"), sort_keys=True).encode()
        )
        path = tmp_path / f"fae-partner-provider.release.{next(serial)}.json"
        path.write_bytes(body)
        path.chmod(mode)
        return SimpleNamespace(
            path=path,
            document=document,
            body=body,
            sha256=sha256(body).hexdigest(),
        )

    return _signed


@pytest.fixture
def config_factory():
    """Minimal stand-in for the Platform Config fields the gate reads."""

    def _factory(
        *,
        environment: str = "production",
        partner_identity_enabled: bool = True,
        partner_provider_kind: str = PROVIDER_KIND,
        release=_UNSET,
        partner_provider_release=_UNSET,
        release_file: str | None = None,
        release_sha256: str | None = None,
    ):
        selected = release if release is not _UNSET else partner_provider_release
        if selected in (_UNSET, None):
            file_value, sha_value = "", ""
        else:
            file_value, sha_value = str(selected.path), selected.sha256
        if release_file is not None:
            file_value = release_file
        if release_sha256 is not None:
            sha_value = release_sha256
        return SimpleNamespace(
            environment=environment,
            partner_identity_enabled=partner_identity_enabled,
            partner_provider_kind=partner_provider_kind,
            partner_provider_release_file=file_value,
            partner_provider_release_sha256=sha_value,
        )

    return _factory


def test_partner_login_stays_absent_without_signed_release(config_factory) -> None:
    config = config_factory(
        environment="production",
        partner_identity_enabled=True,
        partner_provider_release=None,
    )
    with pytest.raises(ValueError, match="partner_provider_release_required"):
        validate_partner_release(config)


def test_reference_provider_can_never_enable_production(
    config_factory, signed_release
) -> None:
    release = signed_release(provider_kind="reference")
    with pytest.raises(ValueError, match="partner_reference_provider_forbidden"):
        validate_partner_release(
            config_factory(environment="production", release=release)
        )


def test_configured_reference_kind_is_forbidden_even_with_valid_evidence(
    config_factory, signed_release
) -> None:
    release = signed_release()
    with pytest.raises(ValueError, match="partner_reference_provider_forbidden"):
        validate_partner_release(
            config_factory(release=release, partner_provider_kind="reference")
        )


def test_validated_release_enables_only_the_registered_non_reference_provider(
    config_factory, signed_release
) -> None:
    release = signed_release()
    config = config_factory(release=release)

    validated = validate_partner_release(config)
    status = evaluate_partner_release(config)

    assert validated.provider_kind == PROVIDER_KIND
    assert validated.release_sha256 == release.sha256
    assert validated.evidence_sha256 == EVIDENCE_SHA256
    assert status.partner_login_available is True
    assert status.config_valid is True
    assert status.reason == "partner_release_validated"
    assert status.provider_kind == PROVIDER_KIND


def test_default_production_posture_is_disabled_and_valid(config_factory) -> None:
    status = evaluate_partner_release(
        config_factory(
            partner_identity_enabled=False,
            partner_provider_kind="",
            partner_provider_release=None,
        )
    )

    assert status.partner_login_available is False
    assert status.config_valid is True
    assert status.reason == "partner_identity_disabled"
    assert status.provider_kind is None


def test_schema_is_a_closed_contract_for_the_pinned_version() -> None:
    document = json.loads(SCHEMA.read_bytes())

    assert document["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert document["type"] == "object"
    assert document["additionalProperties"] is False
    assert set(document["required"]) == RELEASE_FIELDS
    assert set(document["properties"]) == RELEASE_FIELDS
    assert document["properties"]["contract_version"]["const"] == CONTRACT_VERSION
    assert document["properties"]["evidence_sha256"]["pattern"] == "^[0-9a-f]{64}$"
    assert document["properties"]["provider_kind"]["not"]["const"] == "reference"
    for field in THRESHOLD_FIELDS:
        assert document["properties"][field] == {"type": "boolean", "const": True}
    timestamp = document["properties"]["dev_real_account_tested_at"]
    assert timestamp["type"] == "string"
    assert timestamp["format"] == "date-time"


def test_schema_never_describes_provider_credentials() -> None:
    body = SCHEMA.read_text("utf-8").lower()

    for forbidden in ("secret", "token", "password_value", "app_key", "credential"):
        assert forbidden not in body


@pytest.mark.parametrize(
    "overrides",
    [
        {"unexpected_field": True},
        {"contract_version": _ABSENT},
        {"evidence_sha256": _ABSENT},
        {"dev_real_account_tested_at": _ABSENT},
        {"provider_kind": _ABSENT},
        {"stable_subject_verified": _ABSENT},
    ],
)
def test_release_key_set_must_match_the_closed_schema(
    config_factory, signed_release, overrides
) -> None:
    release = signed_release(**overrides)
    with pytest.raises(ValueError, match="partner_provider_release_malformed"):
        validate_partner_release(config_factory(release=release))


@pytest.mark.parametrize(
    "raw",
    [b"", b"not json", b"[]", b"null", b'{"contract_version": ', b"\x00\x01"],
)
def test_unparseable_release_fails_closed(config_factory, signed_release, raw) -> None:
    release = signed_release(raw=raw)
    with pytest.raises(ValueError, match="partner_provider_release_malformed"):
        validate_partner_release(config_factory(release=release))


@pytest.mark.parametrize(
    "version",
    ["orbbec-fae-partner-provider/v2", "orbbec-fae-partner-provider", "v1", ""],
)
def test_contract_version_pin_fails_closed(
    config_factory, signed_release, version
) -> None:
    release = signed_release(contract_version=version)
    with pytest.raises(ValueError, match="partner_provider_release_contract_mismatch"):
        validate_partner_release(config_factory(release=release))


def test_digest_mismatch_fails_closed(config_factory, signed_release) -> None:
    release = signed_release()
    config = config_factory(release=release)
    release.path.write_bytes(release.body + b"\n")
    release.path.chmod(0o600)

    with pytest.raises(ValueError, match="partner_provider_release_digest_mismatch"):
        validate_partner_release(config)


@pytest.mark.parametrize(
    "pinned",
    ["", "  ", "not-a-digest", "A" * 64, "0" * 63, "0" * 65],
)
def test_missing_or_malformed_digest_pin_fails_closed(
    config_factory, signed_release, pinned
) -> None:
    release = signed_release()
    with pytest.raises(ValueError, match="partner_provider_release_digest_invalid"):
        validate_partner_release(
            config_factory(release=release, release_sha256=pinned)
        )


def test_symlinked_release_fails_closed(config_factory, signed_release) -> None:
    release = signed_release()
    link = release.path.with_name("linked.release.json")
    link.symlink_to(release.path)

    with pytest.raises(ValueError, match="partner_provider_release_insecure"):
        validate_partner_release(
            config_factory(release=release, release_file=str(link))
        )


def test_release_is_read_from_one_no_follow_file_descriptor(
    config_factory, signed_release, monkeypatch
) -> None:
    release = signed_release()
    real_open = os.open
    observed_flags: list[int] = []

    def audited_open(path, flags, *args, **kwargs):
        observed_flags.append(flags)
        return real_open(path, flags, *args, **kwargs)

    def forbidden_path_read(*_args, **_kwargs):
        raise AssertionError("release validation must not reopen the checked path")

    monkeypatch.setattr(os, "open", audited_open)
    monkeypatch.setattr(Path, "read_bytes", forbidden_path_read)

    assert validate_partner_release(config_factory(release=release))
    assert observed_flags
    assert observed_flags[0] & os.O_NOFOLLOW


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o660, 0o606, 0o700, 0o777])
def test_over_permissive_release_mode_fails_closed(
    config_factory, signed_release, mode
) -> None:
    release = signed_release(mode=mode)
    with pytest.raises(ValueError, match="partner_provider_release_insecure"):
        validate_partner_release(config_factory(release=release))


def test_release_owned_by_another_user_fails_closed(
    config_factory, signed_release, monkeypatch
) -> None:
    release = signed_release()
    other_uid = os.getuid() + 4242
    monkeypatch.setattr(_release_module().os, "getuid", lambda: other_uid)

    with pytest.raises(ValueError, match="partner_provider_release_insecure"):
        validate_partner_release(config_factory(release=release))


def test_release_owned_by_root_is_accepted(
    config_factory, signed_release, monkeypatch
) -> None:
    release = signed_release()
    metadata = release.path.lstat()
    other_uid = metadata.st_uid + 4242
    monkeypatch.setattr(_release_module().os, "getuid", lambda: other_uid)
    monkeypatch.setattr(
        _release_module(),
        "_release_stat",
        lambda path: SimpleNamespace(
            st_mode=metadata.st_mode, st_uid=0, st_size=metadata.st_size
        ),
    )

    assert validate_partner_release(config_factory(release=release))


def test_non_regular_release_fails_closed(config_factory, signed_release) -> None:
    release = signed_release()
    directory = release.path.parent / "release-directory"
    directory.mkdir(mode=0o700)

    with pytest.raises(ValueError, match="partner_provider_release_insecure"):
        validate_partner_release(
            config_factory(release=release, release_file=str(directory))
        )


@pytest.mark.parametrize(
    "path", ["relative/release.json", "", "  ", "release.json\n"]
)
def test_non_absolute_release_path_fails_closed(
    config_factory, signed_release, path
) -> None:
    release = signed_release()
    with pytest.raises(
        ValueError,
        match="partner_provider_release_(required|insecure)",
    ):
        validate_partner_release(
            config_factory(release=release, release_file=path)
        )


def test_unreadable_release_fails_closed(config_factory, signed_release) -> None:
    release = signed_release()
    missing = release.path.with_name("absent.release.json")

    with pytest.raises(ValueError, match="partner_provider_release_insecure"):
        validate_partner_release(
            config_factory(release=release, release_file=str(missing))
        )


def test_oversized_release_fails_closed(config_factory, signed_release) -> None:
    padding = " " * 8192
    release = signed_release(raw=json.dumps({"padding": padding}).encode())

    with pytest.raises(ValueError, match="partner_provider_release_insecure"):
        validate_partner_release(config_factory(release=release))


def test_unregistered_provider_fails_closed(config_factory, signed_release) -> None:
    release = signed_release(provider_kind="never-registered-adapter")
    with pytest.raises(ValueError, match="partner_provider_not_registered"):
        validate_partner_release(
            config_factory(
                release=release, partner_provider_kind="never-registered-adapter"
            )
        )


def test_provider_without_registered_release_fails_closed(
    config_factory, signed_release
) -> None:
    release = signed_release(provider_kind=UNRELEASED_KIND)
    with pytest.raises(ValueError, match="partner_provider_release_not_registered"):
        validate_partner_release(
            config_factory(release=release, partner_provider_kind=UNRELEASED_KIND)
        )


def test_provider_kind_mismatch_fails_closed(config_factory, signed_release) -> None:
    release = signed_release(provider_kind=UNRELEASED_KIND)
    with pytest.raises(ValueError, match="partner_provider_kind_mismatch"):
        validate_partner_release(config_factory(release=release))


@pytest.mark.parametrize(
    "kind", ["", "Reference", "reference ", "REGISTERED", "provider/kind", "-adapter"]
)
def test_malformed_release_provider_kind_fails_closed(
    config_factory, signed_release, kind
) -> None:
    release = signed_release(provider_kind=kind)
    with pytest.raises(ValueError, match="partner_provider_release_malformed"):
        validate_partner_release(config_factory(release=release))


def test_missing_configured_provider_kind_fails_closed(
    config_factory, signed_release
) -> None:
    release = signed_release()
    with pytest.raises(ValueError, match="partner_provider_kind_required"):
        validate_partner_release(
            config_factory(release=release, partner_provider_kind="")
        )


@pytest.mark.parametrize("field", THRESHOLD_FIELDS)
@pytest.mark.parametrize("value", [False, 1, "true", None, "yes"])
def test_every_provider_threshold_must_be_verified(
    config_factory, signed_release, field, value
) -> None:
    release = signed_release(**{field: value})
    with pytest.raises(
        ValueError,
        match="partner_provider_release_(threshold_unmet|malformed)",
    ):
        validate_partner_release(config_factory(release=release))


@pytest.mark.parametrize(
    "tested_at",
    [
        "2026-02-01T00:00:00Z",
        "2020-08-30T11:20:05Z",
        "2026-09-01T09:00:06Z",
        "2027-01-01T00:00:00Z",
    ],
)
def test_stale_or_future_evidence_fails_closed(
    config_factory, signed_release, tested_at
) -> None:
    release = signed_release(dev_real_account_tested_at=tested_at)
    with pytest.raises(ValueError, match="partner_provider_release_stale"):
        validate_partner_release(config_factory(release=release))


@pytest.mark.parametrize(
    "tested_at",
    [
        "2026-08-30T11:20:05",
        "2026-08-30 11:20:05Z",
        "2026-08-30",
        "not-a-timestamp",
        "2026-13-30T11:20:05Z",
        "",
        1756550405,
    ],
)
def test_malformed_evidence_timestamp_fails_closed(
    config_factory, signed_release, tested_at
) -> None:
    release = signed_release(dev_real_account_tested_at=tested_at)
    with pytest.raises(ValueError, match="partner_provider_release_malformed"):
        validate_partner_release(config_factory(release=release))


@pytest.mark.parametrize(
    "digest", ["", "not-a-digest", "A" * 64, "0" * 63, True, None]
)
def test_malformed_evidence_digest_fails_closed(
    config_factory, signed_release, digest
) -> None:
    release = signed_release(evidence_sha256=digest)
    with pytest.raises(ValueError, match="partner_provider_release_malformed"):
        validate_partner_release(config_factory(release=release))


def test_recent_evidence_within_the_window_is_accepted(
    config_factory, signed_release
) -> None:
    tested_at = (NOW - timedelta(days=179)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    release = signed_release(dev_real_account_tested_at=tested_at)

    assert validate_partner_release(config_factory(release=release))


@pytest.mark.parametrize("environment", ["development", "test"])
def test_non_production_environment_never_enables_production_login(
    config_factory, signed_release, environment
) -> None:
    release = signed_release()
    config = config_factory(environment=environment, release=release)

    with pytest.raises(ValueError, match="partner_release_environment_mismatch"):
        validate_partner_release(config)
    status = evaluate_partner_release(config)
    assert status.partner_login_available is False
    assert status.config_valid is True


@pytest.mark.parametrize("environment", ["", "prod", "Production", "staging"])
def test_unknown_environment_fails_closed(
    config_factory, signed_release, environment
) -> None:
    release = signed_release()
    config = config_factory(environment=environment, release=release)

    with pytest.raises(ValueError, match="partner_environment_invalid"):
        validate_partner_release(config)
    assert evaluate_partner_release(config).config_valid is False


def test_evaluate_never_raises_and_reports_every_rejection(
    config_factory, signed_release
) -> None:
    broken = [
        config_factory(partner_provider_release=None),
        config_factory(release=signed_release(provider_kind="reference")),
        config_factory(release=signed_release(raw=b"not json")),
        config_factory(release=signed_release(mode=0o644)),
        config_factory(release=signed_release(stable_subject_verified=False)),
        config_factory(
            release=signed_release(dev_real_account_tested_at="2020-01-01T00:00:00Z")
        ),
        config_factory(
            release=signed_release(), release_sha256="f" * 64
        ),
        SimpleNamespace(environment="production"),
    ]

    for config in broken:
        status = evaluate_partner_release(config)
        assert status.partner_login_available is False
        assert status.provider_kind is None
        assert status.reason
        assert status.reason != "partner_release_validated"

    assert evaluate_partner_release(
        config_factory(partner_provider_release=None)
    ).config_valid is False
    assert (
        evaluate_partner_release(
            config_factory(release=signed_release(mode=0o644))
        ).config_valid
        is False
    )


def test_incomplete_config_object_fails_closed() -> None:
    with pytest.raises(ValueError, match="partner_release_config_invalid"):
        validate_partner_release(SimpleNamespace(environment="production"))


def test_real_platform_config_carries_the_gate_contract(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_ENVIRONMENT", "production")
    monkeypatch.delenv("PLATFORM_PARTNER_PROVIDER_KIND", raising=False)
    monkeypatch.delenv("PLATFORM_PARTNER_IDENTITY_ENABLED", raising=False)
    monkeypatch.delenv("PLATFORM_PARTNER_PROVIDER_RELEASE_FILE", raising=False)
    monkeypatch.delenv("PLATFORM_PARTNER_PROVIDER_RELEASE_SHA256", raising=False)

    config = load_config()
    status = evaluate_partner_release(config)

    assert config.partner_identity_enabled is False
    assert config.partner_provider_release_file == ""
    assert config.partner_provider_release_sha256 == ""
    assert status.partner_login_available is False
    assert status.config_valid is True
    assert status.reason == "partner_identity_disabled"


def test_enabled_production_config_without_evidence_fails_startup(
    monkeypatch, signed_release
) -> None:
    monkeypatch.setenv("PLATFORM_ENVIRONMENT", "production")
    monkeypatch.setenv("PLATFORM_PARTNER_IDENTITY_ENABLED", "1")
    monkeypatch.setenv("PLATFORM_PARTNER_PROVIDER_KIND", PROVIDER_KIND)

    with pytest.raises(ValueError, match="partner_provider_release_required"):
        load_config()


@pytest.mark.parametrize("value", ["yes-please", "2", "enabled", "TRUE "])
def test_partner_identity_flag_is_strict(monkeypatch, value) -> None:
    monkeypatch.setenv("PLATFORM_PARTNER_IDENTITY_ENABLED", value)

    with pytest.raises(ValueError, match="partner_identity_enabled_invalid"):
        load_config()


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "FALSE"])
def test_partner_identity_flag_defaults_closed(monkeypatch, value) -> None:
    monkeypatch.setenv("PLATFORM_PARTNER_IDENTITY_ENABLED", value)

    assert load_config().partner_identity_enabled is False


def test_gate_report_is_fixed_and_free_of_paths_and_secrets(
    config_factory, signed_release, capsys
) -> None:
    module = _release_module()
    release = signed_release()
    disabled = config_factory(
        partner_identity_enabled=False,
        partner_provider_kind="",
        partner_provider_release=None,
    )

    report = module.render_gate_report(evaluate_partner_release(disabled))

    assert report == (
        "PARTNER_PROVIDER_CONFIG_VALID=true\n"
        "PARTNER_LOGIN_EXPECTED=false\n"
        "PARTNER_PROVIDER_KIND=none\n"
        "PARTNER_RELEASE_REASON=partner_identity_disabled"
    )
    enabled_report = module.render_gate_report(
        evaluate_partner_release(config_factory(release=release))
    )
    assert enabled_report == (
        "PARTNER_PROVIDER_CONFIG_VALID=true\n"
        "PARTNER_LOGIN_EXPECTED=true\n"
        f"PARTNER_PROVIDER_KIND={PROVIDER_KIND}\n"
        "PARTNER_RELEASE_REASON=partner_release_validated"
    )
    for rendered in (report, enabled_report):
        assert str(release.path) not in rendered
        assert release.sha256 not in rendered
        assert EVIDENCE_SHA256 not in rendered


def test_gate_cli_reads_only_the_environment(monkeypatch, capsys) -> None:
    module = _release_module()
    monkeypatch.setenv("PLATFORM_ENVIRONMENT", "production")
    monkeypatch.delenv("PLATFORM_PARTNER_IDENTITY_ENABLED", raising=False)
    monkeypatch.delenv("PLATFORM_PARTNER_PROVIDER_KIND", raising=False)

    exit_code = module.main(["gate"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    assert captured.out == (
        "PARTNER_PROVIDER_CONFIG_VALID=true\n"
        "PARTNER_LOGIN_EXPECTED=false\n"
        "PARTNER_PROVIDER_KIND=none\n"
        "PARTNER_RELEASE_REASON=partner_identity_disabled\n"
    )


def test_gate_cli_rejects_unknown_arguments(monkeypatch, capsys) -> None:
    module = _release_module()

    assert module.main([]) == 2
    assert module.main(["gate", "--enable"]) == 2
    assert module.main(["enable"]) == 2
    assert capsys.readouterr().out == ""


def test_gate_cli_reports_invalid_configuration_without_enabling(
    monkeypatch, signed_release, capsys
) -> None:
    module = _release_module()
    release = signed_release(mode=0o644)
    monkeypatch.setenv("PLATFORM_ENVIRONMENT", "production")
    monkeypatch.setenv("PLATFORM_PARTNER_IDENTITY_ENABLED", "1")
    monkeypatch.setenv("PLATFORM_PARTNER_PROVIDER_KIND", PROVIDER_KIND)
    monkeypatch.setenv("PLATFORM_PARTNER_PROVIDER_RELEASE_FILE", str(release.path))
    monkeypatch.setenv("PLATFORM_PARTNER_PROVIDER_RELEASE_SHA256", release.sha256)

    exit_code = module.main(["gate"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.out == (
        "PARTNER_PROVIDER_CONFIG_VALID=false\n"
        "PARTNER_LOGIN_EXPECTED=false\n"
        "PARTNER_PROVIDER_KIND=none\n"
        "PARTNER_RELEASE_REASON=partner_provider_release_insecure\n"
    )
    assert str(release.path) not in captured.err


def test_cloud_compose_defaults_partner_identity_to_fully_disabled() -> None:
    compose = COMPOSE.read_text("utf-8")

    for declaration in (
        'PLATFORM_PARTNER_IDENTITY_ENABLED: "0"',
        'PLATFORM_PARTNER_PROVIDER_KIND: ""',
        'PLATFORM_PARTNER_PROVIDER_RELEASE_FILE: ""',
        'PLATFORM_PARTNER_PROVIDER_RELEASE_SHA256: ""',
    ):
        assert compose.count(declaration) == 1


def test_acceptance_requires_the_disabled_gate_and_all_route_invariants() -> None:
    script = ACCEPT.read_text("utf-8")

    assert "python -m app.control_plane.partner_release gate" in script
    for evidence in (
        "PARTNER_PROVIDER_CONFIG_VALID=true",
        "PARTNER_LOGIN_EXPECTED=false",
        "PUBLIC_FAE_CHAT_UNCHANGED=true",
        "ENTERPRISE_FAE_LAUNCH_UNCHANGED=true",
        "OFFICE_ROUTE_UNCHANGED=true",
        "PLATFORM_ADMIN_ROUTE_UNCHANGED=true",
    ):
        assert evidence in script
    assert "PARTNER_LOGIN_EXPECTED=true" not in script


def test_probe_runbook_keeps_release_disabled_and_defines_revocation_rollback() -> None:
    runbook = RUNBOOK.read_text("utf-8").lower()

    for required in (
        "two distinct",
        "stable subject",
        "reference provider",
        "0600",
        "sha-256",
        "partner_login_expected=false",
        "public_fae_chat_unchanged=true",
        "enterprise_fae_launch_unchanged=true",
        "office_route_unchanged=true",
        "platform_admin_route_unchanged=true",
        "revoke partner bindings",
        "do not delete",
        "do not restart unrelated agents",
        "provider selection",
    ):
        assert required in runbook


def test_release_file_mode_helper_matches_the_private_file_convention() -> None:
    module = _release_module()

    assert module.MAX_RELEASE_BYTES == 4096
    assert module.MAX_EVIDENCE_AGE_DAYS == 180
    assert module.CONTRACT_VERSION == CONTRACT_VERSION
    assert module.RELEASE_FIELDS == frozenset(RELEASE_FIELDS)
    assert stat.S_IMODE(0o600) & 0o177 == 0


def _production_app(tmp_path, monkeypatch, provider):
    """Build the Platform app the way existing production wiring tests do."""
    from app.control_plane.auth import AuthSecrets
    from app.control_plane.models import ControlPlaneConfig, IdentityMode
    from test_dingtalk_auth_api import FakeAuth, _app
    from test_partner_provider import FakePartnerService

    config_module = importlib.import_module("app.config")
    monkeypatch.setattr(
        config_module,
        "_load_control_plane_config",
        lambda: ControlPlaneConfig(
            mode=IdentityMode.PRODUCTION,
            control_database_url_file="",
            audit_database_url_file="",
            public_base_url="https://agent.orbbec.com.cn",
            route_prefix="/",
            cookie_name="__Host-platform_session",
            dingtalk_app_key="",
            dingtalk_agent_id="",
            dingtalk_corp_id="",
            dingtalk_app_secret_file="",
            encryption_keyring_file="",
            hmac_keyring_file="",
        ),
    )
    monkeypatch.setenv("PLATFORM_IDENTITY_MODE", "production")
    monkeypatch.delenv("PLATFORM_ENVIRONMENT", raising=False)
    auth = FakeAuth(mode=IdentityMode.PRODUCTION)
    auth.secrets = AuthSecrets(b"s" * 32, key_version=7)
    return _app(
        tmp_path,
        monkeypatch,
        auth,
        partner_service=FakePartnerService(provider),
        partner_provider=provider,
    )


def test_app_startup_refuses_a_provider_without_release_evidence(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("PLATFORM_PARTNER_PROVIDER_KIND", PROVIDER_KIND)
    monkeypatch.setenv("PLATFORM_PARTNER_IDENTITY_ENABLED", "1")

    with pytest.raises(ValueError, match="^partner_provider_release_required$"):
        _production_app(tmp_path, monkeypatch, _FakeProvider(PROVIDER_KIND))


def test_app_startup_refuses_a_disabled_gate_with_an_injected_provider(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("PLATFORM_PARTNER_PROVIDER_KIND", PROVIDER_KIND)
    monkeypatch.delenv("PLATFORM_PARTNER_IDENTITY_ENABLED", raising=False)

    with pytest.raises(ValueError, match="^partner_identity_disabled$"):
        _production_app(tmp_path, monkeypatch, _FakeProvider(PROVIDER_KIND))


def test_app_startup_refuses_evidence_naming_another_provider(
    tmp_path, monkeypatch, signed_release
) -> None:
    fresh = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    release = signed_release(dev_real_account_tested_at=fresh)
    monkeypatch.setenv("PLATFORM_PARTNER_PROVIDER_KIND", PROVIDER_KIND)
    monkeypatch.setenv("PLATFORM_PARTNER_IDENTITY_ENABLED", "1")
    monkeypatch.setenv("PLATFORM_PARTNER_PROVIDER_RELEASE_FILE", str(release.path))
    monkeypatch.setenv("PLATFORM_PARTNER_PROVIDER_RELEASE_SHA256", release.sha256)

    with pytest.raises(ValueError, match="^partner_provider_kind_mismatch$"):
        _production_app(tmp_path, monkeypatch, _FakeProvider(ALTERNATE_KIND))
