alter table platform_control.directory_members
  add column real_name_ciphertext bytea,
  add column real_name_nonce bytea,
  add column real_name_encryption_key_version integer,
  add column mobile_ciphertext bytea,
  add column mobile_nonce bytea,
  add column mobile_encryption_key_version integer,
  add column primary_department_ciphertext bytea,
  add column primary_department_nonce bytea,
  add column primary_department_encryption_key_version integer,
  add constraint directory_member_real_name_v39 check (
    (real_name_ciphertext is null and real_name_nonce is null
      and real_name_encryption_key_version is null)
    or (octet_length(real_name_ciphertext) between 16 and 4096
      and octet_length(real_name_nonce)=12
      and real_name_encryption_key_version > 0)
  ),
  add constraint directory_member_mobile_v39 check (
    (mobile_ciphertext is null and mobile_nonce is null
      and mobile_encryption_key_version is null)
    or (octet_length(mobile_ciphertext) between 16 and 4096
      and octet_length(mobile_nonce)=12
      and mobile_encryption_key_version > 0)
  ),
  add constraint directory_member_primary_department_v39 check (
    (primary_department_ciphertext is null and primary_department_nonce is null
      and primary_department_encryption_key_version is null)
    or (octet_length(primary_department_ciphertext) between 16 and 4096
      and octet_length(primary_department_nonce)=12
      and primary_department_encryption_key_version > 0)
  );

alter table platform_control.directory_generations
  add column source_real_name_present_count integer not null default 0,
  add column real_name_present_count integer not null default 0,
  add column source_mobile_present_count integer not null default 0,
  add column mobile_present_count integer not null default 0,
  add column source_primary_department_present_count integer not null default 0,
  add column primary_department_present_count integer not null default 0,
  drop constraint directory_generation_v34_bounds,
  add constraint directory_generation_v39_bounds check (
    source_schema_version between 0 and 3
    and source_department_count <= 20000 and department_count <= 20000
    and source_member_count <= 200000 and member_count <= 200000
    and source_membership_count <= 1000000 and membership_count <= 1000000
    and source_closure_count <= 2000000 and closure_count <= 2000000
    and source_real_name_present_count between 0 and source_member_count
    and real_name_present_count between 0 and member_count
    and source_mobile_present_count between 0 and source_member_count
    and mobile_present_count between 0 and member_count
    and source_primary_department_present_count between 0 and source_member_count
    and primary_department_present_count between 0 and member_count
    and (expected_content_sha256 is null
      or length(expected_content_sha256)=64)
  );

