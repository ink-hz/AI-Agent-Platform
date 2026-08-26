create table platform_brain.agent_task_sessions (
  task_id uuid primary key
    references platform_brain.agent_tasks(task_id),
  child_session_id text not null unique
    check (char_length(child_session_id) between 16 and 256),
  adapter_kind text not null
    check (adapter_kind ~ '^[a-z][a-z0-9_]{0,63}$'),
  adapter_session_ref_ciphertext bytea,
  adapter_session_ref_key_version integer check (
    adapter_session_ref_key_version is null
    or adapter_session_ref_key_version > 0
  ),
  status text not null check (
    status in ('active','completed','failed','cancelled')
  ),
  capability_snapshot jsonb not null check (
    jsonb_typeof(capability_snapshot) = 'object'
  ),
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  terminal_at timestamptz,
  check (
    (adapter_session_ref_ciphertext is null)
      = (adapter_session_ref_key_version is null)
  ),
  check ((status = 'active') = (terminal_at is null))
);

create unique index one_task_session
  on platform_brain.agent_task_sessions(task_id);

create table platform_brain.agent_task_messages (
  task_id uuid not null
    references platform_brain.agent_tasks(task_id),
  seq integer not null check (seq > 0),
  sender text not null check (sender in ('brain','agent')),
  message_kind text not null check (
    message_kind in ('initial','followup','question','reply','result')
  ),
  content_ciphertext bytea not null check (
    octet_length(content_ciphertext) between 29 and 131072
  ),
  content_key_version integer not null check (content_key_version > 0),
  content_sha256 bytea not null check (octet_length(content_sha256) = 32),
  provider_run_ref text check (
    provider_run_ref is null
    or char_length(provider_run_ref) between 1 and 256
  ),
  created_at timestamptz not null,
  primary key (task_id, seq)
);

create table platform_brain.brain_thinking_summaries (
  step_id uuid not null references platform_brain.brain_steps(step_id),
  block_index integer not null check (block_index >= 0),
  last_delta_seq integer not null default 0 check (last_delta_seq >= 0),
  summary_ciphertext bytea not null check (
    octet_length(summary_ciphertext) between 29 and 1048576
  ),
  summary_key_version integer not null check (summary_key_version > 0),
  source text not null check (source = 'provider'),
  provider_run_ref text not null check (
    char_length(provider_run_ref) between 1 and 256
  ),
  status text not null check (
    status in ('streaming','completed','interrupted')
  ),
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  primary key (step_id, block_index)
);

create table platform_brain.brain_wait_subscriptions (
  wait_id uuid primary key,
  brain_tool_call_id uuid not null unique
    references platform_brain.brain_tool_calls(brain_tool_call_id),
  loop_id uuid not null references platform_brain.brain_loops(loop_id),
  task_ids uuid[] not null check (
    cardinality(task_ids) between 1 and 8
    and array_position(task_ids, null) is null
  ),
  wake_on text[] not null check (
    cardinality(wake_on) between 1 and 5
    and wake_on <@ array['question','finding','result','failed','timeout']::text[]
    and array_position(wake_on, null) is null
  ),
  cursors jsonb not null check (jsonb_typeof(cursors) = 'object'),
  status text not null check (
    status in ('active','triggered','cancelled','expired')
  ),
  triggered_task_id uuid references platform_brain.agent_tasks(task_id),
  triggered_event_seq integer check (
    triggered_event_seq is null or triggered_event_seq > 0
  ),
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  terminal_at timestamptz,
  check (
    (triggered_task_id is null) = (triggered_event_seq is null)
  ),
  check (
    (status = 'active' and terminal_at is null
      and triggered_task_id is null)
    or (status = 'triggered' and terminal_at is not null
      and triggered_task_id is not null)
    or (status in ('cancelled','expired') and terminal_at is not null
      and triggered_task_id is null)
  )
);

create unique index one_active_wait_subscription
  on platform_brain.brain_wait_subscriptions(loop_id)
  where status = 'active';

