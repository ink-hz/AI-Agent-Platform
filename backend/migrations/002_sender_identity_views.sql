\set ON_ERROR_STOP on

create or replace view platform_read.sessions as
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
), latest_sender as (
  select distinct on (conversation_id)
    conversation_id, sender_user_id
  from flywheel_analytics.messages
  where role = 'user' and sender_user_id is not null
  order by conversation_id, occurred_at desc, id desc
), metabot as (
  select
    'metabot:' || c.bot_id || ':' || c.id::text as session_key,
    case when c.bot_id = 'marketing-bot' then 'marketing-prospecting-bot'
         else c.bot_id end as agent_id,
    'metabot'::text as source_kind,
    c.id::text as native_id,
    c.platform as channel,
    coalesce(
      (select left(first_message.content, 180) from flywheel_analytics.messages first_message
       where first_message.conversation_id = c.id and first_message.role = 'user'
       order by first_message.occurred_at limit 1),
      c.platform_conversation_id
    ) as title,
    null::text as user_identity,
    c.created_at,
    coalesce(max(m.occurred_at), c.updated_at) as last_active_at,
    count(distinct m.turn_id) filter (
      where m.role = 'assistant' and nullif(btrim(m.content), '') is not null
    )::bigint as turn_count,
    count(distinct f.id)::bigint as feedback_count,
    0::bigint as review_count,
    null::text as latest_outcome,
    null::timestamptz as source_synced_at,
    jsonb_build_object('business_domain', c.business_domain,
                       'conversation_type', c.conversation_type) as details,
    count(distinct m.sender_user_id) filter (
      where m.role = 'user' and m.sender_user_id is not null
    )::bigint as participant_count,
    identity.display_name as primary_sender_name,
    identity.department as primary_sender_department,
    case when identity.display_name is null then 'unavailable'
         when identity.department is null then 'name_only'
         else 'resolved' end::text as sender_identity_status
  from flywheel_analytics.conversations c
  left join flywheel_analytics.messages m on m.conversation_id = c.id
  left join flywheel_core.feedback f on f.conversation_id = c.id
  left join latest_sender on latest_sender.conversation_id = c.id
  left join identity_display identity on identity.user_id = latest_sender.sender_user_id
  where not c.is_synthetic
    and c.bot_id not in ('pc-bot', 'quality-bot')
  group by c.id, c.bot_id, c.platform, c.platform_conversation_id,
           c.created_at, c.updated_at, c.business_domain, c.conversation_type,
           identity.display_name, identity.department
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
    (select count(*) from platform_source_fae.chat_turns t
     where t.session_id = s.id and nullif(btrim(t.answer), '') is not null),
    (select count(*) from platform_source_fae.turn_feedback f
     where f.external_session_id = s.external_session_id),
    (select count(*) from platform_source_fae.turn_reviews r
     join platform_source_fae.chat_turns t on t.id = r.turn_id where t.session_id = s.id),
    (select t.outcome from platform_source_fae.chat_turns t
     where t.session_id = s.id order by t.turn_index desc limit 1),
    s.source_synced_at,
    s.details,
    null::bigint,
    null::text,
    null::text,
    'unavailable'::text
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
    (select count(*) from platform_source_admin.chat_turns t
     where t.session_id = s.id and nullif(btrim(t.answer), '') is not null),
    (select count(*) from platform_source_admin.turn_feedback f
     where f.external_session_id = s.external_session_id),
    (select count(*) from platform_source_admin.turn_reviews r
     join platform_source_admin.chat_turns t on t.id = r.turn_id where t.session_id = s.id),
    (select t.outcome from platform_source_admin.chat_turns t
     where t.session_id = s.id order by t.turn_index desc limit 1),
    s.source_synced_at,
    s.details,
    null::bigint,
    null::text,
    null::text,
    'unavailable'::text
  from platform_source_admin.chat_sessions s
)
select * from metabot
union all select * from fae
union all select * from admin;

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
      filter (where m.role = 'user' and m.sender_user_id is not null))[1] as sender_user_id
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
         else 'resolved' end::text as sender_identity_status
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
    'unavailable'::text
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
    'unavailable'::text
  from platform_source_admin.chat_turns t
)
select * from metabot
union all select * from fae
union all select * from admin;

alter view platform_read.sessions owner to flywheel_owner;
alter view platform_read.turns owner to flywheel_owner;
grant select on platform_read.sessions, platform_read.turns to flywheel_analyst;
