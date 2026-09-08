-- HR v6: one immutable per-turn scope, existing position record and execution base.
alter table platform_control.conversation_turns
  add column hr_input_context jsonb check (
    hr_input_context is null or (jsonb_typeof(hr_input_context)='object'
      and octet_length(hr_input_context::text)<=16384)
  );
alter table platform_hr.position_task_records
  add column contract_version text not null default 'legacy'
    check (contract_version in ('legacy','core_chat_collaboration_v6')),
  add column role_package jsonb,
  add column method_selection jsonb;

create function platform_hr.validate_turn_scope_v6(
  selected_owner uuid, selected_conversation uuid, selected_turn uuid,
  selected_context jsonb
) returns void language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare scope jsonb; selected_position uuid; attachment_ids uuid[]; candidate_ids uuid[];
begin
  if session_user not in ('platform_control_app','platform_control_app_preview',
      'platform_brain_worker','platform_brain_worker_preview') then raise insufficient_privilege; end if;
  perform 1 from platform_control.conversations conversation
  join platform_control.conversation_turns turn on turn.conversation_id=conversation.conversation_id
  where conversation.owner_internal_user_id=selected_owner
    and conversation.conversation_id=selected_conversation and turn.turn_id=selected_turn
    and conversation.mode='direct_agent' and conversation.direct_agent_id='hr-bot';
  if not found then raise no_data_found using message='HR turn scope unavailable'; end if;
  if selected_context is null or jsonb_typeof(selected_context)<>'object'
    or not selected_context ?& array['scope','methodSelection']
    or selected_context-'scope'-'methodSelection'<>'{}'::jsonb
    or octet_length(selected_context::text)>16384 then raise check_violation; end if;
  scope := selected_context->'scope';
  if jsonb_typeof(scope)<>'object'
    or not scope ?& array['positionId','positionCandidateIds','attachmentIds']
    or scope-'positionId'-'positionCandidateIds'-'attachmentIds'<>'{}'::jsonb
    or jsonb_typeof(scope->'positionCandidateIds')<>'array'
    or jsonb_typeof(scope->'attachmentIds')<>'array' then raise check_violation; end if;
  selected_position := (scope->>'positionId')::uuid;
  select coalesce(array_agg(value::uuid),'{}'::uuid[]) into attachment_ids
    from jsonb_array_elements_text(scope->'attachmentIds');
  select coalesce(array_agg(value::uuid),'{}'::uuid[]) into candidate_ids
    from jsonb_array_elements_text(scope->'positionCandidateIds');
  if cardinality(attachment_ids)>32 or cardinality(candidate_ids)>10
    or cardinality(attachment_ids)<>(select count(distinct v) from unnest(attachment_ids) v)
    or cardinality(candidate_ids)<>(select count(distinct v) from unnest(candidate_ids) v)
    or (selected_position is null and cardinality(candidate_ids)>0) then raise check_violation; end if;
  if selected_position is not null then
    perform 1 from platform_hr.positions where position_id=selected_position
      and owner_internal_user_id=selected_owner and internal_status='active';
    if not found then raise no_data_found using message='HR position unavailable'; end if;
  end if;
  if exists(select 1 from unnest(candidate_ids) selected(id)
    left join platform_hr.position_candidates candidate on candidate.position_candidate_id=selected.id
      and candidate.owner_internal_user_id=selected_owner and candidate.position_id=selected_position
      and candidate.status='active'
    where candidate.position_candidate_id is null) then raise no_data_found; end if;
  if exists(select 1 from unnest(attachment_ids) selected(id)
    left join platform_attachments.attachments attachment on attachment.attachment_id=selected.id
      and attachment.owner_internal_user_id=selected_owner
    where attachment.attachment_id is null or attachment.state<>'ready'
      or attachment.retained_until<=clock_timestamp() or attachment.deleted_at is not null
      or attachment.immutable_locator is null or attachment.sha256 is null
      or exists(select 1 from platform_attachments.erasure_jobs erasure where erasure.attachment_id=selected.id)
      or not (
        exists(select 1 from platform_attachments.bindings binding where binding.attachment_id=selected.id
          and binding.owner_internal_user_id=selected_owner and binding.conversation_id=selected_conversation
          and binding.turn_id=selected_turn and binding.kind='turn_input')
        or exists(select 1 from platform_hr.position_materials material where material.attachment_id=selected.id
          and material.owner_internal_user_id=selected_owner and material.position_id=selected_position and material.active)
      )) then raise no_data_found using message='HR material unavailable'; end if;
  if exists(select 1 from platform_attachments.bindings binding
    where binding.owner_internal_user_id=selected_owner and binding.conversation_id=selected_conversation
      and binding.turn_id=selected_turn and binding.kind='turn_input'
      and not binding.attachment_id=any(attachment_ids)) then raise check_violation using message='HR scope omits input'; end if;
