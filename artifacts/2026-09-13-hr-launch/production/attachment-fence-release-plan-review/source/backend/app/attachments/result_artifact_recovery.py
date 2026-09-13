"""Frozen Result file consumer. Never creates or replays an execution Attempt."""

import hashlib

from app.execution_relay.content_crypto import ContentCryptoError, SealedContent

from .conversation_repository import attachment_name_subject
from .result_projection import (
    ConversationResultProjection,
    ConversationResultProjectionError,
)


class ArtifactRecovery:
    def __init__(self, repository):
        self.repository = repository
        self.projection = ConversationResultProjection(
            content_codec=repository.content_codec
        )

    def on_ready(self, attachment_id):
        with self.repository._connection() as connection:
            rows = connection.execute(
                "select intent.run_id,intent.intent_index from platform_control.result_artifact_intents intent "
                "join platform_attachments.artifacts artifact on artifact.task_id=intent.run_id "
                "and artifact.artifact_key=intent.artifact_key "
                "join platform_attachments.artifact_versions version on version.artifact_id=artifact.artifact_id "
                "and version.producer_version_id=intent.producer_version_id "
                "where version.attachment_id=%s and intent.status<>'ready' and intent.grant_id is not null "
                "order by intent.intent_index limit 20",
                (attachment_id,),
            ).fetchall()
        return sum(self._reconcile(row) for row in rows)

    def retry_due(self, limit=20):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("artifact recovery batch invalid")
        with self.repository._connection() as connection:
            rows = connection.execute(
                "select run_id,intent_index from platform_control.result_artifact_intents intent "
                "where (status='pending' and next_attempt_at<=clock_timestamp()) or "
                "(status='failed' and last_error_code='artifact_grant_expired' and grant_id is not null "
                "and exists(select 1 from platform_attachments.artifacts artifact "
                "join platform_attachments.artifact_versions version using(artifact_id) "
                "join platform_attachments.attachments attachment using(attachment_id) "
                "where artifact.task_id=intent.run_id and artifact.agent_id='hr-bot' "
                "and artifact.artifact_key=intent.artifact_key "
                "and version.producer_version_id=intent.producer_version_id "
                "and version.state='ready' and version.result_status='succeeded' and attachment.state='ready')) "
                "order by next_attempt_at,run_id,intent_index limit %s",
                (limit,),
            ).fetchall()
        return sum(self._reconcile(row) for row in rows)

    def _reconcile(self, key):
        with self.repository._connection() as connection:
            # Same conversation-first order as text publication. A slow file
            # must not hold the executor lease or call any external service.
            context = connection.execute(
                "select conversation.conversation_id,conversation.owner_internal_user_id "
                "from platform_control.result_artifact_intents intent "
                "join platform_control.conversation_messages message using(message_id) "
                "join platform_control.conversations conversation using(conversation_id) "
                "where intent.run_id=%s and intent.intent_index=%s for update of conversation skip locked",
                (key["run_id"], key["intent_index"]),
            ).fetchone()
            if context is None:
                return 0
            intent = connection.execute(
                "select * from platform_control.result_artifact_intents where run_id=%s "
                "and intent_index=%s and status<>'ready' and grant_id is not null for update skip locked",
                (key["run_id"], key["intent_index"]),
            ).fetchone()
            if intent is None:
                return 0
            file = connection.execute(
                "select attachment.*, version.state as version_state,version.result_status, "
                "upload.state as upload_state from platform_attachments.artifacts artifact "
                "join platform_attachments.artifact_versions version using(artifact_id) "
                "join platform_attachments.attachments attachment using(attachment_id) "
                "join platform_attachments.uploads upload using(attachment_id) "
                "where artifact.task_id=%s and artifact.agent_id='hr-bot' "
                "and artifact.artifact_key=%s and version.producer_version_id=%s "
                "and upload.declared_mime=%s and upload.size_bytes=%s and upload.expected_sha256=%s",
                (
                    intent["run_id"],
                    intent["artifact_key"],
                    intent["producer_version_id"],
                    intent["declared_mime"],
                    intent["size_bytes"],
                    intent["expected_sha256"],
                ),
            ).fetchone()
            ready = (
                file is not None
                and file["state"] == "ready"
                and file["version_state"] == "ready"
                and file["result_status"] == "succeeded"
            )
            error = None
            if ready:
                try:
                    name = self.repository.content_codec.unseal_json(
                        attachment_name_subject(file["attachment_id"]),
                        SealedContent(
                            bytes(file["original_name_ciphertext"]),
                            file["original_name_key_version"],
                        ),
                    )["original_name"]
                    if (
                        artifact_key(name) != intent["artifact_key"]
                        or file["sha256"] != intent["expected_sha256"]
                        or file["detected_mime"] != intent["declared_mime"]
                    ):
                        raise ValueError("artifact metadata mismatch")
                    self.projection.bind_ready_artifact_locked(
                        connection,
                        owner_id=context["owner_internal_user_id"],
                        conversation_id=context["conversation_id"],
                        message_id=intent["message_id"],
                        task_id=intent["run_id"],
                        agent_id="hr-bot",
                        artifact={
                            "attachmentId": str(file["attachment_id"]),
                            "artifactKey": intent["artifact_key"],
                            "producerVersionId": intent["producer_version_id"],
                            "displayName": name,
                            "status": "ready",
                        },
                    )
                except (
                    ContentCryptoError,
                    ConversationResultProjectionError,
                    KeyError,
                    TypeError,
                    ValueError,
                ):
                    ready, error = False, "artifact_unavailable"
            elif file and (
                file["state"] in {"rejected", "quarantined", "deleted"}
                or file["result_status"] == "failed"
            ):
                error = "artifact_rejected"
            elif file is None or file["upload_state"] == "uploading":
                grant = connection.execute(
                    "select 1 from platform_attachments.task_grants where grant_id=%s "
                    "and revoked_at is null and expires_at>clock_timestamp()",
                    (intent["grant_id"],),
                ).fetchone()
                if grant is None:
                    # Upload completion may have committed after our first read.
                    # Expiry is not permission to discard already accepted bytes.
                    latest = (
                        None
                        if file is None
                        else connection.execute(
                            "select state from platform_attachments.uploads where attachment_id=%s",
                            (file["attachment_id"],),
                        ).fetchone()
                    )
                    if latest is None or latest["state"] == "uploading":
                        error = "artifact_grant_expired"
            if ready or error:
                status = "ready" if ready else "failed"
                connection.execute(
                    "update platform_control.result_artifact_intents set status=%s,attachment_id=%s,"
                    "last_error_code=%s,updated_at=clock_timestamp() where run_id=%s and intent_index=%s",
                    (
                        status,
                        file["attachment_id"] if ready else None,
                        error,
                        intent["run_id"],
                        intent["intent_index"],
                    ),
                )
                if status != intent["status"]:
                    connection.execute(
                        "update platform_control.conversations set snapshot_version=snapshot_version+1 "
                        "where conversation_id=%s",
                        (context["conversation_id"],),
                    )
                    return 1
            else:
                connection.execute(
                    "update platform_control.result_artifact_intents set next_attempt_at=clock_timestamp()+interval '5 seconds' "
                    "where run_id=%s and intent_index=%s",
                    (intent["run_id"], intent["intent_index"]),
                )
            return 0