create function platform_control.directory_generation_checksum_v39(
  selected_generation_id uuid
) returns text
language sql security definer
set search_path = pg_catalog, platform_control
as $function$
  with generation as (
    select * from platform_control.directory_generations
    where generation_id=selected_generation_id
  ), records(category,sort_one,sort_two,sort_three,value) as (
    select 0,''::text,''::text,0,
      convert_to('H','UTF8') ||
      platform_control.canonical_text_v20(source_schema_version::text) ||
      platform_control.canonical_text_v20(source_member_count::text) ||
      platform_control.canonical_text_v20(source_department_count::text) ||
      platform_control.canonical_text_v20(source_membership_count::text) ||
      platform_control.canonical_text_v20(source_closure_count::text) ||
      platform_control.canonical_text_v20(source_real_name_present_count::text) ||
      platform_control.canonical_text_v20(source_mobile_present_count::text) ||
      platform_control.canonical_text_v20(source_primary_department_present_count::text)
    from generation
    union all
    select 1,department.department_key::text,''::text,0,
      convert_to('D','UTF8') ||
      platform_control.canonical_text_v20(department.department_key::text) ||
      platform_control.canonical_text_v20(department.parent_department_key::text) ||
      platform_control.canonical_text_v20(department.display_name) ||
      platform_control.canonical_text_v20(department.lookup_key_version::text) ||
      platform_control.canonical_field_v20(department.lookup_hmac) ||
      platform_control.canonical_text_v20(department.encryption_key_version::text) ||
      platform_control.canonical_field_v20(department.encrypted_provider_id)
    from platform_control.directory_departments department
    where department.generation_id=selected_generation_id
    union all
    select 2,member.member_key::text,''::text,0,
      convert_to('M','UTF8') ||
      platform_control.canonical_text_v20(member.member_key::text) ||
      platform_control.canonical_text_v20(member.display_name) ||
      platform_control.canonical_text_v20(member.status) ||
      platform_control.canonical_text_v20(member.gender) ||
      platform_control.canonical_text_v20(member.lookup_key_version::text) ||
      platform_control.canonical_field_v20(member.lookup_hmac) ||
      platform_control.canonical_text_v20(member.encryption_key_version::text) ||
      platform_control.canonical_field_v20(member.encrypted_provider_id) ||
      platform_control.canonical_text_v20(member.union_lookup_key_version::text) ||
      platform_control.canonical_field_v20(member.union_lookup_hmac) ||
      platform_control.canonical_text_v20(member.union_encryption_key_version::text) ||
      platform_control.canonical_field_v20(member.union_encrypted_provider_id) ||
      platform_control.canonical_text_v20(member.real_name_encryption_key_version::text) ||
      platform_control.canonical_field_v20(member.real_name_nonce) ||
      platform_control.canonical_field_v20(member.real_name_ciphertext) ||
      platform_control.canonical_text_v20(member.mobile_encryption_key_version::text) ||
      platform_control.canonical_field_v20(member.mobile_nonce) ||
      platform_control.canonical_field_v20(member.mobile_ciphertext) ||
      platform_control.canonical_text_v20(member.primary_department_encryption_key_version::text) ||
      platform_control.canonical_field_v20(member.primary_department_nonce) ||
      platform_control.canonical_field_v20(member.primary_department_ciphertext)
    from platform_control.directory_members member
    where member.generation_id=selected_generation_id
    union all
    select 3,membership.member_key::text,membership.department_key::text,0,
      convert_to('P','UTF8') ||
      platform_control.canonical_text_v20(membership.member_key::text) ||
      platform_control.canonical_text_v20(membership.department_key::text)
    from platform_control.member_departments membership
    where membership.generation_id=selected_generation_id
    union all
    select 4,closure.ancestor_department_key::text,
      closure.descendant_department_key::text,closure.depth,
      convert_to('C','UTF8') ||
      platform_control.canonical_text_v20(closure.ancestor_department_key::text) ||
      platform_control.canonical_text_v20(closure.descendant_department_key::text) ||
      platform_control.canonical_text_v20(closure.depth::text)
    from platform_control.department_closure closure
    where closure.generation_id=selected_generation_id
  )
  select encode(sha256(coalesce(
    string_agg(value,''::bytea order by category,sort_one,sort_two,sort_three),
    ''::bytea
  )),'hex') from records;
$function$;

create function platform_control.create_directory_staging_generation_v39(
  selected_generation_id uuid, selected_sync_run_id uuid,
  selected_run_kind text, selected_member_count integer,
  selected_department_count integer, selected_membership_count integer,
  selected_closure_count integer, selected_source_schema_version integer,
  selected_expected_sha256 text, selected_real_name_count integer,
  selected_mobile_count integer, selected_primary_department_count integer
) returns uuid
language plpgsql security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  if selected_source_schema_version <> 3
     or selected_real_name_count not between 0 and selected_member_count
     or selected_mobile_count not between 0 and selected_member_count
     or selected_primary_department_count not between 0 and selected_member_count
  then raise check_violation using message='directory staging input invalid'; end if;
  perform platform_control.create_directory_staging_generation_v34(
    selected_generation_id,selected_sync_run_id,selected_run_kind,
    selected_member_count,selected_department_count,selected_membership_count,
    selected_closure_count,2,selected_expected_sha256
  );
  update platform_control.directory_generations set
    source_schema_version=3,
    source_real_name_present_count=selected_real_name_count,
    source_mobile_present_count=selected_mobile_count,
    source_primary_department_present_count=selected_primary_department_count
  where generation_id=selected_generation_id and status='staging';
  if not found then
    raise check_violation using message='directory staging input invalid';
  end if;
  return selected_generation_id;
