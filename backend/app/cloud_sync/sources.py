"""Complete bounded business snapshots from four explicitly identified databases."""
from __future__ import annotations

import os
import re
import stat
import time
from collections.abc import Callable
from datetime import UTC, datetime

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from .models import SOURCE_IDENTITIES, AgentSnapshot, SourceProfile, SourceReadError
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


def _verify_voc_visibility(execute) -> None:
    """Prove the linked-receipt domain after stabilizing its DDL boundary.

    Counting the same RLS-filtered join twice cannot establish completeness.
    Accept only the reviewed consumer policy and one-to-one source constraints;
    policy/schema evolution requires an explicit contract review.
    """
    def require(condition):
        if not condition:
            raise SourceReadError('source_visibility_unverified')

    # Planning zero-row reads obtains ACCESS SHARE until transaction end, using
    # only the existing column grants (explicit LOCK requires a table grant).
    # READ COMMITTED is essential: catalog statements AFTER these locks must
    # see DDL that committed before the locks, even if identity was read earlier.
    # Policy/constraint DDL then cannot change the contract before the join.
    for (schema, table), columns in VOC_COLUMNS.items():
        execute(sql.SQL('select {} from {}.{} where false').format(
            sql.SQL(',').join(map(sql.Identifier, columns)),
            sql.Identifier(schema), sql.Identifier(table)))
    role = execute('''select oid, rolname, rolsuper, rolcreatedb, rolcreaterole,
        rolinherit, rolreplication, rolbypassrls,
        exists(select 1 from pg_auth_members where member=pg_roles.oid) as member
        from pg_roles where rolname=current_user''').fetchone()
    require(role is not None and role['rolname'] == 'platform_sync_voc_read'
            and not any(role[key] for key in ('rolsuper', 'rolcreatedb', 'rolcreaterole',
                'rolinherit', 'rolreplication', 'rolbypassrls', 'member')))
    relations = {}
    for (schema, table), columns in VOC_COLUMNS.items():
        relation = execute('''select c.oid,c.relkind,c.relowner,c.relrowsecurity,
            row_security_active(c.oid) as active,
            has_schema_privilege(current_user,n.oid,'USAGE') as usage,
            has_schema_privilege(current_user,n.oid,'CREATE') as create_schema,
            has_table_privilege(current_user,c.oid,
                'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') as broad
            from pg_class c join pg_namespace n on n.oid=c.relnamespace
            where n.nspname=%s and c.relname=%s''', (schema, table)).fetchone()
        is_outbox = table == 'outbox_messages'
        require(relation is not None and relation['relkind'] == 'r'
                and relation['relowner'] != role['oid'] and relation['usage']
                and not relation['create_schema'] and not relation['broad']
                and relation['relrowsecurity'] == is_outbox
                and relation['active'] == is_outbox)
        attributes = execute('''select attnum,attname,
            has_column_privilege(current_user,attrelid,attnum,'SELECT') as readable,
            has_column_privilege(current_user,attrelid,attnum,'INSERT,UPDATE,REFERENCES') as writable
            from pg_attribute where attrelid=%s and attnum>0 and not attisdropped''',
            (relation['oid'],)).fetchall()
        require({a['attname'] for a in attributes if a['readable']} == set(columns)
                and not any(a['writable'] for a in attributes))
        relations[table] = (relation['oid'], {a['attname']: a['attnum'] for a in attributes})

    outbox_oid = relations['outbox_messages'][0]
    policies = execute('''select polname,polcmd,polpermissive,polroles,
        pg_get_expr(polqual,polrelid) as expression, polwithcheck is null as no_check
        from pg_policy where polrelid=%s and polcmd in ('r','*')
        and (0=any(polroles) or %s=any(polroles))''', (outbox_oid,role['oid'])).fetchall()
    require(len(policies) == 1)
    policy = policies[0]
    expression = re.sub(r'[\s()]', '', policy['expression'] or '').lower()
    require(policy['polname'] == 'platform_sync_outbox_select_policy'
            and policy['polcmd'] == 'r' and policy['polpermissive']
            and policy['polroles'] == [role['oid']] and policy['no_check']
            and expression == 'source_inbound_message_idisnotnull')

    constraints = execute('''select conrelid,contype,conkey,confrelid,confkey
        from pg_constraint where conrelid=any(%s) and convalidated
        and not condeferrable''', ([r[0] for r in relations.values()],)).fetchall()
    inbound_oid, inbound_columns = relations['inbound_messages']
    inbound_key = [inbound_columns['id']]
    require(any(c['conrelid'] == inbound_oid and c['contype'] == 'p'
                and c['conkey'] == inbound_key for c in constraints))
    for table, column in (('interaction_jobs','inbound_message_id'),
                          ('outbox_messages','source_inbound_message_id')):
        oid, attributes = relations[table]
        key = [attributes[column]]
        require(any(c['conrelid'] == oid and c['contype'] in ('p','u')
                    and c['conkey'] == key for c in constraints))
        require(any(c['conrelid'] == oid and c['contype'] == 'f'
                    and c['conkey'] == key and c['confrelid'] == inbound_oid
                    and c['confkey'] == inbound_key for c in constraints))


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


