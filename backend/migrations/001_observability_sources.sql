\set ON_ERROR_STOP on

create extension if not exists pgcrypto;

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'platform_sync_writer') then
    create role platform_sync_writer login;
  end if;
end
$$;

create schema if not exists platform_source_fae authorization flywheel_owner;
create schema if not exists platform_source_admin authorization flywheel_owner;
create schema if not exists platform_sync authorization flywheel_owner;
create schema if not exists platform_read authorization flywheel_owner;

create table if not exists platform_source_fae.chat_sessions (
  id uuid primary key,
  external_session_id text unique not null,
  channel text not null,
  user_id text,
  external_user_id text,
  conversation_title text,
  created_at timestamptz not null,
  last_active_at timestamptz not null,
  details jsonb not null default '{}'::jsonb,
  source_synced_at timestamptz not null
);

create table if not exists platform_source_fae.chat_turns (
  id uuid primary key,
  session_id uuid references platform_source_fae.chat_sessions(id),
  external_session_id text not null,
  turn_index integer not null,
  trace_id text not null,
  channel text not null,
  question text not null,
  answer text not null,
  sources jsonb not null default '[]'::jsonb,
  stages jsonb not null default '[]'::jsonb,
  done jsonb not null default '{}'::jsonb,
  planned_capabilities jsonb not null default '[]'::jsonb,
  capability_coverage jsonb not null default '{}'::jsonb,
  fallback_used boolean not null default false,
  fallback_reason text,
  outcome text,
  duration_ms bigint,
  created_at timestamptz not null,
  details jsonb not null default '{}'::jsonb,
  source_synced_at timestamptz not null,
  unique (external_session_id, turn_index)
);
create index if not exists idx_platform_fae_turn_trace
  on platform_source_fae.chat_turns(trace_id);
create index if not exists idx_platform_fae_turn_created
  on platform_source_fae.chat_turns(created_at desc);

create table if not exists platform_source_fae.turn_feedback (
  id uuid primary key,
  turn_id uuid references platform_source_fae.chat_turns(id),
  external_session_id text not null,
  trace_id text not null,
  rating text not null,
  reason_code text,
  comment text not null default '',
  channel text not null,
  user_id text,
  external_user_id text,
  created_at timestamptz not null,
  details jsonb not null default '{}'::jsonb,
  source_synced_at timestamptz not null
);

create table if not exists platform_source_fae.turn_reviews (
  id uuid primary key,
  turn_id uuid references platform_source_fae.chat_turns(id),
  priority text not null,
  review_status text not null,
  failure_layer text,
  failure_reason text not null default '',
  expected_answer_notes text not null default '',
  corrected_answer text not null default '',
  reviewer text not null,
  should_add_to_eval boolean not null default false,
  should_update_knowledge boolean not null default false,
  created_at timestamptz not null,
  updated_at timestamptz not null,
  details jsonb not null default '{}'::jsonb,
  source_synced_at timestamptz not null
);

create table if not exists platform_source_fae.eval_candidates (
  id uuid primary key,
  turn_id uuid references platform_source_fae.chat_turns(id),
  candidate_status text not null,
  testset_name text,
  case_json jsonb not null default '{}'::jsonb,
  exported_path text,
  created_at timestamptz not null,
  exported_at timestamptz,
  details jsonb not null default '{}'::jsonb,
  source_synced_at timestamptz not null
);

create table if not exists platform_source_fae.knowledge_improvement_tasks (
  id uuid primary key,
  turn_id uuid references platform_source_fae.chat_turns(id),
  task_status text not null,
  knowledge_area text not null,
  gap_summary text not null,
  proposed_source text not null default '',
  owner text,
  created_at timestamptz not null,
  updated_at timestamptz not null,
  details jsonb not null default '{}'::jsonb,
  source_synced_at timestamptz not null
);

create table if not exists platform_source_fae.qa_review_items (
  id uuid primary key,
  source_type text not null,
  source_ref text not null,
  turn_id uuid references platform_source_fae.chat_turns(id),
  question text not null,
  original_answer text not null default '',
  reviewed_answer text not null default '',
  product_tags jsonb not null default '[]'::jsonb,
  technical_tags jsonb not null default '[]'::jsonb,
  review_status text not null,
  reviewer text not null,
  review_notes text not null default '',
  created_at timestamptz not null,
  updated_at timestamptz not null,
  details jsonb not null default '{}'::jsonb,
  source_synced_at timestamptz not null
);

