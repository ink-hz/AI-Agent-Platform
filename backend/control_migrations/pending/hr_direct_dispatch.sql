-- UNNUMBERED DRAFT: explicitly loaded by disposable PostgreSQL tests only.
-- P03a immutable intake ownership; production numbering remains unassigned.
alter table platform_control.conversation_turns
  add column execution_owner text not null default 'legacy_api_v1'
    check (execution_owner in ('legacy_api_v1','worker_direct')),
  add column origin_route_epoch bigint not null default 0
    check (origin_route_epoch >= 0);

create function platform_control.pin_turn_execution_origin()
returns trigger language plpgsql
set search_path = pg_catalog, platform_control
as $function$
declare selected_mode text; selected_agent text;
begin
  if tg_op='UPDATE' then
    if (new.execution_owner,new.origin_route_epoch)
       is distinct from (old.execution_owner,old.origin_route_epoch) then
      raise check_violation using message='turn execution origin is immutable';
    end if;
    return new;
  end if;
  select execution_owner,route_epoch,mode,direct_agent_id
    into new.execution_owner,new.origin_route_epoch,selected_mode,selected_agent
    from platform_control.conversations
    where conversation_id=new.conversation_id for update;
  if new.execution_owner='worker_direct'
     and (selected_mode<>'direct_agent' or selected_agent is distinct from 'hr-bot') then
    raise check_violation using message='worker execution requires HR direct intake';
  end if;
  return new;
end
$function$;

create trigger pin_turn_execution_origin
before insert or update of execution_owner,origin_route_epoch
on platform_control.conversation_turns
for each row execute function platform_control.pin_turn_execution_origin();

-- P03b1: metadata only. Frozen business content uses execution_jobs ciphertext.
alter table platform_control.execution_jobs drop constraint execution_jobs_job_kind_v42;
alter table platform_control.execution_jobs add constraint execution_jobs_job_kind_v42
  check (job_kind in ('legacy_brain','direct_agent','metabot_local','worker_direct_v5'));

create table platform_control.direct_command_bindings (
  attempt_id uuid primary key references platform_control.turn_attempts(attempt_id),
  command_id uuid not null unique,
  job_id uuid not null unique references platform_control.execution_jobs(job_id),
  conversation_id uuid not null references platform_control.conversations(conversation_id),
  command_seq bigint not null check(command_seq between 1 and 9007199254740990),
  command_hash text not null check(command_hash ~ '^[0-9a-f]{64}$'),
  transport_worker_id text references platform_control.execution_workers(worker_id),
  offered_at timestamptz,
  accepted_at timestamptz,
  launch_lease_epoch bigint check(launch_lease_epoch between 1 and 9007199254740991),
  retired_unsent_at timestamptz,
  check (accepted_at is null or offered_at is not null),
  check (launch_lease_epoch is null or accepted_at is not null),
  check (retired_unsent_at is null or (offered_at is null and accepted_at is null))
);
create unique index direct_command_sequence_reservation
  on platform_control.direct_command_bindings(conversation_id,command_seq)
  where retired_unsent_at is null;
create index direct_command_transport_poll
  on platform_control.direct_command_bindings(transport_worker_id,attempt_id)
  where retired_unsent_at is null;
create function platform_control.preserve_direct_command_binding()
returns trigger language plpgsql set search_path=pg_catalog,platform_control
as $function$
begin
  if (new.attempt_id,new.command_id,new.job_id,new.conversation_id,new.command_seq,new.command_hash)
     is distinct from (old.attempt_id,old.command_id,old.job_id,old.conversation_id,old.command_seq,old.command_hash)
     or (old.offered_at is not null and new.offered_at is distinct from old.offered_at)
     or (old.accepted_at is not null and new.accepted_at is distinct from old.accepted_at)
     or (old.launch_lease_epoch is not null and new.launch_lease_epoch is distinct from old.launch_lease_epoch)
     or (old.retired_unsent_at is not null and new.retired_unsent_at is distinct from old.retired_unsent_at)
     or (old.transport_worker_id is not null and new.transport_worker_id is distinct from old.transport_worker_id) then
    raise check_violation using message='direct command identity is immutable';
  end if;
  return new;
end
$function$;
create trigger preserve_direct_command_binding before update on platform_control.direct_command_bindings
  for each row execute function platform_control.preserve_direct_command_binding();
revoke all on platform_control.direct_command_bindings from public;
-- Raw v5 envelopes cannot use the legacy integer/payload-only event contract.
create table platform_control.v5_source_events (
  run_id uuid not null references platform_control.execution_jobs(run_id),
  seq bigint not null check(seq between 1 and 9007199254740990),
  event_type text not null check(event_type in ('run_heartbeat','raw_progress','result','error','cancelled','interrupted')),
  payload_ciphertext bytea not null,
  encryption_key_version integer not null check(encryption_key_version>0),
  received_at timestamptz not null default clock_timestamp(),
  primary key(run_id,seq)
);
create unique index v5_source_one_terminal on platform_control.v5_source_events(run_id)
  where event_type in ('result','error','cancelled','interrupted');
revoke all on platform_control.v5_source_events from public;
do $grants$
declare selected_app name;
begin
  case current_user
    when 'platform_control_owner' then selected_app := 'platform_control_app';
    when 'platform_control_owner_preview' then selected_app := 'platform_control_app_preview';
    else raise insufficient_privilege using message='direct binding environment invalid';
  end case;
  execute format('grant select,insert,update on platform_control.direct_command_bindings to %I', selected_app);
  execute format('grant select,insert on platform_control.v5_source_events to %I', selected_app);
end
$grants$;
