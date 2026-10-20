-- SDK admission from the verified directory. An empty allowlist grants nobody new.
alter table sdk.members add column grant_source text not null default 'manual'
  check (grant_source in ('manual','directory'));

create table sdk.allowed_departments (
  department_key uuid primary key,
  granted_by uuid not null references platform_control.internal_users(internal_user_id),
  granted_at timestamptz not null default clock_timestamp()
);

create table sdk.department_policy_events (
  event_id bigserial primary key,
  department_key uuid not null,
  actor_id uuid not null references platform_control.internal_users(internal_user_id),
  enabled boolean not null,
  recorded_at timestamptz not null default clock_timestamp()
);

create table sdk.provision_intents (
  internal_user_id uuid primary key references platform_control.internal_users(internal_user_id),
  account text not null unique check (account ~ '^sdk_[a-z2-7]{26}$'),
  marker text not null unique check (marker ~ '^sdk:[0-9a-f]{32}$'),
  state text not null check (state in ('pending','reconcile_required','bound','suspended')),
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  last_access_attempt_at timestamptz
);

create function sdk.sso_department_eligible(selected_user uuid) returns boolean
language sql stable security definer
set search_path = pg_catalog, sdk, platform_control
as $function$
  select exists (
    select 1
    from platform_control.directory_state state
    join platform_control.directory_generations generation
      on generation.generation_id=state.active_generation_id
    join platform_control.directory_members directory_member
      on directory_member.generation_id=generation.generation_id
    join platform_control.member_departments membership
      on membership.generation_id=directory_member.generation_id
     and membership.member_key=directory_member.member_key
    join platform_control.department_closure closure
      on closure.generation_id=membership.generation_id
     and closure.descendant_department_key=membership.department_key
    join sdk.allowed_departments allowed
      on allowed.department_key=closure.ancestor_department_key
    where state.singleton
      and generation.status='complete'
      and generation.completed_at<=clock_timestamp()
      and generation.completed_at>clock_timestamp()-interval '24 hours'
      and directory_member.internal_user_id=selected_user
      and directory_member.status='active'
  );
$function$;

create function sdk.sso_eligible(selected_user uuid) returns boolean
language sql stable security definer
set search_path = pg_catalog, sdk, platform_control
as $function$
  select exists (
    select 1 from platform_control.internal_users person
    where person.internal_user_id=selected_user
      and person.status='active'
      and person.locally_invalidated_at is null
      and exists (
        select 1 from platform_control.directory_state state
        join platform_control.directory_generations generation
          on generation.generation_id=state.active_generation_id
        join platform_control.directory_members directory_member
          on directory_member.generation_id=generation.generation_id
        where state.singleton and generation.status='complete'
          and generation.completed_at<=clock_timestamp()
          and generation.completed_at>clock_timestamp()-interval '24 hours'
          and directory_member.internal_user_id=selected_user
          and directory_member.subject_kind='employee'
          and directory_member.status='active'
      )
      and (
        exists (select 1 from sdk.members member
                where member.internal_user_id=selected_user and member.grant_source='manual')
        or sdk.sso_department_eligible(selected_user)
      )
  );
$function$;

create function sdk.sso_identity(selected_user uuid)
returns table(eligible boolean, display_name text, account text, provision_state text)
language sql stable security definer
set search_path = pg_catalog, sdk, platform_control
as $function$
  select sdk.sso_eligible(selected_user), person.display_name, binding.account, intent.state
  from platform_control.internal_users person
  left join sdk.sso_bindings binding on binding.internal_user_id=person.internal_user_id
  left join sdk.provision_intents intent on intent.internal_user_id=person.internal_user_id
  where person.internal_user_id=selected_user;
$function$;

