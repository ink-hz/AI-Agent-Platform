create table platform_control.access_page_catalog (
  workspace_key text not null
    check (workspace_key ~ '^[a-z][a-z0-9_]{0,31}$'),
  page_key text primary key
    check (page_key ~ '^[a-z][a-z0-9_.]{0,95}$'),
  display_name text not null
    check (char_length(display_name) between 1 and 80),
  allows_agent_id boolean not null default false,
  unique (workspace_key,page_key)
);

insert into platform_control.access_page_catalog(
  workspace_key,page_key,display_name,allows_agent_id
) values
  ('platform','platform.brain','Agent 大脑',false),
  ('platform','platform.conversations','Agent 大脑会话列表',false),
  ('platform','platform.conversation','Agent 大脑会话',false),
  ('platform','platform.agent_directory','专业 Agent',false),
  ('platform','platform.missions','历史任务',false),
  ('platform','platform.mission_detail','任务详情',false),
  ('platform','platform.account','企业账号',false),
  ('platform','platform.ai_notes','AI 听记',false),
  ('platform','platform.ai_note','AI 听记详情',false),
  ('hr','hr.workspace','HR 工作台',false),
  ('hr','hr.conversation','HR 会话',false),
  ('marketing','marketing.workspace','Marketing 工作台',true),
  ('marketing','marketing.conversation','Marketing 会话',true),
  ('office','office.chat','行政问答',false),
  ('office','office.services','行政服务门户',false),
  ('office','office.management','行政管理',false),
  ('office','office.service_detail','行政服务详情',false),
  ('office','office.feedback','行政反馈',false),
  ('office','office.my_feedback','我的行政反馈',false),
  ('office','office.feedback_admin','行政反馈管理',false),
  ('office','office.shuttle','班车服务',false),
  ('office','office.shuttle_admin','班车管理',false),
  ('office','office.lodging','住宿服务',false),
  ('office','office.lodging_admin','住宿管理',false),
  ('office','office.vehicle_registration','车辆登记',false),
  ('office','office.vehicle_registration_admin','车辆登记管理',false),
  ('office','office.notification_admin','行政通知管理',false),
  ('fae','fae.workspace','FAE Agent',false),
  ('fae','fae.conversation','FAE 会话',false),
  ('fae','fae.manage.overview','FAE 工作台概览',false),
  ('fae','fae.manage.sessions','FAE Sessions',false),
  ('fae','fae.manage.session_detail','FAE Session 详情',false),
  ('fae','fae.manage.issues','FAE 反馈与修复',false),
  ('fae','fae.manage.issue_detail','FAE 问题详情',false),
  ('fae','fae.manage.reports','FAE 分析报告',false),
  ('fae','fae.manage.report_detail','FAE 报告详情',false),
  ('voc','voc.workspace','VOC 工作台',false),
  ('voc','voc.records','VOC 记录',false),
  ('voc','voc.record_detail','VOC 记录详情',false),
  ('voc','voc.manage','VOC 管理',false),
  ('voc','voc.manage.record_detail','VOC 管理详情',false),
  ('admin','admin.overview','管理中心总览',false),
  ('admin','admin.agents','Agent 管理',false),
  ('admin','admin.agent_detail','Agent 详情',false),
  ('admin','admin.agent_runtime','Agent 运行状态',false),
  ('admin','admin.sessions','Session 管理',false),
  ('admin','admin.session_detail','Session 详情',false),
  ('admin','admin.review','复审闭环',false),
  ('admin','admin.activity','运行记录',false),
  ('admin','admin.identity','身份管理',false),
  ('admin','admin.governance','治理审计',false),
  ('admin','admin.access_history','访问记录',false);

create table platform_control.user_access_events (
  access_event_id uuid primary key,
  internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  session_id uuid not null,
  event_kind text not null
    check (event_kind in ('login_succeeded','page_view')),
  login_kind text
    check (login_kind is null or login_kind in ('qr','in_client')),
  workspace_key text,
  page_key text,
  agent_id text
    check (
      agent_id is null
      or agent_id in (
        'marketing-prospecting-bot','marketing-inbound-bot',
        'marketing-voice-bot','marketing-intelligence-bot',
        'marketing-gtm-bot'
      )
    ),
  occurred_at timestamptz not null default clock_timestamp(),
  foreign key (workspace_key,page_key)
    references platform_control.access_page_catalog(workspace_key,page_key),
  constraint user_access_event_shape check (
    (
      event_kind='login_succeeded'
      and login_kind is not null
      and workspace_key is null and page_key is null and agent_id is null
    ) or (
      event_kind='page_view'
      and login_kind is null
      and workspace_key is not null and page_key is not null
      and (
        (workspace_key='marketing' and agent_id is not null)
        or (workspace_key<>'marketing' and agent_id is null)
      )
    )
  )
);

create unique index one_login_event_per_session
  on platform_control.user_access_events(session_id)
  where event_kind='login_succeeded';
create index user_access_events_time
  on platform_control.user_access_events(occurred_at desc,access_event_id desc);
