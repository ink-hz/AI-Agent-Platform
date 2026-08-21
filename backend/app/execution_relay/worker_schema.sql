create schema if not exists execution_worker;

comment on schema execution_worker is
  'agent execution worker schema version 1';

do $worker_schema_guard$
begin
  if to_regclass('execution_worker.schema_migrations') is null
     and (
       to_regclass('execution_worker.local_runs') is not null
       or to_regclass('execution_worker.event_outbox') is not null
     ) then
    raise exception 'unversioned execution worker schema';
  end if;
end
$worker_schema_guard$;

create table if not exists execution_worker.schema_migrations (
  singleton boolean primary key default true check (singleton),
  version integer not null check (version > 0),
  applied_at timestamptz not null default now()
);

do $worker_schema_version$
declare
  record_count integer;
  selected_singleton boolean;
  selected_version integer;
begin
  select count(*), bool_and(singleton), max(version)
    into record_count, selected_singleton, selected_version
    from execution_worker.schema_migrations;
  if record_count = 0 then
    insert into execution_worker.schema_migrations(singleton,version)
    values (true,1);
  elsif record_count <> 1
        or selected_singleton is distinct from true
        or selected_version is distinct from 1 then
    raise exception 'unsupported execution worker schema version';
  end if;
end
$worker_schema_version$;

create table if not exists execution_worker.local_runs (
  run_id uuid primary key,
  job_id uuid not null unique,
  agent_id varchar(128) not null check (length(agent_id) > 0),
  metabot_port integer not null check (metabot_port between 1 and 65535),
  callback_token_hash bytea not null
    check (octet_length(callback_token_hash) = 32),
  state varchar(32) not null check (
    state in (
      'leased',
      'dispatching',
      'dispatched',
      'running',
      'completed',
      'failed',
      'cancelled',
      'interrupted'
    )
  ),
  leased_at timestamptz not null,
  dispatched_at timestamptz,
  terminal_at timestamptz
);

create table if not exists execution_worker.event_outbox (
  run_id uuid not null
    references execution_worker.local_runs(run_id),
  seq integer not null check (seq > 0),
  event_json jsonb not null
    check (jsonb_typeof(event_json) = 'object'),
  delivered_at timestamptz,
  primary key (run_id, seq)
);

do $worker_schema_layout$
declare
  local_columns text[];
  outbox_columns text[];
begin
  select array_agg(
    format(
      '%s:%s:%s:%s',
      column_name,
      data_type,
      is_nullable,
      coalesce(character_maximum_length,0)
    ) order by ordinal_position
  ) into local_columns
  from information_schema.columns
  where table_schema = 'execution_worker' and table_name = 'local_runs';

  select array_agg(
    format(
      '%s:%s:%s',
      column_name,
      data_type,
      is_nullable
    ) order by ordinal_position
  ) into outbox_columns
  from information_schema.columns
  where table_schema = 'execution_worker' and table_name = 'event_outbox';

  if local_columns is distinct from array[
       'run_id:uuid:NO:0',
       'job_id:uuid:NO:0',
       'agent_id:character varying:NO:128',
       'metabot_port:integer:NO:0',
       'callback_token_hash:bytea:NO:0',
       'state:character varying:NO:32',
       'leased_at:timestamp with time zone:NO:0',
       'dispatched_at:timestamp with time zone:YES:0',
       'terminal_at:timestamp with time zone:YES:0'
     ]
     or outbox_columns is distinct from array[
       'run_id:uuid:NO',
       'seq:integer:NO',
       'event_json:jsonb:NO',
       'delivered_at:timestamp with time zone:YES'
     ]
     or (select count(*) from pg_constraint
         where conrelid = 'execution_worker.local_runs'::regclass
           and contype = 'p') <> 1
     or (select count(*) from pg_constraint
         where conrelid = 'execution_worker.local_runs'::regclass
           and contype = 'u') <> 1
     or (select count(*) from pg_constraint
         where conrelid = 'execution_worker.local_runs'::regclass
           and contype = 'c') <> 4
     or (select count(*) from pg_constraint
         where conrelid = 'execution_worker.event_outbox'::regclass
           and contype = 'p') <> 1
     or (select count(*) from pg_constraint
         where conrelid = 'execution_worker.event_outbox'::regclass
           and contype = 'f') <> 1
     or (select count(*) from pg_constraint
         where conrelid = 'execution_worker.event_outbox'::regclass
           and contype = 'c') <> 2 then
    raise exception 'incompatible execution worker schema layout';
  end if;

  if not exists (
       select 1 from pg_constraint
       where conrelid = 'execution_worker.local_runs'::regclass
         and conname = 'local_runs_metabot_port_check'
         and pg_get_constraintdef(oid) like '%metabot_port%'
         and pg_get_constraintdef(oid) like '%65535%'
     )
     or not exists (
       select 1 from pg_constraint
       where conrelid = 'execution_worker.local_runs'::regclass
         and conname = 'local_runs_callback_token_hash_check'
         and pg_get_constraintdef(oid) like '%octet_length%'
         and pg_get_constraintdef(oid) like '%32%'
     )
     or not exists (
       select 1 from pg_constraint
       where conrelid = 'execution_worker.local_runs'::regclass
         and conname = 'local_runs_state_check'
         and pg_get_constraintdef(oid) like '%leased%'
         and pg_get_constraintdef(oid) like '%interrupted%'
     )
     or not exists (
       select 1 from pg_constraint
       where conrelid = 'execution_worker.local_runs'::regclass
         and conname = 'local_runs_agent_id_check'
         and pg_get_constraintdef(oid) like '%length%'
     )
     or not exists (
       select 1 from pg_constraint
       where conrelid = 'execution_worker.event_outbox'::regclass
         and conname = 'event_outbox_seq_check'
         and pg_get_constraintdef(oid) like '%seq%'
         and pg_get_constraintdef(oid) like '%0%'
     )
     or not exists (
       select 1 from pg_constraint
       where conrelid = 'execution_worker.event_outbox'::regclass
         and conname = 'event_outbox_event_json_check'
         and pg_get_constraintdef(oid) like '%jsonb_typeof%'
         and pg_get_constraintdef(oid) like '%object%'
     ) then
    raise exception 'incompatible execution worker constraints';
  end if;
end
$worker_schema_layout$;
