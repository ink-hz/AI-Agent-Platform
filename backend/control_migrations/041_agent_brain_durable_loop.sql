create schema platform_brain authorization current_user;

revoke all on schema platform_brain from public;

create table platform_brain.authorization_snapshots (
  authorization_snapshot_id uuid primary key,
  internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  agent_id text not null
    check (agent_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'),
  allowed boolean not null,
  grant_ids uuid[] not null default '{}',
  directory_generation_id uuid,
  capability_version bigint not null check (capability_version > 0),
  effective_decision_hash bytea not null
    check (octet_length(effective_decision_hash) = 32),
  computed_at timestamptz not null default clock_timestamp()
);

create table platform_brain.brain_loops (
  loop_id uuid primary key,
  conversation_id uuid not null,
  turn_id uuid not null unique,
  status text not null check (status in (
    'queued','running','waiting_agents','waiting_user','completing',
    'completed','failed','cancelled','interrupted'
  )),
  outcome text check (
    outcome is null or outcome in (
      'resolved','partially_completed','safe_abstained'
    )
  ),
  reason_code text check (
    reason_code is null or reason_code ~ '^[a-z][a-z0-9_]{0,63}$'
  ),
  model_config_ciphertext bytea not null
    check (octet_length(model_config_ciphertext) between 29 and 1048576),
  model_config_key_version integer not null
    check (model_config_key_version > 0),
  max_steps integer not null check (max_steps between 1 and 128),
  max_tasks integer not null check (max_tasks between 0 and 128),
  max_duration_seconds integer not null
    check (max_duration_seconds between 1 and 86400),
  step_count integer not null default 0 check (step_count between 0 and max_steps),
  task_count integer not null default 0 check (task_count between 0 and max_tasks),
  active_budget_ms bigint not null check (active_budget_ms > 0),
  active_elapsed_ms bigint not null default 0
    check (active_elapsed_ms between 0 and active_budget_ms),
  active_started_at timestamptz,
  active_deadline_at timestamptz,
  waiting_user_expires_at timestamptz,
  cancel_requested boolean not null default false,
  protocol_retry_count integer not null default 0
    check (protocol_retry_count between 0 and 1),
  fallback_used boolean not null default false,
  fallback_kind text check (
    fallback_kind is null or fallback_kind ~ '^[a-z][a-z0-9_]{0,63}$'
  ),
  row_version bigint not null default 0 check (row_version >= 0),
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  terminal_at timestamptz,
  foreign key (conversation_id, turn_id)
    references platform_control.conversation_turns(conversation_id, turn_id)
    deferrable initially deferred,
  check (
    (status in ('completed','failed','cancelled','interrupted'))
      = (terminal_at is not null)
  ),
  check (
    (fallback_used and fallback_kind is not null)
    or (not fallback_used and fallback_kind is null)
  ),
  check (
    (
      status = 'waiting_user'
      and active_started_at is null
      and active_deadline_at is null
      and waiting_user_expires_at is not null
    ) or (
      status in ('running','waiting_agents','completing')
      and active_started_at is not null
      and active_deadline_at is not null
      and waiting_user_expires_at is null
    ) or (
      status in ('queued','completed','failed','cancelled','interrupted')
      and active_started_at is null
      and active_deadline_at is null
      and waiting_user_expires_at is null
    )
  )
);

create table platform_brain.brain_steps (
  step_id uuid primary key,
  loop_id uuid not null
    references platform_brain.brain_loops(loop_id),
  step_seq integer not null check (step_seq > 0),
  status text not null check (status in (
    'queued','leased','requesting_model','waiting_tool_results',
    'completed','failed'
  )),
  lease_worker_id text check (
    lease_worker_id is null
    or lease_worker_id ~ '^[a-z0-9][a-z0-9._-]{0,127}$'
  ),
  lease_expires_at timestamptz,
  attempt integer not null default 0 check (attempt >= 0),
  input_prefix_hash bytea check (
    input_prefix_hash is null or octet_length(input_prefix_hash) = 32
  ),
  model_request_id text check (
    model_request_id is null or char_length(model_request_id) between 1 and 160
  ),
  model_response_ciphertext bytea check (
    model_response_ciphertext is null
    or octet_length(model_response_ciphertext) between 29 and 16777216
  ),
  model_response_key_version integer check (
    model_response_key_version is null or model_response_key_version > 0
  ),
  response_retention_until timestamptz,
  response_erased_at timestamptz,
  usage jsonb not null default '{}'::jsonb
    check (jsonb_typeof(usage) = 'object'),
  cache_usage jsonb not null default '{}'::jsonb
    check (jsonb_typeof(cache_usage) = 'object'),
  stop_reason text check (
    stop_reason is null or stop_reason ~ '^[a-z][a-z0-9_-]{0,63}$'
  ),
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  terminal_at timestamptz,
  unique (loop_id, step_seq),
  check (
    (lease_worker_id is null) = (lease_expires_at is null)
  ),
  check (
    (status in ('leased','requesting_model'))
      = (lease_worker_id is not null and lease_expires_at is not null)
  ),
  check (
    (status in ('completed','failed')) = (terminal_at is not null)
  ),
  check (
    (model_response_ciphertext is null)
      = (model_response_key_version is null)
  ),
  check (
    response_erased_at is null or model_response_ciphertext is null
  )
);

create unique index one_active_brain_step
  on platform_brain.brain_steps(loop_id)
  where status in (
    'queued','leased','requesting_model','waiting_tool_results'
  );

create table platform_brain.brain_tool_calls (
  brain_tool_call_id uuid primary key,
  step_id uuid not null references platform_brain.brain_steps(step_id),
  tool_index integer not null check (tool_index >= 0),
  provider_tool_call_id text not null
    check (char_length(provider_tool_call_id) between 1 and 160),
  tool_name text not null check (tool_name in (
    'list_agents','delegate_task','request_user_input','submit_answer'
  )),
  arguments_ciphertext bytea not null
    check (octet_length(arguments_ciphertext) between 29 and 1048576),
  arguments_key_version integer not null check (arguments_key_version > 0),
  public_reason text not null
    check (octet_length(convert_to(public_reason, 'UTF8')) between 1 and 512),
  status text not null check (status in (
    'accepted','waiting_result','result_ready','consumed','failed'
  )),
  result_ciphertext bytea check (
    result_ciphertext is null
    or octet_length(result_ciphertext) between 29 and 1048576
  ),
  result_key_version integer check (
    result_key_version is null or result_key_version > 0
  ),
  result_sha256 bytea check (
    result_sha256 is null or octet_length(result_sha256) = 32
  ),
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  terminal_at timestamptz,
  unique (step_id, tool_index),
  unique (step_id, provider_tool_call_id),
  check (
    (result_ciphertext is null)
      = (result_key_version is null and result_sha256 is null)
  ),
  check ((status in ('consumed','failed')) = (terminal_at is not null))
);

create table platform_brain.agent_tasks (
  task_id uuid primary key,
  loop_id uuid not null references platform_brain.brain_loops(loop_id),
  brain_tool_call_id uuid not null unique
    references platform_brain.brain_tool_calls(brain_tool_call_id),
  agent_id text not null
    check (agent_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'),
  adapter_kind text not null
    check (adapter_kind ~ '^[a-z][a-z0-9_]{0,63}$'),
  capability_version bigint not null check (capability_version > 0),
  authorization_snapshot_id uuid not null
    references platform_brain.authorization_snapshots(
      authorization_snapshot_id
    ),
  task_context_ciphertext bytea not null
    check (octet_length(task_context_ciphertext) between 29 and 1048576),
  task_context_key_version integer not null
    check (task_context_key_version > 0),
  status text not null check (status in (
    'queued','running','completed','failed','cancelled','timed_out','unavailable'
  )),
  effective_deadline_at timestamptz not null,
  cancel_requested boolean not null default false,
  row_version bigint not null default 0 check (row_version >= 0),
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  started_at timestamptz,
  terminal_at timestamptz,
  check (
    (status = 'queued' and started_at is null and terminal_at is null)
    or (status = 'running' and started_at is not null and terminal_at is null)
    or (
      status in ('completed','failed','cancelled','timed_out','unavailable')
      and terminal_at is not null
    )
  )
);

create table platform_brain.agent_task_events (
  task_id uuid not null references platform_brain.agent_tasks(task_id),
  seq integer not null check (seq > 0),
  event_type text not null
    check (event_type ~ '^[a-z][a-z0-9_.-]{0,63}$'),
  payload_ciphertext bytea not null
    check (octet_length(payload_ciphertext) between 29 and 1048576),
  payload_key_version integer not null check (payload_key_version > 0),
  payload_sha256 bytea not null check (octet_length(payload_sha256) = 32),
  created_at timestamptz not null,
  received_at timestamptz not null default clock_timestamp(),
  primary key (task_id, seq)
);

create table platform_brain.adapter_deliveries (
  delivery_id uuid primary key,
  task_id uuid not null references platform_brain.agent_tasks(task_id),
  adapter_kind text not null
    check (adapter_kind ~ '^[a-z][a-z0-9_]{0,63}$'),
  attempt integer not null check (attempt > 0),
  status text not null check (status in (
    'queued','leased','dispatched','completed','failed','expired'
  )),
  lease_worker_id text check (
    lease_worker_id is null
    or lease_worker_id ~ '^[a-z0-9][a-z0-9._-]{0,127}$'
  ),
  lease_expires_at timestamptz,
  idempotency_key text not null unique
    check (idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{15,159}$'),
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  terminal_at timestamptz,
  unique (task_id, attempt),
  check ((lease_worker_id is null) = (lease_expires_at is null)),
  check (
    (status = 'leased') =
      (lease_worker_id is not null and lease_expires_at is not null)
  ),
  check (
    (status in ('completed','failed','expired')) = (terminal_at is not null)
  )
);

create unique index one_active_adapter_delivery
  on platform_brain.adapter_deliveries(task_id, adapter_kind)
  where status in ('queued','leased','dispatched');

create table platform_brain.brain_checkpoints (
  loop_id uuid not null references platform_brain.brain_loops(loop_id),
  through_step_seq integer not null check (through_step_seq > 0),
  source_hash bytea not null check (octet_length(source_hash) = 32),
  checkpoint_ciphertext bytea not null
    check (octet_length(checkpoint_ciphertext) between 29 and 16777216),
  checkpoint_key_version integer not null
    check (checkpoint_key_version > 0),
  created_at timestamptz not null default clock_timestamp(),
  expires_at timestamptz not null,
  primary key (loop_id, through_step_seq),
  check (expires_at > created_at)
);

alter table platform_control.conversation_turns
  add column retry_of_turn_id uuid
    references platform_control.conversation_turns(turn_id);

alter table platform_control.conversation_turns
  add constraint conversation_turn_retry_not_self_v41 check (
    retry_of_turn_id is null or retry_of_turn_id <> turn_id
  ),
  drop constraint conversation_turns_status_check,
  add constraint conversation_turns_status_check check (status in (
    'accepted','running','waiting_agents','waiting_user','completing',
    'completed','failed','cancelled','interrupted'
  ));

drop index platform_control.one_active_conversation_turn;
create unique index one_active_conversation_turn
  on platform_control.conversation_turns(conversation_id)
  where status in (
    'accepted','running','waiting_agents','waiting_user','completing'
  );

alter table platform_control.conversation_events
  drop constraint conversation_events_event_type_check,
  add constraint conversation_events_event_type_check check (event_type in (
    'conversation.started','conversation.archived',
    'message.accepted','message.completed','message.failed',
    'turn.accepted','turn.running','turn.completed','turn.failed',
    'turn.cancelled','turn.interrupted',
    'brain.responding','plan.created','task.dispatched',
    'agent.accepted','agent.progress','agent.result',
    'task.reviewed','synthesis.started',
    'brain.started','brain.step_started',
    'agent.task_dispatched','agent.task_accepted','agent.task_progress',
    'agent.task_completed','agent.task_failed','agent.task_timed_out',
    'agent.task_unavailable','brain.batch_settled','brain.resumed',
    'brain.user_input_requested','brain.answer_submitted','brain.failed'
  ));

alter table platform_control.worker_heartbeats
  drop constraint worker_heartbeats_worker_name_check,
  add constraint worker_heartbeats_worker_name_check check (worker_name in (
    'dingtalk-directory-event','agent-brain-step','agent-brain-adapter',
    'agent-brain-reaper'
  ));

create function platform_control.upsert_brain_worker_heartbeat_v41(
  selected_worker_name text,
  selected_status text,
  selected_last_error_code text,
  selected_last_seen_at timestamptz
) returns boolean
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  if (
       current_database() = 'agent_platform_control'
       and session_user <> 'platform_brain_worker'
     ) or (
       current_database() = 'agent_platform_control_preview'
       and session_user <> 'platform_brain_worker_preview'
     ) or current_database() not in (
       'agent_platform_control','agent_platform_control_preview'
     )
  then
    raise insufficient_privilege using
      message = 'Brain heartbeat caller invalid';
  end if;
  if selected_worker_name not in (
       'agent-brain-step','agent-brain-adapter','agent-brain-reaper'
     )
     or selected_status not in ('healthy','degraded')
     or selected_last_seen_at is null
     or selected_last_seen_at > clock_timestamp() + interval '10 minutes'
     or (
       selected_last_error_code is not null
       and (
         char_length(selected_last_error_code) not between 1 and 64
         or selected_last_error_code !~ '^[a-z0-9_]+$'
       )
     )
  then
    raise check_violation using message = 'Brain heartbeat invalid';
  end if;
  insert into platform_control.worker_heartbeats (
    worker_name,status,last_error_code,last_seen_at
  ) values (
    selected_worker_name,selected_status,selected_last_error_code,
    selected_last_seen_at
  ) on conflict (worker_name) do update set
    status=excluded.status,
    last_error_code=excluded.last_error_code,
    last_seen_at=excluded.last_seen_at;
  return true;
end
$function$;

create function platform_control.resolve_agent_use_decision_v41(
  selected_user_id uuid,
  selected_agent_id text
) returns table(allowed boolean,directory_generation_id uuid)
language plpgsql
stable
security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  if (
       current_database() = 'agent_platform_control'
       and session_user not in ('platform_control_app','platform_brain_worker')
     ) or (
       current_database() = 'agent_platform_control_preview'
       and session_user not in (
         'platform_control_app_preview','platform_brain_worker_preview'
       )
     ) or current_database() not in (
       'agent_platform_control','agent_platform_control_preview'
     )
  then
    raise insufficient_privilege using
      message = 'Agent use decision caller invalid';
  end if;
  if selected_user_id is null
     or selected_agent_id !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
  then
    raise check_violation using message = 'Agent use decision input invalid';
  end if;
  return query
  select
    platform_control.has_agent_use_scope_v29(
      selected_user_id,selected_agent_id
    ),
    state.active_generation_id
  from platform_control.directory_state state
  where state.singleton=true;
end
$function$;

create function platform_brain.append_agent_task_event_v41(
  selected_task_id uuid,
  selected_seq integer,
  selected_event_type text,
  selected_payload_ciphertext bytea,
  selected_payload_key_version integer,
  selected_payload_sha256 bytea,
  selected_created_at timestamptz,
  selected_terminal_status text,
  selected_result_ciphertext bytea,
  selected_result_key_version integer,
  selected_result_sha256 bytea
) returns boolean
language plpgsql
security definer
set search_path = pg_catalog, platform_brain
as $function$
declare
  current_task_status text;
  current_tool_call_id uuid;
  existing_event platform_brain.agent_task_events%rowtype;
  existing_result_sha256 bytea;
  previous_seq integer;
begin
  if (
       current_database() = 'agent_platform_control'
       and session_user <> 'platform_brain_worker'
     ) or (
       current_database() = 'agent_platform_control_preview'
       and session_user <> 'platform_brain_worker_preview'
     ) or current_database() not in (
       'agent_platform_control','agent_platform_control_preview'
     )
  then
    raise insufficient_privilege using
      message = 'Brain task event caller invalid';
  end if;
  if selected_seq <= 0
     or selected_event_type !~ '^[a-z][a-z0-9_.-]{0,63}$'
     or octet_length(selected_payload_ciphertext) < 29
     or selected_payload_key_version <= 0
     or octet_length(selected_payload_sha256) <> 32
     or selected_created_at is null
     or (
       selected_terminal_status is not null
       and selected_terminal_status not in (
         'completed','failed','cancelled','timed_out','unavailable'
       )
     )
     or (
       (selected_terminal_status is null)
         <> (
           selected_result_ciphertext is null
           and selected_result_key_version is null
           and selected_result_sha256 is null
         )
     )
     or (
       selected_terminal_status is not null
       and (
         octet_length(selected_result_ciphertext) < 29
         or selected_result_key_version <= 0
         or octet_length(selected_result_sha256) <> 32
       )
     )
  then
    raise check_violation using message = 'Brain task event invalid';
  end if;

  select status,brain_tool_call_id into current_task_status,current_tool_call_id
  from platform_brain.agent_tasks
  where task_id=selected_task_id
  for update;
  if not found then
    raise no_data_found using message = 'Brain task missing';
  end if;

  select * into existing_event
  from platform_brain.agent_task_events
  where task_id=selected_task_id and seq=selected_seq;
  if found then
    select result_sha256 into existing_result_sha256
    from platform_brain.brain_tool_calls
    where brain_tool_call_id=current_tool_call_id;
    if existing_event.event_type=selected_event_type
       and existing_event.payload_sha256=selected_payload_sha256
       and existing_event.created_at=selected_created_at
       and (
         selected_terminal_status is null
         or (
           current_task_status=selected_terminal_status
           and existing_result_sha256=selected_result_sha256
         )
       )
    then
      return false;
    end if;
    raise check_violation using message = 'Brain task event conflict';
  end if;

  if current_task_status in (
       'completed','failed','cancelled','timed_out','unavailable'
     )
  then
    raise check_violation using message = 'Brain task already terminal';
  end if;
  select coalesce(max(seq),0) into previous_seq
  from platform_brain.agent_task_events where task_id=selected_task_id;
  if selected_seq <> previous_seq + 1 then
    raise check_violation using message = 'Brain task event sequence invalid';
  end if;

  insert into platform_brain.agent_task_events (
    task_id,seq,event_type,payload_ciphertext,payload_key_version,
    payload_sha256,created_at
  ) values (
    selected_task_id,selected_seq,selected_event_type,
    selected_payload_ciphertext,selected_payload_key_version,
    selected_payload_sha256,selected_created_at
  );

  if selected_terminal_status is null then
    update platform_brain.agent_tasks set
      status='running',started_at=coalesce(started_at,clock_timestamp()),
      updated_at=clock_timestamp(),row_version=row_version+1
    where task_id=selected_task_id and status='queued';
  else
    update platform_brain.agent_tasks set
      status=selected_terminal_status,
      started_at=coalesce(started_at,clock_timestamp()),
      terminal_at=clock_timestamp(),updated_at=clock_timestamp(),
      row_version=row_version+1
    where task_id=selected_task_id;
    update platform_brain.brain_tool_calls set
      status='result_ready',result_ciphertext=selected_result_ciphertext,
      result_key_version=selected_result_key_version,
      result_sha256=selected_result_sha256,updated_at=clock_timestamp()
    where brain_tool_call_id=current_tool_call_id;
  end if;
  return true;
end
$function$;

create function platform_control.enqueue_brain_relay_job_v41(
  selected_job_id uuid,
  selected_run_id uuid,
  selected_agent_id text,
  selected_payload_ciphertext bytea,
  selected_key_version integer
) returns uuid
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  if (
       current_database() = 'agent_platform_control'
       and session_user <> 'platform_brain_worker'
     ) or (
       current_database() = 'agent_platform_control_preview'
       and session_user <> 'platform_brain_worker_preview'
     ) or current_database() not in (
       'agent_platform_control','agent_platform_control_preview'
     )
  then raise insufficient_privilege using message='Brain relay caller invalid';
  end if;
  if selected_job_id is null or selected_run_id is null
     or selected_agent_id not in (
       'hr-bot','fae-bot','marketing-prospecting-bot',
       'marketing-inbound-bot','marketing-voice-bot',
       'marketing-intelligence-bot','marketing-gtm-bot'
     )
     or octet_length(selected_payload_ciphertext) not between 29 and 1048576
     or selected_key_version <= 0
  then raise check_violation using message='Brain relay job invalid';
  end if;
  insert into platform_control.execution_jobs (
    job_id,run_id,agent_id,payload_ciphertext,encryption_key_version,
    status,cancel_requested,job_kind
  ) values (
    selected_job_id,selected_run_id,selected_agent_id,
    selected_payload_ciphertext,selected_key_version,'queued',false,
    'metabot_local'
  );
  return selected_job_id;
end
$function$;

create function platform_control.brain_relay_worker_available_v41(
  selected_agent_id text,
  selected_freshness_seconds integer
) returns boolean
language plpgsql
stable
security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  if (
       current_database() = 'agent_platform_control'
       and session_user <> 'platform_brain_worker'
     ) or (
       current_database() = 'agent_platform_control_preview'
       and session_user <> 'platform_brain_worker_preview'
     ) or current_database() not in (
       'agent_platform_control','agent_platform_control_preview'
     )
  then raise insufficient_privilege using message='Brain relay caller invalid';
  end if;
  if selected_agent_id !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
     or selected_freshness_seconds not between 1 and 3600
  then raise check_violation using message='Brain relay worker query invalid';
  end if;
  return exists(
    select 1 from platform_control.execution_workers
    where status='active' and selected_agent_id=any(allowed_agent_ids)
      and last_seen_at>clock_timestamp()
        -(selected_freshness_seconds*interval '1 second')
  );
end
$function$;

create function platform_control.brain_relay_job_state_v41(
  selected_run_id uuid
) returns table(
  run_id uuid,status text,cancel_requested boolean,created_at timestamptz,
  updated_at timestamptz,lease_expires_at timestamptz,terminal_at timestamptz,
  stop_requested_status text,job_kind text,database_now timestamptz
)
language plpgsql
stable
security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  if (
       current_database() = 'agent_platform_control'
       and session_user <> 'platform_brain_worker'
     ) or (
       current_database() = 'agent_platform_control_preview'
       and session_user <> 'platform_brain_worker_preview'
     ) or current_database() not in (
       'agent_platform_control','agent_platform_control_preview'
     )
  then raise insufficient_privilege using message='Brain relay caller invalid';
  end if;
  return query select
    job.run_id,job.status,job.cancel_requested,job.created_at,job.updated_at,
    job.lease_expires_at,job.terminal_at,job.stop_requested_status,
    job.job_kind,clock_timestamp()
  from platform_control.execution_jobs job
  where job.run_id=selected_run_id and job.job_kind='metabot_local';
end
$function$;

create function platform_control.brain_relay_events_v41(
  selected_run_id uuid
) returns table(
  seq integer,event_type text,payload_ciphertext bytea,
  encryption_key_version integer,created_at timestamptz
)
language plpgsql
stable
security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  if (
       current_database() = 'agent_platform_control'
       and session_user <> 'platform_brain_worker'
     ) or (
       current_database() = 'agent_platform_control_preview'
       and session_user <> 'platform_brain_worker_preview'
     ) or current_database() not in (
       'agent_platform_control','agent_platform_control_preview'
     )
  then raise insufficient_privilege using message='Brain relay caller invalid';
  end if;
  if not exists(
    select 1 from platform_control.execution_jobs job
    where job.run_id=selected_run_id and job.job_kind='metabot_local'
  ) then raise no_data_found using message='Brain relay job missing';
  end if;
  return query select
    event.seq,event.event_type,event.payload_ciphertext,
    event.encryption_key_version,event.created_at
  from platform_control.execution_events event
  where event.run_id=selected_run_id order by event.seq;
end
$function$;

create function platform_control.request_brain_relay_cancel_v41(
  selected_run_id uuid
) returns boolean
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  selected_job platform_control.execution_jobs%rowtype;
begin
  if (
       current_database() = 'agent_platform_control'
       and session_user <> 'platform_brain_worker'
     ) or (
       current_database() = 'agent_platform_control_preview'
       and session_user <> 'platform_brain_worker_preview'
     ) or current_database() not in (
       'agent_platform_control','agent_platform_control_preview'
     )
  then
    raise insufficient_privilege using
      message = 'Brain relay cancellation caller invalid';
  end if;
  select * into selected_job from platform_control.execution_jobs
  where run_id=selected_run_id and job_kind='metabot_local' for update;
  if not found or selected_job.status in (
       'completed','failed','cancelled','interrupted'
     )
  then
    return false;
  end if;
  if selected_job.status='queued' then
    update platform_control.execution_jobs set
      status='cancelled',cancel_requested=true,terminal_at=clock_timestamp(),
      updated_at=clock_timestamp()
    where run_id=selected_run_id;
  else
    update platform_control.execution_jobs set
      cancel_requested=true,stop_requested_status='cancelled',
      stop_acknowledged_at=null,updated_at=clock_timestamp()
    where run_id=selected_run_id;
  end if;
  return true;
end
$function$;

revoke all on all tables in schema platform_brain from public;
revoke all on function platform_control.upsert_brain_worker_heartbeat_v41(
  text,text,text,timestamptz
) from public;
revoke all on function platform_control.resolve_agent_use_decision_v41(
  uuid,text
) from public;
revoke all on function platform_brain.append_agent_task_event_v41(
  uuid,integer,text,bytea,integer,bytea,timestamptz,text,bytea,integer,bytea
) from public;
revoke all on function platform_control.request_brain_relay_cancel_v41(uuid)
from public;
revoke all on function platform_control.enqueue_brain_relay_job_v41(
  uuid,uuid,text,bytea,integer
) from public;
revoke all on function platform_control.brain_relay_worker_available_v41(
  text,integer
) from public;
revoke all on function platform_control.brain_relay_job_state_v41(uuid)
from public;
revoke all on function platform_control.brain_relay_events_v41(uuid)
from public;

do $migration$
declare
  selected_app name;
  selected_brain name;
  role_name name;
begin
  if current_database() = 'agent_platform_control'
     and current_user = 'platform_control_owner'
  then
    selected_app := 'platform_control_app';
    selected_brain := 'platform_brain_worker';
  elsif current_database() = 'agent_platform_control_preview'
     and current_user = 'platform_control_owner_preview'
  then
    selected_app := 'platform_control_app_preview';
    selected_brain := 'platform_brain_worker_preview';
  else
    raise insufficient_privilege using
      message = 'Brain migration owner/environment mismatch';
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
    execute format('revoke all on schema platform_brain from %I', role_name);
    execute format(
      'revoke all on all tables in schema platform_brain from %I', role_name
    );
    execute format(
      'revoke all on function '
      'platform_control.upsert_brain_worker_heartbeat_v41('
      'text,text,text,timestamptz) from %I', role_name
    );
    execute format(
      'revoke all on function '
      'platform_control.resolve_agent_use_decision_v41(uuid,text) from %I',
      role_name
    );
    execute format(
      'revoke all on function '
      'platform_brain.append_agent_task_event_v41('
      'uuid,integer,text,bytea,integer,bytea,timestamptz,text,bytea,integer,bytea) '
      'from %I', role_name
    );
    execute format(
      'revoke all on function '
      'platform_control.request_brain_relay_cancel_v41(uuid) from %I',
      role_name
    );
    execute format(
      'revoke all on function '
      'platform_control.enqueue_brain_relay_job_v41('
      'uuid,uuid,text,bytea,integer) from %I', role_name
    );
    execute format(
      'revoke all on function '
      'platform_control.brain_relay_worker_available_v41(text,integer) '
      'from %I', role_name
    );
    execute format(
      'revoke all on function '
      'platform_control.brain_relay_job_state_v41(uuid) from %I', role_name
    );
    execute format(
      'revoke all on function '
      'platform_control.brain_relay_events_v41(uuid) from %I', role_name
    );
  end loop;

  execute format('grant usage on schema platform_brain to %I', selected_app);
  execute format('grant usage on schema platform_brain to %I', selected_brain);
  execute format('grant usage on schema platform_control to %I', selected_brain);
  execute format(
    'grant select on all tables in schema platform_brain to %I',
    selected_brain
  );
  execute format(
    'grant select on platform_control.worker_heartbeats to %I', selected_brain
  );
  execute format(
    'grant insert on platform_brain.authorization_snapshots, '
    'platform_brain.brain_loops, platform_brain.brain_steps, '
    'platform_brain.brain_tool_calls, platform_brain.agent_tasks, '
    'platform_brain.adapter_deliveries, platform_brain.brain_checkpoints to %I',
    selected_brain
  );
  execute format(
    'grant update on platform_brain.brain_loops, '
    'platform_brain.brain_steps, platform_brain.brain_tool_calls, '
    'platform_brain.adapter_deliveries, platform_brain.brain_checkpoints to %I',
    selected_brain
  );
  execute format(
    'grant select on platform_brain.brain_loops, '
    'platform_brain.brain_steps, platform_brain.brain_tool_calls, '
    'platform_brain.agent_tasks, platform_brain.agent_task_events to %I',
    selected_app
  );
  execute format(
    'grant insert on platform_brain.authorization_snapshots, '
    'platform_brain.brain_loops, platform_brain.brain_steps to %I', selected_app
  );
  execute format(
    'grant update (cancel_requested,updated_at,row_version) '
    'on platform_brain.brain_loops to %I', selected_app
  );
  execute format(
    'grant update (status,active_started_at,active_deadline_at,'
    'waiting_user_expires_at,updated_at,row_version) '
    'on platform_brain.brain_loops to %I', selected_app
  );
  execute format(
    'grant update (status,terminal_at,updated_at) '
    'on platform_brain.brain_steps to %I', selected_app
  );
  execute format(
    'grant update (status,result_ciphertext,result_key_version,result_sha256,'
    'updated_at) on platform_brain.brain_tool_calls to %I', selected_app
  );
  execute format(
    'grant select,insert,update on platform_control.conversations, '
    'platform_control.conversation_messages, '
    'platform_control.conversation_turns, '
    'platform_control.conversation_events to %I', selected_brain
  );
  execute format(
    'grant execute on function '
    'platform_control.upsert_brain_worker_heartbeat_v41('
    'text,text,text,timestamptz) to %I', selected_brain
  );
  execute format(
    'grant execute on function '
    'platform_control.resolve_agent_use_decision_v41(uuid,text) to %I',
    selected_app
  );
  execute format(
    'grant execute on function '
    'platform_control.resolve_agent_use_decision_v41(uuid,text) to %I',
    selected_brain
  );
  execute format(
    'grant execute on function '
    'platform_control.request_brain_relay_cancel_v41(uuid) to %I',
    selected_brain
  );
  execute format(
    'grant execute on function '
    'platform_control.enqueue_brain_relay_job_v41('
    'uuid,uuid,text,bytea,integer) to %I', selected_brain
  );
  execute format(
    'grant execute on function '
    'platform_control.brain_relay_worker_available_v41(text,integer) to %I',
    selected_brain
  );
  execute format(
    'grant execute on function '
    'platform_control.brain_relay_job_state_v41(uuid) to %I', selected_brain
  );
  execute format(
    'grant execute on function '
    'platform_control.brain_relay_events_v41(uuid) to %I', selected_brain
  );
  execute format(
    'grant execute on function '
    'platform_brain.append_agent_task_event_v41('
    'uuid,integer,text,bytea,integer,bytea,timestamptz,text,bytea,integer,bytea) '
    'to %I', selected_brain
  );
end
$migration$;

comment on schema platform_brain is
  'Durable Agent Brain execution state. Conversation system of record remains '
  'in platform_control; checkpoints are disposable caches.';
