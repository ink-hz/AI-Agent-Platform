"""Complete bounded business snapshots from four explicitly identified databases."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import time
from collections.abc import Callable
from datetime import UTC, datetime

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from .models import AgentSnapshot, SOURCE_IDENTITIES, SourceProfile, SourceReadError
from .projection import project_rows, project_voc_snapshot

# Only columns needed for the established business/identity projection are read.
SESSION_COLUMNS = ('id', 'external_session_id', 'channel', 'user_id', 'external_user_id',
    'conversation_title', 'created_at', 'last_active_at', 'metadata')
TURN_COLUMNS = ('id', 'session_id', 'turn_index', 'question', 'answer', 'created_at',
    'question_at', 'answer_at', 'outcome', 'fallback_used', 'duration_ms', 'sources', 'done', 'metadata')
FEEDBACK_COLUMNS = ('id', 'turn_id', 'rating', 'created_at')
REVIEW_COLUMNS = ('id', 'turn_id', 'review_status', 'created_at', 'updated_at')
VOC_COLUMNS = {
    ('voc_raw', 'inbound_messages'): ('id', 'sender_internal_user_id', 'conversation_id',
        'conversation_type', 'sender_name', 'sender_dept', 'received_at', 'dingtalk_created_at', 'text_content'),
    ('voc_raw', 'interaction_jobs'): ('inbound_message_id', 'status'),
    ('voc_core', 'outbox_messages'): ('source_inbound_message_id', 'status', 'content', 'sent_at'),
    ('voc_meta', 'schema_migrations'): ('name',),
}
VOC_QUERY = sql.SQL('''
select inbound.id, inbound.sender_internal_user_id as internal_user_id,
 inbound.conversation_id, inbound.conversation_type,
 inbound.sender_name, inbound.sender_dept as sender_department,
 inbound.received_at, inbound.dingtalk_created_at as question_at,
 inbound.text_content as question,
 case when delivery.status='sent' then delivery.content else '' end as answer,
 case when delivery.status='sent' then delivery.sent_at end as answer_at,
 job.status as job_status, delivery.status as delivery_status
from voc_raw.inbound_messages inbound
left join voc_raw.interaction_jobs job on job.inbound_message_id=inbound.id
left join voc_core.outbox_messages delivery on delivery.source_inbound_message_id=inbound.id
''')


def _tables(profile: SourceProfile) -> dict[tuple[str, str], tuple[str, ...]]:
    if profile.kind == 'voc':
        return VOC_COLUMNS
    prefix = 'admin_' if profile.kind == 'admin' else ''
    session = SESSION_COLUMNS + (('internal_user_id', 'verification_method', 'verified_at')
        if profile.kind == 'admin' else ('owner_subject_id', 'owner_subject_type'))
    reviews = ('id', 'turn_id', 'verdict', 'created_at') if profile.kind == 'admin' else REVIEW_COLUMNS
    result = {
        (profile.schema, prefix + 'chat_sessions'): session,
        (profile.schema, prefix + 'chat_turns'): TURN_COLUMNS,
        (profile.schema, prefix + 'turn_feedback'): FEEDBACK_COLUMNS,
        (profile.schema, prefix + 'turn_reviews'): reviews,
    }
    if profile.kind == 'daq':
        result[(profile.schema, 'daq_installation_identity')] = ('singleton', 'agent_id', 'database_name')
    return result


def _read_dsn(path_value: str) -> str:
    descriptor = None
    try:
        descriptor = os.open(path_value, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_uid != os.getuid() or metadata.st_size > 16384):
            raise ValueError
        with os.fdopen(descriptor, encoding='utf-8') as stream:
            descriptor = None
            value = stream.read(16385).strip()
        if not value or len(value) > 16384 or '\n' in value or '\x00' in value:
            raise ValueError
        return value
    except (OSError, ValueError, UnicodeError):
        raise SourceReadError('source_credentials_invalid') from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


class DatabaseAgentSource:
    def __init__(self, profile: SourceProfile, *, connection_factory: Callable = psycopg.connect,
                 monotonic: Callable[[], float] = time.monotonic,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 max_rows: int = 100_000, max_bytes: int = 64 * 1024 * 1024,
                 budget_ms: int = 10_000):
        if any(type(value) is not int or value < 1 for value in (max_rows, max_bytes, budget_ms)):
            raise ValueError('source_bounds_invalid')
        self.profile = profile
        self._connect, self._monotonic, self._clock = connection_factory, monotonic, clock
        self.max_rows, self.max_bytes, self.budget_ms = max_rows, max_bytes, budget_ms

    def read_snapshot(self) -> AgentSnapshot:
        started = self._monotonic()
        rows_used, bytes_used = 0, 0

        def remaining() -> int:
            result = self.budget_ms - int((self._monotonic() - started) * 1000)
            if result <= 0:
                raise SourceReadError('source_time_limit')
            return result

        try:
            dsn = _read_dsn(self.profile.dsn_file)
            remaining()
            schema = self.profile.schema if self.profile.kind != 'voc' else 'voc_raw'
            options = (f'-c default_transaction_read_only=on -c statement_timeout={self.budget_ms}'
                f' -c lock_timeout={self.budget_ms} -c search_path={schema}')
            with self._connect(dsn, options=options, connect_timeout=max(1, min(5, self.budget_ms // 1000)),
                               row_factory=dict_row) as connection, connection.transaction():
                cursor = connection.cursor()
                cursor.execute('set transaction isolation level repeatable read, read only')

                def execute(query, params=None):
                    cursor.execute("select set_config('statement_timeout', %s, true)", (str(remaining()),))
                    cursor.execute(query, params)
                    return cursor

                identity = execute('select current_database() as database, current_schema() as schema').fetchone()
                if identity != {'database': self.profile.expected_database, 'schema': schema}:
                    raise SourceReadError('source_identity_unverified')
                required = _tables(self.profile)
                for (table_schema, table), columns in required.items():
                    actual = execute('''select column_name, udt_name from information_schema.columns
                        where table_schema=%s and table_name=%s''', (table_schema, table)).fetchall()
                    actual_types = {row['column_name']: row['udt_name'] for row in actual}
                    if not set(columns).issubset(actual_types):
                        raise SourceReadError('source_schema_unverified')
                    # Empty or malformed installations must fail too, without relying on row content.
                    for column in columns:
                        if column.endswith('_at') and actual_types[column] != 'timestamptz':
                            raise SourceReadError('source_schema_unverified')
                        if column in {'metadata', 'sources', 'done'} and actual_types[column] not in {'json', 'jsonb'}:
                            raise SourceReadError('source_schema_unverified')
                if self.profile.kind == 'daq':
                    marker = execute('''select agent_id, database_name=current_database() as valid
                        from daq_fae.daq_installation_identity where singleton''').fetchall()
                    if marker != [{'agent_id': 'ai-daq-fae-agent', 'valid': True}]:
                        raise SourceReadError('source_identity_unverified')
                if self.profile.kind == 'voc':
                    marker = execute("select count(*) as count from voc_meta.schema_migrations where name='020_employee_chatbot_experience.sql'").fetchone()
                    if marker['count'] != 1:
                        raise SourceReadError('source_identity_unverified')

                def fetch_bounded(query):
                    nonlocal rows_used, bytes_used
                    measurement = execute(sql.SQL('select count(*) as count, coalesce(sum(octet_length(row_to_json(snapshot)::text)),0) as bytes from ({}) snapshot').format(query)).fetchone()
                    rows_used += measurement['count']
                    bytes_used += measurement['bytes']
                    if rows_used > self.max_rows:
                        raise SourceReadError('source_row_limit')
                    if bytes_used > self.max_bytes:
                        raise SourceReadError('source_body_limit')
                    values = execute(query).fetchall()
                    if len(values) != measurement['count']:
                        raise SourceReadError('source_snapshot_incomplete')
                    remaining()
                    return values

                if self.profile.kind == 'voc':
                    sessions = project_voc_snapshot(fetch_bounded(VOC_QUERY))
                else:
                    tables = {}
                    for (table_schema, table), columns in required.items():
                        if table == 'daq_installation_identity':
                            continue
                        query = sql.SQL('select {} from {}.{} order by id').format(
                            sql.SQL(',').join(map(sql.Identifier, columns)), sql.Identifier(table_schema), sql.Identifier(table))
                        tables[table] = fetch_bounded(query)
                    if self.profile.kind == 'daq':
                        if (any(not str(row['external_session_id']).startswith('daq:') for row in tables['chat_sessions'])
                                or any(not isinstance(row['metadata'], dict) or row['metadata'].get('agent_id') != self.profile.agent_id for row in tables['chat_turns'])):
                            raise SourceReadError('source_identity_unverified')
                    sessions = project_rows(self.profile.kind, tables)
                remaining()
                observed_at = self._clock()
                if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
                    raise SourceReadError('source_timestamp_invalid')
            elapsed_ms = int((self._monotonic() - started) * 1000)
            if elapsed_ms >= self.budget_ms:
                raise SourceReadError('source_time_limit')
            return AgentSnapshot(self.profile.agent_id, SOURCE_IDENTITIES[self.profile.kind][1],
                observed_at, sessions, elapsed_ms=elapsed_ms)
        except SourceReadError:
            raise
        except psycopg.errors.QueryCanceled:
            raise SourceReadError('source_time_limit') from None
        except psycopg.Error:
            raise SourceReadError('source_unavailable') from None
        except (KeyError, TypeError, ValueError, OverflowError):
            raise SourceReadError('source_snapshot_invalid') from None
