alter table platform_control.directory_generations
  add column source_member_count integer not null default 0
    check (source_member_count >= 0),
  add column source_department_count integer not null default 0
    check (source_department_count >= 0),
  add column source_membership_count integer not null default 0
    check (source_membership_count >= 0),
  add column membership_count integer not null default 0
    check (membership_count >= 0);

alter table platform_control.directory_departments
  add column parent_department_key uuid;

alter table platform_control.directory_members
  add column union_encrypted_provider_id bytea,
  add column union_encryption_key_version integer,
  add constraint directory_members_union_cipher_pair_complete check (
    (union_encrypted_provider_id is null and union_encryption_key_version is null)
    or (
      octet_length(union_encrypted_provider_id) >= 29
      and union_encryption_key_version > 0
    )
  );

create index directory_departments_parent
  on platform_control.directory_departments
    (generation_id, parent_department_key, department_key);
create index department_closure_ancestor_depth
  on platform_control.department_closure
    (generation_id, ancestor_department_key, depth, descendant_department_key);

create function platform_control.directory_generation_checksum(
  selected_generation_id uuid
) returns text
language sql
security definer
set search_path = pg_catalog, platform_control
as $function$
  select encode(sha256(convert_to(coalesce(string_agg(item, E'\n' order by item), ''), 'UTF8')), 'hex')
  from (
    select jsonb_build_array(
      'D', department_key::text, coalesce(parent_department_key::text, ''),
      encode(lookup_hmac, 'hex'), lookup_key_version, display_name
    )::text as item
    from platform_control.directory_departments
    where generation_id = selected_generation_id
    union all
    select jsonb_build_array(
      'M', member_key::text, encode(lookup_hmac, 'hex'), lookup_key_version,
      encode(union_lookup_hmac, 'hex'), union_lookup_key_version,
      encryption_key_version, union_encryption_key_version, display_name, status
    )::text
    from platform_control.directory_members
    where generation_id = selected_generation_id
    union all
    select jsonb_build_array('P', member_key::text, department_key::text)::text
    from platform_control.member_departments
    where generation_id = selected_generation_id
    union all
    select jsonb_build_array(
      'C', ancestor_department_key::text, descendant_department_key::text, depth
    )::text
    from platform_control.department_closure
    where generation_id = selected_generation_id
  ) canonical_items;
$function$;

create function platform_control.validate_directory_generation(
  selected_generation_id uuid
) returns void
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  selected record;
  actual_members bigint;
  actual_departments bigint;
  actual_memberships bigint;
  actual_checksum text;
