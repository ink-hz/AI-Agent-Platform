create type platform_control.agent_subject_type as enum
  ('enterprise_member','partner_operator');

create table platform_control.agent_access_subjects (
  subject_id uuid primary key,
  subject_type platform_control.agent_subject_type not null,
  status text not null check (status in ('active','suspended','disabled')),
  display_name_ciphertext bytea,
  display_name_key_version integer,
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  invalidated_at timestamptz,
  check (num_nonnulls(display_name_ciphertext, display_name_key_version) in (0, 2)),
  check (display_name_key_version is null or display_name_key_version > 0)
);

create table platform_control.enterprise_subject_links (
  subject_id uuid primary key
    references platform_control.agent_access_subjects(subject_id) on delete restrict,
  internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id) on delete restrict,
  unique (internal_user_id),
  check (subject_id = internal_user_id)
);

create function platform_control.guard_agent_access_subject_v53()
returns trigger
language plpgsql
set search_path=pg_catalog,platform_control
as $function$
begin
  if tg_op='UPDATE'
     and new.subject_type is distinct from old.subject_type
     and exists (
       select 1
       from platform_control.enterprise_subject_links link
       where link.subject_id=new.subject_id
     )
  then
    raise check_violation using message='Enterprise subject type required';
  end if;

  if new.subject_type='enterprise_member'
     and num_nonnulls(
       new.display_name_ciphertext,
       new.display_name_key_version
     )<>0
  then
    raise check_violation using
      message='Enterprise subject display name must be null';
  elsif new.subject_type='partner_operator'
     and num_nonnulls(
       new.display_name_ciphertext,
       new.display_name_key_version
     )<>2
  then
    raise check_violation using message='Partner subject display name required';
  end if;

  return new;
end
$function$;

create trigger guard_agent_access_subject_v53
before insert or update on platform_control.agent_access_subjects
for each row execute function platform_control.guard_agent_access_subject_v53();

create function platform_control.guard_enterprise_subject_link_v53()
returns trigger
language plpgsql
set search_path=pg_catalog,platform_control
as $function$
declare
  selected_subject_type platform_control.agent_subject_type;
begin
  select subject.subject_type into selected_subject_type
  from platform_control.agent_access_subjects subject
  where subject.subject_id=new.subject_id
  for update;

  if found and selected_subject_type<>'enterprise_member' then
    raise check_violation using
      message='Partner subject cannot have enterprise link';
  end if;

  return new;
end
$function$;

create trigger guard_enterprise_subject_link_v53
before insert or update on platform_control.enterprise_subject_links
for each row execute function platform_control.guard_enterprise_subject_link_v53();

insert into platform_control.agent_access_subjects (
  subject_id,
  subject_type,
  status,
  created_at,
  updated_at,
  invalidated_at
)
select
  users.internal_user_id,
  'enterprise_member',
  case users.status
    when 'active' then 'active'
    when 'inactive' then 'suspended'
    else 'disabled'
  end,
  users.created_at,
  users.updated_at,
  users.locally_invalidated_at
from platform_control.internal_users users
order by users.internal_user_id;

insert into platform_control.enterprise_subject_links (
  subject_id,
  internal_user_id
)
select subjects.subject_id,users.internal_user_id
from platform_control.internal_users users
join platform_control.agent_access_subjects subjects
  on subjects.subject_id=users.internal_user_id
where subjects.subject_type='enterprise_member'
order by users.internal_user_id;

revoke all on platform_control.agent_access_subjects from public;
revoke all on platform_control.enterprise_subject_links from public;
revoke all on function platform_control.guard_agent_access_subject_v53()
  from public;
revoke all on function platform_control.guard_enterprise_subject_link_v53()
  from public;

do $migration$
declare
  role_name name;
begin
  if not (
    (current_database()='agent_platform_control'
      and current_user='platform_control_owner')
    or
    (current_database()='agent_platform_control_preview'
      and current_user='platform_control_owner_preview')
  ) then
    raise insufficient_privilege using
      message='Agent access subject migration owner invalid';
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
      'revoke all on platform_control.agent_access_subjects, '
      'platform_control.enterprise_subject_links from %I',
      role_name
    );
    execute format(
      'revoke all on function '
      'platform_control.guard_agent_access_subject_v53() from %I',
      role_name
    );
    execute format(
      'revoke all on function '
      'platform_control.guard_enterprise_subject_link_v53() from %I',
      role_name
    );
  end loop;
end
$migration$;
