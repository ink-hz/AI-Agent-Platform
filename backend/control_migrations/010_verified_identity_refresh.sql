create function platform_control.refresh_verified_internal_member(
  selected_user_id uuid,
  selected_display_name text,
  selected_generation_id uuid,
  selected_subject_kind text,
  selected_lookup_versions integer[],
  selected_lookup_hmacs bytea[]
) returns uuid
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  selected_member_key uuid;
  selected_directory_user_id uuid;
begin
  if selected_user_id is null
     or selected_generation_id is null
     or selected_subject_kind <> 'employee'
     or selected_display_name is null
     or selected_display_name <> btrim(selected_display_name)
     or length(selected_display_name) not between 1 and 256
     or selected_display_name ~ '[[:cntrl:]]'
     or selected_lookup_versions is null
     or selected_lookup_hmacs is null
     or array_ndims(selected_lookup_versions) <> 1
     or array_ndims(selected_lookup_hmacs) <> 1
     or array_lower(selected_lookup_versions, 1) <> 1
     or array_lower(selected_lookup_hmacs, 1) <> 1
     or cardinality(selected_lookup_versions) not between 1 and 3
     or cardinality(selected_lookup_versions)
        <> cardinality(selected_lookup_hmacs)
     or array_position(selected_lookup_versions, null) is not null
     or array_position(selected_lookup_hmacs, null) is not null
     or not coalesce(0 < all(selected_lookup_versions), false)
     or exists (
       select 1 from unnest(selected_lookup_hmacs) value
       where octet_length(value) <> 32
     )
  then
    raise check_violation using message = 'verified identity refresh invalid';
  end if;

  select member.member_key, member.internal_user_id
  into strict selected_member_key, selected_directory_user_id
  from platform_control.directory_state state
  join platform_control.directory_generations generation
    on generation.generation_id = state.active_generation_id
   and generation.status = 'complete'
  join platform_control.directory_members member
    on member.generation_id = generation.generation_id
  join unnest(selected_lookup_versions, selected_lookup_hmacs)
    candidate(key_version, lookup_hmac)
    on member.lookup_key_version = candidate.key_version
   and member.lookup_hmac = candidate.lookup_hmac
  where state.singleton
    and generation.generation_id = selected_generation_id
    and member.subject_kind = selected_subject_kind
    and member.status = 'active'
  for update of member;

  if selected_directory_user_id is not null
     and selected_directory_user_id <> selected_user_id
  then
    raise unique_violation using message = 'verified identity collision';
  end if;
  if not exists (
    select 1
    from platform_control.provider_identities identity
    join unnest(selected_lookup_versions, selected_lookup_hmacs)
      candidate(key_version, lookup_hmac)
      on identity.lookup_key_version = candidate.key_version
     and identity.lookup_hmac = candidate.lookup_hmac
    where identity.internal_user_id = selected_user_id
      and identity.subject_kind = selected_subject_kind
  ) then
    raise check_violation using message = 'verified provider mapping required';
  end if;
  if not exists (
    select 1 from platform_control.internal_users users
    where users.internal_user_id = selected_user_id
      and users.status = 'active'
      and users.locally_invalidated_at is null
  ) then
    raise check_violation using message = 'active internal member required';
  end if;

  update platform_control.directory_members
  set internal_user_id = selected_user_id
  where generation_id = selected_generation_id
    and member_key = selected_member_key;

  update platform_control.internal_users
  set display_name = selected_display_name,
      last_confirmed_generation_id = selected_generation_id,
      updated_at = now()
  where internal_user_id = selected_user_id;
  return selected_user_id;
exception
  when no_data_found or too_many_rows then
    raise check_violation using message = 'active directory member unavailable';
end
$function$;

revoke all on function platform_control.refresh_verified_internal_member(
  uuid, text, uuid, text, integer[], bytea[]
) from public;

do $migration$
declare
  selected_app name;
  role_name name;
begin
  selected_app := case current_database()
    when 'agent_platform_control' then 'platform_control_app'
    when 'agent_platform_control_preview' then 'platform_control_app_preview'
    else null
  end;
  if selected_app is null then
    raise exception 'unsupported control database: %', current_database();
  end if;
  foreach role_name in array array[
    'platform_control_migrator',
    'platform_control_app',
    'platform_directory_worker',
    'platform_stream_ingest',
    'platform_audit_append',
    'platform_control_maintenance',
    'platform_control_migrator_preview',
    'platform_control_app_preview',
    'platform_directory_worker_preview',
    'platform_stream_ingest_preview',
    'platform_audit_append_preview',
    'platform_control_maintenance_preview'
  ] loop
    execute format(
      'revoke all on function platform_control.refresh_verified_internal_member('
      'uuid,text,uuid,text,integer[],bytea[]) from %I', role_name
    );
  end loop;
  execute format(
    'grant execute on function platform_control.refresh_verified_internal_member('
    'uuid,text,uuid,text,integer[],bytea[]) to %I', selected_app
  );
end
$migration$;
