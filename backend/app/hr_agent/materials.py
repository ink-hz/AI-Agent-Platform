"""Private immutable UTF-8 material views over the real attachment store."""

import hashlib
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from psycopg.rows import dict_row

from app.attachments.conversation_repository import attachment_object_subject
from app.execution_relay.content_crypto import SealedContent

from .types import (
    HrAgentProblem,
    MaterialText,
    content_sha256,
    problem,
    validate_contract,
)


@dataclass(frozen=True)
class MaterialAsset:
    object_ref: str = field(repr=False)
    immutable_locator: str = field(repr=False)
    size_bytes: int
    sha256: bytes = field(repr=False)
    derivative_kind: str | None = None


class MaterialService:
    def __init__(
        self,
        connection_factory,
        attachment_codec,
        immutable_store,
        *,
        temporary_root=None,
    ):
        self.connection_factory = connection_factory
        self.codec = attachment_codec
        self.store = immutable_store
        self.temporary_root = temporary_root

    def _row(self, owner_id, attachment_id):
        if not isinstance(owner_id, UUID):
            raise problem("scope_denied", http_status=401)
        try:
            attachment_id = UUID(str(attachment_id))
            with (
                self.connection_factory() as connection,
                connection.cursor(row_factory=dict_row) as cursor,
            ):
                cursor.execute(
                    "SELECT a.*,u.write_attempt_id,EXISTS (SELECT 1 FROM platform_attachments.erasure_jobs e WHERE e.attachment_id=a.attachment_id) AS erasing FROM platform_attachments.attachments a LEFT JOIN platform_attachments.uploads u USING (attachment_id) WHERE a.attachment_id=%s AND a.owner_internal_user_id=%s",
                    (attachment_id, owner_id),
                )
                row = cursor.fetchone()
            if row is None:
                raise problem("not_found", http_status=404)
            if (
                row["state"] == "deleted"
                or row["erasing"]
                or row["retained_until"] <= datetime.now(UTC)
            ):
                raise problem("reference_unavailable", http_status=410)
            return row
        except HrAgentProblem:
            raise
        except ValueError:
            raise problem("not_found", http_status=404) from None
        except Exception:  # noqa: BLE001 - private storage and crypto errors are opaque
            raise problem(
                "temporarily_unavailable", retryable=True, http_status=503
            ) from None

    def _view(self, owner_id, row):
        view = {
            "attachment_id": str(row["attachment_id"]),
            "state": row["state"],
            "visibility": {"kind": "private", "subject_id": str(owner_id)},
            "original_ref": None,
            "text_ref": None,
            "parse_state": "not_started",
            "parser_release": None,
            "coverage_complete": False,
        }
        if row["state"] in ("validating", "scanning"):
            view["parse_state"] = "processing"
        if row["state"] != "ready":
            return view, None
        if not row["sha256"] or not row["immutable_locator"]:
            raise problem("temporarily_unavailable", http_status=503)
        digest = bytes(row["sha256"]).hex()
        original = {
            "kind": "material",
            "id": f"{row['attachment_id']}:original",
            "revision": digest,
            "sha256": content_sha256(
                {
                    "byte_sha256": digest,
                    "detected_mime": row["detected_mime"],
                    "size_bytes": row["size_bytes"],
                }
            ),
        }
        view["original_ref"] = original
        if row["detected_mime"] != "text/plain":
            view["parse_state"] = "unsupported"
            return view, None
        try:
            reference = self.codec.unseal_json(
                attachment_object_subject(
                    row["attachment_id"], row["write_attempt_id"]
                ),
                SealedContent(
                    bytes(row["object_ref_ciphertext"]), row["object_ref_key_version"]
                ),
            )
            if set(reference) != {"object_ref"}:
                raise ValueError()
            asset = MaterialAsset(
                reference["object_ref"],
                row["immutable_locator"],
                row["size_bytes"],
                bytes(row["sha256"]),
            )
            with tempfile.TemporaryDirectory(
                prefix="hr-material-", dir=self.temporary_root
            ) as directory:
                path = self.store.stage_verified(asset, directory)
                data = Path(path).read_bytes()
            if (
                len(data) != asset.size_bytes
                or hashlib.sha256(data).digest() != asset.sha256
            ):
                raise ValueError()
        except Exception:  # noqa: BLE001 - private storage and crypto errors are opaque
            raise problem(
                "temporarily_unavailable", retryable=True, http_status=503
            ) from None
        # Recheck current visibility after object I/O, before returning a usable ref.
        current = self._row(owner_id, row["attachment_id"])
        if current["state"] != "ready" or any(
            current[name] != row[name]
            for name in (
                "sha256",
                "detected_mime",
                "size_bytes",
                "immutable_locator",
                "write_attempt_id",
            )
        ):
            raise problem("reference_unavailable", http_status=410)
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeError:
            view["parse_state"] = "failed"
            return view, None
        payload = {
            "original_ref": original,
            "parser_release": "utf8-v1",
            "text": text,
            "coverage_complete": True,
        }
        ref = {
            "kind": "material",
            "id": f"{row['attachment_id']}:text",
            "revision": digest + ":utf8-v1",
            "sha256": content_sha256(payload),
        }
        view.update(
            text_ref=ref,
            parse_state="ready",
            parser_release="utf8-v1",
            coverage_complete=True,
        )
        return view, MaterialText(ref, original, "utf8-v1", text, True)

    def resolve(self, owner_id, attachment_id):
        view, _ = self._view(owner_id, self._row(owner_id, attachment_id))
        return validate_contract("MaterialView", view)

    def read_text(self, owner_id, ref):
        ref = validate_contract("ExactRef", ref)
        if ref["kind"] != "material":
            raise problem("invalid_input")
        view, text = self._view(
            owner_id, self._row(owner_id, ref["id"].split(":", 1)[0])
        )
        if text is None:
            if view["parse_state"] == "unsupported" or ref == view["original_ref"]:
                raise problem("unsupported_kind")
            raise problem("reference_unavailable", http_status=410)
        if ref != text.ref:
            raise problem("reference_unavailable", http_status=410)
        return text


