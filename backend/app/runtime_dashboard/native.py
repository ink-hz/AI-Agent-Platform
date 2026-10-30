"""Current HR metadata: no content codec, sealed columns, objects or owner identity."""
from collections import defaultdict
from threading import Lock
from time import monotonic

import psycopg
from psycopg.rows import dict_row

from .budget import SOURCE_SECONDS, DeadlineConnection, remaining
from .models import Metric, SourceEvidence
from .repository import Contribution, SourceData, Turn


class HrMetadataReader:
    def __init__(self, database_url, *, connect=psycopg.connect, ttl_seconds=30, timer=monotonic):
        self._database_url, self._connect = database_url, connect
        self.ttl, self.timer = ttl_seconds, timer
        self._lock, self._expires, self._cached = Lock(), 0, None
        self.stats = {'queries': 0, 'decrypts': 0}

    def failure(self, now):
        cached = self._cached
        if cached:
            return SourceData(cached.evidence.model_copy(update={'status': 'stale',
                'checked_at': now, 'error': 'hr_metadata_unavailable'}), cached.contributions,
                {key: value.model_copy(update={'status': 'partial', 'reason': 'hr_metadata_unavailable'})
                 if value.value is not None else value for key, value in cached.business.items()})
        return SourceData(SourceEvidence(source_id='native-hr', source_kind='hr',
            checked_at=now, error='hr_metadata_unavailable'))

    def snapshot(self, now, *, deadline=None):
        deadline = deadline if deadline is not None else monotonic()+SOURCE_SECONDS
        try:
            acquired = self._lock.acquire(timeout=remaining(deadline))
        except TimeoutError:
            acquired = False
        if not acquired:
            return self.failure(now)
        try:
            if self._cached and self.timer() < self._expires:
                return self._cached
            try:
                data = self._read(now, deadline)
                remaining(deadline)
            except Exception:
                data = self.failure(now)
            self._cached, self._expires = data, self.timer()+self.ttl
            return data
        finally:
            self._lock.release()

    def _read(self, now, deadline):
        with self._connect(self._database_url, connect_timeout=3,
                options='-c default_transaction_read_only=on -c statement_timeout=5000', row_factory=dict_row) as raw:
            remaining(deadline)
            raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            c = DeadlineConnection(raw, deadline)
            role = c.execute('select current_user as role').fetchone()['role']
            threads = c.execute('select thread_id,created_at from platform_hr_agent.threads order by thread_id limit 10001').fetchall()
            if len(threads) > 10_000:
                raise OverflowError
            rows = c.execute('''select w.thread_id,i.created_at,
                exists(select 1 from platform_hr_agent.entries e where e.work_id=i.work_id
                    and e.input_revision=i.revision and e.kind='assistant') as answered
                from platform_hr_agent.inputs i join platform_hr_agent.works w on w.work_id=i.work_id
                order by i.created_at,i.work_id,i.revision limit 100001''').fetchall()
            if len(rows) > 100_000:
                raise OverflowError
            states = c.execute('select state,answer_state,count(*) as n from platform_hr_agent.works group by state,answer_state').fetchall()
            totals = c.execute('''select (select count(result_id) from platform_hr_agent.results) as results,
                (select count(kind) from platform_hr_agent.entries where kind='question') as questions''').fetchone()
            self.stats['queries'] += 5
        turns = defaultdict(list)
        for row in rows:
            remaining(deadline)
            turns[row['thread_id']].append(Turn(row['created_at'], int(row['answered']), 0, None, 'inputs_created_at'))
        items = tuple(Contribution(str(t['thread_id']), 'hr-bot','hr',None,t['created_at'],
            max((turn.at for turn in turns[t['thread_id']]),default=t['created_at']),tuple(turns[t['thread_id']])) for t in threads)
        def metric(value, unit, definition):
            return Metric(value=value, status='observed', unit=unit, definition=definition)
        business = {'works': metric(sum(s['n'] for s in states),'work','当前保留工作数'),
            'results': metric(totals['results'],'result','已保存成果数；不代表用户确认或任务成功'),
            'question_records': metric(totals['questions'],'turn','提问记录数；不计为用户输入或助手回答'),
            'complete_answers': Metric(unit='turn',definition='完整回答轮次',reason='complete_answer_not_proven')}
        for column in ('state','answer_state'):
            for value in {s[column] for s in states}:
                key = ('work_state_' if column == 'state' else 'answer_state_')+value
                business[key] = metric(sum(s['n'] for s in states if s[column] == value), 'work',
                    f'当前工作 {column}={value} 数；不是历史每轮结果')
        return SourceData(SourceEvidence(source_id='native-hr',source_kind='hr',status='fresh',
            observed_at=now,checked_at=now,complete=True,reader_role=role,
            input_time_basis='inputs_created_at',missing_question_time_records=0),items,business)