end
$function$;

create function platform_hr.record_turn_scope_v6(
  selected_owner uuid, selected_conversation uuid, selected_turn uuid, selected_context jsonb
) returns jsonb language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare turn_row platform_control.conversation_turns%rowtype; selected_position uuid;
begin
  perform platform_hr.validate_turn_scope_v6(selected_owner,selected_conversation,selected_turn,selected_context);
  select * into turn_row from platform_control.conversation_turns
    where conversation_id=selected_conversation and turn_id=selected_turn for update;
  if turn_row.hr_input_context is not null then
    if turn_row.hr_input_context<>selected_context then
      raise unique_violation using message='HR turn scope idempotency mismatch';
    end if;
    return turn_row.hr_input_context;
  end if;
  if turn_row.status<>'accepted' then raise check_violation using message='HR scope already dispatched'; end if;
  update platform_control.conversation_turns set hr_input_context=selected_context
    where turn_id=selected_turn;
  selected_position := (selected_context->'scope'->>'positionId')::uuid;
  if selected_position is not null then
    insert into platform_hr.position_task_records(
      task_record_id,owner_internal_user_id,position_id,client_request_id,task_kind,
      conversation_id,turn_id,prompt_context,canonical_sha256,material_attachment_ids,
      contract_version,method_selection
    ) values (
      md5(selected_turn::text || ':hr-scope-v6')::uuid,selected_owner,selected_position,
      turn_row.client_request_id,'freeform',selected_conversation,selected_turn,
      selected_context::text,encode(sha256(convert_to(selected_context::text,'UTF8')),'hex'),
      array(select value::uuid from jsonb_array_elements_text(selected_context->'scope'->'attachmentIds')),
      'core_chat_collaboration_v6',selected_context->'methodSelection'
    );
  end if;
  return selected_context;
end
$function$;

create function platform_hr.read_turn_scope_v6(
  selected_owner uuid, selected_conversation uuid, selected_turn uuid
) returns jsonb language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected_context jsonb;
begin
  select turn.hr_input_context into selected_context
  from platform_control.conversation_turns turn
  join platform_control.conversations conversation on conversation.conversation_id=turn.conversation_id
  where turn.turn_id=selected_turn and turn.conversation_id=selected_conversation
    and conversation.owner_internal_user_id=selected_owner;
  if selected_context is null then raise no_data_found using message='HR turn scope unavailable'; end if;
  perform platform_hr.validate_turn_scope_v6(selected_owner,selected_conversation,selected_turn,selected_context);
  return selected_context;
end
$function$;

create function platform_hr.guard_turn_scope_v6() returns trigger language plpgsql
set search_path=pg_catalog,platform_hr as $function$
begin
  if old.hr_input_context is not null and new.hr_input_context is distinct from old.hr_input_context then
    raise check_violation using message='HR turn scope is immutable';
  end if;
  return new;
end
$function$;
create trigger guard_hr_turn_scope_v6 before update of hr_input_context
  on platform_control.conversation_turns for each row execute function platform_hr.guard_turn_scope_v6();

revoke all on function platform_hr.validate_turn_scope_v6(uuid,uuid,uuid,jsonb) from public;
revoke all on function platform_hr.record_turn_scope_v6(uuid,uuid,uuid,jsonb) from public;
revoke all on function platform_hr.read_turn_scope_v6(uuid,uuid,uuid) from public;
do $grant$
declare app_role name; brain_role name;
begin
  if current_database()='agent_platform_control' and current_user='platform_control_owner' then
    app_role := 'platform_control_app'; brain_role := 'platform_brain_worker';
  elsif current_database()='agent_platform_control_preview' and current_user='platform_control_owner_preview' then
    app_role := 'platform_control_app_preview'; brain_role := 'platform_brain_worker_preview';
  else raise insufficient_privilege; end if;
  execute format('grant execute on function platform_hr.record_turn_scope_v6(uuid,uuid,uuid,jsonb) to %I,%I',app_role,brain_role);
  execute format('grant execute on function platform_hr.read_turn_scope_v6(uuid,uuid,uuid) to %I,%I',app_role,brain_role);
end
$grant$;

