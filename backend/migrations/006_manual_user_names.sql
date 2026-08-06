\set ON_ERROR_STOP on

begin;

do $$
begin
  if to_regclass('platform_read.sessions_raw_identity') is null then
    alter view platform_read.sessions rename to sessions_raw_identity;
  end if;
  if to_regclass('platform_read.turns_raw_identity') is null then
    alter view platform_read.turns rename to turns_raw_identity;
  end if;
end
$$;

create or replace view platform_read.sessions as
with latest_sender as (
  select distinct on (conversation_id)
    conversation_id,
    sender_user_id
  from flywheel_analytics.messages
  where role = 'user'
    and sender_user_id is not null
  order by conversation_id, occurred_at desc, id desc
), enriched as (
  select
    base.*,
    case
      when base.source_kind = 'metabot'
        and resolved.name_source in ('manual', 'feishu')
        then resolved.preferred_name
      else base.primary_sender_name
    end as effective_name
  from platform_read.sessions_raw_identity base
  left join latest_sender sender
    on base.source_kind = 'metabot'
   and base.native_id = sender.conversation_id::text
  left join flywheel_identity.resolved_user_names resolved
    on resolved.user_id = sender.sender_user_id
)
select
  session_key,
  agent_id,
  source_kind,
  native_id,
  channel,
  title,
  user_identity,
  created_at,
  last_active_at,
  turn_count,
  feedback_count,
  review_count,
  latest_outcome,
  source_synced_at,
  details,
  participant_count,
  effective_name as primary_sender_name,
  primary_sender_department,
  case
    when source_kind = 'metabot' and effective_name is null then 'unavailable'
    when source_kind = 'metabot' and primary_sender_department is null then 'name_only'
    when source_kind = 'metabot' then 'resolved'
    else sender_identity_status
  end::text as sender_identity_status
from enriched;

create or replace view platform_read.turns as
with turn_sender as (
  select distinct on (turn_id)
    turn_id,
    sender_user_id
  from flywheel_analytics.messages
  where role = 'user'
    and sender_user_id is not null
    and not is_synthetic
  order by turn_id, occurred_at desc, id desc
), enriched as (
  select
    base.*,
    case
      when base.source_kind = 'metabot'
        and resolved.name_source in ('manual', 'feishu')
        then resolved.preferred_name
      else base.sender_name
    end as effective_name
  from platform_read.turns_raw_identity base
  left join turn_sender sender
    on base.source_kind = 'metabot'
   and base.native_id = sender.turn_id::text
  left join flywheel_identity.resolved_user_names resolved
    on resolved.user_id = sender.sender_user_id
)
select
  turn_key,
  session_key,
  agent_id,
  source_kind,
  native_id,
  turn_index,
  question,
  answer,
  created_at,
  trace_key,
  outcome,
  fallback_used,
  duration_ms,
  sources,
  feedback_count,
  review_count,
  source_synced_at,
  details,
  effective_name as sender_name,
  sender_department,
  case
    when source_kind = 'metabot' and effective_name is null then 'unavailable'
    when source_kind = 'metabot' and sender_department is null then 'name_only'
    when source_kind = 'metabot' then 'resolved'
    else sender_identity_status
  end::text as sender_identity_status,
  question_at,
  answer_at,
  question_time_status,
  answer_time_status
from enriched;

alter view platform_read.sessions owner to flywheel_owner;
alter view platform_read.turns owner to flywheel_owner;

revoke all on platform_read.sessions_raw_identity
  from public, flywheel_ingest, flywheel_analyst;
revoke all on platform_read.turns_raw_identity
  from public, flywheel_ingest, flywheel_analyst;
revoke all on platform_read.sessions, platform_read.turns
  from public, flywheel_ingest;
grant select on platform_read.sessions, platform_read.turns to flywheel_analyst;

comment on view platform_read.sessions is
  'All sessions with MetaBot sender names resolved through owner-confirmed Flywheel names.';
comment on view platform_read.turns is
  'All turns with MetaBot sender names resolved through owner-confirmed Flywheel names.';

commit;
