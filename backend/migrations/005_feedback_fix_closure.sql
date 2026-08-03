\set ON_ERROR_STOP on

begin;

create extension if not exists pgcrypto;

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'platform_review_writer') then
    create role platform_review_writer login;
  end if;
end
$$;

create schema if not exists platform_review authorization flywheel_owner;

create table if not exists platform_review.feedback_issues (
  id uuid primary key default gen_random_uuid(),
  agent_id text not null,
  origin_turn_key text,
  title text not null,
  priority text not null check (priority in ('P0', 'P1', 'P2', 'P3')),
  failure_layer text check (failure_layer in (
    'channel', 'context', 'guardrail', 'schema', 'planner',
    'capability_evidence', 'coverage', 'synthesis', 'outcome', 'trace_eval'
  )),
  secondary_layers text[] not null default '{}',
  root_cause text not null default '',
  impact_scope text not null default '',
  owner text,
  fix_ready_at timestamptz,
  disposition text not null default 'actionable' check (
    disposition in ('actionable', 'duplicate', 'not_actionable', 'wont_fix')
  ),
  canonical_issue_id uuid references platform_review.feedback_issues(id),
  disposition_reason text not null default '',
  created_by text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  row_version bigint not null default 1 check (row_version > 0),
  check (canonical_issue_id is null or canonical_issue_id <> id),
  check (disposition <> 'duplicate' or canonical_issue_id is not null),
  check (disposition = 'actionable' or nullif(btrim(disposition_reason), '') is not null),
  check (disposition <> 'wont_fix' or nullif(btrim(owner), '') is not null)
);

create unique index if not exists feedback_issues_origin_turn_uq
  on platform_review.feedback_issues(agent_id, origin_turn_key)
  where origin_turn_key is not null;

create table if not exists platform_review.feedback_issue_links (
  id uuid primary key default gen_random_uuid(),
  issue_id uuid not null references platform_review.feedback_issues(id),
  agent_id text not null,
  source_turn_key text not null,
  source_feedback_keys text[] not null default '{}',
  link_role text not null default 'primary' check (
    link_role in ('primary', 'secondary')
  ),
  linked_by text not null,
  linked_at timestamptz not null default now(),
  active boolean not null default true,
  link_reason text not null default ''
);

create unique index if not exists feedback_links_one_active_primary
  on platform_review.feedback_issue_links(agent_id, source_turn_key)
  where active and link_role = 'primary';

create unique index if not exists feedback_link_issue_turn_uq
  on platform_review.feedback_issue_links(issue_id, agent_id, source_turn_key)
  where active;

