-- SDK requirement references and append-only review records.
create schema if not exists sdk;
revoke all on schema sdk from public;

create table sdk.members (
  internal_user_id uuid primary key references platform_control.internal_users(internal_user_id),
  role text not null check (role in ('viewer', 'contributor', 'reviewer', 'release_manager')),
  granted_by uuid not null references platform_control.internal_users(internal_user_id),
  granted_at timestamptz not null default clock_timestamp()
);

create table sdk.write_intents (
  actor_id uuid not null references platform_control.internal_users(internal_user_id),
  request_id uuid not null,
  kind text not null check (kind in ('create_requirement', 'create_task', 'review_requirement')),
  story_id bigint check (story_id > 0),
  digest char(64) not null check (digest ~ '^[0-9a-f]{64}$'),
  request_payload jsonb not null default '{}'::jsonb check (jsonb_typeof(request_payload) = 'object'),
  state text not null check (state in ('pending', 'succeeded', 'reconcile_required', 'rejected')),
  result jsonb,
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  primary key (actor_id, request_id),
  check ((state = 'succeeded') = (result is not null))
);
create unique index sdk_write_intents_external_id on sdk.write_intents(kind, ((result->>'id')::bigint))
  where state='succeeded' and kind in ('create_requirement','create_task');

create table sdk.review_events (
  actor_id uuid not null references platform_control.internal_users(internal_user_id),
  request_id uuid not null,
  story_id bigint not null check (story_id > 0),
  story_version text not null check (story_version ~ '^[1-9][0-9]*$'),
  decision text not null check (decision in ('approved', 'rejected', 'needs_changes')),
  reason text not null check (length(reason) between 1 and 2000),
  created_at timestamptz not null default clock_timestamp(),
  primary key (actor_id, request_id)
);
create index sdk_review_events_story_time on sdk.review_events(story_id, created_at);

create table sdk.task_links (
  story_id bigint not null check (story_id > 0),
  task_id bigint not null check (task_id > 0),
  actor_id uuid not null references platform_control.internal_users(internal_user_id),
  request_id uuid not null,
  created_at timestamptz not null default clock_timestamp(),
  primary key (task_id),
  unique (actor_id, request_id)
);
create index sdk_task_links_story on sdk.task_links(story_id, task_id);

create table sdk.member_events (
  actor_id uuid not null references platform_control.internal_users(internal_user_id),
  request_id uuid not null,
  target_user_id uuid not null references platform_control.internal_users(internal_user_id),
  role text not null check (role in ('viewer', 'contributor', 'reviewer', 'release_manager')),
  created_at timestamptz not null default clock_timestamp(),
  primary key (actor_id, request_id)
);

create table sdk.reconciliation_events (
  admin_actor_id uuid not null references platform_control.internal_users(internal_user_id),
  admin_request_id uuid not null,
  target_actor_id uuid not null,
  target_request_id uuid not null,
  external_id bigint not null check (external_id > 0),
  created_at timestamptz not null default clock_timestamp(),
  primary key (admin_actor_id, admin_request_id),
  foreign key (target_actor_id, target_request_id)
    references sdk.write_intents(actor_id, request_id)
);

do $migration$
declare selected_app name;
begin
  if current_database() = 'agent_platform_control' and current_user = 'platform_control_owner' then
    selected_app := 'platform_control_app';
  elsif current_database() = 'agent_platform_control_preview' and current_user = 'platform_control_owner_preview' then
    selected_app := 'platform_control_app_preview';
  else
    raise insufficient_privilege using message = 'SDK migration owner/environment mismatch';
  end if;
  execute format('grant usage on schema sdk to %I', selected_app);
  execute format('grant select, insert, update on sdk.members, sdk.write_intents to %I', selected_app);
  execute format('grant select, insert on sdk.review_events, sdk.member_events to %I', selected_app);
  execute format('grant select, insert on sdk.task_links to %I', selected_app);
  execute format('grant select, insert on sdk.reconciliation_events to %I', selected_app);
end
$migration$;
