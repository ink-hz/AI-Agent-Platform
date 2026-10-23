-- Durable, access-controlled Office statistics. Raw history retention is unchanged.
create table platform_control.office_operations_state (
 singleton boolean primary key default true check(singleton),
 actor_salt text not null default (gen_random_uuid()::text || gen_random_uuid()::text),
 tracking_started_at timestamptz not null default clock_timestamp(),
 backfilled_at timestamptz
);
insert into platform_control.office_operations_state(singleton) values(true);
create table platform_control.office_operations_catalog (
 page_key text primary key, module_key text not null, label text not null,
 scope text not null check(scope in ('employee','admin')),
 metric text not null check(metric in ('page_view','entry_click')),
 historical_mixed boolean not null default false,
 coverage_start date not null
);
create table platform_control.office_operations_daily (
 day date not null, actor_key text not null, department text not null check(char_length(department) between 1 and 160),
 page_key text not null references platform_control.office_operations_catalog(page_key),
 source text not null check(source in ('legacy','live')),
 events bigint not null check(events>0),
 primary key(day,actor_key,department,page_key,source)
);
create index office_operations_daily_page_day on platform_control.office_operations_daily(page_key,day);
create table platform_control.office_operations_exclusions(actor_key text primary key);
create table platform_control.office_operations_receipts (
 event_id uuid primary key, actor_key text not null, page_key text not null,
 occurred_at timestamptz not null default clock_timestamp()
);
create index office_operations_receipts_rate on platform_control.office_operations_receipts(actor_key,occurred_at);

create function platform_control.office_actor_key_v124(actor uuid) returns text
language sql stable security definer set search_path=pg_catalog,platform_control as $$
 select encode(sha256(convert_to(actor_salt || ':' || actor::text,'UTF8')),'hex')
 from platform_control.office_operations_state where singleton
$$;

insert into platform_control.office_operations_catalog(page_key,module_key,label,scope,metric,historical_mixed,coverage_start) values
 ('office.services','home','行政服务首页','employee','page_view',false,'2026-09-14'),
 ('office.chat','chat','行政小助理','employee','page_view',false,'2026-09-14'),
 ('office.management','management','管理控制台 / 历史管理页','admin','page_view',true,'2026-09-14'),
 ('office.feedback','service-feedback','投诉建议','employee','page_view',false,'2026-09-14'),
 ('office.my_feedback','service-feedback','投诉建议','employee','page_view',false,'2026-09-14'),
 ('office.feedback_admin','service-feedback','投诉建议','admin','page_view',false,'2026-09-14'),
 ('office.shuttle','shenzhen-shunde-shuttle','班车预约','employee','page_view',false,'2026-09-14'),
 ('office.shuttle_admin','shenzhen-shunde-shuttle','班车预约','admin','page_view',false,'2026-09-14'),
 ('office.lodging','shunde-shenzhen-lodging','住宿申请','employee','page_view',false,'2026-09-14'),
 ('office.lodging_admin','shunde-shenzhen-lodging','住宿申请','admin','page_view',false,'2026-09-14'),
 ('office.vehicle_registration','vehicle-registration','免费车辆报备','employee','page_view',false,'2026-09-14'),
 ('office.vehicle_registration_admin','vehicle-registration','免费车辆报备','admin','page_view',false,'2026-09-14'),
 ('office.notification_admin','notifications','行政通知','admin','page_view',false,'2026-09-14'),
 ('office.service.other','historical-unclassified','历史未细分','employee','page_view',true,'2026-09-14'),
 ('office.service_detail','historical-unclassified','历史未细分','employee','page_view',true,'2026-09-14');

