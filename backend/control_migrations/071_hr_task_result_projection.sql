create table platform_hr.hr_task_result_projections (
  projection_id uuid primary key,
  task_record_id uuid not null references platform_hr.position_task_records(task_record_id),
  task_request_id uuid not null references platform_hr.position_task_requests(task_request_id),
  projection_request_id uuid not null,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  position_id uuid not null,
  projection_kind text not null check (projection_kind in ('context','analysis')),
  state text not null check (state in ('pending','processing','completed','failed')),
  worker_id text,
  lease_expires_at timestamptz,
  available_at timestamptz not null default now(),
  attempt_count integer not null default 0 check (attempt_count>=0),
  projected_resource_id uuid,
  error_code text check (
    error_code is null or error_code in (
      'result_invalid','projection_scope_invalid','projection_unavailable'
    )
  ),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  completed_at timestamptz,
  unique (task_record_id),
  unique (projection_request_id),
  foreign key (position_id,owner_internal_user_id)
    references platform_hr.positions(position_id,owner_internal_user_id),
  check (
    (state='processing' and worker_id is not null and lease_expires_at is not null)
    or (state<>'processing' and worker_id is null and lease_expires_at is null)
  ),
  check ((state='completed')=(projected_resource_id is not null)),
  check ((state='completed')=(completed_at is not null)),
  check ((state='failed')=(error_code is not null))
);

create index hr_task_result_projections_available_v71
  on platform_hr.hr_task_result_projections(state,available_at,lease_expires_at);

create function platform_hr.read_hr_task_result_projection_state_v71(
  selected_task_record_id uuid
) returns table(state text,error_code text)
language sql stable security definer set search_path=pg_catalog,platform_hr
as $function$
  select projection.state,projection.error_code
  from platform_hr.hr_task_result_projections projection
  where projection.task_record_id=selected_task_record_id
    and session_user in ('platform_control_app','platform_control_app_preview')
$function$;

