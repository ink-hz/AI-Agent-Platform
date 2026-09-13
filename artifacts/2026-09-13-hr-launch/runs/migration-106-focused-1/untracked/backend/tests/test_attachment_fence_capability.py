from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.attachments.fence_capability import (
    ATTACHMENT_FENCE_MIGRATION_SHA256,
    AttachmentFenceCapabilityError,
    require_attachment_fence_capability,
)


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, rows):
        self.rows = list(rows)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, _query, _params=None):
        return _Result(self.rows.pop(0))


def _connect(rows):
    return lambda *_args, **_kwargs: _Connection(rows)


def test_capability_accepts_exact_106_identity_ledger_and_grants():
    require_attachment_fence_capability(
        "postgresql://platform_control_app@localhost/agent_platform_control",
        purpose="app",
        connect=_connect(
            (
                (
                    "platform_control_app",
                    "platform_control_app",
                    "agent_platform_control",
                ),
                (ATTACHMENT_FENCE_MIGRATION_SHA256,),
                (True, True, True, True),
            )
        ),
    )


def test_capability_checksum_is_the_exact_106_source():
    migration = (
        Path(__file__).parents[1]
        / "control_migrations"
        / "106_attachment_erasure_write_fence.sql"
    )

    assert hashlib.sha256(migration.read_bytes()).hexdigest() == (
        ATTACHMENT_FENCE_MIGRATION_SHA256
    )


@pytest.mark.parametrize(
    "rows",
    (
        (
            ("platform_control_app", "platform_control_app", "agent_platform_control"),
            None,
            (True, True, True, True),
        ),
        (
            ("platform_control_app", "platform_control_app", "agent_platform_control"),
            ("0" * 64,),
            (True, True, True, True),
        ),
        (
            ("platform_control_maintenance", "platform_control_maintenance", "agent_platform_control"),
            (ATTACHMENT_FENCE_MIGRATION_SHA256,),
            (True, True, True, True),
        ),
        (
            ("platform_control_app", "platform_control_app", "agent_platform_control"),
            (ATTACHMENT_FENCE_MIGRATION_SHA256,),
            (True, True, False, True),
        ),
    ),
)
def test_capability_rejects_missing_or_wrong_ledger_role_and_grants(rows):
    with pytest.raises(AttachmentFenceCapabilityError):
        require_attachment_fence_capability(
            "postgresql://platform_control_app@localhost/agent_platform_control",
            purpose="app",
            connect=_connect(rows),
        )


def test_api_attachment_construction_checks_capability_before_other_services(
    monkeypatch,
):
    from app import main

    called = []

    def reject(database_url, *, purpose):
        called.append((database_url, purpose))
        raise AttachmentFenceCapabilityError()

    monkeypatch.setattr(main, "require_attachment_fence_capability", reject)
    monkeypatch.setattr(
        main, "read_secret_file", lambda _path: "exact-app-dsn"
    )

    with pytest.raises(AttachmentFenceCapabilityError):
        main.build_conversation_attachment_services(
            SimpleNamespace(attachment_control_database_url_file="/secret/app")
        )

    assert called == [("exact-app-dsn", "app")]


def test_worker_builders_check_brain_and_maintenance_before_constructing_services(
    monkeypatch,
):
    from app.attachments import worker_runtime

    called = []

    def reject(database_url, *, purpose):
        called.append((database_url, purpose))
        raise AttachmentFenceCapabilityError()

    monkeypatch.setattr(worker_runtime, "require_attachment_fence_capability", reject)
    monkeypatch.setattr(
        worker_runtime,
        "_required_absolute_path",
        lambda name: Path("/secret/" + name.lower()),
    )
    monkeypatch.setattr(
        worker_runtime, "read_secret_file", lambda path: "dsn:" + path
    )

    with pytest.raises(AttachmentFenceCapabilityError):
        worker_runtime.build_processor(content_codec=object(), client=object())
    with pytest.raises(AttachmentFenceCapabilityError):
        worker_runtime.build_maintenance_services(
            content_codec=object(), object_store=object()
        )

    assert [purpose for _dsn, purpose in called] == ["brain", "maintenance"]


def test_worker_healthcheck_uses_same_capability_gate(monkeypatch):
    from app.attachments import worker_runtime

    calls = []
    monkeypatch.setattr(
        worker_runtime,
        "_required_absolute_path",
        lambda name: Path("/secret/" + name.lower()),
    )
    monkeypatch.setattr(worker_runtime, "read_secret_file", lambda path: str(path))

    def reject(database_url, *, purpose):
        calls.append((database_url, purpose))
        raise RuntimeError("missing 106")

    monkeypatch.setattr(worker_runtime, "require_attachment_fence_capability", reject)

    assert worker_runtime.healthcheck() == 1
    assert calls == [
        ("/secret/platform_attachment_worker_database_url_file", "brain")
    ]
