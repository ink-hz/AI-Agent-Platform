do $migration$
declare
  selected_brain text;
begin
  if current_user = 'platform_control_owner' then
    selected_brain := 'platform_brain_worker';
  elsif current_user = 'platform_control_owner_preview' then
    selected_brain := 'platform_brain_worker_preview';
  else
    raise insufficient_privilege using
      message = 'collaboration retention migration owner invalid';
  end if;

  execute format(
    'grant update (task_context_ciphertext,task_context_key_version) '
    'on platform_brain.agent_tasks to %I',
    selected_brain
  );
  execute format(
    'grant update (content_ciphertext,content_key_version) '
    'on platform_brain.agent_task_messages to %I',
    selected_brain
  );
  execute format(
    'grant update (payload_ciphertext,payload_key_version) '
    'on platform_brain.agent_task_events to %I',
    selected_brain
  );
end
$migration$;

comment on table platform_brain.agent_task_messages is
  'Encrypted child-Agent messages. Expired archived content is tombstoned while identity, timestamps and source hashes remain.';

comment on table platform_brain.agent_task_events is
  'Encrypted child-Agent events. Thinking and work provenance hashes survive retention tombstoning.';
