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

create table platform_hr.position_package_projections (
  projection_id uuid primary key,
  projection_request_id uuid not null,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  draft_id uuid not null,
  conversation_id uuid not null,
  turn_id uuid not null,
  assistant_message_id uuid not null,
  state text not null check (
    state in ('pending','processing','completed','skipped','failed')
  ),
  worker_id text,
  lease_expires_at timestamptz,
  available_at timestamptz not null default now(),
  attempt_count integer not null default 0 check (attempt_count>=0),
  draft_version_id uuid,
  error_code text check (
    error_code is null or error_code in (
      'envelope_invalid','projection_scope_invalid','projection_unavailable'
    )
  ),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  terminal_at timestamptz,
  foreign key (draft_id,owner_internal_user_id)
    references platform_hr.position_drafts(draft_id,owner_internal_user_id),
  foreign key (conversation_id,owner_internal_user_id)
    references platform_control.conversations(
      conversation_id,owner_internal_user_id
    ),
  foreign key (conversation_id,turn_id)
    references platform_control.conversation_turns(conversation_id,turn_id),
  foreign key (conversation_id,assistant_message_id)
    references platform_control.conversation_messages(
      conversation_id,message_id
    ),
  foreign key (draft_version_id,owner_internal_user_id)
    references platform_hr.position_draft_versions(
      draft_version_id,owner_internal_user_id
    ),
  unique (projection_request_id),
  unique (assistant_message_id),
  check (
    (state='processing' and worker_id is not null and lease_expires_at is not null)
    or (state<>'processing' and worker_id is null and lease_expires_at is null)
  ),
  check ((state='completed')=(draft_version_id is not null)),
  check (
    (state in ('completed','skipped','failed'))=(terminal_at is not null)
  ),
  check (
    (state='failed' and error_code in (
      'envelope_invalid','projection_scope_invalid'
    ))
    or (state='pending' and error_code is not distinct from 'projection_unavailable')
    or (state in ('processing','completed','skipped') and error_code is null)
  )
);