create table if not exists platform_source_fae.trace_spans (
  trace_id text not null,
  span_id text not null,
  parent_span_id text,
  record_type text not null,
  node text not null,
  started_at timestamptz not null,
  ended_at timestamptz,
  duration_ms bigint,
  input_summary jsonb not null default '{}'::jsonb,
  output_summary jsonb not null default '{}'::jsonb,
  metadata jsonb not null default '{}'::jsonb,
  error text,
  details jsonb not null default '{}'::jsonb,
  source_synced_at timestamptz not null,
  primary key (trace_id, span_id)
);
create index if not exists idx_platform_fae_span_started
  on platform_source_fae.trace_spans(started_at desc);

create table if not exists platform_source_admin.chat_sessions (
  id uuid primary key,
  external_session_id text unique not null,
  channel text not null,
  user_id text,
  external_user_id text,
  conversation_title text,
  created_at timestamptz not null,
  last_active_at timestamptz not null,
  details jsonb not null default '{}'::jsonb,
  source_synced_at timestamptz not null
);

create table if not exists platform_source_admin.chat_turns (
  id uuid primary key,
  session_id uuid references platform_source_admin.chat_sessions(id),
  external_session_id text not null,
  turn_index integer not null,
  trace_id text not null,
  channel text not null,
  question text not null,
  answer text not null,
  sources jsonb not null default '[]'::jsonb,
  source_groups jsonb not null default '[]'::jsonb,
  stages jsonb not null default '[]'::jsonb,
  done jsonb not null default '{}'::jsonb,
  fallback_used boolean not null default false,
  fallback_reason text,
  outcome text,
  duration_ms bigint,
  created_at timestamptz not null,
  details jsonb not null default '{}'::jsonb,
  source_synced_at timestamptz not null,
  unique (external_session_id, turn_index)
);
create index if not exists idx_platform_admin_turn_trace
  on platform_source_admin.chat_turns(trace_id);
create index if not exists idx_platform_admin_turn_created
  on platform_source_admin.chat_turns(created_at desc);

create table if not exists platform_source_admin.turn_feedback (
  id uuid primary key,
  turn_id uuid references platform_source_admin.chat_turns(id),
  external_session_id text not null,
  trace_id text not null,
  rating text not null,
  reason_code text,
  comment text not null default '',
  channel text not null,
  user_id text,
  external_user_id text,
  created_at timestamptz not null,
  details jsonb not null default '{}'::jsonb,
  source_synced_at timestamptz not null
);

create table if not exists platform_source_admin.turn_reviews (
  id uuid primary key,
  turn_id uuid references platform_source_admin.chat_turns(id),
  feedback_id uuid references platform_source_admin.turn_feedback(id),
  external_session_id text not null,
  trace_id text not null,
  reviewer text not null,
  verdict text not null,
  severity text not null,
  failure_layer text not null,
  capability_batch text,
  scope jsonb not null default '[]'::jsonb,
  notes text not null default '',
  suggested_action text not null default '',
  created_at timestamptz not null,
  details jsonb not null default '{}'::jsonb,
  source_synced_at timestamptz not null
);

create table if not exists platform_source_admin.eval_candidates (
  id uuid primary key,
  review_id uuid references platform_source_admin.turn_reviews(id),
  source_turn_id uuid references platform_source_admin.chat_turns(id),
  source_feedback_id uuid references platform_source_admin.turn_feedback(id),
  case_id text not null,
  question text not null,
  sample_type text not null,
  capability_batch text not null,
  scope jsonb not null default '[]'::jsonb,
  case_json jsonb not null default '{}'::jsonb,
  status text not null,
  created_at timestamptz not null,
  details jsonb not null default '{}'::jsonb,
  source_synced_at timestamptz not null
);

create table if not exists platform_source_admin.knowledge_improvement_tasks (
  id uuid primary key,
  review_id uuid references platform_source_admin.turn_reviews(id),
  source_turn_id uuid references platform_source_admin.chat_turns(id),
  source_feedback_id uuid references platform_source_admin.turn_feedback(id),
  task_type text not null,
  failure_layer text not null,
  title text not null,
  description text not null default '',
  status text not null,
  priority text not null,
  source_refs jsonb not null default '[]'::jsonb,
  created_at timestamptz not null,
  updated_at timestamptz not null,
  details jsonb not null default '{}'::jsonb,
  source_synced_at timestamptz not null
);

