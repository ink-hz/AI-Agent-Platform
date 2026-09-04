alter table platform_control.access_page_catalog
  add column module_display_name text;

update platform_control.access_page_catalog
set module_display_name = case
  when page_key in ('platform.brain','platform.conversations','platform.conversation') then 'Agent 大脑'
  when page_key in ('platform.agent_directory','platform.missions','platform.mission_detail') then '专业 Agent'
  when page_key in ('platform.account') then '企业账号'
  when page_key in ('platform.ai_notes','platform.ai_note') then 'AI 工程笔记'
  when workspace_key='hr' then '招聘协作'
  when workspace_key='marketing' then 'Marketing Agent'
  when page_key='office.chat' then '行政问答'
  when page_key in ('office.services','office.service_detail') then '行政服务'
  when page_key in ('office.feedback','office.my_feedback','office.feedback_admin') then '反馈管理'
  when page_key in ('office.shuttle','office.shuttle_admin') then '班车服务'
  when page_key in ('office.lodging','office.lodging_admin') then '住宿服务'
  when page_key in ('office.vehicle_registration','office.vehicle_registration_admin') then '车辆服务'
  when workspace_key='office' then '行政管理'
  when page_key in ('fae.workspace','fae.conversation') then 'FAE Agent'
  when workspace_key='fae' then 'FAE 管理'
  when page_key in ('voc.workspace','voc.records','voc.record_detail') then 'VOC Agent'
  when workspace_key='voc' then 'VOC 管理'
  when page_key in ('admin.overview','admin.agents','admin.agent_detail','admin.agent_runtime') then 'Agent 运营'
  when page_key in ('admin.sessions','admin.session_detail','admin.review','admin.activity') then '数据与复审'
  when workspace_key='admin' then '平台治理'
  else '其他'
end;

alter table platform_control.access_page_catalog
  alter column module_display_name set not null,
  add constraint access_page_catalog_module_name_bound
    check (char_length(module_display_name) between 1 and 80);

insert into platform_control.access_page_catalog(
  workspace_key,page_key,display_name,allows_agent_id,module_display_name
) values
  ('hr','hr.index','岗位工作台',false,'招聘协作'),
  ('hr','hr.free_chat','HR 自由对话',false,'招聘协作'),
  ('hr','hr.position_detail','岗位详情',false,'招聘协作'),
  ('hr','hr.position_conversation','岗位对话',false,'招聘协作'),
  ('office','office.service.vehicle_registration','车辆登记服务详情',false,'行政服务'),
  ('office','office.service.parking_payment','停车缴费服务详情',false,'行政服务'),
  ('office','office.service.property_service','物业服务详情',false,'行政服务'),
  ('office','office.service.visitor_appointment','访客预约服务详情',false,'行政服务'),
  ('office','office.service.shuttle','班车预约服务详情',false,'行政服务'),
  ('office','office.service.lodging','住宿服务详情',false,'行政服务'),
  ('office','office.service.meeting_room','会议室服务详情',false,'行政服务'),
  ('office','office.service.other','其他行政服务详情',false,'行政服务');