-- Candidate parser identity belongs to one source turn, never an entire chat.
create function platform_hr.candidate_parser_scope_valid_v6(
  selected_owner uuid, selected_conversation uuid, selected_turn uuid, selected_draft uuid
) returns boolean language sql stable security definer
set search_path=pg_catalog,platform_hr
as $function$
  select session_user in ('platform_control_app','platform_control_app_preview',
    'platform_brain_worker','platform_brain_worker_preview') and exists (
    select 1 from platform_control.conversation_turns turn
    join platform_control.conversations conversation on conversation.conversation_id=turn.conversation_id
    join platform_hr.candidate_drafts draft on draft.draft_id=selected_draft
      and draft.owner_internal_user_id=conversation.owner_internal_user_id
    where turn.turn_id=selected_turn and turn.conversation_id=selected_conversation
      and conversation.owner_internal_user_id=selected_owner
      and conversation.mode='direct_agent' and conversation.direct_agent_id='hr-bot'
      and turn.hr_input_context is not null
      and ((turn.hr_input_context->'scope'->>'positionId') is null
        or (turn.hr_input_context->'scope'->>'positionId')::uuid=draft.position_id)
      and turn.hr_input_context->'scope'->'positionCandidateIds'='[]'::jsonb
  )
$function$;
revoke all on function platform_hr.candidate_parser_scope_valid_v6(uuid,uuid,uuid,uuid) from public;
do $grant$
declare app_role name; brain_role name;
begin
  if current_database()='agent_platform_control' and current_user='platform_control_owner' then
    app_role:='platform_control_app'; brain_role:='platform_brain_worker';
  elsif current_database()='agent_platform_control_preview' and current_user='platform_control_owner_preview' then
    app_role:='platform_control_app_preview'; brain_role:='platform_brain_worker_preview';
  else raise insufficient_privilege; end if;
  execute format('grant execute on function platform_hr.candidate_parser_scope_valid_v6(uuid,uuid,uuid,uuid) to %I,%I',app_role,brain_role);
end
$grant$;

-- Replace active candidate functions; retain lifecycle and attachment guards.
create or replace function platform_hr.attach_candidate_draft_execution_v70(
  selected_attempt_id uuid,
  selected_worker_id text,
  selected_execution_job_id uuid,
  selected_conversation_id uuid,
  selected_turn_id uuid
) returns platform_hr.candidate_draft_processing_attempts
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected_attempt platform_hr.candidate_draft_processing_attempts%rowtype;
declare selected_assistant_message_id uuid;
declare payload jsonb;
begin
  if session_user not in ('platform_brain_worker','platform_brain_worker_preview') then
    raise insufficient_privilege;
  end if;
  payload := jsonb_build_object(
    'worker_id',btrim(selected_worker_id),
    'execution_job_id',selected_execution_job_id,
    'conversation_id',selected_conversation_id,'turn_id',selected_turn_id
  );
  perform pg_advisory_xact_lock(hashtextextended(
    'candidate-execution:' || selected_attempt_id::text,0
  ));
  select * into selected_attempt
  from platform_hr.candidate_draft_processing_attempts
  where attempt_id=selected_attempt_id and worker_id=selected_worker_id
  for update;
  if not found then raise no_data_found; end if;
  if selected_attempt.execution_job_id is not null then
    if selected_attempt.execution_payload<>payload then
      raise unique_violation using message='candidate execution identity mismatch';
    end if;
    return selected_attempt;
  end if;
  if selected_attempt.state<>'processing'
     or selected_attempt.lease_expires_at<=now() then
    raise serialization_failure;
  end if;
  perform 1 from platform_attachments.attachments attachment
  where attachment.attachment_id=selected_attempt.attachment_id
    and platform_hr.candidate_attachment_usable_v70(
      selected_attempt.owner_internal_user_id,selected_attempt.attachment_id
    ) for update;
  if not found then raise no_data_found; end if;
  select turn.assistant_message_id into selected_assistant_message_id
  from platform_control.execution_jobs execution
  join platform_control.mission_runs run on run.run_id=execution.run_id
  join platform_control.missions mission on mission.mission_id=run.mission_id
  join platform_control.conversation_turns turn
    on turn.mission_id=mission.mission_id
    and turn.turn_id=selected_turn_id
    and turn.conversation_id=selected_conversation_id
  join platform_control.conversations conversation
    on conversation.conversation_id=turn.conversation_id
    and conversation.owner_internal_user_id=mission.owner_internal_user_id
  where execution.job_id=selected_execution_job_id
    and execution.agent_id='hr-bot'
    and execution.status in ('completed','failed','cancelled','interrupted')
    and run.agent_id='hr-bot' and run.phase='direct'
    and mission.mode='direct_agent' and mission.direct_agent_id='hr-bot'
    and mission.owner_internal_user_id=selected_attempt.owner_internal_user_id
    and mission.client_request_id=turn.turn_id
    and conversation.mode='direct_agent'
    and conversation.direct_agent_id='hr-bot'
    and conversation.started_by_client_request_id=
      selected_attempt.attempt_id
    and turn.client_request_id=selected_attempt.attempt_id
    and turn.status in ('completed','failed','cancelled','interrupted')
    and not exists (
      select 1 from platform_attachments.bindings binding
      where binding.owner_internal_user_id=selected_attempt.owner_internal_user_id
        and binding.kind='turn_input'
        and binding.conversation_id=selected_conversation_id
        and binding.turn_id=selected_turn_id
        and binding.attachment_id<>selected_attempt.attachment_id
    )
    and platform_hr.candidate_parser_scope_valid_v6(selected_attempt.owner_internal_user_id,conversation.conversation_id,selected_turn_id,selected_attempt.draft_id);
  if not found then raise no_data_found; end if;
  update platform_hr.candidate_draft_processing_attempts set
    execution_job_id=selected_execution_job_id,
    conversation_id=selected_conversation_id,turn_id=selected_turn_id,
    assistant_message_id=selected_assistant_message_id,
    execution_attached_at=now(),execution_payload=payload,
    execution_payload_sha256=sha256(convert_to(payload::text,'UTF8'))
  where attempt_id=selected_attempt_id returning * into selected_attempt;
  return selected_attempt;
