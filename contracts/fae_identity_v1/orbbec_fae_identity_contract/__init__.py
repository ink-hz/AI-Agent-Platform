"""The executable `orbbec-fae-identity/v1` contract.

Two repositories depend on this package: the Platform serves the two private
back-channel messages, and the AI FAE Agent consumes them. Neither side may
describe the messages in prose only, so the schema, the examples, the
cross-repository content digest and the invariants a JSON Schema cannot state
all live here, next to each other.

The digest is the frozen cross-repository algorithm, identical to the one the
Agent's `tests/contract/conftest.py` reimplements: enumerate every regular file
below `fixtures/` and `schema/` in sorted relative POSIX-path order and update
SHA-256 with ``relative_path_utf8 + NUL + raw_file_bytes + NUL``. Nothing else
in this package is covered, so tests and packaging metadata can change without
breaking a pin.
"""

from __future__ import annotations

import io
import stat
import subprocess
import tarfile
from hashlib import sha256
from json import loads
from pathlib import Path

from jsonschema import Draft202012Validator

CONTRACT_VERSION = "orbbec-fae-identity/v1"
AGENT_ID = "ai-fae-agent"
SUBJECT_TYPES = ("enterprise_member", "partner_operator")
DIGEST_DIRECTORIES = ("fixtures", "schema")
CONTRACT_RELATIVE_PATH = "contracts/fae_identity_v1"
SCHEMA_FILE = "fae-identity-v1.schema.json"
MESSAGES = (
    "exchange_response",
    "validate_response",
    "capabilities_response",
    "contract_document",
)

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "schema" / SCHEMA_FILE
FIXTURES_PATH = ROOT / "fixtures"

__all__ = [
    "AGENT_ID",
    "CONTRACT_RELATIVE_PATH",
    "CONTRACT_VERSION",
    "DIGEST_DIRECTORIES",
    "MESSAGES",
    "SUBJECT_TYPES",
    "archive_digest",
    "check_subject_invariants",
    "contract_digest",
    "load_fixture",
    "load_schema",
    "message_schema",
    "validator",
]


def load_schema() -> dict:
    return loads(SCHEMA_PATH.read_text("utf-8"))


def message_schema(name: str) -> dict:
    """A standalone schema for one message of the contract."""
    if name not in MESSAGES:
        raise ValueError("fae_identity_contract_unknown_message")
    schema = load_schema()
    return {
        "$schema": schema["$schema"],
        "$ref": f"#/$defs/{name}",
        "$defs": schema["$defs"],
    }


def validator(name: str) -> Draft202012Validator:
    schema = message_schema(name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(
        schema, format_checker=Draft202012Validator.FORMAT_CHECKER
    )


def load_fixture(name: str) -> dict:
    return loads((FIXTURES_PATH / name).read_text("utf-8"))


def check_subject_invariants(payload: dict) -> None:
    """The cross-field rules JSON Schema cannot state.

    An enterprise member *is* the internal user, so the two ids have to agree;
    a partner operator has no internal user at all. Getting this wrong would
    silently hand one subject another subject's account scope.
    """
    if payload.get("contract_version") != CONTRACT_VERSION:
        raise ValueError("fae_identity_contract_version_mismatch")
    if payload.get("agent_id") != AGENT_ID:
        raise ValueError("fae_identity_contract_agent_mismatch")
    subject_type = payload.get("subject_type")
    if subject_type == "enterprise_member":
        if payload.get("subject_id") != payload.get("internal_user_id"):
            raise ValueError("fae_identity_contract_enterprise_subject_mismatch")
        if payload.get("partner_display_name") is not None:
            raise ValueError("fae_identity_contract_enterprise_partner_name")
    elif subject_type == "partner_operator":
        if payload.get("internal_user_id") is not None:
            raise ValueError("fae_identity_contract_partner_internal_user")
    else:
        raise ValueError("fae_identity_contract_unknown_subject_type")


def _digest(files: list[tuple[str, bytes]]) -> str:
    digest = sha256()
    for relative, content in sorted(files):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def contract_digest(root: Path | str = ROOT) -> str:
    """Digest the schema and fixture bytes as they are on disk."""
    source = Path(root)
    files: list[tuple[str, bytes]] = []
    for directory in DIGEST_DIRECTORIES:
        base = source / directory
        if not base.is_dir():
            raise ValueError("fae_identity_contract_incomplete")
        for path in base.rglob("*"):
            if stat.S_ISREG(path.lstat().st_mode):
                files.append((path.relative_to(source).as_posix(), path.read_bytes()))
    return _digest(files)


def archive_digest(repository: Path | str, commit: str) -> str:
    """Digest the same bytes as they were committed, so a pin cannot drift.

    Reading through `git archive` is what keeps a pin valid across later
    unrelated commits: the digest is taken from the pinned commit's tree, not
    from whatever the worktree happens to hold now.
    """
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "archive",
            "--format=tar",
            "--",
            f"{commit}:{CONTRACT_RELATIVE_PATH}",
        ],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise ValueError("fae_identity_contract_missing_at_commit")
    files: list[tuple[str, bytes]] = []
    with tarfile.open(fileobj=io.BytesIO(completed.stdout), mode="r:") as archive:
        for member in archive.getmembers():
            relative = Path(member.name)
            if not member.isfile() or relative.parts[0] not in DIGEST_DIRECTORIES:
                continue
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ValueError("fae_identity_contract_unreadable_archive")
            files.append((relative.as_posix(), extracted.read()))
    if not files:
        raise ValueError("fae_identity_contract_missing_at_commit")
    return _digest(files)