begin
  select * into strict selected
  from platform_control.directory_generations
  where generation_id = selected_generation_id
  for update;
  select count(*) into actual_members from platform_control.directory_members
    where generation_id = selected_generation_id;
  select count(*) into actual_departments from platform_control.directory_departments
    where generation_id = selected_generation_id;
  select count(*) into actual_memberships from platform_control.member_departments
    where generation_id = selected_generation_id;

  if selected.status <> 'staging'
     or selected.member_count <> selected.source_member_count
     or selected.department_count <> selected.source_department_count
     or selected.membership_count <> selected.source_membership_count
     or selected.member_count <> actual_members
     or selected.department_count <> actual_departments
     or selected.membership_count <> actual_memberships
     or (actual_departments > 0 and (
       select count(*) from platform_control.directory_departments
       where generation_id = selected_generation_id
         and parent_department_key is null
     ) <> 1)
     or exists (
       select 1 from platform_control.directory_departments child
       where child.generation_id = selected_generation_id
         and child.parent_department_key is not null
         and not exists (
           select 1 from platform_control.directory_departments parent
           where parent.generation_id = selected_generation_id
             and parent.department_key = child.parent_department_key
         )
     )
     or exists (
       select 1 from platform_control.directory_members member
       where member.generation_id = selected_generation_id
         and (
           member.subject_kind <> 'employee'
           or member.union_lookup_hmac is null
           or octet_length(member.union_lookup_hmac) <> 32
           or member.union_lookup_key_version <> member.lookup_key_version
           or octet_length(member.encrypted_provider_id) < 29
           or member.union_encrypted_provider_id is null
           or octet_length(member.union_encrypted_provider_id) < 29
           or member.union_encryption_key_version is null
           or member.union_encryption_key_version <= 0
         )
     )
     or (
       select count(distinct key_version)
       from (
         select lookup_key_version as key_version
         from platform_control.directory_members
         where generation_id=selected_generation_id
         union all
         select lookup_key_version
         from platform_control.directory_departments
         where generation_id=selected_generation_id
       ) generation_versions
     ) <> 1
     or exists (
       select 1 from platform_control.directory_members member
       where member.generation_id = selected_generation_id
         and not exists (
           select 1 from platform_control.member_departments membership
           where membership.generation_id = member.generation_id
             and membership.member_key = member.member_key
         )
     )
  then
    raise check_violation using message = 'directory generation incomplete';
  end if;

  if exists (
    with recursive lineage(descendant, ancestor, depth, path, cycle) as (
      select department_key, department_key, 0, array[department_key], false
      from platform_control.directory_departments
      where generation_id = selected_generation_id
      union all
      select lineage.descendant, department.parent_department_key,
        lineage.depth + 1,
        lineage.path || department.parent_department_key,
        department.parent_department_key = any(lineage.path)
      from lineage
      join platform_control.directory_departments department
        on department.generation_id = selected_generation_id
       and department.department_key = lineage.ancestor
      where department.parent_department_key is not null
        and not lineage.cycle
        and lineage.depth <= actual_departments
    ) select 1 from lineage where cycle
  ) then
    raise check_violation using message = 'directory department cycle';
  end if;

  if exists (
    with recursive expected(ancestor, descendant, depth) as (
      select department_key, department_key, 0
      from platform_control.directory_departments
      where generation_id = selected_generation_id
      union all
      select parent.parent_department_key, expected.descendant, expected.depth + 1
      from expected
      join platform_control.directory_departments parent
        on parent.generation_id = selected_generation_id
       and parent.department_key = expected.ancestor
      where parent.parent_department_key is not null
        and expected.depth <= actual_departments
    ), differences as (
      (select ancestor, descendant, depth from expected
       except
       select ancestor_department_key, descendant_department_key, depth
       from platform_control.department_closure
       where generation_id = selected_generation_id)
      union all
      (select ancestor_department_key, descendant_department_key, depth
       from platform_control.department_closure
       where generation_id = selected_generation_id
       except
       select ancestor, descendant, depth from expected)
    ) select 1 from differences
  ) then
    raise check_violation using message = 'directory closure incomplete';
  end if;

  actual_checksum := platform_control.directory_generation_checksum(selected_generation_id);
  if selected.content_sha256 is not null and selected.content_sha256 <> actual_checksum then
    raise check_violation using message = 'directory checksum mismatch';
  end if;
end
$function$;

create function platform_control.create_directory_staging_generation(
  selected_generation_id uuid,
  selected_sync_run_id uuid,
  selected_run_kind text,
  selected_member_count integer,
  selected_department_count integer,
  selected_membership_count integer
) returns uuid
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  if selected_generation_id is null or selected_sync_run_id is null
     or selected_run_kind not in ('startup','scheduled','targeted','event')
     or selected_member_count < 0 or selected_department_count < 1
     or selected_membership_count < 0
  then raise check_violation using message='directory staging input invalid'; end if;
  insert into platform_control.directory_generations (
    generation_id,status,member_count,department_count,membership_count,
    source_member_count,source_department_count,source_membership_count
  ) values (
    selected_generation_id,'staging',selected_member_count,selected_department_count,
    selected_membership_count,selected_member_count,selected_department_count,
    selected_membership_count
  );
  insert into platform_control.sync_runs (
    sync_run_id,run_kind,status,generation_id,member_count,department_count
  ) values (
    selected_sync_run_id,selected_run_kind,'running',selected_generation_id,
    selected_member_count,selected_department_count
  );
  return selected_generation_id;
end
$function$;

create function platform_control.stage_directory_department(
  selected_generation_id uuid, selected_department_key uuid,
  selected_parent_department_key uuid, selected_lookup_hmac bytea,
  selected_lookup_version integer, selected_ciphertext bytea,
  selected_encryption_version integer, selected_display_name text
) returns uuid
language plpgsql security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  if selected_generation_id is null or selected_department_key is null
     or selected_department_key = selected_parent_department_key
     or octet_length(selected_lookup_hmac) <> 32 or selected_lookup_version <= 0
     or octet_length(selected_ciphertext) < 29 or selected_encryption_version <= 0
     or selected_display_name is null or selected_display_name <> btrim(selected_display_name)
     or length(selected_display_name) not between 1 and 256
     or selected_display_name ~ '[[:cntrl:]]'
     or not exists (
       select 1 from platform_control.provider_identity_key_policies
       where provider='dingtalk'
         and selected_lookup_version = any(lookup_transition_versions)
     )
     or not exists (select 1 from platform_control.directory_generations
       where generation_id=selected_generation_id and status='staging' for update)
  then raise check_violation using message='directory department invalid'; end if;
  insert into platform_control.directory_departments (
    generation_id,department_key,parent_department_key,subject_kind,
    lookup_hmac,lookup_key_version,encrypted_provider_id,encryption_key_version,
    display_name
  ) values (
    selected_generation_id,selected_department_key,selected_parent_department_key,
    'department',selected_lookup_hmac,selected_lookup_version,selected_ciphertext,
    selected_encryption_version,selected_display_name
  );
  return selected_department_key;