def build_material_service(
    database_url, content_keyring_file, immutable_store, *, temporary_root=None
):
    """API and worker share the same owner-bound attachment read adapter."""
    import psycopg

    from app.control_plane.crypto import IdentityKeyring
    from app.control_plane.dsn import validate_control_dsn
    from app.execution_relay.content_crypto import ContentCodec

    validate_control_dsn(database_url, purpose="app")
    codec = ContentCodec(
        IdentityKeyring.from_file(
            content_keyring_file,
            expected_purpose="platform-content-encryption",
            expected_key_length=32,
        )
    )

    def connection():
        return psycopg.connect(
            database_url,
            connect_timeout=3,
            options="-c statement_timeout=10000 -c timezone=UTC",
        )

    return MaterialService(
        connection, codec, immutable_store, temporary_root=temporary_root
    )


def build_material_service_from_environment(environment, *, temporary_root=None):
    """Load only attachment settings; no Relay, DingTalk, or unrelated app setup."""
    from types import SimpleNamespace

    from app.attachments.download_service import S3ImmutableAttachmentStore
    from app.local_secrets import read_secret_file

    flag = environment.get("PLATFORM_CONVERSATION_ATTACHMENT_ENABLED", "0")
    if flag == "0":
        return None
    if flag != "1":
        raise ValueError("HR attachment configuration unavailable")
    try:
        config = SimpleNamespace(
            attachment_s3_endpoint=environment["PLATFORM_ATTACHMENT_S3_ENDPOINT"],
            attachment_s3_bucket=environment["PLATFORM_ATTACHMENT_S3_BUCKET"],
            attachment_s3_access_key_file=environment[
                "PLATFORM_ATTACHMENT_S3_ACCESS_KEY_FILE"
            ],
            attachment_s3_secret_key_file=environment[
                "PLATFORM_ATTACHMENT_S3_SECRET_KEY_FILE"
            ],
        )
        database_url = read_secret_file(
            environment["PLATFORM_ATTACHMENT_CONTROL_DATABASE_URL_FILE"]
        )
        return build_material_service(
            database_url,
            environment["PLATFORM_CONTENT_ENCRYPTION_KEYRING_FILE"],
            S3ImmutableAttachmentStore.from_config(config),
            temporary_root=temporary_root,
        )
    except Exception:  # noqa: BLE001 - never expose storage credentials or DSNs
        raise ValueError("HR attachment configuration unavailable") from None