end
$function$;

create or replace function platform_hr.discover_candidate_draft_execution_v70(
  selected_attempt_id uuid,
  selected_worker_id text
) returns table(execution_job_id uuid,conversation_id uuid,turn_id uuid)
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected_attempt platform_hr.candidate_draft_processing_attempts%rowtype;
declare selected_count bigint;
declare discovered_job_id uuid;
declare discovered_conversation_id uuid;
declare discovered_turn_id uuid;
begin
  if session_user not in ('platform_brain_worker','platform_brain_worker_preview') then
    raise insufficient_privilege;
  end if;
  select * into selected_attempt
  from platform_hr.candidate_draft_processing_attempts
  where attempt_id=selected_attempt_id and worker_id=selected_worker_id
    and state='processing' and lease_expires_at>now()
  for update;
  if not found then raise no_data_found; end if;
  if not platform_hr.candidate_attachment_usable_v70(
    selected_attempt.owner_internal_user_id,selected_attempt.attachment_id
  ) then raise no_data_found; end if;
  if selected_attempt.execution_job_id is not null then
    perform 1 from platform_control.execution_jobs execution
    join platform_control.mission_runs run on run.run_id=execution.run_id
    join platform_control.missions mission on mission.mission_id=run.mission_id
    join platform_control.conversation_turns turn
      on turn.mission_id=mission.mission_id
      and turn.turn_id=selected_attempt.turn_id
      and turn.conversation_id=selected_attempt.conversation_id
    join platform_control.conversations conversation
      on conversation.conversation_id=turn.conversation_id
      and conversation.owner_internal_user_id=mission.owner_internal_user_id
    where execution.job_id=selected_attempt.execution_job_id
      and execution.agent_id='hr-bot'
      and execution.status in ('completed','failed','cancelled','interrupted')
      and run.agent_id='hr-bot' and run.phase='direct'
      and mission.mode='direct_agent' and mission.direct_agent_id='hr-bot'
      and mission.owner_internal_user_id=selected_attempt.owner_internal_user_id
      and mission.client_request_id=turn.turn_id
      and conversation.mode='direct_agent'
      and conversation.direct_agent_id='hr-bot'
      and turn.client_request_id=selected_attempt.attempt_id
      and turn.status in ('completed','failed','cancelled','interrupted')
      and turn.assistant_message_id is not distinct from
        selected_attempt.assistant_message_id
      and platform_hr.candidate_parser_scope_valid_v6(selected_attempt.owner_internal_user_id,conversation.conversation_id,turn.turn_id,selected_attempt.draft_id);
    if not found then raise no_data_found; end if;
    return query select selected_attempt.execution_job_id,
      selected_attempt.conversation_id,selected_attempt.turn_id;
    return;
  end if;
  select count(*),min(candidate.job_id::text)::uuid,
    min(candidate.conversation_id::text)::uuid,min(candidate.turn_id::text)::uuid
  into selected_count,discovered_job_id,discovered_conversation_id,
    discovered_turn_id
  from (
    select execution.job_id,conversation.conversation_id,turn.turn_id
    from platform_control.execution_jobs execution
    join platform_control.mission_runs run on run.run_id=execution.run_id
    join platform_control.missions mission on mission.mission_id=run.mission_id
    join platform_control.conversation_turns turn
      on turn.mission_id=mission.mission_id
    join platform_control.conversations conversation
      on conversation.conversation_id=turn.conversation_id
      and conversation.owner_internal_user_id=mission.owner_internal_user_id
    where execution.agent_id='hr-bot'
      and execution.status in ('completed','failed','cancelled','interrupted')
      and run.agent_id='hr-bot' and run.phase='direct'
      and mission.mode='direct_agent' and mission.direct_agent_id='hr-bot'
      and mission.owner_internal_user_id=selected_attempt.owner_internal_user_id
      and mission.client_request_id=turn.turn_id
      and conversation.mode='direct_agent' and conversation.direct_agent_id='hr-bot'
      and conversation.started_by_client_request_id=
        selected_attempt.attempt_id
      and turn.client_request_id=selected_attempt.attempt_id
      and turn.status in ('completed','failed','cancelled','interrupted')
      and not exists (
        select 1 from platform_attachments.bindings binding
        where binding.owner_internal_user_id=
          selected_attempt.owner_internal_user_id
          and binding.kind='turn_input'
          and binding.conversation_id=conversation.conversation_id
          and binding.turn_id=turn.turn_id
          and binding.attachment_id<>selected_attempt.attachment_id
      )
      and platform_hr.candidate_parser_scope_valid_v6(selected_attempt.owner_internal_user_id,conversation.conversation_id,turn.turn_id,selected_attempt.draft_id)
    order by execution.created_at,execution.job_id
    limit 2
  ) candidate;
  if selected_count=0 then raise no_data_found; end if;
  if selected_count<>1 then
    raise check_violation using message='candidate execution identity is ambiguous';
  end if;
  return query select discovered_job_id,discovered_conversation_id,
    discovered_turn_id;
