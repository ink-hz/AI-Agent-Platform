"""Fail-closed release gate for the FAE Partner Provider.

Partner operator login stays disabled in production until a *real* Partner
Provider is selected and its dev real-account probe evidence is released. The
gate reads one operator-provided evidence file plus a digest pin and answers a
single explicit question: may partner login be enabled at all?

Every rejection is fail-closed and carries a stable code. Missing, malformed,
insecure, stale, wrong-environment, unregistered-provider, digest-mismatched,
symlinked, wrong-owner and over-permissive evidence all keep partner login off.
The Reference Provider can never satisfy the gate, and no fallback or dynamic
substitution exists: the evidence names exactly one registered, non-reference
Provider kind and it must equal the configured kind.

The evidence document carries no secret material — only booleans, a probe
timestamp and the digest of an evidence bundle archived outside this repository
— so neither this module nor its report ever prints a credential or a path.
"""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

from .partner_provider import (
    partner_provider_registered,
    partner_provider_release_registered,
)

CONTRACT_VERSION = "orbbec-fae-partner-provider/v1"
REFERENCE_PROVIDER_KIND = "reference"
THRESHOLD_FIELDS = (
    "stable_subject_verified",
    "two_distinct_subjects_verified",
    "active_status_or_local_revocation_verified",
    "shared_password_forbidden",
    "state_and_callback_replay_verified",
)
RELEASE_FIELDS = frozenset(
    {
        "contract_version",
        "provider_kind",
        "dev_real_account_tested_at",
        "evidence_sha256",
        *THRESHOLD_FIELDS,
    }
)
CONFIG_FIELDS = (
    "environment",
    "partner_identity_enabled",
    "partner_provider_kind",
    "partner_provider_release_file",
    "partner_provider_release_sha256",
)
KNOWN_ENVIRONMENTS = frozenset({"development", "test", "production"})
MAX_RELEASE_BYTES = 4096
MAX_EVIDENCE_AGE_DAYS = 180

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PROVIDER_KIND = re.compile(r"[a-z][a-z0-9_-]{0,127}\Z")
_RFC3339 = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})\Z"
)

# Fail-closed outcomes that are an expected posture, not a broken deployment:
# production without a selected Provider must stay valid so rollback stays a
# no-op instead of an outage.
EXPECTED_DISABLED_REASONS = frozenset(
    {
        "partner_identity_disabled",
        "partner_release_environment_mismatch",
    }
)
_GATE_REASONS = EXPECTED_DISABLED_REASONS | frozenset(
    {
        "partner_release_config_invalid",
        "partner_environment_invalid",
        "partner_provider_kind_required",
        "partner_provider_release_required",
        "partner_reference_provider_forbidden",
        "partner_provider_release_digest_invalid",
        "partner_provider_release_digest_mismatch",
        "partner_provider_release_insecure",
        "partner_provider_release_malformed",
        "partner_provider_release_contract_mismatch",
        "partner_provider_release_threshold_unmet",
        "partner_provider_release_stale",
        "partner_provider_not_registered",
        "partner_provider_release_not_registered",
        "partner_provider_kind_mismatch",
    }
)


@dataclass(frozen=True)
class ValidatedPartnerRelease:
    """The one Provider the release evidence authorises for production."""

    provider_kind: str
    release_sha256: str
    evidence_sha256: str
    dev_real_account_tested_at: datetime


@dataclass(frozen=True)
class PartnerReleaseStatus:
    """Explicit gate result for startup, acceptance and the operator report."""

    partner_login_available: bool
    config_valid: bool
    reason: str
    provider_kind: str | None = None


def _release_stat(descriptor: int) -> os.stat_result:
    """Metadata for the already-open evidence file descriptor."""
    return os.fstat(descriptor)


def _read_config(config: object) -> dict[str, object]:
    values: dict[str, object] = {}
    for field in CONFIG_FIELDS:
        if not hasattr(config, field):
            raise ValueError("partner_release_config_invalid")
        values[field] = getattr(config, field)
    return values


