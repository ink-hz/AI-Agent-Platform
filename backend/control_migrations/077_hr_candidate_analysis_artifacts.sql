alter table platform_hr.candidate_analysis_versions
  add column source_artifact_version_id uuid
  references platform_attachments.artifact_versions(artifact_version_id);

-- candidate_analysis_versions_immutable_v70 remains enabled: artifact identity is
-- supplied at insert time and an analysis version is never patched afterwards.

create function platform_hr.candidate_analysis_artifact_valid_v77(
  selected_owner_internal_user_id uuid,
  selected_projection_request_id uuid,
  selected_position_candidate_id uuid,
  selected_context_version_id uuid,
  selected_artifact_version_id uuid
) returns boolean
language sql stable security definer
set search_path=pg_catalog,platform_hr
as $function$
  select count(artifact.artifact_id)=1
    and bool_and(
      artifact.owner_internal_user_id=record.owner_internal_user_id
      and artifact.conversation_id=record.conversation_id
      and artifact.task_id=run.task_id
      and artifact.agent_id='hr-bot'
      and attachment.owner_internal_user_id=record.owner_internal_user_id
      and attachment.conversation_id=record.conversation_id
      and attachment.source_kind='agent_output'
      and attachment.state='ready'
      and attachment.deleted_at is null
      and attachment.retained_until>now()
      and attachment.detected_mime='application/pdf'
      and attachment.immutable_locator is not null
      and version.state='ready'
      and version.result_status='succeeded'
      and version.retained_until>now()
      and version.detected_mime='application/pdf'
      and version.immutable_locator is not null
      and version.artifact_version_id=selected_artifact_version_id
      and not exists (
        select 1 from platform_attachments.erasure_jobs erasure
        where erasure.attachment_id=attachment.attachment_id
      )
      and exists (
        select 1 from platform_attachments.bindings binding
        where binding.attachment_id=attachment.attachment_id
          and binding.owner_internal_user_id=record.owner_internal_user_id
          and binding.conversation_id=record.conversation_id
          and binding.kind='task_output'
          and binding.task_id=run.task_id
          and binding.agent_id='hr-bot'
      )
      and exists (
        select 1 from platform_attachments.task_grants grant_row
        where grant_row.task_id=run.task_id
          and grant_row.agent_id='hr-bot'
          and grant_row.scope='write_output'
      )
    )
  from platform_hr.hr_task_result_projections projection
  join platform_hr.position_task_records record
    on record.task_record_id=projection.task_record_id
   and record.owner_internal_user_id=projection.owner_internal_user_id
  join platform_control.conversation_turns turn
    on turn.conversation_id=record.conversation_id
   and turn.turn_id=record.turn_id
  join platform_control.missions mission
    on mission.mission_id=turn.mission_id
   and mission.owner_internal_user_id=record.owner_internal_user_id
  join platform_control.mission_runs run
    on run.mission_id=mission.mission_id
   and run.phase='direct' and run.agent_id='hr-bot' and run.status='completed'
  left join platform_attachments.artifacts artifact
    on artifact.task_id=run.task_id and artifact.agent_id='hr-bot'
  left join lateral (
    select candidate_version.*
    from platform_attachments.artifact_versions candidate_version
    where candidate_version.artifact_id=artifact.artifact_id
    order by candidate_version.version_no desc,candidate_version.created_at desc
    limit 1
  ) version on true
  left join platform_attachments.attachments attachment
    on attachment.attachment_id=version.attachment_id
  where projection.projection_request_id=selected_projection_request_id
    and projection.owner_internal_user_id=selected_owner_internal_user_id
    and record.task_kind='candidate_interview_plan'
    and record.position_candidate_id=selected_position_candidate_id
    and record.context_version_id=selected_context_version_id
$function$;

