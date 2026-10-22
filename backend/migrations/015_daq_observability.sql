\set ON_ERROR_STOP on

begin;

-- `daq` identifies a producer and its mirror. `fae` remains the normalized
-- runtime family; DAQ's canonical agent ID and every source key stay distinct.
create schema if not exists platform_source_daq authorization flywheel_owner;
create table if not exists platform_source_daq.chat_sessions (like platform_source_fae.chat_sessions including all);
create table if not exists platform_source_daq.chat_turns (like platform_source_fae.chat_turns including all);
create table if not exists platform_source_daq.turn_feedback (like platform_source_fae.turn_feedback including all);
create table if not exists platform_source_daq.turn_reviews (like platform_source_fae.turn_reviews including all);
create table if not exists platform_source_daq.eval_candidates (like platform_source_fae.eval_candidates including all);
create table if not exists platform_source_daq.knowledge_improvement_tasks (like platform_source_fae.knowledge_improvement_tasks including all);
create table if not exists platform_source_daq.qa_review_items (like platform_source_fae.qa_review_items including all);
create table if not exists platform_source_daq.trace_spans (like platform_source_fae.trace_spans including all);
alter table platform_source_daq.chat_sessions
  add column if not exists owner_subject_id uuid,
  add column if not exists owner_subject_type text;

-- LIKE does not copy foreign keys. Every FK points inside the DAQ mirror,
-- so identical native UUIDs from camera and DAQ never bind across services.
do $$
declare relation_name text;
begin
  if not exists (select 1 from pg_constraint where conrelid='platform_source_daq.chat_turns'::regclass and conname='daq_turn_session_fk') then
    alter table platform_source_daq.chat_turns add constraint daq_turn_session_fk
      foreign key (session_id) references platform_source_daq.chat_sessions(id);
  end if;
  foreach relation_name in array array['turn_feedback', 'turn_reviews', 'eval_candidates', 'knowledge_improvement_tasks', 'qa_review_items'] loop
    if not exists (select 1 from pg_constraint where conrelid=('platform_source_daq.' || relation_name)::regclass and conname='daq_' || relation_name || '_turn_fk') then
      execute format('alter table platform_source_daq.%I add constraint %I foreign key (turn_id) references platform_source_daq.chat_turns(id)', relation_name, 'daq_' || relation_name || '_turn_fk');
    end if;
  end loop;
end $$;

alter table platform_sync.runs drop constraint if exists runs_source_kind_check;
alter table platform_sync.runs add constraint runs_source_kind_check
  check (source_kind in ('fae', 'admin', 'daq'));

-- Preserve the complete existing identity/timestamp wrappers and their OIDs.
-- New public read views append DAQ; original FAE/Admin/MetaBot keys and rows
-- are retained verbatim. All seven switches happen in this transaction.
do $$
declare relation_name text;
begin
  foreach relation_name in array array['sessions', 'turns', 'traces', 'trace_steps', 'feedback', 'reviews', 'improvement_items'] loop
    if to_regclass('platform_read.' || relation_name || '_without_daq') is null then
      execute format('alter view platform_read.%I rename to %I', relation_name, relation_name || '_without_daq');
    end if;
  end loop;
end $$;

create or replace view platform_read.sessions as
select * from platform_read.sessions_without_daq
union all
select
    'daq:' || s.id::text,
    'ai-daq-fae-agent'::text,
    'fae'::text,
    s.id::text,
    s.channel,
    coalesce(s.conversation_title,
      (select left(t.question, 180) from platform_source_daq.chat_turns t
       where t.session_id = s.id order by t.turn_index limit 1)),
    case when s.owner_subject_type = 'enterprise_member'
           and s.owner_subject_id is not null
           and s.user_id = s.owner_subject_id::text
           and s.details->>'authentication_mode' = 'platform_enterprise'
         then 'daq:' || md5(s.owner_subject_id::text) else null end,
    s.created_at,
    s.last_active_at,
    (select count(*) from platform_source_daq.chat_turns t
     where t.session_id = s.id and nullif(btrim(t.answer), '') is not null),
    (select count(*) from platform_source_daq.turn_feedback f
     where f.external_session_id = s.external_session_id),
    (select count(*) from platform_source_daq.turn_reviews r
     join platform_source_daq.chat_turns t on t.id = r.turn_id where t.session_id = s.id),
    (select t.outcome from platform_source_daq.chat_turns t
     where t.session_id = s.id order by t.turn_index desc limit 1),
    s.source_synced_at,
    s.details || jsonb_build_object('external_session_id', s.external_session_id),
    null::bigint,
    null::text,
    null::text,
    'unavailable'::text
  from platform_source_daq.chat_sessions s;