end
$function$;

create function platform_control.stage_directory_member_v19(
  selected_generation_id uuid, selected_member_key uuid,
  selected_lookup_hmac bytea, selected_lookup_version integer,
  selected_ciphertext bytea, selected_encryption_version integer,
  selected_union_lookup_hmac bytea, selected_union_lookup_version integer,
  selected_union_ciphertext bytea, selected_union_encryption_version integer,
  selected_display_name text, selected_status text
) returns uuid
language plpgsql security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  if selected_generation_id is null or selected_member_key is null
     or octet_length(selected_lookup_hmac) <> 32
     or octet_length(selected_union_lookup_hmac) <> 32
     or selected_lookup_version <= 0
     or selected_union_lookup_version <> selected_lookup_version
     or octet_length(selected_ciphertext) < 29
     or octet_length(selected_union_ciphertext) < 29
     or selected_encryption_version <= 0 or selected_union_encryption_version <= 0
     or selected_display_name is null or selected_display_name <> btrim(selected_display_name)
     or length(selected_display_name) not between 1 and 256
     or selected_display_name ~ '[[:cntrl:]]'
     or selected_status not in ('active','inactive','disabled')
     or not exists (
       select 1 from platform_control.provider_identity_key_policies
       where provider='dingtalk'
         and selected_lookup_version = any(lookup_transition_versions)
     )
     or not exists (select 1 from platform_control.directory_generations
       where generation_id=selected_generation_id and status='staging' for update)
  then raise check_violation using message='directory member invalid'; end if;
  insert into platform_control.directory_members (
    generation_id,member_key,internal_user_id,subject_kind,lookup_hmac,
    lookup_key_version,encrypted_provider_id,encryption_key_version,
    union_lookup_hmac,union_lookup_key_version,union_encrypted_provider_id,
    union_encryption_key_version,display_name,status
  ) values (
    selected_generation_id,selected_member_key,null,'employee',selected_lookup_hmac,
    selected_lookup_version,selected_ciphertext,selected_encryption_version,
    selected_union_lookup_hmac,selected_union_lookup_version,
    selected_union_ciphertext,selected_union_encryption_version,
    selected_display_name,selected_status
  );
  return selected_member_key;
end
$function$;

create function platform_control.stage_directory_membership(
  selected_generation_id uuid, selected_member_key uuid,
  selected_department_key uuid
) returns void language plpgsql security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  if not exists (select 1 from platform_control.directory_generations
      where generation_id=selected_generation_id and status='staging' for update)
  then raise check_violation using message='staging directory generation required'; end if;
  insert into platform_control.member_departments
    (generation_id,member_key,department_key)
  values (selected_generation_id,selected_member_key,selected_department_key);
end
$function$;

create function platform_control.stage_department_closure(
  selected_generation_id uuid, selected_ancestor uuid,
  selected_descendant uuid, selected_depth integer
) returns void language plpgsql security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  if selected_depth < 0
     or (selected_depth = 0) <> (selected_ancestor = selected_descendant)
     or not exists (select 1 from platform_control.directory_generations
       where generation_id=selected_generation_id and status='staging' for update)
  then raise check_violation using message='directory closure invalid'; end if;
  insert into platform_control.department_closure
    (generation_id,ancestor_department_key,descendant_department_key,depth)
  values (selected_generation_id,selected_ancestor,selected_descendant,selected_depth);
end
$function$;

create function platform_control.finalize_directory_staging_generation(
  selected_generation_id uuid
) returns text language plpgsql security definer
set search_path = pg_catalog, platform_control
as $function$
declare selected_checksum text;
begin
  perform platform_control.validate_directory_generation(selected_generation_id);
  selected_checksum := platform_control.directory_generation_checksum(selected_generation_id);
  update platform_control.directory_generations set content_sha256=selected_checksum
    where generation_id=selected_generation_id and status='staging';
  return selected_checksum;
end
$function$;

create function platform_control.fail_directory_staging_generation(
  selected_generation_id uuid, selected_error_code text
) returns void language plpgsql security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  if selected_error_code not in (
    'provider_failed','sync_timeout','staging_failed','checksum_mismatch',
    'directory_invalid','database_failed'
  ) then raise check_violation using message='directory failure code invalid'; end if;
  update platform_control.directory_generations set status='failed'
    where generation_id=selected_generation_id and status='staging';
  update platform_control.sync_runs set status='failed',completed_at=clock_timestamp(),
    error_code=selected_error_code
    where generation_id=selected_generation_id and status='running';
