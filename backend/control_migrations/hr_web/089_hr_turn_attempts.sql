-- Opt-in HR production inventory 2026-09-08: target 087; apply base 088 first.
-- Promoted unchanged SQL from the reviewed, disposable-tested HR WEB contract.
-- Assign a production number only after authorized target migration inventory.
-- Additive rollback compatibility: legacy readers continue using existing columns.
alter table platform_control.conversations
  add column execution_owner text not null default 'legacy_api_v1'
    check (execution_owner in ('legacy_api_v1','worker_direct')),
  add column route_epoch bigint not null default 0 check (route_epoch >= 0),
  add column snapshot_version bigint not null default 0 check (snapshot_version >= 0);

create table platform_control.turn_attempts (
  attempt_id uuid primary key,
  turn_id uuid not null references platform_control.conversation_turns(turn_id),
  attempt_no integer not null check (attempt_no > 0),
  executor_kind text not null check (executor_kind in ('legacy_api_v1','worker_direct')),
  executor_id text,
  lease_epoch bigint not null default 0 check (lease_epoch >= 0),
  lease_expires_at timestamptz,
  status text not null check (status in (
    'queued','running','reconciling','completed','failed','cancelled','interrupted'
  )),
  cancel_requested_at timestamptz,
  transport_run_id uuid unique,
  result_message_id uuid references platform_control.conversation_messages(message_id),
  retry_of_attempt_id uuid references platform_control.turn_attempts(attempt_id),
  reason_code text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (turn_id, attempt_no),
  check (status <> 'queued' or
    (executor_id is null and lease_epoch=0 and lease_expires_at is null)),
  check (status not in ('running','reconciling') or
    (executor_id is not null and lease_epoch > 0 and lease_expires_at is not null)),
  check ((status='completed') = (result_message_id is not null))
);
create unique index one_live_attempt_per_turn
  on platform_control.turn_attempts(turn_id)
  where status in ('queued','running','reconciling');
revoke all on platform_control.turn_attempts from public;
do $migration$
declare selected_app name; selected_maintenance name;
begin
  if current_database()='agent_platform_control' and current_user='platform_control_owner' then
    selected_app := 'platform_control_app';
    selected_maintenance := 'platform_control_maintenance';
  elsif current_database()='agent_platform_control_preview' and current_user='platform_control_owner_preview' then
    selected_app := 'platform_control_app_preview';
    selected_maintenance := 'platform_control_maintenance_preview';
  else
    raise insufficient_privilege using message='attempt environment invalid';
  end if;
  execute format('grant select,insert,update on platform_control.turn_attempts to %I', selected_app);
  execute format('grant select on platform_control.turn_attempts to %I', selected_maintenance);
  execute format('grant update (snapshot_version) on platform_control.conversations to %I', selected_app);
end
$migration$;
