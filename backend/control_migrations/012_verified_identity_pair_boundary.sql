alter table platform_control.directory_members
  add column union_lookup_hmac bytea,
  add column union_lookup_key_version integer,
  add constraint directory_members_union_pair_complete check (
    (
      union_lookup_hmac is null
      and union_lookup_key_version is null
    )
    or (
      octet_length(union_lookup_hmac) = 32
      and union_lookup_key_version > 0
      and union_lookup_key_version = lookup_key_version
    )
  );

create unique index directory_members_generation_union_lookup
  on platform_control.directory_members (
    generation_id, union_lookup_key_version, union_lookup_hmac
  )
  where union_lookup_hmac is not null;

create function platform_control.stage_verified_directory_member(
  selected_generation_id uuid,
  selected_member_key uuid,
  selected_subject_kind text,
  selected_lookup_hmac bytea,
  selected_lookup_version integer,
  selected_ciphertext bytea,
  selected_encryption_version integer,
  selected_union_lookup_hmac bytea,
  selected_union_lookup_version integer,
  selected_display_name text,
  selected_status text
) returns uuid
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  configured_versions integer[];
begin
  if selected_generation_id is null
     or selected_member_key is null
     or selected_subject_kind <> 'employee'
     or selected_lookup_version is null or selected_lookup_version <= 0
     or selected_union_lookup_version is null
     or selected_union_lookup_version <> selected_lookup_version
     or selected_encryption_version is null or selected_encryption_version <= 0
     or octet_length(selected_lookup_hmac) <> 32
     or octet_length(selected_union_lookup_hmac) <> 32
     or octet_length(selected_ciphertext) < 1
     or selected_display_name is null
     or selected_display_name <> btrim(selected_display_name)
     or length(selected_display_name) not between 1 and 256
     or selected_display_name ~ '[[:cntrl:]]'
     or selected_status not in ('active', 'inactive', 'disabled')
  then
    raise check_violation using message = 'verified directory member invalid';
  end if;

  select lookup_transition_versions into strict configured_versions
  from platform_control.provider_identity_key_policies
  where provider = 'dingtalk'
  for share;
  if not selected_lookup_version = any(configured_versions) then
    raise check_violation using message = 'identity key policy mismatch';
  end if;
  if not exists (
    select 1 from platform_control.directory_generations generation
    where generation.generation_id = selected_generation_id
      and generation.status = 'staging'
    for update
  ) then
    raise check_violation using message = 'staging directory generation required';
  end if;

  insert into platform_control.directory_members (
    generation_id, member_key, internal_user_id, subject_kind,
    lookup_hmac, lookup_key_version, encrypted_provider_id,
    encryption_key_version, union_lookup_hmac, union_lookup_key_version,
    display_name, status
  ) values (
    selected_generation_id, selected_member_key, null, selected_subject_kind,
    selected_lookup_hmac, selected_lookup_version, selected_ciphertext,
    selected_encryption_version, selected_union_lookup_hmac,
    selected_union_lookup_version, selected_display_name, selected_status
  );
  return selected_member_key;
exception
  when no_data_found then
    raise check_violation using message = 'identity key policy unavailable';
end
$function$;

