-- Preserve semantic source fields and safely pre-create directory matched assignees.
create table sdk.directory_accounts (
  member_key uuid primary key,
  account text not null unique check (account ~ '^sdk_[a-z2-7]{26}$'),
  marker text not null unique check (marker ~ '^sdk:[0-9a-f]{32}$'),
  display_name text not null check (length(display_name) between 1 and 100),
  state text not null check (state in ('reserved','ready')),
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp()
);

create table sdk.plan_story_sources (
  plan_row_id bigint primary key references sdk.plan_rows(id),
  story_id integer not null unique check (story_id > 0),
  proposer_name text,
  responsible_raw text not null,
  assignee_account text,
  assignment_state text not null check (assignment_state in
    ('matched','multiple','uncertain','not_found','duplicate_name','missing','pending','inactive_identity','conflict')),
  planned_start date,
  planned_end date,
  source_priority smallint check (source_priority between 1 and 3),
  source_version text not null,
  source_risk text not null,
  source_status text not null,
  source_note text not null,
  updated_at timestamptz not null default clock_timestamp(),
  check (planned_start is null or planned_end is null or planned_start <= planned_end),
  check (assignment_state <> 'matched' or assignee_account is not null)
);

create function sdk.prepared_account_for(selected_user uuid)
returns table(account text, marker text)
language sql stable security definer
set search_path = pg_catalog, sdk, platform_control
as $function$
  select prepared.account,prepared.marker
  from platform_control.directory_state state
  join platform_control.directory_members member
    on member.generation_id=state.active_generation_id
  join sdk.directory_accounts prepared on prepared.member_key=member.member_key
  where state.singleton and member.internal_user_id=selected_user
    and member.status='active' and prepared.state='ready'
    and sdk.sso_eligible(selected_user)
  limit 1;
$function$;

revoke all on function sdk.prepared_account_for(uuid) from public;

create function sdk.scoped_people()
returns table(member_key uuid, internal_user_id uuid, display_name text, bound_account text,
              intent_account text, intent_state text, identity_available boolean)
language sql stable security definer
set search_path = pg_catalog, sdk, platform_control
as $function$
  select distinct member.member_key,member.internal_user_id,member.display_name,
         binding.account,intent.account,intent.state,
         (member.internal_user_id is null or
          (person.status='active' and person.locally_invalidated_at is null))
  from platform_control.directory_state state
  join platform_control.directory_generations generation
    on generation.generation_id=state.active_generation_id
  join platform_control.directory_members member
    on member.generation_id=generation.generation_id
  join platform_control.member_departments membership
    on membership.generation_id=member.generation_id and membership.member_key=member.member_key
  join platform_control.department_closure closure
    on closure.generation_id=membership.generation_id
   and closure.descendant_department_key=membership.department_key
  join sdk.allowed_departments allowed
    on allowed.department_key=closure.ancestor_department_key
  left join sdk.sso_bindings binding on binding.internal_user_id=member.internal_user_id
  left join sdk.provision_intents intent on intent.internal_user_id=member.internal_user_id
  left join platform_control.internal_users person on person.internal_user_id=member.internal_user_id
  where state.singleton and generation.status='complete'
    and generation.completed_at<=clock_timestamp()
    and generation.completed_at>clock_timestamp()-interval '24 hours'
    and member.subject_kind='employee' and member.status='active';
$function$;

revoke all on function sdk.scoped_people() from public;

do $migration$
declare selected_app name;
begin
  if current_database() = 'agent_platform_control' and current_user = 'platform_control_owner' then
    selected_app := 'platform_control_app';
  elsif current_database() = 'agent_platform_control_preview' and current_user = 'platform_control_owner_preview' then
    selected_app := 'platform_control_app_preview';
  else
    raise insufficient_privilege using message = 'SDK semantic migration owner/environment mismatch';
  end if;
  execute format('grant select, insert, update on sdk.directory_accounts, sdk.plan_story_sources to %I', selected_app);
  execute format('grant execute on function sdk.prepared_account_for(uuid), sdk.scoped_people() to %I', selected_app);
end
$migration$;