create table if not exists platform_sync.runs (
  id uuid primary key default gen_random_uuid(),
  source_kind text not null check (source_kind in ('fae', 'admin')),
  status text not null check (status in ('running', 'succeeded', 'failed')),
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  source_counts jsonb not null default '{}'::jsonb,
  applied_counts jsonb not null default '{}'::jsonb,
  validation jsonb not null default '{}'::jsonb,
  error_summary text
);
create index if not exists idx_platform_sync_source_started
  on platform_sync.runs(source_kind, started_at desc);

create or replace view platform_read.sessions as
with metabot as (
  select
    'metabot:' || c.bot_id || ':' || c.id::text as session_key,
    c.bot_id as agent_id,
    'metabot'::text as source_kind,
    c.id::text as native_id,
    c.platform as channel,
    coalesce(
      (select left(m.content, 180) from flywheel_analytics.messages m
       where m.conversation_id = c.id and m.role = 'user'
       order by m.occurred_at limit 1),
      c.platform_conversation_id
    ) as title,
    null::text as user_identity,
    c.created_at,
    coalesce(max(m.occurred_at), c.updated_at) as last_active_at,
    count(distinct m.turn_id) filter (where m.role = 'assistant')::bigint as turn_count,
    count(distinct f.id)::bigint as feedback_count,
    0::bigint as review_count,
    null::text as latest_outcome,
    null::timestamptz as source_synced_at,
    jsonb_build_object('business_domain', c.business_domain,
                       'conversation_type', c.conversation_type) as details
  from flywheel_analytics.conversations c
  left join flywheel_analytics.messages m on m.conversation_id = c.id
  left join flywheel_core.feedback f on f.conversation_id = c.id
  where not c.is_synthetic
  group by c.id, c.bot_id, c.platform, c.platform_conversation_id,
           c.created_at, c.updated_at, c.business_domain, c.conversation_type
), fae as (
  select
    'fae:' || s.id::text,
    'ai-fae-agent'::text,
    'fae'::text,
    s.id::text,
    s.channel,
    coalesce(s.conversation_title,
      (select left(t.question, 180) from platform_source_fae.chat_turns t
       where t.session_id = s.id order by t.turn_index limit 1)),
    case when coalesce(s.external_user_id, s.user_id) is null then null
         else '***' || right(coalesce(s.external_user_id, s.user_id), 4) end,
    s.created_at,
    s.last_active_at,
    (select count(*) from platform_source_fae.chat_turns t where t.session_id = s.id),
    (select count(*) from platform_source_fae.turn_feedback f
     where f.external_session_id = s.external_session_id),
    (select count(*) from platform_source_fae.turn_reviews r
     join platform_source_fae.chat_turns t on t.id = r.turn_id where t.session_id = s.id),
    (select t.outcome from platform_source_fae.chat_turns t
     where t.session_id = s.id order by t.turn_index desc limit 1),
    s.source_synced_at,
    s.details
  from platform_source_fae.chat_sessions s
), admin as (
  select
    'admin:' || s.id::text,
    'ai-admin-agent'::text,
    'admin'::text,
    s.id::text,
    s.channel,
    coalesce(s.conversation_title,
      (select left(t.question, 180) from platform_source_admin.chat_turns t
       where t.session_id = s.id order by t.turn_index limit 1)),
    case when coalesce(s.external_user_id, s.user_id) is null then null
         else '***' || right(coalesce(s.external_user_id, s.user_id), 4) end,
    s.created_at,
    s.last_active_at,
    (select count(*) from platform_source_admin.chat_turns t where t.session_id = s.id),
    (select count(*) from platform_source_admin.turn_feedback f
     where f.external_session_id = s.external_session_id),
    (select count(*) from platform_source_admin.turn_reviews r
     join platform_source_admin.chat_turns t on t.id = r.turn_id where t.session_id = s.id),
    (select t.outcome from platform_source_admin.chat_turns t
     where t.session_id = s.id order by t.turn_index desc limit 1),
    s.source_synced_at,
    s.details
  from platform_source_admin.chat_sessions s
)
select * from metabot
union all select * from fae
union all select * from admin;

