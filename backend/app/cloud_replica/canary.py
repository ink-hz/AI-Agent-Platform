from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from .crypto import BatchSigner
from .exporter import _atomic_create, build_session_record
from .models import RawAttachment, RawSession, RawTurn
from .protocol import BatchState, encode_batch
from .sanitize import SanitizationPolicy, sanitize_session


_CUSTOMER = "CANARY-CUSTOMER-7F31"
_CANDIDATE = "CANARY-CANDIDATE-8A42"
_PROJECT = "CANARY-PROJECT-9B53"
_PRODUCT = "CANARY-PRODUCT-0C64"
_ADDRESS = "深圳市南山区验收路88号"
_ATTACHMENT = "CANARY-RESUME-1D75.pdf"

# Live credentials must never survive export. These are the acceptance markers.
CANARY_VALUES = (
    "Bearer canaryCredential1234567890",
    "AKIAIOSFODNN7EXAMPLE",
    "ghp_CANARYaaaaaaaaaaaaaaaaaaaaaaaa",
    "password=canarySecret",
)
# Business content must survive export verbatim: the cloud replica is readable
# only by authenticated administrators, and rewriting it corrupts the record.
CANARY_CONTENT_VALUES = (
    "13900001234",
    "cloud-canary@example.invalid",
    "11010519491231002X",
    "/Users/cloud-canary/private.txt",
    "https://example.invalid/private?X-Amz-Signature=canary",
    _CUSTOMER,
    _CANDIDATE,
    _PROJECT,
    _PRODUCT,
    _ADDRESS,
    _ATTACHMENT,
)


def create_synthetic_canary(
    output_path: str | Path,
    *,
    policy: SanitizationPolicy,
    identity_key: bytes,
    signer: BatchSigner,
    created_at: datetime,
) -> None:
    output = Path(output_path)
    if not output.is_absolute() or output.is_symlink():
        raise RuntimeError("canary output unavailable")
    now = created_at.astimezone(UTC)
    raw = RawSession(
        session_key="synthetic-canary-session",
        agent_id="hr-bot",
        source_kind="metabot",
        channel="web",
        title=f"{_CUSTOMER} {_PROJECT}",
        user_identity="on_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        primary_sender_name="验收用户",
        primary_sender_department="验收",
        created_at=now,
        last_active_at=now,
        turns=(
            RawTurn(
                turn_key="synthetic-canary-turn",
                turn_index=1,
                question=" | ".join((*CANARY_VALUES, *CANARY_CONTENT_VALUES)),
                answer=" | ".join(reversed(CANARY_CONTENT_VALUES)),
                created_at=now,
                outcome="success",
                attachments=(
                    RawAttachment(
                        attachment_id="synthetic-canary-attachment",
                        direction="user_input",
                        display_name=_ATTACHMENT,
                        mime_type="application/pdf",
                        size_bytes=1024,
                        received_or_generated_at=now,
                        archive_status="available",
                        delivery_status="not_applicable",
                    ),
                ),
            ),
        ),
    )
    record = build_session_record(raw, sanitize_session(raw, policy), identity_key)
    state = BatchState(
        source_instance_id="synthetic-acceptance",
        sequence=1,
        previous_digest=None,
        lower_watermark=now - timedelta(seconds=1),
        upper_watermark=now,
        created_at=now,
        expires_at=now + timedelta(minutes=15),
        sanitizer_policy_version=policy.version,
    )
    _atomic_create(output, encode_batch((record,), state, signer))