end
$function$;

create function platform_control.stage_directory_member_v39(
  selected_generation_id uuid, selected_member_key uuid,
  selected_lookup_hmac bytea, selected_lookup_version integer,
  selected_ciphertext bytea, selected_encryption_version integer,
  selected_union_lookup_hmac bytea, selected_union_lookup_version integer,
  selected_union_ciphertext bytea, selected_union_encryption_version integer,
  selected_display_name text, selected_status text, selected_gender text,
  selected_real_name_ciphertext bytea, selected_real_name_nonce bytea,
  selected_real_name_encryption_version integer,
  selected_mobile_ciphertext bytea, selected_mobile_nonce bytea,
  selected_mobile_encryption_version integer,
  selected_primary_department_ciphertext bytea,
  selected_primary_department_nonce bytea,
  selected_primary_department_encryption_version integer
) returns uuid
language plpgsql security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  if not (
       (selected_real_name_ciphertext is null and selected_real_name_nonce is null
         and selected_real_name_encryption_version is null)
       or (octet_length(selected_real_name_ciphertext) between 16 and 4096
         and octet_length(selected_real_name_nonce)=12
         and selected_real_name_encryption_version=selected_encryption_version)
     ) or not (
       (selected_mobile_ciphertext is null and selected_mobile_nonce is null
         and selected_mobile_encryption_version is null)
       or (octet_length(selected_mobile_ciphertext) between 16 and 4096
         and octet_length(selected_mobile_nonce)=12
         and selected_mobile_encryption_version=selected_encryption_version)
     ) or not (
       (selected_primary_department_ciphertext is null
         and selected_primary_department_nonce is null
         and selected_primary_department_encryption_version is null)
       or (octet_length(selected_primary_department_ciphertext) between 16 and 4096
         and octet_length(selected_primary_department_nonce)=12
         and selected_primary_department_encryption_version=selected_encryption_version)
     )
  then raise check_violation using message='directory member profile invalid'; end if;

  perform platform_control.stage_directory_member_v34(
    selected_generation_id,selected_member_key,selected_lookup_hmac,
    selected_lookup_version,selected_ciphertext,selected_encryption_version,
    selected_union_lookup_hmac,selected_union_lookup_version,
    selected_union_ciphertext,selected_union_encryption_version,
    selected_display_name,selected_status,selected_gender
  );
  update platform_control.directory_members set
    real_name_ciphertext=selected_real_name_ciphertext,
    real_name_nonce=selected_real_name_nonce,
    real_name_encryption_key_version=selected_real_name_encryption_version,
    mobile_ciphertext=selected_mobile_ciphertext,
    mobile_nonce=selected_mobile_nonce,
    mobile_encryption_key_version=selected_mobile_encryption_version,
    primary_department_ciphertext=selected_primary_department_ciphertext,
    primary_department_nonce=selected_primary_department_nonce,
    primary_department_encryption_key_version=selected_primary_department_encryption_version
  where generation_id=selected_generation_id and member_key=selected_member_key;
  if not found then
    raise check_violation using message='directory member profile invalid';
  end if;
  return selected_member_key;
end
$function$;

create function platform_control.validate_directory_generation_v39(
  selected_generation_id uuid
) returns void
language plpgsql security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  selected record;
  expected_v39 text;
  actual_v39 text;
  actual_real_name_count bigint;
  actual_mobile_count bigint;
  actual_primary_department_count bigint;