insert into platform_control.office_operations_catalog values ('entry.vehicle-registration','vehicle-registration','免费车辆报备','employee','entry_click',false,(clock_timestamp() at time zone 'Asia/Shanghai')::date);
insert into platform_control.office_operations_catalog values ('office.service.vehicle_registration','vehicle-registration','免费车辆报备','employee','page_view',false,'2026-09-14'::date);
insert into platform_control.office_operations_catalog values ('entry.parking-payment','parking-payment','停车缴费','employee','entry_click',false,(clock_timestamp() at time zone 'Asia/Shanghai')::date);
insert into platform_control.office_operations_catalog values ('office.service.parking_payment','parking-payment','停车缴费','employee','page_view',false,'2026-09-14'::date);
insert into platform_control.office_operations_catalog values ('entry.property-service','property-service','物业服务','employee','entry_click',false,(clock_timestamp() at time zone 'Asia/Shanghai')::date);
insert into platform_control.office_operations_catalog values ('office.service.property_service','property-service','物业服务','employee','page_view',false,'2026-09-14'::date);
insert into platform_control.office_operations_catalog values ('entry.visitor-appointment','visitor-appointment','访客预约','employee','entry_click',false,(clock_timestamp() at time zone 'Asia/Shanghai')::date);
insert into platform_control.office_operations_catalog values ('office.service.visitor_appointment','visitor-appointment','访客预约','employee','page_view',false,'2026-09-14'::date);
insert into platform_control.office_operations_catalog values ('entry.shenzhen-shunde-shuttle','shenzhen-shunde-shuttle','班车预约','employee','entry_click',false,(clock_timestamp() at time zone 'Asia/Shanghai')::date);
insert into platform_control.office_operations_catalog values ('office.service.shuttle','shenzhen-shunde-shuttle','班车预约','employee','page_view',false,'2026-09-14'::date);
insert into platform_control.office_operations_catalog values ('entry.shunde-shenzhen-lodging','shunde-shenzhen-lodging','住宿申请','employee','entry_click',false,(clock_timestamp() at time zone 'Asia/Shanghai')::date);
insert into platform_control.office_operations_catalog values ('office.service.lodging','shunde-shenzhen-lodging','住宿申请','employee','page_view',false,'2026-09-14'::date);
insert into platform_control.office_operations_catalog values ('entry.smart-meeting-room','smart-meeting-room','会议室预约','employee','entry_click',false,(clock_timestamp() at time zone 'Asia/Shanghai')::date);
insert into platform_control.office_operations_catalog values ('office.service.meeting_room','smart-meeting-room','会议室预约','employee','page_view',false,'2026-09-14'::date);
insert into platform_control.office_operations_catalog values ('entry.meetings','meetings','会务服务','employee','entry_click',false,(clock_timestamp() at time zone 'Asia/Shanghai')::date);
insert into platform_control.office_operations_catalog values ('office.service.meetings','meetings','会务服务','employee','page_view',false,(clock_timestamp() at time zone 'Asia/Shanghai')::date);
insert into platform_control.office_operations_catalog values ('entry.workspaces','workspaces','工位管理','employee','entry_click',false,(clock_timestamp() at time zone 'Asia/Shanghai')::date);
insert into platform_control.office_operations_catalog values ('office.service.workspaces','workspaces','工位管理','employee','page_view',false,(clock_timestamp() at time zone 'Asia/Shanghai')::date);
insert into platform_control.office_operations_catalog values ('entry.service-feedback','service-feedback','投诉建议','employee','entry_click',false,(clock_timestamp() at time zone 'Asia/Shanghai')::date);
insert into platform_control.office_operations_catalog values ('office.service.feedback','service-feedback','投诉建议','employee','page_view',false,(clock_timestamp() at time zone 'Asia/Shanghai')::date);
insert into platform_control.office_operations_catalog values ('entry.floor-stations','floor-stations','奥比空间站','employee','entry_click',false,(clock_timestamp() at time zone 'Asia/Shanghai')::date);
insert into platform_control.office_operations_catalog values ('office.service.floor_stations','floor-stations','奥比空间站','employee','page_view',false,(clock_timestamp() at time zone 'Asia/Shanghai')::date);
insert into platform_control.office_operations_catalog values ('office.workspaces','workspaces','工位管理','employee','page_view',false,(clock_timestamp() at time zone 'Asia/Shanghai')::date);
insert into platform_control.office_operations_catalog values ('office.workspace_admin','workspaces','工位管理','admin','page_view',false,(clock_timestamp() at time zone 'Asia/Shanghai')::date);
insert into platform_control.office_operations_catalog values ('office.meetings','meetings','会务服务','employee','page_view',false,(clock_timestamp() at time zone 'Asia/Shanghai')::date);
insert into platform_control.office_operations_catalog values ('office.meeting_admin','meetings','会务服务','admin','page_view',false,(clock_timestamp() at time zone 'Asia/Shanghai')::date);
insert into platform_control.office_operations_catalog values ('office.floor_stations','floor-stations','奥比空间站','employee','page_view',false,(clock_timestamp() at time zone 'Asia/Shanghai')::date);
insert into platform_control.office_operations_catalog values ('office.invitation_admin','invitations','邀请函管理','admin','page_view',false,(clock_timestamp() at time zone 'Asia/Shanghai')::date);
insert into platform_control.office_operations_catalog values ('office.operations','operations','运营看板','admin','page_view',false,(clock_timestamp() at time zone 'Asia/Shanghai')::date);

