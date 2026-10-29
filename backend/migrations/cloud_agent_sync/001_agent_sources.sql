-- Applied by the database owner. No agent is taken over by installing this schema.
do $$ begin
    if not exists (select 1 from pg_roles where rolname = 'platform_cloud_sync') then
        create role platform_cloud_sync nologin;
    end if;
end $$;
grant usage on schema platform_replica to platform_cloud_sync;

create table if not exists platform_replica.agent_sources (
    agent_id text primary key,
    source_kind text not null,
    mode text not null check (mode in ('cloud_direct', 'office', 'native')),
    publisher text not null check (publisher in ('office', 'cloud', 'native')),
    source_instance_id text,
    writer_role name,
    ownership_epoch bigint not null default 1,
    last_attempt_at timestamptz,
    last_source_success_at timestamptz,
    last_published_at timestamptz,
    last_failure_at timestamptz,
    last_error text,
    source_sessions bigint check (source_sessions >= 0),
    source_turns bigint check (source_turns >= 0),
    last_duration_ms integer check (last_duration_ms >= 0),
    check (publisher <> 'cloud' or (mode = 'cloud_direct' and writer_role is not null)),
    constraint agent_sources_direct_identity check (publisher <> 'cloud' or
        (agent_id, source_kind) in (('ai-fae-agent', 'fae'), ('ai-admin-agent', 'admin'),
                                   ('ai-daq-fae-agent', 'fae'), ('voc', 'voc')))
);
create table if not exists platform_replica.agent_source_ownership_audit (
    audit_id bigserial primary key,
    agent_id text not null,
    ownership_epoch bigint not null,
    publisher text not null,
    writer_role name,
    changed_by name not null default session_user,
    changed_at timestamptz not null default clock_timestamp()
);
-- Existing default privileges grant the importer DML on new tables: revoke it.
revoke all on platform_replica.agent_sources,
    platform_replica.agent_source_ownership_audit from public, platform_replica_import, platform_cloud_sync;
revoke all on sequence platform_replica.agent_source_ownership_audit_audit_id_seq from public, platform_replica_import, platform_cloud_sync;
grant select on platform_replica.agent_sources to platform_replica_import, platform_replica_read, platform_cloud_sync;
grant update (last_attempt_at, last_source_success_at, last_published_at,
    last_failure_at, last_error, source_sessions, source_turns, last_duration_ms)
    on platform_replica.agent_sources to platform_cloud_sync, platform_replica_import;
grant select, insert, update, delete on platform_replica.sessions, platform_replica.agents to platform_cloud_sync;
alter table platform_replica.sessions add column if not exists writer_publisher text;
alter table platform_replica.sessions add column if not exists writer_epoch bigint;
alter table platform_replica.import_audit add column if not exists skipped_session_count integer not null default 0;

-- One namespace covers writers even before a registry row exists. Call this
-- before acquiring row locks; trigger guards use a nonblocking check below.
create or replace function platform_replica.lock_agent_writer(target_agent text, exclusive boolean default false)
returns void language plpgsql security definer
set search_path = pg_catalog, platform_replica as $$
begin
    if exclusive then
        perform pg_advisory_xact_lock(hashtextextended('platform_replica.agent_writer:' || target_agent, 0));
    else
        perform pg_advisory_xact_lock_shared(hashtextextended('platform_replica.agent_writer:' || target_agent, 0));
    end if;
end $$;
revoke all on function platform_replica.lock_agent_writer(text, boolean) from public;
grant execute on function platform_replica.lock_agent_writer(text, boolean)
    to platform_replica_import, platform_cloud_sync;

create or replace function platform_replica.check_agent_writer(target_agent text)
returns void language plpgsql security definer
set search_path = pg_catalog, platform_replica as $$
declare authority platform_replica.agent_sources%rowtype;
begin
    -- A BEFORE ROW trigger can already hold a session row lock. Never wait
    -- here behind a publisher that holds advisory and needs that row. Normal
    -- writers pre-acquire this same lock before DML; direct SQL fails visibly
    -- if it arrived after a takeover/publication already owns the boundary.
    if not pg_try_advisory_xact_lock_shared(hashtextextended('platform_replica.agent_writer:' || target_agent, 0)) then
        raise exception 'agent_writer_busy' using errcode = '55P03';
    end if;
    select * into authority from platform_replica.agent_sources
        where agent_id = target_agent;
    if exists (select 1 from pg_roles where rolname = session_user and rolsuper) then
        return;
    end if;
    if authority.publisher = 'cloud' then
        if authority.writer_role = session_user
           and pg_has_role(session_user, 'platform_cloud_sync', 'member') then return; end if;
    elsif (authority.publisher = 'office' or authority.agent_id is null)
       and pg_has_role(session_user, 'platform_replica_import', 'member') then
        return;
    end if;
    raise exception 'agent_writer_ownership' using errcode = '42501';
end $$;
revoke all on function platform_replica.check_agent_writer(text) from public;

create or replace function platform_replica.guard_agent_write()
returns trigger language plpgsql security definer
set search_path = pg_catalog, platform_replica as $$
declare authority platform_replica.agent_sources%rowtype;
begin
    if TG_OP = 'UPDATE' and NEW.agent_id is distinct from OLD.agent_id then
        raise exception 'agent_session_scope_immutable' using errcode = '42501';
    end if;
    if TG_OP <> 'INSERT' then perform platform_replica.check_agent_writer(OLD.agent_id); end if;
    if TG_OP <> 'DELETE' then
        perform platform_replica.check_agent_writer(NEW.agent_id);
        if TG_TABLE_NAME = 'sessions' then
            select * into authority from platform_replica.agent_sources where agent_id = NEW.agent_id;
            NEW.writer_publisher := coalesce(authority.publisher, 'office');
            NEW.writer_epoch := coalesce(authority.ownership_epoch, 0);
        end if;
        return NEW;
    end if;
    return OLD;
