-- Deliberately expose only display projections, never provider IDs or profiles.
create function platform_control.read_organization_directory_v109(
  selected_department_id uuid,
  selected_generation_id uuid,
  selected_cursor uuid,
  page_limit integer
) returns jsonb
language plpgsql
stable
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  active_id uuid;
  completed timestamptz;
  root_id uuid;
  root_count integer;
  reachable_count integer;
  result jsonb;
begin
  if page_limit is null or page_limit not between 1 and 100
     or (selected_department_id is not null and selected_generation_id is null)
  then
    return jsonb_build_object('error','invalid_input');
  end if;

  select generation.generation_id, generation.completed_at
    into active_id, completed
  from platform_control.directory_state state
  join platform_control.directory_generations generation
    on generation.generation_id=state.active_generation_id
  where state.singleton and generation.status='complete';
  if active_id is null or completed is null or completed>now() then
    return jsonb_build_object('error','directory_unavailable');
  end if;
  if selected_generation_id is not null and selected_generation_id<>active_id then
    return jsonb_build_object('error','generation_changed');
  end if;

  select count(*) into root_count
  from platform_control.directory_departments
  where generation_id=active_id and parent_department_key is null;
  if root_count<>1 then
    return jsonb_build_object('error','directory_unavailable');
  end if;
  select department_key into root_id
  from platform_control.directory_departments
  where generation_id=active_id and parent_department_key is null;

  -- A single parent per node means every valid node must be reachable from root.
  -- UNION terminates defensively; disconnected cycles and orphans fail closed.
  with recursive reachable(department_key) as (
    select root_id
    union
    select department.department_key
    from platform_control.directory_departments department
    join reachable on department.parent_department_key=reachable.department_key
    where department.generation_id=active_id
  ) select count(*) into reachable_count from reachable;
  if reachable_count<>(select count(*) from platform_control.directory_departments
                       where generation_id=active_id) then
    return jsonb_build_object('error','directory_unavailable');
  end if;

  if selected_department_id is null then
    select jsonb_build_object(
      'generation_id',active_id,'completed_at',completed,
      'freshness',case when now()-completed>=interval '24 hours' then 'hard_stale'
                       when now()-completed>=interval '8 hours' then 'warning'
                       else 'fresh' end,
      'scope','visible_directory','root_id',root_id,
      'departments',jsonb_agg(jsonb_build_object(
        'id',department_key,'parent_id',parent_department_key,'name',display_name
      ) order by display_name,department_key)
    ) into result
    from platform_control.directory_departments where generation_id=active_id;
    return result;
  end if;

  if not exists (select 1 from platform_control.directory_departments
                 where generation_id=active_id and department_key=selected_department_id) then
    return jsonb_build_object('error','department_not_found');
  end if;

  with included as materialized (
    select member.member_key,member.display_name,member.status
    from platform_control.directory_members member
    where member.generation_id=active_id and (
      selected_department_id=root_id or exists (
        select 1 from platform_control.member_departments membership
        join platform_control.department_closure closure
          on closure.generation_id=membership.generation_id
          and closure.descendant_department_key=membership.department_key
        where membership.generation_id=active_id
          and membership.member_key=member.member_key
          and closure.ancestor_department_key=selected_department_id
      )
    )
  ), page_plus_one as materialized (
    select * from included
    where selected_cursor is null or member_key>selected_cursor
    order by member_key limit page_limit+1
  ), page as materialized (
    select * from page_plus_one order by member_key limit page_limit
  )
  select jsonb_build_object(
    'generation_id',active_id,'department_id',selected_department_id,
    'direct_count',(select count(*) from platform_control.member_departments
      where generation_id=active_id and department_key=selected_department_id),
    'total_count',(select count(*) from included),
    'status_counts',jsonb_build_object(
      'active',(select count(*) from included where status='active'),
      'inactive',(select count(*) from included where status='inactive'),
      'disabled',(select count(*) from included where status='disabled')),
    'position_available',false,
    'members',coalesce((select jsonb_agg(jsonb_build_object(
      'id',page.member_key,'name',page.display_name,'status',page.status,
      'departments',coalesce((
        select jsonb_agg(jsonb_build_object('id',department.department_key,
          'name',department.display_name) order by department.display_name,department.department_key)
        from platform_control.member_departments membership
        join platform_control.directory_departments department
          on department.generation_id=membership.generation_id
          and department.department_key=membership.department_key
        where membership.generation_id=active_id and membership.member_key=page.member_key
      ),'[]'::jsonb)
    ) order by page.member_key) from page),'[]'::jsonb),
    'next_cursor',case when (select count(*) from page_plus_one)>page_limit
      then (select member_key from page order by member_key desc limit 1) else null end
  ) into result;
  return result;
end
$function$;

revoke all on function platform_control.read_organization_directory_v109(uuid,uuid,uuid,integer) from public;

do $migration$
declare
  selected_app name;
  role_name name;
begin
  if current_database()='agent_platform_control' and current_user='platform_control_owner' then
    selected_app := 'platform_control_app';
  elsif current_database()='agent_platform_control_preview' and current_user='platform_control_owner_preview' then
    selected_app := 'platform_control_app_preview';
  else
    raise insufficient_privilege using message='Organization migration owner/environment mismatch';
  end if;
  foreach role_name in array array[
    'platform_control_migrator','platform_control_app','platform_directory_worker',
    'platform_stream_ingest','platform_audit_append','platform_control_maintenance',
    'platform_brain_worker','platform_control_migrator_preview','platform_control_app_preview',
    'platform_directory_worker_preview','platform_stream_ingest_preview',
    'platform_audit_append_preview','platform_control_maintenance_preview','platform_brain_worker_preview'
  ] loop
    execute format('revoke all on function platform_control.read_organization_directory_v109(uuid,uuid,uuid,integer) from %I',role_name);
  end loop;
  execute format('grant execute on function platform_control.read_organization_directory_v109(uuid,uuid,uuid,integer) to %I',selected_app);
end
$migration$;