create or replace function platform_control.resolve_verified_dingtalk_member(
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
  resolved_user_id uuid;
  corporate_user_ids uuid[];
  union_user_ids uuid[];
  configured_versions integer[];
  locked_identity record;
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
     or corporate_candidate_versions is null
     or corporate_candidate_hmacs is null
     or union_candidate_versions is null
     or union_candidate_hmacs is null
     or array_ndims(corporate_candidate_versions) <> 1
     or array_ndims(corporate_candidate_hmacs) <> 1
     or array_ndims(union_candidate_versions) <> 1
     or array_ndims(union_candidate_hmacs) <> 1
     or array_lower(corporate_candidate_versions, 1) <> 1
     or array_lower(corporate_candidate_hmacs, 1) <> 1
     or array_lower(union_candidate_versions, 1) <> 1
     or array_lower(union_candidate_hmacs, 1) <> 1
     or cardinality(corporate_candidate_versions) not between 1 and 3
     or cardinality(union_candidate_versions) not between 1 and 3
     or cardinality(corporate_candidate_versions)
        <> cardinality(corporate_candidate_hmacs)
     or cardinality(union_candidate_versions)
        <> cardinality(union_candidate_hmacs)
     or array_position(corporate_candidate_versions, null) is not null
     or array_position(corporate_candidate_hmacs, null) is not null
     or array_position(union_candidate_versions, null) is not null
     or array_position(union_candidate_hmacs, null) is not null
     or not coalesce(0 < all(corporate_candidate_versions), false)
     or not coalesce(0 < all(union_candidate_versions), false)
     or exists (
       select 1 from unnest(corporate_candidate_hmacs) value
       where octet_length(value) <> 32
     )
     or exists (
       select 1 from unnest(union_candidate_hmacs) value
       where octet_length(value) <> 32
     )
     or not exists (
       select 1 from unnest(
         corporate_candidate_versions, corporate_candidate_hmacs
       ) candidate(version, lookup_hmac)
       where candidate.version = corporate_lookup_version
         and candidate.lookup_hmac = corporate_lookup_hmac
     )
     or not exists (
       select 1 from unnest(
         union_candidate_versions, union_candidate_hmacs
       ) candidate(version, lookup_hmac)
       where candidate.version = union_lookup_version
         and candidate.lookup_hmac = union_lookup_hmac
     )
  then
    raise check_violation using message = 'verified identity input invalid';
  end if;

  perform pg_advisory_xact_lock(1229998928);
  select lookup_transition_versions into strict configured_versions
  from platform_control.provider_identity_key_policies
  where provider = 'dingtalk'
  for share;
  if configured_versions <> corporate_candidate_versions
     or configured_versions <> union_candidate_versions
  then
    raise check_violation using message = 'identity key policy mismatch';
  end if;

  for locked_identity in
    select distinct version, lookup_hmac
    from (
      select * from unnest(
        corporate_candidate_versions, corporate_candidate_hmacs
      ) corporate(version, lookup_hmac)
      union all
      select * from unnest(
        union_candidate_versions, union_candidate_hmacs
      ) union_identity(version, lookup_hmac)
    ) candidates
    order by version, lookup_hmac
  loop
    perform pg_advisory_xact_lock(
      hashtextextended(
        locked_identity.version::text || ':' ||
          encode(locked_identity.lookup_hmac, 'hex'),
        0
      )
    );
  end loop;

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
    and member.lookup_key_version = corporate_lookup_version
    and member.lookup_hmac = corporate_lookup_hmac
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
        and selected_directory_user_id = corporate_user_ids[1]
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

revoke all on function platform_control.stage_verified_directory_member(
  uuid,uuid,text,bytea,integer,bytea,integer,bytea,integer,text,text
) from public;
revoke all on function platform_control.resolve_verified_dingtalk_member(
  uuid,text,uuid,bytea,integer,bytea,integer,integer[],bytea[],
  uuid,bytea,integer,bytea,integer,integer[],bytea[]
) from public;

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
      message = 'verified identity pair migration owner/environment mismatch';
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
      'revoke all on function platform_control.resolve_verified_dingtalk_member('
      'uuid,text,uuid,bytea,integer,bytea,integer,integer[],bytea[],'
      'uuid,bytea,integer,bytea,integer,integer[],bytea[]) from %I', role_name
    );
    execute format(
      'revoke all on function platform_control.stage_verified_directory_member('
      'uuid,uuid,text,bytea,integer,bytea,integer,bytea,integer,text,text) '
      'from %I', role_name
    );
  end loop;

  execute format(
    'revoke insert, update, delete on '
    'platform_control.provider_identities from %I', selected_directory
  );
  execute format(
    'revoke insert, update, delete on '
    'platform_control.internal_users from %I', selected_directory
  );
  execute format(
    'revoke update (display_name,status,last_confirmed_generation_id,'
    'locally_invalidated_at,updated_at) on '
    'platform_control.internal_users from %I', selected_directory
  );
  execute format(
    'revoke insert, update, delete on '
    'platform_control.directory_members from %I', selected_directory
  );
  execute format(
    'grant execute on function platform_control.resolve_verified_dingtalk_member('
    'uuid,text,uuid,bytea,integer,bytea,integer,integer[],bytea[],'
    'uuid,bytea,integer,bytea,integer,integer[],bytea[]) to %I', selected_app
  );
  execute format(
    'grant execute on function platform_control.stage_verified_directory_member('
    'uuid,uuid,text,bytea,integer,bytea,integer,bytea,integer,text,text) '
    'to %I', selected_directory
  );
end
$migration$;