create function platform_hr.create_candidate_analysis_v77(
  selected_analysis_version_id uuid,
  selected_owner_internal_user_id uuid,
  selected_position_candidate_id uuid,
  selected_context_version_id uuid,
  selected_client_request_id uuid,
  selected_analysis_kind text,
  selected_document_ids uuid[],
  selected_feedback_ids uuid[],
  selected_result jsonb,
  selected_evidence jsonb,
  selected_unknowns jsonb,
  selected_conflicts jsonb,
  selected_verification_questions jsonb,
  selected_agent_version text,
  selected_model_version text,
  selected_source_artifact_version_id uuid
) returns platform_hr.candidate_analysis_versions
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.candidate_analysis_versions%rowtype;
declare relation platform_hr.position_candidates%rowtype;
declare next_version bigint;
declare selected_document_id uuid;
declare selected_feedback_id uuid;
declare payload jsonb;
begin
  if current_user not in (
    'platform_control_owner','platform_control_owner_preview'
  )
     or session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <>
        (session_user='platform_control_app')
  then raise insufficient_privilege; end if;
  if selected_source_artifact_version_id is null
     and selected_analysis_kind<>'candidate_interview_plan' then
    return platform_hr.create_candidate_analysis_v70(
      selected_analysis_version_id,selected_owner_internal_user_id,
      selected_position_candidate_id,selected_context_version_id,
      selected_client_request_id,selected_analysis_kind,selected_document_ids,
      selected_feedback_ids,selected_result,selected_evidence,selected_unknowns,
      selected_conflicts,selected_verification_questions,selected_agent_version,
      selected_model_version
    );
  end if;
  if selected_source_artifact_version_id is null then
    raise no_data_found using message='candidate interview artifact required';
  end if;
  if selected_analysis_kind<>'candidate_interview_plan' then
    raise no_data_found using message='candidate analysis artifact invalid';
  end if;
  if not platform_hr.candidate_json_safe_v70(selected_result,false)
     or not platform_hr.candidate_json_safe_v70(selected_evidence,false) then
    raise check_violation using message='candidate analysis contains forbidden fields';
  end if;
  if cardinality(selected_feedback_ids)>100
     or cardinality(selected_feedback_ids)<>cardinality(array(
       select distinct value from unnest(selected_feedback_ids) value
     )) then
    raise check_violation using message='candidate feedback snapshot invalid';
  end if;
  payload := jsonb_build_object(
    'analysis_version_id',selected_analysis_version_id,
    'position_candidate_id',selected_position_candidate_id,
    'context_version_id',selected_context_version_id,
    'analysis_kind',selected_analysis_kind,
    'document_ids',to_jsonb(selected_document_ids),
    'feedback_ids',to_jsonb(selected_feedback_ids),
    'result',selected_result,'evidence',selected_evidence,
    'unknowns',selected_unknowns,'conflicts',selected_conflicts,
    'verification_questions',selected_verification_questions,
    'agent_version',btrim(selected_agent_version),
    'model_version',btrim(selected_model_version),
    'source_artifact_version_id',selected_source_artifact_version_id
  );
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':candidate-analysis:' ||
    selected_client_request_id::text,0
  ));
  select * into selected from platform_hr.candidate_analysis_versions
  where owner_internal_user_id=selected_owner_internal_user_id
    and client_request_id=selected_client_request_id;
  if found then
    if selected.canonical_payload<>payload
       or selected.analysis_version_id<>selected_analysis_version_id
       or selected.position_candidate_id<>selected_position_candidate_id
       or selected.context_version_id<>selected_context_version_id
       or selected.analysis_kind<>selected_analysis_kind
       or selected.result<>selected_result
       or selected.evidence<>selected_evidence
       or selected.unknowns<>selected_unknowns
       or selected.conflicts<>selected_conflicts
       or selected.verification_questions<>selected_verification_questions
       or selected.agent_version<>btrim(selected_agent_version)
       or selected.model_version<>btrim(selected_model_version)
       or selected.source_artifact_version_id
          is distinct from selected_source_artifact_version_id
       or coalesce((
         select array_agg(link.document_id order by link.document_id)
         from platform_hr.candidate_analysis_documents link
         where link.analysis_version_id=selected.analysis_version_id
       ),'{}'::uuid[])<>coalesce((
         select array_agg(value order by value)
         from unnest(selected_document_ids) value
       ),'{}'::uuid[])
       or coalesce((
         select array_agg(link.feedback_id order by link.feedback_id)
         from platform_hr.candidate_analysis_feedback link
         where link.analysis_version_id=selected.analysis_version_id
       ),'{}'::uuid[])<>coalesce((
         select array_agg(value order by value)
         from unnest(selected_feedback_ids) value
       ),'{}'::uuid[]) then
      raise unique_violation using message='candidate analysis idempotency mismatch';
    end if;
    return selected;
  end if;
  if not platform_hr.candidate_analysis_artifact_valid_v77(
    selected_owner_internal_user_id,selected_client_request_id,
    selected_position_candidate_id,selected_context_version_id,
    selected_source_artifact_version_id
  ) then
    raise no_data_found using message='candidate analysis artifact invalid';
  end if;
  select * into relation from platform_hr.position_candidates
  where position_candidate_id=selected_position_candidate_id
    and owner_internal_user_id=selected_owner_internal_user_id
    and status='active' for update;
  if not found or relation.context_version_id<>selected_context_version_id then
    raise no_data_found;
  end if;
  if cardinality(selected_document_ids)<1 then raise check_violation; end if;
  foreach selected_document_id in array selected_document_ids loop
    perform 1 from platform_hr.candidate_documents document
    join platform_attachments.attachments attachment
      on attachment.attachment_id=document.attachment_id
      and attachment.owner_internal_user_id=document.owner_internal_user_id
    where document.document_id=selected_document_id
      and document.owner_internal_user_id=selected_owner_internal_user_id
      and document.candidate_id=relation.candidate_id
      and document.status='active'
      and attachment.state='ready' and attachment.deleted_at is null
      and attachment.retained_until>now()
      and not exists (
        select 1 from platform_attachments.erasure_jobs erasure
        where erasure.attachment_id=attachment.attachment_id
      ) for update of attachment;
    if not found then raise no_data_found; end if;
  end loop;
  foreach selected_feedback_id in array selected_feedback_ids loop
    perform 1 from platform_hr.human_feedback feedback
    join platform_hr.candidate_analysis_versions analysis
      on feedback.analysis_version_id=analysis.analysis_version_id
      and feedback.owner_internal_user_id=analysis.owner_internal_user_id
    where feedback.feedback_id=selected_feedback_id
      and feedback.owner_internal_user_id=selected_owner_internal_user_id
      and feedback.position_candidate_id=selected_position_candidate_id
      and analysis.position_candidate_id=selected_position_candidate_id
      and analysis.context_version_id=selected_context_version_id;
    if not found then raise no_data_found; end if;
  end loop;
  select coalesce(max(version_number),0)+1 into next_version
  from platform_hr.candidate_analysis_versions
  where position_candidate_id=selected_position_candidate_id;
  insert into platform_hr.candidate_analysis_versions(
    analysis_version_id,owner_internal_user_id,position_candidate_id,
    position_id,candidate_id,context_version_id,client_request_id,
    version_number,analysis_kind,result,evidence,unknowns,conflicts,
    verification_questions,agent_version,model_version,
    canonical_payload,payload_sha256,source_artifact_version_id
  ) values (
    selected_analysis_version_id,selected_owner_internal_user_id,
    selected_position_candidate_id,relation.position_id,relation.candidate_id,
    selected_context_version_id,selected_client_request_id,next_version,
    selected_analysis_kind,selected_result,selected_evidence,selected_unknowns,
    selected_conflicts,selected_verification_questions,
    btrim(selected_agent_version),btrim(selected_model_version),payload,
    sha256(convert_to(payload::text,'UTF8')),selected_source_artifact_version_id
  ) returning * into selected;
  insert into platform_hr.candidate_analysis_documents(
    analysis_version_id,document_id,owner_internal_user_id
  ) select selected_analysis_version_id,value,selected_owner_internal_user_id
  from unnest(selected_document_ids) value;
  insert into platform_hr.candidate_analysis_feedback(
    analysis_version_id,feedback_id,owner_internal_user_id
  ) select selected_analysis_version_id,value,selected_owner_internal_user_id
  from unnest(selected_feedback_ids) value;
  return selected;
