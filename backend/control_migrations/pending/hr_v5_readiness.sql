-- UNNUMBERED DRAFT: disposable tests only; production application is not authorized.
alter table platform_control.execution_workers add column v5_observation jsonb
  check (v5_observation is null or (jsonb_typeof(v5_observation)='object'
    and octet_length(v5_observation::text)<=8192));
alter table platform_control.turn_attempts
  add column readiness_check_after timestamptz,
  add column capability_missing_since timestamptz,
  add column capability_alerted_at timestamptz;
do $migration$
declare selected_app name;
begin
  if current_database()='agent_platform_control' and current_user='platform_control_owner' then
    selected_app := 'platform_control_app';
  elsif current_database()='agent_platform_control_preview' and current_user='platform_control_owner_preview' then
    selected_app := 'platform_control_app_preview';
  else raise insufficient_privilege using message='readiness environment invalid'; end if;
  execute format('grant update (v5_observation) on platform_control.execution_workers to %I', selected_app);
end
$migration$;
