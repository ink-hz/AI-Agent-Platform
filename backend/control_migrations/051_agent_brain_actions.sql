create table platform_brain.agent_task_actions (
  action_id uuid primary key,
  task_id uuid not null references platform_brain.agent_tasks(task_id),
  action_seq integer not null check (action_seq > 0),
  action_kind text not null
    check (action_kind ~ '^[a-z][a-z0-9_.-]{0,127}$'),
  summary_ciphertext bytea not null
    check (octet_length(summary_ciphertext) between 29 and 1048576),
  summary_key_version integer not null check (summary_key_version > 0),
  summary_sha256 bytea not null check (octet_length(summary_sha256) = 32),
  impact_ciphertext bytea not null
    check (octet_length(impact_ciphertext) between 29 and 1048576),
  impact_key_version integer not null check (impact_key_version > 0),
  impact_sha256 bytea not null check (octet_length(impact_sha256) = 32),
  parameters_ciphertext bytea not null
    check (octet_length(parameters_ciphertext) between 29 and 1048576),
  parameters_key_version integer not null check (parameters_key_version > 0),
  parameters_sha256 bytea not null check (octet_length(parameters_sha256) = 32),
  action_digest bytea not null check (octet_length(action_digest) = 32),
  status text not null check (
    status in ('pending','confirmed','rejected','expired','superseded')
  ),
  expires_at timestamptz not null,
  confirmed_by_internal_user_id uuid
    references platform_control.internal_users(internal_user_id),
  confirmed_at timestamptz,
  execution_timeout_seconds integer not null
    check (execution_timeout_seconds between 1 and 900),
  execution_status text not null check (
    execution_status in ('not_started','queued','running','completed','failed')
  ),
  execution_deadline_at timestamptz,
  execution_result_ciphertext bytea,
  execution_result_key_version integer,
  execution_result_sha256 bytea,
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  terminal_at timestamptz,
  unique (task_id, action_seq),
  check (expires_at > created_at),
  check (
    (status='pending' and terminal_at is null
      and confirmed_by_internal_user_id is null and confirmed_at is null
      and execution_status='not_started' and execution_deadline_at is null)
    or (status='confirmed' and terminal_at is not null
      and confirmed_by_internal_user_id is not null and confirmed_at is not null
      and execution_status in ('queued','running','completed','failed')
      and execution_deadline_at is not null)
    or (status in ('rejected','expired','superseded') and terminal_at is not null
      and confirmed_by_internal_user_id is null and confirmed_at is null
      and execution_status='not_started' and execution_deadline_at is null)
  ),
  check (
    (execution_result_ciphertext is null
      and execution_result_key_version is null
      and execution_result_sha256 is null
      and execution_status in ('not_started','queued','running'))
    or (octet_length(execution_result_ciphertext) between 29 and 1048576
      and execution_result_key_version > 0
      and octet_length(execution_result_sha256) = 32
      and execution_status in ('completed','failed'))
  )
);

create index agent_task_actions_digest
  on platform_brain.agent_task_actions(task_id,action_digest);
create index agent_task_actions_pending_expiry
  on platform_brain.agent_task_actions(expires_at,action_id)
  where status='pending';

