create function platform_control.read_office_recipient_directory_v53(
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

  if selected_operation='search' then
    return query
    with active_generation as (
      select generation.generation_id
      from platform_control.directory_state state
      join platform_control.directory_generations generation
        on generation.generation_id=state.active_generation_id
       and generation.status='complete'
      where state.singleton
    ), matching as (
      select generation.generation_id,
        member.member_key,
        member.internal_user_id as selected_internal_user_id,
        member.display_name as selected_display_name,
        member.status as selected_status,
        member.encrypted_provider_id as selected_encrypted_provider_id,
        member.encryption_key_version as selected_encryption_key_version,
        member.real_name_ciphertext as selected_real_name_ciphertext,
        member.real_name_nonce as selected_real_name_nonce,
        member.real_name_encryption_key_version
          as selected_real_name_encryption_key_version,
        coalesce(member_departments.names,array[]::text[]) as department_names,
        count(*) over () as matching_count
      from active_generation generation
      join platform_control.directory_members member
        on member.generation_id=generation.generation_id
      left join lateral (
        select array_agg(distinct department.display_name
                         order by department.display_name) as names
        from platform_control.member_departments membership
        join platform_control.directory_departments department
          on department.generation_id=membership.generation_id
         and department.department_key=membership.department_key
        where membership.generation_id=member.generation_id
          and membership.member_key=member.member_key
      ) member_departments on true
      where member.status='active'
        and (selected_cursor is null or member.member_key>selected_cursor)
        and (
          selected_query=''
          or strpos(lower(member.display_name),lower(selected_query))>0
        )
        and (
          cardinality(coalesce(selected_department_ids,array[]::uuid[]))=0
          or exists (
            select 1
            from platform_control.member_departments membership_filter
            where membership_filter.generation_id=member.generation_id
              and membership_filter.member_key=member.member_key
              and (
                (
                  not selected_include_descendants
                  and membership_filter.department_key
                    =any(selected_department_ids)
                )
                or (
                  selected_include_descendants
                  and exists (
                    select 1
                    from platform_control.department_closure closure
                    where closure.generation_id=member.generation_id
                      and closure.ancestor_department_key
                        =any(selected_department_ids)
                      and closure.descendant_department_key
                        =membership_filter.department_key
                  )
                )
              )
          )
        )
      order by member.member_key
    ), page as (
      select * from matching order by member_key limit selected_limit
    )
    select page.generation_id,'member'::text,page.member_key,
      page.selected_internal_user_id,page.selected_display_name,
      page.selected_status,page.selected_encrypted_provider_id,
      page.selected_encryption_key_version,page.selected_real_name_ciphertext,
      page.selected_real_name_nonce,page.selected_real_name_encryption_key_version,
      page.department_names,null::uuid,null::text,
      case when page.matching_count>selected_limit then (
        select final_page.member_key from page final_page
        order by final_page.member_key desc limit 1
      ) else null::uuid end,
      null::uuid,null::uuid,null::text
    from page
    union all
    select generation.generation_id,'metadata'::text,null::uuid,null::uuid,
      null::text,null::text,null::bytea,null::integer,null::bytea,null::bytea,
      null::integer,null::text[],null::uuid,null::text,null::uuid,null::uuid,
      null::uuid,null::text
    from active_generation generation
    where not exists(select 1 from page);
    return;
  end if;

  if selected_operation='resolve' then
    if cardinality(coalesce(selected_directory_member_ids,array[]::uuid[]))
       + cardinality(coalesce(selected_internal_user_ids,array[]::uuid[]))=0
    then
      raise check_violation using message='Office recipient identifiers required';
    end if;
    return query
    with active_generation as (
      select generation.generation_id
      from platform_control.directory_state state
      join platform_control.directory_generations generation
        on generation.generation_id=state.active_generation_id
       and generation.status='complete'
      where state.singleton
    ), requests(requested_kind,selected_requested_id) as (
      select 'member'::text,unnest(
        coalesce(selected_directory_member_ids,array[]::uuid[])
      )
      union
      select 'internal'::text,unnest(
        coalesce(selected_internal_user_ids,array[]::uuid[])
      )
    )
    select generation.generation_id,
      case when member.member_key is not null and member.status='active'
        then 'member'::text else 'issue'::text end,
      case when member.status='active' then member.member_key else null::uuid end,
      case when member.status='active' then member.internal_user_id else null::uuid end,
      case when member.status='active' then member.display_name else null::text end,
      case when member.status='active' then member.status else null::text end,
      case when member.status='active' then member.encrypted_provider_id
        else null::bytea end,
      case when member.status='active' then member.encryption_key_version
        else null::integer end,
      case when member.status='active' then member.real_name_ciphertext
        else null::bytea end,
      case when member.status='active' then member.real_name_nonce
        else null::bytea end,
      case when member.status='active' then member.real_name_encryption_key_version
        else null::integer end,
      case when member.status='active' then
        coalesce(member_departments.names,array[]::text[])
        else null::text[] end,
      requests.selected_requested_id,
      case
        when member.member_key is null then 'not_found'::text
        when member.status='inactive' then 'inactive'::text
        when member.status='disabled' then 'disabled'::text
        else null::text
      end,
      null::uuid,null::uuid,null::uuid,null::text
    from requests
    left join active_generation generation on true
    left join lateral (
      select candidate.*
      from platform_control.directory_members candidate
      where candidate.generation_id=generation.generation_id
        and (
          (requests.requested_kind='member'
            and candidate.member_key=requests.selected_requested_id)
          or (requests.requested_kind='internal'
            and candidate.internal_user_id=requests.selected_requested_id)
        )
      order by candidate.member_key
      limit 1
    ) member on true
    left join lateral (
      select array_agg(distinct department.display_name
                       order by department.display_name) as names
      from platform_control.member_departments membership
      join platform_control.directory_departments department
        on department.generation_id=membership.generation_id
       and department.department_key=membership.department_key
      where membership.generation_id=member.generation_id
        and membership.member_key=member.member_key
    ) member_departments on true;
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
  order by department_name nulls last,department_id nulls last;
end
$function$;

revoke all on function platform_control.read_office_recipient_directory_v53(
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
      'platform_control.read_office_recipient_directory_v53('
      'text,text,uuid[],boolean,integer,uuid,uuid[],uuid[]) from %I',
      role_name
    );
  end loop;
  execute format(
    'grant execute on function '
    'platform_control.read_office_recipient_directory_v53('
    'text,text,uuid[],boolean,integer,uuid,uuid[],uuid[]) to %I',
    selected_app
  );
end
$migration$;
