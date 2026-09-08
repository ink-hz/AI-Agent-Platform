-- UNNUMBERED DRAFT; retain explicit Position-material validation unchanged.
-- Implicit freeform turns use exact owned turn_input bindings, not promotion.
create or replace function platform_hr.create_position_task_record_v69(
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
  selected_canonical_sha256 text
) returns platform_hr.position_task_records
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.position_task_records%rowtype;
declare request platform_hr.position_task_requests%rowtype;
declare current_official_id uuid;
declare current_context_id uuid;
declare turn_request_id uuid;
declare bound_material_ids uuid[];
declare request_is_explicit boolean := false;
begin
  if session_user not in (
    'platform_control_app','platform_control_app_preview',
    'platform_brain_worker','platform_brain_worker_preview'
  ) then raise insufficient_privilege; end if;
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':position-task:' ||
    selected_client_request_id::text,0
  ));
  select * into selected from platform_hr.position_task_records
  where owner_internal_user_id=selected_owner_internal_user_id
    and client_request_id=selected_client_request_id;
  if found then
    if selected.task_record_id<>selected_task_record_id
      or selected.position_id<>selected_position_id
      or selected.task_kind<>selected_task_kind
      or selected.official_position_version_id
        is distinct from selected_official_position_version_id
      or selected.context_version_id is distinct from selected_context_version_id
      or selected.material_attachment_ids<>selected_material_attachment_ids
      or selected.candidate_id is distinct from selected_candidate_id
      or selected.position_candidate_id is distinct from selected_position_candidate_id
      or selected.document_attachment_ids<>selected_document_attachment_ids
      or selected.human_feedback_ids<>selected_human_feedback_ids
      or selected.conversation_id<>selected_conversation_id
      or selected.turn_id<>selected_turn_id
      or selected.prompt_context<>selected_prompt_context
      or selected.canonical_sha256<>selected_canonical_sha256 then
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
  select * into request from platform_hr.position_task_requests
  where owner_internal_user_id=selected_owner_internal_user_id
    and position_id=selected_position_id
    and client_request_id=selected_client_request_id
    and status='active' for update;
  if found then
    request_is_explicit := true;
    if request.task_kind<>selected_task_kind
      or request.expected_context_version_id is distinct from selected_context_version_id
      or request.material_attachment_ids<>selected_material_attachment_ids
      or request.candidate_id is distinct from selected_candidate_id
      or request.position_candidate_id is distinct from selected_position_candidate_id then
      raise unique_violation using message='position task selection mismatch';
    end if;
  elsif selected_task_kind<>'freeform'
    or selected_candidate_id is not null
    or selected_position_candidate_id is not null
    or cardinality(selected_document_attachment_ids)<>0
    or cardinality(selected_human_feedback_ids)<>0 then
    raise no_data_found using message='explicit position task request required';
  end if;
  select current_official_version_id,current_context_version_id
    into current_official_id,current_context_id
  from platform_hr.positions
  where owner_internal_user_id=selected_owner_internal_user_id
    and position_id=selected_position_id for update;
  if current_official_id is distinct from selected_official_position_version_id then
    raise serialization_failure using message='official position task baseline conflict';
  end if;
  if not request_is_explicit
    and current_context_id is distinct from selected_context_version_id then
    raise serialization_failure using message='implicit position task context conflict';
  end if;
  if selected_context_version_id is not null then
    perform 1 from platform_hr.position_context_versions
    where context_version_id=selected_context_version_id
      and owner_internal_user_id=selected_owner_internal_user_id
      and position_id=selected_position_id and state in ('confirmed','superseded');
    if not found then raise no_data_found; end if;
  end if;
  select coalesce(array_agg(binding.attachment_id order by binding.attachment_id),'{}'::uuid[])
    into bound_material_ids
  from platform_attachments.bindings binding
  where binding.owner_internal_user_id=selected_owner_internal_user_id
    and binding.conversation_id=selected_conversation_id
    and binding.turn_id=selected_turn_id and binding.kind='turn_input';
  if request_is_explicit then
    if request.material_attachment_ids<>selected_material_attachment_ids
      or not bound_material_ids<@selected_material_attachment_ids
      or not platform_hr.validate_position_materials_v69(
        selected_owner_internal_user_id,selected_position_id,
        selected_material_attachment_ids
      ) then raise no_data_found; end if;
  elsif bound_material_ids<>selected_material_attachment_ids
    or exists (
      select 1 from unnest(bound_material_ids) chosen(attachment_id)
      left join platform_attachments.attachments attachment
        on attachment.attachment_id=chosen.attachment_id
        and attachment.owner_internal_user_id=selected_owner_internal_user_id
      where attachment.attachment_id is null or attachment.state<>'ready'
        or attachment.deleted_at is not null
        or attachment.retained_until<=clock_timestamp()
        or attachment.immutable_locator is null or attachment.sha256 is null
        or exists (select 1 from platform_attachments.erasure_jobs erasure
          where erasure.attachment_id=attachment.attachment_id)
    ) then raise no_data_found; end if;
  if not platform_hr.validate_candidate_task_inputs_v69(
    selected_owner_internal_user_id,selected_position_id,
    selected_context_version_id,selected_candidate_id,
    selected_position_candidate_id,selected_document_attachment_ids,
    selected_human_feedback_ids
  ) then raise no_data_found; end if;
  if not request_is_explicit then
    insert into platform_hr.position_task_requests(
      task_request_id,owner_internal_user_id,position_id,client_request_id,
      canonical_payload_sha256,task_kind,expected_context_version_id,
      material_attachment_ids,candidate_id,position_candidate_id,status
    ) values (
      md5(selected_owner_internal_user_id::text || ':' ||
        selected_client_request_id::text || ':implicit-freeform')::uuid,
      selected_owner_internal_user_id,selected_position_id,
      selected_client_request_id,selected_canonical_sha256,'freeform',
      selected_context_version_id,selected_material_attachment_ids,
      null,null,'consumed'
    ) returning * into request;
  end if;
  insert into platform_hr.position_task_records(
    task_record_id,owner_internal_user_id,position_id,client_request_id,
    task_kind,official_position_version_id,context_version_id,
    material_attachment_ids,candidate_id,position_candidate_id,
    document_attachment_ids,human_feedback_ids,conversation_id,turn_id,
    prompt_context,canonical_sha256
  ) values (
    selected_task_record_id,selected_owner_internal_user_id,
    selected_position_id,selected_client_request_id,selected_task_kind,
    selected_official_position_version_id,selected_context_version_id,
    selected_material_attachment_ids,selected_candidate_id,
    selected_position_candidate_id,selected_document_attachment_ids,
    selected_human_feedback_ids,selected_conversation_id,selected_turn_id,
    selected_prompt_context,selected_canonical_sha256
  ) returning * into selected;
  if request_is_explicit then
    update platform_hr.position_task_requests set status='consumed'
    where task_request_id=request.task_request_id;
  end if;
  return selected;
end
$function$;