create or replace view platform_read.turns as
with metabot_messages as (
  select
    m.conversation_id,
    m.turn_id,
    min(m.occurred_at) as created_at,
    max(m.content) filter (where m.role = 'user') as question,
    max(m.content) filter (where m.role = 'assistant') as answer
  from flywheel_analytics.messages m
  where not m.is_synthetic
  group by m.conversation_id, m.turn_id
), metabot as (
  select
    'metabot:' || c.bot_id || ':' || mm.turn_id::text as turn_key,
    'metabot:' || c.bot_id || ':' || c.id::text as session_key,
    c.bot_id as agent_id,
    'metabot'::text as source_kind,
    mm.turn_id::text as native_id,
    row_number() over (partition by c.id order by mm.created_at)::integer - 1 as turn_index,
    coalesce(mm.question, '') as question,
    coalesce(mm.answer, '') as answer,
    mm.created_at,
    case when r.id is null then null else 'metabot:' || c.bot_id || ':' || r.id::text end as trace_key,
    null::text as outcome,
    false as fallback_used,
    r.duration_ms,
    '[]'::jsonb as sources,
    (select count(*) from flywheel_core.feedback f where f.turn_id = mm.turn_id)::bigint as feedback_count,
    0::bigint as review_count,
    null::timestamptz as source_synced_at,
    jsonb_build_object('engine', r.engine, 'model', r.model, 'status', r.status) as details
  from metabot_messages mm
  join flywheel_analytics.conversations c on c.id = mm.conversation_id
  left join flywheel_analytics.runs r on r.turn_id = mm.turn_id
), fae as (
  select
    'fae:' || t.id::text,
    'fae:' || t.session_id::text,
    'ai-fae-agent'::text,
    'fae'::text,
    t.id::text,
    t.turn_index,
    t.question,
    t.answer,
    t.created_at,
    'fae:' || t.trace_id,
    t.outcome,
    t.fallback_used,
    t.duration_ms,
    t.sources,
    (select count(*) from platform_source_fae.turn_feedback f where f.turn_id = t.id),
    (select count(*) from platform_source_fae.turn_reviews r where r.turn_id = t.id),
    t.source_synced_at,
    t.details || jsonb_build_object(
      'done', t.done,
      'planned_capabilities', t.planned_capabilities,
      'capability_coverage', t.capability_coverage,
      'fallback_reason', t.fallback_reason
    )
  from platform_source_fae.chat_turns t
), admin as (
  select
    'admin:' || t.id::text,
    'admin:' || t.session_id::text,
    'ai-admin-agent'::text,
    'admin'::text,
    t.id::text,
    t.turn_index,
    t.question,
    t.answer,
    t.created_at,
    'admin:' || t.trace_id,
    t.outcome,
    t.fallback_used,
    t.duration_ms,
    t.sources,
    (select count(*) from platform_source_admin.turn_feedback f where f.turn_id = t.id),
    (select count(*) from platform_source_admin.turn_reviews r where r.turn_id = t.id),
    t.source_synced_at,
    t.details || jsonb_build_object(
      'done', t.done,
      'source_groups', t.source_groups,
      'fallback_reason', t.fallback_reason
    )
  from platform_source_admin.chat_turns t
)
select * from metabot
union all select * from fae
union all select * from admin;

create or replace view platform_read.traces as
select
  'metabot:' || r.bot_id || ':' || r.id::text as trace_key,
  'metabot:' || r.bot_id || ':' || r.turn_id::text as turn_key,
  r.bot_id as agent_id,
  'metabot'::text as source_kind,
  r.id::text as native_id,
  r.status,
  r.started_at,
  r.completed_at,
  r.duration_ms,
  r.engine,
  r.backend,
  r.model,
  r.input_tokens,
  r.output_tokens,
  r.cost_usd,
  r.error_class,
  r.error_message,
  'available'::text as detail_availability,
  null::timestamptz as source_synced_at,
  jsonb_build_object('retry_count', r.retry_count,
                     'agent_version', r.agent_version,
                     'prompt_version', r.prompt_version,
                     'knowledge_version', r.knowledge_version) as details
from flywheel_analytics.runs r
where not r.is_synthetic
union all
select
  'fae:' || t.trace_id,
  'fae:' || t.id::text,
  'ai-fae-agent'::text,
  'fae'::text,
  t.trace_id,
  coalesce(t.done->>'status', case when t.answer <> '' then 'completed' else 'failed' end),
  t.created_at,
  case when t.duration_ms is null then null
       else t.created_at + make_interval(secs => t.duration_ms / 1000.0) end,
  t.duration_ms,
  null::text,
  null::text,
  null::text,
  null::bigint,
  null::bigint,
  null::numeric,
  case when t.done ? 'error' then 'agent_error' else null end,
  t.done->>'error',
  case when exists (select 1 from platform_source_fae.trace_spans s where s.trace_id = t.trace_id)
       then 'available' else 'missing' end,
  t.source_synced_at,
  t.details