end
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
    from platform_control.directory_generations
    where generation_id=selected_generation_id for update;
  if selected_status='complete' and previous_generation_id=selected_generation_id then
    return selected_generation_id;
  end if;
  perform platform_control.validate_directory_generation(selected_generation_id);
  if selected_checksum is null
     or selected_checksum <> platform_control.directory_generation_checksum(selected_generation_id)
  then raise check_violation using message='directory checksum mismatch'; end if;
  if previous_generation_id is not null then
    update platform_control.directory_generations set status='superseded'
      where generation_id=previous_generation_id and status='complete';
    if not found then raise check_violation using message='active directory generation invalid'; end if;
  end if;
  update platform_control.directory_generations
    set status='complete',completed_at=clock_timestamp()
    where generation_id=selected_generation_id and status='staging';
  if not found then raise check_violation using message='directory generation incomplete'; end if;
  update platform_control.directory_state
    set active_generation_id=selected_generation_id,
        last_complete_at=clock_timestamp(),updated_at=clock_timestamp()
    where singleton;
  update platform_control.sync_runs
    set status='succeeded',completed_at=clock_timestamp(),error_code=null
    where generation_id=selected_generation_id and status='running';
  return selected_generation_id;
exception when no_data_found or too_many_rows then
  raise check_violation using message='directory generation unavailable';
end
$function$;

create function platform_control.try_directory_worker_lease()
returns boolean language sql security definer
set search_path = pg_catalog, platform_control
as $function$ select pg_try_advisory_lock(1229998930); $function$;
create function platform_control.release_directory_worker_lease()
returns boolean language sql security definer
set search_path = pg_catalog, platform_control
as $function$ select pg_advisory_unlock(1229998930); $function$;

revoke all on all functions in schema platform_control from public;

do $migration$
declare selected_directory name; role_name name;
begin
  if current_database()='agent_platform_control' and current_user='platform_control_owner' then
    selected_directory := 'platform_directory_worker';
  elsif current_database()='agent_platform_control_preview' and current_user='platform_control_owner_preview' then
    selected_directory := 'platform_directory_worker_preview';
  else raise insufficient_privilege using message='directory migration owner/environment mismatch';
  end if;
  foreach role_name in array array[
    'platform_control_migrator','platform_control_app','platform_directory_worker',
    'platform_stream_ingest','platform_audit_append','platform_control_maintenance',
    'platform_control_migrator_preview','platform_control_app_preview',
    'platform_directory_worker_preview','platform_stream_ingest_preview',
    'platform_audit_append_preview','platform_control_maintenance_preview'
  ] loop
    execute format('revoke insert,update,delete on platform_control.directory_generations,platform_control.directory_state,platform_control.directory_members,platform_control.directory_departments,platform_control.department_closure,platform_control.member_departments,platform_control.sync_runs from %I',role_name);
    execute format('revoke all on function platform_control.stage_verified_directory_member(uuid,uuid,text,bytea,integer,bytea,integer,bytea,integer,text,text) from %I',role_name);
    execute format('revoke all on function platform_control.promote_verified_directory_generation(uuid) from %I',role_name);
  end loop;
  execute format('grant execute on function platform_control.create_directory_staging_generation(uuid,uuid,text,integer,integer,integer) to %I',selected_directory);
  execute format('grant execute on function platform_control.stage_directory_department(uuid,uuid,uuid,bytea,integer,bytea,integer,text) to %I',selected_directory);
  execute format('grant execute on function platform_control.stage_directory_member_v19(uuid,uuid,bytea,integer,bytea,integer,bytea,integer,bytea,integer,text,text) to %I',selected_directory);
  execute format('grant execute on function platform_control.stage_directory_membership(uuid,uuid,uuid) to %I',selected_directory);
  execute format('grant execute on function platform_control.stage_department_closure(uuid,uuid,uuid,integer) to %I',selected_directory);
  execute format('grant execute on function platform_control.finalize_directory_staging_generation(uuid) to %I',selected_directory);
  execute format('grant execute on function platform_control.fail_directory_staging_generation(uuid,text) to %I',selected_directory);
  execute format('grant execute on function platform_control.promote_verified_directory_generation(uuid) to %I',selected_directory);
  execute format('grant execute on function platform_control.try_directory_worker_lease() to %I',selected_directory);
  execute format('grant execute on function platform_control.release_directory_worker_lease() to %I',selected_directory);
end
$migration$;
