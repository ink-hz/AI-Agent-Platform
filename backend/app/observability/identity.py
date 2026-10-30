"""Resolve trusted FAE/DAQ references inside separately authorized Agent reads."""
from __future__ import annotations

from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from app.control_plane.dsn import validate_control_dsn
from app.execution_relay.content_crypto import ContentCodec, ContentCryptoError, SealedContent

from .models import Page, SessionDetail
from .repository import ObservabilityReadError


class FaeSubjectRepository:
    def __init__(self, database_url: str, *, connect=psycopg.connect):
        validate_control_dsn(database_url, purpose="app")
        self._database_url, self._connect = database_url, connect

    def __repr__(self):
        return "FaeSubjectRepository(database_url=<redacted>)"

    def read(self, session_id: UUID, subject_ids: tuple[UUID, ...], *, agent_id="ai-fae-agent"):
        try:
            with self._connect(
                self._database_url, connect_timeout=3,
                options="-c statement_timeout=5000 -c default_transaction_read_only=on",
                row_factory=dict_row,
            ) as connection:
                return connection.execute(
                    "select * from platform_control.read_agent_session_subjects_v127(%s,%s,%s::uuid[])",
                    (session_id, agent_id, list(subject_ids)),
                ).fetchall()
        except psycopg.Error:
            raise ObservabilityReadError("session identity unavailable") from None


class FaeSessionIdentity:
    def __init__(self, repository: FaeSubjectRepository, *, content_codec: ContentCodec | None):
        self.repository, self.content_codec = repository, content_codec

    @staticmethod
    def _fae(item):
        return item.source_kind == "fae" and item.agent_id in {"ai-fae-agent", "ai-daq-fae-agent"}

    @staticmethod
    def _reference(item):
        ref = item.verified_subject
        if ref and item.agent_id == "ai-daq-fae-agent" and ref.subject_type != "enterprise_member":
            return None
        return ref

    def hydrate(self, result, context):
        if result is None:
            return None
        items = result.items if isinstance(result, Page) else [result]
        fae = [item for item in items if self._fae(item)]
        names = {}
        for agent_id in sorted({item.agent_id for item in fae}):
            refs = {(ref.subject_id, ref.subject_type) for item in fae
                    if item.agent_id == agent_id and (ref := self._reference(item))}
            ids = tuple(sorted({subject_id for subject_id, _ in refs}))
            if not ids:
                continue
            if context is None or not isinstance(context.session_id, UUID):
                raise ObservabilityReadError("session identity unavailable")
            # Each Agent requires its own DB scope check, including in mixed lists.
            rows = (self.repository.read(context.session_id, ids) if agent_id == "ai-fae-agent"
                    else self.repository.read(context.session_id, ids, agent_id=agent_id))
            for row in rows:
                subject_id, subject_type = row["subject_id"], row["subject_type"]
                if subject_id not in ids:
                    raise ObservabilityReadError("session identity unavailable")
                if (subject_id, subject_type) not in refs:
                    continue
                name = row["display_name"]
                # A known enterprise subject can outlive its directory profile.
                # Retain the trusted reference without turning a missing name
                # into a failure of every session on the page.
                if subject_type == "enterprise_member" and name is None:
                    continue
                if subject_type == "partner_operator":
                    if self.content_codec is None:
                        raise ObservabilityReadError("session identity unavailable")
                    try:
                        value = self.content_codec.unseal_json(
                            f"agent-subject-display:{subject_id}",
                            SealedContent(bytes(row["display_name_ciphertext"]), row["display_name_key_version"]),
                        )
                        if set(value) != {"display_name"}:
                            raise ValueError
                        name = value["display_name"]
                    except (ContentCryptoError, KeyError, TypeError, ValueError):
                        raise ObservabilityReadError("session identity unavailable") from None
                if not isinstance(name, str) or not name.strip() or len(name) > 512 or any(ord(c) < 32 or ord(c) == 127 for c in name):
                    raise ObservabilityReadError("session identity unavailable")
                names[(agent_id, subject_id, subject_type)] = name.strip()

        return self.project(result, names)

    @classmethod
    def project(cls, result, names):
        """Clear replica-supplied names even when identity lookup is disabled."""
        if result is None:
            return None

        def project(item):
            if not cls._fae(item):
                return item
            ref = cls._reference(item)
            name = names.get((item.agent_id, ref.subject_id, ref.subject_type)) if ref else None
            updates = {"verified_subject": ref, "primary_sender_name": name, "primary_sender_department": None,
                       "sender_identity_status": "name_only" if name else "unavailable"}
            if isinstance(item, SessionDetail):
                updates["turns"] = [turn.model_copy(update={
                    "verified_subject": ref, "sender_name": name, "sender_department": None,
                    "sender_identity_status": updates["sender_identity_status"],
                }) for turn in item.turns]
            return item.model_copy(update=updates)

        if isinstance(result, Page):
            return result.model_copy(update={"items": [project(item) for item in result.items]})
        return project(result)
