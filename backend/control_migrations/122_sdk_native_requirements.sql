-- Native SDK requirement write intents and non-ZenTao source supplements.
create table sdk.native_create_intents (
  actor_id uuid not null references platform_control.internal_users(internal_user_id),
  request_id uuid not null,
  payload_digest text not null check (payload_digest ~ '^[0-9a-f]{64}$'),
  marker text not null unique check (marker ~ '^sdk-native:[0-9a-f]{32}:[0-9a-f]{32}$'),
  requested_assignee text,
  proposer_name text,
  device_platform text not null,
  state text not null check (state in ('pending','created','rejected')),
  story_id integer unique check (story_id > 0),
  rejection_status smallint check (rejection_status in (403,502,503)),
  rejection_detail text check (rejection_detail = 'sdk_create_rejected'),
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  primary key (actor_id,request_id),
  check ((state='pending' and story_id is null and rejection_status is null and rejection_detail is null) or
         (state='created' and story_id is not null and rejection_status is null and rejection_detail is null) or
         (state='rejected' and story_id is null and rejection_status is not null and rejection_detail is not null))
);
create unique index sdk_native_create_one_pending on sdk.native_create_intents(actor_id)
  where state='pending';

create table sdk.native_story_supplements (
  story_id integer primary key check (story_id > 0),
  proposer_name text check (proposer_name is null or length(proposer_name) <= 200),
  device_platform text check (device_platform is null or length(device_platform) between 1 and 500),
  updated_at timestamptz not null default clock_timestamp()
);

revoke all on sdk.native_create_intents, sdk.native_story_supplements from public;

do $migration$
declare selected_app name;
begin
  if current_database() = 'agent_platform_control' and current_user = 'platform_control_owner' then
    selected_app := 'platform_control_app';
  elsif current_database() = 'agent_platform_control_preview' and current_user = 'platform_control_owner_preview' then
    selected_app := 'platform_control_app_preview';
  else
    raise insufficient_privilege using message = 'SDK native requirement migration owner/environment mismatch';
  end if;
  execute format('grant select, insert, update on sdk.native_create_intents, sdk.native_story_supplements to %I', selected_app);
end
$migration$;