create index user_access_events_user_time
  on platform_control.user_access_events(
    internal_user_id,occurred_at desc,access_event_id desc
  );
create index user_access_events_workspace_time
  on platform_control.user_access_events(
    workspace_key,occurred_at desc,access_event_id desc
  ) where workspace_key is not null;
create index user_access_events_kind_time
  on platform_control.user_access_events(
    event_kind,occurred_at desc,access_event_id desc
  );
create index user_access_events_session_page_rate
  on platform_control.user_access_events(session_id,occurred_at desc)
  where event_kind='page_view';

revoke all on platform_control.access_page_catalog from public;
revoke all on platform_control.user_access_events from public;

create function platform_control.consume_attempt_and_issue_session_v65(
  selected_attempt_id uuid,
  selected_internal_user_id uuid,
  selected_session_id uuid,
  selected_token_hash bytea,
  selected_token_key_version integer,
  selected_csrf_hash bytea,
  selected_csrf_key_version integer,
  selected_idle_seconds integer,
  selected_absolute_seconds integer,
  selected_hard_stale_read_only boolean
) returns table(
  session_id uuid,
  idle_expires_at timestamptz,
  absolute_expires_at timestamptz
)
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  issued_session_id uuid;
  issued_idle_expires_at timestamptz;
  issued_absolute_expires_at timestamptz;
  selected_login_kind text;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
  then
    raise insufficient_privilege using message='access history caller rejected';
  end if;

  select issued.session_id,issued.idle_expires_at,issued.absolute_expires_at
    into issued_session_id,issued_idle_expires_at,issued_absolute_expires_at
  from platform_control.consume_attempt_and_issue_session_v22(
    selected_attempt_id,selected_internal_user_id,selected_session_id,
    selected_token_hash,selected_token_key_version,selected_csrf_hash,
    selected_csrf_key_version,selected_idle_seconds,selected_absolute_seconds,
    selected_hard_stale_read_only
  ) issued;
  if issued_session_id is null then return; end if;

  select attempt.attempt_kind into selected_login_kind
  from platform_control.login_attempts attempt
  where attempt.login_attempt_id=selected_attempt_id
    and attempt.consumed_at is not null;
  if selected_login_kind is null then
    raise check_violation using message='login access event unavailable';
  end if;

  insert into platform_control.user_access_events(
    access_event_id,internal_user_id,session_id,event_kind,login_kind
  ) values (
    gen_random_uuid(),selected_internal_user_id,issued_session_id,
    'login_succeeded',selected_login_kind
  );

  return query select issued_session_id,issued_idle_expires_at,
    issued_absolute_expires_at;
end
$function$;

create function platform_control.append_page_view_v65(
  selected_access_event_id uuid,
  selected_actor_id uuid,
  selected_session_id uuid,
  selected_workspace_key text,
  selected_page_key text,
  selected_agent_id text
) returns table(outcome text,retry_after_seconds integer)
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  database_now timestamptz := clock_timestamp();
  stored platform_control.user_access_events%rowtype;
  recent_count integer;
  catalog_allows_agent boolean;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or selected_access_event_id is null
     or selected_actor_id is null
     or selected_session_id is null
     or selected_workspace_key is null
     or selected_page_key is null
  then
    raise check_violation using message='page access event invalid';
  end if;

  perform pg_advisory_xact_lock(
    hashtextextended(selected_session_id::text,0)
  );

  select event.* into stored
  from platform_control.user_access_events event
  where event.access_event_id=selected_access_event_id;
  if found then
    if stored.internal_user_id=selected_actor_id
       and stored.session_id=selected_session_id
       and stored.event_kind='page_view'
       and stored.workspace_key=selected_workspace_key
       and stored.page_key=selected_page_key
       and stored.agent_id is not distinct from selected_agent_id
    then
      return query select 'duplicate'::text,null::integer;
      return;
    end if;
    raise check_violation using message='access event id conflict';
  end if;

  perform 1 from platform_control.web_sessions session
  join platform_control.internal_users users
    on users.internal_user_id=session.internal_user_id
  where session.session_id=selected_session_id
    and session.internal_user_id=selected_actor_id
    and session.revoked_at is null
    and session.idle_expires_at > database_now
    and session.absolute_expires_at > database_now
    and users.status='active'
    and users.locally_invalidated_at is null;
  if not found then
    raise insufficient_privilege using message='page access session rejected';
  end if;

  select catalog.allows_agent_id into catalog_allows_agent
  from platform_control.access_page_catalog catalog
  where catalog.workspace_key=selected_workspace_key
    and catalog.page_key=selected_page_key;
  if not found
     or (
       catalog_allows_agent
       and selected_agent_id not in (
         'marketing-prospecting-bot','marketing-inbound-bot',
         'marketing-voice-bot','marketing-intelligence-bot',
         'marketing-gtm-bot'
       )
     )
     or (not catalog_allows_agent and selected_agent_id is not null)
  then
    raise check_violation using message='page access catalog rejected';
  end if;

  select count(*)::integer into recent_count
  from platform_control.user_access_events event
  where event.session_id=selected_session_id
    and event.event_kind='page_view'
    and event.occurred_at > database_now-interval '60 seconds';
  if recent_count >= 120 then
    return query select 'rate_limited'::text,60::integer;
    return;
  end if;

  insert into platform_control.user_access_events(
    access_event_id,internal_user_id,session_id,event_kind,
    workspace_key,page_key,agent_id,occurred_at
  ) values (
    selected_access_event_id,selected_actor_id,selected_session_id,'page_view',
    selected_workspace_key,selected_page_key,selected_agent_id,database_now
  );
  return query select 'inserted'::text,null::integer;