create function sdk.set_allowed_department(selected_actor uuid, selected_department uuid, enable boolean)
returns boolean
language plpgsql security definer
set search_path = pg_catalog, sdk, platform_control
as $function$
begin
  if selected_actor is null or selected_department is null or enable is null or
     not exists (select 1 from platform_control.internal_users
                 where internal_user_id=selected_actor and role='platform_owner'
                   and status='active' and locally_invalidated_at is null) then
    return false;
  end if;
  if enable then
    if not exists (
      select 1 from platform_control.directory_state state
      join platform_control.directory_generations generation
        on generation.generation_id=state.active_generation_id
      join platform_control.directory_departments department
        on department.generation_id=generation.generation_id
      where state.singleton and department.department_key=selected_department
        and department.parent_department_key is not null
        and generation.status='complete'
        and generation.completed_at<=clock_timestamp()
        and generation.completed_at>clock_timestamp()-interval '24 hours'
    ) then return false; end if;
    insert into sdk.allowed_departments(department_key,granted_by)
      values (selected_department,selected_actor) on conflict do nothing;
  else
    delete from sdk.allowed_departments where department_key=selected_department;
  end if;
  insert into sdk.department_policy_events(department_key,actor_id,enabled)
    values (selected_department,selected_actor,enable);
  return true;
end
$function$;

create function sdk.sso_department_options() returns jsonb
language sql stable security definer
set search_path = pg_catalog, sdk, platform_control
as $function$
  select jsonb_agg(jsonb_build_object(
    'id',department.department_key,
    'name',department.display_name,
    'selected',allowed.department_key is not null,
    'root',department.parent_department_key is null,
    'path',(
      select string_agg(ancestor.display_name,' / ' order by lineage.depth desc)
      from platform_control.department_closure lineage
      join platform_control.directory_departments ancestor
        on ancestor.generation_id=lineage.generation_id
       and ancestor.department_key=lineage.ancestor_department_key
      where lineage.generation_id=generation.generation_id
        and lineage.descendant_department_key=department.department_key
    ),
    'people',(
      select count(distinct scoped_member.internal_user_id)
      from platform_control.department_closure scope
      join platform_control.member_departments membership
        on membership.generation_id=scope.generation_id
       and membership.department_key=scope.descendant_department_key
      join platform_control.directory_members scoped_member
        on scoped_member.generation_id=membership.generation_id
       and scoped_member.member_key=membership.member_key
      where scope.generation_id=generation.generation_id
        and scope.ancestor_department_key=department.department_key
        and scoped_member.status='active'
    )
  ) order by department.display_name,department.department_key)
  from platform_control.directory_state state
  join platform_control.directory_generations generation
    on generation.generation_id=state.active_generation_id
  join platform_control.directory_departments department
    on department.generation_id=generation.generation_id
  left join sdk.allowed_departments allowed
    on allowed.department_key=department.department_key
  where state.singleton and generation.status='complete'
    and generation.completed_at<=clock_timestamp()
    and generation.completed_at>clock_timestamp()-interval '24 hours';
$function$;

create function sdk.sso_access_changes(max_rows integer default 100)
returns table(internal_user_id uuid, account text, marker text, state text, eligible boolean)
language sql stable security definer
set search_path = pg_catalog, sdk, platform_control
as $function$
  select intent.internal_user_id,intent.account,intent.marker,intent.state,
         sdk.sso_eligible(intent.internal_user_id)
  from sdk.provision_intents intent
  join sdk.members member on member.internal_user_id=intent.internal_user_id
    and member.grant_source='directory'
  where max_rows between 1 and 100
    and exists (
      select 1 from platform_control.directory_state directory_state
      join platform_control.directory_generations generation
        on generation.generation_id=directory_state.active_generation_id
      where directory_state.singleton and generation.status='complete'
        and generation.completed_at<=clock_timestamp()
        and generation.completed_at>clock_timestamp()-interval '24 hours'
    )
    and (intent.last_access_attempt_at is null
      or intent.last_access_attempt_at < clock_timestamp() - interval '5 minutes')
    and ((intent.state='bound' and not sdk.sso_eligible(intent.internal_user_id))
      or (intent.state='suspended' and sdk.sso_eligible(intent.internal_user_id)))
  order by coalesce(intent.last_access_attempt_at,intent.updated_at),intent.internal_user_id
  limit max_rows;
$function$;

