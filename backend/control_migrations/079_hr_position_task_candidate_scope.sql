alter table platform_hr.position_task_requests
  add column document_ids uuid[] not null default '{}'::uuid[],
  add column document_attachment_ids uuid[] not null default '{}'::uuid[],
  add column human_feedback_ids uuid[] not null default '{}'::uuid[],
  add column candidate_prompt_context text,
  add column candidate_snapshot_sha256 text;

alter table platform_hr.position_task_requests
  add constraint position_task_candidate_snapshot_shape_v79 check (
    cardinality(document_ids)<=100
    and cardinality(document_attachment_ids)<=100
    and cardinality(human_feedback_ids)<=100
    and (
      (task_kind in ('candidate_match','candidate_interview_plan') and (
        (candidate_snapshot_sha256 is null and cardinality(document_ids)=0
          and cardinality(document_attachment_ids)=0
          and cardinality(human_feedback_ids)=0
          and candidate_prompt_context is null)
        or (candidate_snapshot_sha256 ~ '^[a-f0-9]{64}$'
          and cardinality(document_ids) between 1 and 100
          and cardinality(document_ids)=cardinality(document_attachment_ids)
          and char_length(candidate_prompt_context) between 1 and 65536
          and octet_length(candidate_prompt_context)<=65536)
      ))
      or (task_kind not in ('candidate_match','candidate_interview_plan')
        and cardinality(document_ids)=0
        and cardinality(document_attachment_ids)=0
        and cardinality(human_feedback_ids)=0
        and candidate_prompt_context is null
        and candidate_snapshot_sha256 is null)
    )
  );

create function platform_hr.candidate_task_snapshot_sha256_v79(
  selected_candidate_id uuid,
  selected_position_candidate_id uuid,
  selected_context_version_id uuid,
  selected_document_ids uuid[],
  selected_document_attachment_ids uuid[],
  selected_human_feedback_ids uuid[],
  selected_prompt_context text
) returns text language sql immutable
set search_path=pg_catalog,platform_hr
as $function$
  select encode(sha256(convert_to(
    'v1|' || selected_candidate_id::text || '|' ||
    selected_position_candidate_id::text || '|' ||
    selected_context_version_id::text || '|' ||
    array_to_string(selected_document_ids,',') || '|' ||
    array_to_string(selected_document_attachment_ids,',') || '|' ||
    array_to_string(selected_human_feedback_ids,',') || '|' ||
    encode(convert_to(selected_prompt_context,'UTF8'),'hex'),'UTF8')),'hex')
$function$;