end
$function$;

create function platform_control.read_user_access_events_v65(
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
  event_kind text,
  login_kind text,
  workspace_key text,
  page_key text,
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
    and users.role = 'platform_owner';
  if not found then
    raise insufficient_privilege using message='access history owner required';
  end if;

  return query
  select event.access_event_id,users.display_name,event.event_kind,
    event.login_kind,event.workspace_key,event.page_key,catalog.display_name,
    event.agent_id,event.occurred_at
  from platform_control.user_access_events event
  join platform_control.internal_users users
    on users.internal_user_id=event.internal_user_id
  left join platform_control.access_page_catalog catalog
    on catalog.workspace_key=event.workspace_key
   and catalog.page_key=event.page_key
  where event.occurred_at >= selected_date_from
    and event.occurred_at < selected_date_to
    and (
      selected_display_name is null
      or users.display_name=selected_display_name
    )
    and (
      selected_workspace_key is null
      or event.workspace_key=selected_workspace_key
    )
    and (
      selected_event_kind is null
      or event.event_kind=selected_event_kind
    )
  order by event.occurred_at desc,event.access_event_id desc
  limit selected_limit offset selected_offset;
end
$function$;

create function platform_control.retain_user_access_events_v65(
  selected_cutoff timestamptz
) returns bigint
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  deleted_count bigint;
  effective_cutoff timestamptz;
begin
  if session_user not in (
    'platform_control_maintenance','platform_control_maintenance_preview'
  ) or selected_cutoff is null
  then
    raise insufficient_privilege using message='access retention caller rejected';
  end if;
  effective_cutoff := least(
    selected_cutoff,clock_timestamp()-interval '90 days'
  );
  delete from platform_control.user_access_events event
  where event.occurred_at < effective_cutoff;
  get diagnostics deleted_count = row_count;
  return deleted_count;
end
$function$;

revoke all on function platform_control.consume_attempt_and_issue_session_v65(
  uuid,uuid,uuid,bytea,integer,bytea,integer,integer,integer,boolean
) from public;
revoke all on function platform_control.append_page_view_v65(
  uuid,uuid,uuid,text,text,text
) from public;
revoke all on function platform_control.read_user_access_events_v65(
  uuid,uuid,timestamptz,timestamptz,text,text,text,integer,integer
) from public;
revoke all on function platform_control.retain_user_access_events_v65(
  timestamptz
) from public;

do $migration$
declare
  selected_app name;
  selected_maintenance name;
  role_name name;
begin
  if current_database()='agent_platform_control'
     and current_user='platform_control_owner'
  then
    selected_app:='platform_control_app';
    selected_maintenance:='platform_control_maintenance';
  elsif current_database()='agent_platform_control_preview'
        and current_user='platform_control_owner_preview'
  then
    selected_app:='platform_control_app_preview';
    selected_maintenance:='platform_control_maintenance_preview';
  else
    raise insufficient_privilege using
      message='access history owner/environment mismatch';
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
      'revoke all on platform_control.access_page_catalog from %I',role_name
    );
    execute format(
      'revoke all on platform_control.user_access_events from %I',role_name
    );
    execute format(
      'revoke all on function platform_control.consume_attempt_and_issue_session_v65('
      'uuid,uuid,uuid,bytea,integer,bytea,integer,integer,integer,boolean) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.append_page_view_v65('
      'uuid,uuid,uuid,text,text,text) from %I',role_name
    );
    execute format(
      'revoke all on function platform_control.read_user_access_events_v65('
      'uuid,uuid,timestamptz,timestamptz,text,text,text,integer,integer) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.retain_user_access_events_v65('
      'timestamptz) from %I',role_name
    );
  end loop;

  -- Keep v22 executable during the rollback window. Migration 065 can be
  -- applied before every application node has moved to the v65 wrapper.
  execute format(
    'grant execute on function platform_control.consume_attempt_and_issue_session_v65('
    'uuid,uuid,uuid,bytea,integer,bytea,integer,integer,integer,boolean) to %I',
    selected_app
  );
  execute format(
    'grant execute on function platform_control.append_page_view_v65('
    'uuid,uuid,uuid,text,text,text) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_control.read_user_access_events_v65('
    'uuid,uuid,timestamptz,timestamptz,text,text,text,integer,integer) to %I',
    selected_app
  );
  execute format(
    'grant execute on function platform_control.retain_user_access_events_v65('
    'timestamptz) to %I',selected_maintenance
  );
end
$migration$;