from platform_source_fae.chat_turns t
union all
select
  'admin:' || t.trace_id,
  'admin:' || t.id::text,
  'ai-admin-agent'::text,
  'admin'::text,
  t.trace_id,
  coalesce(t.done->>'status', case when t.answer <> '' then 'completed' else 'failed' end),
  t.created_at,
  case when t.duration_ms is null then null
       else t.created_at + make_interval(secs => t.duration_ms / 1000.0) end,
  t.duration_ms,
  null::text,
  null::text,
  null::text,
  null::bigint,
  null::bigint,
  null::numeric,
  case when t.done ? 'error' then 'agent_error' else null end,
  t.done->>'error',
  'unavailable'::text,
  t.source_synced_at,
  t.details
from platform_source_admin.chat_turns t;

create or replace view platform_read.trace_steps as
select
  'metabot:' || e.bot_id || ':' || e.id::text as step_key,
  'metabot:' || e.bot_id || ':' || e.run_id::text as trace_key,
  e.bot_id as agent_id,
  'metabot'::text as source_kind,
  case when e.event_type = 'tool_call' then 'tool_call' else 'event' end as kind,
  coalesce(e.payload->>'tool_name', e.event_type) as name,
  e.payload->>'status' as status,
  null::text as parent_step_key,
  e.seq,
  e.occurred_at as started_at,
  null::bigint as duration_ms,
  '{}'::jsonb as input_summary,
  '{}'::jsonb as output_summary,
  e.payload - 'content' - 'input' - 'output' as safe_metadata,
  e.payload->>'error_class' as error_summary,
  null::timestamptz as source_synced_at
from flywheel_analytics.events e
where e.run_id is not null and not e.is_synthetic
union all
select
  'fae:stage:' || t.id::text || ':' || stage.ordinality::text,
  'fae:' || t.trace_id,
  'ai-fae-agent'::text,
  'fae'::text,
  'stage'::text,
  coalesce(stage.value->>'stage', 'stage'),
  stage.value->>'status',
  null::text,
  stage.ordinality::bigint,
  t.created_at + make_interval(secs => coalesce((stage.value->>'elapsed_ms')::numeric, 0) / 1000.0),
  nullif(stage.value->>'duration_ms', '')::bigint,
  '{}'::jsonb,
  '{}'::jsonb,
  stage.value - 'message',
  stage.value->>'error',
  t.source_synced_at
from platform_source_fae.chat_turns t
cross join lateral jsonb_array_elements(t.stages) with ordinality as stage(value, ordinality)
union all
select
  'fae:span:' || s.trace_id || ':' || s.span_id,
  'fae:' || s.trace_id,
  'ai-fae-agent'::text,
  'fae'::text,
  'span'::text,
  s.node,
  case when s.error is null then 'completed' else 'failed' end,
  case when s.parent_span_id is null then null
       else 'fae:span:' || s.trace_id || ':' || s.parent_span_id end,
  null::bigint,
  s.started_at,
  s.duration_ms,
  s.input_summary,
  s.output_summary,
  s.metadata,
  s.error,
  s.source_synced_at
from platform_source_fae.trace_spans s
union all
select
  'admin:stage:' || t.id::text || ':' || stage.ordinality::text,
  'admin:' || t.trace_id,
  'ai-admin-agent'::text,
  'admin'::text,
  'stage'::text,
  coalesce(stage.value->>'stage', 'stage'),
  stage.value->>'status',
  null::text,
  stage.ordinality::bigint,
  t.created_at + make_interval(secs => coalesce((stage.value->>'elapsed_ms')::numeric, 0) / 1000.0),
  nullif(stage.value->>'duration_ms', '')::bigint,
  '{}'::jsonb,
  '{}'::jsonb,
  stage.value - 'message',
  stage.value->>'error',
  t.source_synced_at
from platform_source_admin.chat_turns t
cross join lateral jsonb_array_elements(t.stages) with ordinality as stage(value, ordinality);

