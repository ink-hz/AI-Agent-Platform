"""Canonical business-row projection; no identity claims from display metadata."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime
from typing import Any
from uuid import UUID

from app.cloud_replica.models import (
    RawSession,
    RawTraceAggregate,
    RawTurn,
    VerifiedSubject,
)
from app.sync_remote.importer import (
    ImportValidationError,
    _fae_verified_subject,
    _daq_verified_subject,
    normalize_session_subject_link,
)
from app.sync_remote.voc_export import project_voc_rows

from .models import SOURCE_IDENTITIES, SourceReadError

# The native FAE protocol's runtime-failure outcomes and the existing platform
# failure vocabulary. These describe execution failure even when the producer
# persists an explanatory answer. Completion requires an explicit known final
# outcome or native execution status, never merely nonempty text.
_FAILED_EXECUTION_OUTCOMES = frozenset({
    'budget_exhausted', 'max_duration', 'empty_answer', 'truncated_answer',
    'tool_xml_leak', 'unstructured_final', 'invalid_answer_contract',
    'provider_unavailable', 'provider_configuration_error', 'provider_protocol_error',
    'provider_rate_limited', 'provider_error', 'internal_error', 'schema_error',
    'persistence_error', 'authorization_error', 'provider_refusal',
    'unsafe_fail_suspected', 'delivery_failed',
})
_COMPLETED_EXECUTION_OUTCOMES = frozenset({
    'resolved', 'safe_abstained', 'escalate_rd', 'escalate_fae', 'success', 'sent',
})
_NON_SUCCESS_EXECUTION_STATES = frozenset({
    'failed', 'cancelled', 'error', 'timeout', 'pending', 'processing', 'partial', 'unavailable',
})


def _execution_status(outcome: str | None, done: dict) -> str:
    if outcome in _NON_SUCCESS_EXECUTION_STATES:
        derived = outcome
    elif outcome in _FAILED_EXECUTION_OUTCOMES:
        derived = 'failed'
    elif outcome in _COMPLETED_EXECUTION_OUTCOMES:
        derived = 'completed'
    else:
        derived = 'unknown'
    explicit = _text(done.get('status'))
    if explicit is None:
        return derived
    # A contradictory success flag must not hide an explicitly persisted
    # failure/inflight outcome. Other native execution evidence stays intact.
    if explicit in {'success', 'succeeded', 'completed'} and derived in _NON_SUCCESS_EXECUTION_STATES:
        return derived
    return explicit


def timestamp(value: Any, *, optional: bool = False) -> datetime | None:
    if value is None and optional:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
        if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed
    except (ValueError, TypeError):
        raise SourceReadError('source_timestamp_invalid') from None


def _uuid(value: Any) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError):
        raise SourceReadError('source_row_invalid') from None


def _text(value: Any) -> str | None:
    if value is not None and not isinstance(value, str):
        raise SourceReadError('source_row_invalid')
    return value


def _unique(rows: list[dict], *, turn_rows: bool = False) -> dict[str, dict]:
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise SourceReadError('source_row_invalid')
        if row.get('metadata') is not None and not isinstance(row['metadata'], dict):
            raise SourceReadError('source_row_invalid')
        key = _uuid(row.get('id'))
        if key in result:
            raise SourceReadError('source_duplicate_row')
        if turn_rows and (type(row.get('turn_index')) is not int or row['turn_index'] < 0):
            raise SourceReadError('source_row_invalid')
        result[key] = row
    return result


def _legacy_user(row: dict) -> str | None:
    value = row.get('external_user_id')
    if value is None:
        value = row.get('user_id')
    return '***' + str(value)[-4:] if value is not None else None


def _identity(kind: str, row: dict, observed_at: datetime) -> tuple[str | None, VerifiedSubject | None, dict]:
    details = {}
    if kind == 'fae':
        try:
            subject = _fae_verified_subject(row)
        except ImportValidationError:
            raise SourceReadError('source_identity_unverified') from None
        return _legacy_user(row), VerifiedSubject.from_value(subject) if subject else None, details
    if kind == 'admin':
        evidence = dict(row)
        # The existing verifier expects canonical text, as in its JSON exporter.
        for key in ('user_id', 'internal_user_id'):
            if isinstance(evidence.get(key), UUID):
                evidence[key] = str(evidence[key])
        link = normalize_session_subject_link(evidence, observed_at)
        if link:
            owner = link.values['internal_user_id']
            details['verified_internal_user_id'] = owner
            return 'admin:' + hashlib.md5(owner.encode()).hexdigest(), None, details
        return _legacy_user(row), None, details
    owner = row.get('owner_subject_id')
    if kind == 'daq':
        subject = _daq_verified_subject(row)
        # Preserve the established HMAC input; adding attribution never rekeys history.
        return (('daq:' + hashlib.md5(subject['subject_id'].encode()).hexdigest()
                 if subject else None), VerifiedSubject.from_value(subject) if subject else None, details)
    return ('voc:' + hashlib.md5(_uuid(owner).encode()).hexdigest() if owner else None), None, details


def project_rows(kind: str, tables: dict[str, list[dict]]) -> tuple[RawSession, ...]:
    if kind not in SOURCE_IDENTITIES:
        raise SourceReadError('source_profile_invalid')
    prefix = 'admin_' if kind == 'admin' else ''
    expected = {prefix + 'chat_sessions', prefix + 'chat_turns'}
    if kind != 'voc':
        expected |= {prefix + 'turn_feedback', prefix + 'turn_reviews'}
    if set(tables) != expected:
        raise SourceReadError('source_snapshot_incomplete')
    sessions = _unique(tables[prefix + 'chat_sessions'])
    turns = _unique(tables[prefix + 'chat_turns'], turn_rows=True)
    grouped = defaultdict(list)
    indices = set()
    for tid, row in turns.items():
        sid = _uuid(row.get('session_id'))
        if sid not in sessions or (sid, row['turn_index']) in indices:
            raise SourceReadError('source_snapshot_incomplete')
        indices.add((sid, row['turn_index']))
        grouped[sid].append((tid, row))
    feedback, reviews = defaultdict(list), defaultdict(list)
    for table, dest, status_field in (
        (prefix + 'turn_feedback', feedback, 'rating'),
        (prefix + 'turn_reviews', reviews, 'verdict' if kind == 'admin' else 'review_status'),
    ):
        for _id, row in _unique(tables.get(table, [])).items():
            tid = _uuid(row.get('turn_id'))
            if tid not in turns:
                raise SourceReadError('source_snapshot_incomplete')
            when = timestamp(row.get('updated_at') or row.get('created_at'))
            status = _text(row.get(status_field))
            if status is None:
                raise SourceReadError('source_row_invalid')
            if dest is feedback:
                status = {'good': 'positive', 'bad': 'negative'}.get(status, 'other')
            dest[tid].append((when, _id, status))
    result = []
    agent, family, _schema = SOURCE_IDENTITIES[kind]
    for sid, row in sorted(sessions.items()):
        created = timestamp(row.get('created_at'))
        active = timestamp(row.get('last_active_at'))
        identity, verified, details = _identity(kind, row, created)
        projected_turns = []
        for tid, turn in sorted(grouped[sid], key=lambda item: (item[1]['turn_index'], item[0])):
            created_at = timestamp(turn.get('created_at'))
            question_at = timestamp(turn.get('question_at'), optional=True)
            answer_at = timestamp(turn.get('answer_at'), optional=True)
            question, answer = _text(turn.get('question')), _text(turn.get('answer'))
            outcome = _text(turn.get('outcome'))
            fallback = turn.get('fallback_used')
            duration = turn.get('duration_ms')
            if ((fallback is not None and type(fallback) is not bool)
                    or (duration is not None and (type(duration) is not int or duration < 0))):
                raise SourceReadError('source_row_invalid')
            done = turn.get('done')
            if done is None:
                done = {}
            if not isinstance(done, dict):
                raise SourceReadError('source_row_invalid')
            trace = None
            if kind != 'voc':
                trace = RawTraceAggregate(status=_execution_status(outcome, done),
                    duration_ms=duration, engine=_text(done.get('engine')), backend=_text(done.get('backend')),
                    model=_text(done.get('actual_provider_model')), input_tokens=None, output_tokens=None,
                    cost_usd=None, error_class=_text(done.get('error_type')))
            sources = turn.get('sources')
            if sources is None:
                sources = []
            if not isinstance(sources, list) or any(not isinstance(value, dict) for value in sources):
                raise SourceReadError('source_row_invalid')
            projected_turns.append(RawTurn(
                turn_key=kind + ':' + tid, turn_index=turn['turn_index'], question=question, answer=answer,
                created_at=created_at, question_at=question_at, answer_at=answer_at,
                question_time_status='exact' if question_at else 'unavailable',
                answer_time_status='exact' if answer_at else 'unavailable', outcome=outcome,
                fallback_used=fallback, duration_ms=duration,
                feedback_sentiments=tuple(value[2] for value in sorted(feedback[tid])),
                review_statuses=tuple(value[2] for value in sorted(reviews[tid])), trace=trace,
                sources=tuple(sources)))
        title = _text(row.get('conversation_title'))
        if title is None and projected_turns:
            title = (projected_turns[0].question or '')[:180] or None
        name = department = None
        if kind == 'voc':
            name, department = _text(row.get('primary_sender_name')), _text(row.get('primary_sender_department'))
        elif kind == 'admin' and details.get('verified_internal_user_id'):
            # This is migration 014's exact trust gate: server-minted session
            # evidence precedes metadata display. Metadata alone never owns a session.
            metadata = row.get('metadata') or {}
            name = (_text(metadata.get('display_name')) or '').strip(' ') or None
            department = (_text(metadata.get('primary_department')) or '').strip(' ') or None
        result.append(RawSession(session_key=kind + ':' + sid, agent_id=agent, source_kind=family,
            channel=_text(row.get('channel')), title=title, user_identity=identity,
            primary_sender_name=name, primary_sender_department=department,
            created_at=created, last_active_at=active, turns=tuple(projected_turns),
            details=details, verified_subject=verified))
    return tuple(result)


def project_voc_snapshot(rows: list[dict]) -> tuple[RawSession, ...]:
    try:
        return project_rows('voc', project_voc_rows(rows))
    except SourceReadError:
        raise
    except (ValueError, KeyError, TypeError, OverflowError):
        raise SourceReadError('source_row_invalid') from None
