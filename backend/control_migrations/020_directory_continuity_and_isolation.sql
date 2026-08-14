alter table platform_control.directory_generations
  add column source_schema_version integer not null default 0,
  add column source_closure_count integer not null default 0,
  add column closure_count integer not null default 0,
  add column expected_content_sha256 text,
  add constraint directory_generation_v20_bounds check (
    source_schema_version between 0 and 1
    and source_department_count <= 20000
    and department_count <= 20000
    and source_member_count <= 200000
    and member_count <= 200000
    and source_membership_count <= 1000000
    and membership_count <= 1000000
    and source_closure_count <= 2000000
    and closure_count <= 2000000
    and (
      expected_content_sha256 is null
      or length(expected_content_sha256)=64
    )
  );

alter table platform_control.directory_members
  add constraint directory_member_ciphertext_v20_bound check (
    octet_length(encrypted_provider_id) <= 4096
    and (
      union_encrypted_provider_id is null
      or octet_length(union_encrypted_provider_id) <= 4096
    )
  );
alter table platform_control.directory_departments
  add constraint directory_department_ciphertext_v20_bound check (
    octet_length(encrypted_provider_id) <= 4096
  );

create function platform_control.canonical_field_v20(selected_value bytea)
returns bytea language sql immutable
set search_path = pg_catalog, platform_control
as $function$
  select case when selected_value is null then int4send(-1)
    else int4send(octet_length(selected_value)) || selected_value end;
$function$;

create function platform_control.canonical_text_v20(selected_value text)
returns bytea language sql immutable
set search_path = pg_catalog, platform_control
as $function$
  select platform_control.canonical_field_v20(
    case when selected_value is null then null else convert_to(selected_value,'UTF8') end
  );
$function$;

