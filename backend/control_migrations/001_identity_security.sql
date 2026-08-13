revoke all on schema public from public;
revoke all on schema platform_control from public;

create type platform_control.user_role as enum
  ('member', 'management_viewer', 'platform_owner');

create table platform_control.directory_generations (
  generation_id uuid primary key,
  status text not null check (status in ('staging', 'complete', 'failed', 'superseded')),
  member_count integer not null default 0 check (member_count >= 0),
  department_count integer not null default 0 check (department_count >= 0),
  content_sha256 text,
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  check (content_sha256 is null or length(content_sha256) = 64)
);

create table platform_control.internal_users (
  internal_user_id uuid primary key,
  role platform_control.user_role not null default 'member',
  display_name text not null,
  status text not null check (status in ('active', 'inactive', 'disabled')),
  last_confirmed_generation_id uuid references platform_control.directory_generations(generation_id),
  locally_invalidated_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create unique index one_platform_owner
  on platform_control.internal_users ((role))
  where role = 'platform_owner' and status = 'active';

create table platform_control.provider_identities (
  provider_identity_id uuid primary key,
  internal_user_id uuid not null references platform_control.internal_users(internal_user_id) on delete cascade,
  subject_kind text not null check (subject_kind <> ''),
  lookup_hmac bytea not null check (octet_length(lookup_hmac) = 32),
  lookup_key_version integer not null check (lookup_key_version > 0),
  encrypted_provider_id bytea not null,
  encryption_key_version integer not null check (encryption_key_version > 0),
  verified_at timestamptz not null default now(),
  unique (subject_kind, lookup_hmac, lookup_key_version)
);
create index provider_identities_internal_user
  on platform_control.provider_identities (internal_user_id);

create table platform_control.directory_state (
  singleton boolean primary key default true check (singleton),
  active_generation_id uuid references platform_control.directory_generations(generation_id),
  last_complete_at timestamptz,
  updated_at timestamptz not null default now()
);
insert into platform_control.directory_state (singleton) values (true);

create table platform_control.directory_members (
  generation_id uuid not null references platform_control.directory_generations(generation_id) on delete cascade,
  member_key uuid not null,
  internal_user_id uuid references platform_control.internal_users(internal_user_id),
  subject_kind text not null,
  lookup_hmac bytea not null check (octet_length(lookup_hmac) = 32),
  lookup_key_version integer not null check (lookup_key_version > 0),
  encrypted_provider_id bytea not null,
  encryption_key_version integer not null check (encryption_key_version > 0),
  display_name text not null,
  status text not null check (status in ('active', 'inactive', 'disabled')),
  primary key (generation_id, member_key),
  unique (generation_id, subject_kind, lookup_hmac, lookup_key_version)
);
create index directory_members_active_lookup
  on platform_control.directory_members (generation_id, status, internal_user_id);

create table platform_control.directory_departments (
  generation_id uuid not null references platform_control.directory_generations(generation_id) on delete cascade,
  department_key uuid not null,
  subject_kind text not null default 'department',
  lookup_hmac bytea not null check (octet_length(lookup_hmac) = 32),
  lookup_key_version integer not null check (lookup_key_version > 0),
  encrypted_provider_id bytea not null,
  encryption_key_version integer not null check (encryption_key_version > 0),
  display_name text not null,
  primary key (generation_id, department_key),
  unique (generation_id, subject_kind, lookup_hmac, lookup_key_version)
);

create table platform_control.department_closure (
  generation_id uuid not null,
  ancestor_department_key uuid not null,
  descendant_department_key uuid not null,
  depth integer not null check (depth >= 0),
  primary key (generation_id, ancestor_department_key, descendant_department_key),
  foreign key (generation_id, ancestor_department_key)
    references platform_control.directory_departments(generation_id, department_key) on delete cascade,
  foreign key (generation_id, descendant_department_key)
    references platform_control.directory_departments(generation_id, department_key) on delete cascade
);
create index department_closure_descendant
  on platform_control.department_closure (generation_id, descendant_department_key, ancestor_department_key);

create table platform_control.member_departments (
  generation_id uuid not null,
  member_key uuid not null,
  department_key uuid not null,
  primary key (generation_id, member_key, department_key),
  foreign key (generation_id, member_key)
    references platform_control.directory_members(generation_id, member_key) on delete cascade,
  foreign key (generation_id, department_key)
    references platform_control.directory_departments(generation_id, department_key) on delete cascade
);
create index member_departments_department
  on platform_control.member_departments (generation_id, department_key, member_key);

create table platform_control.login_attempts (
  login_attempt_id uuid primary key,
  attempt_kind text not null check (attempt_kind in ('qr', 'in_client')),
  state_hash bytea not null,
  challenge_hash bytea,
  return_path text,
  expires_at timestamptz not null,
  consumed_at timestamptz,
  created_at timestamptz not null default now()
);
create index login_attempts_active
  on platform_control.login_attempts (expires_at) where consumed_at is null;

create table platform_control.web_sessions (
  session_id uuid primary key,
  internal_user_id uuid not null references platform_control.internal_users(internal_user_id) on delete cascade,
  token_hash bytea not null unique,
  csrf_hash bytea not null,
  idle_expires_at timestamptz not null,
  absolute_expires_at timestamptz not null,
  hard_stale_read_only boolean not null default false,
  revoked_at timestamptz,
  revoked_reason text,
  created_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  check (idle_expires_at <= absolute_expires_at)
);
create index web_sessions_active_user
  on platform_control.web_sessions (internal_user_id, absolute_expires_at)
  where revoked_at is null;

create table platform_control.observation_grants (
  observation_grant_id uuid primary key,
  agent_id text not null check (agent_id <> ''),
  viewer_internal_user_id uuid not null references platform_control.internal_users(internal_user_id),
  created_by uuid not null references platform_control.internal_users(internal_user_id),
  created_at timestamptz not null default now(),
  revoked_at timestamptz,
  revoked_by uuid references platform_control.internal_users(internal_user_id)
);
create unique index active_observation_grants_unique
  on platform_control.observation_grants (agent_id, viewer_internal_user_id)
  where revoked_at is null;

create table platform_control.stream_inbox (
  inbox_id bigint generated always as identity primary key,
  event_key text not null unique,
  event_type text not null,
  encrypted_payload bytea not null,
  encryption_key_version integer not null check (encryption_key_version > 0),
  status text not null default 'pending' check (status in ('pending', 'processing', 'processed', 'ignored', 'dead_letter')),
  attempts integer not null default 0 check (attempts >= 0),
  available_at timestamptz not null default now(),
  received_at timestamptz not null default now(),
  processed_at timestamptz,
  last_error_code text
);
create index stream_inbox_claim
  on platform_control.stream_inbox (status, available_at, inbox_id);

create table platform_control.sync_runs (
  sync_run_id uuid primary key,
  run_kind text not null check (run_kind in ('startup', 'scheduled', 'targeted', 'event')),
  status text not null check (status in ('running', 'succeeded', 'failed')),
  generation_id uuid references platform_control.directory_generations(generation_id),
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  member_count integer check (member_count >= 0),
  department_count integer check (department_count >= 0),
  error_code text
);
create index sync_runs_latest on platform_control.sync_runs (started_at desc);

create table platform_control.auth_rate_buckets (
  bucket_key bytea not null,
  bucket_kind text not null,
  window_started_at timestamptz not null,
  request_count integer not null default 0 check (request_count >= 0),
  token_balance numeric not null default 0 check (token_balance >= 0),
  updated_at timestamptz not null default now(),
  primary key (bucket_kind, bucket_key, window_started_at)
);
create index auth_rate_buckets_cleanup
  on platform_control.auth_rate_buckets (updated_at);

create table platform_control.audit_events (
  audit_event_id uuid primary key,
  actor_internal_user_id uuid references platform_control.internal_users(internal_user_id),
  event_type text not null check (event_type <> ''),
  target_type text not null check (target_type <> ''),
  target_internal_id text not null,
  request_id uuid not null,
  result text not null,
  reason_code text not null,
  sanitized_before_after jsonb not null default '{}'::jsonb,
  occurred_at timestamptz not null default now()
);
create index audit_events_governance_order
  on platform_control.audit_events (occurred_at desc, audit_event_id);
create index audit_events_request on platform_control.audit_events (request_id);

create function platform_control.append_audit_event(
  event_id uuid,
  actor_id uuid,
  event_name text,
  target_name text,
  target_id text,
  correlation_id uuid,
  event_result text,
  reason text,
  details jsonb
) returns uuid
language sql
security definer
set search_path = pg_catalog, platform_control
as $function$
  insert into platform_control.audit_events (
    audit_event_id, actor_internal_user_id, event_type, target_type,
    target_internal_id, request_id, result, reason_code, sanitized_before_after
  ) values (
    event_id, actor_id, event_name, target_name, target_id,
    correlation_id, event_result, reason, details
  ) returning audit_event_id
$function$;

create function platform_control.retain_audit_events(cutoff timestamptz)
returns bigint
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  database_now timestamptz := clock_timestamp();
  deleted_count bigint;
begin
  if cutoff > database_now - interval '365 days' then
    raise check_violation using message = 'audit cutoff must be at least 365 days old';
  end if;
  delete from platform_control.audit_events where occurred_at < cutoff;
  get diagnostics deleted_count = row_count;
  return deleted_count;
end
$function$;

revoke all on all tables in schema platform_control from public;
revoke all on all sequences in schema platform_control from public;
revoke all on all functions in schema platform_control from public;
grant usage on schema platform_control to
  platform_control_app,
  platform_directory_worker,
  platform_stream_ingest,
  platform_audit_append,
  platform_control_maintenance;

grant select on all tables in schema platform_control to platform_control_app;
grant insert, update on
  platform_control.internal_users,
  platform_control.provider_identities,
  platform_control.login_attempts,
  platform_control.web_sessions,
  platform_control.observation_grants,
  platform_control.auth_rate_buckets
to platform_control_app;

grant select, insert, update on
  platform_control.provider_identities,
  platform_control.directory_generations,
  platform_control.directory_state,
  platform_control.directory_members,
  platform_control.directory_departments,
  platform_control.department_closure,
  platform_control.member_departments,
  platform_control.stream_inbox,
  platform_control.sync_runs
to platform_directory_worker;
grant select on platform_control.internal_users to platform_directory_worker;
grant update (
  display_name,
  status,
  last_confirmed_generation_id,
  locally_invalidated_at,
  updated_at
) on platform_control.internal_users to platform_directory_worker;
grant delete on
  platform_control.directory_members,
  platform_control.directory_departments,
  platform_control.department_closure,
  platform_control.member_departments
to platform_directory_worker;

grant insert on platform_control.stream_inbox to platform_stream_ingest;
grant usage, select on sequence platform_control.stream_inbox_inbox_id_seq
  to platform_stream_ingest, platform_directory_worker;
grant execute on function platform_control.append_audit_event(
  uuid, uuid, text, text, text, uuid, text, text, jsonb
) to platform_audit_append;
grant execute on function platform_control.retain_audit_events(timestamptz)
  to platform_control_maintenance;
