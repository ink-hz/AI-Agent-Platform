-- Add second-level department summaries without changing stored facts or the existing report contract.
-- The directory root is level one; its children are the summary departments.
-- Repeated names are resolved only through unique current memberships, otherwise kept unmapped.
-- Every group and child count is aggregated from actor-level facts, never by summing UVs.
create or replace function platform_control.read_office_operations_v124(from_day date,to_day date,grain text,metric text,scope text,department text,module text)
returns jsonb language plpgsql security definer set search_path=pg_catalog,platform_control as $$
declare answer jsonb;
begin
 if session_user not in ('platform_control_app','platform_control_app_preview') then raise insufficient_privilege; end if;
 if from_day is null or to_day is null or from_day<'2026-09-14' or to_day<from_day or to_day-from_day>3660
 or grain not in ('day','month') or metric not in ('page_view','entry_click') or scope not in ('employee','admin','all')
 or char_length(department)>160 or char_length(module)>96 then raise check_violation; end if;
 if not exists(select 1 from platform_control.office_operations_state where backfilled_at is not null)
 or not exists(select 1 from platform_control.office_operations_exclusions) then raise object_not_in_prerequisite_state; end if;
 with recursive catalog as materialized (
  select c.* from platform_control.office_operations_catalog c
  where c.metric=read_office_operations_v124.metric and (read_office_operations_v124.scope='all' or c.scope=read_office_operations_v124.scope)
 ), base as materialized (
  select d.*,c.module_key,c.label,c.historical_mixed from platform_control.office_operations_daily d join catalog c using(page_key)
  where d.day between from_day and to_day and not exists(select 1 from platform_control.office_operations_exclusions x where x.actor_key=d.actor_key)
 ), chosen as materialized (
  select b.*,case when grain='month' then to_char(b.day,'YYYY-MM') else to_char(b.day,'YYYY-MM-DD') end period from base b
  where (read_office_operations_v124.department is null or b.department=read_office_operations_v124.department) and (module is null or b.module_key=module)
 ), org_tree as (
  select d.generation_id,d.department_key,d.display_name,null::text group_key,null::text group_label,array[d.department_key] path
  from platform_control.directory_departments d
  join platform_control.directory_state s on s.active_generation_id=d.generation_id and s.singleton
  join platform_control.directory_generations g on g.generation_id=d.generation_id and g.status='complete'
  where d.parent_department_key is null
  union all
  select d.generation_id,d.department_key,d.display_name,
    coalesce(t.group_key,'org:'||d.department_key::text),coalesce(t.group_label,d.display_name),t.path||d.department_key
  from org_tree t join platform_control.directory_departments d
    on d.generation_id=t.generation_id and d.parent_department_key=t.department_key
  where not d.department_key=any(t.path) and cardinality(t.path)<32
 ), name_groups as materialized (
  select display_name,count(distinct group_key) group_count,min(group_key) group_key,min(group_label) group_label
  from org_tree where group_key is not null group by display_name
 ), membership_groups as materialized (
  select platform_control.office_actor_key_v124(m.internal_user_id) actor_key,t.display_name,
    count(distinct t.group_key) group_count,min(t.group_key) group_key,min(t.group_label) group_label
  from org_tree t join name_groups n on n.display_name=t.display_name and n.group_count>1
  join platform_control.member_departments md on md.generation_id=t.generation_id and md.department_key=t.department_key
  join platform_control.directory_members m on m.generation_id=md.generation_id and m.member_key=md.member_key
  where m.internal_user_id is not null and m.status='active'
  group by m.internal_user_id,t.display_name
 ), classified as materialized (
  select c.*,
    case when n.group_count=1 then n.group_key when m.group_count=1 then m.group_key else 'unmapped' end group_key,
    case when n.group_count=1 then n.group_label when m.group_count=1 then m.group_label else '未匹配上级部门' end group_label
  from chosen c left join name_groups n on n.display_name=c.department
  left join membership_groups m on m.actor_key=c.actor_key and m.display_name=c.department
 ), group_totals as (
  select group_key,min(group_label) label,sum(events) events,count(distinct actor_key) users
  from classified group by group_key
 ), group_cells as (
  select group_key,module_key,sum(events) events,count(distinct actor_key) users
  from classified group by group_key,module_key
 ), child_totals as (
  select group_key,c.department,sum(events) events,count(distinct actor_key) users
  from classified c group by group_key,c.department
 ), child_cells as (
  select group_key,c.department,module_key,sum(events) events,count(distinct actor_key) users
  from classified c group by group_key,c.department,module_key
 ), child_nodes as (
  select t.group_key,t.department,t.events,jsonb_build_object('key',t.department,'label',t.department,'events',t.events,'users',t.users,
    'cells',coalesce((select jsonb_agg(jsonb_build_object('module',c.module_key,'events',c.events,'users',c.users) order by c.module_key)
      from child_cells c where c.group_key=t.group_key and c.department=t.department),'[]'::jsonb)) node
  from child_totals t
 ), group_nodes as (
  select g.group_key,g.events,jsonb_build_object('key',g.group_key,'label',g.label,'events',g.events,'users',g.users,
    'cells',coalesce((select jsonb_agg(jsonb_build_object('module',c.module_key,'events',c.events,'users',c.users) order by c.module_key)
      from group_cells c where c.group_key=g.group_key),'[]'::jsonb),
    'children',coalesce((select jsonb_agg(c.node order by c.events desc,c.department) from child_nodes c where c.group_key=g.group_key),'[]'::jsonb)) node
  from group_totals g
 ), module_totals as (
  select module_key,sum(events) events,count(distinct actor_key) users from chosen group by module_key
 ), module_catalog as (
  select module_key,min(label) label,min(coverage_start) coverage_start,bool_or(historical_mixed) historical_mixed from catalog
  where module is null or module_key=module group by module_key
 ), trend_totals as (
  select period,sum(events) events,count(distinct actor_key) users from chosen group by period
 ), periods as (
  select distinct case when grain='month' then to_char(d,'YYYY-MM') else to_char(d,'YYYY-MM-DD') end period
  from generate_series(greatest(from_day,(select min(coverage_start) from module_catalog))::timestamp,
     least(to_day,(clock_timestamp() at time zone 'Asia/Shanghai')::date)::timestamp,interval '1 day') d
 ), dept_totals as (
  select c.department,sum(events) events,count(distinct actor_key) users from chosen c group by c.department
 ), cells as (
  select c.department,c.module_key,sum(events) events,count(distinct actor_key) users from chosen c group by c.department,c.module_key
 ), exports as (
  select period,c.department,c.module_key,min(c.label) label,sum(events) events,count(distinct actor_key) users,
    case when bool_and(source='legacy') then '历史按当前通讯录回填' when bool_and(source='live') then '访问时主部门' else '包含历史回填和访问时主部门' end department_basis
  from chosen c group by period,c.department,c.module_key
 ) select jsonb_build_object(
 'department_groups',coalesce((select jsonb_agg(g.node order by (g.group_key='unmapped'),g.events desc,g.group_key) from group_nodes g),'[]'::jsonb),
 'department_hierarchy',jsonb_build_object('basis','current_directory','group_level',2,'unmapped_events',coalesce((select sum(events) from classified where group_key='unmapped'),0)),
 'summary',(select jsonb_build_object('events',coalesce(sum(events),0),'users',count(distinct actor_key),'departments',count(distinct c.department),'modules',count(distinct module_key)) from chosen c),
 'trend',coalesce((select jsonb_agg(jsonb_build_object('period',p.period,'events',coalesce(t.events,0),'users',coalesce(t.users,0)) order by p.period) from periods p left join trend_totals t using(period)),'[]'::jsonb),
 'modules',coalesce((select jsonb_agg(jsonb_build_object('key',c.module_key,'label',c.label,'events',coalesce(t.events,0),'users',coalesce(t.users,0),'share',coalesce(t.events::numeric/nullif((select sum(events) from chosen),0),0),'coverage_start',c.coverage_start,'historical_mixed',c.historical_mixed) order by coalesce(t.events,0) desc,c.module_key) from module_catalog c left join module_totals t using(module_key)),'[]'::jsonb),
 'departments',coalesce((select jsonb_agg(jsonb_build_object('key',d.department,'label',d.department,'events',d.events,'users',d.users) order by d.events desc,d.department) from dept_totals d),'[]'::jsonb),
 'matrix',coalesce((select jsonb_agg(jsonb_build_object('department',c.department,'module',c.module_key,'events',c.events,'users',c.users) order by c.department,c.module_key) from cells c),'[]'::jsonb),
 'options',jsonb_build_object('departments',coalesce((select jsonb_agg(jsonb_build_object('key',d.department,'label',d.department) order by d.department) from (select distinct b.department from base b) d),'[]'::jsonb),'modules',coalesce((select jsonb_agg(jsonb_build_object('key',c.module_key,'label',c.label) order by c.module_key) from (select module_key,min(label) label from catalog group by module_key) c),'[]'::jsonb)),
 'export_rows',coalesce((select jsonb_agg(to_jsonb(e) order by period,e.department,module_key) from exports e),'[]'::jsonb),
 'coverage',jsonb_build_object('launch_date','2026-09-14','raw_retention_days',90,'tracking_started_at',(select min(coverage_start)::timestamp at time zone 'Asia/Shanghai' from module_catalog),'historical_department_basis','current_directory','excluded_developer_accounts',(select count(*) from platform_control.office_operations_exclusions),'notes',jsonb_build_array('历史未细分记录无法还原到具体服务；新版本开始独立记录。','历史部门按回填时通讯录归属，新记录使用访问时主部门。','访问次数与服务入口点击分别统计；使用人数在所选周期内去重，不能将各部门或各天人数相加。','每日统计自上线起长期保留；原始访问明细保留 90 天。'))
 ) into answer;
 return answer;
end $$;