create function platform_control.read_user_access_events_v67(
  selected_requester_id uuid,
  selected_session_id uuid,
  selected_date_from timestamptz,
  selected_date_to timestamptz,
  selected_display_name text,
  selected_workspace_key text,
  selected_event_kind text,
  selected_limit integer,
  selected_offset integer
) returns table(
  access_event_id uuid,
  display_name text,
  departments text[],
  event_kind text,
  login_kind text,
  workspace_key text,
  page_key text,
  module_display_name text,
  page_display_name text,
  agent_id text,
  occurred_at timestamptz
)
language plpgsql
stable
security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or selected_requester_id is null
     or selected_session_id is null
     or selected_date_from is null or selected_date_to is null
     or selected_date_from >= selected_date_to
     or selected_date_to-selected_date_from > interval '90 days'
     or selected_limit not between 1 and 101
     or selected_offset not between 0 and 100000
     or (selected_display_name is not null and (
       char_length(selected_display_name) not between 1 and 128
     ))
     or (selected_workspace_key is not null and not exists (
       select 1 from platform_control.access_page_catalog catalog
       where catalog.workspace_key=selected_workspace_key
     ))
     or (selected_event_kind is not null and selected_event_kind not in (
       'login_succeeded','page_view'
     ))
  then
    raise check_violation using message='access history query invalid';
  end if;

  perform 1 from platform_control.web_sessions session
  join platform_control.internal_users users
    on users.internal_user_id=session.internal_user_id
  where session.session_id=selected_session_id
    and session.internal_user_id=selected_requester_id
    and session.revoked_at is null
    and session.idle_expires_at > clock_timestamp()
    and session.absolute_expires_at > clock_timestamp()
    and users.status='active'
    and users.locally_invalidated_at is null
    and users.role='platform_owner';
  if not found then
    raise insufficient_privilege using message='access history owner required';
  end if;

  return query
  with active_generation as (
    select generation.generation_id
    from platform_control.directory_state state
    join platform_control.directory_generations generation
      on generation.generation_id=state.active_generation_id
     and generation.status='complete'
    where state.singleton
  ), current_departments as (
    select member.internal_user_id,
      array_agg(distinct department.display_name order by department.display_name) as names
    from active_generation generation
    join platform_control.directory_members member
      on member.generation_id=generation.generation_id
     and member.status='active'
    join platform_control.member_departments membership
      on membership.generation_id=member.generation_id
     and membership.member_key=member.member_key
    join platform_control.directory_departments department
      on department.generation_id=membership.generation_id
     and department.department_key=membership.department_key
    where member.internal_user_id is not null
    group by member.internal_user_id
  )
  select event.access_event_id,users.display_name,
    coalesce(current_departments.names,array[]::text[]),event.event_kind,
    event.login_kind,event.workspace_key,event.page_key,
    catalog.module_display_name,catalog.display_name,event.agent_id,
    event.occurred_at
  from platform_control.user_access_events event
  join platform_control.internal_users users
    on users.internal_user_id=event.internal_user_id
  left join platform_control.access_page_catalog catalog
    on catalog.workspace_key=event.workspace_key
   and catalog.page_key=event.page_key
  left join current_departments
    on current_departments.internal_user_id=event.internal_user_id
  where event.occurred_at >= selected_date_from
    and event.occurred_at < selected_date_to
    and (selected_display_name is null or users.display_name=selected_display_name)
    and (selected_workspace_key is null or event.workspace_key=selected_workspace_key)
    and (selected_event_kind is null or event.event_kind=selected_event_kind)
  order by event.occurred_at desc,event.access_event_id desc
  limit selected_limit offset selected_offset;
end
$function$;

