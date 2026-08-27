from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
import unicodedata
from typing import Callable

import yaml

from .models import (
    OperationEventProjection,
    RawAttachment,
    RawSession,
    SanitizedAttachment,
    SanitizedSessionRecord,
    SanitizedText,
    SanitizedTraceAggregate,
    SanitizedTurnRecord,
    ReviewInboxProjection,
    ReviewIssueProjection,
    ReviewFeedbackTotalsProjection,
)
from .crypto import stable_id


_DICTIONARY_GROUPS = (
    "customers",
    "candidates",
    "projects",
    "products",
    "addresses",
)


@dataclass(frozen=True, slots=True)
class SanitizationPolicy:
    version: str = "2026-08-17-raw"
    customers: tuple[str, ...] = ()
    candidates: tuple[str, ...] = ()
    projects: tuple[str, ...] = ()
    products: tuple[str, ...] = ()
    addresses: tuple[str, ...] = ()

    @classmethod
    def from_private_file(
        cls, path_value: str | Path, *, version: str = "2026-08-17-raw"
    ) -> SanitizationPolicy:
        path = Path(path_value)
        if not path.is_absolute():
            raise RuntimeError("sanitizer dictionary must use an absolute path")
        try:
            metadata = path.lstat()
        except OSError as error:
            raise RuntimeError(
                "sanitizer dictionary must be a regular mode 0600 file"
            ) from error
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError(
                "sanitizer dictionary must be a regular mode 0600 file"
            )
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise RuntimeError("sanitizer dictionary must use mode 0600")
        if metadata.st_uid != os.getuid():
            raise RuntimeError("sanitizer dictionary must be owned by the service user")
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeError, yaml.YAMLError) as error:
            raise RuntimeError("sanitizer dictionary is invalid") from error
        if not isinstance(loaded, dict) or set(loaded) - set(_DICTIONARY_GROUPS):
            raise RuntimeError("sanitizer dictionary is invalid")
        normalized: dict[str, tuple[str, ...]] = {}
        for group in _DICTIONARY_GROUPS:
            values = loaded.get(group, [])
            if not isinstance(values, list) or not all(
                isinstance(value, str) and value.strip() for value in values
            ):
                raise RuntimeError("sanitizer dictionary is invalid")
            normalized[group] = tuple(dict.fromkeys(value.strip() for value in values))
        return cls(version=version, **normalized)


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    value = "".join(
        character
        for character in value
        if character in {"\n", "\t"} or not unicodedata.category(character).startswith("C")
    )
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return value.strip()


def _replace_pattern(
    pattern: re.Pattern[str],
    text: str,
    replacement: str | Callable[[re.Match[str]], str],
) -> str:
    return pattern.sub(replacement, text)


# Replica text is exported verbatim. The cloud copy is reachable only by
# authenticated administrators who are entitled to read the business content, so
# names, contacts, links and paths are preserved exactly as the Agent produced
# them. Live machine credentials are the sole exception: no reviewer needs them
# and they must not be copied into the cloud database or its encrypted backups.
_CREDENTIAL_PATTERNS = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b", re.IGNORECASE),
    re.compile(
        r"\b(?:password|client_secret|api_key|access_token)\s*[:=]\s*\S+",
        re.IGNORECASE,
    ),
)
_CREDENTIAL_PLACEHOLDER = "[凭证]"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sanitized(value: str, safe: bool, policy: SanitizationPolicy) -> SanitizedText:
    return SanitizedText(
        text=value,
        safe=safe,
        sha256=_digest(value),
        policy_version=policy.version,
    )


def _strip_credentials(value: str) -> str:
    for pattern in _CREDENTIAL_PATTERNS:
        value = _replace_pattern(pattern, value, _CREDENTIAL_PLACEHOLDER)
    return value


def _sanitize_text(text: str | None, policy: SanitizationPolicy) -> SanitizedText:
    return _sanitized(_strip_credentials(_normalize(text or "")), True, policy)


def sanitize_text(
    text: str, policy: SanitizationPolicy, scope: str
) -> SanitizedText:
    # Scope is deliberately not persisted; it only documents the call site.
    del scope
    return _sanitize_text(text, policy)