create or replace view platform_read.turns as
select * from platform_read.turns_without_daq
union all
select
    'daq:' || t.id::text,
    'daq:' || t.session_id::text,
    'ai-daq-fae-agent'::text,
    'fae'::text,
    t.id::text,
    t.turn_index,
    t.question,
    t.answer,
    t.created_at,
    'daq:' || t.trace_id,
    t.outcome,
    t.fallback_used,
    t.duration_ms,
    t.sources,
    (select count(*) from platform_source_daq.turn_feedback f where f.turn_id = t.id),
    (select count(*) from platform_source_daq.turn_reviews r where r.turn_id = t.id),
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
  from platform_source_daq.chat_turns t;

create or replace view platform_read.traces as
select * from platform_read.traces_without_daq
union all
select
  'daq:' || t.trace_id,
  'daq:' || t.id::text,
  'ai-daq-fae-agent'::text,
  'fae'::text,
  t.trace_id,
  coalesce(t.done->>'status', case when t.answer <> '' then 'completed' else 'failed' end),
  coalesce(root.started_at, t.question_at, t.created_at),
  coalesce(root.ended_at, t.answer_at),
  t.duration_ms,
  nullif(t.done->>'engine', ''),
  nullif(t.done->>'backend', ''),
  nullif(t.done->>'actual_provider_model', ''),
  null::bigint,
  null::bigint,
  null::numeric,
  coalesce(t.done->>'error_type', case when root.error is not null then 'agent_error' end),
  coalesce(t.done->>'error', root.error),
  case when root.span_id is not null then 'available' else 'missing' end,
  t.source_synced_at,
  t.details || coalesce(root.metadata, '{}'::jsonb) || jsonb_build_object(
    'done', t.done, 'fallback_used', t.fallback_used,
    'fallback_reason', t.fallback_reason, 'outcome', t.outcome)
from platform_source_daq.chat_turns t
left join lateral (
  select * from platform_source_daq.trace_spans span
  where span.trace_id=t.trace_id and span.parent_span_id is null
    and span.record_type='root'
    and span.metadata->>'agent_id'='ai-daq-fae-agent'
  order by span.started_at, span.span_id limit 1
) root on true;

-- DAQ StageLedger elapsed_ms is a stage duration, not a request offset.
-- Absolute stage times were not persisted; spans retain their real timestamps.
create or replace view platform_read.trace_steps as
select * from platform_read.trace_steps_without_daq
union all
select
  'daq:stage:' || t.id::text || ':' || stage.ordinality::text,
  'daq:' || t.trace_id,
  'ai-daq-fae-agent'::text,
  'fae'::text,
  'stage'::text,
  coalesce(stage.value->>'stage', 'stage'),
  stage.value->>'status',
  null::text,
  stage.ordinality::bigint,
  null::timestamptz,
  coalesce(nullif(stage.value->>'duration_ms', ''), nullif(stage.value->>'elapsed_ms', ''))::bigint,
  '{}'::jsonb,
  '{}'::jsonb,
  (stage.value - 'message') || jsonb_build_object(
    'timing_status', 'unavailable', 'elapsed_ms_semantics', 'stage_duration'),
  stage.value->>'error',
  t.source_synced_at
from platform_source_daq.chat_turns t
cross join lateral jsonb_array_elements(t.stages) with ordinality as stage(value, ordinality)
union all
select
  'daq:span:' || s.trace_id || ':' || s.span_id,
  'daq:' || s.trace_id,
  'ai-daq-fae-agent'::text,
  'fae'::text,
  'span'::text,
  s.node,
  case when s.error is null then 'completed' else 'failed' end,
  case when s.parent_span_id is null then null
       else 'daq:span:' || s.trace_id || ':' || s.parent_span_id end,
  null::bigint,
  s.started_at,
  s.duration_ms,
  s.input_summary,
  s.output_summary,
  s.metadata,
  s.error,
  s.source_synced_at
