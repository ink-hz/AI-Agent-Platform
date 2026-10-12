-- Add DAQ enterprise launch while preserving the existing camera and partner contracts.
-- Widen the two launch table Agent IDs and scoped functions; historical migrations remain immutable.

alter table platform_control.agent_launch_codes
  drop constraint agent_launch_codes_agent_id_check,
  add constraint agent_launch_codes_agent_id_check
    check (agent_id in ('ai-fae-agent','ai-daq-fae-agent'));

alter table platform_control.agent_identity_bindings
  drop constraint agent_identity_bindings_agent_id_check,
  add constraint agent_identity_bindings_agent_id_check
    check (agent_id in ('ai-fae-agent','ai-daq-fae-agent'));

create or replace function platform_control.has_agent_use_scope_v29(
  selected_user_id uuid,
  selected_agent_id text
) returns boolean
language sql
stable
security definer
set search_path = pg_catalog, platform_control
as $function$
  with active_member as (
    select state.active_generation_id, member.member_key, users.role
    from platform_control.internal_users users
    join platform_control.directory_state state on state.singleton
    join platform_control.directory_generations generation
      on generation.generation_id = state.active_generation_id
     and generation.status = 'complete'
    join platform_control.directory_members member
      on member.generation_id = state.active_generation_id
     and member.internal_user_id = users.internal_user_id
     and member.status = 'active'
    where users.internal_user_id = selected_user_id
      and users.status = 'active'
      and users.locally_invalidated_at is null
      and selected_agent_id in (
        'hr-bot',
        'voc',
        'marketing-prospecting-bot',
        'marketing-inbound-bot',
        'marketing-voice-bot',
        'marketing-intelligence-bot',
        'marketing-gtm-bot',
        'ai-admin-agent',
        'ai-fae-agent',
        'ai-daq-fae-agent'
      )
  )
  select exists (
    select 1 from active_member member
    where (
      selected_agent_id = 'hr-bot'
      and member.role in ('platform_owner', 'platform_admin')
    ) or exists (
      select 1 from platform_control.agent_use_grants grant_row
      where grant_row.agent_id = selected_agent_id
        and grant_row.revoked_at is null
        and (
          grant_row.target_kind = 'all_members'
          or (
            grant_row.target_kind = 'user'
            and grant_row.target_internal_user_id = selected_user_id
          )
          or (
            grant_row.target_kind = 'department'
            and grant_row.include_descendants
            and exists (
              select 1
              from platform_control.member_departments membership
              join platform_control.department_closure closure
                on closure.generation_id = membership.generation_id
               and closure.descendant_department_key = membership.department_key
               and closure.ancestor_department_key = grant_row.target_department_key
              where membership.generation_id = member.active_generation_id
                and membership.member_key = member.member_key
            )
          )
        )
    )
  )
$function$;

comment on function platform_control.has_agent_use_scope_v29(uuid, text) is
  'Active platform owners and admins may use HR by default; other Agent grants, including DAQ, retain their explicit scope.';