create function platform_control.directory_generation_checksum_v20(
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
      platform_control.canonical_text_v20(source_closure_count::text)
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
      platform_control.canonical_text_v20(member.lookup_key_version::text) ||
      platform_control.canonical_field_v20(member.lookup_hmac) ||
      platform_control.canonical_text_v20(member.encryption_key_version::text) ||
      platform_control.canonical_field_v20(member.encrypted_provider_id) ||
      platform_control.canonical_text_v20(member.union_lookup_key_version::text) ||
      platform_control.canonical_field_v20(member.union_lookup_hmac) ||
      platform_control.canonical_text_v20(member.union_encryption_key_version::text) ||
      platform_control.canonical_field_v20(member.union_encrypted_provider_id)
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

create function platform_control.create_directory_staging_generation_v20(
  selected_generation_id uuid, selected_sync_run_id uuid,
  selected_run_kind text, selected_member_count integer,
  selected_department_count integer, selected_membership_count integer,
  selected_closure_count integer, selected_source_schema_version integer,
  selected_expected_sha256 text
) returns uuid
language plpgsql security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  if selected_generation_id is null or selected_sync_run_id is null
     or selected_run_kind not in ('startup','scheduled','targeted','event')
     or selected_source_schema_version <> 1
     or selected_member_count not between 0 and 200000
     or selected_department_count not between 1 and 20000
     or selected_membership_count not between 0 and 1000000
     or selected_closure_count not between 1 and 2000000
     or selected_expected_sha256 !~ '^[0-9a-f]{64}$'
  then raise check_violation using message='directory staging input invalid'; end if;
  insert into platform_control.directory_generations(
    generation_id,status,member_count,department_count,membership_count,
    closure_count,source_member_count,source_department_count,
    source_membership_count,source_closure_count,source_schema_version,
    expected_content_sha256
  ) values (
    selected_generation_id,'staging',selected_member_count,
    selected_department_count,selected_membership_count,selected_closure_count,
    selected_member_count,selected_department_count,selected_membership_count,
    selected_closure_count,selected_source_schema_version,selected_expected_sha256
  );
  insert into platform_control.sync_runs(
    sync_run_id,run_kind,status,generation_id,member_count,department_count
  ) values (
    selected_sync_run_id,selected_run_kind,'running',selected_generation_id,
    selected_member_count,selected_department_count
  );
  return selected_generation_id;
end
$function$;

create function platform_control.validate_directory_generation_v20(
  selected_generation_id uuid
) returns void
language plpgsql security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  selected record;
  actual_members bigint;
  actual_departments bigint;
  actual_memberships bigint;
  actual_closure bigint;
  actual_checksum text;
begin
  select * into strict selected from platform_control.directory_generations
    where generation_id=selected_generation_id for update;
  select count(*) into actual_members from platform_control.directory_members
    where generation_id=selected_generation_id;
  select count(*) into actual_departments from platform_control.directory_departments
    where generation_id=selected_generation_id;
  select count(*) into actual_memberships from platform_control.member_departments
    where generation_id=selected_generation_id;
  select count(*) into actual_closure from platform_control.department_closure
    where generation_id=selected_generation_id;

  if selected.status <> 'staging'
     or selected.source_schema_version <> 1
     or selected.member_count <> selected.source_member_count
     or selected.department_count <> selected.source_department_count
     or selected.membership_count <> selected.source_membership_count
     or selected.closure_count <> selected.source_closure_count
     or selected.member_count <> actual_members
     or selected.department_count <> actual_departments
     or selected.membership_count <> actual_memberships
     or selected.closure_count <> actual_closure
     or selected.department_count > 20000 or selected.member_count > 200000
     or selected.membership_count > 1000000 or actual_closure > 2000000
     or (actual_departments > 0 and (
       select count(*) from platform_control.directory_departments
       where generation_id=selected_generation_id
         and parent_department_key is null
     ) <> 1)
     or exists(select 1 from platform_control.directory_departments child
       where child.generation_id=selected_generation_id
         and child.parent_department_key is not null
         and not exists(select 1 from platform_control.directory_departments parent
           where parent.generation_id=selected_generation_id
             and parent.department_key=child.parent_department_key))
     or exists(select 1 from platform_control.department_closure
       where generation_id=selected_generation_id and depth > 128)
     or exists(select 1 from platform_control.member_departments
       where generation_id=selected_generation_id group by member_key
       having count(*) > 128)
     or exists(select 1 from platform_control.directory_members
       where generation_id=selected_generation_id and (
         subject_kind <> 'employee'
         or length(display_name) not between 1 and 256
         or octet_length(encrypted_provider_id) not between 29 and 4096
         or octet_length(union_encrypted_provider_id) not between 29 and 4096
         or union_lookup_hmac is null
         or octet_length(union_lookup_hmac) <> 32
         or lookup_key_version <> union_lookup_key_version
         or encryption_key_version <> union_encryption_key_version
       ))
     or exists(select 1 from platform_control.directory_departments
       where generation_id=selected_generation_id and (
         subject_kind <> 'department'
         or length(display_name) not between 1 and 256
         or octet_length(encrypted_provider_id) not between 29 and 4096
       ))
     or (select count(distinct key_version) from (
       select lookup_key_version as key_version
       from platform_control.directory_members
       where generation_id=selected_generation_id
       union all
       select lookup_key_version from platform_control.directory_departments
       where generation_id=selected_generation_id
     ) generation_versions) <> 1
     or exists(select 1 from platform_control.directory_members member
       where member.generation_id=selected_generation_id
         and not exists(select 1 from platform_control.member_departments membership
           where membership.generation_id=member.generation_id
             and membership.member_key=member.member_key))
  then raise check_violation using message='directory generation v20 invalid'; end if;

  if exists (
    with recursive lineage(descendant,ancestor,depth,path,cycle) as (
      select department_key,department_key,0,array[department_key],false
      from platform_control.directory_departments
      where generation_id=selected_generation_id
      union all
      select lineage.descendant,department.parent_department_key,
        lineage.depth+1,lineage.path||department.parent_department_key,
        department.parent_department_key=any(lineage.path)
      from lineage join platform_control.directory_departments department
        on department.generation_id=selected_generation_id
       and department.department_key=lineage.ancestor
      where department.parent_department_key is not null
        and not lineage.cycle and lineage.depth <= 128
    ) select 1 from lineage where cycle or depth > 128
  ) then raise check_violation using message='directory department cycle'; end if;

  if exists (
    with recursive expected(ancestor,descendant,depth) as (
      select department_key,department_key,0
      from platform_control.directory_departments
      where generation_id=selected_generation_id
      union all
      select parent.parent_department_key,expected.descendant,expected.depth+1
      from expected join platform_control.directory_departments parent
        on parent.generation_id=selected_generation_id
       and parent.department_key=expected.ancestor
      where parent.parent_department_key is not null and expected.depth < 128
    ), differences as (
      (select ancestor,descendant,depth from expected
       except select ancestor_department_key,descendant_department_key,depth
       from platform_control.department_closure
       where generation_id=selected_generation_id)
      union all
      (select ancestor_department_key,descendant_department_key,depth
       from platform_control.department_closure
       where generation_id=selected_generation_id
       except select ancestor,descendant,depth from expected)
    ) select 1 from differences
  ) then raise check_violation using message='directory closure incomplete'; end if;

  actual_checksum := platform_control.directory_generation_checksum_v20(selected_generation_id);
  if selected.expected_content_sha256 is null
     or selected.expected_content_sha256 <> actual_checksum
  then raise check_violation using message='directory checksum mismatch'; end if;
end
$function$;

create or replace function platform_control.finalize_directory_staging_generation(
  selected_generation_id uuid
) returns text language plpgsql security definer
set search_path = pg_catalog, platform_control
as $function$
declare selected_checksum text;
begin
  perform platform_control.validate_directory_generation_v20(selected_generation_id);
  selected_checksum := platform_control.directory_generation_checksum_v20(selected_generation_id);
  update platform_control.directory_generations set content_sha256=selected_checksum
    where generation_id=selected_generation_id and status='staging';
  return selected_checksum;
end
$function$;

create function platform_control.read_active_directory_member_v20(
  selected_lookup_version integer, selected_lookup_hmac bytea,
  selected_union_lookup_version integer, selected_union_lookup_hmac bytea
) returns table(
  generation_id uuid, member_key uuid, internal_user_id uuid,
  lookup_key_version integer, lookup_hmac bytea,
  union_lookup_key_version integer, union_lookup_hmac bytea,
  member_status text
) language sql security definer
set search_path = pg_catalog, platform_control
as $function$
  select member.generation_id,member.member_key,member.internal_user_id,
    member.lookup_key_version,member.lookup_hmac,
    member.union_lookup_key_version,member.union_lookup_hmac,member.status
  from platform_control.directory_state state
  join platform_control.directory_generations generation
    on generation.generation_id=state.active_generation_id
   and generation.status='complete'
  join platform_control.directory_members member
    on member.generation_id=generation.generation_id
  where state.singleton and member.status='active'
    and member.lookup_key_version=selected_lookup_version
    and member.lookup_hmac=selected_lookup_hmac
    and member.union_lookup_key_version=selected_union_lookup_version
    and member.union_lookup_hmac=selected_union_lookup_hmac;
$function$;

create function platform_control.read_active_directory_status_v20()
returns table(
  database_now timestamptz,last_complete_at timestamptz,
  active_generation_id uuid,last_run_result text
) language sql security definer
set search_path = pg_catalog, platform_control
as $function$
  select clock_timestamp(),state.last_complete_at,state.active_generation_id,
    (select run.status from platform_control.sync_runs run
     order by run.started_at desc,run.sync_run_id desc limit 1)
  from platform_control.directory_state state
  join platform_control.directory_generations generation
    on generation.generation_id=state.active_generation_id
   and generation.status='complete'
  where state.singleton;
$function$;

create or replace function platform_control.promote_verified_directory_generation(
  selected_generation_id uuid
) returns uuid
language plpgsql security definer
set search_path = pg_catalog, platform_control
as $function$
declare previous_generation_id uuid; selected_status text; selected_checksum text;
begin
  if selected_generation_id is null then
    raise check_violation using message='directory generation invalid';
  end if;
  perform platform_control.lock_dingtalk_identity_directory();
  select active_generation_id into previous_generation_id
    from platform_control.directory_state where singleton for update;
  select status,content_sha256 into strict selected_status,selected_checksum
    from platform_control.directory_generations where generation_id=selected_generation_id
    for update;
  if selected_status='complete' and previous_generation_id=selected_generation_id then
    return selected_generation_id;
  end if;
  perform platform_control.validate_directory_generation_v20(selected_generation_id);
  if selected_checksum is null
     or selected_checksum <> platform_control.directory_generation_checksum_v20(selected_generation_id)
  then raise check_violation using message='directory checksum mismatch'; end if;

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
    if not found then raise check_violation using message='active directory generation invalid'; end if;
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
  if not found then raise check_violation using message='directory generation incomplete'; end if;
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

revoke all on function platform_control.canonical_field_v20(bytea) from public;
revoke all on function platform_control.canonical_text_v20(text) from public;
revoke all on function platform_control.directory_generation_checksum_v20(uuid) from public;
revoke all on function platform_control.create_directory_staging_generation_v20(uuid,uuid,text,integer,integer,integer,integer,integer,text) from public;
revoke all on function platform_control.validate_directory_generation_v20(uuid) from public;
revoke all on function platform_control.read_active_directory_member_v20(integer,bytea,integer,bytea) from public;
revoke all on function platform_control.read_active_directory_status_v20() from public;

do $migration$
declare selected_app name; selected_directory name; role_name name;
begin
  if current_database()='agent_platform_control' and current_user='platform_control_owner' then
    selected_app:='platform_control_app'; selected_directory:='platform_directory_worker';
  elsif current_database()='agent_platform_control_preview' and current_user='platform_control_owner_preview' then
    selected_app:='platform_control_app_preview'; selected_directory:='platform_directory_worker_preview';
  else raise insufficient_privilege using message='directory v20 owner/environment mismatch';
  end if;
  foreach role_name in array array[
    'platform_control_migrator','platform_control_app','platform_directory_worker',
    'platform_stream_ingest','platform_audit_append','platform_control_maintenance',
    'platform_control_migrator_preview','platform_control_app_preview',
    'platform_directory_worker_preview','platform_stream_ingest_preview',
    'platform_audit_append_preview','platform_control_maintenance_preview'
  ] loop
    execute format('revoke all on function platform_control.create_directory_staging_generation(uuid,uuid,text,integer,integer,integer) from %I',role_name);
    execute format('revoke all on function platform_control.create_directory_staging_generation_v20(uuid,uuid,text,integer,integer,integer,integer,integer,text) from %I',role_name);
    execute format('revoke all on function platform_control.read_active_directory_member_v20(integer,bytea,integer,bytea) from %I',role_name);
    execute format('revoke all on function platform_control.read_active_directory_status_v20() from %I',role_name);
  end loop;
  execute format('revoke select on platform_control.directory_generations,platform_control.directory_state,platform_control.directory_members,platform_control.directory_departments,platform_control.department_closure,platform_control.member_departments from %I',selected_app);
  execute format('grant execute on function platform_control.create_directory_staging_generation_v20(uuid,uuid,text,integer,integer,integer,integer,integer,text) to %I',selected_directory);
  execute format('grant execute on function platform_control.read_active_directory_member_v20(integer,bytea,integer,bytea) to %I',selected_app);
  execute format('grant execute on function platform_control.read_active_directory_status_v20() to %I',selected_app);
end
$migration$;
