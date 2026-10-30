"""Pure projection: a single UTC instant supplies every reporting window."""
from collections import Counter
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.agent_catalog import load_agent_catalog
from app.agent_catalog.web_entries import web_entry_for
from .models import (Application, Channel, Counts, DashboardOverview, Inventory, Metric,
    Ranking, RuntimeEvidence, SourceEvidence, Summary, Trend, Usage, Window)
from .repository import DIRECT, SourceData

SHANGHAI = ZoneInfo('Asia/Shanghai')
DEFINITIONS = {'sessions': ('session', '保留的来源会话；窗口内至少有一条用户输入的会话'),
    'turns': ('turn', '原始用户输入轮次，包含失败与未回复输入；按question_at归窗，缺失用record.created_at代理；现行HR按inputs.created_at'),
    'answered_turns': ('turn', '所选输入群组当前有回复记录的轮次，不是窗口内回复事件；不代表完整回答或业务成功'),
    'deliveries': ('delivery', '所选输入群组当前有明确成功回执的轮次，不是窗口内送达事件')}


def metric(name, value=None, status='unavailable', reason=None):
    unit, definition = DEFINITIONS[name]
    return Metric(value=value, status=status, unit=unit, definition=definition, reason=reason)


def empty_counts(status='unavailable', reason='source_unavailable'):
    return Counts(**{name: metric(name, status=status, reason=reason) for name in DEFINITIONS})


def usage_for(items, evidence, start, end, *, delivery_supported=False, applicable=True):
    if not applicable:
        return Usage(retained=empty_counts('not_applicable', 'workspace_not_conversational'),
            window=empty_counts('not_applicable', 'workspace_not_conversational'))
    if evidence.status == 'unavailable':
        return Usage(retained=empty_counts(reason=evidence.error or 'source_unavailable'),
            window=empty_counts(reason=evidence.error or 'source_unavailable'))
    status = 'observed' if evidence.complete and evidence.status == 'fresh' else 'partial'
    reason = None if status == 'observed' else evidence.error or 'source_partial'
    # Incomplete absence cannot establish an empty source.
    if not items and not evidence.complete:
        return Usage(retained=empty_counts(reason=reason), window=empty_counts(reason=reason))
    all_turns = [turn for item in items for turn in item.turns]
    selected = [turn for turn in all_turns if start <= turn.at < end]
    proxy = any(turn.input_time_basis == 'record_created_at' for turn in all_turns)
    def counts(turns, sessions, *, window=False):
        count_status = 'partial' if window and proxy else status
        count_reason = reason or ('input_time_proxy' if window and proxy else None)
        values = {'sessions': sessions, 'turns': len(turns), 'answered_turns': sum(t.answered for t in turns),
                  'deliveries': sum(t.delivered for t in turns)}
        return Counts(**{name: metric(name, value, count_status, count_reason) if name != 'deliveries' or delivery_supported
            else metric(name, reason='delivery_not_proven') for name, value in values.items()})
    return Usage(retained=counts(all_turns, len(items)),
        window=counts(selected, sum(any(start <= turn.at < end for turn in item.turns) for item in items),window=True),
        last_activity_at=max((turn.at for turn in all_turns), default=None),
        outcome_counts=dict(Counter(t.outcome for t in all_turns if t.outcome is not None)))


def sum_counts(values):
    fields = {}
    for name in DEFINITIONS:
        metrics = [getattr(value, name) for value in values]
        observed = [m for m in metrics if m.value is not None]
        status = 'observed' if len(observed) == len(metrics) and all(m.status == 'observed' for m in metrics) else 'partial'
        fields[name] = metric(name, sum(m.value for m in observed), status,
            None if status == 'observed' else 'source_coverage_partial') if observed else metric(name, reason='source_unavailable')
    return Counts(**fields)


def active_agents_for(sources, start, end):
    # Reuse the exact cohort selection and coverage/clock precision rules.
    metrics = [usage_for(data.contributions,data.evidence,start,end).window.turns for data in sources]
    known = [value for value in metrics if value.value is not None]
    complete = len(known) == len(metrics) and all(value.status == 'observed' for value in metrics)
    return Metric(value=sum(value.value > 0 for value in known) if known else None, unit='agent',
        status='observed' if complete else 'partial' if known else 'unavailable',
        reason=None if complete else 'source_coverage_or_input_time_partial' if known else 'source_unavailable',
        definition='近15分钟有用户输入的独立Agent服务数；按输入时间半开窗口，缺提问时间使用record.created_at代理；缺源仅计已知范围')