end
$function$;

create or replace function platform_hr.read_candidate_draft_execution_result_v70(
  selected_attempt_id uuid,
  selected_worker_id text
) returns table(
  execution_status text,
  turn_status text,
  conversation_id uuid,
  assistant_message_id uuid,
  content_ciphertext bytea,
  encryption_key_version integer
)
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
begin
  if session_user not in (
    'platform_brain_worker','platform_brain_worker_preview'
  ) then raise insufficient_privilege; end if;
  return query
  select execution.status::text,turn.status::text,
    conversation.conversation_id,selected_attempt.assistant_message_id,
    message.content_ciphertext,message.encryption_key_version
  from platform_hr.candidate_draft_processing_attempts selected_attempt
  join platform_control.execution_jobs execution
    on execution.job_id=selected_attempt.execution_job_id
    and execution.agent_id='hr-bot'
    and execution.status in ('completed','failed','cancelled','interrupted')
  join platform_control.mission_runs run
    on run.run_id=execution.run_id and run.agent_id='hr-bot'
    and run.phase='direct'
  join platform_control.missions mission
    on mission.mission_id=run.mission_id
    and mission.owner_internal_user_id=selected_attempt.owner_internal_user_id
    and mission.mode='direct_agent' and mission.direct_agent_id='hr-bot'
  join platform_control.conversation_turns turn
    on turn.mission_id=mission.mission_id
    and turn.turn_id=selected_attempt.turn_id
    and turn.conversation_id=selected_attempt.conversation_id
    and turn.client_request_id=selected_attempt.attempt_id
    and turn.status in ('completed','failed','cancelled','interrupted')
  join platform_control.conversations conversation
    on conversation.conversation_id=turn.conversation_id
    and conversation.owner_internal_user_id=selected_attempt.owner_internal_user_id
    and conversation.started_by_client_request_id=
      selected_attempt.attempt_id
    and conversation.mode='direct_agent'
    and conversation.direct_agent_id='hr-bot'
  left join platform_control.conversation_messages message
    on message.conversation_id=conversation.conversation_id
    and message.message_id=selected_attempt.assistant_message_id
    and message.turn_id=turn.turn_id
    and message.mission_id=mission.mission_id and message.role='assistant'
    and message.delivery_status='completed'
  where selected_attempt.attempt_id=selected_attempt_id
    and selected_attempt.worker_id=selected_worker_id
    and selected_attempt.state='processing'
    and selected_attempt.lease_expires_at>now()
    and mission.client_request_id=turn.turn_id
    and turn.assistant_message_id is not distinct from
      selected_attempt.assistant_message_id
    and platform_hr.candidate_parser_scope_valid_v6(selected_attempt.owner_internal_user_id,conversation.conversation_id,turn.turn_id,selected_attempt.draft_id);
  if not found then raise no_data_found; end if;
end
$function$;

