create schema platform_hr authorization current_user;
revoke all on schema platform_hr from public;

create unique index artifact_owner_identity_v65
  on platform_attachments.artifacts(artifact_id,owner_internal_user_id);

create table platform_hr.positions (
  position_id uuid primary key,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  client_request_id uuid not null,
  source_kind text not null
    check (source_kind in ('official_site','manual')),
  official_job_id text
    check (official_job_id is null or official_job_id ~ '^J[0-9]{4,12}$'),
  title text not null check (char_length(btrim(title)) between 1 and 500),
  department text check (
    department is null or char_length(btrim(department)) between 1 and 500
  ),
  locations jsonb not null default '[]'::jsonb check (
    jsonb_typeof(locations)='array' and octet_length(locations::text) <= 32768
  ),
  official_status text check (
    official_status is null or
    official_status in ('active','stale','suspected_inactive','inactive')
  ),
  internal_status text not null default 'active'
    check (internal_status in ('draft','active','archived')),
  source_version text check (
    source_version is null or char_length(source_version) between 1 and 256
  ),
  official_content_hash text check (
    official_content_hash is null or official_content_hash ~ '^[a-f0-9]{64}$'
  ),
  row_version bigint not null default 1 check (row_version > 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (
    (source_kind='official_site' and official_job_id is not null
      and official_status is not null)
    or (source_kind='manual' and official_job_id is null
      and official_status is null)
  ),
  unique (position_id,owner_internal_user_id),
  unique (owner_internal_user_id,client_request_id)
);

create unique index positions_official_identity_v65
  on platform_hr.positions(owner_internal_user_id,official_job_id)
  where source_kind='official_site';

create table platform_hr.position_drafts (
  draft_id uuid primary key,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  client_request_id uuid not null,
  source_kind text not null
    check (source_kind in ('historical_conversation','new_conversation')),
  source_key text not null check (char_length(source_key) between 1 and 256),
  source_conversation_id uuid,
  title text not null check (char_length(btrim(title)) between 1 and 500),
  proposal jsonb not null default '{}'::jsonb check (
    jsonb_typeof(proposal)='object' and octet_length(proposal::text) <= 131072
  ),
  evidence jsonb not null check (
    jsonb_typeof(evidence)='object' and octet_length(evidence::text) <= 65536
  ),
  discovery_rule_version text not null
    check (char_length(discovery_rule_version) between 1 and 128),
  state text not null default 'proposed'
    check (state in ('proposed','confirmed','merged','dismissed')),
  resolved_position_id uuid,
  row_version bigint not null default 1 check (row_version > 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (source_conversation_id,owner_internal_user_id)
    references platform_control.conversations(
      conversation_id,owner_internal_user_id
    ),
  foreign key (resolved_position_id,owner_internal_user_id)
    references platform_hr.positions(position_id,owner_internal_user_id),
  check (
    (state='proposed' and resolved_position_id is null)
    or (state in ('confirmed','merged') and resolved_position_id is not null)
    or (state='dismissed' and resolved_position_id is null)
  ),
  unique (owner_internal_user_id,client_request_id),
  unique (owner_internal_user_id,source_kind,source_key)
);

create table platform_hr.position_conversations (
  conversation_id uuid primary key
    references platform_control.conversations(conversation_id),
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  position_id uuid not null,
  client_request_id uuid not null,
  binding_kind text not null check (
    binding_kind in ('created_in_position','draft_confirmed','draft_merged','historical_exact','manual_correction')
  ),
  previous_position_id uuid,
  created_at timestamptz not null default now(),
  foreign key (position_id,owner_internal_user_id)
    references platform_hr.positions(position_id,owner_internal_user_id),
  foreign key (previous_position_id,owner_internal_user_id)
    references platform_hr.positions(position_id,owner_internal_user_id),
  foreign key (conversation_id,owner_internal_user_id)
    references platform_control.conversations(
      conversation_id,owner_internal_user_id
    ),
  unique (owner_internal_user_id,client_request_id)
);

create table platform_hr.position_binding_events (
  event_id uuid primary key,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  conversation_id uuid not null,
  previous_position_id uuid not null,
  new_position_id uuid not null,
  reason text not null check (char_length(btrim(reason)) between 1 and 1000),
  created_at timestamptz not null default now(),
  foreign key (conversation_id,owner_internal_user_id)
    references platform_control.conversations(
      conversation_id,owner_internal_user_id
    ),
  foreign key (previous_position_id,owner_internal_user_id)
    references platform_hr.positions(position_id,owner_internal_user_id),
  foreign key (new_position_id,owner_internal_user_id)
    references platform_hr.positions(position_id,owner_internal_user_id),
  check (previous_position_id<>new_position_id),
  unique (owner_internal_user_id,event_id)
);

create table platform_hr.position_materials (
  position_id uuid not null,
  attachment_id uuid not null,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  client_request_id uuid not null,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (position_id,owner_internal_user_id)
    references platform_hr.positions(position_id,owner_internal_user_id),
  foreign key (attachment_id,owner_internal_user_id)
    references platform_attachments.attachments(
      attachment_id,owner_internal_user_id
    ),
  unique (position_id,attachment_id),
  unique (owner_internal_user_id,client_request_id)
);

create table platform_hr.position_artifacts (
  position_id uuid not null,
  artifact_id uuid not null
    references platform_attachments.artifacts(artifact_id),
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  client_request_id uuid not null,
  created_at timestamptz not null default now(),
  foreign key (position_id,owner_internal_user_id)
    references platform_hr.positions(position_id,owner_internal_user_id),
  foreign key (artifact_id,owner_internal_user_id)
    references platform_attachments.artifacts(
      artifact_id,owner_internal_user_id
    ),
  unique (position_id,artifact_id),
  unique (owner_internal_user_id,client_request_id)
);

create table platform_hr.position_import_evidence (
  evidence_id uuid primary key,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  position_id uuid,
  draft_id uuid,
  source_conversation_id uuid,
  source_message_seq integer check (
    source_message_seq is null or source_message_seq > 0
  ),
  source_kind text not null check (
    source_kind in ('official_snapshot','historical_exact','historical_draft')
  ),
  source_key text not null check (char_length(source_key) between 1 and 256),
  rule_version text not null check (char_length(rule_version) between 1 and 128),
  evidence jsonb not null check (
    jsonb_typeof(evidence)='object' and octet_length(evidence::text) <= 65536
  ),
  created_at timestamptz not null default now(),
  foreign key (position_id,owner_internal_user_id)
    references platform_hr.positions(position_id,owner_internal_user_id),
  foreign key (draft_id)
    references platform_hr.position_drafts(draft_id),
  foreign key (source_conversation_id,owner_internal_user_id)
    references platform_control.conversations(
      conversation_id,owner_internal_user_id
    ),
  check (num_nonnulls(position_id,draft_id)=1),
  unique (owner_internal_user_id,source_kind,source_key,rule_version)
);

create function platform_hr.create_position_v65(
  selected_position_id uuid,
  selected_owner_internal_user_id uuid,
  client_request_id uuid,
  selected_source_kind text,
  selected_official_job_id text,
  selected_title text,
  selected_department text,
  selected_locations jsonb,
  selected_official_status text,
  selected_source_version text
) returns platform_hr.positions
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.positions%rowtype;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  select * into selected from platform_hr.positions
  where owner_internal_user_id=selected_owner_internal_user_id
    and positions.client_request_id=create_position_v65.client_request_id;
  if found then return selected; end if;
  if selected_source_kind='official_site' then
    select * into selected from platform_hr.positions
    where owner_internal_user_id=selected_owner_internal_user_id
      and official_job_id=selected_official_job_id;
    if found then return selected; end if;
  end if;
  insert into platform_hr.positions(
    position_id,owner_internal_user_id,client_request_id,source_kind,
    official_job_id,title,department,locations,official_status,source_version
  ) values (
    selected_position_id,selected_owner_internal_user_id,client_request_id,
    selected_source_kind,selected_official_job_id,btrim(selected_title),
    nullif(btrim(selected_department),''),selected_locations,
    selected_official_status,selected_source_version
  ) returning * into selected;
  return selected;
end
$function$;

create function platform_hr.project_official_position_v65(
  selected_position_id uuid,
  selected_owner_internal_user_id uuid,
  client_request_id uuid,
  selected_official_job_id text,
  selected_title text,
  selected_department text,
  selected_locations jsonb,
  selected_official_status text,
  selected_source_version text,
  selected_content_hash text
) returns platform_hr.positions
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.positions%rowtype;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  select * into selected from platform_hr.positions
  where owner_internal_user_id=selected_owner_internal_user_id
    and official_job_id=selected_official_job_id for update;
  if found then
    update platform_hr.positions set
      title=btrim(selected_title),
      department=nullif(btrim(selected_department),''),
      locations=selected_locations,
      official_status=selected_official_status,
      source_version=selected_source_version,
      official_content_hash=selected_content_hash,
      row_version=case when
        title is distinct from btrim(selected_title)
        or department is distinct from nullif(btrim(selected_department),'')
        or locations is distinct from selected_locations
        or official_status is distinct from selected_official_status
        or source_version is distinct from selected_source_version
        or official_content_hash is distinct from selected_content_hash
        then row_version+1 else row_version end,
      updated_at=case when
        title is distinct from btrim(selected_title)
        or department is distinct from nullif(btrim(selected_department),'')
        or locations is distinct from selected_locations
        or official_status is distinct from selected_official_status
        or source_version is distinct from selected_source_version
        or official_content_hash is distinct from selected_content_hash
        then now() else updated_at end
    where position_id=selected.position_id returning * into selected;
    return selected;
  end if;
  insert into platform_hr.positions(
    position_id,owner_internal_user_id,client_request_id,source_kind,
    official_job_id,title,department,locations,official_status,source_version,
    official_content_hash
  ) values (
    selected_position_id,selected_owner_internal_user_id,client_request_id,
    'official_site',selected_official_job_id,btrim(selected_title),
    nullif(btrim(selected_department),''),selected_locations,
    selected_official_status,selected_source_version,selected_content_hash
  ) returning * into selected;
  return selected;
end
$function$;

create function platform_hr.confirm_position_draft_v65(
  selected_draft_id uuid,
  selected_owner_internal_user_id uuid,
  selected_position_id uuid,
  client_request_id uuid,
  expected_row_version bigint
) returns platform_hr.positions
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare draft platform_hr.position_drafts%rowtype;
declare selected platform_hr.positions%rowtype;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  select * into draft from platform_hr.position_drafts
  where draft_id=selected_draft_id
    and owner_internal_user_id=selected_owner_internal_user_id for update;
  if not found then raise no_data_found; end if;
  if draft.state='confirmed' then
    select * into selected from platform_hr.positions
    where position_id=draft.resolved_position_id;
    return selected;
  end if;
  if draft.state<>'proposed' or draft.row_version<>expected_row_version then
    raise serialization_failure;
  end if;
  insert into platform_hr.positions(
    position_id,owner_internal_user_id,client_request_id,source_kind,title
  ) values (
    selected_position_id,selected_owner_internal_user_id,client_request_id,
    'manual',draft.title
  ) returning * into selected;
  update platform_hr.position_drafts set
    state='confirmed',resolved_position_id=selected.position_id,
    row_version=row_version+1,updated_at=now()
  where draft_id=selected_draft_id;
  if draft.source_conversation_id is not null then
    insert into platform_hr.position_conversations(
      conversation_id,owner_internal_user_id,position_id,
      client_request_id,binding_kind
    ) values (
      draft.source_conversation_id,selected_owner_internal_user_id,
      selected.position_id,client_request_id,'draft_confirmed'
    );
  end if;
  return selected;
end
$function$;

create function platform_hr.propose_position_draft_v65(
  selected_draft_id uuid,
  selected_owner_internal_user_id uuid,
  client_request_id uuid,
  selected_source_kind text,
  selected_source_key text,
  selected_source_conversation_id uuid,
  selected_title text,
  selected_proposal jsonb,
  selected_evidence jsonb,
  selected_discovery_rule_version text
) returns platform_hr.position_drafts
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.position_drafts%rowtype;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  select * into selected from platform_hr.position_drafts
  where owner_internal_user_id=selected_owner_internal_user_id
    and (position_drafts.client_request_id=propose_position_draft_v65.client_request_id
      or (source_kind=selected_source_kind and source_key=selected_source_key));
  if found then return selected; end if;
  insert into platform_hr.position_drafts(
    draft_id,owner_internal_user_id,client_request_id,source_kind,source_key,
    source_conversation_id,title,proposal,evidence,discovery_rule_version
  ) values (
    selected_draft_id,selected_owner_internal_user_id,client_request_id,
    selected_source_kind,selected_source_key,selected_source_conversation_id,
    btrim(selected_title),selected_proposal,selected_evidence,
    selected_discovery_rule_version
  ) returning * into selected;
  return selected;
end
$function$;

create function platform_hr.merge_position_draft_v65(
  selected_draft_id uuid,
  selected_owner_internal_user_id uuid,
  selected_target_position_id uuid,
  client_request_id uuid,
  expected_row_version bigint
) returns platform_hr.position_drafts
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.position_drafts%rowtype;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  select * into selected from platform_hr.position_drafts
  where draft_id=selected_draft_id
    and owner_internal_user_id=selected_owner_internal_user_id for update;
  if not found then raise no_data_found; end if;
  if selected.state='merged'
     and selected.resolved_position_id=selected_target_position_id then
    return selected;
  end if;
  if selected.state<>'proposed' or selected.row_version<>expected_row_version then
    raise serialization_failure;
  end if;
  perform 1 from platform_hr.positions
  where position_id=selected_target_position_id
    and owner_internal_user_id=selected_owner_internal_user_id;
  if not found then raise no_data_found; end if;
  if selected.source_conversation_id is not null then
    insert into platform_hr.position_conversations(
      conversation_id,owner_internal_user_id,position_id,
      client_request_id,binding_kind
    ) values (
      selected.source_conversation_id,selected_owner_internal_user_id,
      selected_target_position_id,client_request_id,'draft_merged'
    );
  end if;
  update platform_hr.position_drafts set
    state='merged',resolved_position_id=selected_target_position_id,
    row_version=row_version+1,updated_at=now()
  where draft_id=selected_draft_id returning * into selected;
  return selected;
end
$function$;

create function platform_hr.dismiss_position_draft_v65(
  selected_draft_id uuid,
  selected_owner_internal_user_id uuid,
  client_request_id uuid,
  expected_row_version bigint
) returns platform_hr.position_drafts
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.position_drafts%rowtype;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  select * into selected from platform_hr.position_drafts
  where draft_id=selected_draft_id
    and owner_internal_user_id=selected_owner_internal_user_id for update;
  if not found then raise no_data_found; end if;
  if selected.state='dismissed' then return selected; end if;
  if selected.state<>'proposed' or selected.row_version<>expected_row_version then
    raise serialization_failure;
  end if;
  update platform_hr.position_drafts set
    state='dismissed',row_version=row_version+1,updated_at=now()
  where draft_id=selected_draft_id returning * into selected;
  return selected;
end
$function$;

create function platform_hr.bind_conversation_v65(
  selected_owner_internal_user_id uuid,
  selected_position_id uuid,
  selected_conversation_id uuid,
  client_request_id uuid,
  selected_binding_kind text
) returns platform_hr.position_conversations
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.position_conversations%rowtype;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  select * into selected from platform_hr.position_conversations
  where conversation_id=selected_conversation_id;
  if found then
    if selected.owner_internal_user_id<>selected_owner_internal_user_id
       or selected.position_id<>selected_position_id then
      raise unique_violation;
    end if;
    return selected;
  end if;
  perform 1 from platform_control.conversations
  where conversation_id=selected_conversation_id
    and owner_internal_user_id=selected_owner_internal_user_id
    and direct_agent_id='hr-bot';
  if not found then raise no_data_found; end if;
  insert into platform_hr.position_conversations(
    conversation_id,owner_internal_user_id,position_id,
    client_request_id,binding_kind
  ) values (
    selected_conversation_id,selected_owner_internal_user_id,
    selected_position_id,client_request_id,selected_binding_kind
  ) returning * into selected;
  return selected;
end
$function$;

create function platform_hr.attach_conversation_to_draft_v65(
  selected_owner_internal_user_id uuid,
  selected_draft_id uuid,
  selected_conversation_id uuid,
  client_request_id uuid
) returns platform_hr.position_drafts
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.position_drafts%rowtype;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  select * into selected from platform_hr.position_drafts
  where draft_id=selected_draft_id
    and owner_internal_user_id=selected_owner_internal_user_id for update;
  if not found then raise no_data_found; end if;
  if selected.source_conversation_id=selected_conversation_id then
    return selected;
  end if;
  if selected.state<>'proposed' or selected.source_conversation_id is not null then
    raise serialization_failure;
  end if;
  perform 1 from platform_control.conversations
  where conversation_id=selected_conversation_id
    and owner_internal_user_id=selected_owner_internal_user_id
    and direct_agent_id='hr-bot';
  if not found then raise no_data_found; end if;
  update platform_hr.position_drafts set
    source_conversation_id=selected_conversation_id,
    row_version=row_version+1,updated_at=now()
  where draft_id=selected_draft_id returning * into selected;
  return selected;
end
$function$;

create function platform_hr.correct_conversation_binding_v65(
  selected_owner_internal_user_id uuid,
  selected_conversation_id uuid,
  selected_previous_position_id uuid,
  selected_new_position_id uuid,
  client_request_id uuid,
  selected_reason text
) returns platform_hr.position_conversations
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.position_conversations%rowtype;
declare prior_event platform_hr.position_binding_events%rowtype;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  select * into prior_event from platform_hr.position_binding_events
  where event_id=client_request_id
    and owner_internal_user_id=selected_owner_internal_user_id;
  if found then
    if prior_event.conversation_id<>selected_conversation_id
       or prior_event.previous_position_id<>selected_previous_position_id
       or prior_event.new_position_id<>selected_new_position_id then
      raise unique_violation;
    end if;
    select * into selected from platform_hr.position_conversations
    where conversation_id=selected_conversation_id;
    if selected.position_id<>selected_new_position_id then
      raise serialization_failure;
    end if;
    return selected;
  end if;
  select * into selected from platform_hr.position_conversations
  where conversation_id=selected_conversation_id
    and owner_internal_user_id=selected_owner_internal_user_id for update;
  if not found then raise no_data_found; end if;
  if selected.position_id<>selected_previous_position_id then
    raise serialization_failure;
  end if;
  perform 1 from platform_hr.positions
  where position_id=selected_new_position_id
    and owner_internal_user_id=selected_owner_internal_user_id;
  if not found then raise no_data_found; end if;
  insert into platform_hr.position_binding_events(
    event_id,owner_internal_user_id,conversation_id,
    previous_position_id,new_position_id,reason
  ) values (
    client_request_id,selected_owner_internal_user_id,
    selected_conversation_id,selected_previous_position_id,
    selected_new_position_id,btrim(selected_reason)
  );
  update platform_hr.position_conversations set
    position_id=selected_new_position_id,
    client_request_id=correct_conversation_binding_v65.client_request_id,
    binding_kind='manual_correction',
    previous_position_id=selected_previous_position_id
  where conversation_id=selected_conversation_id returning * into selected;
  return selected;
end
$function$;

create function platform_hr.promote_material_v65(
  selected_owner_internal_user_id uuid,
  selected_position_id uuid,
  selected_attachment_id uuid,
  client_request_id uuid
) returns platform_hr.position_materials
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.position_materials%rowtype;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  perform 1 from platform_attachments.attachments
  where attachment_id=selected_attachment_id
    and owner_internal_user_id=selected_owner_internal_user_id
    and state='ready' and deleted_at is null and retained_until>now();
  if not found then raise no_data_found; end if;
  insert into platform_hr.position_materials(
    position_id,attachment_id,owner_internal_user_id,client_request_id,active
  ) values (
    selected_position_id,selected_attachment_id,
    selected_owner_internal_user_id,client_request_id,true
  ) on conflict (position_id,attachment_id) do update set
    active=true,updated_at=now()
  returning * into selected;
  return selected;
end
$function$;

create function platform_hr.remove_material_v65(
  selected_owner_internal_user_id uuid,
  selected_position_id uuid,
  selected_attachment_id uuid,
  client_request_id uuid
) returns platform_hr.position_materials
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.position_materials%rowtype;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  select * into selected from platform_hr.position_materials
  where owner_internal_user_id=selected_owner_internal_user_id
    and position_id=selected_position_id
    and attachment_id=selected_attachment_id for update;
  if not found then raise no_data_found; end if;
  if not selected.active then return selected; end if;
  update platform_hr.position_materials set
    active=false,updated_at=now()
  where position_id=selected_position_id
    and attachment_id=selected_attachment_id returning * into selected;
  return selected;
end
$function$;

create function platform_hr.link_artifact_v65(
  selected_owner_internal_user_id uuid,
  selected_position_id uuid,
  selected_artifact_id uuid,
  client_request_id uuid
) returns platform_hr.position_artifacts
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.position_artifacts%rowtype;
begin
  if session_user not in (
    'platform_control_app','platform_control_app_preview',
    'platform_brain_worker','platform_brain_worker_preview'
  ) then raise insufficient_privilege; end if;
  insert into platform_hr.position_artifacts(
    position_id,artifact_id,owner_internal_user_id,client_request_id
  ) select selected_position_id,selected_artifact_id,
    selected_owner_internal_user_id,client_request_id
  from platform_attachments.artifacts artifact
  where artifact.artifact_id=selected_artifact_id
    and artifact.owner_internal_user_id=selected_owner_internal_user_id
  on conflict (position_id,artifact_id) do update set
    client_request_id=position_artifacts.client_request_id
  returning * into selected;
  if not found then raise no_data_found; end if;
  return selected;
end
$function$;

revoke all on all tables in schema platform_hr from public;
revoke all on all functions in schema platform_hr from public;
revoke all on function platform_hr.create_position_v65(
  uuid,uuid,uuid,text,text,text,text,jsonb,text,text
) from public;
revoke all on function platform_hr.project_official_position_v65(
  uuid,uuid,uuid,text,text,text,jsonb,text,text,text
) from public;
revoke all on function platform_hr.confirm_position_draft_v65(
  uuid,uuid,uuid,uuid,bigint
) from public;
revoke all on function platform_hr.propose_position_draft_v65(
  uuid,uuid,uuid,text,text,uuid,text,jsonb,jsonb,text
) from public;
revoke all on function platform_hr.merge_position_draft_v65(
  uuid,uuid,uuid,uuid,bigint
) from public;
revoke all on function platform_hr.dismiss_position_draft_v65(
  uuid,uuid,uuid,bigint
) from public;
revoke all on function platform_hr.bind_conversation_v65(
  uuid,uuid,uuid,uuid,text
) from public;
revoke all on function platform_hr.attach_conversation_to_draft_v65(
  uuid,uuid,uuid,uuid
) from public;
revoke all on function platform_hr.correct_conversation_binding_v65(
  uuid,uuid,uuid,uuid,uuid,text
) from public;
revoke all on function platform_hr.promote_material_v65(
  uuid,uuid,uuid,uuid
) from public;
revoke all on function platform_hr.remove_material_v65(
  uuid,uuid,uuid,uuid
) from public;
revoke all on function platform_hr.link_artifact_v65(
  uuid,uuid,uuid,uuid
) from public;

do $migration$
declare selected_app name;
declare selected_brain name;
begin
  if current_database()='agent_platform_control'
     and current_user='platform_control_owner' then
    selected_app := 'platform_control_app';
    selected_brain := 'platform_brain_worker';
  elsif current_database()='agent_platform_control_preview'
        and current_user='platform_control_owner_preview' then
    selected_app := 'platform_control_app_preview';
    selected_brain := 'platform_brain_worker_preview';
  else
    raise insufficient_privilege using
      message='HR position migration owner/environment mismatch';
  end if;
  execute format('grant usage on schema platform_hr to %I,%I',selected_app,selected_brain);
  execute format('grant select on all tables in schema platform_hr to %I',selected_app);
  execute format(
    'grant execute on function platform_hr.create_position_v65('
    'uuid,uuid,uuid,text,text,text,text,jsonb,text,text) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.project_official_position_v65('
    'uuid,uuid,uuid,text,text,text,jsonb,text,text,text) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.confirm_position_draft_v65('
    'uuid,uuid,uuid,uuid,bigint) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.propose_position_draft_v65('
    'uuid,uuid,uuid,text,text,uuid,text,jsonb,jsonb,text) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.merge_position_draft_v65('
    'uuid,uuid,uuid,uuid,bigint) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.dismiss_position_draft_v65('
    'uuid,uuid,uuid,bigint) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.bind_conversation_v65('
    'uuid,uuid,uuid,uuid,text) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.attach_conversation_to_draft_v65('
    'uuid,uuid,uuid,uuid) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.correct_conversation_binding_v65('
    'uuid,uuid,uuid,uuid,uuid,text) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.promote_material_v65('
    'uuid,uuid,uuid,uuid) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.remove_material_v65('
    'uuid,uuid,uuid,uuid) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.link_artifact_v65('
    'uuid,uuid,uuid,uuid) to %I',selected_app
  );
  execute format(
    'grant select on platform_hr.position_conversations to %I',selected_brain
  );
  execute format(
    'grant execute on function platform_hr.link_artifact_v65('
    'uuid,uuid,uuid,uuid) to %I',selected_brain
  );
end
$migration$;