begin
  select * into strict selected from platform_control.directory_generations
    where generation_id=selected_generation_id for update;
  if selected.status <> 'staging' or selected.source_schema_version <> 3 then
    raise check_violation using message='directory generation v39 invalid';
  end if;
  expected_v39 := selected.expected_content_sha256;

  update platform_control.directory_generations set source_schema_version=2
  where generation_id=selected_generation_id;
  update platform_control.directory_generations set
    expected_content_sha256=platform_control.directory_generation_checksum_v34(
      selected_generation_id
    ) where generation_id=selected_generation_id;
  perform platform_control.validate_directory_generation_v34(selected_generation_id);
  update platform_control.directory_generations set
    source_schema_version=3,expected_content_sha256=expected_v39
  where generation_id=selected_generation_id;

  if exists (
    select 1 from platform_control.directory_members member
    where member.generation_id=selected_generation_id and (
      (member.real_name_ciphertext is not null and
        member.real_name_encryption_key_version<>member.encryption_key_version)
      or (member.mobile_ciphertext is not null and
        member.mobile_encryption_key_version<>member.encryption_key_version)
      or (member.primary_department_ciphertext is not null and
        member.primary_department_encryption_key_version<>member.encryption_key_version)
    )
  ) then raise check_violation using message='directory generation v39 invalid'; end if;

  select
    count(*) filter (where real_name_ciphertext is not null),
    count(*) filter (where mobile_ciphertext is not null),
    count(*) filter (where primary_department_ciphertext is not null)
  into actual_real_name_count,actual_mobile_count,actual_primary_department_count
  from platform_control.directory_members
  where generation_id=selected_generation_id and status='active';

  if selected.source_real_name_present_count<>actual_real_name_count
     or selected.source_mobile_present_count<>actual_mobile_count
     or selected.source_primary_department_present_count<>
        actual_primary_department_count
  then raise check_violation using message='directory profile counts invalid'; end if;

  actual_v39 := platform_control.directory_generation_checksum_v39(
    selected_generation_id
  );
  if expected_v39 is null or expected_v39<>actual_v39 then
    raise check_violation using message='directory checksum mismatch';
  end if;
  update platform_control.directory_generations set
    real_name_present_count=actual_real_name_count,
    mobile_present_count=actual_mobile_count,
    primary_department_present_count=actual_primary_department_count
  where generation_id=selected_generation_id;
exception when no_data_found or too_many_rows then
  raise check_violation using message='directory generation unavailable';
end
$function$;

create or replace function platform_control.finalize_directory_staging_generation(
  selected_generation_id uuid
) returns text language plpgsql security definer
set search_path = pg_catalog, platform_control
as $function$
declare selected_schema_version integer; selected_checksum text;
begin
  select source_schema_version into strict selected_schema_version
    from platform_control.directory_generations
    where generation_id=selected_generation_id for update;
  if selected_schema_version=1 then
    perform platform_control.validate_directory_generation_v20(selected_generation_id);
    selected_checksum := platform_control.directory_generation_checksum_v20(selected_generation_id);
  elsif selected_schema_version=2 then
    perform platform_control.validate_directory_generation_v34(selected_generation_id);
    selected_checksum := platform_control.directory_generation_checksum_v34(selected_generation_id);
  elsif selected_schema_version=3 then
    perform platform_control.validate_directory_generation_v39(selected_generation_id);
    selected_checksum := platform_control.directory_generation_checksum_v39(selected_generation_id);
  else
    raise check_violation using message='directory source schema invalid';
  end if;
  update platform_control.directory_generations set content_sha256=selected_checksum
    where generation_id=selected_generation_id and status='staging';
  return selected_checksum;
end
$function$;

create or replace function platform_control.promote_verified_directory_generation(
  selected_generation_id uuid
) returns uuid
language plpgsql security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  previous_generation_id uuid;
  selected_status text;
  selected_checksum text;
  selected_schema_version integer;
  actual_checksum text;