def sanitize_management_projection(
    raw: object,
    policy: SanitizationPolicy,
    identity_key: bytes,
) -> dict[str, object]:
    if len(identity_key) != 32:
        raise ValueError("invalid identity key")
    agent_id = getattr(raw, "agent_id", None)
    if _safe_identifier(agent_id) != agent_id:
        raise ValueError("unsafe management projection")
    if isinstance(raw, ReviewIssueProjection):
        title = sanitize_text(raw.title, policy, "review-issue-title")
        owner = (
            sanitize_text(raw.owner_display, policy, "review-owner")
            if raw.owner_display
            else None
        )
        return {
            "kind": "review_issue_projection",
            "key": str(raw.issue_id),
            "agent_id": raw.agent_id,
            "status": _safe_identifier(raw.status) or "unknown",
            "priority": _safe_identifier(raw.priority) or "unknown",
            "title": {"text": title.text},
            "failure_layer": _safe_identifier(raw.failure_layer),
            "owner_display": owner.text if owner and owner.safe else None,
            "linked_turn_count": max(raw.linked_turn_count, 0),
            "updated_at": raw.updated_at,
            "sanitizer_policy_version": policy.version,
        }
    if isinstance(raw, ReviewInboxProjection):
        return {
            "kind": "review_inbox_projection",
            "key": stable_id(
                "review-inbox", f"{raw.agent_id}:{raw.turn_key}", identity_key
            ),
            "agent_id": raw.agent_id,
            "turn_key": stable_id("turn", raw.turn_key, identity_key),
            "feedback_count": max(raw.feedback_count, 0),
            "first_feedback_at": raw.first_feedback_at,
            "sanitizer_policy_version": policy.version,
        }
    if isinstance(raw, ReviewFeedbackTotalsProjection):
        return {
            "kind": "review_feedback_totals_projection",
            "key": stable_id("review-feedback-totals", raw.agent_id, identity_key),
            "agent_id": raw.agent_id,
            "feedback_rows": max(raw.feedback_rows, 0),
            "negative_rows": max(raw.negative_rows, 0),
            "negative_turns": max(raw.negative_turns, 0),
            "positive_rows": max(raw.positive_rows, 0),
            "observed_at": raw.observed_at,
            "sanitizer_policy_version": policy.version,
        }
    if isinstance(raw, OperationEventProjection):
        title = sanitize_text(raw.title, policy, "operation-title")
        summary = sanitize_text(raw.summary, policy, "operation-summary")
        return {
            "kind": "operation_event_projection",
            "key": stable_id("operation-event", raw.event_id, identity_key),
            "agent_id": raw.agent_id,
            "event_type": _safe_identifier(raw.event_type) or "unknown",
            "event_family": _safe_identifier(raw.event_family) or "execution",
            "severity": _safe_identifier(raw.severity) or "unknown",
            "status": _safe_identifier(raw.status) or "historical",
            "title": {"text": title.text},
            "summary": {"text": summary.text},
            "source_kind": _safe_identifier(raw.source_kind) or "unknown",
            "occurred_at": raw.occurred_at,
            "sanitizer_policy_version": policy.version,
        }
    raise ValueError("unsupported management projection")


def _size_bucket(size_bytes: int | None) -> str:
    if size_bytes is None or size_bytes < 0:
        return "unknown"
    if size_bytes < 100 * 1024:
        return "<100 KiB"
    if size_bytes < 1024 * 1024:
        return "100 KiB–1 MiB"
    if size_bytes < 10 * 1024 * 1024:
        return "1–10 MiB"
    return ">=10 MiB"


def _attachment_category(attachment: RawAttachment) -> str:
    mime_type = (attachment.mime_type or "").lower()
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("video/"):
        return "video"
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type in {"application/pdf", "application/msword"} or "document" in mime_type:
        return "document"
    if mime_type.startswith("text/"):
        return "text"
    return "binary"


def _safe_enum(value: str | None, allowed: set[str]) -> str | None:
    return value if value in allowed else None


def _safe_attachment_direction(value: str | None) -> str:
    return {
        "user_input": "incoming",
        "agent_output": "generated",
        "source": "source",
        "generated": "generated",
        "incoming": "incoming",
        "outgoing": "outgoing",
    }.get(value or "", "unknown")


