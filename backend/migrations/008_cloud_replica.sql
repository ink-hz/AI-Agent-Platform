do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'platform_replica_import') then
        create role platform_replica_import nologin;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'platform_replica_read') then
        create role platform_replica_read nologin;
    end if;
end
$$;

create schema if not exists platform_replica;
revoke all on schema public from platform_replica_import, platform_replica_read;
revoke all on schema platform_replica from public;
grant usage on schema platform_replica to platform_replica_import, platform_replica_read;

create table if not exists platform_replica.generations (
    source_instance_id text primary key,
    last_sequence bigint not null,
    last_digest char(64) not null,
    upper_watermark timestamptz not null,
    committed_at timestamptz not null default now()
);

create table if not exists platform_replica.agents (
    agent_id text primary key,
    display_payload bytea not null,
    payload_nonce bytea not null,
    payload_sha256 char(64) not null,
    updated_at timestamptz not null
);

create table if not exists platform_replica.sessions (
    session_key text primary key,
    user_id text not null,
    agent_id text not null references platform_replica.agents(agent_id),
    source_kind text not null,
    channel text,
    created_at timestamptz not null,
    last_active_at timestamptz not null,
    expires_at timestamptz not null,
    generation_sequence bigint not null,
    display_payload bytea not null,
    payload_nonce bytea not null,
    payload_sha256 char(64) not null,
    updated_at timestamptz not null default now()
);

create index if not exists replica_sessions_agent_activity_idx
    on platform_replica.sessions(agent_id, last_active_at desc, session_key);
create index if not exists replica_sessions_expiry_idx
    on platform_replica.sessions(expires_at);

create table if not exists platform_replica.runtime_snapshots (
    snapshot_key text primary key,
    agent_id text not null,
    observed_at timestamptz not null,
    display_payload bytea not null,
    payload_nonce bytea not null,
    payload_sha256 char(64) not null
);

create table if not exists platform_replica.aggregate_snapshots (
    snapshot_key text primary key,
    observed_at timestamptz not null,
    display_payload bytea not null,
    payload_nonce bytea not null,
    payload_sha256 char(64) not null
);

create table if not exists platform_replica.import_audit (
    source_instance_id text not null,
    sequence bigint not null,
    digest char(64) not null,
    record_count integer not null,
    upper_watermark timestamptz not null,
    imported_at timestamptz not null default now(),
    primary key (source_instance_id, sequence)
);

create table if not exists platform_replica.retention_audit (
    retention_run_id bigserial primary key,
    cutoff_at timestamptz not null,
    deleted_session_count integer not null,
    deleted_agent_count integer not null,
    completed_at timestamptz not null default now()
);

grant select, insert, update, delete on all tables in schema platform_replica
    to platform_replica_import;
grant usage, select on all sequences in schema platform_replica
    to platform_replica_import;
grant select on all tables in schema platform_replica to platform_replica_read;

alter default privileges in schema platform_replica
    grant select, insert, update, delete on tables to platform_replica_import;
alter default privileges in schema platform_replica
    grant select on tables to platform_replica_read;
