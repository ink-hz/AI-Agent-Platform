"""One bounded replica metadata scan; retain numeric contributions, never content."""
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import Lock
from time import monotonic

from .budget import SOURCE_SECONDS, DeadlineConnection, remaining
from .models import SourceEvidence


@dataclass(frozen=True)
class Turn:
    at: datetime
    answered: int
    delivered: int
    outcome: str | None
    input_time_basis: str = 'record_created_at'


@dataclass(frozen=True)
class Contribution:
    key: str
    agent_id: str
    source_kind: str
    channel: str | None
    created_at: datetime
    last_active_at: datetime
    turns: tuple[Turn, ...]


def timestamp(value):
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('timestamp_timezone_required')
    return result.astimezone(UTC)


def contribution(record):
    unique = {}
    for turn in record.get('turns', []):
        key = turn['key']
        if key in unique:
            raise ValueError('duplicate_turn')
        answer = turn.get('answer') or {}
        text = answer.get('text', '') if isinstance(answer, dict) else answer
        question_at = turn.get('question_at')
        # Only genuine absence permits the declared record-time proxy. Invalid
        # supplied timestamps fail the source rather than silently moving input.
        basis = 'question_at' if question_at is not None else 'record_created_at'
        unique[key] = Turn(timestamp(question_at if question_at is not None else turn['created_at']),
            int(bool(text and text.strip())),
            int(record['source_kind'] == 'voc' and turn.get('outcome') == 'sent'), turn.get('outcome'), basis)
    return Contribution(record['key'], record['agent_id'], record['source_kind'], record['channel'],
        timestamp(record['created_at']), timestamp(record['last_active_at']), tuple(unique.values()))


@dataclass
class SourceData:
    evidence: SourceEvidence
    contributions: tuple[Contribution, ...] = ()
    business: dict = field(default_factory=dict)


def input_clock_evidence(items, *, complete):
    if not items and not complete:
        return {'input_time_basis': None, 'missing_question_time_records': None}
    turns = [turn for item in items for turn in item.turns]
    bases = {turn.input_time_basis for turn in turns}
    return {'input_time_basis': next(iter(bases)) if len(bases) == 1 else 'mixed' if bases else None,
        'missing_question_time_records': sum(turn.input_time_basis == 'record_created_at' for turn in turns)}


# Authoritative agent/family ownership selects the service; retain actual channels.
# These are observed baseline channels, not a filter on future trusted direct data.
DIRECT = {'ai-fae-agent': ('fae', ('fae',)), 'ai-daq-fae-agent': ('fae', ('fae',)),
          'voc': ('voc', ('dingtalk',)), 'ai-admin-agent': ('admin', (None, 'dingtalk'))}


def source_key(agent_id, family, channel):
    if agent_id in DIRECT and DIRECT[agent_id][0] == family :
        return agent_id
    if family == 'metabot' and channel == 'feishu':
        if agent_id == 'hr-bot':
            return 'legacy-hr-feishu'
        if agent_id.startswith('marketing-'):
            return agent_id
    return None


def representation_fingerprint(row):
    # Inherit every plaintext comparison in ReplicaObservabilityRepository._decrypt.
    # A hash certifies payload content, not these separately stored columns.
    return (row['session_key'], row['payload_sha256'], row['agent_id'],
        row['source_kind'], row['channel'], row['created_at'], row['last_active_at'])


