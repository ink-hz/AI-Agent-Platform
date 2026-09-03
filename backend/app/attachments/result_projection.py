from __future__ import annotations

import json
from uuid import NAMESPACE_URL, UUID, uuid5

from psycopg.types.json import Jsonb
from pydantic import ValidationError

from app.execution_relay.content_crypto import ContentCodec
from app.execution_relay.models import CitationPayload, RegisteredArtifactPayload

from .citation_service import CitationInput, CitationService, _citation_subject


class ConversationResultProjectionError(RuntimeError):
    pass


class ConversationResultProjection:
    """Persist public v4 citations and registered outputs with the answer message."""

    def __init__(self, *, content_codec: ContentCodec) -> None:
        if not isinstance(content_codec, ContentCodec):
            raise TypeError("content codec required")
        self._codec = content_codec

    @staticmethod
    def _model(model, value: object):
        try:
            return model.model_validate_json(
                json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                strict=True,
            )
        except (TypeError, ValueError, ValidationError):
            raise ConversationResultProjectionError(
                "conversation result projection invalid"
            ) from None

    def _insert_citations(
        self,
        cursor,
        *,
        conversation_id: UUID,
        message_id: UUID,
        values: object,
    ) -> None:
        if not isinstance(values, list) or len(values) > 50:
            raise ConversationResultProjectionError(
                "conversation result projection invalid"
            )
        for ordinal, value in enumerate(values, 1):
            parsed = self._model(CitationPayload, value)
            citation = CitationService._normalize(
                CitationInput(
                    citation_key=parsed.citation_key,
                    title=parsed.title,
                    url=parsed.url,
                    site=parsed.site,
                    retrieved_at=parsed.retrieved_at,
                    supports=parsed.supports,
                )
            )
            citation_id = uuid5(
                NAMESPACE_URL,
                f"conversation-citation-v64:{message_id}:{citation.citation_key}",
            )
            url = self._codec.seal_json(
                _citation_subject(citation_id, "url"), {"url": citation.url}
            )
            site = self._codec.seal_json(
                _citation_subject(citation_id, "site"), {"site": citation.site}
            )
            title = self._codec.seal_json(
                _citation_subject(citation_id, "title"), {"title": citation.title}
            )
            cursor.execute(
                "insert into platform_attachments.message_citations("
                "citation_id,conversation_id,message_id,ordinal,citation_key,"
                "url_ciphertext,url_key_version,site_ciphertext,site_key_version,"
                "title_ciphertext,title_key_version,supported_claim_locations,"
                "retrieved_at) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    citation_id,
                    conversation_id,
                    message_id,
                    ordinal,
                    citation.citation_key,
                    url.ciphertext,
                    url.key_version,
                    site.ciphertext,
                    site.key_version,
                    title.ciphertext,
                    title.key_version,
                    Jsonb(list(citation.supports)),
                    citation.retrieved_at,
                ),
            )

    def _bind_artifacts(
        self,
        cursor,
        *,
        owner_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        task_id: UUID,
        agent_id: str,
        values: object,
    ) -> None:
        if not isinstance(values, list) or len(values) > 20:
            raise ConversationResultProjectionError(
                "conversation result projection invalid"
            )
        for value in values:
            artifact = self._model(RegisteredArtifactPayload, value)
            row = cursor.execute(
                "select attachment.owner_internal_user_id,attachment.conversation_id,"
                "attachment.state as attachment_state,attachment.immutable_locator,"
                "version.state as version_state,version.result_status "
                "from platform_attachments.artifacts artifact "
                "join platform_attachments.artifact_versions version "
                "on version.artifact_id=artifact.artifact_id "
                "join platform_attachments.attachments attachment "
                "on attachment.attachment_id=version.attachment_id "
                "where artifact.task_id=%s and artifact.agent_id=%s "
                "and artifact.artifact_key=%s and version.attachment_id=%s "
                "and version.producer_version_id=%s",
                (
                    task_id,
                    agent_id,
                    artifact.artifact_key,
                    artifact.attachment_id,
                    artifact.producer_version_id,
                ),
            ).fetchone()
            if row is None:
                raise ConversationResultProjectionError(
                    "conversation result projection invalid"
                )
            ready = (
                row["owner_internal_user_id"] == owner_id
                and row["conversation_id"] == conversation_id
                and row["attachment_state"] == "ready"
                and row["immutable_locator"] is not None
                and row["version_state"] == "ready"
                and row["result_status"] == "succeeded"
            )
            rejected = (
                row["version_state"] == "rejected"
                or row["result_status"] == "failed"
                or row["attachment_state"] in {"quarantined", "rejected", "deleted"}
            )
            if artifact.status == "rejected":
                if not rejected:
                    raise ConversationResultProjectionError(
                        "conversation result projection invalid"
                    )
                continue
            if not ready:
                raise ConversationResultProjectionError(
                    "conversation result projection invalid"
                )
            cursor.execute(
                "insert into platform_attachments.bindings("
                "binding_id,attachment_id,owner_internal_user_id,kind,"
                "conversation_id,message_id,agent_id) "
                "values (%s,%s,%s,'message_output',%s,%s,%s)",
                (
                    uuid5(
                        NAMESPACE_URL,
                        f"conversation-message-output-v64:{message_id}:"
                        f"{artifact.attachment_id}",
                    ),
                    artifact.attachment_id,
                    owner_id,
                    conversation_id,
                    message_id,
                    agent_id,
                ),
            )

    def project_locked(
        self,
        cursor,
        *,
        owner_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        task_id: UUID,
        agent_id: str,
        collaboration: object,
    ) -> None:
        if (
            not isinstance(collaboration, dict)
            or collaboration.get("contract_version") != "core_chat_collaboration_v4"
            or set(collaboration)
            != {
                "contract_version",
                "citations",
                "artifacts",
                "completion",
                "recovery",
            }
        ):
            raise ConversationResultProjectionError(
                "conversation result projection invalid"
            )
        self._insert_citations(
            cursor,
            conversation_id=conversation_id,
            message_id=message_id,
            values=collaboration["citations"],
        )
        self._bind_artifacts(
            cursor,
            owner_id=owner_id,
            conversation_id=conversation_id,
            message_id=message_id,
            task_id=task_id,
            agent_id=agent_id,
            values=collaboration["artifacts"],
        )