def _release_metadata_is_secure(metadata: os.stat_result) -> bool:
    """Accept a private service-owned file or a root-owned service-group copy."""
    mode = stat.S_IMODE(metadata.st_mode)
    effective_uid = os.getuid()
    if metadata.st_uid == effective_uid:
        return mode & 0o177 == 0
    if metadata.st_uid != 0 or effective_uid == 0:
        return False
    service_groups = {os.getgid(), *os.getgroups()}
    return (
        metadata.st_gid in service_groups
        and bool(mode & 0o040)
        and mode & 0o137 == 0
    )


def _require_secure_release_file(path_value: str) -> tuple[Path, bytes]:
    candidate = path_value.strip()
    if not candidate:
        raise ValueError("partner_provider_release_required")
    path = Path(candidate)
    if not path.is_absolute():
        raise ValueError("partner_provider_release_insecure")
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if no_follow == 0:  # pragma: no cover - production is Linux
        raise ValueError("partner_provider_release_insecure")
    non_blocking = getattr(os, "O_NONBLOCK", 0)
    if non_blocking == 0:  # pragma: no cover - production is Linux
        raise ValueError("partner_provider_release_insecure")
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | no_follow | non_blocking | getattr(os, "O_CLOEXEC", 0),
        )
        metadata = _release_stat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("partner_provider_release_insecure")
        if not _release_metadata_is_secure(metadata):
            raise ValueError("partner_provider_release_insecure")
        if metadata.st_size > MAX_RELEASE_BYTES:
            raise ValueError("partner_provider_release_insecure")
        chunks: list[bytes] = []
        remaining = MAX_RELEASE_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        body = b"".join(chunks)
    except ValueError:
        raise
    except OSError as error:
        raise ValueError("partner_provider_release_insecure") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(body) > MAX_RELEASE_BYTES:
        raise ValueError("partner_provider_release_insecure")
    return path, body


def _parse_release_document(body: bytes) -> dict[str, object]:
    try:
        document = json.loads(body)
    except (RecursionError, UnicodeDecodeError, ValueError) as error:
        raise ValueError("partner_provider_release_malformed") from error
    if not isinstance(document, dict):
        raise ValueError("partner_provider_release_malformed")  # noqa: TRY004
    if set(document) != set(RELEASE_FIELDS):
        raise ValueError("partner_provider_release_malformed")
    return document


def _release_provider_kind(document: dict[str, object]) -> str:
    kind = document["provider_kind"]
    if not isinstance(kind, str) or _PROVIDER_KIND.fullmatch(kind) is None:
        raise ValueError("partner_provider_release_malformed")
    if kind == REFERENCE_PROVIDER_KIND:
        raise ValueError("partner_reference_provider_forbidden")
    return kind


def _release_evidence_digest(document: dict[str, object]) -> str:
    digest = document["evidence_sha256"]
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        raise ValueError("partner_provider_release_malformed")
    return digest


def _release_tested_at(document: dict[str, object], now: datetime) -> datetime:
    raw = document["dev_real_account_tested_at"]
    if not isinstance(raw, str) or _RFC3339.fullmatch(raw) is None:
        raise ValueError("partner_provider_release_malformed")
    try:
        tested_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("partner_provider_release_malformed") from error
    if tested_at.tzinfo is None:
        raise ValueError("partner_provider_release_malformed")
    if tested_at > now:
        raise ValueError("partner_provider_release_stale")
    if now - tested_at > timedelta(days=MAX_EVIDENCE_AGE_DAYS):
        raise ValueError("partner_provider_release_stale")
    return tested_at


def _require_thresholds(document: dict[str, object]) -> None:
    for field in THRESHOLD_FIELDS:
        value = document[field]
        if value is True:
            continue
        if isinstance(value, bool):
            raise ValueError("partner_provider_release_threshold_unmet")  # noqa: TRY004
        raise ValueError("partner_provider_release_malformed")