-- Existing ticket functions are deliberately tightened as well as the new target-aware issuer.
create or replace function sdk.issue_sso_ticket(
  selected_user uuid, selected_session uuid, selected_digest bytea, selected_target text
) returns boolean
language plpgsql security definer
set search_path = pg_catalog, sdk, platform_control
as $function$
begin
  if selected_user is null or selected_session is null or
     selected_digest is null or octet_length(selected_digest) <> 32 or
     selected_target is null or
     selected_target not in ('home','requirements','tasks','tests','releases') then
    return false;
  end if;
  delete from sdk.sso_tickets where expires_at < clock_timestamp() - interval '1 hour';
  insert into sdk.sso_tickets(token_digest,internal_user_id,session_id,expires_at,target)
  select selected_digest, selected_user, selected_session,
         clock_timestamp() + interval '45 seconds', selected_target
  from platform_control.web_sessions session
  join sdk.sso_bindings binding on binding.internal_user_id=session.internal_user_id
  where session.session_id=selected_session
    and session.internal_user_id=selected_user
    and session.revoked_at is null
    and session.idle_expires_at > clock_timestamp()
    and session.absolute_expires_at > clock_timestamp()
    and not session.hard_stale_read_only
    and sdk.sso_eligible(selected_user)
  on conflict do nothing;
  return found;
end
$function$;

revoke all on function sdk.issue_sso_ticket(uuid,uuid,bytea) from public;
-- The legacy three-argument issuer cannot bypass the new admission policy.
create or replace function sdk.issue_sso_ticket(
  selected_user uuid, selected_session uuid, selected_digest bytea
) returns boolean
language sql security definer
set search_path = pg_catalog, sdk
as $function$
  select sdk.issue_sso_ticket(selected_user,selected_session,selected_digest,'home');
$function$;

create or replace function sdk.consume_sso_ticket(selected_digest bytea) returns text
language sql security definer
set search_path = pg_catalog, sdk, platform_control
as $function$
  update sdk.sso_tickets ticket
  set consumed_at=clock_timestamp()
  from sdk.sso_bindings binding,
       platform_control.web_sessions session
  where ticket.token_digest=selected_digest
    and ticket.consumed_at is null
    and ticket.expires_at > clock_timestamp()
    and binding.internal_user_id=ticket.internal_user_id
    and session.session_id=ticket.session_id
    and session.internal_user_id=ticket.internal_user_id
    and session.revoked_at is null
    and session.idle_expires_at > clock_timestamp()
    and session.absolute_expires_at > clock_timestamp()
    and not session.hard_stale_read_only
    and sdk.sso_eligible(ticket.internal_user_id)
  returning binding.account;
$function$;

revoke all on sdk.allowed_departments, sdk.department_policy_events, sdk.provision_intents from public;
revoke all on function sdk.sso_department_eligible(uuid) from public;
revoke all on function sdk.sso_eligible(uuid) from public;
revoke all on function sdk.sso_identity(uuid) from public;
revoke all on function sdk.set_allowed_department(uuid,uuid,boolean) from public;
revoke all on function sdk.sso_department_options() from public;
revoke all on function sdk.sso_access_changes(integer) from public;

do $migration$
declare selected_app name;
begin
  if current_database() = 'agent_platform_control' and current_user = 'platform_control_owner' then
    selected_app := 'platform_control_app';
  elsif current_database() = 'agent_platform_control_preview' and current_user = 'platform_control_owner_preview' then
    selected_app := 'platform_control_app_preview';
  else
    raise insufficient_privilege using message = 'SDK auto membership migration owner/environment mismatch';
  end if;
  execute format('grant execute on function sdk.sso_identity(uuid) to %I', selected_app);
  execute format('grant execute on function sdk.sso_eligible(uuid) to %I', selected_app);
  execute format('grant execute on function sdk.set_allowed_department(uuid,uuid,boolean) to %I', selected_app);
  execute format('grant execute on function sdk.sso_department_options() to %I', selected_app);
  execute format('grant execute on function sdk.sso_access_changes(integer) to %I', selected_app);
  execute format('grant select,insert,update on sdk.provision_intents to %I', selected_app);
  execute format('grant select on sdk.allowed_departments to %I', selected_app);
end
$migration$;