class ReplicaDashboardReader:
    def __init__(self, repository, *, max_sessions=10_000, ttl_seconds=30, timer=monotonic):
        self.repository = repository
        self.max_sessions, self.ttl, self.timer = max_sessions, ttl_seconds, timer
        self._lock = Lock()
        self._cached = {}
        self._sources = {}
        self._expires = 0
        self.stats = {'metadata_reads': 0, 'payload_reads': 0, 'decrypts': 0, 'refreshes': 0}

    def failure(self, now, error=None):
        code = 'replica_limit_exceeded' if isinstance(error, OverflowError) else 'replica_unavailable'
        result = {key: SourceData(value.evidence.model_copy(update={
            'status': 'stale', 'checked_at': now, 'error': code}), value.contributions)
            for key, value in self._sources.items()}
        for key, (family, channels) in DIRECT.items():
            channel = channels[0] if len(channels) == 1 else None
            result.setdefault(key, SourceData(SourceEvidence(source_id=key, source_kind=family,
                channel=channel, checked_at=now, error=code)))
        return result

    def snapshot(self, now, *, deadline=None):
        deadline = deadline if deadline is not None else monotonic()+SOURCE_SECONDS
        try:
            acquired = self._lock.acquire(timeout=remaining(deadline))
        except TimeoutError:
            acquired = False
        if not acquired:
            return self.failure(now)
        try:
            if self.timer() < self._expires:
                return self._sources
            try:
                result = self._refresh(now, deadline)
            except Exception as error:
                result = self.failure(now, error)
            self._sources = result
            self._expires = self.timer() + self.ttl
            return result
        finally:
            self._lock.release()

    def _refresh(self, now, deadline):
        repo = self.repository
        with repo._connection() as raw:
            remaining(deadline)
            raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            connection = DeadlineConnection(raw, deadline)
            role = connection.execute('select current_user as role').fetchone()['role']
            rows = connection.execute('''select session_key, payload_sha256, agent_id, source_kind,
                channel, created_at, last_active_at from platform_replica.sessions order by session_key limit %s''',
                (self.max_sessions + 1,)).fetchall()
            self.stats['metadata_reads'] += 1
            if len(rows) > self.max_sessions:
                raise OverflowError
            states = {row['agent_id']: row for row in connection.execute('''select agent_id, source_kind,
                mode, publisher, last_source_success_at, last_published_at, last_failure_at,
                source_sessions, source_turns from platform_replica.agent_sources''').fetchall()}
            changed = [row['session_key'] for row in rows
                if (source_key(row['agent_id'], row['source_kind'], row['channel']) or row['session_key'] in self._cached)
                and (row['session_key'] not in self._cached
                    or self._cached[row['session_key']][0] != representation_fingerprint(row))]
            payloads = {}
            # Batches cap transient encrypted material; plaintext is discarded each iteration.
            from app.cloud_replica.repository import _SESSION_COLUMNS
            for offset in range(0, len(changed), 100):
                for row in connection.execute(f'select {_SESSION_COLUMNS} from platform_replica.sessions where session_key = any(%s)',
                        (changed[offset:offset+100],)).fetchall():
                    remaining(deadline)
                    self.stats['payload_reads'] += 1
                    self.stats['decrypts'] += 1
                    try:
                        payloads[row['session_key']] = (representation_fingerprint(row), contribution(repo._decrypt(row)))
                    except Exception:
                        remaining(deadline)
                        payloads[row['session_key']] = None
        groups = {key: [] for key in set(DIRECT) | set(self._sources)}
        for aid, state in states.items():
            key = source_key(aid, state['source_kind'], 'feishu')
            if key:
                groups.setdefault(key, [])
        failed = set()
        updated = {}
        for row in rows:
            remaining(deadline)
            # An identity drift must also invalidate the last verified owner,
            # including when the new metadata no longer maps to a known source.
            previous = self._cached.get(row['session_key'])
            if row['session_key'] in payloads and payloads[row['session_key']] is None and previous:
                prior = previous[1]
                failed.add(source_key(prior.agent_id, prior.source_kind, prior.channel))
            key = source_key(row['agent_id'], row['source_kind'], row['channel'])
            if not key:
                continue
            item = payloads.get(row['session_key']) if row['session_key'] in payloads else self._cached.get(row['session_key'])
            if item is None:
                failed.add(key)
            else:
                updated[row['session_key']] = item
                groups.setdefault(key, []).append(item[1])
        result = {}
        for key, items in groups.items():
            aid = 'hr-bot' if key == 'legacy-hr-feishu' else key
            family, channels = DIRECT.get(key, ('metabot', ('feishu',)))
            channel = channels[0] if len(channels) == 1 else None
            state = states.get(aid, {})
            # Old HR shares the registered id but must never inherit native/direct evidence.
            if state.get('source_kind') != family or (state.get('mode'), state.get('publisher')) != (('cloud_direct', 'cloud') if key in DIRECT else ('office', 'office')):
                state = {}
            observed = state.get('last_source_success_at')
            complete = observed is not None
            failure = state.get('last_failure_at')
            stale_after = min(repo._stale_after, timedelta(seconds=60)) if state.get('mode') == 'cloud_direct' else repo._stale_after
            stale = not observed or now - observed > stale_after or observed > now or (failure and failure >= observed)
            mismatch = complete and (state.get('source_sessions') != len(items) or
                state.get('source_turns') != sum(len(item.turns) for item in items))
            error = 'source_count_mismatch' if mismatch else 'source_failed' if failure and (not observed or failure >= observed) else 'source_stale' if stale else None
            evidence = SourceEvidence(source_id=key, source_kind=family, channel=channel,
                status='stale' if stale else 'fresh', complete=bool(complete and not mismatch), observed_at=observed,
                published_at=state.get('last_published_at'), checked_at=now,
                error=error or ('source_completeness_unknown' if not complete else None), reader_role=role,
                channels=sorted({item.channel for item in items},key=lambda c:c or ''),
                **input_clock_evidence(items,complete=bool(complete and not mismatch)))
            previous = self._sources.get(key)
            if key in failed or (previous is not None and not evidence.complete):
                failure_code = 'replica_payload_invalid' if key in failed else evidence.error
                result[key] = SourceData((previous.evidence if previous else evidence).model_copy(update={
                    'status': 'stale' if previous else 'unavailable', 'complete': False,
                    'checked_at': now, 'error': failure_code,
                    **({} if previous else {'input_time_basis': None, 'missing_question_time_records': None})}),
                    previous.contributions if previous else ())
                updated = {k: v for k, v in updated.items() if source_key(v[1].agent_id, v[1].source_kind, v[1].channel) != key}
                updated.update({k: v for k, v in self._cached.items() if source_key(v[1].agent_id, v[1].source_kind, v[1].channel) == key})
            else:
                actual_channels = {item.channel for item in items}
                if actual_channels:
                    evidence.channel = next(iter(actual_channels)) if len(actual_channels) == 1 else None
                result[key] = SourceData(evidence, tuple(items))
        if len(updated) > self.max_sessions:
            raise OverflowError
        remaining(deadline)
        self._cached = updated
        self.stats['refreshes'] += 1
        return result