def _safe_attachment_archive_status(value: str | None) -> str | None:
    return {
        "available": "archived",
        "source_unavailable": "unavailable",
        "pending": "pending",
        "archived": "archived",
        "expired": "expired",
        "failed": "failed",
        "unavailable": "unavailable",
    }.get(value or "")


def _safe_attachment_delivery_status(value: str | None) -> str | None:
    return {
        "not_applicable": "unavailable",
        "pending": "pending",
        "delivered": "delivered",
        "failed": "failed",
        "unavailable": "unavailable",
    }.get(value or "")


def _safe_identifier(value: str | None) -> str | None:
    if value and re.fullmatch(r"[A-Za-z0-9._-]{1,80}", value):
        return value
    return None


def _safe_count(value: int | None) -> int | None:
    return max(value, 0) if isinstance(value, int) else None


def _sanitize_trace(trace):
    if trace is None:
        return None
    model = _safe_identifier(trace.model)
    model_family = model.split("-")[0] if model else None
    return SanitizedTraceAggregate(
        status=_safe_enum(
            trace.status,
            {"success", "failed", "partial", "timeout", "running", "unknown"},
        ),
        duration_ms=_safe_count(trace.duration_ms),
        engine=_safe_identifier(trace.engine),
        backend=_safe_identifier(trace.backend),
        model_family=model_family,
        input_tokens=_safe_count(trace.input_tokens),
        output_tokens=_safe_count(trace.output_tokens),
        cost_usd=max(float(trace.cost_usd), 0.0)
        if isinstance(trace.cost_usd, (int, float))
        else None,
        error_class=_safe_identifier(trace.error_class),
        tool_categories=tuple(
            dict.fromkeys(
                category
                for category in trace.tool_categories
                if _safe_identifier(category)
            )
        ),
    )


def sanitize_session(
    raw: RawSession, policy: SanitizationPolicy
) -> SanitizedSessionRecord:
    title = _sanitize_text(raw.title, policy)
    turns: list[SanitizedTurnRecord] = []
    for turn in sorted(raw.turns, key=lambda item: item.turn_index):
        question = _sanitize_text(turn.question, policy)
        answer = _sanitize_text(turn.answer, policy)
        attachments = tuple(
            SanitizedAttachment(
                display_label=_strip_credentials(
                    _normalize(attachment.display_name or "")
                )
                or f"附件 {index}",
                category=_attachment_category(attachment),
                mime_type=(attachment.mime_type or "")[:127] or None,
                size_bucket=_size_bucket(attachment.size_bytes),
                direction=_safe_attachment_direction(attachment.direction),
                archive_status=_safe_attachment_archive_status(
                    attachment.archive_status
                ),
                delivery_status=_safe_attachment_delivery_status(
                    attachment.delivery_status
                ),
                occurred_at=attachment.received_or_generated_at,
            )
            for index, attachment in enumerate(turn.attachments, start=1)
        )
        turns.append(
            SanitizedTurnRecord(
                turn_index=turn.turn_index,
                question=question,
                answer=answer,
                created_at=turn.created_at,
                question_at=turn.question_at,
                answer_at=turn.answer_at,
                question_time_status=_safe_enum(
                    turn.question_time_status, {"exact", "estimated", "unavailable"}
                )
                or "unavailable",
                answer_time_status=_safe_enum(
                    turn.answer_time_status, {"exact", "estimated", "unavailable"}
                )
                or "unavailable",
                outcome=_safe_enum(
                    turn.outcome,
                    {"success", "failed", "partial", "timeout", "unknown"},
                ),
                fallback_used=turn.fallback_used,
                duration_ms=max(turn.duration_ms, 0)
                if isinstance(turn.duration_ms, int)
                else None,
                attachments=attachments,
                trace=_sanitize_trace(turn.trace),
            )
        )
    return SanitizedSessionRecord(
        agent_id=raw.agent_id,
        source_kind=_safe_enum(raw.source_kind, {"metabot", "fae", "admin"})
        or "unknown",
        channel=_safe_enum(raw.channel, {"feishu", "dingtalk", "web", "api"}),
        title=title,
        primary_sender_name=raw.primary_sender_name,
        primary_sender_department=raw.primary_sender_department,
        created_at=raw.created_at,
        last_active_at=raw.last_active_at,
        turns=tuple(turns),
        sanitizer_policy_version=policy.version,
    )