insert into platform_control.access_page_catalog(workspace_key,page_key,display_name,allows_agent_id,module_display_name)
 select 'office',page_key,label,false,label from platform_control.office_operations_catalog where metric='page_view'
 on conflict(page_key) do update set display_name=excluded.display_name,module_display_name=excluded.module_display_name;

create function platform_control.add_office_operations_fact_v124(event_id uuid,actor uuid,page text,department text,occurred timestamptz)
returns void language plpgsql security definer set search_path=pg_catalog,platform_control as $$
declare actor_token text:=platform_control.office_actor_key_v124(actor);
begin
 insert into platform_control.office_operations_receipts values(event_id,actor_token,page,occurred);
 insert into platform_control.office_operations_daily values((occurred at time zone 'Asia/Shanghai')::date,actor_token,department,page,'live',1)
 on conflict on constraint office_operations_daily_pkey do update set events=office_operations_daily.events+1;
end $$;

create function platform_control.append_office_page_view_v124(event_id uuid,actor uuid,session uuid,page text,department text)
returns table(outcome text,retry_after_seconds integer)
language plpgsql security definer set search_path=pg_catalog,platform_control as $$
declare result record; occurred timestamptz;
begin
 if session_user not in ('platform_control_app','platform_control_app_preview') then raise insufficient_privilege; end if;
 if department is null or char_length(department) not between 1 and 160 then raise check_violation; end if;
 select * into result from platform_control.append_page_view_v65(event_id,actor,session,'office',page,null);
 if result.outcome='inserted' then
  select occurred_at into occurred from platform_control.user_access_events where access_event_id=event_id;
  perform platform_control.add_office_operations_fact_v124(event_id,actor,page,department,occurred);
 end if;
 return query select result.outcome,result.retry_after_seconds;
end $$;

create function platform_control.append_office_click_v124(event_id uuid,actor uuid,session uuid,module text,department text)
returns table(outcome text,retry_after_seconds integer)
language plpgsql security definer set search_path=pg_catalog,platform_control as $$
declare page text:='entry.' || module; actor_token text:=platform_control.office_actor_key_v124(actor); stored record; now_at timestamptz:=clock_timestamp();
begin
 if session_user not in ('platform_control_app','platform_control_app_preview') then raise insufficient_privilege; end if;
 if event_id is null or department is null or char_length(department) not between 1 and 160
 or not exists(select 1 from platform_control.office_operations_catalog c where c.page_key=page and c.metric='entry_click') then raise check_violation; end if;
 perform 1 from platform_control.web_sessions s join platform_control.internal_users u using(internal_user_id)
 where s.session_id=session and s.internal_user_id=actor and s.revoked_at is null and s.idle_expires_at>now_at and s.absolute_expires_at>now_at and u.status='active' and u.locally_invalidated_at is null;
 if not found then raise insufficient_privilege; end if;
 perform pg_advisory_xact_lock(hashtextextended(actor_token,124));
 select * into stored from platform_control.office_operations_receipts r where r.event_id=append_office_click_v124.event_id;
 if found then
  if stored.actor_key<>actor_token or stored.page_key<>page then raise check_violation; end if;
  return query select 'duplicate'::text,null::integer; return;
 end if;
 if (select count(*) from platform_control.office_operations_receipts r where r.actor_key=actor_token and r.page_key like 'entry.%' and r.occurred_at>now_at-interval '60 seconds')>=120 then
  return query select 'rate_limited'::text,60; return;
 end if;
 perform platform_control.add_office_operations_fact_v124(event_id,actor,page,department,now_at);
 return query select 'inserted'::text,null::integer;