create table platform_brain.brain_user_interventions (
  intervention_id uuid primary key,
  loop_id uuid not null references platform_brain.brain_loops(loop_id),
  message_id uuid not null unique
    references platform_control.conversation_messages(message_id),
  content_ciphertext bytea not null check (
    octet_length(content_ciphertext) between 29 and 131072
  ),
  content_key_version integer not null check (content_key_version > 0),
  content_sha256 bytea not null check (octet_length(content_sha256) = 32),
  status text not null check (status in ('pending','consumed','rejected')),
  consumed_by_step_id uuid references platform_brain.brain_steps(step_id),
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  terminal_at timestamptz,
  check (
    (status = 'pending' and consumed_by_step_id is null and terminal_at is null)
    or (status = 'consumed' and consumed_by_step_id is not null
      and terminal_at is not null)
    or (status = 'rejected' and consumed_by_step_id is null
      and terminal_at is not null)
  )
);

drop index platform_brain.one_active_adapter_delivery;

alter table platform_brain.adapter_deliveries
  drop constraint adapter_deliveries_task_id_attempt_key,
  add column delivery_kind text not null default 'initial',
  add column source_message_seq integer,
  add constraint adapter_deliveries_delivery_kind_v45 check (
    delivery_kind in ('initial','followup','stop')
  ),
  add constraint adapter_deliveries_source_message_v45 check (
    (delivery_kind = 'followup' and source_message_seq > 0)
    or (delivery_kind in ('initial','stop') and source_message_seq is null)
  ),
  add constraint adapter_deliveries_attempt_v45 unique nulls not distinct
    (task_id,delivery_kind,source_message_seq,attempt);

create unique index adapter_delivery_identity_v45
  on platform_brain.adapter_deliveries(
    task_id,delivery_kind,source_message_seq
  ) nulls not distinct;

create index leaseable_adapter_deliveries_v45
  on platform_brain.adapter_deliveries(status,created_at,delivery_id)
  where status = 'queued';

alter table platform_brain.brain_tool_calls
  drop constraint brain_tool_calls_tool_name_check,
  add constraint brain_tool_calls_tool_name_check check (tool_name in (
    'list_agents','delegate_task','await_agent_events',
    'send_agent_message','stop_agent_task','request_user_input','submit_answer'
  ));

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
    'brain.user_input_requested','brain.answer_submitted','brain.failed',
    'brain.thinking_summary','brain.waiting_agents',
    'brain.user_intervention','brain.agent_message_sent',
    'brain.agent_stop_requested','agent.thinking_summary','agent.message',
    'agent.work_update','agent.artifact','agent.question','agent.cancelled',
    'agent.task_recovered'
  ));

create function platform_brain.append_agent_task_event_v45(
  selected_task_id uuid,
  selected_seq integer,
  selected_event_type text,
  selected_payload_ciphertext bytea,
  selected_payload_key_version integer,
  selected_payload_sha256 bytea,
  selected_created_at timestamptz
) returns boolean
language plpgsql
security definer
set search_path = pg_catalog, platform_brain
as $function$
declare
  existing_event platform_brain.agent_task_events%rowtype;
  previous_seq integer;
  current_status text;
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
      message = 'Brain collaboration event caller invalid';
  end if;
  if selected_seq <= 0
     or selected_event_type not in (
       'thinking_summary','message','work_update','artifact','question',
       'finding','result','failed','timeout','cancelled'
     )
     or octet_length(selected_payload_ciphertext) < 29
     or selected_payload_key_version <= 0
     or octet_length(selected_payload_sha256) <> 32
     or selected_created_at is null
  then
    raise check_violation using message = 'Brain collaboration event invalid';
  end if;

  select status into current_status
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
    if existing_event.event_type=selected_event_type
       and existing_event.payload_sha256=selected_payload_sha256
       and existing_event.created_at=selected_created_at
    then
      return false;
    end if;
    raise check_violation using message = 'Brain collaboration event conflict';
  end if;

  select coalesce(max(seq),0) into previous_seq
  from platform_brain.agent_task_events where task_id=selected_task_id;
  if selected_seq <> previous_seq + 1 then
    raise check_violation using
      message = 'Brain collaboration event sequence invalid';
  end if;

  insert into platform_brain.agent_task_events (
    task_id,seq,event_type,payload_ciphertext,payload_key_version,
    payload_sha256,created_at
  ) values (
    selected_task_id,selected_seq,selected_event_type,
    selected_payload_ciphertext,selected_payload_key_version,
    selected_payload_sha256,selected_created_at
  );

  if selected_event_type in ('result','failed','timeout','cancelled') then
    update platform_brain.agent_tasks set
      status=case selected_event_type
        when 'result' then 'completed'
        when 'failed' then 'failed'
        when 'timeout' then 'timed_out'
        else 'cancelled'
      end,
      started_at=coalesce(started_at,clock_timestamp()),
      terminal_at=coalesce(terminal_at,clock_timestamp()),
      updated_at=clock_timestamp(),row_version=row_version+1
    where task_id=selected_task_id;
    if selected_event_type in ('failed','timeout','cancelled') then
      update platform_brain.agent_task_sessions set
        status=case selected_event_type
          when 'cancelled' then 'cancelled'
          else 'failed'
        end,
        terminal_at=coalesce(terminal_at,clock_timestamp()),
        updated_at=clock_timestamp()
      where task_id=selected_task_id and status='active';
    end if;
  elsif current_status='queued' then
    update platform_brain.agent_tasks set
      status='running',started_at=coalesce(started_at,clock_timestamp()),
      updated_at=clock_timestamp(),row_version=row_version+1
    where task_id=selected_task_id;
  end if;
  return true;
