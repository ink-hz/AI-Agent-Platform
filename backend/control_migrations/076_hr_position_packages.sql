create unique index position_draft_owner_identity_v76
  on platform_hr.position_drafts(draft_id,owner_internal_user_id);

create table platform_hr.position_draft_versions (
  draft_version_id uuid primary key,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  draft_id uuid not null,
  client_request_id uuid not null,
  version_number integer not null check (version_number>0),
  title text not null check (char_length(btrim(title)) between 1 and 500),
  modules jsonb not null check (
    jsonb_typeof(modules)='object'
    and modules ?& array['mission','jd','jr']
    and modules-'mission'-'jd'-'jr'='{}'::jsonb
    and jsonb_typeof(modules->'mission')='object'
    and jsonb_typeof(modules->'jd')='object'
    and jsonb_typeof(modules->'jr')='object'
    and (modules->'mission')-'text'='{}'::jsonb
    and (modules->'jd')-'text'='{}'::jsonb
    and (modules->'jr')-'text'='{}'::jsonb
    and (modules->'mission') ? 'text'
    and (modules->'jd') ? 'text'
    and (modules->'jr') ? 'text'
    and jsonb_typeof(modules->'mission'->'text')='string'
    and jsonb_typeof(modules->'jd'->'text')='string'
    and jsonb_typeof(modules->'jr'->'text')='string'
    and char_length(btrim(modules->'mission'->>'text')) between 1 and 131072
    and char_length(btrim(modules->'jd'->>'text')) between 1 and 131072
    and char_length(btrim(modules->'jr'->>'text')) between 1 and 131072
    and octet_length(modules::text)<=524288
  ),
  source_conversation_id uuid not null,
  source_turn_id uuid not null,
  source_assistant_message_id uuid not null,
  agent_id text not null check (char_length(btrim(agent_id)) between 1 and 128),
  model_version text not null
    check (char_length(btrim(model_version)) between 1 and 160),
  row_version bigint not null default 1 check (row_version>0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (draft_id,owner_internal_user_id)
    references platform_hr.position_drafts(draft_id,owner_internal_user_id),
  foreign key (source_conversation_id,owner_internal_user_id)
    references platform_control.conversations(
      conversation_id,owner_internal_user_id
    ),
  foreign key (source_conversation_id,source_turn_id)
    references platform_control.conversation_turns(conversation_id,turn_id),
  foreign key (source_conversation_id,source_assistant_message_id)
    references platform_control.conversation_messages(conversation_id,message_id),
  unique (draft_version_id,owner_internal_user_id),
  unique (owner_internal_user_id,client_request_id),
  unique (owner_internal_user_id,draft_id,version_number),
  unique (owner_internal_user_id,draft_id,source_assistant_message_id)
);

alter table platform_hr.position_drafts
  add column confirmation_client_request_id uuid,
  add column confirmation_draft_version_id uuid,
  add column confirmation_context_version_id uuid,
  add column confirmation_conversation_id uuid,
  add column confirmation_expected_row_version bigint check (
    confirmation_expected_row_version is null
    or confirmation_expected_row_version>0
  ),
  add foreign key (confirmation_draft_version_id,owner_internal_user_id)
    references platform_hr.position_draft_versions(
      draft_version_id,owner_internal_user_id
    ),
  add foreign key (confirmation_context_version_id,owner_internal_user_id)
    references platform_hr.position_context_versions(
      context_version_id,owner_internal_user_id
    ),
  add foreign key (confirmation_conversation_id,owner_internal_user_id)
    references platform_control.conversations(
      conversation_id,owner_internal_user_id
    ),
  add unique (owner_internal_user_id,confirmation_client_request_id),
  add check (
    (confirmation_client_request_id is null
      and confirmation_draft_version_id is null
      and confirmation_context_version_id is null
      and confirmation_conversation_id is null
      and confirmation_expected_row_version is null)
    or (confirmation_client_request_id is not null
      and confirmation_draft_version_id is not null
      and confirmation_context_version_id is not null
      and confirmation_conversation_id is not null
      and confirmation_expected_row_version is not null)
  );

create function platform_hr.guard_position_draft_version_immutability_v76()
returns trigger language plpgsql
set search_path=pg_catalog,platform_hr
as $function$
begin
  raise check_violation using message='position draft version is immutable';
end
$function$;

create trigger guard_position_draft_version_immutability_v76
before update or delete on platform_hr.position_draft_versions
for each row execute function
  platform_hr.guard_position_draft_version_immutability_v76();

create function platform_hr.create_position_draft_version_v76(
  selected_draft_version_id uuid,
  selected_owner_internal_user_id uuid,
  selected_draft_id uuid,
  selected_client_request_id uuid,
  selected_title text,
  selected_modules jsonb,
  selected_source_conversation_id uuid,
  selected_source_turn_id uuid,
  selected_source_assistant_message_id uuid,
  selected_agent_id text,
  selected_model_version text
) returns platform_hr.position_draft_versions
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.position_draft_versions%rowtype;
declare draft platform_hr.position_drafts%rowtype;
declare next_version integer;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  select * into draft from platform_hr.position_drafts draft_record
  where draft_record.draft_id=selected_draft_id
    and draft_record.owner_internal_user_id=selected_owner_internal_user_id
  for update;
  if not found then raise no_data_found; end if;
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':position-draft-version-request:' ||
    selected_client_request_id::text,0
  ));
  select * into selected from platform_hr.position_draft_versions version
  where version.owner_internal_user_id=selected_owner_internal_user_id
    and version.client_request_id=selected_client_request_id;
  if found then
    if selected.draft_version_id is distinct from selected_draft_version_id
      or selected.draft_id is distinct from selected_draft_id
      or selected.title is distinct from btrim(selected_title)
      or selected.modules is distinct from selected_modules
      or selected.source_conversation_id
        is distinct from selected_source_conversation_id
      or selected.source_turn_id is distinct from selected_source_turn_id
      or selected.source_assistant_message_id
        is distinct from selected_source_assistant_message_id
      or selected.agent_id is distinct from btrim(selected_agent_id)
      or selected.model_version is distinct from btrim(selected_model_version) then
      raise unique_violation using
        message='position draft version idempotency payload mismatch';
    end if;
    return selected;
  end if;
  if draft.state<>'proposed'
    or draft.source_conversation_id
      is distinct from selected_source_conversation_id then
    raise no_data_found;
  end if;
  perform 1
  from platform_control.conversation_turns turn_record
  join platform_control.conversation_messages assistant
    on assistant.conversation_id=turn_record.conversation_id
    and assistant.message_id=turn_record.assistant_message_id
  join platform_control.conversations conversation
    on conversation.conversation_id=turn_record.conversation_id
  where turn_record.conversation_id=selected_source_conversation_id
    and turn_record.turn_id=selected_source_turn_id
    and turn_record.assistant_message_id=selected_source_assistant_message_id
    and turn_record.status='completed'
    and assistant.role='assistant'
    and assistant.delivery_status='completed'
    and conversation.owner_internal_user_id=selected_owner_internal_user_id
    and conversation.mode='direct_agent'
    and conversation.direct_agent_id='hr-bot';
  if not found then raise no_data_found; end if;
  if jsonb_typeof(selected_modules)<>'object'
    or not (selected_modules ?& array['mission','jd','jr'])
    or selected_modules-'mission'-'jd'-'jr'<>'{}'::jsonb then
    raise check_violation using message='position package modules invalid';
  end if;
  select coalesce(max(version_number),0)+1 into next_version
  from platform_hr.position_draft_versions version
  where version.owner_internal_user_id=selected_owner_internal_user_id
    and version.draft_id=selected_draft_id;
  insert into platform_hr.position_draft_versions(
    draft_version_id,owner_internal_user_id,draft_id,client_request_id,
    version_number,title,modules,source_conversation_id,source_turn_id,
    source_assistant_message_id,agent_id,model_version
  ) values (
    selected_draft_version_id,selected_owner_internal_user_id,
    selected_draft_id,selected_client_request_id,next_version,
    btrim(selected_title),selected_modules,selected_source_conversation_id,
    selected_source_turn_id,selected_source_assistant_message_id,
    btrim(selected_agent_id),btrim(selected_model_version)
  ) returning * into selected;
  return selected;