begin
  if selected_generation_id is null then
    raise check_violation using message='directory generation invalid';
  end if;
  perform platform_control.lock_dingtalk_identity_directory();
  select active_generation_id into previous_generation_id
    from platform_control.directory_state where singleton for update;
  select status,content_sha256,source_schema_version
    into strict selected_status,selected_checksum,selected_schema_version
    from platform_control.directory_generations where generation_id=selected_generation_id
    for update;
  if selected_status='complete' and previous_generation_id=selected_generation_id then
    return selected_generation_id;
  end if;
  if selected_schema_version=1 then
    perform platform_control.validate_directory_generation_v20(selected_generation_id);
    actual_checksum := platform_control.directory_generation_checksum_v20(selected_generation_id);
  elsif selected_schema_version=2 then
    perform platform_control.validate_directory_generation_v34(selected_generation_id);
    actual_checksum := platform_control.directory_generation_checksum_v34(selected_generation_id);
  elsif selected_schema_version=3 then
    perform platform_control.validate_directory_generation_v39(selected_generation_id);
    actual_checksum := platform_control.directory_generation_checksum_v39(selected_generation_id);
  else
    raise check_violation using message='directory source schema invalid';
  end if;
  if selected_checksum is null or selected_checksum<>actual_checksum then
    raise check_violation using message='directory checksum mismatch';
  end if;

  if previous_generation_id is not null then
    with verified_matches as (
      select fresh.member_key,previous.internal_user_id
      from platform_control.directory_members fresh
      join platform_control.directory_members previous
        on previous.generation_id=previous_generation_id
       and previous.status='active'
       and previous.lookup_key_version=fresh.lookup_key_version
       and previous.lookup_hmac=fresh.lookup_hmac
       and previous.union_lookup_key_version=fresh.union_lookup_key_version
       and previous.union_lookup_hmac=fresh.union_lookup_hmac
      join platform_control.provider_identities corporate
        on corporate.subject_kind='employee'
       and corporate.lookup_key_version=fresh.lookup_key_version
       and corporate.lookup_hmac=fresh.lookup_hmac
       and corporate.internal_user_id=previous.internal_user_id
      join platform_control.provider_identities union_identity
        on union_identity.subject_kind='employee_union'
       and union_identity.lookup_key_version=fresh.union_lookup_key_version
       and union_identity.lookup_hmac=fresh.union_lookup_hmac
       and union_identity.internal_user_id=previous.internal_user_id
      join platform_control.internal_users users
        on users.internal_user_id=previous.internal_user_id
       and users.status='active' and users.locally_invalidated_at is null
      where fresh.generation_id=selected_generation_id
        and fresh.status='active' and previous.internal_user_id is not null
    )
    update platform_control.directory_members fresh
      set internal_user_id=verified_matches.internal_user_id
      from verified_matches
      where fresh.generation_id=selected_generation_id
        and fresh.member_key=verified_matches.member_key;

    update platform_control.directory_generations set status='superseded'
      where generation_id=previous_generation_id and status='complete';
    if not found then
      raise check_violation using message='active directory generation invalid';
    end if;
  end if;

  update platform_control.internal_users users
    set last_confirmed_generation_id=selected_generation_id,
      display_name=fresh.display_name,updated_at=clock_timestamp()
    from platform_control.directory_members fresh
    where fresh.generation_id=selected_generation_id
      and fresh.status='active' and fresh.internal_user_id=users.internal_user_id
      and users.status='active' and users.locally_invalidated_at is null;

  update platform_control.directory_generations
    set status='complete',completed_at=clock_timestamp()
    where generation_id=selected_generation_id and status='staging';
  if not found then
    raise check_violation using message='directory generation incomplete';
  end if;
  update platform_control.directory_state
    set active_generation_id=selected_generation_id,last_complete_at=clock_timestamp(),
      updated_at=clock_timestamp() where singleton;
  update platform_control.sync_runs set status='succeeded',
    completed_at=clock_timestamp(),error_code=null
    where generation_id=selected_generation_id and status='running';
  return selected_generation_id;