create or replace function platform_control.issue_agent_launch_v57(
  selected_code_id uuid,
  selected_code_hash bytea,
  selected_code_key_version integer,
  selected_subject_id uuid,
  selected_subject_type platform_control.agent_subject_type,
  selected_source_session_id uuid,
  selected_internal_user_id uuid,
  selected_agent_id text,
  selected_binding_id uuid,
  selected_ttl_seconds integer
) returns timestamptz
language plpgsql
security definer
set search_path=pg_catalog,platform_control
as $function$
declare
  database_now timestamptz := clock_timestamp();
  selected_expires_at timestamptz := database_now + interval '60 seconds';
  selected_allowed boolean := false;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or selected_code_id is null
     or selected_code_hash is null
     or octet_length(selected_code_hash)<>32
     or selected_code_key_version is null
     or selected_code_key_version<=0
     or selected_subject_id is null
     or selected_subject_type is null
     or selected_agent_id not in ('ai-fae-agent','ai-daq-fae-agent')
     or (selected_agent_id='ai-daq-fae-agent'
       and selected_subject_type<>'enterprise_member')
     or selected_binding_id is null
     or selected_ttl_seconds<>60
     or not (
       (selected_subject_type='enterprise_member'
         and selected_source_session_id is not null
         and selected_internal_user_id is not null
         and selected_subject_id=selected_internal_user_id)
       or
       (selected_subject_type='partner_operator'
         and selected_source_session_id is null
         and selected_internal_user_id is null)
     )
  then
    raise check_violation using message='Agent launch input invalid';
  end if;

  if selected_subject_type='enterprise_member' then
    select exists (
      select 1
      from platform_control.web_sessions session
      join platform_control.internal_users users
        on users.internal_user_id=session.internal_user_id
      where session.session_id=selected_source_session_id
        and session.internal_user_id=selected_internal_user_id
        and session.revoked_at is null
        and session.idle_expires_at>database_now
        and session.absolute_expires_at>database_now
        and session.hard_stale_read_only=false
        and users.status='active'
        and users.locally_invalidated_at is null
        and platform_control.has_agent_use_scope_v29(
          selected_internal_user_id,selected_agent_id
        )
    ) into selected_allowed;

    if selected_allowed is true then
      insert into platform_control.agent_access_subjects(
        subject_id,subject_type,status,created_at,updated_at,invalidated_at
      )
      select users.internal_user_id,'enterprise_member',
        case users.status
          when 'active' then 'active'
          when 'inactive' then 'suspended'
          else 'disabled'
        end,
        users.created_at,users.updated_at,users.locally_invalidated_at
      from platform_control.internal_users users
      where users.internal_user_id=selected_internal_user_id
      on conflict (subject_id) do nothing;

      insert into platform_control.enterprise_subject_links(
        subject_id,internal_user_id
      )
      select subject.subject_id,selected_internal_user_id
      from platform_control.agent_access_subjects subject
      where subject.subject_id=selected_subject_id
        and subject.subject_type='enterprise_member'
      on conflict (subject_id) do nothing;

      select exists (
        select 1
        from platform_control.web_sessions session
        join platform_control.internal_users users
          on users.internal_user_id=session.internal_user_id
        join platform_control.enterprise_subject_links link
          on link.internal_user_id=users.internal_user_id
        join platform_control.agent_access_subjects subject
          on subject.subject_id=link.subject_id
        where session.session_id=selected_source_session_id
          and session.internal_user_id=selected_internal_user_id
          and link.subject_id=selected_subject_id
          and subject.subject_type='enterprise_member'
          and subject.status='active'
          and subject.invalidated_at is null
          and session.revoked_at is null
          and session.idle_expires_at>database_now
          and session.absolute_expires_at>database_now
          and session.hard_stale_read_only=false
          and users.status='active'
          and users.locally_invalidated_at is null
          and platform_control.has_agent_use_scope_v29(
            selected_internal_user_id,selected_agent_id
          )
      ) into selected_allowed;
    end if;
  else
    select exists (
      select 1
      from platform_control.agent_access_subjects subject
      join platform_control.partner_operators operator
        on operator.subject_id=subject.subject_id
      join platform_control.partner_organizations organization
        on organization.partner_organization_id=operator.partner_organization_id
      where subject.subject_id=selected_subject_id
        and subject.subject_type='partner_operator'
        and subject.status='active'
        and subject.invalidated_at is null
        and operator.status='active'
        and operator.invalidated_at is null
        and organization.status='active'
        and organization.invalidated_at is null
        and exists (
          select 1
          from platform_control.partner_agent_grants grant_row
          where grant_row.subject_id=subject.subject_id
            and grant_row.agent_id=selected_agent_id
            and grant_row.revoked_at is null
        )
        and exists (
          select 1
          from platform_control.partner_provider_identities identity
          where identity.partner_operator_id=operator.partner_operator_id
            and identity.revoked_at is null
        )
    ) into selected_allowed;
  end if;

  if selected_allowed is distinct from true then
    raise insufficient_privilege using message='Agent launch denied';
  end if;

  insert into platform_control.agent_launch_codes(
    launch_code_id,code_hash,code_key_version,subject_id,subject_type,
    source_session_id,internal_user_id,agent_id,identity_binding_id,expires_at
  ) values (
    selected_code_id,selected_code_hash,selected_code_key_version,
    selected_subject_id,selected_subject_type,selected_source_session_id,
    selected_internal_user_id,selected_agent_id,selected_binding_id,
    selected_expires_at
  );
  return selected_expires_at;