def validate_partner_release(
    config: object, *, now: datetime | None = None
) -> ValidatedPartnerRelease:
    """Return the authorised Provider, or raise the fail-closed reason.

    Raises ``ValueError`` whose single argument is a stable gate code. Callers
    that must not fail startup use :func:`evaluate_partner_release` instead.
    """
    moment = now or datetime.now(UTC)
    values = _read_config(config)

    environment = values["environment"]
    if not isinstance(environment, str) or environment not in KNOWN_ENVIRONMENTS:
        raise ValueError("partner_environment_invalid")
    if values["partner_identity_enabled"] is not True:
        raise ValueError("partner_identity_disabled")
    if environment != "production":
        raise ValueError("partner_release_environment_mismatch")

    configured_kind = values["partner_provider_kind"]
    if not isinstance(configured_kind, str) or not configured_kind.strip():
        raise ValueError("partner_provider_kind_required")
    configured_kind = configured_kind.strip()
    if configured_kind == REFERENCE_PROVIDER_KIND:
        raise ValueError("partner_reference_provider_forbidden")

    release_file = values["partner_provider_release_file"]
    if not isinstance(release_file, str):
        raise ValueError("partner_release_config_invalid")  # noqa: TRY004
    _path, body = _require_secure_release_file(release_file)

    pinned = values["partner_provider_release_sha256"]
    if not isinstance(pinned, str) or _SHA256.fullmatch(pinned.strip()) is None:
        raise ValueError("partner_provider_release_digest_invalid")
    if sha256(body).hexdigest() != pinned.strip():
        raise ValueError("partner_provider_release_digest_mismatch")

    document = _parse_release_document(body)
    if document["contract_version"] != CONTRACT_VERSION:
        raise ValueError("partner_provider_release_contract_mismatch")
    released_kind = _release_provider_kind(document)
    _require_thresholds(document)
    evidence_sha256 = _release_evidence_digest(document)
    tested_at = _release_tested_at(document, moment)

    if not partner_provider_registered(released_kind):
        raise ValueError("partner_provider_not_registered")
    if released_kind != configured_kind:
        raise ValueError("partner_provider_kind_mismatch")
    if not partner_provider_release_registered(released_kind):
        raise ValueError("partner_provider_release_not_registered")

    return ValidatedPartnerRelease(
        provider_kind=released_kind,
        release_sha256=sha256(body).hexdigest(),
        evidence_sha256=evidence_sha256,
        dev_real_account_tested_at=tested_at,
    )


def evaluate_partner_release(
    config: object, *, now: datetime | None = None
) -> PartnerReleaseStatus:
    """Never-raising view of the gate, for startup reports and acceptance."""
    try:
        validated = validate_partner_release(config, now=now)
    except ValueError as error:
        return status_for_reason(str(error))
    return PartnerReleaseStatus(
        partner_login_available=True,
        config_valid=True,
        reason="partner_release_validated",
        provider_kind=validated.provider_kind,
    )


def status_for_reason(reason: str) -> PartnerReleaseStatus:
    """Map a gate code to a disabled status, hiding unrecognised detail."""
    code = reason if reason in _GATE_REASONS else "partner_release_config_invalid"
    return PartnerReleaseStatus(
        partner_login_available=False,
        config_valid=code in EXPECTED_DISABLED_REASONS,
        reason=code,
        provider_kind=None,
    )


def render_gate_report(status: PartnerReleaseStatus) -> str:
    """Fixed four-line operator report. Carries no path and no digest."""
    return "\n".join(
        (
            f"PARTNER_PROVIDER_CONFIG_VALID={'true' if status.config_valid else 'false'}",
            f"PARTNER_LOGIN_EXPECTED={'true' if status.partner_login_available else 'false'}",
            f"PARTNER_PROVIDER_KIND={status.provider_kind or 'none'}",
            f"PARTNER_RELEASE_REASON={status.reason}",
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m app.control_plane.partner_release gate`` — read-only report.

    The CLI only reports. It can never enable partner login, so acceptance and
    the runbook can run it against production configuration without mutating
    anything.
    """
    import sys

    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments != ["gate"]:
        print(
            "usage: python -m app.control_plane.partner_release gate",
            file=sys.stderr,
        )
        return 2

    from ..config import load_config

    try:
        config = load_config()
    except ValueError as error:
        status = status_for_reason(str(error))
    else:
        status = evaluate_partner_release(config)
    print(render_gate_report(status))
    return 0 if status.config_valid else 1


if __name__ == "__main__":  # pragma: no cover - module CLI entry point
    raise SystemExit(main())
