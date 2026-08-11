\set ON_ERROR_STOP on

begin;

alter table platform_review.feedback_issues
  add column if not exists canonical_key text;

create unique index if not exists feedback_issues_canonical_key_uq
  on platform_review.feedback_issues(agent_id, canonical_key)
  where canonical_key is not null;

create table if not exists platform_review.feedback_release_handoffs (
  idempotency_key text primary key
    check (idempotency_key ~ '^sha256:[0-9a-f]{64}$'),
  batch_id text not null unique check (nullif(btrim(batch_id), '') is not null),
  agent_id text not null check (nullif(btrim(agent_id), '') is not null),
  payload_sha256 text not null
    check (payload_sha256 ~ '^[0-9a-f]{64}$'),
  release_name text not null
    check (nullif(btrim(release_name), '') is not null),
  deployment_sha text not null
    check (deployment_sha ~ '^[0-9a-f]{40}$'),
  import_status text not null
    check (import_status in ('processing', 'blocked', 'imported', 'terminal_failed')),
  failure_reason text not null default '',
  result jsonb not null default '{}'::jsonb
    check (jsonb_typeof(result) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists platform_review.feedback_release_handoff_events (
  id uuid primary key default gen_random_uuid(),
  idempotency_key text not null references
    platform_review.feedback_release_handoffs(idempotency_key) on delete restrict,
  event_type text not null check (nullif(btrim(event_type), '') is not null),
  actor text not null check (nullif(btrim(actor), '') is not null),
  reason text not null default '',
  before jsonb not null default '{}'::jsonb
    check (jsonb_typeof(before) = 'object'),
  after jsonb not null default '{}'::jsonb
    check (jsonb_typeof(after) = 'object'),
  created_at timestamptz not null default now()
);

create index if not exists feedback_release_handoff_events_key_idx
  on platform_review.feedback_release_handoff_events(
    idempotency_key, created_at, id
  );

create or replace function
  platform_review.prevent_imported_handoff_identity_mutation()
returns trigger
language plpgsql
as $$
begin
  if old.import_status = 'imported' and (
    new.idempotency_key is distinct from old.idempotency_key
    or new.batch_id is distinct from old.batch_id
    or new.agent_id is distinct from old.agent_id
    or new.payload_sha256 is distinct from old.payload_sha256
    or new.release_name is distinct from old.release_name
    or new.deployment_sha is distinct from old.deployment_sha
    or new.import_status is distinct from old.import_status
  ) then
    raise exception 'imported release handoff identity is immutable';
  end if;
  return new;
end
$$;

drop trigger if exists feedback_release_handoff_imported_immutable
  on platform_review.feedback_release_handoffs;
create trigger feedback_release_handoff_imported_immutable
before update on platform_review.feedback_release_handoffs
for each row execute function
  platform_review.prevent_imported_handoff_identity_mutation();

drop trigger if exists feedback_release_handoff_events_append_only
  on platform_review.feedback_release_handoff_events;
create trigger feedback_release_handoff_events_append_only
before update or delete on platform_review.feedback_release_handoff_events
for each row execute function platform_review.prevent_issue_event_mutation();

alter table platform_review.feedback_release_handoffs owner to flywheel_owner;
alter table platform_review.feedback_release_handoff_events owner to flywheel_owner;
alter function platform_review.prevent_imported_handoff_identity_mutation()
  owner to flywheel_owner;

revoke all on platform_review.feedback_release_handoffs from public;
revoke all on platform_review.feedback_release_handoff_events from public;
revoke all on platform_review.feedback_release_handoffs from platform_sync_writer;
revoke all on platform_review.feedback_release_handoff_events
  from platform_sync_writer;

grant select, insert, update on
  platform_review.feedback_release_handoffs to platform_review_writer;
revoke delete on platform_review.feedback_release_handoffs
  from platform_review_writer;
grant select, insert on platform_review.feedback_release_handoff_events
  to platform_review_writer;
revoke update, delete on platform_review.feedback_release_handoff_events
  from platform_review_writer;

grant select on platform_review.feedback_release_handoffs,
  platform_review.feedback_release_handoff_events to flywheel_analyst;

revoke all on schema platform_source_fae from platform_review_writer;
revoke all on schema platform_source_admin from platform_review_writer;
revoke all on schema platform_sync from platform_review_writer;
revoke all on schema platform_read from platform_review_writer;
revoke all on all tables in schema platform_read from platform_review_writer;
grant usage on schema platform_read to platform_review_writer;
grant select on platform_read.feedback, platform_read.turns
  to platform_review_writer;

commit;
