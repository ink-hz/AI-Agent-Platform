create function platform_control.lock_dingtalk_identity_directory()
returns void
language sql
security definer
set search_path = pg_catalog, platform_control
as $function$
  select pg_advisory_xact_lock(1229998928);
$function$;

alter function platform_control.resolve_verified_dingtalk_member(
  uuid,text,uuid,bytea,integer,bytea,integer,integer[],bytea[],
  uuid,bytea,integer,bytea,integer,integer[],bytea[]
) rename to resolve_verified_dingtalk_member_v12;

create function platform_control.resolve_verified_dingtalk_member(
  selected_user_id uuid,
  selected_display_name text,
  corporate_identity_id uuid,
  corporate_lookup_hmac bytea,
  corporate_lookup_version integer,
  corporate_ciphertext bytea,
  corporate_encryption_version integer,
  corporate_candidate_versions integer[],
  corporate_candidate_hmacs bytea[],
  union_identity_id uuid,
  union_lookup_hmac bytea,
  union_lookup_version integer,
  union_ciphertext bytea,
  union_encryption_version integer,
  union_candidate_versions integer[],
  union_candidate_hmacs bytea[]
) returns uuid
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  selected_generation_id uuid;
  selected_member_key uuid;
  selected_directory_user_id uuid;
  corporate_user_ids uuid[];
  union_user_ids uuid[];
  mapped_user_id uuid;
begin
  perform platform_control.lock_dingtalk_identity_directory();

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
    and member.union_lookup_key_version = $12
    and member.union_lookup_hmac = $11
  for update of member;

  select array_agg(distinct identity.internal_user_id)
  into corporate_user_ids
  from platform_control.provider_identities identity
  join unnest(corporate_candidate_versions, corporate_candidate_hmacs)
    candidate(version, lookup_hmac)
    on identity.lookup_key_version = candidate.version
   and identity.lookup_hmac = candidate.lookup_hmac
  where identity.subject_kind = 'employee';

  select array_agg(distinct identity.internal_user_id)
  into union_user_ids
  from platform_control.provider_identities identity
  join unnest(union_candidate_versions, union_candidate_hmacs)
    candidate(version, lookup_hmac)
    on identity.lookup_key_version = candidate.version
   and identity.lookup_hmac = candidate.lookup_hmac
  where identity.subject_kind = 'employee_union';

  if corporate_user_ids is null and union_user_ids is null then
    if selected_directory_user_id is not null then
      raise unique_violation using message = 'verified identity collision';
    end if;
  elsif corporate_user_ids is not null
        and union_user_ids is not null
        and cardinality(corporate_user_ids) = 1
        and cardinality(union_user_ids) = 1
        and corporate_user_ids[1] = union_user_ids[1]
  then
    mapped_user_id := corporate_user_ids[1];
    if not exists (
      select 1 from platform_control.internal_users users
      where users.internal_user_id = mapped_user_id
        and users.status = 'active'
        and users.locally_invalidated_at is null
    ) then
      raise check_violation using message = 'active internal member required';
    end if;
    if selected_directory_user_id is null then
      update platform_control.directory_members
      set internal_user_id = mapped_user_id
      where generation_id = selected_generation_id
        and member_key = selected_member_key;
    elsif selected_directory_user_id <> mapped_user_id then
      raise unique_violation using message = 'verified identity collision';
    end if;
  else
    raise unique_violation using message = 'verified identity collision';
  end if;

  return platform_control.resolve_verified_dingtalk_member_v12(
    selected_user_id,
    selected_display_name,
    corporate_identity_id,
    corporate_lookup_hmac,
    corporate_lookup_version,
    corporate_ciphertext,
    corporate_encryption_version,
    corporate_candidate_versions,
    corporate_candidate_hmacs,
    union_identity_id,
    union_lookup_hmac,
    union_lookup_version,
    union_ciphertext,
    union_encryption_version,
    union_candidate_versions,
    union_candidate_hmacs
  );
exception
  when no_data_found or too_many_rows then
    raise check_violation using message = 'active directory member unavailable';
end
$function$;

create function platform_control.promote_verified_directory_generation(
  selected_generation_id uuid
) returns uuid
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  previous_generation_id uuid;
  selected_status text;
  selected_member_count integer;
  selected_department_count integer;
  selected_content_sha256 text;
  actual_member_count bigint;
  actual_department_count bigint;
