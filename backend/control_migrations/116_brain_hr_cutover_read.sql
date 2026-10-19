-- The Brain candidate parser checks the HR cutover state before claiming work.
-- Migration 102 granted this read to the app role but omitted the Brain role.
do $migration$
declare selected_brain name;
begin
  if current_database() = 'agent_platform_control' and current_user = 'platform_control_owner' then
    selected_brain := 'platform_brain_worker';
  elsif current_database() = 'agent_platform_control_preview' and current_user = 'platform_control_owner_preview' then
    selected_brain := 'platform_brain_worker_preview';
  else
    raise insufficient_privilege using message = 'Brain HR cutover migration owner/environment mismatch';
  end if;
  execute format('grant select on table platform_control.hr_execution_cutover to %I', selected_brain);
end
$migration$;
