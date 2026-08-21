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
     or (select array_agg(a.attname::text order by key.ordinality)
         from pg_constraint c
         cross join lateral unnest(c.conkey)
           with ordinality as key(attnum,ordinality)
         join pg_attribute a
           on a.attrelid = c.conrelid and a.attnum = key.attnum
         where c.conrelid = 'execution_worker.local_runs'::regclass
           and c.conname = 'local_runs_pkey' and c.contype = 'p')
        is distinct from array['run_id']
     or (select array_agg(a.attname::text order by key.ordinality)
         from pg_constraint c
         cross join lateral unnest(c.conkey)
           with ordinality as key(attnum,ordinality)
         join pg_attribute a
           on a.attrelid = c.conrelid and a.attnum = key.attnum
         where c.conrelid = 'execution_worker.local_runs'::regclass
           and c.conname = 'local_runs_job_id_key' and c.contype = 'u')
        is distinct from array['job_id']
     or (select array_agg(a.attname::text order by key.ordinality)
         from pg_constraint c
         cross join lateral unnest(c.conkey)
           with ordinality as key(attnum,ordinality)
         join pg_attribute a
           on a.attrelid = c.conrelid and a.attnum = key.attnum
         where c.conrelid = 'execution_worker.event_outbox'::regclass
           and c.conname = 'event_outbox_pkey' and c.contype = 'p')
        is distinct from array['run_id','seq']
     or (select array_agg(a.attname::text order by key.ordinality)
         from pg_constraint c
         cross join lateral unnest(c.conkey)
           with ordinality as key(attnum,ordinality)
         join pg_attribute a
           on a.attrelid = c.conrelid and a.attnum = key.attnum
         where c.conrelid = 'execution_worker.event_outbox'::regclass
           and c.conname = 'event_outbox_run_id_fkey' and c.contype = 'f')
        is distinct from array['run_id']
     or (select array_agg(a.attname::text order by key.ordinality)
         from pg_constraint c
         cross join lateral unnest(c.confkey)
           with ordinality as key(attnum,ordinality)
         join pg_attribute a
           on a.attrelid = c.confrelid and a.attnum = key.attnum
         where c.conrelid = 'execution_worker.event_outbox'::regclass
           and c.conname = 'event_outbox_run_id_fkey'
           and c.contype = 'f'
           and c.confrelid = 'execution_worker.local_runs'::regclass)
        is distinct from array['run_id'] then
    raise exception 'incompatible execution worker schema layout';
  end if;
end
$worker_schema_layout$;

do $worker_schema_behavior$
declare
  probe_run uuid := md5(random()::text || clock_timestamp()::text)::uuid;
  probe_job uuid := md5(random()::text || clock_timestamp()::text || 'job')::uuid;
  probe_run_two uuid := md5(random()::text || clock_timestamp()::text || 'run2')::uuid;
  probe_job_two uuid := md5(random()::text || clock_timestamp()::text || 'job2')::uuid;
  probe_hash bytea := decode(repeat('00',32),'hex');
begin
  begin
    insert into execution_worker.local_runs(
      run_id,job_id,agent_id,metabot_port,callback_token_hash,state,leased_at
    ) values (
      probe_run,probe_job,'schema-probe',1,probe_hash,'leased',now()
    );
    insert into execution_worker.local_runs(
      run_id,job_id,agent_id,metabot_port,callback_token_hash,state,leased_at
    ) values (
      probe_run_two,probe_job_two,'schema-probe',65535,
      probe_hash,'leased',now()
    );

    begin
      insert into execution_worker.local_runs values (
        md5(probe_run::text || 'port-zero')::uuid,
        md5(probe_job::text || 'port-zero')::uuid,
        'schema-probe-port-zero',0,probe_hash,'leased',now(),null,null
      );
      raise exception 'invalid port accepted';
    exception when check_violation then null;
    end;

    begin
      insert into execution_worker.local_runs values (
        md5(probe_run::text || 'port-high')::uuid,
        md5(probe_job::text || 'port-high')::uuid,
        'schema-probe-port-high',65536,probe_hash,'leased',now(),null,null
      );
      raise exception 'invalid port accepted';
    exception when check_violation then null;
    end;

    begin
      insert into execution_worker.local_runs values (
        md5(probe_run::text || 'state')::uuid,
        md5(probe_job::text || 'state')::uuid,
        'schema-probe-state',1,probe_hash,'unknown',now(),null,null
      );
      raise exception 'invalid state accepted';
    exception when check_violation then null;
    end;

    begin
      insert into execution_worker.local_runs values (
        md5(probe_run::text || 'hash')::uuid,
        md5(probe_job::text || 'hash')::uuid,
        'schema-probe-hash',1,decode(repeat('00',31),'hex'),
        'leased',now(),null,null
      );
      raise exception 'invalid hash accepted';
    exception when check_violation then null;
    end;

    begin
      insert into execution_worker.local_runs values (
        probe_run,md5(probe_job::text || 'duplicate-run')::uuid,
        'schema-probe-duplicate-run',1,probe_hash,'leased',now(),null,null
      );
      raise exception 'duplicate run accepted';
    exception when unique_violation then null;
    end;

    begin
      insert into execution_worker.local_runs values (
        md5(probe_run::text || 'duplicate-job')::uuid,probe_job,
        'schema-probe-duplicate-job',1,probe_hash,'leased',now(),null,null
      );
      raise exception 'duplicate job accepted';
    exception when unique_violation then null;
    end;

    insert into execution_worker.event_outbox(run_id,seq,event_json)
    values (probe_run,1,'{}'::jsonb);

    begin
      insert into execution_worker.event_outbox(run_id,seq,event_json)
      values (probe_run,0,'{}'::jsonb);
      raise exception 'invalid sequence accepted';
    exception when check_violation then null;
    end;

    begin
      insert into execution_worker.event_outbox(run_id,seq,event_json)
      values (probe_run,2,'true'::jsonb);
      raise exception 'scalar event accepted';
    exception when check_violation then null;
    end;

    begin
      insert into execution_worker.event_outbox(run_id,seq,event_json)
      values (probe_run,1,'{}'::jsonb);
      raise exception 'duplicate event accepted';
    exception when unique_violation then null;
    end;

    begin
      insert into execution_worker.event_outbox(run_id,seq,event_json)
      values (md5(probe_run::text || 'unknown')::uuid,1,'{}'::jsonb);
      raise exception 'unknown run accepted';
    exception when foreign_key_violation then null;
    end;

    delete from execution_worker.event_outbox where run_id = probe_run;
    delete from execution_worker.local_runs
    where run_id in (probe_run,probe_run_two);
  exception when others then
    raise exception 'incompatible execution worker schema';
  end;
end
$worker_schema_behavior$;