end
$function$;


create or replace function platform_control.validate_agent_identity_binding_v57(
  selected_binding_id uuid,
  selected_agent_id text
) returns table(
  subject_id uuid,
  subject_type platform_control.agent_subject_type,
  identity_binding_id uuid,
  agent_id text,
  internal_user_id uuid,
  display_name text,
  active boolean
)
language plpgsql
security definer
set search_path=pg_catalog,platform_control
as $function$
declare
  database_now timestamptz := clock_timestamp();
  selected platform_control.agent_identity_bindings%rowtype;
  selected_active boolean := false;
  selected_display_name text;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or selected_binding_id is null
     or selected_agent_id not in ('ai-fae-agent','ai-daq-fae-agent')
  then
    return;
  end if;

  select binding.* into selected
  from platform_control.agent_identity_bindings binding
  where binding.identity_binding_id=selected_binding_id
    and binding.agent_id=selected_agent_id
  for update;
  if not found then
    return;
  end if;

  if selected.revoked_at is null and selected.subject_type='enterprise_member' then
    select users.display_name, true
    into selected_display_name, selected_active
    from platform_control.web_sessions session
    join platform_control.internal_users users
      on users.internal_user_id=session.internal_user_id
    join platform_control.enterprise_subject_links link
      on link.internal_user_id=users.internal_user_id
    join platform_control.agent_access_subjects subject
      on subject.subject_id=link.subject_id
    where session.session_id=selected.source_session_id
      and session.internal_user_id=selected.internal_user_id
      and link.subject_id=selected.subject_id
      and subject.subject_type='enterprise_member'
      and subject.status='active'
      and subject.invalidated_at is null
      and session.revoked_at is null
      and session.idle_expires_at>database_now
      and session.absolute_expires_at>database_now
      and session.hard_stale_read_only=false
      and users.status='active'
      and users.locally_invalidated_at is null
      and platform_control.has_agent_use_scope_v29(
        selected.internal_user_id,selected.agent_id
      );
  elsif selected.revoked_at is null
    and selected.subject_type='partner_operator'
    and selected_agent_id='ai-fae-agent' then
    select exists (
      select 1
      from platform_control.agent_access_subjects subject
      join platform_control.partner_operators operator
        on operator.subject_id=subject.subject_id
      join platform_control.partner_organizations organization
        on organization.partner_organization_id=operator.partner_organization_id
      where subject.subject_id=selected.subject_id
        and subject.subject_type='partner_operator'
        and subject.status='active'
        and subject.invalidated_at is null
        and operator.status='active'
        and operator.invalidated_at is null
        and organization.status='active'
        and organization.invalidated_at is null
        and exists (
          select 1
          from platform_control.partner_agent_grants grant_row
          where grant_row.subject_id=subject.subject_id
            and grant_row.agent_id=selected.agent_id
            and grant_row.revoked_at is null
        )
        and exists (
          select 1
          from platform_control.partner_provider_identities identity
          where identity.partner_operator_id=operator.partner_operator_id
            and identity.revoked_at is null
        )
    ) into selected_active;
  end if;

  selected_active := coalesce(selected_active,false);
  if selected_active is true then
    update platform_control.agent_identity_bindings binding
      set last_validated_at=database_now
      where binding.identity_binding_id=selected.identity_binding_id;
  else
    update platform_control.agent_identity_bindings binding
      set revoked_at=coalesce(binding.revoked_at,database_now)
      where binding.identity_binding_id=selected.identity_binding_id;
  end if;

  return query select
    selected.subject_id,selected.subject_type,selected.identity_binding_id,
    selected.agent_id,selected.internal_user_id,selected_display_name,
    selected_active;
end
$function$;
