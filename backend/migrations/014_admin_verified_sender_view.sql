\set ON_ERROR_STOP on

begin;

-- Source display text is shown only after the import created a Platform-verified
-- subject link for the same native Admin session. Historical anonymous rows stay anonymous.
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
      when base.source_kind = 'admin'
        and link.internal_user_id is not null
        then 'admin:' || md5(link.internal_user_id::text)
      else base.user_identity
    end as effective_user_identity,
    case
      when base.source_kind = 'metabot'
        and resolved.name_source in ('manual', 'feishu')
        then resolved.preferred_name
      when base.source_kind = 'admin'
        and link.internal_user_id is not null
        then nullif(btrim(base.details->>'display_name'), '')
      else base.primary_sender_name
    end as effective_name,
    case
      when base.source_kind = 'admin'
        and link.internal_user_id is not null
        then nullif(btrim(base.details->>'primary_department'), '')
      else base.primary_sender_department
    end as effective_department
  from platform_read.sessions_raw_identity base
  left join latest_sender sender
    on base.source_kind = 'metabot'
   and base.native_id = sender.conversation_id::text
  left join flywheel_identity.resolved_user_names resolved
    on resolved.user_id = sender.sender_user_id
  left join platform_identity.session_subject_links link
    on base.source_kind = 'admin'
   and link.source_kind = 'admin'
   and link.native_session_id = base.native_id
)
select
  session_key,
  agent_id,
  source_kind,
  native_id,
  channel,
  title,
  effective_user_identity as user_identity,
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
  effective_department as primary_sender_department,
  case
    when source_kind = 'metabot' and effective_name is null then 'unavailable'
    when source_kind = 'metabot' and effective_department is null then 'name_only'
    when source_kind = 'metabot' then 'resolved'
    when source_kind = 'admin' and effective_name is null then 'unavailable'
    when source_kind = 'admin' and effective_department is null then 'name_only'
    when source_kind = 'admin' then 'resolved'
    else sender_identity_status
  end::text as sender_identity_status
from enriched;

alter view platform_read.sessions owner to flywheel_owner;
revoke all on platform_read.sessions from public, flywheel_ingest;
grant select on platform_read.sessions to flywheel_analyst;

comment on view platform_read.sessions is
  'Sessions with MetaBot sender names and only verified Admin employee names.';

commit;