create function platform_hr.create_position_task_record_v79(
  selected_task_record_id uuid,
  selected_owner_internal_user_id uuid,
  selected_position_id uuid,
  selected_client_request_id uuid,
  selected_task_kind text,
  selected_official_position_version_id uuid,
  selected_context_version_id uuid,
  selected_material_attachment_ids uuid[],
  selected_candidate_id uuid,
  selected_position_candidate_id uuid,
  selected_document_attachment_ids uuid[],
  selected_human_feedback_ids uuid[],
  selected_conversation_id uuid,
  selected_turn_id uuid,
  selected_prompt_context text,
  selected_canonical_sha256 text,
  selected_execution_model_version text
) returns platform_hr.position_task_records
language plpgsql security definer set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.position_task_records%rowtype;
declare request platform_hr.position_task_requests%rowtype;
declare current_official_id uuid;
declare turn_request_id uuid;
declare bound_material_ids uuid[];
begin
  if session_user not in (
    'platform_control_app','platform_control_app_preview',
    'platform_brain_worker','platform_brain_worker_preview'
  ) or selected_execution_model_version is null
    or char_length(btrim(selected_execution_model_version)) not between 1 and 128
  then raise insufficient_privilege; end if;
  if selected_task_kind not in ('candidate_match','candidate_interview_plan') then
    return platform_hr.create_position_task_record_v71(
      selected_task_record_id,selected_owner_internal_user_id,
      selected_position_id,selected_client_request_id,selected_task_kind,
      selected_official_position_version_id,selected_context_version_id,
      selected_material_attachment_ids,selected_candidate_id,
      selected_position_candidate_id,selected_document_attachment_ids,
      selected_human_feedback_ids,selected_conversation_id,selected_turn_id,
      selected_prompt_context,selected_canonical_sha256,
      selected_execution_model_version);
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':position-task:' ||
    selected_client_request_id::text,0));
  select * into selected from platform_hr.position_task_records record
  where record.owner_internal_user_id=selected_owner_internal_user_id
    and record.client_request_id=selected_client_request_id;
  if found then
    if selected.task_record_id<>selected_task_record_id
      or selected.position_id<>selected_position_id
      or selected.task_kind<>selected_task_kind
      or selected.official_position_version_id is distinct from selected_official_position_version_id
      or selected.context_version_id is distinct from selected_context_version_id
      or selected.material_attachment_ids<>selected_material_attachment_ids
      or selected.candidate_id is distinct from selected_candidate_id
      or selected.position_candidate_id is distinct from selected_position_candidate_id
      or selected.document_attachment_ids<>selected_document_attachment_ids
      or selected.human_feedback_ids<>selected_human_feedback_ids
      or selected.conversation_id<>selected_conversation_id
      or selected.turn_id<>selected_turn_id
      or selected.prompt_context<>selected_prompt_context
      or selected.canonical_sha256<>selected_canonical_sha256
      or selected.execution_model_version is distinct from btrim(selected_execution_model_version) then
      raise unique_violation using message='position task idempotency payload mismatch';
    end if;
    return selected;
  end if;
  select turn.client_request_id into turn_request_id
  from platform_hr.position_conversations binding
  join platform_control.conversation_turns turn
    on turn.conversation_id=binding.conversation_id
  where binding.owner_internal_user_id=selected_owner_internal_user_id
    and binding.position_id=selected_position_id
    and binding.conversation_id=selected_conversation_id
    and turn.turn_id=selected_turn_id;
  if not found then raise no_data_found; end if;
  if turn_request_id<>selected_client_request_id then
    raise unique_violation using message='position task turn request mismatch';
  end if;
  select * into request from platform_hr.position_task_requests task_request
  where task_request.owner_internal_user_id=selected_owner_internal_user_id
    and task_request.position_id=selected_position_id
    and task_request.client_request_id=selected_client_request_id
    and task_request.status='active' for update;
  if not found or request.candidate_snapshot_sha256 is null then
    raise no_data_found using message='candidate task snapshot unavailable';
  end if;
  if request.task_kind<>selected_task_kind
    or request.expected_context_version_id is distinct from selected_context_version_id
    or request.material_attachment_ids<>selected_material_attachment_ids
    or request.candidate_id is distinct from selected_candidate_id
    or request.position_candidate_id is distinct from selected_position_candidate_id
    or request.document_attachment_ids<>selected_document_attachment_ids
    or request.human_feedback_ids<>selected_human_feedback_ids
    or request.candidate_snapshot_sha256<>
      platform_hr.candidate_task_snapshot_sha256_v79(
        request.candidate_id,request.position_candidate_id,
        request.expected_context_version_id,request.document_ids,
        request.document_attachment_ids,request.human_feedback_ids,
        request.candidate_prompt_context) then
    raise unique_violation using message='position task selection mismatch';
  end if;
  select position.current_official_version_id into current_official_id
  from platform_hr.positions position
  where position.owner_internal_user_id=selected_owner_internal_user_id
    and position.position_id=selected_position_id for update;
  if not found then raise no_data_found; end if;
  if current_official_id is distinct from selected_official_position_version_id then
    raise serialization_failure using message='official position task baseline conflict';
  end if;
  perform 1 from platform_hr.position_context_versions context
  where context.context_version_id=selected_context_version_id
    and context.owner_internal_user_id=selected_owner_internal_user_id
    and context.position_id=selected_position_id
    and context.state in ('confirmed','superseded');
  if not found then raise no_data_found; end if;
  select coalesce(array_agg(binding.attachment_id order by binding.attachment_id),'{}'::uuid[])
    into bound_material_ids
  from platform_attachments.bindings binding
  where binding.owner_internal_user_id=selected_owner_internal_user_id
    and binding.conversation_id=selected_conversation_id
    and binding.turn_id=selected_turn_id and binding.kind='turn_input';
  if not bound_material_ids<@selected_material_attachment_ids
    or not platform_hr.validate_position_materials_v69(
      selected_owner_internal_user_id,selected_position_id,
      selected_material_attachment_ids) then raise no_data_found; end if;
  insert into platform_hr.position_task_records(
    task_record_id,owner_internal_user_id,position_id,client_request_id,
    task_kind,official_position_version_id,context_version_id,
    material_attachment_ids,candidate_id,position_candidate_id,
    document_attachment_ids,human_feedback_ids,conversation_id,turn_id,
    prompt_context,canonical_sha256,execution_model_version
  ) values (
    selected_task_record_id,selected_owner_internal_user_id,
    selected_position_id,selected_client_request_id,selected_task_kind,
    selected_official_position_version_id,selected_context_version_id,
    selected_material_attachment_ids,selected_candidate_id,
    selected_position_candidate_id,selected_document_attachment_ids,
    selected_human_feedback_ids,selected_conversation_id,selected_turn_id,
    selected_prompt_context,selected_canonical_sha256,
    btrim(selected_execution_model_version)
  ) returning * into selected;
  update platform_hr.position_task_requests set status='consumed'
  where task_request_id=request.task_request_id;
  return selected;