end
$function$;

create function platform_hr.confirm_position_package_v76(
  selected_owner_internal_user_id uuid,
  selected_draft_id uuid,
  selected_draft_version_id uuid,
  selected_client_request_id uuid,
  selected_expected_row_version bigint
) returns table (
  position_id uuid,
  context_version_id uuid,
  conversation_id uuid
)
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare draft platform_hr.position_drafts%rowtype;
declare selected_version platform_hr.position_draft_versions%rowtype;
declare new_position_id uuid;
declare new_context_version_id uuid;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  select * into draft from platform_hr.position_drafts draft_record
  where draft_record.draft_id=selected_draft_id
    and draft_record.owner_internal_user_id=selected_owner_internal_user_id
  for update;
  if not found then raise no_data_found; end if;
  if draft.confirmation_client_request_id is not null then
    if draft.confirmation_client_request_id
        is distinct from selected_client_request_id
      or draft.confirmation_draft_version_id
        is distinct from selected_draft_version_id
      or draft.confirmation_expected_row_version
        is distinct from selected_expected_row_version then
      raise unique_violation using
        message='position package confirmation payload mismatch';
    end if;
    return query select
      draft.resolved_position_id,
      draft.confirmation_context_version_id,
      draft.confirmation_conversation_id;
    return;
  end if;
  if draft.state<>'proposed'
    or draft.row_version<>selected_expected_row_version then
    raise serialization_failure using message='position draft row conflict';
  end if;
  select * into selected_version
  from platform_hr.position_draft_versions version
  where version.draft_version_id=selected_draft_version_id
    and version.owner_internal_user_id=selected_owner_internal_user_id
    and version.draft_id=selected_draft_id
    and version.source_conversation_id=draft.source_conversation_id;
  if not found then raise no_data_found; end if;
  new_position_id := md5(
    selected_owner_internal_user_id::text || ':position-package:' ||
    selected_client_request_id::text || ':position'
  )::uuid;
  new_context_version_id := md5(
    selected_owner_internal_user_id::text || ':position-package:' ||
    selected_client_request_id::text || ':context'
  )::uuid;
  insert into platform_hr.positions(
    position_id,owner_internal_user_id,client_request_id,source_kind,title,
    internal_status
  ) values (
    new_position_id,selected_owner_internal_user_id,
    selected_client_request_id,'manual',selected_version.title,'active'
  );
  insert into platform_hr.position_context_versions(
    context_version_id,owner_internal_user_id,position_id,client_request_id,
    version_number,state,modules,summary,source_conversation_id,
    source_turn_id,agent_id,model_version,created_by,confirmed_by,
    confirmed_at,confirmed_module_names
  ) values (
    new_context_version_id,selected_owner_internal_user_id,new_position_id,
    selected_client_request_id,1,'confirmed',selected_version.modules,
    selected_version.title,selected_version.source_conversation_id,
    selected_version.source_turn_id,selected_version.agent_id,
    selected_version.model_version,selected_owner_internal_user_id,
    selected_owner_internal_user_id,now(),array['mission','jd','jr']
  );
  update platform_hr.positions set
    current_context_version_id=new_context_version_id
  where positions.position_id=new_position_id
    and positions.owner_internal_user_id=selected_owner_internal_user_id;
  insert into platform_hr.position_conversations(
    conversation_id,owner_internal_user_id,position_id,client_request_id,
    binding_kind
  ) values (
    selected_version.source_conversation_id,selected_owner_internal_user_id,
    new_position_id,selected_client_request_id,'draft_confirmed'
  );
  update platform_hr.position_drafts set
    state='confirmed',resolved_position_id=new_position_id,
    confirmation_client_request_id=selected_client_request_id,
    confirmation_draft_version_id=selected_draft_version_id,
    confirmation_context_version_id=new_context_version_id,
    confirmation_conversation_id=selected_version.source_conversation_id,
    confirmation_expected_row_version=selected_expected_row_version,
    row_version=row_version+1,updated_at=now()
  where position_drafts.draft_id=selected_draft_id
    and position_drafts.owner_internal_user_id=selected_owner_internal_user_id;
  return query select
    new_position_id,new_context_version_id,
    selected_version.source_conversation_id;
end
$function$;

revoke all on table platform_hr.position_draft_versions from public;
revoke all on function platform_hr.guard_position_draft_version_immutability_v76()
  from public;
revoke all on function platform_hr.create_position_draft_version_v76(
  uuid,uuid,uuid,uuid,text,jsonb,uuid,uuid,uuid,text,text
) from public;
revoke all on function platform_hr.confirm_position_package_v76(
  uuid,uuid,uuid,uuid,bigint
) from public;

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
      message='HR position package migration owner/environment mismatch';
  end if;
  execute format(
    'grant select on platform_hr.position_draft_versions to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.create_position_draft_version_v76('
    'uuid,uuid,uuid,uuid,text,jsonb,uuid,uuid,uuid,text,text) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.confirm_position_package_v76('
    'uuid,uuid,uuid,uuid,bigint) to %I',selected_app
  );
end
$migration$;
