create function platform_brain.wake_loop_for_user_intervention_v46(
  selected_loop_id uuid,
  selected_tool_call_id uuid,
  selected_result_ciphertext bytea,
  selected_result_key_version integer,
  selected_result_sha256 bytea,
  selected_next_step_id uuid
) returns boolean
language plpgsql
security definer
set search_path = pg_catalog, platform_brain, platform_control
as $function$
declare
  selected_loop platform_brain.brain_loops%rowtype;
  selected_wait platform_brain.brain_wait_subscriptions%rowtype;
  selected_call platform_brain.brain_tool_calls%rowtype;
  selected_step platform_brain.brain_steps%rowtype;
begin
  if (
       current_database() = 'agent_platform_control'
       and session_user <> 'platform_control_app'
     ) or (
       current_database() = 'agent_platform_control_preview'
       and session_user <> 'platform_control_app_preview'
     ) or current_database() not in (
       'agent_platform_control','agent_platform_control_preview'
     )
  then
    raise insufficient_privilege using
      message = 'Brain intervention wake caller invalid';
  end if;
  if selected_loop_id is null
     or selected_tool_call_id is null
     or octet_length(selected_result_ciphertext) < 29
     or selected_result_key_version <= 0
     or octet_length(selected_result_sha256) <> 32
     or selected_next_step_id is null
  then
    raise check_violation using
      message = 'Brain intervention wake payload invalid';
  end if;

  select * into selected_wait
  from platform_brain.brain_wait_subscriptions
  where loop_id=selected_loop_id and status='active'
  for update;
  if not found then
    return false;
  end if;

  select call.* into selected_call
  from platform_brain.brain_tool_calls call
  where call.brain_tool_call_id=selected_tool_call_id
    and call.brain_tool_call_id=selected_wait.brain_tool_call_id
    and call.status='waiting_result'
  for update;
  if not found then
    raise check_violation using message = 'Brain wait tool call invalid';
  end if;

  select * into selected_step
  from platform_brain.brain_steps
  where step_id=selected_call.step_id
    and loop_id=selected_loop_id
    and status='waiting_tool_results'
  for update;
  if not found then
    raise check_violation using message = 'Brain wait step invalid';
  end if;

  select * into selected_loop
  from platform_brain.brain_loops
  where loop_id=selected_loop_id
  for update;
  if not found then
    raise no_data_found using message = 'Brain loop missing';
  end if;
  if selected_loop.status <> 'waiting_agents' then
    return false;
  end if;

  update platform_brain.brain_wait_subscriptions set
    status='cancelled',terminal_at=clock_timestamp(),
    updated_at=clock_timestamp()
  where wait_id=selected_wait.wait_id;

  update platform_brain.brain_tool_calls set
    status='result_ready',result_ciphertext=selected_result_ciphertext,
    result_key_version=selected_result_key_version,
    result_sha256=selected_result_sha256,updated_at=clock_timestamp()
  where brain_tool_call_id=selected_call.brain_tool_call_id;

  update platform_brain.brain_steps set
    status='completed',terminal_at=clock_timestamp(),
    updated_at=clock_timestamp()
  where step_id=selected_step.step_id;

  insert into platform_brain.brain_steps (
    step_id,loop_id,step_seq,status
  ) values (
    selected_next_step_id,selected_loop_id,selected_step.step_seq + 1,'queued'
  );

  update platform_brain.brain_loops set
    status='running',updated_at=clock_timestamp(),row_version=row_version+1
  where loop_id=selected_loop_id;

  update platform_control.conversation_turns set
    status='running',updated_at=clock_timestamp()
  where turn_id=selected_loop.turn_id;

  return true;
end
$function$;

revoke all on function platform_brain.wake_loop_for_user_intervention_v46(
  uuid,uuid,bytea,integer,bytea,uuid
) from public;

do $migration$
declare
  selected_app text;
begin
  if current_database() = 'agent_platform_control' then
    selected_app := 'platform_control_app';
  elsif current_database() = 'agent_platform_control_preview' then
    selected_app := 'platform_control_app_preview';
  else
    raise insufficient_privilege using
      message = 'Brain intervention wake database invalid';
  end if;

  execute format(
    'grant execute on function '
    'platform_brain.wake_loop_for_user_intervention_v46('
    'uuid,uuid,bytea,integer,bytea,uuid) to %I',
    selected_app
  );
end
$migration$;

comment on function platform_brain.wake_loop_for_user_intervention_v46(
  uuid,uuid,bytea,integer,bytea,uuid
) is
  'Atomically cancels one active Agent wait and resumes its Brain loop for a '
  'verified user intervention without granting the web role table updates.';