end
$function$;

create function platform_hr.claim_hr_task_result_projection_v77(
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
  execution_model_version text,
  content_ciphertext bytea,
  encryption_key_version integer
)
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare claimed record;
declare resolved_artifact_version_id uuid;
begin
  select * into claimed
  from platform_hr.claim_hr_task_result_projection_v71(
    selected_worker_id,selected_lease_seconds
  );
  if not found then return; end if;
  resolved_artifact_version_id := claimed.output_artifact_version_id;
  if claimed.task_kind='candidate_interview_plan' then
    select case when count(artifact.artifact_id)=1 and bool_and(
      artifact.owner_internal_user_id=claimed.owner_internal_user_id
      and artifact.conversation_id=claimed.conversation_id
      and artifact.agent_id='hr-bot'
      and attachment.owner_internal_user_id=claimed.owner_internal_user_id
      and attachment.conversation_id=claimed.conversation_id
      and attachment.source_kind='agent_output'
      and attachment.state='ready' and attachment.deleted_at is null
      and attachment.retained_until>now()
      and attachment.detected_mime='application/pdf'
      and attachment.immutable_locator is not null
      and version.state='ready' and version.result_status='succeeded'
      and version.retained_until>now()
      and version.detected_mime='application/pdf'
      and version.immutable_locator is not null
      and not exists (
        select 1 from platform_attachments.erasure_jobs erasure
        where erasure.attachment_id=attachment.attachment_id
      )
      and exists (
        select 1 from platform_attachments.bindings binding
        where binding.attachment_id=attachment.attachment_id
          and binding.owner_internal_user_id=claimed.owner_internal_user_id
          and binding.conversation_id=claimed.conversation_id
          and binding.kind='task_output' and binding.task_id=run.task_id
          and binding.agent_id='hr-bot'
      )
      and exists (
        select 1 from platform_attachments.task_grants grant_row
        where grant_row.task_id=run.task_id and grant_row.agent_id='hr-bot'
          and grant_row.scope='write_output'
      )
    ) then (array_agg(version.artifact_version_id))[1] end
    into resolved_artifact_version_id
    from platform_hr.position_task_records record
    join platform_control.conversation_turns turn
      on turn.conversation_id=record.conversation_id and turn.turn_id=record.turn_id
    join platform_control.missions mission on mission.mission_id=turn.mission_id
    join platform_control.mission_runs run
      on run.mission_id=mission.mission_id and run.phase='direct'
     and run.agent_id='hr-bot' and run.status='completed'
    left join platform_attachments.artifacts artifact
      on artifact.task_id=run.task_id and artifact.agent_id='hr-bot'
    left join lateral (
      select candidate_version.*
      from platform_attachments.artifact_versions candidate_version
      where candidate_version.artifact_id=artifact.artifact_id
      order by candidate_version.version_no desc,candidate_version.created_at desc
      limit 1
    ) version on true
    left join platform_attachments.attachments attachment
      on attachment.attachment_id=version.attachment_id
    where record.task_record_id=claimed.task_record_id;
  elsif claimed.task_kind='candidate_match' then
    resolved_artifact_version_id := null;
  end if;
  return query select
    claimed.task_record_id,claimed.task_request_id,claimed.projection_request_id,
    claimed.owner_internal_user_id,claimed.position_id,claimed.task_kind,
    claimed.official_position_version_id,claimed.context_version_id,
    claimed.material_attachment_ids,claimed.candidate_id,
    claimed.position_candidate_id,claimed.document_ids,
    claimed.human_feedback_ids,claimed.conversation_id,claimed.turn_id,
    resolved_artifact_version_id,claimed.assistant_message_id,claimed.agent_id,
    claimed.execution_model_version,claimed.content_ciphertext,
    claimed.encryption_key_version;
