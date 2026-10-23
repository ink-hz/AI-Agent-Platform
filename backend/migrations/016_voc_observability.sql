\set ON_ERROR_STOP on
begin;

create schema if not exists platform_source_voc authorization flywheel_owner;
create table platform_source_voc.chat_sessions
  (like platform_source_fae.chat_sessions including all);
create table platform_source_voc.chat_turns
  (like platform_source_fae.chat_turns including all);
alter table platform_source_voc.chat_sessions
  add column owner_subject_id uuid,
  add column owner_subject_type text,
  add column primary_sender_name text,
  add column primary_sender_department text;
alter table platform_source_voc.chat_turns
  alter column trace_id drop not null,
  alter column fallback_used drop not null,
  add constraint voc_turn_session_fk foreign key(session_id)
    references platform_source_voc.chat_sessions(id);
alter table platform_source_voc.chat_sessions owner to flywheel_owner;
alter table platform_source_voc.chat_turns owner to flywheel_owner;

create view platform_source_voc.sessions_projection as
select
  'voc:' || s.id::text as session_key, 'voc'::text as agent_id,
  'voc'::text as source_kind, s.id::text as native_id, s.channel,
  s.conversation_title as title,
  case when s.owner_subject_id is not null then 'voc:' || md5(s.owner_subject_id::text) end as user_identity,
  s.created_at, s.last_active_at,
  (select count(*) from platform_source_voc.chat_turns t
   where t.session_id=s.id and nullif(btrim(t.answer),'') is not null) as turn_count,
  null::bigint as feedback_count, null::bigint as review_count,
  (select t.outcome from platform_source_voc.chat_turns t
   where t.session_id=s.id order by t.turn_index desc limit 1) as latest_outcome,
  s.source_synced_at, s.details,
  case when s.owner_subject_id is not null then 1::bigint end as participant_count,
  s.primary_sender_name, s.primary_sender_department,
  case when s.primary_sender_name is null then 'unavailable'
       when s.primary_sender_department is null then 'name_only'
       else 'resolved' end::text as sender_identity_status
from platform_source_voc.chat_sessions s;

create view platform_source_voc.turns_projection as
select
  'voc:' || t.id::text as turn_key, 'voc:' || t.session_id::text as session_key,
  'voc'::text as agent_id, 'voc'::text as source_kind, t.id::text as native_id,
  t.turn_index, t.question, t.answer, t.created_at,
  null::text as trace_key, t.outcome, t.fallback_used, t.duration_ms, t.sources,
  null::bigint as feedback_count, null::bigint as review_count,
  t.source_synced_at, t.details,
  s.primary_sender_name as sender_name, s.primary_sender_department as sender_department,
  case when s.primary_sender_name is null then 'unavailable'
       when s.primary_sender_department is null then 'name_only'
       else 'resolved' end::text as sender_identity_status,
  t.question_at, t.answer_at,
  case when t.question_at is null then 'unavailable' else 'exact' end::text as question_time_status,
  case when t.answer_at is null then 'unavailable' else 'exact' end::text as answer_time_status
from platform_source_voc.chat_turns t
join platform_source_voc.chat_sessions s on s.id=t.session_id;
alter view platform_source_voc.sessions_projection owner to flywheel_owner;
alter view platform_source_voc.turns_projection owner to flywheel_owner;

-- Append to the exact deployed definitions, retaining public view OIDs, grants,
-- identity wrappers and every existing producer. No per-page fallback is added.
do $$
declare name text; definition text;
begin
  foreach name in array array['sessions','turns'] loop
    definition := regexp_replace(pg_get_viewdef(('platform_read.' || name)::regclass, true), ';[[:space:]]*$', '');
    execute format('create or replace view platform_read.%I as %s union all select * from platform_source_voc.%I',
      name, definition, name || '_projection');
  end loop;
end $$;

alter table platform_sync.runs drop constraint if exists runs_source_kind_check;
alter table platform_sync.runs add constraint runs_source_kind_check
  check(source_kind in ('fae','admin','daq','voc'));
revoke all on schema platform_source_voc from public, flywheel_ingest, flywheel_analyst, platform_review_writer;
revoke all on all tables in schema platform_source_voc from public, flywheel_ingest, flywheel_analyst, platform_review_writer;
grant usage on schema platform_source_voc to platform_sync_writer;
grant select,insert,update on platform_source_voc.chat_sessions,platform_source_voc.chat_turns to platform_sync_writer;
commit;