begin
  if selected_generation_id is null then
    raise check_violation using message = 'directory generation invalid';
  end if;
  perform platform_control.lock_dingtalk_identity_directory();

  select active_generation_id into previous_generation_id
  from platform_control.directory_state
  where singleton
  for update;

  select status, member_count, department_count, content_sha256
  into strict selected_status, selected_member_count,
    selected_department_count, selected_content_sha256
  from platform_control.directory_generations
  where generation_id = selected_generation_id
  for update;

  select count(*) into actual_member_count
  from platform_control.directory_members
  where generation_id = selected_generation_id;
  select count(*) into actual_department_count
  from platform_control.directory_departments
  where generation_id = selected_generation_id;

  if selected_status <> 'staging'
     or selected_content_sha256 is null
     or length(selected_content_sha256) <> 64
     or selected_member_count <> actual_member_count
     or selected_department_count <> actual_department_count
     or exists (
       select 1 from platform_control.directory_members member
       where member.generation_id = selected_generation_id
         and (
           member.subject_kind <> 'employee'
           or member.union_lookup_hmac is null
           or octet_length(member.union_lookup_hmac) <> 32
           or member.union_lookup_key_version is null
           or member.union_lookup_key_version <> member.lookup_key_version
         )
     )
  then
    raise check_violation using message = 'directory generation incomplete';
  end if;
  if previous_generation_id is not null and not exists (
    select 1 from platform_control.directory_generations generation
    where generation.generation_id = previous_generation_id
      and generation.status = 'complete'
  ) then
    raise check_violation using message = 'active directory generation invalid';
  end if;

  if previous_generation_id is not null then
    update platform_control.directory_generations
    set status = 'superseded'
    where generation_id = previous_generation_id;
  end if;
  update platform_control.directory_generations
  set status = 'complete', completed_at = now()
  where generation_id = selected_generation_id;
  update platform_control.directory_state
  set active_generation_id = selected_generation_id,
      last_complete_at = now(),
      updated_at = now()
  where singleton;
  return selected_generation_id;
exception
  when no_data_found or too_many_rows then
    raise check_violation using message = 'directory generation unavailable';
end
$function$;

revoke all on function platform_control.lock_dingtalk_identity_directory()
  from public;
revoke all on function platform_control.resolve_verified_dingtalk_member_v12(
  uuid,text,uuid,bytea,integer,bytea,integer,integer[],bytea[],
  uuid,bytea,integer,bytea,integer,integer[],bytea[]
) from public;
revoke all on function platform_control.resolve_verified_dingtalk_member(
  uuid,text,uuid,bytea,integer,bytea,integer,integer[],bytea[],
  uuid,bytea,integer,bytea,integer,integer[],bytea[]
) from public;
revoke all on function platform_control.promote_verified_directory_generation(uuid)
  from public;

do $migration$
declare
  selected_app name;
  selected_directory name;
  role_name name;
begin
  if current_database() = 'agent_platform_control'
     and current_user = 'platform_control_owner'
  then
    selected_app := 'platform_control_app';
    selected_directory := 'platform_directory_worker';
  elsif current_database() = 'agent_platform_control_preview'
        and current_user = 'platform_control_owner_preview'
  then
    selected_app := 'platform_control_app_preview';
    selected_directory := 'platform_directory_worker_preview';
  else
    raise insufficient_privilege using
      message = 'directory promotion migration owner/environment mismatch';
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
      'revoke all on function '
      'platform_control.lock_dingtalk_identity_directory() from %I', role_name
    );
    execute format(
      'revoke all on function platform_control.resolve_verified_dingtalk_member_v12('
      'uuid,text,uuid,bytea,integer,bytea,integer,integer[],bytea[],'
      'uuid,bytea,integer,bytea,integer,integer[],bytea[]) from %I', role_name
    );
    execute format(
      'revoke all on function platform_control.resolve_verified_dingtalk_member('
      'uuid,text,uuid,bytea,integer,bytea,integer,integer[],bytea[],'
      'uuid,bytea,integer,bytea,integer,integer[],bytea[]) from %I', role_name
    );
    execute format(
      'revoke all on function '
      'platform_control.promote_verified_directory_generation(uuid) from %I',
      role_name
    );
  end loop;

  execute format(
    'revoke insert, update, delete on '
    'platform_control.directory_state from %I', selected_directory
  );
  execute format(
    'revoke update on platform_control.directory_generations from %I',
    selected_directory
  );
  execute format(
    'grant execute on function '
    'platform_control.lock_dingtalk_identity_directory() to %I, %I',
    selected_app, selected_directory
  );
  execute format(
    'grant execute on function platform_control.resolve_verified_dingtalk_member('
    'uuid,text,uuid,bytea,integer,bytea,integer,integer[],bytea[],'
    'uuid,bytea,integer,bytea,integer,integer[],bytea[]) to %I', selected_app
  );
  execute format(
    'grant execute on function '
    'platform_control.promote_verified_directory_generation(uuid) to %I',
    selected_directory
  );
end
$migration$;