class DashboardService:
    def __init__(self, *, replica=None, hr=None, clock=lambda: datetime.now(UTC), catalog=None):
        self.replica, self.hr, self.clock = replica, hr, clock
        self.catalog = catalog or load_agent_catalog()

    def overview(self, days=7, *, now=None, health=None):
        if days not in {7, 30}:
            raise ValueError('days_invalid')
        now = now or self.clock()
        start = (now.astimezone(SHANGHAI).replace(hour=0, minute=0, second=0, microsecond=0)
                 - timedelta(days=days-1)).astimezone(UTC)
        sources = dict(self.replica.snapshot(now)) if self.replica else {}
        if self.hr:
            sources['hr-bot'] = self.hr.snapshot(now)
        health = health or {}
        current, channels = [], []
        usages = {}
        def source(key, family, channel=None):
            return sources.get(key, SourceData(SourceEvidence(source_id=key, source_kind=family,
                channel=channel, checked_at=now, error='source_not_configured')))
        def use(data, a, b, applicable=True):
            return usage_for(data.contributions, data.evidence, a, b,
                delivery_supported=data.evidence.source_kind == 'voc', applicable=applicable)
        for card in self.catalog:
            entry = web_entry_for(card.agent_id)
            if entry:
                family, channels_allowed = DIRECT.get(card.agent_id, ('hr', (None,)))
                channel = channels_allowed[0] if len(channels_allowed) == 1 else None
                data = source(card.agent_id, family, channel)
                usages[card.agent_id] = data
                observation = health.get(card.agent_id)
                current.append(Application(id=card.agent_id, name=card.display_name, kind='agent',
                    entry_path=entry['path'], source=data.evidence, usage=use(data, start, now),
                    business_metrics=data.business, **(observation.model_dump() if observation else {})))
            else:
                data = source(card.agent_id, 'metabot', 'feishu')
                channels.append(Channel(id=card.agent_id, agent_id=card.agent_id, name=card.display_name,
                    source=data.evidence, usage=use(data, start, now),
                    records_path=f'/admin/sessions?agent_id={card.agent_id}&source_kind=metabot&channel=feishu'))
        for key, name, path in [('sdk', 'SDK 工作台', '/sdk/'), ('manufacturing', '大制造工作台', '/manufacturing/')]:
            data = source(key, key)
            current.append(Application(id=key, name=name, kind='workspace', entry_path=path,
                source=data.evidence, usage=use(data, start, now, False),
                runtime=RuntimeEvidence(coverage='not_applicable',error='embedded_workspace',method=None)))
        legacy = source('legacy-hr-feishu', 'metabot', 'feishu')
        legacy_rows = [Channel(id='legacy-hr-feishu', agent_id='hr-bot', name='HR 旧飞书来源', configured=False,
            source=legacy.evidence, usage=use(legacy, start, now),
            records_path='/admin/sessions?agent_id=hr-bot&source_kind=metabot&channel=feishu')]
        summary_usage = sum_counts([a.usage.window for a in current if a.kind == 'agent'])
        coverage = sum(data.evidence.complete and data.evidence.status == 'fresh' for data in usages.values())
        trend = []
        for index in range(days):
            day = start + timedelta(days=index)
            count = sum_counts([use(data, day, min(day+timedelta(days=1), now)).window for data in usages.values()])
            trend.append(Trend(date=day.astimezone(SHANGHAI).date().isoformat(), turns=count.turns,
                answered_turns=count.answered_turns, deliveries=count.deliveries))
        result = DashboardOverview(generated_at=now, window=Window(start=start, end=now, days=days),
            rolling_24h=Window(start=now-timedelta(hours=24), end=now),
            inventory=Inventory(applications=len(current), agent_services=len(usages), business_workspaces=2,
                configured_agents=len({card.agent_id for card in self.catalog}), channel_agents=len(channels), legacy_streams=len(legacy_rows)),
            applications=current, channel_agents=channels, legacy_streams=legacy_rows,
            summary=Summary(usage=summary_usage, covered_sources=coverage, expected_sources=len(usages),
                active_agents_15m=active_agents_for(usages.values(),now-timedelta(minutes=15),now),
                status='complete' if coverage == len(usages) else 'partial' if coverage else 'unavailable'),
            trend=trend, last_24h=sum_counts([use(data, now-timedelta(hours=24), now).window for data in usages.values()]),
            ranking=sorted([Ranking(application_id=a.id, turns=a.usage.window.turns) for a in current
                if a.kind == 'agent' and a.usage.window.turns.value is not None], key=lambda r: (-r.turns.value, r.application_id)))
        update_runtime_summary(result)
        return result


def update_runtime_summary(result):
    agents = [a for a in result.applications if a.kind == 'agent']
    states = Counter(a.runtime.state for a in agents)
    result.summary.healthy = states['healthy']
    result.summary.abnormal = states['degraded'] + states['offline']
    result.summary.unknown = states['unknown']
    result.summary.observed_runtime = sum(a.runtime.coverage == 'observed' and not a.runtime.stale for a in agents)
    result.summary.expected_runtime = len(agents)
