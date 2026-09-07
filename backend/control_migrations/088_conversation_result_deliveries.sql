-- A result-enrichment failure must never roll back a committed text answer.
create table platform_control.conversation_result_deliveries (
  mission_id uuid primary key
    references platform_control.missions(mission_id) on delete cascade,
  message_id uuid not null unique
    references platform_control.conversation_messages(message_id) on delete cascade,
  status text not null default 'pending'
    check (status in ('pending','completed','failed')),
  attempts integer not null default 0 check (attempts between 0 and 8),
  next_attempt_at timestamptz not null default now(),
  last_error_code text check (last_error_code in ('result_projection_unavailable')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (status <> 'pending' or attempts < 8),
  check (status <> 'completed' or last_error_code is null)
);
create index conversation_result_deliveries_due_v88
  on platform_control.conversation_result_deliveries(next_attempt_at,mission_id)
  where status='pending';
revoke all on platform_control.conversation_result_deliveries from public;
do $migration$
declare selected_app name;
begin
  if current_database()='agent_platform_control'
     and current_user='platform_control_owner' then
    selected_app := 'platform_control_app';
  elsif current_database()='agent_platform_control_preview'
     and current_user='platform_control_owner_preview' then
    selected_app := 'platform_control_app_preview';
  else
    raise insufficient_privilege using message='result delivery environment invalid';
  end if;
  execute format(
    'grant select,insert,update on platform_control.conversation_result_deliveries to %I',
    selected_app
  );
end
$migration$;
