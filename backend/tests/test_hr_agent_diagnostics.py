from uuid import uuid4

import pytest
from app.control_plane.crypto import IdentityKeyring
from app.execution_relay.content_crypto import ContentCodec
from app.hr_agent.diagnostics import DiagnosticAccessError, DiagnosticStore
from app.hr_agent.types import DiagnosticIdentity


def codec():
    return ContentCodec(
        IdentityKeyring(1, "platform-content-encryption", {1: b"d" * 32})
    )


def test_enabled_diagnostics_requires_explicit_restricted_audit(tmp_path):
    with pytest.raises(ValueError, match="diagnostic configuration"):
        DiagnosticStore(
            tmp_path / "private",
            codec(),
            enabled=True,
            trusted_roles=("hr_diagnostics",),
            max_ttl_seconds=60,
        )


def test_diagnostic_access_is_audited_and_unavailable_sink_denies_decryption(tmp_path):
    actor = DiagnosticIdentity(uuid4(), ("hr_diagnostics",))
    events = []
    store = DiagnosticStore(
        tmp_path / "private",
        codec(),
        enabled=True,
        trusted_roles=("hr_diagnostics",),
        max_ttl_seconds=60,
        audit=lambda *event: events.append(event),
    )
    record = store.create(actor, uuid4(), {"body": "private sentinel"}, ttl_seconds=30)
    assert store.read(actor, record.diagnostic_id).sealed_payload == {
        "body": "private sentinel"
    }
    assert [event[0] for event in events] == ["create", "read"]

    class NoDecrypt:
        def unseal_json(self, *args):
            pytest.fail("Audit failure must deny before decrypt")

    def failed_audit(*args):
        raise RuntimeError("AUDIT_SECRET_SENTINEL")

    store._codec = NoDecrypt()
    store._audit = failed_audit
    with pytest.raises(DiagnosticAccessError) as error:
        store.read(actor, record.diagnostic_id)
    assert "AUDIT_SECRET_SENTINEL" not in str(error.value)
    with pytest.raises(DiagnosticAccessError):
        store.delete(actor, record.diagnostic_id)
    assert (tmp_path / "private" / f"{record.diagnostic_id}.json").exists()
    store._audit = lambda *event: events.append(event)
    store.delete(actor, record.diagnostic_id)
    assert events[-1][0] == "delete"
    assert not (tmp_path / "private" / f"{record.diagnostic_id}.json").exists()