def _snapshot_query(profile: SourceProfile, schema: str, table: str,
                    columns: tuple[str, ...]) -> sql.Composed:
    """Transfer only business/identity fields consumed by the replica encoder.

    Session metadata stays intact for ownership and verified Admin display.
    Turn metadata only establishes its JSON object shape, plus DAQ agent scope.
    Citations are not serialized by SanitizedTurnRecord; validate their complete
    array-of-objects shape before returning an empty array. Malformed values
    pass through unchanged so projection still fails visibly, never repairs them.
    """
    fields = []
    is_turn = table in {'chat_turns', 'admin_chat_turns'}
    for column in columns:
        identifier = sql.Identifier(column)
        if is_turn and column == 'done':
            consumed = ('status', 'engine', 'backend', 'actual_provider_model', 'error_type')
            pairs = []
            for key in consumed:
                pairs.extend((sql.Literal(key), sql.SQL('{}::jsonb -> {}').format(identifier, sql.Literal(key))))
            fields.append(sql.SQL("case when jsonb_typeof({}::jsonb)='object' then jsonb_build_object({}) else {}::jsonb end as {}").format(
                identifier, sql.SQL(',').join(pairs), identifier, identifier))
        elif is_turn and column == 'metadata':
            kept = (sql.SQL("jsonb_build_object('agent_id', {}::jsonb -> 'agent_id')").format(identifier)
                    if profile.kind == 'daq' else sql.SQL("'{}'::jsonb"))
            fields.append(sql.SQL("case when jsonb_typeof({}::jsonb)='object' then {} else {}::jsonb end as {}").format(
                identifier, kept, identifier, identifier))
        elif is_turn and column == 'sources':
            fields.append(sql.SQL("""case when jsonb_typeof({}::jsonb)='array' then
                case when not exists (
                    select 1 from jsonb_array_elements({}::jsonb) element(value)
                    where jsonb_typeof(element.value) is distinct from 'object'
                ) then '[]'::jsonb else {}::jsonb end
                else {}::jsonb end as {}""").format(
                    identifier, identifier, identifier, identifier, identifier))
        else:
            fields.append(identifier)
    return sql.SQL('select {} from {}.{} order by id').format(
        sql.SQL(',').join(fields), sql.Identifier(schema), sql.Identifier(table))


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
                # VOC proves current catalog state after relation locks and reads
                # all business rows in ONE bounded statement snapshot. Other
                # sources retain their multi-statement repeatable-read contract.
                isolation = 'read committed' if self.profile.kind == 'voc' else 'repeatable read'
                cursor.execute(f'set transaction isolation level {isolation}, read only')
                # Conservative start-of-observation time, never completion time.
                # The store lease orders entire read/publish cycles.
                observed_at = self._clock()
                if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
                    raise SourceReadError('source_timestamp_invalid')

                def execute(query, params=None):
                    cursor.execute("select set_config('statement_timeout', %s, true)", (str(remaining()),))
                    cursor.execute(query, params)
                    return cursor

                identity = execute('select current_database() as database, current_schema() as schema').fetchone()
                if identity != {'database': self.profile.expected_database, 'schema': schema}:
                    raise SourceReadError('source_identity_unverified')
                if self.profile.kind == 'voc':
                    _verify_voc_visibility(execute)
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
                    # Materialize once: bounds and payload must use the same
                    # statement snapshot despite concurrent producer commits.
                    # The left join emits only a measurement sentinel on empty
                    # or oversized snapshots, never transfers excess payloads.
                    query = sql.SQL('''with snapshot as materialized ({}),
                        measurement as (
                            select count(*) as snapshot_count,
                            coalesce(sum(octet_length(row_to_json(snapshot)::text)),0) as snapshot_bytes
                            from snapshot)
                        select measurement.*, snapshot.* from measurement
                        left join snapshot on snapshot_count <= %s and snapshot_bytes <= %s
                    ''').format(VOC_QUERY)
                    values = execute(query, (self.max_rows, self.max_bytes)).fetchall()
                    count, size = values[0]['snapshot_count'], values[0]['snapshot_bytes']
                    if count > self.max_rows:
                        raise SourceReadError('source_row_limit')
                    if size > self.max_bytes:
                        raise SourceReadError('source_body_limit')
                    if not count:
                        values = []
                    elif len(values) != count:
                        raise SourceReadError('source_snapshot_incomplete')
                    for value in values:
                        del value['snapshot_count'], value['snapshot_bytes']
                    remaining()
                    sessions = project_voc_snapshot(values)
                else:
                    tables = {}
                    for (table_schema, table), columns in required.items():
                        if table == 'daq_installation_identity':
                            continue
                        query = _snapshot_query(self.profile, table_schema, table, columns)
                        tables[table] = fetch_bounded(query)
                    if self.profile.kind == 'daq' and (
                        any(not str(row['external_session_id']).startswith('daq:') for row in tables['chat_sessions'])
                        or any(not isinstance(row['metadata'], dict) or row['metadata'].get('agent_id') != self.profile.agent_id for row in tables['chat_turns'])
                    ):
                        raise SourceReadError('source_identity_unverified')
                    sessions = project_rows(self.profile.kind, tables)
                remaining()
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
