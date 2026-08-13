alter function platform_control.resolve_verified_dingtalk_member(
  uuid,text,uuid,bytea,integer,bytea,integer,integer[],bytea[],
  uuid,bytea,integer,bytea,integer,integer[],bytea[]
) rename to resolve_verified_dingtalk_member_v13;

create function platform_control.resolve_verified_dingtalk_member(
  selected_user_id uuid,
  selected_display_name text,
  corporate_identity_id uuid,
  corporate_lookup_hmac bytea,
  corporate_lookup_version integer,
  corporate_ciphertext bytea,
  corporate_encryption_version integer,
  union_identity_id uuid,
  union_lookup_hmac bytea,
  union_lookup_version integer,
  union_ciphertext bytea,
  union_encryption_version integer
) returns uuid
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  selected_generation_id uuid;
  selected_member_key uuid;
  selected_directory_user_id uuid;
  resolved_user_id uuid;
  corporate_user_ids uuid[];
  union_user_ids uuid[];
  configured_versions integer[];
begin
  if selected_user_id is null
     or selected_display_name is null
     or selected_display_name <> btrim(selected_display_name)
     or length(selected_display_name) not between 1 and 256
     or selected_display_name ~ '[[:cntrl:]]'
     or corporate_identity_id is null
     or union_identity_id is null
     or corporate_identity_id = union_identity_id
     or corporate_lookup_version is null or corporate_lookup_version <= 0
     or union_lookup_version is null
     or union_lookup_version <> corporate_lookup_version
     or corporate_encryption_version is null or corporate_encryption_version <= 0
     or union_encryption_version is null or union_encryption_version <= 0
     or octet_length(corporate_lookup_hmac) <> 32
     or octet_length(union_lookup_hmac) <> 32
     or octet_length(corporate_ciphertext) < 1
     or octet_length(union_ciphertext) < 1
  then
    raise check_violation using message = 'verified identity input invalid';
  end if;

  perform platform_control.lock_dingtalk_identity_directory();
  select lookup_transition_versions into strict configured_versions
  from platform_control.provider_identity_key_policies
  where provider = 'dingtalk'
  for share;
  if not corporate_lookup_version = any(configured_versions) then
    raise check_violation using message = 'identity key policy mismatch';
  end if;

  -- Online login never follows a transition candidate. A dedicated maintenance
  -- migration must rotate every provider mapping before a new HMAC version can
  -- authorize login; mixed mapping versions fail the whole boundary closed.
  if exists (
    select 1 from platform_control.provider_identities identity
    where identity.subject_kind in ('employee', 'employee_union')
      and identity.lookup_key_version <> corporate_lookup_version
  ) then
    raise unique_violation using message = 'verified identity collision';
  end if;

  select generation.generation_id, member.member_key, member.internal_user_id
  into strict selected_generation_id, selected_member_key,
    selected_directory_user_id
  from platform_control.directory_state state
  join platform_control.directory_generations generation
    on generation.generation_id = state.active_generation_id
   and generation.status = 'complete'
  join platform_control.directory_members member
    on member.generation_id = generation.generation_id
  where state.singleton
    and member.subject_kind = 'employee'
    and member.status = 'active'
    and member.lookup_key_version = $5
    and member.lookup_hmac = $4
    and member.union_lookup_key_version = $10
    and member.union_lookup_hmac = $9
  for update of member;

  select array_agg(distinct identity.internal_user_id)
  into corporate_user_ids
  from platform_control.provider_identities identity
  where identity.subject_kind = 'employee'
    and identity.lookup_key_version = corporate_lookup_version
    and identity.lookup_hmac = corporate_lookup_hmac;

  select array_agg(distinct identity.internal_user_id)
  into union_user_ids
  from platform_control.provider_identities identity
  where identity.subject_kind = 'employee_union'
    and identity.lookup_key_version = union_lookup_version
    and identity.lookup_hmac = union_lookup_hmac;

  if corporate_user_ids is null and union_user_ids is null then
    if selected_directory_user_id is not null then
      raise unique_violation using message = 'verified identity collision';
    end if;
    resolved_user_id := selected_user_id;
    insert into platform_control.internal_users (
      internal_user_id, role, display_name, status,
      last_confirmed_generation_id
    ) values (
      resolved_user_id, 'member', selected_display_name, 'active',
      selected_generation_id
    );
    insert into platform_control.provider_identities (
      provider_identity_id, internal_user_id, subject_kind, lookup_hmac,
      lookup_key_version, encrypted_provider_id, encryption_key_version
    ) values
      (
        corporate_identity_id, resolved_user_id, 'employee',
        corporate_lookup_hmac, corporate_lookup_version,
        corporate_ciphertext, corporate_encryption_version
      ),
      (
        union_identity_id, resolved_user_id, 'employee_union',
        union_lookup_hmac, union_lookup_version,
        union_ciphertext, union_encryption_version
      );
  elsif corporate_user_ids is not null
        and union_user_ids is not null
        and cardinality(corporate_user_ids) = 1
        and cardinality(union_user_ids) = 1
        and corporate_user_ids[1] = union_user_ids[1]
  then
    resolved_user_id := corporate_user_ids[1];
    if not exists (
      select 1 from platform_control.internal_users users
      where users.internal_user_id = resolved_user_id
        and users.status = 'active'
        and users.locally_invalidated_at is null
    ) then
      raise check_violation using message = 'active internal member required';
    end if;
    if selected_directory_user_id is not null
       and selected_directory_user_id <> resolved_user_id
    then
      raise unique_violation using message = 'verified identity collision';
    end if;
  else
    raise unique_violation using message = 'verified identity collision';
  end if;

  update platform_control.directory_members
  set internal_user_id = resolved_user_id
  where generation_id = selected_generation_id
    and member_key = selected_member_key;
  update platform_control.internal_users
  set display_name = selected_display_name,
      last_confirmed_generation_id = selected_generation_id,
      updated_at = now()
  where internal_user_id = resolved_user_id;
  return resolved_user_id;
