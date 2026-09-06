create table platform_hr.position_intelligence_bundle_references (
  reference_id uuid primary key,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  client_request_id uuid not null,
  position_id uuid not null,
  conversation_id uuid not null,
  turn_id uuid not null,
  bundle_id uuid not null,
  observed_at timestamptz not null,
  context_document jsonb not null check (
    jsonb_typeof(context_document)='object'
    and octet_length(context_document::text)<=32768
    and context_document->>'bundle_id'=bundle_id::text
  ),
  created_at timestamptz not null default now(),
  foreign key (position_id,owner_internal_user_id)
    references platform_hr.positions(position_id,owner_internal_user_id),
  foreign key (conversation_id,owner_internal_user_id)
    references platform_control.conversations(
      conversation_id,owner_internal_user_id
    ),
  foreign key (conversation_id,turn_id)
    references platform_control.conversation_turns(conversation_id,turn_id),
  foreign key (bundle_id,owner_internal_user_id)
    references platform_hr.intelligence_bundles(bundle_id,owner_internal_user_id),
  unique(owner_internal_user_id,client_request_id),
  unique(owner_internal_user_id,position_id,turn_id)
);

create function platform_hr.guard_intelligence_bundle_reference_v86()
returns trigger language plpgsql
set search_path=pg_catalog,platform_hr
as $function$
begin
  raise check_violation using message='intelligence bundle task reference is immutable';
end
$function$;

create trigger guard_intelligence_bundle_reference_v86
before update or delete
on platform_hr.position_intelligence_bundle_references
for each row execute function
  platform_hr.guard_intelligence_bundle_reference_v86();

create function platform_hr.create_intelligence_bundle_reference_v86(
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
  join platform_hr.position_conversations binding
    on binding.conversation_id=turn_record.conversation_id
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

create function platform_hr.read_intelligence_bundle_reference_for_turn_v86(
  selected_owner_internal_user_id uuid,
  selected_position_id uuid,
  selected_turn_id uuid
) returns setof platform_hr.position_intelligence_bundle_references
language sql security definer stable
set search_path=pg_catalog,platform_hr
as $function$
  select reference.*
  from platform_hr.position_intelligence_bundle_references reference
  where session_user in ('platform_control_app','platform_control_app_preview')
    and (current_database()='agent_platform_control') =
      (session_user='platform_control_app')
    and reference.owner_internal_user_id=selected_owner_internal_user_id
    and reference.position_id=selected_position_id
    and reference.turn_id=selected_turn_id
$function$;

revoke all on platform_hr.position_intelligence_bundle_references from public;
revoke all on platform_hr.position_intelligence_bundle_references
  from platform_control_app,platform_control_app_preview;
revoke all on function platform_hr.create_intelligence_bundle_reference_v86(
  uuid,uuid,uuid,uuid,uuid,uuid,timestamptz,jsonb
) from public;
grant execute on function platform_hr.create_intelligence_bundle_reference_v86(
  uuid,uuid,uuid,uuid,uuid,uuid,timestamptz,jsonb
) to platform_control_app,platform_control_app_preview;
revoke all on function platform_hr.read_intelligence_bundle_reference_for_turn_v86(
  uuid,uuid,uuid
) from public;
grant execute on function platform_hr.read_intelligence_bundle_reference_for_turn_v86(
  uuid,uuid,uuid
) to platform_control_app,platform_control_app_preview;