create table if not exists platform_review.feedback_fix_evidence (
  id uuid primary key default gen_random_uuid(),
  issue_id uuid not null references platform_review.feedback_issues(id),
  evidence_type text not null check (
    evidence_type in ('commit', 'pull_request', 'merge', 'deployment')
  ),
  repository text not null default '',
  reference text not null,
  url text not null default '',
  version text not null default '',
  commit_sha text not null default '',
  release_manifest_ref text not null default '',
  environment text not null default '',
  observed_at timestamptz not null,
  observed_by text not null,
  verification_status text not null default 'pending' check (
    verification_status in ('pending', 'verified', 'rejected', 'revoked')
  ),
  verification_details jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists feedback_fix_evidence_issue_idx
  on platform_review.feedback_fix_evidence(issue_id, observed_at desc);

create table if not exists platform_review.feedback_replay_runs (
  id uuid primary key default gen_random_uuid(),
  issue_id uuid not null references platform_review.feedback_issues(id),
  issue_link_id uuid not null references platform_review.feedback_issue_links(id),
  idempotency_key text not null,
  attempt_no integer not null check (attempt_no > 0),
  target_kind text not null default 'dev' check (target_kind = 'dev'),
  target_url_fingerprint text not null,
  expected_version text not null,
  actual_version text not null default '',
  expected_git_sha text not null,
  actual_git_sha text not null default '',
  configured_model text not null default '',
  actual_model text not null default '',
  actual_model_source text not null default '',
  question text not null,
  context_snapshot jsonb not null default '[]'::jsonb,
  attachment_manifest jsonb not null default '[]'::jsonb,
  answer text not null default '',
  sources jsonb not null default '[]'::jsonb,
  done jsonb not null default '{}'::jsonb,
  trace_id text not null default '',
  duration_ms bigint check (duration_ms is null or duration_ms >= 0),
  execution_status text not null default 'running' check (
    execution_status in ('running', 'succeeded', 'failed', 'blocked')
  ),
  runtime_gate text not null default 'pending' check (
    runtime_gate in ('pending', 'passed', 'failed')
  ),
  runtime_failure_reason text not null default '',
  semantic_verdict text not null default 'pending' check (
    semantic_verdict in ('pending', 'passed', 'failed')
  ),
  review_method text check (review_method in ('codex', 'human_fae')),
  reviewer text,
  review_reason text not null default '',
  reviewed_at timestamptz,
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  unique (issue_link_id, idempotency_key),
  unique (issue_link_id, attempt_no),
  check (
    semantic_verdict = 'pending'
    or (
      runtime_gate = 'passed'
      and review_method is not null
      and nullif(btrim(reviewer), '') is not null
      and nullif(btrim(review_reason), '') is not null
      and reviewed_at is not null
    )
  )
);

create table if not exists platform_review.feedback_issue_events (
  id uuid primary key default gen_random_uuid(),
  issue_id uuid not null references platform_review.feedback_issues(id),
  event_type text not null,
  actor text not null,
  reason text not null default '',
  before jsonb not null default '{}'::jsonb,
  after jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists feedback_issue_events_issue_idx
  on platform_review.feedback_issue_events(issue_id, created_at, id);

create or replace function platform_review.validate_issue_layers()
returns trigger
language plpgsql
as $$
begin
  if exists (
    select 1
    from unnest(new.secondary_layers) as layer
    where layer not in (
      'channel', 'context', 'guardrail', 'schema', 'planner',
      'capability_evidence', 'coverage', 'synthesis', 'outcome', 'trace_eval'
    )
  ) then
    raise exception 'invalid secondary failure layer';
  end if;
  return new;
end
$$;

drop trigger if exists feedback_issues_validate_layers
  on platform_review.feedback_issues;
create trigger feedback_issues_validate_layers
before insert or update of secondary_layers
on platform_review.feedback_issues
for each row execute function platform_review.validate_issue_layers();

create or replace function platform_review.prevent_fix_evidence_rewrite()
returns trigger
language plpgsql
as $$
begin
  if new.issue_id is distinct from old.issue_id
    or new.evidence_type is distinct from old.evidence_type
    or new.repository is distinct from old.repository
    or new.reference is distinct from old.reference
    or new.url is distinct from old.url
    or new.version is distinct from old.version
    or new.commit_sha is distinct from old.commit_sha
    or new.release_manifest_ref is distinct from old.release_manifest_ref
    or new.environment is distinct from old.environment
    or new.observed_at is distinct from old.observed_at
    or new.observed_by is distinct from old.observed_by
    or new.created_at is distinct from old.created_at then
    raise exception 'fix evidence facts are immutable';
  end if;
  return new;
end
$$;

drop trigger if exists feedback_fix_evidence_immutable
  on platform_review.feedback_fix_evidence;
create trigger feedback_fix_evidence_immutable
before update on platform_review.feedback_fix_evidence
for each row execute function platform_review.prevent_fix_evidence_rewrite();

create or replace function platform_review.prevent_completed_replay_mutation()
returns trigger
language plpgsql
as $$
begin
  if old.execution_status <> 'running' and (
    new.issue_id is distinct from old.issue_id
    or new.issue_link_id is distinct from old.issue_link_id
    or new.idempotency_key is distinct from old.idempotency_key
    or new.attempt_no is distinct from old.attempt_no
    or new.target_kind is distinct from old.target_kind
    or new.target_url_fingerprint is distinct from old.target_url_fingerprint
    or new.expected_version is distinct from old.expected_version
    or new.actual_version is distinct from old.actual_version
    or new.expected_git_sha is distinct from old.expected_git_sha
    or new.actual_git_sha is distinct from old.actual_git_sha
    or new.configured_model is distinct from old.configured_model
    or new.actual_model is distinct from old.actual_model
    or new.actual_model_source is distinct from old.actual_model_source
    or new.question is distinct from old.question
    or new.context_snapshot is distinct from old.context_snapshot
    or new.attachment_manifest is distinct from old.attachment_manifest
    or new.answer is distinct from old.answer
    or new.sources is distinct from old.sources
    or new.done is distinct from old.done
    or new.trace_id is distinct from old.trace_id
    or new.duration_ms is distinct from old.duration_ms
    or new.execution_status is distinct from old.execution_status
    or new.runtime_gate is distinct from old.runtime_gate
    or new.runtime_failure_reason is distinct from old.runtime_failure_reason
    or new.started_at is distinct from old.started_at
    or new.completed_at is distinct from old.completed_at
  ) then
    raise exception 'completed replay evidence is immutable';
  end if;
  return new;
end
$$;

drop trigger if exists feedback_replay_completed_immutable
  on platform_review.feedback_replay_runs;
create trigger feedback_replay_completed_immutable
before update on platform_review.feedback_replay_runs
for each row execute function platform_review.prevent_completed_replay_mutation();

create or replace function platform_review.prevent_issue_event_mutation()
returns trigger
language plpgsql
as $$
begin
  raise exception 'feedback issue events are append-only';
end
$$;

drop trigger if exists feedback_issue_events_append_only
  on platform_review.feedback_issue_events;
create trigger feedback_issue_events_append_only
before update or delete on platform_review.feedback_issue_events
for each row execute function platform_review.prevent_issue_event_mutation();

create or replace function platform_review.replay_runtime_qualified(
  replay platform_review.feedback_replay_runs,
  deployment_sha text,
  deployment_at timestamptz
)
returns boolean
language sql
stable
as $$
  select replay.target_kind = 'dev'
    and replay.execution_status = 'succeeded'
    and replay.runtime_gate = 'passed'
    and replay.started_at >= deployment_at
    and replay.actual_git_sha = deployment_sha
    and nullif(btrim(replay.answer), '') is not null
    and nullif(btrim(replay.trace_id), '') is not null
    and nullif(btrim(replay.actual_model), '') is not null
    and replay.actual_model = replay.configured_model
    and nullif(btrim(replay.actual_model_source), '') is not null
    and coalesce(replay.done->>'fallback_used', 'false') = 'false'
    and coalesce(replay.done#>>'{loop,truncation_rounds}', '0') = '0'
    and replay.done#>'{loop,provider_model_echo,complete}' = 'true'::jsonb
    and replay.done#>'{loop,provider_model_echo,consistent}' = 'true'::jsonb
    and coalesce(replay.done->>'error', '') = ''
    and replay.runtime_failure_reason = ''
$$;

create or replace view platform_review.issue_progress_inputs as
select
  issue.id as issue_id,
  (
    select count(*)
    from platform_review.feedback_issue_links link
    where link.issue_id = issue.id and link.active
  )::bigint as active_link_count,
  (
    select count(*)
    from platform_review.feedback_fix_evidence evidence
    where evidence.issue_id = issue.id
      and evidence.evidence_type = 'merge'
      and evidence.verification_status = 'verified'
  )::bigint as verified_merge_count,
  (
    select count(*)
    from platform_review.feedback_fix_evidence evidence
    where evidence.issue_id = issue.id
      and evidence.evidence_type = 'deployment'
      and evidence.verification_status = 'verified'
      and evidence.verification_details->'contains_merge' = 'true'::jsonb
  )::bigint as verified_deployment_count,
  (
    select evidence.commit_sha
    from platform_review.feedback_fix_evidence evidence
    where evidence.issue_id = issue.id
      and evidence.evidence_type = 'deployment'
      and evidence.verification_status = 'verified'
      and evidence.verification_details->'contains_merge' = 'true'::jsonb
    order by evidence.observed_at desc, evidence.created_at desc
    limit 1
  ) as latest_deployment_sha,
  (
    select count(distinct link.id)
    from platform_review.feedback_issue_links link
    where link.issue_id = issue.id
      and link.active
      and exists (
        select 1
        from platform_review.feedback_replay_runs replay
        join platform_review.feedback_fix_evidence deployment
          on deployment.issue_id = issue.id
         and deployment.evidence_type = 'deployment'
         and deployment.verification_status = 'verified'
         and deployment.verification_details->'contains_merge' = 'true'::jsonb
        where replay.issue_link_id = link.id
          and platform_review.replay_runtime_qualified(
            replay, deployment.commit_sha, deployment.observed_at
          )
      )
  )::bigint as qualified_replay_link_count,
  (
    select count(distinct link.id)
    from platform_review.feedback_issue_links link
    where link.issue_id = issue.id
      and link.active
      and exists (
        select 1
        from platform_review.feedback_replay_runs replay
        join platform_review.feedback_fix_evidence deployment
          on deployment.issue_id = issue.id
         and deployment.evidence_type = 'deployment'
         and deployment.verification_status = 'verified'
         and deployment.verification_details->'contains_merge' = 'true'::jsonb
        where replay.issue_link_id = link.id
          and platform_review.replay_runtime_qualified(
            replay, deployment.commit_sha, deployment.observed_at
          )
          and replay.semantic_verdict = 'passed'
          and replay.review_method in ('codex', 'human_fae')
          and nullif(btrim(replay.reviewer), '') is not null
          and nullif(btrim(replay.review_reason), '') is not null
      )
  )::bigint as semantic_passed_link_count
from platform_review.feedback_issues issue;

alter table platform_review.feedback_issues owner to flywheel_owner;
alter table platform_review.feedback_issue_links owner to flywheel_owner;
alter table platform_review.feedback_fix_evidence owner to flywheel_owner;
alter table platform_review.feedback_replay_runs owner to flywheel_owner;
alter table platform_review.feedback_issue_events owner to flywheel_owner;
alter function platform_review.validate_issue_layers() owner to flywheel_owner;
alter function platform_review.prevent_fix_evidence_rewrite() owner to flywheel_owner;
alter function platform_review.prevent_completed_replay_mutation() owner to flywheel_owner;
alter function platform_review.prevent_issue_event_mutation() owner to flywheel_owner;
alter function platform_review.replay_runtime_qualified(
  platform_review.feedback_replay_runs, text, timestamptz
) owner to flywheel_owner;
alter view platform_review.issue_progress_inputs owner to flywheel_owner;

revoke all on schema platform_review from public;
revoke all on schema platform_review from platform_sync_writer;
revoke all on schema platform_source_fae from platform_review_writer;
revoke all on schema platform_source_admin from platform_review_writer;
revoke all on schema platform_sync from platform_review_writer;

grant usage on schema platform_read to platform_review_writer;
grant select on platform_read.feedback, platform_read.turns
  to platform_review_writer;
grant usage on schema platform_review to platform_review_writer;
grant select, insert, update on all tables in schema platform_review
  to platform_review_writer;
revoke update, delete on platform_review.feedback_issue_events
  from platform_review_writer;

grant usage on schema platform_review to flywheel_analyst;
grant select on all tables in schema platform_review to flywheel_analyst;

alter default privileges for role flywheel_owner in schema platform_review
  grant select, insert, update on tables to platform_review_writer;
alter default privileges for role flywheel_owner in schema platform_review
  grant select on tables to flywheel_analyst;

commit;