create or replace view platform_read.feedback as
select
  'metabot:' || f.id::text as feedback_key,
  'metabot:' || f.bot_id || ':' || f.turn_id::text as turn_key,
  f.bot_id as agent_id,
  'metabot'::text as source_kind,
  case when lower(f.kind) in ('good', 'like', 'positive') then 'positive'
       when lower(f.kind) in ('bad', 'dislike', 'negative') then 'negative'
       else 'other' end as sentiment,
  f.kind as raw_rating,
  f.payload->>'reason_code' as reason_code,
  coalesce(f.raw_text, f.payload->>'comment', '') as comment,
  f.occurred_at as created_at,
  null::timestamptz as source_synced_at,
  f.payload as details
from flywheel_core.feedback f
where not f.is_synthetic
union all
select
  'fae:' || f.id::text,
  'fae:' || f.turn_id::text,
  'ai-fae-agent'::text,
  'fae'::text,
  case f.rating when 'good' then 'positive' when 'bad' then 'negative' else 'other' end,
  f.rating,
  f.reason_code,
  f.comment,
  f.created_at,
  f.source_synced_at,
  f.details
from platform_source_fae.turn_feedback f
union all
select
  'admin:' || f.id::text,
  'admin:' || f.turn_id::text,
  'ai-admin-agent'::text,
  'admin'::text,
  case f.rating when 'good' then 'positive' when 'bad' then 'negative' else 'other' end,
  f.rating,
  f.reason_code,
  f.comment,
  f.created_at,
  f.source_synced_at,
  f.details
from platform_source_admin.turn_feedback f;

create or replace view platform_read.reviews as
select
  'fae:' || r.id::text as review_key,
  'fae:' || r.turn_id::text as turn_key,
  'ai-fae-agent'::text as agent_id,
  'fae'::text as source_kind,
  r.review_status as status,
  r.priority as native_priority,
  case r.priority when 'P0' then 'blocker' when 'P1' then 'high'
       when 'P2' then 'medium' else 'low' end as normalized_priority,
  r.failure_layer,
  r.failure_reason as notes,
  r.corrected_answer,
  r.reviewer,
  r.created_at,
  r.updated_at,
  r.source_synced_at,
  r.details || jsonb_build_object('expected_answer_notes', r.expected_answer_notes,
                                  'should_add_to_eval', r.should_add_to_eval,
                                  'should_update_knowledge', r.should_update_knowledge) as details
from platform_source_fae.turn_reviews r
union all
select
  'admin:' || r.id::text,
  'admin:' || r.turn_id::text,
  'ai-admin-agent'::text,
  'admin'::text,
  r.verdict,
  r.severity,
  r.severity,
  r.failure_layer,
  r.notes,
  ''::text,
  r.reviewer,
  r.created_at,
  r.created_at,
  r.source_synced_at,
  r.details || jsonb_build_object('suggested_action', r.suggested_action,
                                  'capability_batch', r.capability_batch,
                                  'scope', r.scope)
from platform_source_admin.turn_reviews r;

create or replace view platform_read.improvement_items as
select
  'fae:eval:' || e.id::text as item_key,
  'fae:' || e.turn_id::text as turn_key,
  'ai-fae-agent'::text as agent_id,
  'fae'::text as source_kind,
  'evaluation'::text as item_type,
  e.candidate_status as status,
  null::text as priority,
  coalesce(e.testset_name, 'Evaluation candidate') as title,
  coalesce(e.case_json->>'question', '') as summary,
  e.created_at,
  coalesce(e.exported_at, e.created_at) as updated_at,
  e.source_synced_at,
  e.details || jsonb_build_object('case', e.case_json, 'exported_path', e.exported_path) as details
from platform_source_fae.eval_candidates e
union all
select
  'fae:knowledge:' || k.id::text,
  'fae:' || k.turn_id::text,
  'ai-fae-agent'::text,
  'fae'::text,
  'knowledge'::text,
  k.task_status,
  null::text,
  k.knowledge_area,
  k.gap_summary,
  k.created_at,
  k.updated_at,
  k.source_synced_at,
  k.details || jsonb_build_object('proposed_source', k.proposed_source, 'owner', k.owner)
from platform_source_fae.knowledge_improvement_tasks k
union all
select
  'fae:qa:' || q.id::text,
  case when q.turn_id is null then null else 'fae:' || q.turn_id::text end,
  'ai-fae-agent'::text,
  'fae'::text,
  'qa'::text,
  q.review_status,
  null::text,
  left(q.question, 180),
  q.review_notes,
  q.created_at,
  q.updated_at,
  q.source_synced_at,
  q.details || jsonb_build_object('source_type', q.source_type,
                                  'source_ref', q.source_ref,
                                  'product_tags', q.product_tags,
                                  'technical_tags', q.technical_tags,
                                  'reviewed_answer', q.reviewed_answer)
