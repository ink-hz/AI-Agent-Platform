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
end
$migration$;

comment on table platform_brain.agent_task_sessions is
  'Canonical professional-Agent child session for one durable Brain task.';
comment on table platform_brain.brain_thinking_summaries is
  'Encrypted Provider-native summarized thinking; excluded from flywheel and search.';