create or replace function platform_hr.fail_candidate_parser_submission_collision_v70(
  selected_owner_internal_user_id uuid,
  selected_attempt_id uuid
) returns platform_hr.candidate_drafts
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected_attempt platform_hr.candidate_draft_processing_attempts%rowtype;
declare selected_draft platform_hr.candidate_drafts%rowtype;
begin
  if session_user not in (
    'platform_control_app','platform_control_app_preview'
  ) then raise insufficient_privilege; end if;
  select * into selected_attempt
  from platform_hr.candidate_draft_processing_attempts
  where attempt_id=selected_attempt_id
    and owner_internal_user_id=selected_owner_internal_user_id
  for update;
  if not found then raise no_data_found; end if;
  if selected_attempt.state='failed'
     and selected_attempt.terminal_request_id=selected_attempt.attempt_id then
    select * into selected_draft from platform_hr.candidate_drafts
    where draft_id=selected_attempt.draft_id
      and owner_internal_user_id=selected_owner_internal_user_id;
    if not found then raise no_data_found; end if;
    return selected_draft;
  end if;
  if selected_attempt.state<>'processing'
     or selected_attempt.lease_expires_at<=now()
     or selected_attempt.execution_job_id is not null then
    raise serialization_failure;
  end if;
  perform 1 from platform_control.conversations conversation
  where conversation.owner_internal_user_id=selected_owner_internal_user_id
    and conversation.conversation_id in (select source_turn.conversation_id from platform_control.conversation_turns source_turn where source_turn.client_request_id=selected_attempt.attempt_id)
    and (
      conversation.mode<>'direct_agent'
      or conversation.direct_agent_id is distinct from 'hr-bot'
      or not platform_hr.candidate_parser_scope_valid_v6(selected_owner_internal_user_id,conversation.conversation_id,
        (select source_turn.turn_id from platform_control.conversation_turns source_turn where source_turn.conversation_id=conversation.conversation_id and source_turn.client_request_id=selected_attempt.attempt_id),selected_attempt.draft_id)
      or not exists (
        select 1 from platform_control.conversation_turns turn
        where turn.conversation_id=conversation.conversation_id
          and turn.client_request_id=selected_attempt.attempt_id
      )
    );
  if not found then raise no_data_found; end if;
  selected_draft := platform_hr.fail_candidate_draft_v70(
    selected_owner_internal_user_id,selected_attempt.draft_id,
    selected_attempt.attempt_id,selected_attempt.claimed_row_version,
    'parser_request_collision'
  );
  update platform_hr.candidate_draft_processing_attempts set
    state='failed',finished_at=now(),terminal_request_id=attempt_id
  where attempt_id=selected_attempt.attempt_id;
  return selected_draft;
end
$function$;

