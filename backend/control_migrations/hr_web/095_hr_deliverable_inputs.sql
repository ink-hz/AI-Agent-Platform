-- v7 inputs use the existing immutable turn context and result ledger.
alter table platform_hr.tool_operations_v6
  add column result_candidate_ids uuid[],
  add column candidate_derived boolean;
alter table platform_hr.position_task_records drop constraint position_task_records_contract_version_check;
alter table platform_hr.position_task_records add constraint position_task_records_contract_version_check
  check(contract_version in ('legacy','core_chat_collaboration_v6','core_chat_collaboration_v7'));
create function platform_hr.validate_turn_scope_node_v7(
  selected_owner uuid, selected_conversation uuid, selected_turn uuid,
  selected_context jsonb
) returns void language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare scope jsonb; selected_position uuid; attachment_ids uuid[]; candidate_ids uuid[]; input_ref jsonb; source_row record;
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
    or selected_context-'scope'-'methodSelection'-'inputResultRefs'<>'{}'::jsonb
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
        or exists(select 1 from platform_hr.candidate_draft_processing_attempts processing
          join platform_control.conversation_turns parser_turn on parser_turn.client_request_id=processing.attempt_id
          where processing.owner_internal_user_id=selected_owner and processing.attachment_id=selected.id
          and parser_turn.turn_id=selected_turn and processing.state='processing' and processing.lease_expires_at>clock_timestamp()
          and selected_position is null and cardinality(candidate_ids)=0 and cardinality(attachment_ids)=1)
        or exists(select 1 from platform_hr.candidate_documents document
          join platform_hr.position_candidates candidate on candidate.candidate_id=document.candidate_id and candidate.owner_internal_user_id=document.owner_internal_user_id
          where document.attachment_id=selected.id and document.owner_internal_user_id=selected_owner and document.status='active'
          and candidate.position_candidate_id=any(candidate_ids) and candidate.position_id=selected_position and candidate.status='active')
      )) then raise no_data_found using message='HR material unavailable'; end if;
  if exists(select 1 from platform_attachments.bindings binding
    where binding.owner_internal_user_id=selected_owner and binding.conversation_id=selected_conversation
      and binding.turn_id=selected_turn and binding.kind='turn_input'
      and not binding.attachment_id=any(attachment_ids)) then raise check_violation using message='HR scope omits input'; end if;
  if selected_context ? 'inputResultRefs' then
    if jsonb_typeof(selected_context->'inputResultRefs') <> 'array'
      or jsonb_array_length(selected_context->'inputResultRefs') > 20 then raise check_violation; end if;
    if (select count(*) <> count(distinct value->>'resultId') from jsonb_array_elements(selected_context->'inputResultRefs')) then raise check_violation; end if;
    for input_ref in select value from jsonb_array_elements(selected_context->'inputResultRefs') loop
      if jsonb_typeof(input_ref)<>'object' or not input_ref ?& array['resultId','schemaId','contentSha256']
        or input_ref-'resultId'-'schemaId'-'contentSha256'<>'{}'::jsonb then raise check_violation; end if;
      select o.*,t.hr_input_context into source_row from platform_hr.tool_operations_v6 o
        join platform_control.conversation_turns t using(turn_id)
        where o.receipt_id=(input_ref->>'resultId')::uuid and o.owner_internal_user_id=selected_owner
          and o.tool='hr.submit_result' and o.schema_id=input_ref->>'schemaId'
          and o.content_sha256=input_ref->>'contentSha256' and o.turn_id<>selected_turn
          and o.created_at < (select created_at from platform_control.conversation_turns where turn_id=selected_turn);
      if not found or (source_row.hr_input_context->'scope'->>'positionId')::uuid is distinct from selected_position then
        raise no_data_found using message='HR input result unavailable';
      end if;
      if not coalesce(source_row.result_candidate_ids,
          array(select value::uuid from jsonb_array_elements_text(source_row.hr_input_context->'scope'->'positionCandidateIds')))
          <@ candidate_ids then raise no_data_found using message='HR result candidate scope mismatch'; end if;
    end loop;
  end if;
end
$function$;

-- Validate every distinct ancestor once. UNION deduplicates shared ancestors and cycles.
revoke all on function platform_hr.validate_turn_scope_node_v7(uuid,uuid,uuid,jsonb) from public;
create or replace function platform_hr.validate_turn_scope_v6(
 selected_owner uuid,selected_conversation uuid,selected_turn uuid,selected_context jsonb
) returns void language plpgsql security definer set search_path=pg_catalog,platform_hr as $function$
declare ancestor record;
begin
 perform platform_hr.validate_turn_scope_node_v7(selected_owner,selected_conversation,selected_turn,selected_context);
 for ancestor in
   with recursive ancestors(turn_id) as (
     select o.turn_id from jsonb_array_elements(coalesce(selected_context->'inputResultRefs','[]'::jsonb)) r
       join platform_hr.tool_operations_v6 o on o.receipt_id=(r->>'resultId')::uuid and o.owner_internal_user_id=selected_owner
     union
     select o.turn_id from ancestors a join platform_control.conversation_turns t using(turn_id)
       cross join lateral jsonb_array_elements(coalesce(t.hr_input_context->'inputResultRefs','[]'::jsonb)) r
       join platform_hr.tool_operations_v6 o on o.receipt_id=(r->>'resultId')::uuid and o.owner_internal_user_id=selected_owner
   ) select t.turn_id,t.conversation_id,t.hr_input_context from ancestors a
       join platform_control.conversation_turns t using(turn_id)
 loop
   perform platform_hr.validate_turn_scope_node_v7(selected_owner,ancestor.conversation_id,ancestor.turn_id,ancestor.hr_input_context);
 end loop;