end
$function$;

create function platform_hr.guard_position_task_request_snapshot_v79()
returns trigger language plpgsql set search_path=pg_catalog,platform_hr
as $function$
begin
  if tg_op='DELETE' then return old; end if;
  if (to_jsonb(new)-'status')=(to_jsonb(old)-'status') then return new; end if;
  if old.candidate_snapshot_sha256 is null
    and cardinality(old.document_ids)=0
    and cardinality(old.document_attachment_ids)=0
    and cardinality(old.human_feedback_ids)=0
    and old.candidate_prompt_context is null
    and (to_jsonb(new)-'document_ids'-'document_attachment_ids'
      -'human_feedback_ids'-'candidate_prompt_context'-'candidate_snapshot_sha256')=
      (to_jsonb(old)-'document_ids'-'document_attachment_ids'
      -'human_feedback_ids'-'candidate_prompt_context'-'candidate_snapshot_sha256')
    and new.candidate_snapshot_sha256 is not null then return new;
  end if;
  raise check_violation using message='position task request snapshot is immutable';
end
$function$;

create trigger position_task_request_snapshot_immutable_v79
before update or delete on platform_hr.position_task_requests
for each row execute function platform_hr.guard_position_task_request_snapshot_v79();

create function platform_hr.create_position_task_request_v79(
  selected_task_request_id uuid,
  selected_owner_internal_user_id uuid,
  selected_position_id uuid,
  selected_client_request_id uuid,
  selected_canonical_payload_sha256 text,
  selected_task_kind text,
  selected_expected_context_version_id uuid,
  selected_material_attachment_ids uuid[],
  selected_candidate_id uuid,
  selected_position_candidate_id uuid,
  selected_document_ids uuid[],
  selected_document_attachment_ids uuid[],
  selected_human_feedback_ids uuid[],
  selected_candidate_prompt_context text
) returns platform_hr.position_task_requests
language plpgsql security definer set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.position_task_requests%rowtype;
declare selected_snapshot_sha256 text;
declare matched_documents integer;
declare active_documents integer;
declare matched_feedback integer;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  if selected_document_ids is null or selected_document_attachment_ids is null
    or selected_human_feedback_ids is null then
    raise check_violation using message='position task snapshot arrays required';
  end if;
  if selected_task_kind in ('candidate_match','candidate_interview_plan') then
    if selected_candidate_id is null or selected_position_candidate_id is null
      or selected_expected_context_version_id is null
      or cardinality(selected_document_ids) not between 1 and 100
      or cardinality(selected_document_ids)<>cardinality(selected_document_attachment_ids)
      or cardinality(selected_human_feedback_ids)>100
      or cardinality(selected_document_ids)<>(
        select count(distinct value) from unnest(selected_document_ids) value)
      or cardinality(selected_document_attachment_ids)<>(
        select count(distinct value)
        from unnest(selected_document_attachment_ids) value)
      or cardinality(selected_human_feedback_ids)<>(
        select count(distinct value) from unnest(selected_human_feedback_ids) value)
      or selected_candidate_prompt_context is null
      or char_length(btrim(selected_candidate_prompt_context)) not between 1 and 65536
      or octet_length(selected_candidate_prompt_context)>65536 then
      raise no_data_found using message='candidate task snapshot unavailable';
    end if;
    selected_candidate_prompt_context:=btrim(selected_candidate_prompt_context);
    selected_snapshot_sha256:=platform_hr.candidate_task_snapshot_sha256_v79(
      selected_candidate_id,selected_position_candidate_id,
      selected_expected_context_version_id,selected_document_ids,
      selected_document_attachment_ids,selected_human_feedback_ids,
      selected_candidate_prompt_context);
  elsif cardinality(selected_document_ids)<>0
    or cardinality(selected_document_attachment_ids)<>0
    or cardinality(selected_human_feedback_ids)<>0
    or selected_candidate_prompt_context is not null then
    raise check_violation using message='non-candidate task snapshot invalid';
  end if;

  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':position-task-request:' ||
    selected_position_id::text || ':' || selected_client_request_id::text,0));
  select * into selected from platform_hr.position_task_requests request
  where request.owner_internal_user_id=selected_owner_internal_user_id
    and request.position_id=selected_position_id
    and request.client_request_id=selected_client_request_id for update;
  if found then
    if selected.task_request_id<>selected_task_request_id
      or selected.canonical_payload_sha256<>selected_canonical_payload_sha256
      or selected.task_kind<>selected_task_kind
      or selected.expected_context_version_id is distinct from selected_expected_context_version_id
      or selected.material_attachment_ids<>selected_material_attachment_ids
      or selected.candidate_id is distinct from selected_candidate_id
      or selected.position_candidate_id is distinct from selected_position_candidate_id
      or selected.document_ids<>selected_document_ids
      or selected.document_attachment_ids<>selected_document_attachment_ids
      or selected.human_feedback_ids<>selected_human_feedback_ids
      or selected.candidate_prompt_context is distinct from selected_candidate_prompt_context
      or selected.candidate_snapshot_sha256 is distinct from selected_snapshot_sha256 then
      raise unique_violation using message='position task request payload mismatch';
    end if;
    return selected;
  end if;

  if selected_task_kind in ('candidate_match','candidate_interview_plan') then
    perform 1 from platform_hr.position_candidates relation
    join platform_hr.candidates candidate
      on candidate.candidate_id=relation.candidate_id
      and candidate.owner_internal_user_id=relation.owner_internal_user_id
    where relation.position_candidate_id=selected_position_candidate_id
      and relation.owner_internal_user_id=selected_owner_internal_user_id
      and relation.position_id=selected_position_id
      and relation.candidate_id=selected_candidate_id
      and relation.context_version_id=selected_expected_context_version_id
      and relation.status='active' for update of relation,candidate;
    if not found then
      raise no_data_found using message='candidate task scope unavailable';
    end if;
    perform document.document_id
    from platform_hr.candidate_documents document
    where document.owner_internal_user_id=selected_owner_internal_user_id
      and document.candidate_id=selected_candidate_id
    for update;
    perform document.document_id from
      unnest(selected_document_ids,selected_document_attachment_ids)
        requested(document_id,attachment_id)
    join platform_hr.candidate_documents document
      on document.document_id=requested.document_id
      and document.attachment_id=requested.attachment_id
      and document.owner_internal_user_id=selected_owner_internal_user_id
      and document.candidate_id=selected_candidate_id
      and document.status='active'
    join platform_attachments.attachments attachment
      on attachment.attachment_id=requested.attachment_id
      and attachment.owner_internal_user_id=document.owner_internal_user_id
      and attachment.state='ready' and attachment.deleted_at is null
      and attachment.retained_until>now() and attachment.immutable_locator is not null
    where not exists (select 1 from platform_attachments.erasure_jobs erasure
      where erasure.attachment_id=attachment.attachment_id)
    for update of document,attachment;
    get diagnostics matched_documents=row_count;
    select count(*) into active_documents
    from platform_hr.candidate_documents document
    where document.owner_internal_user_id=selected_owner_internal_user_id
      and document.candidate_id=selected_candidate_id and document.status='active';
    if matched_documents<>cardinality(selected_document_ids)
      or active_documents<>cardinality(selected_document_ids) then
      raise no_data_found using message='candidate task documents unavailable';
    end if;
    perform feedback.feedback_id from unnest(selected_human_feedback_ids) requested(feedback_id)
    join platform_hr.human_feedback feedback
      on feedback.feedback_id=requested.feedback_id
      and feedback.owner_internal_user_id=selected_owner_internal_user_id
      and feedback.position_candidate_id=selected_position_candidate_id
    join platform_hr.candidate_analysis_versions analysis
      on analysis.analysis_version_id=feedback.analysis_version_id
      and analysis.owner_internal_user_id=feedback.owner_internal_user_id
      and analysis.position_candidate_id=selected_position_candidate_id
      and analysis.position_id=selected_position_id
      and analysis.candidate_id=selected_candidate_id
      and analysis.context_version_id=selected_expected_context_version_id
    for update of feedback,analysis;
    get diagnostics matched_feedback=row_count;
    if matched_feedback<>cardinality(selected_human_feedback_ids) then
      raise no_data_found using message='candidate task feedback unavailable';
    end if;
  end if;
  selected:=platform_hr.create_position_task_request_v69(
    selected_task_request_id,selected_owner_internal_user_id,
    selected_position_id,selected_client_request_id,
    selected_canonical_payload_sha256,selected_task_kind,
    selected_expected_context_version_id,selected_material_attachment_ids,
    selected_candidate_id,selected_position_candidate_id);
  if selected_snapshot_sha256 is not null then
    update platform_hr.position_task_requests request set
      document_ids=selected_document_ids,
      document_attachment_ids=selected_document_attachment_ids,
      human_feedback_ids=selected_human_feedback_ids,
      candidate_prompt_context=selected_candidate_prompt_context,
      candidate_snapshot_sha256=selected_snapshot_sha256
    where request.task_request_id=selected.task_request_id returning request.* into selected;
  end if;
  return selected;