-- Preserve standard/intelligence services with exact source-turn scope.
create or replace function platform_hr.create_context_draft_v69(
  selected_context_version_id uuid,
  selected_owner_internal_user_id uuid,
  selected_position_id uuid,
  selected_client_request_id uuid,
  selected_base_context_version_id uuid,
  selected_official_position_version_id uuid,
  selected_modules jsonb,
  selected_summary text,
  selected_source_conversation_id uuid,
  selected_source_turn_id uuid,
  selected_source_artifact_version_id uuid,
  selected_source_material_attachment_ids uuid[],
  selected_agent_id text,
  selected_model_version text,
  selected_created_by uuid
) returns platform_hr.position_context_versions
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.position_context_versions%rowtype;
declare current_context_id uuid;
declare next_version integer;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':context-draft:' ||
    selected_client_request_id::text,0
  ));
  select * into selected from platform_hr.position_context_versions
  where owner_internal_user_id=selected_owner_internal_user_id
    and client_request_id=selected_client_request_id;
  if found then
    if selected.context_version_id<>selected_context_version_id
      or selected.position_id<>selected_position_id
      or selected.base_context_version_id is distinct from selected_base_context_version_id
      or selected.official_position_version_id is distinct from selected_official_position_version_id
      or selected.modules<>selected_modules
      or selected.summary<>btrim(selected_summary)
      or selected.source_conversation_id is distinct from selected_source_conversation_id
      or selected.source_turn_id is distinct from selected_source_turn_id
      or selected.source_artifact_version_id is distinct from selected_source_artifact_version_id
      or selected.source_material_attachment_ids<>selected_source_material_attachment_ids
      or selected.agent_id is distinct from selected_agent_id
      or selected.model_version is distinct from selected_model_version
      or selected.created_by<>selected_created_by then
      raise unique_violation using message='context draft idempotency payload mismatch';
    end if;
    return selected;
  end if;
  select current_context_version_id into current_context_id
  from platform_hr.positions where position_id=selected_position_id
    and owner_internal_user_id=selected_owner_internal_user_id for update;
  if not found then raise no_data_found; end if;
  if current_context_id is distinct from selected_base_context_version_id then
    raise serialization_failure using message='context baseline conflict';
  end if;
  if selected_official_position_version_id is not null then
    perform 1 from platform_hr.official_position_versions
    where official_position_version_id=selected_official_position_version_id
      and owner_internal_user_id=selected_owner_internal_user_id
      and position_id=selected_position_id;
    if not found then raise no_data_found; end if;
  end if;
  if selected_source_conversation_id is not null then
    perform 1 from platform_hr.position_task_records binding
    where binding.owner_internal_user_id=selected_owner_internal_user_id
      and binding.position_id=selected_position_id
      and binding.conversation_id=selected_source_conversation_id
      and binding.turn_id=selected_source_turn_id;
    if not found then raise no_data_found; end if;
  end if;
  if selected_source_artifact_version_id is not null then
    perform 1
    from platform_attachments.artifact_versions version
    join platform_attachments.artifacts artifact
      on artifact.artifact_id=version.artifact_id
    join platform_attachments.attachments attachment
      on attachment.attachment_id=version.attachment_id
      and attachment.owner_internal_user_id=artifact.owner_internal_user_id
    join platform_hr.position_artifacts position_artifact
      on position_artifact.artifact_id=artifact.artifact_id
      and position_artifact.owner_internal_user_id=artifact.owner_internal_user_id
    where version.artifact_version_id=selected_source_artifact_version_id
      and artifact.owner_internal_user_id=selected_owner_internal_user_id
      and position_artifact.position_id=selected_position_id
      and version.state='ready' and version.result_status='succeeded'
      and version.retained_until>now() and version.immutable_locator is not null
      and attachment.state='ready' and attachment.deleted_at is null
      and attachment.retained_until>now() and attachment.immutable_locator is not null
      and not exists (
        select 1 from platform_attachments.erasure_jobs erasure
        where erasure.attachment_id=attachment.attachment_id
      );
    if not found then raise no_data_found; end if;
  end if;
  if exists (
    select 1 from unnest(selected_source_material_attachment_ids)
      as selected_attachment(attachment_id)
    left join platform_hr.position_materials material
      on material.attachment_id=selected_attachment.attachment_id
      and material.position_id=selected_position_id
      and material.owner_internal_user_id=selected_owner_internal_user_id
      and material.active
    left join platform_attachments.attachments attachment
      on attachment.attachment_id=material.attachment_id
      and attachment.owner_internal_user_id=material.owner_internal_user_id
      and attachment.state='ready' and attachment.deleted_at is null
      and attachment.retained_until>now() and attachment.immutable_locator is not null
    where material.attachment_id is null or attachment.attachment_id is null
      or exists (
        select 1 from platform_attachments.erasure_jobs erasure
        where erasure.attachment_id=selected_attachment.attachment_id
      )
  ) then raise no_data_found; end if;
  select coalesce(max(version_number),0)+1 into next_version
  from platform_hr.position_context_versions
  where position_id=selected_position_id and state in ('confirmed','superseded');
  insert into platform_hr.position_context_versions(
    context_version_id,owner_internal_user_id,position_id,client_request_id,
    version_number,state,modules,summary,official_position_version_id,
    base_context_version_id,source_conversation_id,source_turn_id,
    source_artifact_version_id,source_material_attachment_ids,agent_id,
    model_version,created_by
  ) values (
    selected_context_version_id,selected_owner_internal_user_id,
    selected_position_id,selected_client_request_id,next_version,'draft',
    selected_modules,btrim(selected_summary),selected_official_position_version_id,
    selected_base_context_version_id,selected_source_conversation_id,
    selected_source_turn_id,selected_source_artifact_version_id,
    selected_source_material_attachment_ids,selected_agent_id,
    selected_model_version,selected_created_by
  ) returning * into selected;
  return selected;
end
$function$;

