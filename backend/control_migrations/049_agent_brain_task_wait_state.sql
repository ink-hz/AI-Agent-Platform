alter table platform_brain.agent_tasks
  add column dispatched_at timestamptz,
  add column active_elapsed_ms bigint not null default 0,
  add column terminal_reason_code text;

update platform_brain.agent_tasks
set dispatched_at=coalesce(dispatched_at,started_at,created_at)
where status <> 'queued';

alter table platform_brain.agent_tasks
  drop constraint agent_tasks_status_check,
  add constraint agent_tasks_status_check check (status in (
    'queued','dispatched','running','waiting_input','waiting_confirmation',
    'completed','failed','cancelled','timed_out','unavailable'
  )),
  add constraint agent_tasks_active_elapsed_v49 check (
    active_elapsed_ms >= 0
  ),
  add constraint agent_tasks_terminal_reason_v49 check (
    terminal_reason_code is null
    or terminal_reason_code ~ '^[a-z][a-z0-9_]{0,63}$'
  );

do $migration$
declare
  selected_constraint name;
begin
  select conname into selected_constraint
  from pg_constraint
  where conrelid='platform_brain.agent_tasks'::regclass
    and contype='c'
    and pg_get_constraintdef(oid) like '%started_at%'
    and pg_get_constraintdef(oid) like '%terminal_at%'
    and pg_get_constraintdef(oid) like '%status%'
    and conname <> 'agent_tasks_status_check'
  order by conname
  limit 1;
  if selected_constraint is null then
    raise check_violation using
      message = 'Agent task state constraint missing';
  end if;
  execute format(
    'alter table platform_brain.agent_tasks drop constraint %I',
    selected_constraint
  );
end
$migration$;

alter table platform_brain.agent_tasks
  add constraint agent_tasks_state_v49 check (
    (
      status='queued'
      and dispatched_at is null
      and started_at is null
      and terminal_at is null
      and terminal_reason_code is null
    ) or (
      status='dispatched'
      and dispatched_at is not null
      and started_at is null
      and terminal_at is null
      and terminal_reason_code is null
    ) or (
      status in ('running','waiting_input','waiting_confirmation')
      and started_at is not null
      and terminal_at is null
      and terminal_reason_code is null
    ) or (
      status in ('completed','failed','cancelled','timed_out','unavailable')
      and terminal_at is not null
    )
  ),
  add constraint agent_tasks_terminal_reason_status_v49 check (
    terminal_reason_code is null
    or status in ('failed','cancelled','timed_out','unavailable')
  );

alter table platform_brain.brain_loops
  add column intervention_expires_at timestamptz,
  drop constraint brain_loops_status_check,
  add constraint brain_loops_status_check check (status in (
    'queued','running','waiting_agents','waiting_user',
    'waiting_confirmation','completing',
    'completed','failed','cancelled','interrupted'
  ));

do $migration$
declare
  selected_constraint name;
begin
  select conname into selected_constraint
  from pg_constraint
  where conrelid='platform_brain.brain_loops'::regclass
    and contype='c'
    and pg_get_constraintdef(oid) like '%active_started_at%'
    and pg_get_constraintdef(oid) like '%waiting_user_expires_at%'
  order by conname
  limit 1;
  if selected_constraint is null then
    raise check_violation using
      message = 'Brain loop active-clock constraint missing';
  end if;
  execute format(
    'alter table platform_brain.brain_loops drop constraint %I',
    selected_constraint
  );
end
$migration$;

alter table platform_brain.brain_loops
  add constraint brain_loops_active_clock_v49 check (
    (
      status='waiting_user'
      and active_started_at is null
      and active_deadline_at is null
      and waiting_user_expires_at is not null
      and intervention_expires_at is null
    ) or (
      status='waiting_confirmation'
      and active_started_at is null
      and active_deadline_at is null
      and waiting_user_expires_at is null
      and intervention_expires_at is not null
    ) or (
      status in ('running','waiting_agents','completing')
      and active_started_at is not null
      and active_deadline_at is not null
      and waiting_user_expires_at is null
      and intervention_expires_at is null
    ) or (
      status in ('queued','completed','failed','cancelled','interrupted')
      and active_started_at is null
      and active_deadline_at is null
      and waiting_user_expires_at is null
      and intervention_expires_at is null
    )
  );

