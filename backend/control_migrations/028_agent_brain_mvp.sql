create table platform_control.agent_use_grants (
  agent_use_grant_id uuid primary key,
  agent_id text not null
    check (agent_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'),
  target_kind text not null
    check (target_kind in ('user', 'department', 'all_members')),
  target_internal_user_id uuid
    references platform_control.internal_users(internal_user_id),
  target_department_key uuid,
  include_descendants boolean not null default false,
  created_by uuid not null
    references platform_control.internal_users(internal_user_id),
  created_at timestamptz not null default now(),
  created_audit_event_id uuid
    references platform_control.audit_events(audit_event_id),
  revoked_at timestamptz,
  revoked_by uuid
    references platform_control.internal_users(internal_user_id),
  revoked_audit_event_id uuid
    references platform_control.audit_events(audit_event_id),
  check (
    (
      target_kind = 'user'
      and target_internal_user_id is not null
      and target_department_key is null
      and not include_descendants
    ) or (
      target_kind = 'department'
      and target_internal_user_id is null
      and target_department_key is not null
      and include_descendants
    ) or (
      target_kind = 'all_members'
      and target_internal_user_id is null
      and target_department_key is null
      and not include_descendants
    )
  ),
  check (
    (revoked_at is null and revoked_by is null and revoked_audit_event_id is null)
    or (revoked_at is not null and revoked_by is not null)
  )
);

create unique index active_agent_use_user_grants_unique
  on platform_control.agent_use_grants (agent_id, target_internal_user_id)
  where revoked_at is null and target_kind = 'user';
create unique index active_agent_use_department_grants_unique
  on platform_control.agent_use_grants (agent_id, target_department_key)
  where revoked_at is null and target_kind = 'department';
create unique index active_agent_use_all_member_grants_unique
  on platform_control.agent_use_grants (agent_id)
  where revoked_at is null and target_kind = 'all_members';

create table platform_control.missions (
  mission_id uuid primary key,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  client_request_id uuid not null,
  mode text not null check (mode in ('brain', 'direct_agent')),
  direct_agent_id text
    check (
      direct_agent_id is null
      or direct_agent_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
    ),
  status text not null check (status in (
    'planning', 'delegated', 'synthesizing', 'completed',
    'partially_completed', 'failed', 'cancelled', 'interrupted'
  )),
  cancel_requested boolean not null default false,
  row_version bigint not null default 0 check (row_version >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  terminal_at timestamptz,
  unique (owner_internal_user_id, client_request_id),
  check (
    (mode = 'brain' and direct_agent_id is null)
    or (mode = 'direct_agent' and direct_agent_id is not null)
  ),
  check (
    (status in (
      'completed', 'partially_completed', 'failed', 'cancelled', 'interrupted'
    )) = (terminal_at is not null)
  )
);

comment on table platform_control.missions is
  'Access is limited to the trusted backend app role. Task 4 must enforce '
  'owner-scoped repository reads and decryption using the separate keyring; '
  'Task 6 must enforce authenticated owner-scoped routes. PostgreSQL ciphertext '
  'alone is not user-readable without that keyring.';

create table platform_control.mission_messages (
  message_id uuid primary key,
  mission_id uuid not null references platform_control.missions(mission_id),
  seq integer not null check (seq > 0),
  role text not null check (role in ('user', 'brain', 'agent')),
  content_ciphertext bytea not null
    check (octet_length(content_ciphertext) between 29 and 1048576),
  encryption_key_version integer not null
    check (encryption_key_version > 0),
  created_at timestamptz not null default now(),
  unique (mission_id, seq),
  unique (mission_id, message_id)
);

create table platform_control.mission_tasks (
  task_id uuid primary key,
  mission_id uuid not null references platform_control.missions(mission_id),
  task_index integer not null default 1 check (task_index > 0),
  agent_id text not null
    check (agent_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'),
  objective_ciphertext bytea not null
    check (octet_length(objective_ciphertext) between 29 and 1048576),
  encryption_key_version integer not null
    check (encryption_key_version > 0),
  status text not null check (status in (
    'queued', 'running', 'completed', 'failed', 'cancelled', 'interrupted'
  )),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  started_at timestamptz,
  terminal_at timestamptz,
  unique (mission_id, task_id),
  unique (mission_id, task_index),
  check ((status = 'running') = (started_at is not null and terminal_at is null)
    or status <> 'running'),
  check (
    (status in ('completed', 'failed', 'cancelled', 'interrupted'))
      = (terminal_at is not null)
  )
);

create unique index one_mission_child_task
  on platform_control.mission_tasks (mission_id);

create table platform_control.mission_runs (
  run_id uuid primary key,
  mission_id uuid not null references platform_control.missions(mission_id),
  task_id uuid,
  phase text not null check (phase in (
    'planning', 'professional', 'synthesis', 'direct'
  )),
  agent_id text not null
    check (agent_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'),
  status text not null check (status in (
    'queued', 'running', 'completed', 'failed', 'cancelled', 'interrupted'
  )),
  input_ciphertext bytea not null
    check (octet_length(input_ciphertext) between 29 and 1048576),
  encryption_key_version integer not null
    check (encryption_key_version > 0),
  output_ciphertext bytea,
  output_encryption_key_version integer,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  started_at timestamptz,
  terminal_at timestamptz,
  unique (mission_id, run_id),
  foreign key (mission_id, task_id)
    references platform_control.mission_tasks(mission_id, task_id),
  check (
    (phase = 'professional' and task_id is not null)
    or (phase <> 'professional' and task_id is null)
  ),
  check (
    (output_ciphertext is null and output_encryption_key_version is null)
    or (
      octet_length(output_ciphertext) between 29 and 1048576
      and output_encryption_key_version > 0
    )
  ),
  check ((status = 'running') = (started_at is not null and terminal_at is null)
    or status <> 'running'),
  check (
    (status in ('completed', 'failed', 'cancelled', 'interrupted'))
      = (terminal_at is not null)
  )
);

create table platform_control.mission_events (
  event_id uuid primary key,
  mission_id uuid not null references platform_control.missions(mission_id),
  run_id uuid,
  seq integer not null check (seq > 0),
  event_type text not null check (event_type in (
    'mission.started', 'brain.responding', 'plan.created',
    'task.dispatched', 'agent.accepted', 'agent.progress', 'agent.result',
    'task.reviewed', 'synthesis.started', 'mission.completed',
    'mission.failed', 'mission.cancelled', 'mission.interrupted'
  )),
  payload_ciphertext bytea not null
    check (octet_length(payload_ciphertext) between 29 and 1048576),
  encryption_key_version integer not null
    check (encryption_key_version > 0),
  created_at timestamptz not null default now(),
  unique (mission_id, seq),
  foreign key (mission_id, run_id)
    references platform_control.mission_runs(mission_id, run_id)
);

create index missions_owner_created
  on platform_control.missions (owner_internal_user_id, created_at desc, mission_id);
create index missions_status_updated
  on platform_control.missions (status, updated_at, mission_id);
create index mission_runs_mission_phase
  on platform_control.mission_runs (mission_id, phase, created_at);

create function platform_control.has_agent_use_scope_v28(
  selected_user_id uuid,
  selected_agent_id text
) returns boolean
language sql
stable
security definer
set search_path = pg_catalog, platform_control
as $function$
  with active_member as (
    select state.active_generation_id, member.member_key
    from platform_control.internal_users users
    join platform_control.directory_state state on state.singleton
    join platform_control.directory_generations generation
      on generation.generation_id = state.active_generation_id
     and generation.status = 'complete'
    join platform_control.directory_members member
      on member.generation_id = state.active_generation_id
     and member.internal_user_id = users.internal_user_id
     and member.status = 'active'
    where users.internal_user_id = selected_user_id
      and users.status = 'active'
      and users.locally_invalidated_at is null
      and selected_agent_id in (
        'hr-bot',
        'fae-bot',
        'marketing-prospecting-bot',
        'marketing-inbound-bot',
        'marketing-voice-bot',
        'marketing-intelligence-bot',
        'marketing-gtm-bot'
      )
  )
  select exists (
    select 1
    from active_member member
    join platform_control.agent_use_grants grant_row
      on grant_row.agent_id = selected_agent_id
     and grant_row.revoked_at is null
    where (
      grant_row.target_kind = 'all_members'
      or (
        grant_row.target_kind = 'user'
        and grant_row.target_internal_user_id = selected_user_id
      )
      or (
        grant_row.target_kind = 'department'
        and grant_row.include_descendants
        and exists (
          select 1
          from platform_control.member_departments membership
          join platform_control.department_closure closure
            on closure.generation_id = membership.generation_id
           and closure.descendant_department_key = membership.department_key
           and closure.ancestor_department_key = grant_row.target_department_key
          where membership.generation_id = member.active_generation_id
            and membership.member_key = member.member_key
        )
      )
    )
  )
$function$;

create function platform_control.grant_agent_use_scope_v28(
  selected_grant_id uuid,
  selected_agent_id text,
  selected_target_kind text,
  selected_target_user_id uuid,
  selected_target_department_key uuid,
  selected_include_descendants boolean,
  selected_actor_id uuid,
  selected_change_reference text,
  selected_request_id uuid
) returns uuid
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  expected_details jsonb;
  existing_audit platform_control.audit_events%rowtype;
begin
  if selected_grant_id is null
     or selected_actor_id is null
     or selected_request_id is null
     or substring(selected_request_id::text from 15 for 1) <> '4'
     or substring(selected_request_id::text from 20 for 1) !~ '^[89ab]$'
     or selected_agent_id !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
     or selected_target_kind not in ('user', 'department', 'all_members')
     or selected_include_descendants is null
     or selected_change_reference is null
     or selected_change_reference !~ '^[A-Z][A-Z0-9_-]{7,63}$'
     or not (
       (
         selected_target_kind = 'user'
         and selected_target_user_id is not null
         and selected_target_department_key is null
         and not selected_include_descendants
       ) or (
         selected_target_kind = 'department'
         and selected_target_user_id is null
         and selected_target_department_key is not null
         and selected_include_descendants
       ) or (
         selected_target_kind = 'all_members'
         and selected_target_user_id is null
         and selected_target_department_key is null
         and not selected_include_descendants
       )
     )
  then
    raise check_violation using message = 'agent use grant input invalid';
  end if;

  expected_details := jsonb_build_object(
    'agent_id', selected_agent_id,
    'grant_id', selected_grant_id,
    'include_descendants', selected_include_descendants,
    'reference', selected_change_reference,
    'target_department_key', selected_target_department_key,
    'target_internal_user_id', selected_target_user_id,
    'target_kind', selected_target_kind
  );
  perform pg_advisory_xact_lock(
    hashtextextended('agent-use-request:' || selected_request_id::text, 0)
  );
  select * into existing_audit
  from platform_control.audit_events
  where audit_event_id = selected_request_id
  for update;
  if found then
    if existing_audit.actor_internal_user_id is distinct from selected_actor_id
       or existing_audit.event_type <> 'agent_use_scope_granted'
       or existing_audit.target_type <> 'agent_use_scope'
       or existing_audit.target_internal_id <> selected_grant_id::text
       or existing_audit.request_id <> selected_request_id
       or existing_audit.result <> 'completed'
       or existing_audit.reason_code <> 'offline_maintenance'
       or existing_audit.sanitized_before_after is distinct from expected_details
    then
      raise check_violation using message = 'agent use grant request collision';
    end if;
    return selected_grant_id;
  end if;

  perform platform_control.require_platform_owner(selected_actor_id);
  perform pg_advisory_xact_lock(
    hashtextextended('agent-use-grant:' || selected_grant_id::text, 0)
  );
  insert into platform_control.audit_events (
    audit_event_id, actor_internal_user_id, event_type, target_type,
    target_internal_id, request_id, result, reason_code,
    sanitized_before_after
  ) values (
    selected_request_id, selected_actor_id, 'agent_use_scope_granted',
    'agent_use_scope', selected_grant_id::text, selected_request_id,
    'completed', 'offline_maintenance', expected_details
  );
  insert into platform_control.agent_use_grants (
    agent_use_grant_id, agent_id, target_kind, target_internal_user_id,
    target_department_key, include_descendants, created_by,
    created_audit_event_id
  ) values (
    selected_grant_id, selected_agent_id, selected_target_kind,
    selected_target_user_id, selected_target_department_key,
    selected_include_descendants, selected_actor_id, selected_request_id
  );
  return selected_grant_id;
end
$function$;

create function platform_control.revoke_agent_use_scope_v28(
  selected_grant_id uuid,
  selected_actor_id uuid,
  selected_change_reference text,
  selected_request_id uuid
) returns uuid
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  selected_grant platform_control.agent_use_grants%rowtype;
  expected_details jsonb;
  existing_audit platform_control.audit_events%rowtype;
begin
  if selected_grant_id is null
     or selected_actor_id is null
     or selected_request_id is null
     or substring(selected_request_id::text from 15 for 1) <> '4'
     or substring(selected_request_id::text from 20 for 1) !~ '^[89ab]$'
     or selected_change_reference is null
     or selected_change_reference !~ '^[A-Z][A-Z0-9_-]{7,63}$'
  then
    raise check_violation using message = 'agent use revocation input invalid';
  end if;

  perform pg_advisory_xact_lock(
    hashtextextended('agent-use-request:' || selected_request_id::text, 0)
  );
  select * into existing_audit
  from platform_control.audit_events
  where audit_event_id = selected_request_id
  for update;
  if found then
    if existing_audit.actor_internal_user_id is distinct from selected_actor_id
       or existing_audit.event_type <> 'agent_use_scope_revoked'
       or existing_audit.target_type <> 'agent_use_scope'
       or existing_audit.target_internal_id <> selected_grant_id::text
       or existing_audit.request_id <> selected_request_id
       or existing_audit.result <> 'completed'
       or existing_audit.reason_code <> 'offline_maintenance'
       or existing_audit.sanitized_before_after->>'grant_id'
            <> selected_grant_id::text
       or existing_audit.sanitized_before_after->>'reference'
            <> selected_change_reference
    then
      raise check_violation using message = 'agent use revocation request collision';
    end if;
    return selected_grant_id;
  end if;

  perform platform_control.require_platform_owner(selected_actor_id);
  perform pg_advisory_xact_lock(
    hashtextextended('agent-use-grant:' || selected_grant_id::text, 0)
  );
  select * into selected_grant
  from platform_control.agent_use_grants
  where agent_use_grant_id = selected_grant_id
  for update;
  if not found or selected_grant.revoked_at is not null then
    raise check_violation using message = 'active agent use grant required';
  end if;

  expected_details := jsonb_build_object(
    'agent_id', selected_grant.agent_id,
    'grant_id', selected_grant.agent_use_grant_id,
    'include_descendants', selected_grant.include_descendants,
    'reference', selected_change_reference,
    'target_department_key', selected_grant.target_department_key,
    'target_internal_user_id', selected_grant.target_internal_user_id,
    'target_kind', selected_grant.target_kind
  );
  insert into platform_control.audit_events (
    audit_event_id, actor_internal_user_id, event_type, target_type,
    target_internal_id, request_id, result, reason_code,
    sanitized_before_after
  ) values (
    selected_request_id, selected_actor_id, 'agent_use_scope_revoked',
    'agent_use_scope', selected_grant_id::text, selected_request_id,
    'completed', 'offline_maintenance', expected_details
  );
  update platform_control.agent_use_grants
  set revoked_at = clock_timestamp(),
      revoked_by = selected_actor_id,
      revoked_audit_event_id = selected_request_id
  where agent_use_grant_id = selected_grant_id;
  return selected_grant_id;
end
$function$;

revoke all on
  platform_control.agent_use_grants,
  platform_control.missions,
  platform_control.mission_messages,
  platform_control.mission_tasks,
  platform_control.mission_runs,
  platform_control.mission_events
from public;
revoke all on function platform_control.has_agent_use_scope_v28(uuid, text)
from public;
revoke all on function platform_control.grant_agent_use_scope_v28(
  uuid, text, text, uuid, uuid, boolean, uuid, text, uuid
) from public;
revoke all on function platform_control.revoke_agent_use_scope_v28(
  uuid, uuid, text, uuid
) from public;

do $migration$
declare
  selected_suffix text;
  selected_app text;
  selected_maintenance text;
  role_name text;
begin
  case current_user
    when 'platform_control_owner' then selected_suffix := '';
    when 'platform_control_owner_preview' then selected_suffix := '_preview';
    else
      raise insufficient_privilege using
        message = 'control migration must run as an approved owner role';
  end case;
  selected_app := 'platform_control_app' || selected_suffix;
  selected_maintenance := 'platform_control_maintenance' || selected_suffix;

  foreach role_name in array array[
    'platform_control_migrator',
    'platform_control_app',
    'platform_directory_worker',
    'platform_stream_ingest',
    'platform_audit_append',
    'platform_control_maintenance',
    'platform_control_migrator_preview',
    'platform_control_app_preview',
    'platform_directory_worker_preview',
    'platform_stream_ingest_preview',
    'platform_audit_append_preview',
    'platform_control_maintenance_preview'
  ] loop
    execute format(
      'revoke all on platform_control.agent_use_grants, '
      'platform_control.missions, platform_control.mission_messages, '
      'platform_control.mission_tasks, platform_control.mission_runs, '
      'platform_control.mission_events from %I', role_name
    );
    execute format(
      'revoke all on function '
      'platform_control.has_agent_use_scope_v28(uuid,text) from %I', role_name
    );
    execute format(
      'revoke all on function '
      'platform_control.grant_agent_use_scope_v28('
      'uuid,text,text,uuid,uuid,boolean,uuid,text,uuid) from %I', role_name
    );
    execute format(
      'revoke all on function '
      'platform_control.revoke_agent_use_scope_v28('
      'uuid,uuid,text,uuid) from %I', role_name
    );
  end loop;

  execute format(
    'grant select,insert on platform_control.missions to %I',
    selected_app
  );
  execute format(
    'grant update (status,cancel_requested,row_version,updated_at,terminal_at) '
    'on platform_control.missions to %I', selected_app
  );
  execute format(
    'grant select,insert on platform_control.mission_messages to %I',
    selected_app
  );
  execute format(
    'grant select,insert on platform_control.mission_tasks, '
    'platform_control.mission_runs to %I', selected_app
  );
  execute format(
    'grant update (status,updated_at,started_at,terminal_at) '
    'on platform_control.mission_tasks to %I', selected_app
  );
  execute format(
    'grant update (status,output_ciphertext,output_encryption_key_version,'
    'updated_at,started_at,terminal_at) on platform_control.mission_runs to %I',
    selected_app
  );
  execute format(
    'grant select,insert on platform_control.mission_events to %I',
    selected_app
  );
  execute format(
    'grant execute on function '
    'platform_control.has_agent_use_scope_v28(uuid,text) to %I', selected_app
  );
  execute format(
    'grant execute on function '
    'platform_control.grant_agent_use_scope_v28('
    'uuid,text,text,uuid,uuid,boolean,uuid,text,uuid) to %I',
    selected_maintenance
  );
  execute format(
    'grant execute on function '
    'platform_control.revoke_agent_use_scope_v28('
    'uuid,uuid,text,uuid) to %I', selected_maintenance
  );
end
$migration$;