end $$;
revoke all on function platform_replica.guard_agent_write() from public;
drop trigger if exists replica_sessions_writer on platform_replica.sessions;
create trigger replica_sessions_writer before insert or update or delete on platform_replica.sessions
    for each row execute function platform_replica.guard_agent_write();
drop trigger if exists replica_agents_writer on platform_replica.agents;
create trigger replica_agents_writer before insert or update or delete on platform_replica.agents
    for each row execute function platform_replica.guard_agent_write();

create or replace function platform_replica.guard_source_state()
returns trigger language plpgsql security definer
set search_path = pg_catalog, platform_replica as $$
begin
    if TG_OP = 'UPDATE' and (NEW.agent_id, NEW.source_kind, NEW.mode, NEW.publisher,
        NEW.writer_role, NEW.source_instance_id, NEW.ownership_epoch)
        is not distinct from (OLD.agent_id, OLD.source_kind, OLD.mode, OLD.publisher,
        OLD.writer_role, OLD.source_instance_id, OLD.ownership_epoch) then
        -- UPDATE already locked this authority row. Validate its old owner
        -- without taking advisory after the row lock (the setter takes the
        -- inverse order). The row itself serializes this state-only change.
        if exists (select 1 from pg_roles where rolname = session_user and rolsuper)
           or (OLD.publisher = 'cloud' and OLD.writer_role = session_user
               and pg_has_role(session_user, 'platform_cloud_sync', 'member'))
           or (OLD.publisher = 'office'
               and pg_has_role(session_user, 'platform_replica_import', 'member')) then
            return NEW;
        end if;
        raise exception 'agent_writer_ownership' using errcode = '42501';
    end if;
    if not exists (select 1 from pg_roles where rolname = session_user and rolsuper) then
        raise exception 'agent_ownership_admin_required' using errcode = '42501';
    end if;
    if TG_OP = 'DELETE'
       or current_setting('platform_replica.ownership_agent', true) is distinct from NEW.agent_id then
        raise exception 'agent_ownership_use_set_agent_publisher' using errcode = '42501';
    end if;
    if TG_OP = 'UPDATE' then NEW.ownership_epoch := OLD.ownership_epoch + 1; end if;
    insert into platform_replica.agent_source_ownership_audit
        (agent_id, ownership_epoch, publisher, writer_role)
        values (NEW.agent_id, NEW.ownership_epoch, NEW.publisher, NEW.writer_role);
    return NEW;
end $$;
revoke all on function platform_replica.guard_source_state() from public;
drop trigger if exists replica_source_state on platform_replica.agent_sources;
create trigger replica_source_state before insert or update or delete on platform_replica.agent_sources
    for each row execute function platform_replica.guard_source_state();


-- Controlled installation/takeover/rollback API. The advisory lock must precede
-- the INSERT/UPDATE row lock, including first ownership assignment. Applications
-- cannot call this function or alter ownership columns.
create or replace function platform_replica.set_agent_publisher(
    agent_id text, source_kind text, mode text,
    publisher text, writer_role name, source_instance_id text
) returns void language plpgsql security definer
set search_path = pg_catalog, platform_replica as $$
declare
    target_agent alias for $1;
    target_source_kind alias for $2;
    target_mode alias for $3;
    target_publisher alias for $4;
    target_writer_role alias for $5;
    target_source_instance_id alias for $6;
    previous_marker text;
begin
    if not exists (select 1 from pg_roles where rolname = session_user and rolsuper) then
        raise exception 'agent_ownership_admin_required' using errcode = '42501';
    end if;
    perform platform_replica.lock_agent_writer(target_agent, true);
    previous_marker := current_setting('platform_replica.ownership_agent', true);
    perform set_config('platform_replica.ownership_agent', target_agent, true);
    if exists (select 1 from platform_replica.agent_sources authority where authority.agent_id = target_agent) then
        update platform_replica.agent_sources as authority set
            source_kind = target_source_kind, mode = target_mode,
            publisher = target_publisher, writer_role = target_writer_role,
            source_instance_id = target_source_instance_id,
            last_attempt_at = null, last_source_success_at = null,
            last_failure_at = null, last_error = null, source_sessions = null,
            source_turns = null, last_duration_ms = null
        where authority.agent_id = target_agent
          and (authority.source_kind, authority.mode, authority.publisher,
               authority.writer_role, authority.source_instance_id)
              is distinct from
              (target_source_kind, target_mode, target_publisher,
               target_writer_role, target_source_instance_id);
    else
        insert into platform_replica.agent_sources
            (agent_id, source_kind, mode, publisher, writer_role, source_instance_id)
            values (target_agent, target_source_kind, target_mode, target_publisher,
                    target_writer_role, target_source_instance_id);
    end if;
    perform set_config('platform_replica.ownership_agent', coalesce(previous_marker, ''), true);
end $$;
revoke all on function platform_replica.set_agent_publisher(text, text, text, text, name, text)
    from public, platform_replica_import, platform_cloud_sync, platform_replica_read;