alter table platform_control.conversation_turns
  drop constraint conversation_turns_status_check,
  add constraint conversation_turns_status_check check (status in (
    'accepted','running','waiting_agents','waiting_user',
    'waiting_confirmation','completing',
    'completed','failed','cancelled','interrupted'
  ));

drop index platform_control.one_active_conversation_turn;
create unique index one_active_conversation_turn
  on platform_control.conversation_turns(conversation_id)
  where status in (
    'accepted','running','waiting_agents','waiting_user',
    'waiting_confirmation','completing'
  );

create table platform_brain.brain_task_event_cursors (
  task_id uuid primary key
    references platform_brain.agent_tasks(task_id),
  loop_id uuid not null
    references platform_brain.brain_loops(loop_id),
  delivered_seq integer not null default 0 check (delivered_seq >= 0),
  updated_at timestamptz not null default clock_timestamp()
);

insert into platform_brain.brain_task_event_cursors (
  task_id,loop_id,delivered_seq
)
select
  task.task_id,
  task.loop_id,
  max(coalesce((wait.cursors ->> task.task_id::text)::integer,0))
from platform_brain.agent_tasks task
left join platform_brain.brain_wait_subscriptions wait
  on task.loop_id=wait.loop_id and task.task_id=any(wait.task_ids)
group by task.task_id,task.loop_id;

create index brain_task_event_cursors_loop
  on platform_brain.brain_task_event_cursors(loop_id,task_id);

create table platform_brain.agent_runtime_health (
  agent_id text primary key
    check (agent_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'),
  status text not null check (status in ('healthy','unhealthy')),
  reason_code text check (
    reason_code is null or reason_code ~ '^[a-z][a-z0-9_]{0,63}$'
  ),
  source_task_id uuid,
  updated_at timestamptz not null default clock_timestamp(),
  check (
    (status='healthy' and reason_code is null)
    or (status='unhealthy' and reason_code is not null)
  )
);

alter table platform_brain.brain_wait_subscriptions
  drop constraint brain_wait_subscriptions_wake_on_check,
  add constraint brain_wait_subscriptions_wake_on_check check (
    cardinality(wake_on) between 1 and 7
    and wake_on <@ array[
      'question','finding','result','failed','timeout',
      'input_required','action_required'
    ]::text[]
    and array_position(wake_on,null) is null
  ),
  drop column cursors,
  add column trigger_origin text not null default 'agent_event'
    check (trigger_origin in ('agent_event','platform_control'));

do $migration$
declare
  selected_constraint name;
begin
  select conname into selected_constraint
  from pg_constraint
  where conrelid='platform_brain.brain_wait_subscriptions'::regclass
    and contype='c'
    and pg_get_constraintdef(oid) like '%triggered_task_id%'
    and pg_get_constraintdef(oid) like '%triggered_event_seq%'
  order by conname
  limit 1;
  if selected_constraint is null then
    raise check_violation using
      message = 'Brain wait terminal-state constraint missing';
  end if;
  execute format(
    'alter table platform_brain.brain_wait_subscriptions drop constraint %I',
    selected_constraint
  );
end
$migration$;

alter table platform_brain.brain_wait_subscriptions
  add constraint brain_wait_subscriptions_terminal_v49 check (
    (status='active' and terminal_at is null
      and triggered_task_id is null and triggered_event_seq is null)
    or (status='triggered' and terminal_at is not null
      and triggered_task_id is not null
      and (
        (trigger_origin='agent_event' and triggered_event_seq is not null)
        or (trigger_origin='platform_control' and triggered_event_seq is null)
      ))
    or (status in ('cancelled','expired') and terminal_at is not null
      and triggered_task_id is null and triggered_event_seq is null)
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
    'brain.user_input_requested','brain.answer_submitted','brain.failed',
    'brain.thinking_summary','brain.waiting_agents',
    'brain.user_intervention','brain.agent_message_sent',
    'brain.agent_stop_requested','agent.thinking_summary','agent.message',
    'agent.work_update','agent.artifact','agent.question','agent.cancelled',
    'agent.task_recovered','agent.input_required','agent.action_required'
  ));

