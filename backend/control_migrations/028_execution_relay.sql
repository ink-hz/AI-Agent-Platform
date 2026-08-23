create table platform_control.execution_workers (
  worker_id text primary key
    check (worker_id ~ '^[a-z0-9][a-z0-9._-]{0,63}$'),
  allowed_agent_ids text[] not null
    check (cardinality(allowed_agent_ids) > 0),
  status text not null check (status in ('active', 'revoked')),
  created_at timestamptz not null default now(),
  revoked_at timestamptz,
  last_seen_at timestamptz,
  check ((status = 'active' and revoked_at is null) or status = 'revoked')
);

create table platform_control.execution_worker_keys (
  worker_id text not null
    references platform_control.execution_workers(worker_id),
  key_id text not null check (key_id ~ '^worker-v[1-9][0-9]*$'),
  public_key bytea not null check (octet_length(public_key) = 32),
  status text not null check (status in ('active', 'revoked')),
  created_at timestamptz not null default now(),
  revoked_at timestamptz,
  primary key (worker_id, key_id),
  unique (public_key),
  check ((status = 'active' and revoked_at is null) or status = 'revoked')
);

create table platform_control.execution_jobs (
  job_id uuid primary key,
  run_id uuid not null unique,
  agent_id text not null
    check (agent_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'),
  payload_ciphertext bytea not null,
  encryption_key_version integer not null
    check (encryption_key_version > 0),
  status text not null check (status in (
    'queued', 'leased', 'dispatched', 'running', 'completed', 'failed',
    'cancelled', 'interrupted'
  )),
  lease_worker_id text
    references platform_control.execution_workers(worker_id),
  lease_expires_at timestamptz,
  cancel_requested boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  terminal_at timestamptz,
  check (
    (status = 'queued' and lease_worker_id is null and lease_expires_at is null)
    or (status <> 'queued' and lease_worker_id is not null)
  ),
  check (
    (status in ('completed', 'failed', 'cancelled', 'interrupted'))
      = (terminal_at is not null)
  )
);

create table platform_control.execution_events (
  run_id uuid not null references platform_control.execution_jobs(run_id),
  seq integer not null check (seq > 0),
  event_type text not null
    check (event_type ~ '^[a-z][a-z0-9_.-]{0,63}$'),
  payload_ciphertext bytea not null,
  encryption_key_version integer not null
    check (encryption_key_version > 0),
  created_at timestamptz not null,
  received_at timestamptz not null default now(),
  primary key (run_id, seq)
);

create table platform_control.execution_worker_nonces (
  worker_id text not null
    references platform_control.execution_workers(worker_id),
  nonce bytea not null check (octet_length(nonce) = 32),
  expires_at timestamptz not null,
  primary key (worker_id, nonce)
);

create index execution_jobs_status_created
  on platform_control.execution_jobs (status, created_at);
create index execution_jobs_worker_status
  on platform_control.execution_jobs (lease_worker_id, status);
create index execution_worker_nonces_expiry
  on platform_control.execution_worker_nonces (expires_at);

create function platform_control.append_execution_worker_audit_v28(
  selected_request_id uuid,
  selected_event_type text,
  selected_target_type text,
  selected_target_id text,
  selected_details jsonb
) returns boolean
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  stored platform_control.audit_events%rowtype;
  expected_keys text[];
  actual_keys text[];
  selected_worker_id text;
  selected_key_id text;
begin
  if selected_request_id is null
     or substring(selected_request_id::text from 15 for 1) <> '4'
     or substring(selected_request_id::text from 20 for 1) !~ '^[89ab]$'
     or jsonb_typeof(selected_details) <> 'object'
  then
    raise check_violation using message = 'execution relay audit input invalid';
  end if;

  case selected_event_type
    when 'execution_worker_registered' then
      if selected_target_type <> 'execution_worker' then
        raise check_violation using message = 'execution relay audit target invalid';
      end if;
      expected_keys := array[
        'allowed_agent_ids', 'key_id', 'public_key_sha256', 'reference',
        'worker_id'
      ];
    when 'execution_worker_key_added' then
      if selected_target_type <> 'execution_worker_key' then
        raise check_violation using message = 'execution relay audit target invalid';
      end if;
      expected_keys := array[
        'allowed_agent_ids', 'key_id', 'public_key_sha256', 'reference',
        'worker_id'
      ];
    when 'execution_worker_key_revoked' then
      if selected_target_type <> 'execution_worker_key' then
        raise check_violation using message = 'execution relay audit target invalid';
      end if;
      expected_keys := array[
        'allowed_agent_ids', 'key_id', 'public_key_sha256', 'reference',
        'worker_id'
      ];
    when 'execution_worker_revoked' then
      if selected_target_type <> 'execution_worker' then
        raise check_violation using message = 'execution relay audit target invalid';
      end if;
      expected_keys := array['allowed_agent_ids', 'reference', 'worker_id'];
    else
      raise check_violation using message = 'execution relay audit event invalid';
  end case;

  select array_agg(key order by key) into actual_keys
    from jsonb_object_keys(selected_details) key;
  if actual_keys is distinct from expected_keys then
    raise check_violation using message = 'execution relay audit details invalid';
  end if;

  selected_worker_id := selected_details ->> 'worker_id';
  selected_key_id := selected_details ->> 'key_id';
  if selected_worker_id is null
     or selected_worker_id !~ '^[a-z0-9][a-z0-9._-]{0,63}$'
     or selected_details ->> 'reference' is null
     or selected_details ->> 'reference' !~ '^[A-Z][A-Z0-9_-]{7,63}$'
     or jsonb_typeof(selected_details -> 'allowed_agent_ids') <> 'array'
     or jsonb_array_length(selected_details -> 'allowed_agent_ids') = 0
     or exists (
       select 1
       from jsonb_array_elements(selected_details -> 'allowed_agent_ids') item
       where jsonb_typeof(item) <> 'string'
          or item #>> '{}' !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
     )
  then
    raise check_violation using message = 'execution relay audit details invalid';
  end if;

  if selected_event_type in (
       'execution_worker_registered', 'execution_worker_key_added',
       'execution_worker_key_revoked'
     ) and (
       selected_key_id is null
       or selected_key_id !~ '^worker-v[1-9][0-9]*$'
       or selected_details ->> 'public_key_sha256' is null
       or selected_details ->> 'public_key_sha256' !~ '^[0-9a-f]{64}$'
     )
  then
    raise check_violation using message = 'execution relay audit key details invalid';
  end if;

  if selected_target_id is null or (
       selected_target_type = 'execution_worker'
       and selected_target_id <> selected_worker_id
     ) or (
       selected_target_type = 'execution_worker_key'
       and selected_target_id <> selected_worker_id || '/' || selected_key_id
     )
  then
    raise check_violation using message = 'execution relay audit identity invalid';
  end if;

  perform pg_advisory_xact_lock(
    hashtextextended('execution-request:' || selected_request_id::text, 0)
  );
  select * into stored
    from platform_control.audit_events
    where audit_event_id = selected_request_id
    for update;
  if found then
    if stored.actor_internal_user_id is distinct from null
       or stored.event_type is distinct from selected_event_type
       or stored.target_type is distinct from selected_target_type
       or stored.target_internal_id is distinct from selected_target_id
       or stored.request_id is distinct from selected_request_id
       or stored.result is distinct from 'completed'
       or stored.reason_code is distinct from 'offline_maintenance'
       or stored.sanitized_before_after is distinct from selected_details
    then
      raise check_violation using
        message = 'execution relay audit identity collision';
    end if;
    return false;
  end if;

  insert into platform_control.audit_events (
    audit_event_id, actor_internal_user_id, event_type, target_type,
    target_internal_id, request_id, result, reason_code,
    sanitized_before_after
  ) values (
    selected_request_id, null, selected_event_type, selected_target_type,
    selected_target_id, selected_request_id, 'completed',
    'offline_maintenance', selected_details
  );
  return true;
end
$function$;

create function platform_control.replay_execution_worker_audit_v28(
  selected_request_id uuid,
  selected_event_type text,
  selected_target_type text,
  selected_target_id text,
  selected_input_details jsonb
) returns boolean
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  stored platform_control.audit_events%rowtype;
  expected_keys text[];
  actual_keys text[];
  selected_worker_id text;
  selected_key_id text;
begin
  if selected_request_id is null
     or substring(selected_request_id::text from 15 for 1) <> '4'
     or substring(selected_request_id::text from 20 for 1) !~ '^[89ab]$'
     or jsonb_typeof(selected_input_details) <> 'object'
  then
    raise check_violation using message = 'execution relay audit input invalid';
  end if;

  case selected_event_type
    when 'execution_worker_registered' then
      if selected_target_type <> 'execution_worker' then
        raise check_violation using message = 'execution relay audit target invalid';
      end if;
      expected_keys := array[
        'allowed_agent_ids', 'key_id', 'public_key_sha256', 'reference',
        'worker_id'
      ];
    when 'execution_worker_key_added' then
      if selected_target_type <> 'execution_worker_key' then
        raise check_violation using message = 'execution relay audit target invalid';
      end if;
      expected_keys := array[
        'key_id', 'public_key_sha256', 'reference', 'worker_id'
      ];
    when 'execution_worker_key_revoked' then
      if selected_target_type <> 'execution_worker_key' then
        raise check_violation using message = 'execution relay audit target invalid';
      end if;
      expected_keys := array['key_id', 'reference', 'worker_id'];
    when 'execution_worker_revoked' then
      if selected_target_type <> 'execution_worker' then
        raise check_violation using message = 'execution relay audit target invalid';
      end if;
      expected_keys := array['reference', 'worker_id'];
    else
      raise check_violation using message = 'execution relay audit event invalid';
  end case;

  select array_agg(key order by key) into actual_keys
    from jsonb_object_keys(selected_input_details) key;
  selected_worker_id := selected_input_details ->> 'worker_id';
  selected_key_id := selected_input_details ->> 'key_id';
  if actual_keys is distinct from expected_keys
     or selected_worker_id is null
     or selected_worker_id !~ '^[a-z0-9][a-z0-9._-]{0,63}$'
     or selected_input_details ->> 'reference' is null
     or selected_input_details ->> 'reference'
        !~ '^[A-Z][A-Z0-9_-]{7,63}$'
     or selected_target_id is null
     or (
       selected_target_type = 'execution_worker'
       and selected_target_id <> selected_worker_id
     )
     or (
       selected_target_type = 'execution_worker_key'
       and selected_target_id <> selected_worker_id || '/' || selected_key_id
     )
  then
    raise check_violation using message = 'execution relay audit input invalid';
  end if;
  if selected_event_type in (
       'execution_worker_registered', 'execution_worker_key_added',
       'execution_worker_key_revoked'
     ) and (
       selected_key_id is null
       or selected_key_id !~ '^worker-v[1-9][0-9]*$'
     )
  then
    raise check_violation using message = 'execution relay audit input invalid';
  end if;
  if selected_event_type in (
       'execution_worker_registered', 'execution_worker_key_added'
     ) and (
       selected_input_details ->> 'public_key_sha256' is null
       or selected_input_details ->> 'public_key_sha256'
          !~ '^[0-9a-f]{64}$'
     )
  then
    raise check_violation using message = 'execution relay audit input invalid';
  end if;

  perform pg_advisory_xact_lock(
    hashtextextended('execution-request:' || selected_request_id::text, 0)
  );
  select * into stored
    from platform_control.audit_events
    where audit_event_id = selected_request_id
    for update;
  if not found then return false; end if;
  if stored.actor_internal_user_id is distinct from null
     or stored.event_type is distinct from selected_event_type
     or stored.target_type is distinct from selected_target_type
     or stored.target_internal_id is distinct from selected_target_id
     or stored.request_id is distinct from selected_request_id
     or stored.result is distinct from 'completed'
     or stored.reason_code is distinct from 'offline_maintenance'
     or (
       selected_event_type = 'execution_worker_registered'
       and stored.sanitized_before_after is distinct from selected_input_details
     )
     or (
       selected_event_type <> 'execution_worker_registered'
       and not (stored.sanitized_before_after @> selected_input_details)
     )
  then
    raise check_violation using
      message = 'execution relay audit identity collision';
  end if;
  perform platform_control.append_execution_worker_audit_v28(
    selected_request_id, selected_event_type, selected_target_type,
    selected_target_id, stored.sanitized_before_after
  );
  return true;
end
$function$;

create function platform_control.register_execution_worker_v28(
  selected_worker_id text,
  selected_key_id text,
  selected_public_key bytea,
  selected_allowed_agent_ids text[],
  selected_change_reference text,
  selected_request_id uuid
) returns void
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  audit_inserted boolean;
  audit_details jsonb;
begin
  if selected_worker_id is null
     or selected_worker_id !~ '^[a-z0-9][a-z0-9._-]{0,63}$'
     or selected_key_id is null
     or selected_key_id !~ '^worker-v[1-9][0-9]*$'
     or selected_public_key is null
     or octet_length(selected_public_key) <> 32
     or cardinality(selected_allowed_agent_ids) is null
     or cardinality(selected_allowed_agent_ids) = 0
     or exists (
       select 1 from unnest(selected_allowed_agent_ids) agent_id
       where agent_id is null
          or agent_id !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
     )
     or cardinality(selected_allowed_agent_ids) <> (
       select count(distinct agent_id)
       from unnest(selected_allowed_agent_ids) agent_id
     )
  then
    raise check_violation using message = 'execution worker registration invalid';
  end if;

  perform pg_advisory_xact_lock(
    hashtextextended('execution-request:' || selected_request_id::text, 0)
  );
  audit_details := jsonb_build_object(
    'worker_id', selected_worker_id,
    'key_id', selected_key_id,
    'public_key_sha256', encode(sha256(selected_public_key), 'hex'),
    'allowed_agent_ids', to_jsonb(selected_allowed_agent_ids),
    'reference', selected_change_reference
  );
  if platform_control.replay_execution_worker_audit_v28(
    selected_request_id, 'execution_worker_registered', 'execution_worker',
    selected_worker_id, audit_details
  ) then return; end if;
  perform pg_advisory_xact_lock(
    hashtextextended('execution-worker:' || selected_worker_id, 0)
  );
  audit_inserted := platform_control.append_execution_worker_audit_v28(
    selected_request_id, 'execution_worker_registered', 'execution_worker',
    selected_worker_id, audit_details
  );
  if not audit_inserted then return; end if;

  if exists (
    select 1 from platform_control.execution_workers
    where worker_id = selected_worker_id
  ) then
    raise check_violation using message = 'execution worker already registered';
  end if;
  insert into platform_control.execution_workers (
    worker_id, allowed_agent_ids, status
  ) values (
    selected_worker_id, selected_allowed_agent_ids, 'active'
  );
  insert into platform_control.execution_worker_keys (
    worker_id, key_id, public_key, status
  ) values (
    selected_worker_id, selected_key_id, selected_public_key, 'active'
  );
end
$function$;

create function platform_control.add_execution_worker_key_v28(
  selected_worker_id text,
  selected_key_id text,
  selected_public_key bytea,
  selected_change_reference text,
  selected_request_id uuid
) returns void
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  audit_inserted boolean;
  selected_worker platform_control.execution_workers%rowtype;
  stored_key platform_control.execution_worker_keys%rowtype;
  input_details jsonb;
begin
  if selected_worker_id is null
     or selected_worker_id !~ '^[a-z0-9][a-z0-9._-]{0,63}$'
     or selected_key_id is null
     or selected_key_id !~ '^worker-v[1-9][0-9]*$'
     or selected_public_key is null
     or octet_length(selected_public_key) <> 32
  then
    raise check_violation using message = 'execution worker key input invalid';
  end if;
  perform pg_advisory_xact_lock(
    hashtextextended('execution-request:' || selected_request_id::text, 0)
  );
  input_details := jsonb_build_object(
    'worker_id', selected_worker_id,
    'key_id', selected_key_id,
    'public_key_sha256', encode(sha256(selected_public_key), 'hex'),
    'reference', selected_change_reference
  );
  if platform_control.replay_execution_worker_audit_v28(
    selected_request_id, 'execution_worker_key_added',
    'execution_worker_key', selected_worker_id || '/' || selected_key_id,
    input_details
  ) then return; end if;
  perform pg_advisory_xact_lock(
    hashtextextended('execution-worker:' || selected_worker_id, 0)
  );
  select * into selected_worker
    from platform_control.execution_workers
    where worker_id = selected_worker_id
    for update;
  if not found then
    raise check_violation using message = 'execution worker not found';
  end if;
  audit_inserted := platform_control.append_execution_worker_audit_v28(
    selected_request_id, 'execution_worker_key_added', 'execution_worker_key',
    selected_worker_id || '/' || selected_key_id,
    jsonb_build_object(
      'worker_id', selected_worker_id,
      'key_id', selected_key_id,
      'public_key_sha256', encode(sha256(selected_public_key), 'hex'),
      'allowed_agent_ids', to_jsonb(selected_worker.allowed_agent_ids),
      'reference', selected_change_reference
    )
  );
  if not audit_inserted then return; end if;
  if selected_worker.status <> 'active' then
    raise check_violation using message = 'execution worker is not active';
  end if;

  select * into stored_key
    from platform_control.execution_worker_keys
    where worker_id = selected_worker_id and key_id = selected_key_id
    for update;
  if found then
    if stored_key.public_key is distinct from selected_public_key then
      raise check_violation using message = 'execution worker key id reused';
    end if;
    if stored_key.status <> 'active' then
      raise check_violation using message = 'execution worker key is revoked';
    end if;
    return;
  end if;
  if (
    select count(*)
    from platform_control.execution_worker_keys
    where worker_id = selected_worker_id and status = 'active'
  ) >= 2 then
    raise check_violation using
      message = 'execution worker dual key window exceeded';
  end if;
  insert into platform_control.execution_worker_keys (
    worker_id, key_id, public_key, status
  ) values (
    selected_worker_id, selected_key_id, selected_public_key, 'active'
  );
end
$function$;

create function platform_control.revoke_execution_worker_key_v28(
  selected_worker_id text,
  selected_key_id text,
  selected_change_reference text,
  selected_request_id uuid
) returns void
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  audit_inserted boolean;
  selected_worker platform_control.execution_workers%rowtype;
  selected_key platform_control.execution_worker_keys%rowtype;
begin
  if selected_worker_id is null
     or selected_worker_id !~ '^[a-z0-9][a-z0-9._-]{0,63}$'
     or selected_key_id is null
     or selected_key_id !~ '^worker-v[1-9][0-9]*$'
  then
    raise check_violation using message = 'execution worker key input invalid';
  end if;
  perform pg_advisory_xact_lock(
    hashtextextended('execution-request:' || selected_request_id::text, 0)
  );
  if platform_control.replay_execution_worker_audit_v28(
    selected_request_id, 'execution_worker_key_revoked',
    'execution_worker_key', selected_worker_id || '/' || selected_key_id,
    jsonb_build_object(
      'worker_id', selected_worker_id,
      'key_id', selected_key_id,
      'reference', selected_change_reference
    )
  ) then return; end if;
  perform pg_advisory_xact_lock(
    hashtextextended('execution-worker:' || selected_worker_id, 0)
  );
  select * into selected_worker
    from platform_control.execution_workers
    where worker_id = selected_worker_id
    for update;
  select * into selected_key
    from platform_control.execution_worker_keys
    where worker_id = selected_worker_id and key_id = selected_key_id
    for update;
  if selected_worker.worker_id is null or selected_key.key_id is null then
    raise check_violation using message = 'execution worker key not found';
  end if;
  audit_inserted := platform_control.append_execution_worker_audit_v28(
    selected_request_id, 'execution_worker_key_revoked',
    'execution_worker_key', selected_worker_id || '/' || selected_key_id,
    jsonb_build_object(
      'worker_id', selected_worker_id,
      'key_id', selected_key_id,
      'public_key_sha256', encode(sha256(selected_key.public_key), 'hex'),
      'allowed_agent_ids', to_jsonb(selected_worker.allowed_agent_ids),
      'reference', selected_change_reference
    )
  );
  if not audit_inserted then return; end if;
  if selected_worker.status <> 'active' or selected_key.status <> 'active' then
    raise check_violation using message = 'execution worker key is not active';
  end if;
  update platform_control.execution_worker_keys
    set status = 'revoked', revoked_at = clock_timestamp()
    where worker_id = selected_worker_id and key_id = selected_key_id;
end
$function$;

create function platform_control.revoke_execution_worker_v28(
  selected_worker_id text,
  selected_change_reference text,
  selected_request_id uuid
) returns void
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  audit_inserted boolean;
  selected_worker platform_control.execution_workers%rowtype;
  database_now timestamptz := clock_timestamp();
begin
  if selected_worker_id is null
     or selected_worker_id !~ '^[a-z0-9][a-z0-9._-]{0,63}$'
  then
    raise check_violation using message = 'execution worker input invalid';
  end if;
  perform pg_advisory_xact_lock(
    hashtextextended('execution-request:' || selected_request_id::text, 0)
  );
  if platform_control.replay_execution_worker_audit_v28(
    selected_request_id, 'execution_worker_revoked', 'execution_worker',
    selected_worker_id, jsonb_build_object(
      'worker_id', selected_worker_id,
      'reference', selected_change_reference
    )
  ) then return; end if;
  perform pg_advisory_xact_lock(
    hashtextextended('execution-worker:' || selected_worker_id, 0)
  );
  select * into selected_worker
    from platform_control.execution_workers
    where worker_id = selected_worker_id
    for update;
  if not found then
    raise check_violation using message = 'execution worker not found';
  end if;
  audit_inserted := platform_control.append_execution_worker_audit_v28(
    selected_request_id, 'execution_worker_revoked', 'execution_worker',
    selected_worker_id, jsonb_build_object(
      'worker_id', selected_worker_id,
      'allowed_agent_ids', to_jsonb(selected_worker.allowed_agent_ids),
      'reference', selected_change_reference
    )
  );
  if not audit_inserted then return; end if;
  if selected_worker.status <> 'active' then
    raise check_violation using message = 'execution worker is not active';
  end if;
  update platform_control.execution_worker_keys
    set status = 'revoked', revoked_at = database_now
    where worker_id = selected_worker_id and status = 'active';
  update platform_control.execution_workers
    set status = 'revoked', revoked_at = database_now
    where worker_id = selected_worker_id;
end
$function$;

create function platform_control.touch_execution_worker_v28(
  selected_worker_id text
) returns void
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  if selected_worker_id is null
     or selected_worker_id !~ '^[a-z0-9][a-z0-9._-]{0,63}$'
  then
    raise check_violation using message = 'execution worker heartbeat invalid';
  end if;
  update platform_control.execution_workers
    set last_seen_at = clock_timestamp()
    where worker_id = selected_worker_id and status = 'active';
  if not found then
    raise check_violation using message = 'active execution worker required';
  end if;
end
$function$;

revoke all on
  platform_control.execution_workers,
  platform_control.execution_worker_keys,
  platform_control.execution_jobs,
  platform_control.execution_events,
  platform_control.execution_worker_nonces
from public;
revoke all on function platform_control.append_execution_worker_audit_v28(
  uuid, text, text, text, jsonb
) from public;
revoke all on function platform_control.replay_execution_worker_audit_v28(
  uuid, text, text, text, jsonb
) from public;
revoke all on function platform_control.register_execution_worker_v28(
  text, text, bytea, text[], text, uuid
) from public;
revoke all on function platform_control.add_execution_worker_key_v28(
  text, text, bytea, text, uuid
) from public;
revoke all on function platform_control.revoke_execution_worker_key_v28(
  text, text, text, uuid
) from public;
revoke all on function platform_control.revoke_execution_worker_v28(
  text, text, uuid
) from public;
revoke all on function platform_control.touch_execution_worker_v28(text)
from public;

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
      'revoke all on platform_control.execution_workers, '
      'platform_control.execution_worker_keys, '
      'platform_control.execution_jobs, platform_control.execution_events, '
      'platform_control.execution_worker_nonces from %I', role_name
    );
    execute format(
      'revoke all on function '
      'platform_control.append_execution_worker_audit_v28('
      'uuid,text,text,text,jsonb) from %I', role_name
    );
    execute format(
      'revoke all on function '
      'platform_control.replay_execution_worker_audit_v28('
      'uuid,text,text,text,jsonb) from %I', role_name
    );
    execute format(
      'revoke all on function '
      'platform_control.register_execution_worker_v28('
      'text,text,bytea,text[],text,uuid) from %I', role_name
    );
    execute format(
      'revoke all on function '
      'platform_control.add_execution_worker_key_v28('
      'text,text,bytea,text,uuid) from %I', role_name
    );
    execute format(
      'revoke all on function '
      'platform_control.revoke_execution_worker_key_v28('
      'text,text,text,uuid) from %I', role_name
    );
    execute format(
      'revoke all on function '
      'platform_control.revoke_execution_worker_v28(text,text,uuid) from %I',
      role_name
    );
    execute format(
      'revoke all on function '
      'platform_control.touch_execution_worker_v28(text) from %I', role_name
    );
  end loop;

  execute format(
    'grant select on platform_control.execution_workers, '
    'platform_control.execution_worker_keys to %I', selected_app
  );
  execute format(
    'grant select,insert,update on platform_control.execution_jobs, '
    'platform_control.execution_events to %I', selected_app
  );
  execute format(
    'grant select,insert,delete on '
    'platform_control.execution_worker_nonces to %I', selected_app
  );
  execute format(
    'grant execute on function '
    'platform_control.touch_execution_worker_v28(text) to %I', selected_app
  );
  execute format(
    'grant execute on function '
    'platform_control.register_execution_worker_v28('
    'text,text,bytea,text[],text,uuid) to %I', selected_maintenance
  );
  execute format(
    'grant execute on function '
    'platform_control.add_execution_worker_key_v28('
    'text,text,bytea,text,uuid) to %I', selected_maintenance
  );
  execute format(
    'grant execute on function '
    'platform_control.revoke_execution_worker_key_v28('
    'text,text,text,uuid) to %I', selected_maintenance
  );
  execute format(
    'grant execute on function '
    'platform_control.revoke_execution_worker_v28(text,text,uuid) to %I',
    selected_maintenance
  );
end
$migration$;