from platform_source_fae.qa_review_items q
union all
select
  'admin:eval:' || e.id::text,
  case when e.source_turn_id is null then null else 'admin:' || e.source_turn_id::text end,
  'ai-admin-agent'::text,
  'admin'::text,
  'evaluation'::text,
  e.status,
  null::text,
  e.case_id,
  e.question,
  e.created_at,
  e.created_at,
  e.source_synced_at,
  e.details || jsonb_build_object('sample_type', e.sample_type,
                                  'capability_batch', e.capability_batch,
                                  'scope', e.scope,
                                  'case', e.case_json)
from platform_source_admin.eval_candidates e
union all
select
  'admin:knowledge:' || k.id::text,
  case when k.source_turn_id is null then null else 'admin:' || k.source_turn_id::text end,
  'ai-admin-agent'::text,
  'admin'::text,
  'knowledge'::text,
  k.status,
  k.priority,
  k.title,
  k.description,
  k.created_at,
  k.updated_at,
  k.source_synced_at,
  k.details || jsonb_build_object('task_type', k.task_type,
                                  'failure_layer', k.failure_layer,
                                  'source_refs', k.source_refs)
from platform_source_admin.knowledge_improvement_tasks k;

create or replace view platform_read.sync_status as
select distinct on (source_kind)
  source_kind,
  status,
  started_at,
  completed_at,
  source_counts,
  applied_counts,
  validation,
  error_summary
from platform_sync.runs
order by source_kind, started_at desc;

do $$
declare
  relation_name text;
begin
  foreach relation_name in array array[
    'platform_source_fae.chat_sessions',
    'platform_source_fae.chat_turns',
    'platform_source_fae.turn_feedback',
    'platform_source_fae.turn_reviews',
    'platform_source_fae.eval_candidates',
    'platform_source_fae.knowledge_improvement_tasks',
    'platform_source_fae.qa_review_items',
    'platform_source_fae.trace_spans',
    'platform_source_admin.chat_sessions',
    'platform_source_admin.chat_turns',
    'platform_source_admin.turn_feedback',
    'platform_source_admin.turn_reviews',
    'platform_source_admin.eval_candidates',
    'platform_source_admin.knowledge_improvement_tasks',
    'platform_sync.runs'
  ] loop
    execute format('alter table %s owner to flywheel_owner', relation_name);
  end loop;

  foreach relation_name in array array[
    'platform_read.sessions',
    'platform_read.turns',
    'platform_read.traces',
    'platform_read.trace_steps',
    'platform_read.feedback',
    'platform_read.reviews',
    'platform_read.improvement_items',
    'platform_read.sync_status'
  ] loop
    execute format('alter view %s owner to flywheel_owner', relation_name);
  end loop;
end
$$;

revoke all on schema platform_source_fae from public;
revoke all on schema platform_source_admin from public;
revoke all on schema platform_sync from public;
revoke all on schema platform_read from public;

revoke all on schema platform_source_fae from flywheel_analyst;
revoke all on schema platform_source_admin from flywheel_analyst;
revoke all on schema platform_sync from flywheel_analyst;

grant usage on schema platform_source_fae to platform_sync_writer;
grant usage on schema platform_source_admin to platform_sync_writer;
grant usage on schema platform_sync to platform_sync_writer;
grant select, insert, update on all tables in schema platform_source_fae to platform_sync_writer;
grant select, insert, update on all tables in schema platform_source_admin to platform_sync_writer;
grant select, insert, update on all tables in schema platform_sync to platform_sync_writer;

grant usage on schema platform_read to flywheel_analyst;
grant select on all tables in schema platform_read to flywheel_analyst;

alter default privileges for role flywheel_owner in schema platform_source_fae
  grant select, insert, update on tables to platform_sync_writer;
alter default privileges for role flywheel_owner in schema platform_source_admin
  grant select, insert, update on tables to platform_sync_writer;
alter default privileges for role flywheel_owner in schema platform_sync
  grant select, insert, update on tables to platform_sync_writer;
alter default privileges for role flywheel_owner in schema platform_read
  grant select on tables to flywheel_analyst;