from platform_source_daq.trace_spans s;

create or replace view platform_read.feedback as
select * from platform_read.feedback_without_daq
union all
select
  'daq:' || f.id::text,
  'daq:' || f.turn_id::text,
  'ai-daq-fae-agent'::text,
  'fae'::text,
  case f.rating when 'good' then 'positive' when 'bad' then 'negative' else 'other' end,
  f.rating,
  f.reason_code,
  f.comment,
  f.created_at,
  f.source_synced_at,
  f.details
from platform_source_daq.turn_feedback f;

create or replace view platform_read.reviews as
select * from platform_read.reviews_without_daq
union all
select
  'daq:' || r.id::text as review_key,
  'daq:' || r.turn_id::text as turn_key,
  'ai-daq-fae-agent'::text as agent_id,
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
from platform_source_daq.turn_reviews r;

create or replace view platform_read.improvement_items as
select * from platform_read.improvement_items_without_daq
union all
select
  'daq:eval:' || e.id::text as item_key,
  'daq:' || e.turn_id::text as turn_key,
  'ai-daq-fae-agent'::text as agent_id,
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
from platform_source_daq.eval_candidates e
union all
select
  'daq:knowledge:' || k.id::text,
  'daq:' || k.turn_id::text,
  'ai-daq-fae-agent'::text,
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
from platform_source_daq.knowledge_improvement_tasks k
union all
select
  'daq:qa:' || q.id::text,
  case when q.turn_id is null then null else 'daq:' || q.turn_id::text end,
  'ai-daq-fae-agent'::text,
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
from platform_source_daq.qa_review_items q;

-- Preserve owners and SELECT ACLs on the existing public names, including
-- the deliberately narrow review-writer grants. Underlying mirrors are private.
do $$
declare relation_name text; grant_row record; owner_name text;
begin
  foreach relation_name in array array['sessions', 'turns', 'traces', 'trace_steps', 'feedback', 'reviews', 'improvement_items'] loop
    select pg_get_userbyid(relowner) into owner_name from pg_class
      where oid=('platform_read.' || relation_name || '_without_daq')::regclass;
    execute format('alter view platform_read.%I owner to %I', relation_name, owner_name);
    execute format('revoke all on platform_read.%I from public, flywheel_ingest', relation_name);
    for grant_row in
      select acl.grantee, acl.is_grantable from pg_class relation,
        lateral aclexplode(coalesce(relation.relacl, acldefault('r', relation.relowner))) acl
      where relation.oid=('platform_read.' || relation_name || '_without_daq')::regclass
        and acl.privilege_type='SELECT' and acl.grantee <> 0
    loop
      execute format('grant select on platform_read.%I to %I%s', relation_name,
        pg_get_userbyid(grant_row.grantee), case when grant_row.is_grantable then ' with grant option' else '' end);
    end loop;
  end loop;
  foreach relation_name in array array['chat_sessions', 'chat_turns', 'turn_feedback', 'turn_reviews', 'eval_candidates', 'knowledge_improvement_tasks', 'qa_review_items', 'trace_spans'] loop
    execute format('alter table platform_source_daq.%I owner to flywheel_owner', relation_name);
  end loop;
end $$;
revoke all on schema platform_source_daq from public, flywheel_ingest, flywheel_analyst, platform_review_writer;
revoke all on all tables in schema platform_source_daq from public, flywheel_ingest, flywheel_analyst, platform_review_writer;
grant usage on schema platform_source_daq to platform_sync_writer;
grant select, insert, update on all tables in schema platform_source_daq to platform_sync_writer;
alter default privileges for role flywheel_owner in schema platform_source_daq
  grant select, insert, update on tables to platform_sync_writer;

comment on schema platform_source_daq is 'Independent DAQ producer mirror; normalized source_kind fae denotes runtime family only.';
commit;
