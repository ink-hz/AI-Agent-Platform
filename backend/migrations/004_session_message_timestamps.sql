\set ON_ERROR_STOP on

alter table platform_source_fae.chat_turns
  add column if not exists question_at timestamptz;
alter table platform_source_fae.chat_turns
  add column if not exists answer_at timestamptz;
alter table platform_source_admin.chat_turns
  add column if not exists question_at timestamptz;
alter table platform_source_admin.chat_turns
  add column if not exists answer_at timestamptz;

create or replace view platform_read.turns as
with identity_display as (
  select user_id,
    coalesce(
      max(nullif(btrim(display_name), '')) filter (where subject_kind = 'union_id'),
      max(nullif(btrim(display_name), ''))
    ) as display_name,
    coalesce(
      max(nullif(btrim(department), '')) filter (where subject_kind = 'union_id'),
      max(nullif(btrim(department), ''))
    ) as department
  from flywheel_identity.external_identities
  where provider = 'feishu'
  group by user_id
), metabot_messages as (
  select
    m.conversation_id,
    m.turn_id,
    min(m.occurred_at) as created_at,
    max(m.content) filter (where m.role = 'user') as question,
    max(m.content) filter (where m.role = 'assistant') as answer,
    (array_agg(m.sender_user_id order by m.occurred_at desc, m.id desc)
      filter (where m.role = 'user' and m.sender_user_id is not null))[1] as sender_user_id,
    min(m.occurred_at) filter (where m.role = 'user') as question_at,
    max(m.occurred_at) filter (where m.role = 'assistant') as answer_at
  from flywheel_analytics.messages m
  where not m.is_synthetic
  group by m.conversation_id, m.turn_id
), metabot as (
  select
    'metabot:' || c.bot_id || ':' || mm.turn_id::text as turn_key,
    'metabot:' || c.bot_id || ':' || c.id::text as session_key,
    case when c.bot_id = 'marketing-bot' then 'marketing-prospecting-bot'
         else c.bot_id end as agent_id,
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
    jsonb_build_object('engine', r.engine, 'model', r.model, 'status', r.status) as details,
    identity.display_name as sender_name,
    identity.department as sender_department,
    case when identity.display_name is null then 'unavailable'
         when identity.department is null then 'name_only'
         else 'resolved' end::text as sender_identity_status,
    mm.question_at,
    mm.answer_at,
    case when mm.question_at is null then 'unavailable' else 'exact' end::text as question_time_status,
    case when mm.answer_at is null then 'unavailable' else 'exact' end::text as answer_time_status
  from metabot_messages mm
  join flywheel_analytics.conversations c on c.id = mm.conversation_id
  left join flywheel_analytics.runs r on r.turn_id = mm.turn_id
  left join identity_display identity on identity.user_id = mm.sender_user_id
  where c.bot_id not in ('pc-bot', 'quality-bot')
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
    ),
    null::text,
    null::text,
    'unavailable'::text,
    coalesce(
      t.question_at,
      case when t.duration_ms is not null and t.duration_ms >= 0
           then t.created_at - (t.duration_ms * interval '1 millisecond') end
    ) as question_at,
    coalesce(t.answer_at, t.created_at) as answer_at,
    case when t.question_at is not null then 'exact'
         when t.duration_ms is not null and t.duration_ms >= 0 then 'estimated'::text
         else 'unavailable' end::text as question_time_status,
    case when t.answer_at is not null then 'exact'
         when t.created_at is not null then 'estimated'::text
         else 'unavailable' end::text as answer_time_status
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
    ),
    null::text,
    null::text,
    'unavailable'::text,
    coalesce(
      t.question_at,
      case when t.duration_ms is not null and t.duration_ms >= 0
           then t.created_at - (t.duration_ms * interval '1 millisecond') end
    ) as question_at,
    coalesce(t.answer_at, t.created_at) as answer_at,
    case when t.question_at is not null then 'exact'
         when t.duration_ms is not null and t.duration_ms >= 0 then 'estimated'::text
         else 'unavailable' end::text as question_time_status,
    case when t.answer_at is not null then 'exact'
         when t.created_at is not null then 'estimated'::text
         else 'unavailable' end::text as answer_time_status
  from platform_source_admin.chat_turns t
)
select * from metabot
union all select * from fae
union all select * from admin;

alter view platform_read.turns owner to flywheel_owner;
grant select on platform_read.turns to flywheel_analyst;