create or replace function platform_hr.create_position_insight_retrieval_v79(
  selected_retrieval_id uuid,
  selected_owner_internal_user_id uuid,
  selected_client_request_id uuid,
  selected_position_id uuid,
  selected_conversation_id uuid,
  selected_turn_id uuid,
  selected_insight_version_ids uuid[],
  selected_query_sha256 text,
  selected_retrieved_excerpts jsonb
) returns platform_hr.position_insight_retrievals
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.position_insight_retrievals%rowtype;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <>
        (session_user='platform_control_app') then
    raise insufficient_privilege;
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':insight-retrieval-request:' ||
    selected_client_request_id::text,0
  ));
  select * into selected from platform_hr.position_insight_retrievals retrieval
  where retrieval.owner_internal_user_id=selected_owner_internal_user_id
    and retrieval.client_request_id=selected_client_request_id;
  if found then
    if selected.retrieval_id is distinct from selected_retrieval_id
      or selected.position_id is distinct from selected_position_id
      or selected.conversation_id is distinct from selected_conversation_id
      or selected.turn_id is distinct from selected_turn_id
      or selected.insight_version_ids
        is distinct from selected_insight_version_ids
      or selected.query_sha256 is distinct from selected_query_sha256
      or selected.retrieved_excerpts
        is distinct from selected_retrieved_excerpts then
      raise unique_violation using
        message='position insight retrieval idempotency payload mismatch';
    end if;
    return selected;
  end if;
  if cardinality(selected_insight_version_ids) not between 1 and 5
    or not platform_hr.uuid_array_is_unique_v79(
      selected_insight_version_ids
    ) then
    raise check_violation using message='insight retrieval selection invalid';
  end if;
  perform 1 from platform_hr.position_task_records binding
  where binding.position_id=selected_position_id
    and binding.conversation_id=selected_conversation_id and binding.turn_id=selected_turn_id
    and binding.owner_internal_user_id=selected_owner_internal_user_id;
  if not found then raise no_data_found; end if;
  perform 1 from platform_control.conversation_turns turn_record
  where turn_record.conversation_id=selected_conversation_id
    and turn_record.turn_id=selected_turn_id;
  if not found then raise no_data_found; end if;
  if (
    select count(*) from platform_hr.talent_insight_versions insight
    where insight.owner_internal_user_id=selected_owner_internal_user_id
      and insight.insight_version_id=any(selected_insight_version_ids)
  )<>cardinality(selected_insight_version_ids) then
    raise no_data_found;
  end if;
  insert into platform_hr.position_insight_retrievals(
    retrieval_id,owner_internal_user_id,client_request_id,position_id,
    conversation_id,turn_id,insight_version_ids,query_sha256,retrieved_excerpts
  ) values (
    selected_retrieval_id,selected_owner_internal_user_id,
    selected_client_request_id,selected_position_id,selected_conversation_id,
    selected_turn_id,selected_insight_version_ids,selected_query_sha256,
    selected_retrieved_excerpts
  ) returning * into selected;
  return selected;
end
$function$;

create or replace function platform_hr.create_intelligence_bundle_reference_v86(
  selected_reference_id uuid,
  selected_owner_internal_user_id uuid,
  selected_client_request_id uuid,
  selected_position_id uuid,
  selected_turn_id uuid,
  selected_bundle_id uuid,
  selected_observed_at timestamptz,
  selected_context_document jsonb
) returns platform_hr.position_intelligence_bundle_references
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.position_intelligence_bundle_references%rowtype;
declare selected_conversation_id uuid;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <>
        (session_user='platform_control_app') then
    raise insufficient_privilege;
  end if;
  if selected_reference_id is null
    or selected_owner_internal_user_id is null
    or selected_client_request_id is null
    or selected_position_id is null
    or selected_turn_id is null
    or selected_bundle_id is null
    or selected_observed_at is null
    or jsonb_typeof(selected_context_document)<>'object'
    or octet_length(selected_context_document::text)>32768
    or selected_context_document->>'bundle_id'<>selected_bundle_id::text then
    raise check_violation using message='intelligence bundle task reference invalid';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':bundle-task-reference:' ||
    selected_turn_id::text,0
  ));
  select * into selected
  from platform_hr.position_intelligence_bundle_references reference
  where reference.owner_internal_user_id=selected_owner_internal_user_id
    and reference.position_id=selected_position_id
    and reference.turn_id=selected_turn_id;
  if found then
    if selected.reference_id is distinct from selected_reference_id
      or selected.client_request_id is distinct from selected_client_request_id
      or selected.bundle_id is distinct from selected_bundle_id
      or selected.observed_at is distinct from selected_observed_at
      or selected.context_document is distinct from selected_context_document then
      raise unique_violation using message='intelligence bundle task reference mismatch';
    end if;
    return selected;
  end if;
  select turn_record.conversation_id into selected_conversation_id
  from platform_control.conversation_turns turn_record
  join platform_hr.position_task_records binding
    on binding.conversation_id=turn_record.conversation_id and binding.turn_id=turn_record.turn_id
   and binding.owner_internal_user_id=selected_owner_internal_user_id
   and binding.position_id=selected_position_id
  where turn_record.turn_id=selected_turn_id;
  if not found then raise no_data_found; end if;
  perform 1 from platform_hr.intelligence_bundles bundle
  where bundle.bundle_id=selected_bundle_id
    and bundle.owner_internal_user_id=selected_owner_internal_user_id;
  if not found then raise no_data_found; end if;
  insert into platform_hr.position_intelligence_bundle_references(
    reference_id,owner_internal_user_id,client_request_id,position_id,
    conversation_id,turn_id,bundle_id,observed_at,context_document
  ) values (
    selected_reference_id,selected_owner_internal_user_id,
    selected_client_request_id,selected_position_id,selected_conversation_id,
    selected_turn_id,selected_bundle_id,selected_observed_at,
    selected_context_document
  ) returning * into selected;
  return selected;
end
$function$;
