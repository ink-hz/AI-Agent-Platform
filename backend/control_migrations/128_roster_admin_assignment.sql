-- An owner may select an unlinked employee from the published directory.
-- Bind the audit target to the selected generation: a corporate ID may be
-- reassigned to a different Union identity between publications.
create function platform_control.assign_roster_platform_admin_v128(
  selected_operation_id uuid,
  selected_actor_id uuid,
  selected_member_key uuid,
  selected_audit_event_id uuid,
  selected_generation_id uuid
) returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  target_id uuid;
  resolved_id uuid;
  selected_member platform_control.directory_members%rowtype;
  replay jsonb;
begin
  if selected_operation_id is null or selected_actor_id is null
     or selected_member_key is null or selected_audit_event_id is null
     or selected_generation_id is null then
    raise check_violation using message = 'admin assignment precondition invalid';
  end if;
  target_id := left(encode(sha256(convert_to(
    'orbbec:roster-admin:v1:' || selected_generation_id::text || ':' || selected_member_key::text, 'UTF8'
  )), 'hex'), 32)::uuid;

  perform platform_control.lock_dingtalk_identity_directory();
  perform pg_advisory_xact_lock(hashtextextended(selected_operation_id::text, 0));
  perform platform_control.require_platform_owner(selected_actor_id);
  replay := platform_control.replay_management_mutation(
    selected_operation_id, 'assign_admin', selected_actor_id, target_id,
    null, null, 0, 0, null, selected_audit_event_id
  );
  if replay is not null then return replay; end if;

  perform platform_control.require_requested_audit(
    selected_audit_event_id, selected_operation_id, selected_actor_id,
    'admin_role_assignment_requested', 'internal_user', target_id::text,
    'admin_access_approved'
  );

  select member.* into selected_member
  from platform_control.directory_state state
  join platform_control.directory_generations generation
    on generation.generation_id = state.active_generation_id and generation.status = 'complete'
  join platform_control.directory_members member on member.generation_id = generation.generation_id
  where state.singleton
    and generation.generation_id = selected_generation_id
    and state.last_complete_at > clock_timestamp() - interval '8 hours'
    and state.last_complete_at <= clock_timestamp()
    and member.member_key = selected_member_key
    and member.subject_kind = 'employee' and member.status = 'active'
    and member.internal_user_id is null
    and member.union_lookup_hmac is not null and member.union_lookup_key_version is not null
    and member.union_encrypted_provider_id is not null and member.union_encryption_key_version is not null
  for update of member;
  if not found then
    raise check_violation using message = 'roster member changed or unavailable';
  end if;

  -- Compare the name and department labels the owner can actually see across
  -- the full set of eligible namesakes, including matches beyond the search page.
  if exists (
    with namesakes as (
      select member.member_key, array(
        select distinct department.display_name
        from platform_control.member_departments membership
        join platform_control.directory_departments department
          using (generation_id, department_key)
        where membership.generation_id = member.generation_id
          and membership.member_key = member.member_key
        order by department.display_name
      ) as department_names
      from platform_control.directory_members member
      left join platform_control.internal_users users on users.internal_user_id = member.internal_user_id
      where member.generation_id = selected_member.generation_id
        and member.status = 'active' and member.display_name = selected_member.display_name
        and (member.internal_user_id is null or (users.status = 'active' and users.role = 'member'))
    )
    select 1 from namesakes selected join namesakes other using (department_names)
    where selected.member_key = selected_member_key and other.member_key <> selected_member_key
  ) then
    raise check_violation using message = 'roster member identity ambiguous';
  end if;

  -- A pre-existing account can only be changed through the linked-account path.
  if exists (select 1 from platform_control.internal_users where internal_user_id = target_id) then
    raise check_violation using message = 'roster member identity collision';
  end if;
  resolved_id := platform_control.resolve_verified_dingtalk_member(
    target_id, selected_member.display_name,
    gen_random_uuid(), selected_member.lookup_hmac, selected_member.lookup_key_version,
    selected_member.encrypted_provider_id, selected_member.encryption_key_version,
    gen_random_uuid(), selected_member.union_lookup_hmac, selected_member.union_lookup_key_version,
    selected_member.union_encrypted_provider_id, selected_member.union_encryption_key_version
  );
  if resolved_id is distinct from target_id then
    raise check_violation using message = 'roster member identity collision';
  end if;
  -- This checks the full intent, enforces the member role/version, updates
  -- the role and records the replay result in the same transaction as provisioning.
  return platform_control.assign_platform_admin(
    selected_operation_id, selected_actor_id, target_id, 0, selected_audit_event_id
  );
end
$function$;

revoke all on function platform_control.assign_roster_platform_admin_v128(uuid,uuid,uuid,uuid,uuid) from public;
do $migration$
declare selected_app name;
begin
  if current_database() = 'agent_platform_control' and current_user = 'platform_control_owner' then
    selected_app := 'platform_control_app';
  elsif current_database() = 'agent_platform_control_preview' and current_user = 'platform_control_owner_preview' then
    selected_app := 'platform_control_app_preview';
  else
    raise insufficient_privilege using message = 'roster grant owner/environment mismatch';
  end if;
  execute format(
    'grant execute on function platform_control.assign_roster_platform_admin_v128(uuid,uuid,uuid,uuid,uuid) to %I',
    selected_app
  );
end
$migration$;

-- An intent may survive a process failure before there is a control mutation.
-- Reserve its request ID as soon as the first administrator intent is recorded.
create function platform_control.guard_admin_request_identity_v128() returns trigger
language plpgsql
set search_path = pg_catalog, platform_control
as $function$
begin
  perform pg_advisory_xact_lock(hashtextextended(new.request_id::text, 128));
  if exists (
    select 1 from platform_control.audit_events prior
    where prior.request_id = new.request_id and prior.result = 'requested'
      and prior.audit_event_id <> new.audit_event_id
      and (prior.event_type in ('admin_role_assignment_requested','admin_role_revocation_requested')
        or new.event_type in ('admin_role_assignment_requested','admin_role_revocation_requested'))
  ) then
    raise check_violation using message = 'operation identity collision',
      constraint = 'admin_request_identity_v128';
  end if;
  return new;
end
$function$;

revoke all on function platform_control.guard_admin_request_identity_v128() from public;
create trigger guard_admin_request_identity_v128 before insert on platform_control.audit_events
  for each row when (new.result = 'requested')
  execute function platform_control.guard_admin_request_identity_v128();