create function platform_hr.claim_hr_task_result_projection_v71(
  selected_worker_id text,
  selected_lease_seconds integer
) returns table(
  task_record_id uuid,
  task_request_id uuid,
  projection_request_id uuid,
  owner_internal_user_id uuid,
  position_id uuid,
  task_kind text,
  official_position_version_id uuid,
  context_version_id uuid,
  material_attachment_ids uuid[],
  candidate_id uuid,
  position_candidate_id uuid,
  document_ids uuid[],
  human_feedback_ids uuid[],
  conversation_id uuid,
  turn_id uuid,
  output_artifact_version_id uuid,
  assistant_message_id uuid,
  agent_id text,
  content_ciphertext bytea,
  encryption_key_version integer
)
language plpgsql security definer set search_path=pg_catalog,platform_hr
as $function$
declare selected_record_id uuid;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or selected_worker_id is null
     or selected_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
     or selected_lease_seconds not between 30 and 900 then
    raise insufficient_privilege;
  end if;

  select record.task_record_id into selected_record_id
  from platform_hr.position_task_records record
  join platform_hr.position_task_requests request
    on request.owner_internal_user_id=record.owner_internal_user_id
   and request.position_id=record.position_id
   and request.client_request_id=record.client_request_id
   and request.task_kind=record.task_kind
   and request.expected_context_version_id is not distinct from record.context_version_id
   and request.material_attachment_ids=record.material_attachment_ids
   and request.candidate_id is not distinct from record.candidate_id
   and request.position_candidate_id is not distinct from record.position_candidate_id
   and request.status='consumed'
  join platform_hr.position_conversations binding
    on binding.conversation_id=record.conversation_id
   and binding.owner_internal_user_id=record.owner_internal_user_id
   and binding.position_id=record.position_id
  join platform_control.conversations conversation
    on conversation.conversation_id=record.conversation_id
   and conversation.owner_internal_user_id=record.owner_internal_user_id
   and conversation.mode='direct_agent'
   and conversation.direct_agent_id='hr-bot'
  join platform_control.conversation_turns turn
    on turn.turn_id=record.turn_id
   and turn.conversation_id=record.conversation_id
   and turn.client_request_id=record.client_request_id
   and turn.status='completed'
  join platform_control.missions mission
    on mission.mission_id=turn.mission_id
   and mission.owner_internal_user_id=record.owner_internal_user_id
   and mission.client_request_id=turn.turn_id
   and mission.mode='direct_agent'
   and mission.direct_agent_id='hr-bot'
   and mission.status='completed'
  join platform_control.mission_runs run
    on run.mission_id=mission.mission_id
   and run.phase='direct' and run.agent_id='hr-bot' and run.status='completed'
  join platform_control.execution_jobs execution
    on execution.run_id=run.run_id
   and execution.agent_id='hr-bot' and execution.status='completed'
  join platform_control.conversation_messages message
    on message.message_id=turn.assistant_message_id
   and message.conversation_id=record.conversation_id
   and message.turn_id=record.turn_id
   and message.mission_id=mission.mission_id
   and message.role='assistant'
   and message.delivery_status='completed'
  left join platform_hr.hr_task_result_projections projection
    on projection.task_record_id=record.task_record_id
  where record.task_kind in (
    'jd','jr','talent_profile','sourcing_strategy','position_interview_plan',
    'candidate_match','candidate_interview_plan'
  )
    and platform_hr.validate_candidate_task_inputs_v69(
      record.owner_internal_user_id,record.position_id,record.context_version_id,
      record.candidate_id,record.position_candidate_id,
      record.document_attachment_ids,record.human_feedback_ids
    )
    and (
      projection.task_record_id is null
      or (projection.state='pending' and projection.available_at<=now())
      or (projection.state='processing' and projection.lease_expires_at<=now())
    )
    and (select count(distinct execution.job_id)
         from platform_control.mission_runs counted_run
         join platform_control.execution_jobs execution
           on execution.run_id=counted_run.run_id
          and execution.agent_id='hr-bot' and execution.status='completed'
         where counted_run.mission_id=mission.mission_id
           and counted_run.phase='direct' and counted_run.agent_id='hr-bot'
           and counted_run.status='completed')=1
    and (select count(distinct message.message_id)
         from platform_control.conversation_messages message
         where message.message_id=turn.assistant_message_id
           and message.conversation_id=record.conversation_id
           and message.turn_id=record.turn_id
           and message.mission_id=mission.mission_id
           and message.role='assistant'
           and message.delivery_status='completed')=1
  order by record.created_at,record.task_record_id
  for update of record skip locked limit 1;

  if selected_record_id is null then return; end if;

  insert into platform_hr.hr_task_result_projections(
    projection_id,task_record_id,task_request_id,projection_request_id,
    owner_internal_user_id,position_id,projection_kind,state,worker_id,
    lease_expires_at,attempt_count
  )
  select md5(record.task_record_id::text||':projection-ledger')::uuid,
    record.task_record_id,request.task_request_id,
    md5(record.task_record_id::text||':result-projection')::uuid,
    record.owner_internal_user_id,record.position_id,
    case when record.task_kind in ('candidate_match','candidate_interview_plan')
      then 'analysis' else 'context' end,
    'processing',selected_worker_id,
    now()+make_interval(secs=>selected_lease_seconds),1
  from platform_hr.position_task_records record
  join platform_hr.position_task_requests request
    on request.owner_internal_user_id=record.owner_internal_user_id
   and request.position_id=record.position_id
   and request.client_request_id=record.client_request_id
   and request.task_kind=record.task_kind
  where record.task_record_id=selected_record_id
  on conflict on constraint hr_task_result_projections_task_record_id_key
  do update set
    state='processing',worker_id=excluded.worker_id,
    lease_expires_at=excluded.lease_expires_at,
    attempt_count=platform_hr.hr_task_result_projections.attempt_count+1,
    error_code=null,updated_at=now();

  return query
  select record.task_record_id,request.task_request_id,
    projection.projection_request_id,record.owner_internal_user_id,
    record.position_id,record.task_kind,record.official_position_version_id,
    record.context_version_id,record.material_attachment_ids,
    record.candidate_id,record.position_candidate_id,
    coalesce((select array_agg(document.document_id order by requested.ordinality)
      from unnest(record.document_attachment_ids) with ordinality
        requested(attachment_id,ordinality)
      join platform_hr.candidate_documents document
        on document.owner_internal_user_id=record.owner_internal_user_id
       and document.candidate_id=record.candidate_id
       and document.attachment_id=requested.attachment_id
       and document.status='active'),'{}'::uuid[]),
    record.human_feedback_ids,record.conversation_id,record.turn_id,
    record.output_artifact_version_id,turn.assistant_message_id,'hr-bot'::text,
    message.content_ciphertext,message.encryption_key_version
  from platform_hr.position_task_records record
  join platform_hr.position_task_requests request
    on request.owner_internal_user_id=record.owner_internal_user_id
   and request.position_id=record.position_id
   and request.client_request_id=record.client_request_id
   and request.task_kind=record.task_kind
  join platform_hr.hr_task_result_projections projection
    on projection.task_record_id=record.task_record_id
   and projection.worker_id=selected_worker_id and projection.state='processing'
  join platform_control.conversation_turns turn
    on turn.turn_id=record.turn_id and turn.conversation_id=record.conversation_id
  join platform_control.conversation_messages message
    on message.message_id=turn.assistant_message_id
   and message.conversation_id=record.conversation_id
  where record.task_record_id=selected_record_id;