def artifact_key(display_name):
    # Shared wire identity with MetaBot's existing attachment transfer adapter.
    return "artifact-" + hashlib.sha256(display_name.encode()).hexdigest()[:24]


def freeze_artifact_intents(connection, event, message_id, command, materials):
    """Called in the authenticated text commit, before terminal grant revocation."""
    intents = event.payload.artifact_intents
    if not intents:
        return
    output = materials["outputWriteGrant"]
    grant = None
    if output is not None:
        grant = connection.execute(
            "select * from platform_attachments.task_grants where task_id=%s "
            "and agent_id='hr-bot' and scope='write_output' and token_sha256=%s "
            "and revoked_at is null and expires_at>clock_timestamp()",
            (event.run_id, hashlib.sha256(output["bearerToken"].encode()).digest()),
        ).fetchone()
    keys = [artifact_key(intent.display_name) for intent in intents]
    for intent, key in zip(intents, keys, strict=True):
        valid = (
            grant is not None
            and intent.task_id == event.run_id
            and str(intent.conversation_id) == command["conversationId"]
            and intent.principal_ref == command["principalRef"]
            and intent.size_bytes <= grant["max_file_bytes"]
            and sum(item.size_bytes for item in intents) <= grant["max_bytes"]
            and len(intents) <= grant["max_files"]
            and keys.count(key) == 1
            and intent.display_name == intent.display_name.strip()
            and len(intent.display_name.encode()) <= 1024
        )
        connection.execute(
            "insert into platform_control.result_artifact_intents "
            "(run_id,source_seq,intent_index,message_id,grant_id,artifact_key,producer_version_id,"
            "declared_mime,size_bytes,expected_sha256,status,last_error_code) "
            "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                event.run_id,
                event.seq,
                intent.index,
                message_id,
                grant["grant_id"] if valid else None,
                key,
                intent.sha256_hex,
                intent.mime_type,
                intent.size_bytes,
                bytes.fromhex(intent.sha256_hex),
                "pending" if valid else "failed",
                None if valid else "artifact_intent_invalid",
            ),
        )


def artifact_enrichment(connection, message_id):
    counts = connection.execute(
        "select count(*) filter(where status='pending') as pending, "
        "count(*) filter(where status='ready') as ready, "
        "count(*) filter(where status='failed') as failed "
        "from platform_control.result_artifact_intents where message_id=%s",
        (message_id,),
    ).fetchone()
    pending, ready, failed = (counts[key] for key in ("pending", "ready", "failed"))
    status = (
        "pending" if pending else "failed" if failed else "ready" if ready else "none"
    )
    if ready and (pending or failed):
        status = "partial"
    return {"status": status, "pending_count": pending, "failed_count": failed}
