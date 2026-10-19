-- Append-only SDK baseline and release evidence. Only candidate_state and replay results are mutable.
create table sdk.candidates (
  id bigint generated always as identity primary key,
  story_id bigint not null check (story_id > 0),
  story_version text not null check (story_version ~ '^[1-9][0-9]*$'),
  task_ids jsonb not null check (jsonb_typeof(task_ids) = 'array'),
  manifest jsonb not null check (jsonb_typeof(manifest) = 'object'),
  targets jsonb not null check (jsonb_typeof(targets) = 'array'),
  creator_id uuid not null references platform_control.internal_users(internal_user_id),
  request_id uuid not null,
  created_at timestamptz not null default clock_timestamp()
);
create index sdk_candidates_story on sdk.candidates(story_id, id desc);

create table sdk.candidate_state (
  candidate_id bigint primary key references sdk.candidates(id),
  evidence_version bigint not null default 0 check (evidence_version >= 0)
);

create table sdk.builds (
  id bigint generated always as identity primary key,
  candidate_id bigint not null references sdk.candidates(id),
  actor_id uuid not null references platform_control.internal_users(internal_user_id),
  request_id uuid not null,
  os text not null,
  arch text not null,
  status text not null check (status in ('success','failed','cancelled','timeout')),
  command text not null,
  log_uri text not null,
  artifact_name text,
  artifact_sha256 char(64) check (artifact_sha256 ~ '^[0-9a-f]{64}$'),
  artifact_size bigint check (artifact_size > 0),
  artifact_uri text,
  created_at timestamptz not null default clock_timestamp(),
  check ((status = 'success') = (artifact_name is not null and artifact_sha256 is not null and artifact_size is not null and artifact_uri is not null))
);
create index sdk_builds_candidate on sdk.builds(candidate_id, id);

create table sdk.test_runs (
  id bigint generated always as identity primary key,
  build_id bigint not null references sdk.builds(id),
  actor_id uuid not null references platform_control.internal_users(internal_user_id),
  request_id uuid not null,
  suite text not null check (suite in ('unit','hardware')),
  status text not null check (status in ('passed','failed','blocked','skipped')),
  passed integer not null check (passed >= 0),
  failed integer not null check (failed >= 0),
  skipped integer not null check (skipped >= 0),
  report_uri text not null,
  device text not null,
  firmware text not null,
  created_at timestamptz not null default clock_timestamp()
);
create index sdk_test_runs_build on sdk.test_runs(build_id, suite, id);

create table sdk.approvals (
  id bigint generated always as identity primary key,
  candidate_id bigint not null references sdk.candidates(id),
  actor_id uuid not null references platform_control.internal_users(internal_user_id),
  request_id uuid not null,
  evidence_version bigint not null check (evidence_version > 0),
  note text not null,
  created_at timestamptz not null default clock_timestamp()
);
create index sdk_approvals_candidate on sdk.approvals(candidate_id, id);

create table sdk.releases (
  id bigint generated always as identity primary key,
  candidate_id bigint not null references sdk.candidates(id),
  approval_id bigint not null references sdk.approvals(id),
  actor_id uuid not null references platform_control.internal_users(internal_user_id),
  request_id uuid not null,
  channel text not null,
  rollback_uri text not null,
  released_at timestamptz not null,
  artifact_sha256s jsonb not null check (jsonb_typeof(artifact_sha256s) = 'array'),
  created_at timestamptz not null default clock_timestamp()
);
create index sdk_releases_candidate on sdk.releases(candidate_id, id);

create table sdk.evidence_requests (
  actor_id uuid not null references platform_control.internal_users(internal_user_id),
  request_id uuid not null,
  digest char(64) not null check (digest ~ '^[0-9a-f]{64}$'),
  kind text not null check (kind in ('candidate','build','test','approval','release')),
  object_id bigint check (object_id > 0),
  result jsonb not null,
  created_at timestamptz not null default clock_timestamp(),
  primary key (actor_id, request_id)
);
create unique index sdk_evidence_requests_object on sdk.evidence_requests(kind,object_id) where object_id is not null;

alter table sdk.candidates add constraint sdk_candidates_request_fk foreign key (creator_id,request_id) references sdk.evidence_requests(actor_id,request_id);
alter table sdk.builds add constraint sdk_builds_request_fk foreign key (actor_id,request_id) references sdk.evidence_requests(actor_id,request_id);
alter table sdk.test_runs add constraint sdk_tests_request_fk foreign key (actor_id,request_id) references sdk.evidence_requests(actor_id,request_id);
alter table sdk.approvals add constraint sdk_approvals_request_fk foreign key (actor_id,request_id) references sdk.evidence_requests(actor_id,request_id);
alter table sdk.releases add constraint sdk_releases_request_fk foreign key (actor_id,request_id) references sdk.evidence_requests(actor_id,request_id);

do $migration$
declare selected_app name;
begin
  if current_database() = 'agent_platform_control' and current_user = 'platform_control_owner' then
    selected_app := 'platform_control_app';
  elsif current_database() = 'agent_platform_control_preview' and current_user = 'platform_control_owner_preview' then
    selected_app := 'platform_control_app_preview';
  else
    raise insufficient_privilege using message = 'SDK evidence migration owner/environment mismatch';
  end if;
  execute format('grant select, insert on sdk.candidates, sdk.builds, sdk.test_runs, sdk.approvals, sdk.releases to %I', selected_app);
  execute format('grant select, insert, update on sdk.candidate_state, sdk.evidence_requests to %I', selected_app);
  execute format('grant usage, select on all sequences in schema sdk to %I', selected_app);
end
$migration$;