end
$function$;

create function platform_hr.complete_hr_task_result_projection_v71(
  selected_task_record_id uuid,selected_worker_id text,
  selected_projection_request_id uuid,selected_resource_id uuid
) returns boolean
language plpgsql security definer set search_path=pg_catalog,platform_hr
as $function$
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or selected_resource_id is null then raise insufficient_privilege; end if;
  update platform_hr.hr_task_result_projections set
    state='completed',worker_id=null,lease_expires_at=null,
    projected_resource_id=selected_resource_id,error_code=null,
    completed_at=now(),updated_at=now()
  where task_record_id=selected_task_record_id
    and projection_request_id=selected_projection_request_id
    and state='processing' and worker_id=selected_worker_id
    and lease_expires_at>now();
  if found then return true; end if;
  return exists(select 1 from platform_hr.hr_task_result_projections
    where task_record_id=selected_task_record_id
      and projection_request_id=selected_projection_request_id
      and state='completed' and projected_resource_id=selected_resource_id);
end
$function$;

create function platform_hr.fail_hr_task_result_projection_v71(
  selected_task_record_id uuid,selected_worker_id text,
  selected_projection_request_id uuid,selected_error_code text
) returns boolean
language plpgsql security definer set search_path=pg_catalog,platform_hr
as $function$
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or selected_error_code not in ('result_invalid','projection_scope_invalid')
  then raise insufficient_privilege; end if;
  update platform_hr.hr_task_result_projections set
    state='failed',worker_id=null,lease_expires_at=null,error_code=selected_error_code,
    updated_at=now()
  where task_record_id=selected_task_record_id
    and projection_request_id=selected_projection_request_id
    and state='processing' and worker_id=selected_worker_id
    and lease_expires_at>now();
  return found;
end
$function$;

create function platform_hr.release_hr_task_result_projection_v71(
  selected_task_record_id uuid,selected_worker_id text,
  selected_projection_request_id uuid,selected_error_code text
) returns boolean
language plpgsql security definer set search_path=pg_catalog,platform_hr
as $function$
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or selected_error_code<>'projection_unavailable'
  then raise insufficient_privilege; end if;
  update platform_hr.hr_task_result_projections set
    state='pending',worker_id=null,lease_expires_at=null,
    available_at=now()+interval '5 seconds',error_code=null,
    updated_at=now()
  where task_record_id=selected_task_record_id
    and projection_request_id=selected_projection_request_id
    and state='processing' and worker_id=selected_worker_id
    and lease_expires_at>now();
  return found;
end
$function$;

revoke all on table platform_hr.hr_task_result_projections from public;
revoke all on function platform_hr.read_hr_task_result_projection_state_v71(uuid) from public;
revoke all on function platform_hr.claim_hr_task_result_projection_v71(text,integer) from public;
revoke all on function platform_hr.complete_hr_task_result_projection_v71(uuid,text,uuid,uuid) from public;
revoke all on function platform_hr.fail_hr_task_result_projection_v71(uuid,text,uuid,text) from public;
revoke all on function platform_hr.release_hr_task_result_projection_v71(uuid,text,uuid,text) from public;

do $migration$
declare selected_app name;
begin
  if current_database()='agent_platform_control'
     and current_user='platform_control_owner' then
    selected_app := 'platform_control_app';
  elsif current_database()='agent_platform_control_preview'
        and current_user='platform_control_owner_preview' then
    selected_app := 'platform_control_app_preview';
  else
    raise insufficient_privilege using
      message='HR result projection migration owner/environment mismatch';
  end if;
  execute format('grant usage on schema platform_hr to %I',selected_app);
  execute format('grant execute on function platform_hr.read_hr_task_result_projection_state_v71(uuid) to %I',selected_app);
  execute format('grant execute on function platform_hr.claim_hr_task_result_projection_v71(text,integer) to %I',selected_app);
  execute format('grant execute on function platform_hr.complete_hr_task_result_projection_v71(uuid,text,uuid,uuid) to %I',selected_app);
  execute format('grant execute on function platform_hr.fail_hr_task_result_projection_v71(uuid,text,uuid,text) to %I',selected_app);
  execute format('grant execute on function platform_hr.release_hr_task_result_projection_v71(uuid,text,uuid,text) to %I',selected_app);
end
$migration$;
