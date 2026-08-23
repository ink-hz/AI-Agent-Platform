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
  singleton boolean default true,
  version integer not null,
  applied_at timestamptz not null default now(),
  constraint schema_migrations_pkey primary key (singleton),
  constraint schema_migrations_singleton_check check (singleton),
  constraint schema_migrations_version_check check (version > 0)
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
  run_id uuid,
  job_id uuid not null,
  agent_id varchar(128) not null,
  metabot_port integer not null,
  callback_token_hash bytea not null,
  state varchar(32) not null,
  leased_at timestamptz not null,
  dispatched_at timestamptz,
  terminal_at timestamptz,
  constraint local_runs_pkey primary key (run_id),
  constraint local_runs_job_id_key unique (job_id),
  constraint local_runs_agent_id_check check (length(agent_id) > 0),
  constraint local_runs_metabot_port_check
    check (metabot_port between 1 and 65535),
  constraint local_runs_callback_token_hash_check
    check (octet_length(callback_token_hash) = 32),
  constraint local_runs_state_check check (
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
  )
);

create table if not exists execution_worker.event_outbox (
  run_id uuid not null,
  seq integer not null,
  event_json jsonb not null,
  delivered_at timestamptz,
  constraint event_outbox_pkey primary key (run_id, seq),
  constraint event_outbox_run_id_fkey foreign key (run_id)
    references execution_worker.local_runs(run_id),
  constraint event_outbox_seq_check check (seq > 0),
  constraint event_outbox_event_json_check
    check (jsonb_typeof(event_json) = 'object')
);

do $worker_schema_layout$
declare
  migration_columns text[];
  local_columns text[];
  outbox_columns text[];
  migration_constraints text[];
  local_constraints text[];
  outbox_constraints text[];
begin
  select array_agg(
    format(
      '%s:%s:%s:%s',
      column_name,
      data_type,
      is_nullable,
      coalesce(character_maximum_length,0)
    ) order by ordinal_position
  ) into migration_columns
  from information_schema.columns
  where table_schema = 'execution_worker'
    and table_name = 'schema_migrations';

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

  select array_agg(
    format(
      '%s|%s|%s|%s|%s|%s|%s|%s|%s|%s',
      c.conname,
      c.contype,
      c.convalidated,
      c.condeferrable,
      c.condeferred,
      case when c.contype = 'f' then c.confupdtype::text else '-' end,
      case when c.contype = 'f' then c.confdeltype::text else '-' end,
      case when c.contype = 'f' then c.confmatchtype::text else '-' end,
      case when c.contype = 'c'
        then pg_get_constraintdef(c.oid,true) else '' end,
      coalesce(pg_get_expr(c.conbin,c.conrelid,true),'')
    ) order by c.conname
  ) into migration_constraints
  from pg_constraint c
  where c.conrelid = 'execution_worker.schema_migrations'::regclass;

  select array_agg(
    format(
      '%s|%s|%s|%s|%s|%s|%s|%s|%s|%s',
      c.conname,
      c.contype,
      c.convalidated,
      c.condeferrable,
      c.condeferred,
      case when c.contype = 'f' then c.confupdtype::text else '-' end,
      case when c.contype = 'f' then c.confdeltype::text else '-' end,
      case when c.contype = 'f' then c.confmatchtype::text else '-' end,
      case when c.contype = 'c'
        then pg_get_constraintdef(c.oid,true) else '' end,
      coalesce(pg_get_expr(c.conbin,c.conrelid,true),'')
    ) order by c.conname
  ) into local_constraints
  from pg_constraint c
  where c.conrelid = 'execution_worker.local_runs'::regclass;

  select array_agg(
    format(
      '%s|%s|%s|%s|%s|%s|%s|%s|%s|%s',
      c.conname,
      c.contype,
      c.convalidated,
      c.condeferrable,
      c.condeferred,
      case when c.contype = 'f' then c.confupdtype::text else '-' end,
      case when c.contype = 'f' then c.confdeltype::text else '-' end,
      case when c.contype = 'f' then c.confmatchtype::text else '-' end,
      case when c.contype = 'c'
        then pg_get_constraintdef(c.oid,true) else '' end,
      coalesce(pg_get_expr(c.conbin,c.conrelid,true),'')
    ) order by c.conname
  ) into outbox_constraints
  from pg_constraint c
  where c.conrelid = 'execution_worker.event_outbox'::regclass;

  if migration_columns is distinct from array[
       'singleton:boolean:NO:0',
       'version:integer:NO:0',
       'applied_at:timestamp with time zone:NO:0'
     ]
     or local_columns is distinct from array[
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
     or migration_constraints is distinct from array[
       'schema_migrations_pkey|p|t|f|f|-|-|-||',
       'schema_migrations_singleton_check|c|t|f|f|-|-|-|CHECK (singleton)|singleton',
       'schema_migrations_version_check|c|t|f|f|-|-|-|CHECK (version > 0)|version > 0'
     ]
     or local_constraints is distinct from array[
       'local_runs_agent_id_check|c|t|f|f|-|-|-|CHECK (length(agent_id::text) > 0)|length(agent_id::text) > 0',
       'local_runs_callback_token_hash_check|c|t|f|f|-|-|-|CHECK (octet_length(callback_token_hash) = 32)|octet_length(callback_token_hash) = 32',
       'local_runs_job_id_key|u|t|f|f|-|-|-||',
       'local_runs_metabot_port_check|c|t|f|f|-|-|-|CHECK (metabot_port >= 1 AND metabot_port <= 65535)|metabot_port >= 1 AND metabot_port <= 65535',
       'local_runs_pkey|p|t|f|f|-|-|-||',
       'local_runs_state_check|c|t|f|f|-|-|-|CHECK (state::text = ANY (ARRAY[''leased''::character varying, ''dispatching''::character varying, ''dispatched''::character varying, ''running''::character varying, ''completed''::character varying, ''failed''::character varying, ''cancelled''::character varying, ''interrupted''::character varying]::text[]))|state::text = ANY (ARRAY[''leased''::character varying, ''dispatching''::character varying, ''dispatched''::character varying, ''running''::character varying, ''completed''::character varying, ''failed''::character varying, ''cancelled''::character varying, ''interrupted''::character varying]::text[])'
     ]
     or outbox_constraints is distinct from array[
       'event_outbox_event_json_check|c|t|f|f|-|-|-|CHECK (jsonb_typeof(event_json) = ''object''::text)|jsonb_typeof(event_json) = ''object''::text',
       'event_outbox_pkey|p|t|f|f|-|-|-||',
       'event_outbox_run_id_fkey|f|t|f|f|a|a|s||',
       'event_outbox_seq_check|c|t|f|f|-|-|-|CHECK (seq > 0)|seq > 0'
     ]
     or (select array_agg(a.attname::text order by key.ordinality)
         from pg_constraint c
         cross join lateral unnest(c.conkey)
           with ordinality as key(attnum,ordinality)
         join pg_attribute a
           on a.attrelid = c.conrelid and a.attnum = key.attnum
         where c.conrelid = 'execution_worker.schema_migrations'::regclass
           and c.conname = 'schema_migrations_pkey' and c.contype = 'p')
        is distinct from array['singleton']
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