end $$;

create function platform_control.read_office_operations_v124(from_day date,to_day date,grain text,metric text,scope text,department text,module text)
returns jsonb language plpgsql security definer set search_path=pg_catalog,platform_control as $$
declare answer jsonb;
begin
 if session_user not in ('platform_control_app','platform_control_app_preview') then raise insufficient_privilege; end if;
 if from_day is null or to_day is null or from_day<'2026-09-14' or to_day<from_day or to_day-from_day>3660
 or grain not in ('day','month') or metric not in ('page_view','entry_click') or scope not in ('employee','admin','all')
 or char_length(department)>160 or char_length(module)>96 then raise check_violation; end if;
 if not exists(select 1 from platform_control.office_operations_state where backfilled_at is not null)
 or not exists(select 1 from platform_control.office_operations_exclusions) then raise object_not_in_prerequisite_state; end if;
 with catalog as materialized (
  select c.* from platform_control.office_operations_catalog c
  where c.metric=read_office_operations_v124.metric and (read_office_operations_v124.scope='all' or c.scope=read_office_operations_v124.scope)
 ), base as materialized (
  select d.*,c.module_key,c.label,c.historical_mixed from platform_control.office_operations_daily d join catalog c using(page_key)
  where d.day between from_day and to_day and not exists(select 1 from platform_control.office_operations_exclusions x where x.actor_key=d.actor_key)
 ), chosen as materialized (
  select b.*,case when grain='month' then to_char(b.day,'YYYY-MM') else to_char(b.day,'YYYY-MM-DD') end period from base b
  where (read_office_operations_v124.department is null or b.department=read_office_operations_v124.department) and (module is null or b.module_key=module)
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

-- Restricted maintenance input includes only encrypted primary departments, no names.
create function platform_control.office_backfill_profiles_v124()
returns table(internal_user_id uuid,generation_id uuid,member_key uuid,primary_department_ciphertext bytea,primary_department_nonce bytea,primary_department_encryption_key_version integer,departments text[])
language plpgsql security definer set search_path=pg_catalog,platform_control as $$
begin
 if session_user not in ('platform_control_maintenance','platform_control_maintenance_preview') then raise insufficient_privilege; end if;
 return query select u.internal_user_id,m.generation_id,m.member_key,m.primary_department_ciphertext,m.primary_department_nonce,m.primary_department_encryption_key_version,
  array(select distinct d.display_name from platform_control.member_departments md join platform_control.directory_departments d using(generation_id,department_key) where md.generation_id=m.generation_id and md.member_key=m.member_key order by d.display_name)
 from (select distinct e.internal_user_id from platform_control.user_access_events e where e.workspace_key='office' and e.occurred_at>='2026-09-14 00:00:00+08') u
 left join platform_control.directory_state s on s.singleton
 left join platform_control.directory_members m on m.generation_id=s.active_generation_id and m.internal_user_id=u.internal_user_id and m.status='active';
end $$;

create function platform_control.backfill_office_operations_v124(department_by_actor jsonb,excluded_actor uuid)
returns bigint language plpgsql security definer set search_path=pg_catalog,platform_control as $$
declare inserted bigint;
begin
 if session_user not in ('platform_control_maintenance','platform_control_maintenance_preview') then raise insufficient_privilege; end if;
 if jsonb_typeof(department_by_actor)<>'object' or excluded_actor is null or not exists(select 1 from platform_control.internal_users where internal_user_id=excluded_actor and status='active') then raise check_violation; end if;
 perform 1 from platform_control.office_operations_state for update;
 insert into platform_control.office_operations_exclusions values(platform_control.office_actor_key_v124(excluded_actor)) on conflict do nothing;
 if exists(select 1 from platform_control.office_operations_state where backfilled_at is not null) then return 0; end if;
 if exists(select 1 from platform_control.user_access_events e where e.workspace_key='office' and e.occurred_at>='2026-09-14 00:00:00+08' and not exists(select 1 from platform_control.office_operations_catalog c where c.page_key=e.page_key)) then raise check_violation using message='unmapped Office history'; end if;
 insert into platform_control.office_operations_daily(day,actor_key,department,page_key,source,events)
 select (e.occurred_at at time zone 'Asia/Shanghai')::date,platform_control.office_actor_key_v124(e.internal_user_id),
  coalesce(nullif(trim(department_by_actor->>e.internal_user_id::text),''),'未归属部门'),e.page_key,'legacy',count(*)
 from platform_control.user_access_events e
 where e.workspace_key='office' and e.event_kind='page_view' and e.occurred_at>='2026-09-14 00:00:00+08'
 and not exists(select 1 from platform_control.office_operations_receipts r where r.event_id=e.access_event_id)
 group by 1,2,3,4;
 get diagnostics inserted=row_count;
 update platform_control.office_operations_state set backfilled_at=clock_timestamp();
 return inserted;
end $$;

create function platform_control.retain_office_operations_receipts_v124() returns bigint
language plpgsql security definer set search_path=pg_catalog,platform_control as $$
declare removed bigint;
begin
 if session_user not in ('platform_control_maintenance','platform_control_maintenance_preview') then raise insufficient_privilege; end if;
 delete from platform_control.office_operations_receipts where occurred_at<clock_timestamp()-interval '90 days';
 get diagnostics removed=row_count; return removed;
end $$;

revoke all on platform_control.office_operations_state,platform_control.office_operations_catalog,platform_control.office_operations_daily,platform_control.office_operations_receipts,platform_control.office_operations_exclusions from public;
do $$
declare f record; app_role name; maintenance_role name;
begin
 if current_database()='agent_platform_control' and current_user='platform_control_owner' then app_role:='platform_control_app'; maintenance_role:='platform_control_maintenance';
 elsif current_database()='agent_platform_control_preview' and current_user='platform_control_owner_preview' then app_role:='platform_control_app_preview'; maintenance_role:='platform_control_maintenance_preview';
 else raise insufficient_privilege; end if;
 for f in select p.oid::regprocedure signature,p.proname from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='platform_control' and p.proname like '%office%v124' loop
  execute format('revoke all on function %s from public',f.signature);
  if f.proname in ('append_office_page_view_v124','append_office_click_v124','read_office_operations_v124') then
   execute format('grant execute on function %s to %I',f.signature,app_role);
  elsif f.proname in ('office_backfill_profiles_v124','backfill_office_operations_v124','retain_office_operations_receipts_v124') then
   execute format('grant execute on function %s to %I',f.signature,maintenance_role);
  end if;
 end loop;
end $$;

-- Existing maintenance workers retain receipt idempotency for the same 90 days.
create or replace function platform_control.retain_user_access_events_v65(selected_cutoff timestamptz)
returns bigint language plpgsql security definer set search_path=pg_catalog,platform_control as $$
declare deleted_count bigint; effective_cutoff timestamptz;
begin
 if session_user not in ('platform_control_maintenance','platform_control_maintenance_preview') or selected_cutoff is null then raise insufficient_privilege; end if;
 effective_cutoff:=least(selected_cutoff,clock_timestamp()-interval '90 days');
 delete from platform_control.user_access_events where occurred_at<effective_cutoff;
 get diagnostics deleted_count=row_count;
 delete from platform_control.office_operations_receipts where occurred_at<effective_cutoff;
 return deleted_count;
end $$;