create function platform_control.read_access_subjects_v67(
  selected_requester_id uuid,
  selected_session_id uuid,
  selected_date_from timestamptz,
  selected_date_to timestamptz,
  selected_display_name text,
  selected_workspace_key text,
  selected_event_kind text,
  selected_limit integer,
  selected_offset integer
) returns table(
  display_name text,
  departments text[],
  event_count bigint,
  latest_occurred_at timestamptz,
  latest_event_kind text,
  latest_workspace_key text,
  latest_module_display_name text,
  latest_page_display_name text,
  latest_agent_id text
)
language plpgsql
stable
security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or selected_requester_id is null
     or selected_session_id is null
     or selected_date_from is null or selected_date_to is null
     or selected_date_from >= selected_date_to
     or selected_date_to-selected_date_from > interval '90 days'
     or selected_limit not between 1 and 101
     or selected_offset not between 0 and 100000
     or (selected_display_name is not null and (
       char_length(selected_display_name) not between 1 and 128
     ))
     or (selected_workspace_key is not null and not exists (
       select 1 from platform_control.access_page_catalog catalog
       where catalog.workspace_key=selected_workspace_key
     ))
     or (selected_event_kind is not null and selected_event_kind not in (
       'login_succeeded','page_view'
     ))
  then
    raise check_violation using message='access subject query invalid';
  end if;

  perform 1 from platform_control.web_sessions session
  join platform_control.internal_users users
    on users.internal_user_id=session.internal_user_id
  where session.session_id=selected_session_id
    and session.internal_user_id=selected_requester_id
    and session.revoked_at is null
    and session.idle_expires_at > clock_timestamp()
    and session.absolute_expires_at > clock_timestamp()
    and users.status='active'
    and users.locally_invalidated_at is null
    and users.role='platform_owner';
  if not found then
    raise insufficient_privilege using message='access history owner required';
  end if;

  if exists (
    select 1
    from platform_control.user_access_events event
    join platform_control.internal_users users
      on users.internal_user_id=event.internal_user_id
    where event.occurred_at >= selected_date_from
      and event.occurred_at < selected_date_to
      and (selected_display_name is null
           or strpos(lower(users.display_name),lower(selected_display_name))>0)
      and (selected_workspace_key is null or event.workspace_key=selected_workspace_key)
      and (selected_event_kind is null or event.event_kind=selected_event_kind)
    group by users.display_name
    having count(distinct event.internal_user_id)>1
  ) then
    raise check_violation using message='access history display name ambiguous';
  end if;

  return query
  with active_generation as (
    select generation.generation_id
    from platform_control.directory_state state
    join platform_control.directory_generations generation
      on generation.generation_id=state.active_generation_id
     and generation.status='complete'
    where state.singleton
  ), current_departments as (
    select member.internal_user_id,
      array_agg(distinct department.display_name order by department.display_name) as names
    from active_generation generation
    join platform_control.directory_members member
      on member.generation_id=generation.generation_id
     and member.status='active'
    join platform_control.member_departments membership
      on membership.generation_id=member.generation_id
     and membership.member_key=member.member_key
    join platform_control.directory_departments department
      on department.generation_id=membership.generation_id
     and department.department_key=membership.department_key
    where member.internal_user_id is not null
    group by member.internal_user_id
  ), filtered as (
    select event.internal_user_id,users.display_name,event.event_kind,
      event.workspace_key,catalog.module_display_name,
      catalog.display_name as page_display_name,event.agent_id,
      event.occurred_at,event.access_event_id,
      count(*) over (partition by event.internal_user_id) as selected_event_count,
      row_number() over (
        partition by event.internal_user_id
        order by event.occurred_at desc,event.access_event_id desc
      ) as recency
    from platform_control.user_access_events event
    join platform_control.internal_users users
      on users.internal_user_id=event.internal_user_id
    left join platform_control.access_page_catalog catalog
      on catalog.workspace_key=event.workspace_key
     and catalog.page_key=event.page_key
    where event.occurred_at >= selected_date_from
      and event.occurred_at < selected_date_to
      and (selected_display_name is null
           or strpos(lower(users.display_name),lower(selected_display_name))>0)
      and (selected_workspace_key is null or event.workspace_key=selected_workspace_key)
      and (selected_event_kind is null or event.event_kind=selected_event_kind)
  )
  select filtered.display_name,
    coalesce(current_departments.names,array[]::text[]),
    filtered.selected_event_count,filtered.occurred_at,
    filtered.event_kind,filtered.workspace_key,
    filtered.module_display_name,filtered.page_display_name,
    filtered.agent_id
  from filtered
  left join current_departments
    on current_departments.internal_user_id=filtered.internal_user_id
  where filtered.recency=1
  order by filtered.occurred_at desc,filtered.display_name
  limit selected_limit offset selected_offset;
end
$function$;

revoke all on function platform_control.read_user_access_events_v67(
  uuid,uuid,timestamptz,timestamptz,text,text,text,integer,integer
) from public;
revoke all on function platform_control.read_access_subjects_v67(
  uuid,uuid,timestamptz,timestamptz,text,text,text,integer,integer
) from public;

do $migration$
declare
  selected_app name;
  role_name name;
begin
  if current_database()='agent_platform_control'
     and current_user='platform_control_owner'
  then
    selected_app:='platform_control_app';
  elsif current_database()='agent_platform_control_preview'
        and current_user='platform_control_owner_preview'
  then
    selected_app:='platform_control_app_preview';
  else
    raise insufficient_privilege using
      message='access subject index owner/environment mismatch';
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
      'revoke all on function platform_control.read_user_access_events_v67('
      'uuid,uuid,timestamptz,timestamptz,text,text,text,integer,integer) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.read_access_subjects_v67('
      'uuid,uuid,timestamptz,timestamptz,text,text,text,integer,integer) from %I',
      role_name
    );
    execute format(
      'revoke all on platform_control.access_page_catalog from %I',role_name
    );
    execute format(
      'revoke all on platform_control.user_access_events from %I',role_name
    );
  end loop;

  execute format(
    'grant execute on function platform_control.read_user_access_events_v67('
    'uuid,uuid,timestamptz,timestamptz,text,text,text,integer,integer) to %I',
    selected_app
  );
  execute format(
    'grant execute on function platform_control.read_access_subjects_v67('
    'uuid,uuid,timestamptz,timestamptz,text,text,text,integer,integer) to %I',
    selected_app
  );
end
$migration$;