end
$function$;

revoke all on function platform_hr.create_position_task_request_v69(
  uuid,uuid,uuid,uuid,text,text,uuid,uuid[],uuid,uuid
) from platform_control_app,platform_control_app_preview;
revoke all on function platform_hr.create_position_task_record_v69(
  uuid,uuid,uuid,uuid,text,uuid,uuid,uuid[],uuid,uuid,uuid[],uuid[],
  uuid,uuid,text,text
) from platform_control_app,platform_control_app_preview,
  platform_brain_worker,platform_brain_worker_preview,
  platform_control_maintenance,platform_control_maintenance_preview;
revoke all on function platform_hr.create_position_task_record_v71(
  uuid,uuid,uuid,uuid,text,uuid,uuid,uuid[],uuid,uuid,uuid[],uuid[],
  uuid,uuid,text,text,text
) from platform_control_app,platform_control_app_preview,
  platform_brain_worker,platform_brain_worker_preview,
  platform_control_maintenance,platform_control_maintenance_preview;
revoke all on function platform_hr.create_position_task_request_v79(
  uuid,uuid,uuid,uuid,text,text,uuid,uuid[],uuid,uuid,uuid[],uuid[],uuid[],text
) from public;
revoke all on function platform_hr.create_position_task_record_v79(
  uuid,uuid,uuid,uuid,text,uuid,uuid,uuid[],uuid,uuid,uuid[],uuid[],
  uuid,uuid,text,text,text
) from public;

do $migration$
declare selected_app name;
begin
  if current_database()='agent_platform_control'
     and current_user='platform_control_owner' then
    selected_app:='platform_control_app';
  elsif current_database()='agent_platform_control_preview'
        and current_user='platform_control_owner_preview' then
    selected_app:='platform_control_app_preview';
  else
    raise insufficient_privilege using
      message='HR candidate task scope migration owner/environment mismatch';
  end if;
  execute format(
    'grant execute on function platform_hr.create_position_task_request_v79('
    'uuid,uuid,uuid,uuid,text,text,uuid,uuid[],uuid,uuid,uuid[],uuid[],uuid[],text) to %I',
    selected_app);
  execute format(
    'grant execute on function platform_hr.create_position_task_record_v79('
    'uuid,uuid,uuid,uuid,text,uuid,uuid,uuid[],uuid,uuid,uuid[],uuid[],'
    'uuid,uuid,text,text,text) to %I',selected_app);
end
$migration$;