create function platform_brain.append_agent_task_event_v49(
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
      message = 'Brain task event v49 caller invalid';
  end if;
  if selected_seq <= 0
     or selected_event_type not in (
       'thinking_summary','message','work_update','artifact','question',
       'finding','result','failed','timeout','cancelled',
       'input_required','action_required'
     )
     or octet_length(selected_payload_ciphertext) < 29
     or selected_payload_key_version <= 0
     or octet_length(selected_payload_sha256) <> 32
     or selected_created_at is null
  then
    raise check_violation using message = 'Brain task event v49 invalid';
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
    raise check_violation using message = 'Brain task event v49 conflict';
  end if;

  if current_status in (
       'completed','failed','cancelled','timed_out','unavailable'
     )
  then
    raise check_violation using message = 'Brain task already terminal';
  end if;

  select coalesce(max(seq),0) into previous_seq
  from platform_brain.agent_task_events where task_id=selected_task_id;
  if selected_seq <> previous_seq + 1 then
    raise check_violation using message = 'Brain task event v49 sequence invalid';
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
      dispatched_at=coalesce(dispatched_at,created_at),
      started_at=coalesce(started_at,clock_timestamp()),
      terminal_reason_code=null,
      terminal_at=clock_timestamp(),
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
  elsif selected_event_type='work_update' then
    update platform_brain.agent_tasks set
      status='running',
      dispatched_at=coalesce(dispatched_at,created_at),
      started_at=coalesce(started_at,clock_timestamp()),
      updated_at=clock_timestamp(),row_version=row_version+1
    where task_id=selected_task_id
      and status in ('queued','dispatched','waiting_input','waiting_confirmation');
  elsif selected_event_type='input_required' then
    update platform_brain.agent_tasks set
      status='waiting_input',
      dispatched_at=coalesce(dispatched_at,created_at),
      started_at=coalesce(started_at,clock_timestamp()),
      updated_at=clock_timestamp(),row_version=row_version+1
    where task_id=selected_task_id;
  elsif selected_event_type='action_required' then
    update platform_brain.agent_tasks set
      status='waiting_confirmation',
      dispatched_at=coalesce(dispatched_at,created_at),
      started_at=coalesce(started_at,clock_timestamp()),
      updated_at=clock_timestamp(),row_version=row_version+1
    where task_id=selected_task_id;
  end if;
  return true;
end
$function$;

create function platform_brain.mark_adapter_delivery_dispatched_v49(
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
      message = 'Brain collaboration delivery v49 caller invalid';
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
    status='dispatched',
    dispatched_at=coalesce(dispatched_at,clock_timestamp()),
    updated_at=clock_timestamp(),row_version=row_version+1
  where task_id=selected_task_id and status='queued';
  if not found then
    raise check_violation using
      message = 'Brain collaboration task dispatch v49 invalid';
  end if;
  return true;
end
$function$;

create function platform_brain.fail_agent_task_protocol_v49(
  selected_task_id uuid
) returns boolean
language plpgsql
security definer
set search_path = pg_catalog, platform_brain
as $function$
declare
  selected_agent_id text;
  selected_status text;
  selected_reason text;
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
      message = 'Brain task protocol failure caller invalid';
  end if;

  select agent_id,status,terminal_reason_code
  into selected_agent_id,selected_status,selected_reason
  from platform_brain.agent_tasks
  where task_id=selected_task_id
  for update;
  if not found then
    raise no_data_found using message = 'Brain task missing';
  end if;
  if selected_status in (
       'completed','failed','cancelled','timed_out','unavailable'
     )
  then
    return selected_status='failed'
      and selected_reason='protocol_violation';
  end if;

  update platform_brain.agent_tasks set
    status='failed',
    dispatched_at=coalesce(dispatched_at,created_at),
    started_at=coalesce(started_at,clock_timestamp()),
    terminal_reason_code='protocol_violation',
    terminal_at=clock_timestamp(),updated_at=clock_timestamp(),
    row_version=row_version+1
  where task_id=selected_task_id;

  update platform_brain.agent_task_sessions set
    status='failed',terminal_at=coalesce(terminal_at,clock_timestamp()),
    updated_at=clock_timestamp()
  where task_id=selected_task_id and status='active';

  update platform_brain.adapter_deliveries set
    status='failed',lease_worker_id=null,lease_expires_at=null,
    terminal_at=coalesce(terminal_at,clock_timestamp()),
    updated_at=clock_timestamp()
  where task_id=selected_task_id
    and status in ('queued','leased','dispatched','expired');

  insert into platform_brain.agent_runtime_health (
    agent_id,status,reason_code,source_task_id,updated_at
  ) values (
    selected_agent_id,'unhealthy','protocol_violation',selected_task_id,
    clock_timestamp()
  ) on conflict (agent_id) do update set
    status=excluded.status,reason_code=excluded.reason_code,
    source_task_id=excluded.source_task_id,updated_at=excluded.updated_at;
  return true;
end
$function$;

revoke all on table platform_brain.brain_task_event_cursors from public;
revoke all on table platform_brain.agent_runtime_health from public;
revoke all on function platform_brain.append_agent_task_event_v49(
  uuid,integer,text,bytea,integer,bytea,timestamptz
) from public;
revoke all on function platform_brain.mark_adapter_delivery_dispatched_v49(
  uuid,uuid
) from public;
revoke all on function platform_brain.fail_agent_task_protocol_v49(uuid)
  from public;

do $migration$
declare
  selected_brain name;
  selected_app name;
  role_name name;
begin
  if current_database() = 'agent_platform_control'
     and current_user = 'platform_control_owner'
  then
    selected_brain := 'platform_brain_worker';
    selected_app := 'platform_control_app';
  elsif current_database() = 'agent_platform_control_preview'
     and current_user = 'platform_control_owner_preview'
  then
    selected_brain := 'platform_brain_worker_preview';
    selected_app := 'platform_control_app_preview';
  else
    raise insufficient_privilege using
      message = 'Brain task/wait migration owner/environment mismatch';
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
      'revoke all on platform_brain.brain_task_event_cursors from %I',
      role_name
    );
    execute format(
      'revoke all on platform_brain.agent_runtime_health from %I',
      role_name
    );
    execute format(
      'revoke all on function '
      'platform_brain.append_agent_task_event_v49('
      'uuid,integer,text,bytea,integer,bytea,timestamptz) from %I',
      role_name
    );
    execute format(
      'revoke all on function '
      'platform_brain.mark_adapter_delivery_dispatched_v49(uuid,uuid) from %I',
      role_name
    );
    execute format(
      'revoke all on function '
      'platform_brain.fail_agent_task_protocol_v49(uuid) from %I',
      role_name
    );
  end loop;

  execute format(
    'grant select,insert,update on '
    'platform_brain.brain_task_event_cursors to %I',
    selected_brain
  );
  execute format(
    'grant select,insert,update on '
    'platform_brain.agent_runtime_health to %I',
    selected_brain
  );
  execute format(
    'grant select on platform_brain.agent_runtime_health to %I',
    selected_app
  );
  execute format(
    'grant execute on function '
    'platform_brain.append_agent_task_event_v49('
    'uuid,integer,text,bytea,integer,bytea,timestamptz) to %I',
    selected_brain
  );
  execute format(
    'grant execute on function '
    'platform_brain.mark_adapter_delivery_dispatched_v49(uuid,uuid) to %I',
    selected_brain
  );
  execute format(
    'grant execute on function '
    'platform_brain.fail_agent_task_protocol_v49(uuid) to %I',
    selected_brain
  );
end
$migration$;

comment on table platform_brain.brain_task_event_cursors is
  'Sole durable per-task delivery waterline for Agent Brain wait settlement.';
comment on table platform_brain.agent_runtime_health is
  'Persistent Agent-local health projection; protocol failures never degrade unrelated workers.';