exception
  when no_data_found or too_many_rows then
    raise check_violation using message = 'active directory member unavailable';
end
$function$;

revoke all on function platform_control.resolve_verified_dingtalk_member_v13(
  uuid,text,uuid,bytea,integer,bytea,integer,integer[],bytea[],
  uuid,bytea,integer,bytea,integer,integer[],bytea[]
) from public;
revoke all on function platform_control.resolve_verified_dingtalk_member(
  uuid,text,uuid,bytea,integer,bytea,integer,
  uuid,bytea,integer,bytea,integer
) from public;

do $migration$
declare
  selected_app name;
  role_name name;
begin
  if current_database() = 'agent_platform_control'
     and current_user = 'platform_control_owner'
  then
    selected_app := 'platform_control_app';
  elsif current_database() = 'agent_platform_control_preview'
        and current_user = 'platform_control_owner_preview'
  then
    selected_app := 'platform_control_app_preview';
  else
    raise insufficient_privilege using
      message = 'exact identity mapping migration owner/environment mismatch';
  end if;

  foreach role_name in array array[
    'platform_control_migrator', 'platform_control_app',
    'platform_directory_worker', 'platform_stream_ingest',
    'platform_audit_append', 'platform_control_maintenance',
    'platform_control_migrator_preview', 'platform_control_app_preview',
    'platform_directory_worker_preview', 'platform_stream_ingest_preview',
    'platform_audit_append_preview', 'platform_control_maintenance_preview'
  ] loop
    execute format(
      'revoke all on function platform_control.resolve_verified_dingtalk_member_v13('
      'uuid,text,uuid,bytea,integer,bytea,integer,integer[],bytea[],'
      'uuid,bytea,integer,bytea,integer,integer[],bytea[]) from %I', role_name
    );
    execute format(
      'revoke all on function platform_control.resolve_verified_dingtalk_member('
      'uuid,text,uuid,bytea,integer,bytea,integer,'
      'uuid,bytea,integer,bytea,integer) from %I', role_name
    );
  end loop;

  execute format(
    'grant execute on function platform_control.resolve_verified_dingtalk_member('
    'uuid,text,uuid,bytea,integer,bytea,integer,'
    'uuid,bytea,integer,bytea,integer) to %I', selected_app
  );
end
$migration$;