exception when no_data_found or too_many_rows then
  raise check_violation using message='directory generation unavailable';
end
$function$;

create function platform_control.read_employee_profile_readiness_v39()
returns table(
  generation_id uuid,
  active_employee_count bigint,
  real_name_present_count bigint,
  mobile_present_count bigint,
  primary_department_present_count bigint
)
language sql security definer
set search_path = pg_catalog, platform_control
as $function$
  select generation.generation_id,
    count(member.member_key) filter (where member.status='active'),
    generation.real_name_present_count::bigint,
    generation.mobile_present_count::bigint,
    generation.primary_department_present_count::bigint
  from platform_control.directory_state state
  join platform_control.directory_generations generation
    on generation.generation_id=state.active_generation_id
   and generation.status='complete' and generation.source_schema_version=3
  left join platform_control.directory_members member
    on member.generation_id=generation.generation_id
  where state.singleton
  group by generation.generation_id,generation.real_name_present_count,
    generation.mobile_present_count,
    generation.primary_department_present_count;
$function$;

revoke all on function platform_control.directory_generation_checksum_v39(uuid)
  from public;
revoke all on function platform_control.create_directory_staging_generation_v39(
  uuid,uuid,text,integer,integer,integer,integer,integer,text,integer,integer,integer
) from public;
revoke all on function platform_control.stage_directory_member_v39(
  uuid,uuid,bytea,integer,bytea,integer,bytea,integer,bytea,integer,text,text,text,
  bytea,bytea,integer,bytea,bytea,integer,bytea,bytea,integer
) from public;
revoke all on function platform_control.validate_directory_generation_v39(uuid)
  from public;
revoke all on function platform_control.read_employee_profile_readiness_v39()
  from public;

do $migration$
declare selected_directory name; role_name name;
begin
  if current_database()='agent_platform_control'
     and current_user='platform_control_owner' then
    selected_directory:='platform_directory_worker';
  elsif current_database()='agent_platform_control_preview'
     and current_user='platform_control_owner_preview' then
    selected_directory:='platform_directory_worker_preview';
  else
    raise insufficient_privilege using
      message='directory v39 owner/environment mismatch';
  end if;
  foreach role_name in array array[
    'platform_control_migrator','platform_control_app','platform_directory_worker',
    'platform_stream_ingest','platform_audit_append','platform_control_maintenance',
    'platform_control_migrator_preview','platform_control_app_preview',
    'platform_directory_worker_preview','platform_stream_ingest_preview',
    'platform_audit_append_preview','platform_control_maintenance_preview'
  ] loop
    execute format('revoke all on function platform_control.directory_generation_checksum_v39(uuid) from %I',role_name);
    execute format('revoke all on function platform_control.create_directory_staging_generation_v39(uuid,uuid,text,integer,integer,integer,integer,integer,text,integer,integer,integer) from %I',role_name);
    execute format('revoke all on function platform_control.stage_directory_member_v39(uuid,uuid,bytea,integer,bytea,integer,bytea,integer,bytea,integer,text,text,text,bytea,bytea,integer,bytea,bytea,integer,bytea,bytea,integer) from %I',role_name);
    execute format('revoke all on function platform_control.validate_directory_generation_v39(uuid) from %I',role_name);
    execute format('revoke all on function platform_control.read_employee_profile_readiness_v39() from %I',role_name);
  end loop;
  execute format('grant execute on function platform_control.create_directory_staging_generation_v39(uuid,uuid,text,integer,integer,integer,integer,integer,text,integer,integer,integer) to %I',selected_directory);
  execute format('grant execute on function platform_control.stage_directory_member_v39(uuid,uuid,bytea,integer,bytea,integer,bytea,integer,bytea,integer,text,text,text,bytea,bytea,integer,bytea,bytea,integer,bytea,bytea,integer) to %I',selected_directory);
  execute format('grant execute on function platform_control.read_employee_profile_readiness_v39() to %I',selected_directory);
end
$migration$;