create table platform_brain.agent_action_deliveries (
  delivery_id uuid primary key,
  action_id uuid not null unique
    references platform_brain.agent_task_actions(action_id),
  status text not null check (
    status in ('queued','leased','dispatched','completed','failed')
  ),
  attempt integer not null default 1 check (attempt > 0),
  idempotency_key text not null unique
    check (idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{15,159}$'),
  lease_worker_id text,
  lease_expires_at timestamptz,
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  terminal_at timestamptz,
  check ((lease_worker_id is null) = (lease_expires_at is null)),
  check ((status='leased') = (lease_worker_id is not null)),
  check ((status in ('completed','failed')) = (terminal_at is not null))
);

create function platform_brain.propose_agent_task_action_v51(
  selected_action_id uuid,
  selected_task_id uuid,
  selected_action_seq integer,
  selected_action_kind text,
  selected_summary_ciphertext bytea,
  selected_summary_key_version integer,
  selected_summary_sha256 bytea,
  selected_impact_ciphertext bytea,
  selected_impact_key_version integer,
  selected_impact_sha256 bytea,
  selected_parameters_ciphertext bytea,
  selected_parameters_key_version integer,
  selected_parameters_sha256 bytea,
  selected_action_digest bytea,
  selected_expires_at timestamptz,
  selected_execution_timeout_seconds integer
) returns boolean
language plpgsql
security definer
set search_path = pg_catalog, platform_brain
as $function$
declare
  existing_action platform_brain.agent_task_actions%rowtype;
begin
  if (
       current_database()='agent_platform_control'
       and session_user<>'platform_brain_worker'
     ) or (
       current_database()='agent_platform_control_preview'
       and session_user<>'platform_brain_worker_preview'
     ) or current_database() not in (
       'agent_platform_control','agent_platform_control_preview'
     )
  then
    raise insufficient_privilege using message='Action proposal caller invalid';
  end if;

  select * into existing_action
  from platform_brain.agent_task_actions
  where action_id=selected_action_id
  for update;
  if found then
    if existing_action.task_id=selected_task_id
       and existing_action.action_seq=selected_action_seq
       and existing_action.action_kind=selected_action_kind
       and existing_action.action_digest=selected_action_digest
    then
      return false;
    end if;
    raise check_violation using message='Action proposal conflict';
  end if;

  if not exists (
       select 1 from platform_brain.agent_tasks
       where task_id=selected_task_id
         and status not in ('completed','failed','cancelled','timed_out','unavailable')
     )
     or selected_expires_at<=clock_timestamp()
  then
    raise check_violation using message='Action proposal invalid';
  end if;

  update platform_brain.agent_task_actions set
    status='superseded',terminal_at=clock_timestamp(),updated_at=clock_timestamp()
  where task_id=selected_task_id and status='pending';

  insert into platform_brain.agent_task_actions (
    action_id,task_id,action_seq,action_kind,
    summary_ciphertext,summary_key_version,summary_sha256,
    impact_ciphertext,impact_key_version,impact_sha256,
    parameters_ciphertext,parameters_key_version,parameters_sha256,
    action_digest,status,expires_at,execution_timeout_seconds,execution_status
  ) values (
    selected_action_id,selected_task_id,selected_action_seq,selected_action_kind,
    selected_summary_ciphertext,selected_summary_key_version,selected_summary_sha256,
    selected_impact_ciphertext,selected_impact_key_version,selected_impact_sha256,
    selected_parameters_ciphertext,selected_parameters_key_version,
    selected_parameters_sha256,selected_action_digest,'pending',selected_expires_at,
    selected_execution_timeout_seconds,'not_started'
  );
  return true;
end
$function$;

create function platform_brain.confirm_agent_task_action_v51(
  selected_owner_id uuid,
  selected_action_id uuid,
  selected_action_digest bytea,
  selected_delivery_id uuid,
  selected_idempotency_key text
) returns boolean
language plpgsql
security definer
set search_path = pg_catalog, platform_brain, platform_control
as $function$
declare
  selected_action platform_brain.agent_task_actions%rowtype;
  actual_owner uuid;
  loop_status text;
begin
  if (
       current_database()='agent_platform_control'
       and session_user<>'platform_control_app'
     ) or (
       current_database()='agent_platform_control_preview'
       and session_user<>'platform_control_app_preview'
     ) or current_database() not in (
       'agent_platform_control','agent_platform_control_preview'
     )
  then
    raise insufficient_privilege using message='Action confirmation caller invalid';
  end if;

  select * into selected_action
  from platform_brain.agent_task_actions
  where action_id=selected_action_id
  for update;
  if not found then
    raise no_data_found using message='Action missing';
  end if;
  select conversation.owner_internal_user_id,loop.status
  into actual_owner,loop_status
  from platform_brain.agent_task_actions action
  join platform_brain.agent_tasks task on task.task_id=action.task_id
  join platform_brain.brain_loops loop on loop.loop_id=task.loop_id
  join platform_control.conversations conversation
    on conversation.conversation_id=loop.conversation_id
  where action.action_id=selected_action_id;
  if actual_owner<>selected_owner_id then
    raise insufficient_privilege using message='Action owner invalid';
  end if;
  if selected_action.action_digest<>selected_action_digest then
    raise check_violation using message='Action digest mismatch';
  end if;
  if selected_action.status='confirmed' then
    return true;
  end if;
  if selected_action.status<>'pending'
     or selected_action.expires_at<=clock_timestamp()
     or loop_status in ('completed','failed','cancelled','interrupted')
  then
    raise check_violation using message='Action confirmation invalid';
  end if;

  update platform_brain.agent_task_actions set
    status='confirmed',confirmed_by_internal_user_id=selected_owner_id,
    confirmed_at=clock_timestamp(),terminal_at=clock_timestamp(),
    execution_status='queued',execution_deadline_at=
      clock_timestamp()+(execution_timeout_seconds*interval '1 second'),
    updated_at=clock_timestamp()
  where action_id=selected_action_id;
  insert into platform_brain.agent_action_deliveries (
    delivery_id,action_id,status,idempotency_key
  ) values (
    selected_delivery_id,selected_action_id,'queued',selected_idempotency_key
  );
  return true;
end
$function$;

create function platform_brain.reject_agent_task_action_v51(
  selected_owner_id uuid,
  selected_action_id uuid
) returns boolean
language plpgsql
security definer
set search_path = pg_catalog, platform_brain, platform_control
as $function$
declare
  actual_owner uuid;
  current_status text;
begin
  if (
       current_database()='agent_platform_control'
       and session_user<>'platform_control_app'
     ) or (
       current_database()='agent_platform_control_preview'
       and session_user<>'platform_control_app_preview'
     ) or current_database() not in (
       'agent_platform_control','agent_platform_control_preview'
     )
  then
    raise insufficient_privilege using message='Action rejection caller invalid';
  end if;
  select conversation.owner_internal_user_id,action.status
  into actual_owner,current_status
  from platform_brain.agent_task_actions action
  join platform_brain.agent_tasks task on task.task_id=action.task_id
  join platform_brain.brain_loops loop on loop.loop_id=task.loop_id
  join platform_control.conversations conversation
    on conversation.conversation_id=loop.conversation_id
  where action.action_id=selected_action_id
  for update of action;
  if not found then raise no_data_found using message='Action missing'; end if;
  if actual_owner<>selected_owner_id then
    raise insufficient_privilege using message='Action owner invalid';
  end if;
  if current_status='rejected' then return true; end if;
  if current_status<>'pending' then
    raise check_violation using message='Action rejection invalid';
  end if;
  update platform_brain.agent_task_actions set
    status='rejected',terminal_at=clock_timestamp(),updated_at=clock_timestamp()
  where action_id=selected_action_id;
  return true;
end
$function$;

create function platform_brain.expire_agent_task_actions_v51(
  selected_limit integer
) returns integer
language plpgsql
security definer
set search_path = pg_catalog, platform_brain
as $function$
declare
  changed integer;
  selected_action_id uuid;
begin
  if (
       current_database()='agent_platform_control'
       and session_user<>'platform_brain_worker'
     ) or (
       current_database()='agent_platform_control_preview'
       and session_user<>'platform_brain_worker_preview'
     ) or current_database() not in (
       'agent_platform_control','agent_platform_control_preview'
     )
     or selected_limit not between 1 and 1000
  then
    raise insufficient_privilege using message='Action expiry caller invalid';
  end if;
  changed := 0;
  for selected_action_id in
    select action_id from platform_brain.agent_task_actions
    where status='pending' and expires_at<=clock_timestamp()
    order by expires_at,action_id for update skip locked limit selected_limit
  loop
    update platform_brain.agent_task_actions set
      status='expired',terminal_at=clock_timestamp(),updated_at=clock_timestamp()
    where action_id=selected_action_id and status='pending';
    if found then
      changed := changed + 1;
      perform platform_brain.resume_action_resolution_v51(selected_action_id);
    end if;
  end loop;
  return changed;
end
$function$;

create function platform_brain.supersede_agent_task_action_v51(
  selected_action_id uuid
) returns boolean
language plpgsql
security definer
set search_path = pg_catalog, platform_brain
as $function$
begin
  if (
       current_database()='agent_platform_control'
       and session_user<>'platform_brain_worker'
     ) or (
       current_database()='agent_platform_control_preview'
       and session_user<>'platform_brain_worker_preview'
     ) or current_database() not in (
       'agent_platform_control','agent_platform_control_preview'
     )
  then raise insufficient_privilege using message='Action supersede caller invalid';
  end if;
  update platform_brain.agent_task_actions set
    status='superseded',terminal_at=clock_timestamp(),updated_at=clock_timestamp()
  where action_id=selected_action_id and status='pending';
  return found;
end
$function$;

create function platform_brain.resume_action_resolution_v51(
  selected_action_id uuid
) returns boolean
language plpgsql
security definer
set search_path = pg_catalog, platform_brain
as $function$
declare
  selected_loop platform_brain.brain_loops%rowtype;
begin
  if (
       current_database()='agent_platform_control'
       and session_user not in ('platform_brain_worker','platform_control_app')
     ) or (
       current_database()='agent_platform_control_preview'
       and session_user not in (
         'platform_brain_worker_preview','platform_control_app_preview'
       )
     ) or current_database() not in (
       'agent_platform_control','agent_platform_control_preview'
     )
  then raise insufficient_privilege using message='Action resume caller invalid';
  end if;

  select loop.* into selected_loop
  from platform_brain.agent_task_actions action
  join platform_brain.agent_tasks task on task.task_id=action.task_id
  join platform_brain.brain_loops loop on loop.loop_id=task.loop_id
  where action.action_id=selected_action_id and action.status<>'pending'
  for update of loop;
  if not found or selected_loop.status<>'waiting_confirmation' then
    return false;
  end if;
  if exists (
    select 1 from platform_brain.agent_task_actions action
    join platform_brain.agent_tasks task on task.task_id=action.task_id
    where task.loop_id=selected_loop.loop_id and action.status='pending'
  ) then
    return false;
  end if;

  update platform_brain.brain_loops set
    status='running',active_started_at=clock_timestamp(),
    active_deadline_at=clock_timestamp()+(
      greatest(0,active_budget_ms-active_elapsed_ms)*interval '1 millisecond'
    ),intervention_expires_at=null,updated_at=clock_timestamp(),
    row_version=row_version+1
  where loop_id=selected_loop.loop_id;
  update platform_control.conversation_turns set
    status='running',updated_at=clock_timestamp()
  where turn_id=selected_loop.turn_id;
  return true;
end
$function$;

revoke all on table
  platform_brain.agent_task_actions,
  platform_brain.agent_action_deliveries
from public;
revoke all on function platform_brain.propose_agent_task_action_v51(
  uuid,uuid,integer,text,bytea,integer,bytea,bytea,integer,bytea,
  bytea,integer,bytea,bytea,timestamptz,integer
) from public;
revoke all on function platform_brain.confirm_agent_task_action_v51(
  uuid,uuid,bytea,uuid,text
) from public;
revoke all on function platform_brain.reject_agent_task_action_v51(uuid,uuid)
  from public;
revoke all on function platform_brain.expire_agent_task_actions_v51(integer)
  from public;
revoke all on function platform_brain.supersede_agent_task_action_v51(uuid)
  from public;
revoke all on function platform_brain.resume_action_resolution_v51(uuid)
  from public;

do $migration$
declare
  selected_app name;
  selected_brain name;
  role_name name;
begin
  if current_database()='agent_platform_control'
     and current_user='platform_control_owner'
  then
    selected_app := 'platform_control_app';
    selected_brain := 'platform_brain_worker';
  elsif current_database()='agent_platform_control_preview'
     and current_user='platform_control_owner_preview'
  then
    selected_app := 'platform_control_app_preview';
    selected_brain := 'platform_brain_worker_preview';
  else
    raise insufficient_privilege using message='Action migration owner invalid';
  end if;
  foreach role_name in array array[
    'platform_control_migrator','platform_control_app',
    'platform_directory_worker','platform_stream_ingest',
    'platform_audit_append','platform_control_maintenance',
    'platform_brain_worker','platform_control_migrator_preview',
    'platform_control_app_preview','platform_directory_worker_preview',
    'platform_stream_ingest_preview','platform_audit_append_preview',
    'platform_control_maintenance_preview','platform_brain_worker_preview'
  ] loop
    execute format(
      'revoke all on platform_brain.agent_task_actions, '
      'platform_brain.agent_action_deliveries from %I',role_name
    );
  end loop;
  execute format(
    'grant select,insert,update on platform_brain.agent_task_actions, '
    'platform_brain.agent_action_deliveries to %I',selected_brain
  );
  execute format(
    'grant select on platform_brain.agent_task_actions, '
    'platform_brain.agent_action_deliveries to %I',selected_app
  );
  execute format(
    'grant execute on function platform_brain.propose_agent_task_action_v51('
    'uuid,uuid,integer,text,bytea,integer,bytea,bytea,integer,bytea,'
    'bytea,integer,bytea,bytea,timestamptz,integer) to %I',selected_brain
  );
  execute format(
    'grant execute on function platform_brain.confirm_agent_task_action_v51('
    'uuid,uuid,bytea,uuid,text) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_brain.reject_agent_task_action_v51('
    'uuid,uuid) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_brain.expire_agent_task_actions_v51('
    'integer) to %I',selected_brain
  );
  execute format(
    'grant execute on function platform_brain.supersede_agent_task_action_v51('
    'uuid) to %I',selected_brain
  );
  execute format(
    'grant execute on function platform_brain.resume_action_resolution_v51('
    'uuid) to %I',selected_brain
  );
  execute format(
    'grant execute on function platform_brain.resume_action_resolution_v51('
    'uuid) to %I',selected_app
  );
end
$migration$;

comment on table platform_brain.agent_task_actions is
  'Encrypted owner-confirmed professional-Agent actions bound through Task to Conversation owner.';
comment on table platform_brain.agent_action_deliveries is
  'Exactly-once execution deliveries created only after verified Action confirmation.';