end
$function$;

revoke all on function platform_hr.candidate_analysis_artifact_valid_v77(
  uuid,uuid,uuid,uuid,uuid
) from public;
revoke all on function platform_hr.create_candidate_analysis_v77(
  uuid,uuid,uuid,uuid,uuid,text,uuid[],uuid[],jsonb,jsonb,jsonb,jsonb,jsonb,
  text,text,uuid
) from public;
revoke all on function platform_hr.claim_hr_task_result_projection_v77(
  text,integer
) from public;

do $migration$
declare selected_app name;
declare denied_role name;
declare denied_roles name[];
begin
  if current_database()='agent_platform_control'
     and current_user='platform_control_owner' then
    selected_app := 'platform_control_app';
    denied_roles := array[
      'platform_control_migrator','platform_directory_worker',
      'platform_stream_ingest','platform_audit_append',
      'platform_control_maintenance','platform_brain_worker'
    ]::name[];
  elsif current_database()='agent_platform_control_preview'
        and current_user='platform_control_owner_preview' then
    selected_app := 'platform_control_app_preview';
    denied_roles := array[
      'platform_control_migrator_preview','platform_directory_worker_preview',
      'platform_stream_ingest_preview','platform_audit_append_preview',
      'platform_control_maintenance_preview','platform_brain_worker_preview'
    ]::name[];
  else
    raise insufficient_privilege using
      message='HR candidate analysis artifact migration owner/environment mismatch';
  end if;
  execute format(
    'revoke execute on function platform_hr.create_candidate_analysis_v70('
    'uuid,uuid,uuid,uuid,uuid,text,uuid[],uuid[],jsonb,jsonb,jsonb,jsonb,jsonb,'
    'text,text) from %I',selected_app
  );
  foreach denied_role in array denied_roles loop
    execute format(
      'revoke execute on function platform_hr.create_candidate_analysis_v70('
      'uuid,uuid,uuid,uuid,uuid,text,uuid[],uuid[],jsonb,jsonb,jsonb,jsonb,jsonb,'
      'text,text) from %I',denied_role
    );
    execute format(
      'revoke execute on function platform_hr.create_candidate_analysis_v77('
      'uuid,uuid,uuid,uuid,uuid,text,uuid[],uuid[],jsonb,jsonb,jsonb,jsonb,jsonb,'
      'text,text,uuid) from %I',denied_role
    );
  end loop;
  execute format(
    'grant execute on function platform_hr.create_candidate_analysis_v77('
    'uuid,uuid,uuid,uuid,uuid,text,uuid[],uuid[],jsonb,jsonb,jsonb,jsonb,jsonb,'
    'text,text,uuid) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.claim_hr_task_result_projection_v77('
    'text,integer) to %I',selected_app
  );
end
$migration$;
