create function platform_control.read_office_recipient_directory_v54(
  selected_operation text,
  selected_query text,
  selected_department_ids uuid[],
  selected_include_descendants boolean,
  selected_limit integer,
  selected_cursor uuid,
  selected_directory_member_ids uuid[],
  selected_internal_user_ids uuid[]
) returns table(
  directory_generation_id uuid,
  row_kind text,
  directory_member_id uuid,
  internal_user_id uuid,
  display_name text,
  status text,
  encrypted_provider_id bytea,
  encryption_key_version integer,
  real_name_ciphertext bytea,
  real_name_nonce bytea,
  real_name_encryption_key_version integer,
  departments text[],
  requested_id uuid,
  issue_reason text,
  next_cursor uuid,
  department_id uuid,
  parent_department_id uuid,
  department_name text
)
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or selected_operation not in ('search','resolve','departments')
     or selected_query is null
     or length(selected_query)>256
     or selected_include_descendants is null
     or selected_limit not between 1 and 200
     or cardinality(coalesce(selected_department_ids,array[]::uuid[]))>200
     or cardinality(coalesce(selected_directory_member_ids,array[]::uuid[]))>200
     or cardinality(coalesce(selected_internal_user_ids,array[]::uuid[]))>200
     or cardinality(coalesce(selected_directory_member_ids,array[]::uuid[]))
        + cardinality(coalesce(selected_internal_user_ids,array[]::uuid[]))>200
  then
    raise check_violation using message='Office recipient directory input invalid';
  end if;

  if selected_operation<>'departments' then
    return query
    select legacy.*
    from platform_control.read_office_recipient_directory_v53(
      selected_operation,
      selected_query,
      selected_department_ids,
      selected_include_descendants,
      selected_limit,
      selected_cursor,
      selected_directory_member_ids,
      selected_internal_user_ids
    ) legacy;
    return;
  end if;

  return query
  with active_generation as (
    select generation.generation_id
    from platform_control.directory_state state
    join platform_control.directory_generations generation
      on generation.generation_id=state.active_generation_id
     and generation.status='complete'
    where state.singleton
  ), tree as (
    select generation.generation_id,department.department_key,
      department.parent_department_key,department.display_name
    from active_generation generation
    join platform_control.directory_departments department
      on department.generation_id=generation.generation_id
  )
  select ordered.*
  from (
    select tree.generation_id,'department'::text,null::uuid,null::uuid,
      null::text,null::text,null::bytea,null::integer,null::bytea,null::bytea,
      null::integer,null::text[],null::uuid,null::text,null::uuid,
      tree.department_key,tree.parent_department_key,tree.display_name
    from tree
    union all
    select generation.generation_id,'metadata'::text,null::uuid,null::uuid,
      null::text,null::text,null::bytea,null::integer,null::bytea,null::bytea,
      null::integer,null::text[],null::uuid,null::text,null::uuid,null::uuid,
      null::uuid,null::text
    from active_generation generation
    where not exists(select 1 from tree)
  ) as ordered(
    selected_generation_id,selected_row_kind,selected_member_id,
    selected_internal_user_id,selected_display_name,selected_status,
    selected_encrypted_provider_id,selected_encryption_key_version,
    selected_real_name_ciphertext,selected_real_name_nonce,
    selected_real_name_encryption_key_version,selected_departments,
    selected_requested_id,selected_issue_reason,selected_next_cursor,
    department_id,parent_department_id,department_name
  )
  order by ordered.department_name nulls last,ordered.department_id nulls last;
end
$function$;

revoke all on function platform_control.read_office_recipient_directory_v54(
  text,text,uuid[],boolean,integer,uuid,uuid[],uuid[]
) from public;

do $migration$
declare
  selected_app name;
  role_name name;
begin
  if current_database()='agent_platform_control'
     and current_user='platform_control_owner'
  then selected_app := 'platform_control_app';
  elsif current_database()='agent_platform_control_preview'
     and current_user='platform_control_owner_preview'
  then selected_app := 'platform_control_app_preview';
  else
    raise insufficient_privilege using
      message='Office recipient migration owner/environment mismatch';
  end if;
  foreach role_name in array array[
    'platform_control_migrator','platform_control_app',
    'platform_directory_worker','platform_stream_ingest',
    'platform_audit_append','platform_control_maintenance',
    'platform_brain_worker','platform_control_migrator_preview',
    'platform_control_app_preview','platform_directory_worker_preview',
    'platform_stream_ingest_preview','platform_audit_append_preview',
    'platform_control_maintenance_preview','platform_brain_worker_preview'
  ] loop
    execute format(
      'revoke all on function '
      'platform_control.read_office_recipient_directory_v54('
      'text,text,uuid[],boolean,integer,uuid,uuid[],uuid[]) from %I',
      role_name
    );
  end loop;
  execute format(
    'grant execute on function '
    'platform_control.read_office_recipient_directory_v54('
    'text,text,uuid[],boolean,integer,uuid,uuid[],uuid[]) to %I',
    selected_app
  );
end
$migration$;