end $function$;

create or replace function platform_hr.record_turn_scope_v6(
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
      case when selected_context ? 'inputResultRefs' then 'core_chat_collaboration_v7' else 'core_chat_collaboration_v6' end,selected_context->'methodSelection'
    );
  end if;
  return selected_context;
end
$function$;


create function platform_hr.record_result_scope_v7(selected_grant uuid, selected_worker text,
 selected_token_hash text, selected_receipt uuid, selected_candidates uuid[])
returns void language plpgsql security definer set search_path=pg_catalog,platform_hr as $function$
declare identity jsonb; input jsonb; derived boolean;
begin
 identity:=platform_hr.authorize_tool_grant_v6(selected_grant,selected_worker,selected_token_hash);
 select hr_input_context into input from platform_control.conversation_turns where turn_id=(identity->>'turnId')::uuid;
 if not selected_candidates <@ array(select value::uuid from jsonb_array_elements_text(input->'scope'->'positionCandidateIds')) then raise insufficient_privilege; end if;
 derived:=jsonb_array_length(input->'scope'->'positionCandidateIds')>0
   or exists(select 1 from platform_hr.candidate_documents d where d.owner_internal_user_id=(identity->>'ownerId')::uuid
     and (input->'scope'->'attachmentIds') ? d.attachment_id::text)
   or exists(select 1 from jsonb_array_elements(coalesce(input->'inputResultRefs','[]'::jsonb)) r
     join platform_hr.tool_operations_v6 o on o.receipt_id=(r->>'resultId')::uuid where o.candidate_derived is true)
   or exists(select 1 from platform_hr.tool_operations_v6 o where o.turn_id=(identity->>'turnId')::uuid and o.resource_kind='candidate');
 update platform_hr.tool_operations_v6 set result_candidate_ids=selected_candidates,candidate_derived=derived
   where receipt_id=selected_receipt and turn_id=(identity->>'turnId')::uuid
     and tool='hr.submit_result' and result_candidate_ids is null;
 if not found then raise no_data_found; end if;
end $function$;
revoke all on function platform_hr.record_result_scope_v7(uuid,text,text,uuid,uuid[]) from public;
do $grant$
declare app_role name; brain_role name;
begin
 if current_database()='agent_platform_control' and current_user='platform_control_owner' then
 app_role:='platform_control_app';brain_role:='platform_brain_worker';
 elsif current_database()='agent_platform_control_preview' and current_user='platform_control_owner_preview' then
 app_role:='platform_control_app_preview';brain_role:='platform_brain_worker_preview';
 else raise insufficient_privilege;end if;
 execute format('grant execute on function platform_hr.record_result_scope_v7(uuid,text,text,uuid,uuid[]) to %I,%I',app_role,brain_role);
end $grant$;

create or replace function platform_hr.guard_position_task_record_immutability_v69()
returns trigger language plpgsql set search_path=pg_catalog,platform_hr as $function$
begin
 if tg_op='DELETE' then
  if session_user in ('platform_control_app','platform_control_app_preview','platform_brain_worker','platform_brain_worker_preview') then
   raise check_violation using message='position task record is immutable';
  end if;
  return old;
 end if;
 if not (
  (to_jsonb(new)-'output_artifact_version_id'-'draft_context_version_id'-'role_package')=
  (to_jsonb(old)-'output_artifact_version_id'-'draft_context_version_id'-'role_package')
  and (old.output_artifact_version_id is null or new.output_artifact_version_id=old.output_artifact_version_id)
  and (old.draft_context_version_id is null or new.draft_context_version_id=old.draft_context_version_id)
  and (new.role_package is not distinct from old.role_package or
    (old.role_package is null and old.contract_version in ('core_chat_collaboration_v6','core_chat_collaboration_v7') and new.role_package is not null))
 ) then raise check_violation using message='position task record is immutable'; end if;
 return new;
end $function$;
create or replace function platform_hr.pin_turn_role_v6(selected_owner uuid,selected_turn uuid,selected_role jsonb)
returns void language plpgsql security definer set search_path=pg_catalog,platform_hr as $function$
begin
 if session_user not in ('platform_control_app','platform_control_app_preview','platform_brain_worker','platform_brain_worker_preview') then raise insufficient_privilege; end if;
 if jsonb_typeof(selected_role)<>'object' or pg_column_size(selected_role)>2048
 or selected_role->>'teamCommit' !~ '^[a-f0-9]{40}$'
 or selected_role->>'manifestSha256' !~ '^[a-f0-9]{64}$' then raise check_violation; end if;
 update platform_hr.position_task_records set role_package=selected_role
 where owner_internal_user_id=selected_owner and turn_id=selected_turn and contract_version in ('core_chat_collaboration_v6','core_chat_collaboration_v7')
 and (role_package is null or role_package=selected_role);
 if not found and exists(select 1 from platform_hr.position_task_records where turn_id=selected_turn) then raise check_violation; end if;
end $function$;