end
$function$;

create function platform_brain.mark_adapter_delivery_dispatched_v45(
  selected_delivery_id uuid,
  selected_task_id uuid
) returns boolean
language plpgsql
security definer
set search_path = pg_catalog, platform_brain
as $function$
declare
  selected_kind text;
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
      message = 'Brain collaboration delivery caller invalid';
  end if;

  update platform_brain.adapter_deliveries set
    status='dispatched',lease_worker_id=null,lease_expires_at=null,
    updated_at=clock_timestamp()
  where delivery_id=selected_delivery_id and task_id=selected_task_id
    and delivery_kind='initial' and status='leased'
  returning delivery_kind into selected_kind;
  if selected_kind is null then
    return false;
  end if;

  update platform_brain.agent_tasks set
    status='running',started_at=coalesce(started_at,clock_timestamp()),
    updated_at=clock_timestamp(),row_version=row_version+1
  where task_id=selected_task_id and status='queued';
  if not found then
    raise check_violation using
      message = 'Brain collaboration task dispatch invalid';
  end if;
  return true;
end
$function$;

revoke all on function platform_brain.append_agent_task_event_v45(
  uuid,integer,text,bytea,integer,bytea,timestamptz
) from public;
revoke all on function platform_brain.mark_adapter_delivery_dispatched_v45(
  uuid,uuid
) from public;

revoke all on table
  platform_brain.agent_task_sessions,
  platform_brain.agent_task_messages,
  platform_brain.brain_thinking_summaries,
  platform_brain.brain_wait_subscriptions,
  platform_brain.brain_user_interventions
from public;

do $migration$
declare
  selected_app text;
  selected_brain text;
begin
  if current_database() = 'agent_platform_control' then
    selected_app := 'platform_control_app';
    selected_brain := 'platform_brain_worker';
  elsif current_database() = 'agent_platform_control_preview' then
    selected_app := 'platform_control_app_preview';
    selected_brain := 'platform_brain_worker_preview';
  else
    raise insufficient_privilege using
      message = 'Live collaboration database invalid';
  end if;

  execute format(
    'grant select,insert,update on '
    'platform_brain.agent_task_sessions, '
    'platform_brain.brain_thinking_summaries, '
    'platform_brain.brain_wait_subscriptions to %I',
    selected_brain
  );
  execute format(
    'grant select,insert on platform_brain.agent_task_messages to %I',
    selected_brain
  );
  execute format(
    'grant select,update on platform_brain.brain_user_interventions to %I',
    selected_brain
  );
  execute format(
    'grant select on platform_brain.agent_task_sessions, '
    'platform_brain.agent_task_messages, '
    'platform_brain.brain_thinking_summaries, '
    'platform_brain.brain_wait_subscriptions to %I',
    selected_app
  );
  execute format(
    'grant select,insert on platform_brain.brain_user_interventions to %I',
    selected_app
  );
  execute format(
    'grant execute on function '
    'platform_brain.append_agent_task_event_v45('
    'uuid,integer,text,bytea,integer,bytea,timestamptz) to %I',
    selected_brain
  );
  execute format(
    'grant execute on function '
    'platform_brain.mark_adapter_delivery_dispatched_v45(uuid,uuid) to %I',
    selected_brain
  );
end
$migration$;

comment on table platform_brain.agent_task_sessions is
  'Canonical professional-Agent child session for one durable Brain task.';
comment on table platform_brain.brain_thinking_summaries is
  'Encrypted Provider-native summarized thinking; excluded from flywheel and search.';