create index position_package_projections_available_v76
  on platform_hr.position_package_projections(
    state,available_at,lease_expires_at,created_at
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

create function platform_hr.claim_position_package_projection_v76(
  selected_worker_id text,
  selected_lease_seconds integer
) returns table(
  projection_id uuid,
  projection_request_id uuid,
  owner_internal_user_id uuid,
  draft_id uuid,
  conversation_id uuid,
  turn_id uuid,
  assistant_message_id uuid,
  agent_id text,
  content_ciphertext bytea,
  encryption_key_version integer
)
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected_projection_id uuid;
declare selected_owner_id uuid;
declare selected_draft_id uuid;
declare selected_conversation_id uuid;
declare selected_turn_id uuid;
declare selected_message_id uuid;
declare selected_conversation_title text;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or selected_worker_id is null
     or selected_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
     or selected_lease_seconds not between 30 and 900 then
    raise insufficient_privilege;
  end if;

  select conversation.owner_internal_user_id,projection.draft_id,
    conversation.conversation_id,turn.turn_id,message.message_id,
    conversation.title
  into selected_owner_id,selected_draft_id,selected_conversation_id,
    selected_turn_id,selected_message_id,selected_conversation_title
  from platform_control.conversation_messages message
  join platform_control.conversation_turns turn
    on turn.conversation_id=message.conversation_id
   and turn.turn_id=message.turn_id
   and turn.assistant_message_id=message.message_id
   and turn.status='completed'
  join platform_control.conversations conversation
    on conversation.conversation_id=message.conversation_id
   and conversation.mode='direct_agent'
   and conversation.direct_agent_id='hr-bot'
  left join platform_hr.position_package_projections projection
    on projection.assistant_message_id=message.message_id
  where message.role='assistant'
    and message.delivery_status='completed'
    and octet_length(message.content_ciphertext)>0
    and (
      projection.projection_id is null
      or (projection.state='pending' and projection.available_at<=now())
      or (projection.state='processing' and projection.lease_expires_at<=now())
    )
  order by message.created_at,message.message_id
  for update of message skip locked
  limit 1;

  if selected_message_id is null then return; end if;

  if selected_draft_id is null then
    select draft.draft_id into selected_draft_id
    from platform_hr.position_drafts draft
    where draft.owner_internal_user_id=selected_owner_id
      and draft.source_conversation_id=selected_conversation_id
      and draft.state='proposed'
    order by draft.created_at,draft.draft_id
    limit 1;
  end if;

  if selected_draft_id is null then
    selected_draft_id := md5(
      selected_owner_id::text || ':position-package:conversation:' ||
      selected_conversation_id::text || ':draft'
    )::uuid;
    insert into platform_hr.position_drafts(
      draft_id,owner_internal_user_id,client_request_id,source_kind,
      source_key,source_conversation_id,title,proposal,evidence,
      discovery_rule_version
    ) values (
      selected_draft_id,selected_owner_id,
      md5(
        selected_owner_id::text || ':position-package:conversation:' ||
        selected_conversation_id::text || ':draft-request'
      )::uuid,
      'new_conversation','conversation:' || selected_conversation_id::text,
      selected_conversation_id,selected_conversation_title,'{}'::jsonb,
      '{}'::jsonb,'interactive-v1'
    )
    on conflict do nothing;
    select draft.draft_id into selected_draft_id
    from platform_hr.position_drafts draft
    where draft.owner_internal_user_id=selected_owner_id
      and draft.source_kind='new_conversation'
      and draft.source_key='conversation:' || selected_conversation_id::text
      and draft.source_conversation_id=selected_conversation_id;
    if selected_draft_id is null then raise no_data_found; end if;
  end if;

  insert into platform_hr.position_package_projections(
    projection_id,projection_request_id,owner_internal_user_id,draft_id,
    conversation_id,turn_id,assistant_message_id,state,worker_id,
    lease_expires_at,attempt_count
  ) values (
    md5(selected_message_id::text || ':position-package-ledger')::uuid,
    md5(selected_message_id::text || ':position-package-projection')::uuid,
    selected_owner_id,selected_draft_id,selected_conversation_id,
    selected_turn_id,selected_message_id,'processing',selected_worker_id,
    now()+make_interval(secs=>selected_lease_seconds),1
  )
  on conflict on constraint
    position_package_projections_assistant_message_id_key do update set
    state='processing',worker_id=excluded.worker_id,
    lease_expires_at=excluded.lease_expires_at,
    attempt_count=platform_hr.position_package_projections.attempt_count+1,
    error_code=null,updated_at=now()
  where (platform_hr.position_package_projections.state='pending'
      and platform_hr.position_package_projections.available_at<=now())
    or (platform_hr.position_package_projections.state='processing'
      and platform_hr.position_package_projections.lease_expires_at<=now())
  returning platform_hr.position_package_projections.projection_id
    into selected_projection_id;

  if selected_projection_id is null then return; end if;

  return query
  select projection.projection_id,projection.projection_request_id,
    projection.owner_internal_user_id,projection.draft_id,
    projection.conversation_id,projection.turn_id,
    projection.assistant_message_id,'hr-bot'::text,
    message.content_ciphertext,message.encryption_key_version
  from platform_hr.position_package_projections projection
  join platform_control.conversation_messages message
    on message.conversation_id=projection.conversation_id
   and message.message_id=projection.assistant_message_id
  where projection.projection_id=selected_projection_id
    and projection.state='processing'
    and projection.worker_id=selected_worker_id;
end
$function$;

create function platform_hr.complete_position_package_projection_v76(
  selected_projection_id uuid,
  selected_worker_id text,
  selected_projection_request_id uuid,
  selected_draft_version_id uuid
) returns boolean
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or selected_worker_id is null
     or selected_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$' then
    raise insufficient_privilege;
  end if;
  update platform_hr.position_package_projections projection set
    state=case when selected_draft_version_id is null
      then 'skipped' else 'completed' end,
    worker_id=null,lease_expires_at=null,
    draft_version_id=selected_draft_version_id,error_code=null,
    terminal_at=now(),updated_at=now()
  where projection.projection_id=selected_projection_id
    and projection.projection_request_id=selected_projection_request_id
    and projection.state='processing'
    and projection.worker_id=selected_worker_id
    and projection.lease_expires_at>now()
    and (
      selected_draft_version_id is null
      or exists(
        select 1 from platform_hr.position_draft_versions version
        where version.draft_version_id=selected_draft_version_id
          and version.owner_internal_user_id=projection.owner_internal_user_id
          and version.draft_id=projection.draft_id
          and version.client_request_id=projection.projection_request_id
          and version.source_conversation_id=projection.conversation_id
          and version.source_turn_id=projection.turn_id
          and version.source_assistant_message_id=projection.assistant_message_id
      )
    );
  if found then return true; end if;
  return exists(
    select 1 from platform_hr.position_package_projections projection
    where projection.projection_id=selected_projection_id
      and projection.projection_request_id=selected_projection_request_id
      and (
        (selected_draft_version_id is null and projection.state='skipped'
          and projection.draft_version_id is null)
        or (selected_draft_version_id is not null
          and projection.state='completed'
          and projection.draft_version_id=selected_draft_version_id)
      )
  );
end
$function$;

create function platform_hr.fail_position_package_projection_v76(
  selected_projection_id uuid,
  selected_worker_id text,
  selected_projection_request_id uuid,
  selected_error_code text
) returns boolean
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or selected_error_code not in (
       'envelope_invalid','projection_scope_invalid'
     ) then
    raise insufficient_privilege;
  end if;
  update platform_hr.position_package_projections set
    state='failed',worker_id=null,lease_expires_at=null,
    draft_version_id=null,error_code=selected_error_code,
    terminal_at=now(),updated_at=now()
  where projection_id=selected_projection_id
    and projection_request_id=selected_projection_request_id
    and state='processing' and worker_id=selected_worker_id
    and lease_expires_at>now();
  if found then return true; end if;
  return exists(
    select 1 from platform_hr.position_package_projections
    where projection_id=selected_projection_id
      and projection_request_id=selected_projection_request_id
      and state='failed' and error_code=selected_error_code
  );
end
$function$;

create function platform_hr.release_position_package_projection_v76(
  selected_projection_id uuid,
  selected_worker_id text,
  selected_projection_request_id uuid,
  selected_error_code text
) returns boolean
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or selected_error_code<>'projection_unavailable' then
    raise insufficient_privilege;
  end if;
  update platform_hr.position_package_projections set
    state='pending',worker_id=null,lease_expires_at=null,
    available_at=now()+interval '1 second',draft_version_id=null,
    error_code=selected_error_code,terminal_at=null,updated_at=now()
  where projection_id=selected_projection_id
    and projection_request_id=selected_projection_request_id
    and state='processing' and worker_id=selected_worker_id
    and lease_expires_at>now();
  return found;
end
$function$;

revoke all on table platform_hr.position_draft_versions from public;
revoke all on table platform_hr.position_package_projections from public;
revoke all on function platform_hr.guard_position_draft_version_immutability_v76()
  from public;
revoke all on function platform_hr.create_position_draft_version_v76(
  uuid,uuid,uuid,uuid,text,jsonb,uuid,uuid,uuid,text,text
) from public;
revoke all on function platform_hr.confirm_position_package_v76(
  uuid,uuid,uuid,uuid,bigint
) from public;
revoke all on function platform_hr.claim_position_package_projection_v76(
  text,integer
) from public;
revoke all on function platform_hr.complete_position_package_projection_v76(
  uuid,text,uuid,uuid
) from public;
revoke all on function platform_hr.fail_position_package_projection_v76(
  uuid,text,uuid,text
) from public;
revoke all on function platform_hr.release_position_package_projection_v76(
  uuid,text,uuid,text
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
    'grant select on platform_hr.position_package_projections to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.create_position_draft_version_v76('
    'uuid,uuid,uuid,uuid,text,jsonb,uuid,uuid,uuid,text,text) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.confirm_position_package_v76('
    'uuid,uuid,uuid,uuid,bigint) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.claim_position_package_projection_v76('
    'text,integer) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.complete_position_package_projection_v76('
    'uuid,text,uuid,uuid) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.fail_position_package_projection_v76('
    'uuid,text,uuid,text) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.release_position_package_projection_v76('
    'uuid,text,uuid,text) to %I',selected_app
  );
end
$migration$;
