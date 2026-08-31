insert into platform_control.agent_access_subjects (
  subject_id,subject_type,status,created_at,updated_at,invalidated_at
)
select users.internal_user_id,'enterprise_member',
  case users.status
    when 'active' then 'active'
    when 'inactive' then 'suspended'
    else 'disabled'
  end,
  users.created_at,users.updated_at,users.locally_invalidated_at
from platform_control.internal_users users
on conflict (subject_id) do update set
  status=excluded.status,
  updated_at=excluded.updated_at,
  invalidated_at=excluded.invalidated_at
where platform_control.agent_access_subjects.subject_type='enterprise_member';

insert into platform_control.enterprise_subject_links(subject_id,internal_user_id)
select users.internal_user_id,users.internal_user_id
from platform_control.internal_users users
on conflict (subject_id) do nothing;

alter table platform_control.agent_launch_codes
  add column subject_id uuid
    references platform_control.agent_access_subjects(subject_id) on delete restrict,
  add column subject_type platform_control.agent_subject_type;

update platform_control.agent_launch_codes
set subject_id=internal_user_id,
    subject_type='enterprise_member';

alter table platform_control.agent_launch_codes
  alter column subject_id set not null,
  alter column subject_type set not null,
  alter column source_session_id drop not null,
  alter column internal_user_id drop not null,
  add constraint agent_launch_codes_subject_shape_v57 check (
    (subject_type='enterprise_member' and source_session_id is not null
      and internal_user_id is not null and subject_id=internal_user_id)
    or
    (subject_type='partner_operator' and source_session_id is null
      and internal_user_id is null)
  );

alter table platform_control.agent_identity_bindings
  add column subject_id uuid
    references platform_control.agent_access_subjects(subject_id) on delete restrict,
  add column subject_type platform_control.agent_subject_type;

update platform_control.agent_identity_bindings
set subject_id=internal_user_id,
    subject_type='enterprise_member';

alter table platform_control.agent_identity_bindings
  alter column subject_id set not null,
  alter column subject_type set not null,
  alter column source_session_id drop not null,
  alter column internal_user_id drop not null,
  add constraint agent_identity_bindings_subject_shape_v57 check (
    (subject_type='enterprise_member' and source_session_id is not null
      and internal_user_id is not null and subject_id=internal_user_id)
    or
    (subject_type='partner_operator' and source_session_id is null
      and internal_user_id is null)
  );

create index agent_launch_codes_subject_v57
  on platform_control.agent_launch_codes(subject_id,subject_type,agent_id)
  where consumed_at is null;

create index agent_identity_bindings_subject_v57
  on platform_control.agent_identity_bindings(subject_id,subject_type,agent_id)
  where revoked_at is null;

create function platform_control.issue_agent_launch_v57(
  selected_code_id uuid,
  selected_code_hash bytea,
  selected_code_key_version integer,
  selected_subject_id uuid,
  selected_subject_type platform_control.agent_subject_type,
  selected_source_session_id uuid,
  selected_internal_user_id uuid,
  selected_agent_id text,
  selected_binding_id uuid,
  selected_ttl_seconds integer
) returns timestamptz
language plpgsql
security definer
set search_path=pg_catalog,platform_control
as $function$
declare
  database_now timestamptz := clock_timestamp();
  selected_expires_at timestamptz := database_now + interval '60 seconds';
  selected_allowed boolean := false;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or selected_code_id is null
     or selected_code_hash is null
     or octet_length(selected_code_hash)<>32
     or selected_code_key_version is null
     or selected_code_key_version<=0
     or selected_subject_id is null
     or selected_subject_type is null
     or selected_agent_id<>'ai-fae-agent'
     or selected_binding_id is null
     or selected_ttl_seconds<>60
     or not (
       (selected_subject_type='enterprise_member'
         and selected_source_session_id is not null
         and selected_internal_user_id is not null
         and selected_subject_id=selected_internal_user_id)
       or
       (selected_subject_type='partner_operator'
         and selected_source_session_id is null
         and selected_internal_user_id is null)
     )
  then
    raise check_violation using message='Agent launch input invalid';
  end if;

  if selected_subject_type='enterprise_member' then
    select exists (
      select 1
      from platform_control.web_sessions session
      join platform_control.internal_users users
        on users.internal_user_id=session.internal_user_id
      where session.session_id=selected_source_session_id
        and session.internal_user_id=selected_internal_user_id
        and session.revoked_at is null
        and session.idle_expires_at>database_now
        and session.absolute_expires_at>database_now
        and session.hard_stale_read_only=false
        and users.status='active'
        and users.locally_invalidated_at is null
        and platform_control.has_agent_use_scope_v29(
          selected_internal_user_id,selected_agent_id
        )
    ) into selected_allowed;

    if selected_allowed is true then
      insert into platform_control.agent_access_subjects(
        subject_id,subject_type,status,created_at,updated_at,invalidated_at
      )
      select users.internal_user_id,'enterprise_member',
        case users.status
          when 'active' then 'active'
          when 'inactive' then 'suspended'
          else 'disabled'
        end,
        users.created_at,users.updated_at,users.locally_invalidated_at
      from platform_control.internal_users users
      where users.internal_user_id=selected_internal_user_id
      on conflict (subject_id) do nothing;

      insert into platform_control.enterprise_subject_links(
        subject_id,internal_user_id
      )
      select subject.subject_id,selected_internal_user_id
      from platform_control.agent_access_subjects subject
      where subject.subject_id=selected_subject_id
        and subject.subject_type='enterprise_member'
      on conflict (subject_id) do nothing;

      select exists (
        select 1
        from platform_control.web_sessions session
        join platform_control.internal_users users
          on users.internal_user_id=session.internal_user_id
        join platform_control.enterprise_subject_links link
          on link.internal_user_id=users.internal_user_id
        join platform_control.agent_access_subjects subject
          on subject.subject_id=link.subject_id
        where session.session_id=selected_source_session_id
          and session.internal_user_id=selected_internal_user_id
          and link.subject_id=selected_subject_id
          and subject.subject_type='enterprise_member'
          and subject.status='active'
          and subject.invalidated_at is null
          and session.revoked_at is null
          and session.idle_expires_at>database_now
          and session.absolute_expires_at>database_now
          and session.hard_stale_read_only=false
          and users.status='active'
          and users.locally_invalidated_at is null
          and platform_control.has_agent_use_scope_v29(
            selected_internal_user_id,selected_agent_id
          )
      ) into selected_allowed;
    end if;
  else
    select exists (
      select 1
      from platform_control.agent_access_subjects subject
      join platform_control.partner_operators operator
        on operator.subject_id=subject.subject_id
      join platform_control.partner_organizations organization
        on organization.partner_organization_id=operator.partner_organization_id
      where subject.subject_id=selected_subject_id
        and subject.subject_type='partner_operator'
        and subject.status='active'
        and subject.invalidated_at is null
        and operator.status='active'
        and operator.invalidated_at is null
        and organization.status='active'
        and organization.invalidated_at is null
        and exists (
          select 1
          from platform_control.partner_agent_grants grant_row
          where grant_row.subject_id=subject.subject_id
            and grant_row.agent_id=selected_agent_id
            and grant_row.revoked_at is null
        )
        and exists (
          select 1
          from platform_control.partner_provider_identities identity
          where identity.partner_operator_id=operator.partner_operator_id
            and identity.revoked_at is null
        )
    ) into selected_allowed;
  end if;

  if selected_allowed is distinct from true then
    raise insufficient_privilege using message='Agent launch denied';
  end if;

  insert into platform_control.agent_launch_codes(
    launch_code_id,code_hash,code_key_version,subject_id,subject_type,
    source_session_id,internal_user_id,agent_id,identity_binding_id,expires_at
  ) values (
    selected_code_id,selected_code_hash,selected_code_key_version,
    selected_subject_id,selected_subject_type,selected_source_session_id,
    selected_internal_user_id,selected_agent_id,selected_binding_id,
    selected_expires_at
  );
  return selected_expires_at;
end
$function$;

create function platform_control.exchange_agent_launch_v57(
  selected_code_hash bytea,
  selected_code_key_version integer
) returns table(
  subject_id uuid,
  subject_type platform_control.agent_subject_type,
  identity_binding_id uuid,
  agent_id text,
  internal_user_id uuid,
  display_name text
)
language plpgsql
security definer
set search_path=pg_catalog,platform_control
as $function$
declare
  database_now timestamptz := clock_timestamp();
  selected platform_control.agent_launch_codes%rowtype;
  selected_allowed boolean := false;
  selected_display_name text;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or selected_code_hash is null
     or octet_length(selected_code_hash)<>32
     or selected_code_key_version is null
     or selected_code_key_version<=0
  then
    return;
  end if;

  select code.* into selected
  from platform_control.agent_launch_codes code
  where code.code_hash=selected_code_hash
    and code.code_key_version=selected_code_key_version
    and code.consumed_at is null
    and code.expires_at>database_now
  for update;
  if not found then
    return;
  end if;

  if selected.subject_type='enterprise_member' then
    select users.display_name, true
    into selected_display_name, selected_allowed
    from platform_control.web_sessions session
    join platform_control.internal_users users
      on users.internal_user_id=session.internal_user_id
    join platform_control.enterprise_subject_links link
      on link.internal_user_id=users.internal_user_id
    join platform_control.agent_access_subjects subject
      on subject.subject_id=link.subject_id
    where session.session_id=selected.source_session_id
      and session.internal_user_id=selected.internal_user_id
      and link.subject_id=selected.subject_id
      and subject.subject_type='enterprise_member'
      and subject.status='active'
      and subject.invalidated_at is null
      and session.revoked_at is null
      and session.idle_expires_at>database_now
      and session.absolute_expires_at>database_now
      and session.hard_stale_read_only=false
      and users.status='active'
      and users.locally_invalidated_at is null
      and platform_control.has_agent_use_scope_v29(
        selected.internal_user_id,selected.agent_id
      );
  else
    select exists (
      select 1
      from platform_control.agent_access_subjects subject
      join platform_control.partner_operators operator
        on operator.subject_id=subject.subject_id
      join platform_control.partner_organizations organization
        on organization.partner_organization_id=operator.partner_organization_id
      where subject.subject_id=selected.subject_id
        and subject.subject_type='partner_operator'
        and subject.status='active'
        and subject.invalidated_at is null
        and operator.status='active'
        and operator.invalidated_at is null
        and organization.status='active'
        and organization.invalidated_at is null
        and exists (
          select 1
          from platform_control.partner_agent_grants grant_row
          where grant_row.subject_id=subject.subject_id
            and grant_row.agent_id=selected.agent_id
            and grant_row.revoked_at is null
        )
        and exists (
          select 1
          from platform_control.partner_provider_identities identity
          where identity.partner_operator_id=operator.partner_operator_id
            and identity.revoked_at is null
        )
    ) into selected_allowed;
  end if;

  if selected_allowed is distinct from true then
    update platform_control.agent_launch_codes code
      set consumed_at=database_now
      where code.launch_code_id=selected.launch_code_id;
    return;
  end if;

  insert into platform_control.agent_identity_bindings(
    identity_binding_id,subject_id,subject_type,source_session_id,
    internal_user_id,agent_id
  ) values (
    selected.identity_binding_id,selected.subject_id,selected.subject_type,
    selected.source_session_id,selected.internal_user_id,selected.agent_id
  );
  update platform_control.agent_launch_codes code
    set consumed_at=database_now
    where code.launch_code_id=selected.launch_code_id;

  return query select
    selected.subject_id,selected.subject_type,selected.identity_binding_id,
    selected.agent_id,selected.internal_user_id,selected_display_name;
end
$function$;

create function platform_control.validate_agent_identity_binding_v57(
  selected_binding_id uuid,
  selected_agent_id text
) returns table(
  subject_id uuid,
  subject_type platform_control.agent_subject_type,
  identity_binding_id uuid,
  agent_id text,
  internal_user_id uuid,
  display_name text,
  active boolean
)
language plpgsql
security definer
set search_path=pg_catalog,platform_control
as $function$
declare
  database_now timestamptz := clock_timestamp();
  selected platform_control.agent_identity_bindings%rowtype;
  selected_active boolean := false;
  selected_display_name text;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or selected_binding_id is null
     or selected_agent_id<>'ai-fae-agent'
  then
    return;
  end if;

  select binding.* into selected
  from platform_control.agent_identity_bindings binding
  where binding.identity_binding_id=selected_binding_id
    and binding.agent_id=selected_agent_id
  for update;
  if not found then
    return;
  end if;

  if selected.revoked_at is null and selected.subject_type='enterprise_member' then
    select users.display_name, true
    into selected_display_name, selected_active
    from platform_control.web_sessions session
    join platform_control.internal_users users
      on users.internal_user_id=session.internal_user_id
    join platform_control.enterprise_subject_links link
      on link.internal_user_id=users.internal_user_id
    join platform_control.agent_access_subjects subject
      on subject.subject_id=link.subject_id
    where session.session_id=selected.source_session_id
      and session.internal_user_id=selected.internal_user_id
      and link.subject_id=selected.subject_id
      and subject.subject_type='enterprise_member'
      and subject.status='active'
      and subject.invalidated_at is null
      and session.revoked_at is null
      and session.idle_expires_at>database_now
      and session.absolute_expires_at>database_now
      and session.hard_stale_read_only=false
      and users.status='active'
      and users.locally_invalidated_at is null
      and platform_control.has_agent_use_scope_v29(
        selected.internal_user_id,selected.agent_id
      );
  elsif selected.revoked_at is null and selected.subject_type='partner_operator' then
    select exists (
      select 1
      from platform_control.agent_access_subjects subject
      join platform_control.partner_operators operator
        on operator.subject_id=subject.subject_id
      join platform_control.partner_organizations organization
        on organization.partner_organization_id=operator.partner_organization_id
      where subject.subject_id=selected.subject_id
        and subject.subject_type='partner_operator'
        and subject.status='active'
        and subject.invalidated_at is null
        and operator.status='active'
        and operator.invalidated_at is null
        and organization.status='active'
        and organization.invalidated_at is null
        and exists (
          select 1
          from platform_control.partner_agent_grants grant_row
          where grant_row.subject_id=subject.subject_id
            and grant_row.agent_id=selected.agent_id
            and grant_row.revoked_at is null
        )
        and exists (
          select 1
          from platform_control.partner_provider_identities identity
          where identity.partner_operator_id=operator.partner_operator_id
            and identity.revoked_at is null
        )
    ) into selected_active;
  end if;

  selected_active := coalesce(selected_active,false);
  if selected_active is true then
    update platform_control.agent_identity_bindings binding
      set last_validated_at=database_now
      where binding.identity_binding_id=selected.identity_binding_id;
  else
    update platform_control.agent_identity_bindings binding
      set revoked_at=coalesce(binding.revoked_at,database_now)
      where binding.identity_binding_id=selected.identity_binding_id;
  end if;

  return query select
    selected.subject_id,selected.subject_type,selected.identity_binding_id,
    selected.agent_id,selected.internal_user_id,selected_display_name,
    selected_active;
end
$function$;

create function platform_control.revoke_agent_identity_binding_v57(
  selected_binding_id uuid,
  selected_agent_id text
) returns boolean
language plpgsql
security definer
set search_path=pg_catalog,platform_control
as $function$
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or selected_binding_id is null
     or selected_agent_id<>'ai-fae-agent'
  then
    return false;
  end if;
  update platform_control.agent_identity_bindings binding
    set revoked_at=coalesce(binding.revoked_at,clock_timestamp())
    where binding.identity_binding_id=selected_binding_id
      and binding.agent_id=selected_agent_id;
  return found;
end
$function$;

create function platform_control.get_partner_fae_subject_v57(
  selected_subject_id uuid,
  selected_provider_kind text
) returns table(
  identity_kind text,
  identity_lookup_hmac bytea,
  identity_lookup_key_version integer,
  identity_ciphertext bytea,
  identity_encryption_key_version integer,
  display_name_ciphertext bytea,
  display_name_key_version integer,
  partner_organization_id uuid,
  partner_name_ciphertext bytea,
  partner_name_key_version integer
)
language plpgsql
security definer
set search_path=pg_catalog,platform_control
as $function$
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or selected_subject_id is null
     or selected_provider_kind is null
     or selected_provider_kind=''
     or selected_provider_kind<>btrim(selected_provider_kind)
     or position(':' in selected_provider_kind)<>0
  then
    return;
  end if;
  return query
  select identity.provider_kind,
    identity.provider_subject_lookup_hmac,identity.lookup_key_version,
    identity.provider_subject_ciphertext,identity.encryption_key_version,
    subject.display_name_ciphertext,subject.display_name_key_version,
    organization.partner_organization_id,organization.name_ciphertext,
    organization.name_key_version
  from platform_control.agent_access_subjects subject
  join platform_control.partner_operators operator
    on operator.subject_id=subject.subject_id
  join platform_control.partner_organizations organization
    on organization.partner_organization_id=operator.partner_organization_id
  join platform_control.partner_provider_identities identity
    on identity.partner_operator_id=operator.partner_operator_id
  where subject.subject_id=selected_subject_id
    and subject.subject_type='partner_operator'
    and subject.status='active'
    and subject.invalidated_at is null
    and operator.status='active'
    and operator.invalidated_at is null
    and organization.status='active'
    and organization.invalidated_at is null
    and identity.provider_kind=selected_provider_kind
    and identity.revoked_at is null
    and exists (
      select 1
      from platform_control.partner_agent_grants grant_row
      where grant_row.subject_id=subject.subject_id
        and grant_row.agent_id='ai-fae-agent'
        and grant_row.revoked_at is null
    );
end
$function$;

revoke all on function platform_control.issue_agent_launch_v57(
  uuid,bytea,integer,uuid,platform_control.agent_subject_type,uuid,uuid,text,uuid,integer
) from public;
revoke all on function platform_control.exchange_agent_launch_v57(
  bytea,integer
) from public;
revoke all on function platform_control.validate_agent_identity_binding_v57(
  uuid,text
) from public;
revoke all on function platform_control.revoke_agent_identity_binding_v57(
  uuid,text
) from public;
revoke all on function platform_control.get_partner_fae_subject_v57(
  uuid,text
) from public;
do $migration$
declare
  selected_app name;
  role_name name;
begin
  selected_app := case
    when current_database()='agent_platform_control'
      and current_user='platform_control_owner'
      then 'platform_control_app'
    when current_database()='agent_platform_control_preview'
      and current_user='platform_control_owner_preview'
      then 'platform_control_app_preview'
    else null
  end;
  if selected_app is null then
    raise insufficient_privilege using
      message='Generic agent launch migration owner invalid';
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
      'revoke all on function platform_control.issue_agent_launch_v52(uuid,bytea,integer,uuid,uuid,text,uuid,integer) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.exchange_agent_launch_v52(bytea,integer) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.validate_agent_identity_binding_v52(uuid,text) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.issue_agent_launch_v57(uuid,bytea,integer,uuid,platform_control.agent_subject_type,uuid,uuid,text,uuid,integer) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.exchange_agent_launch_v57(bytea,integer) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.validate_agent_identity_binding_v57(uuid,text) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.revoke_agent_identity_binding_v57(uuid,text) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.get_partner_fae_subject_v57(uuid,text) from %I',
      role_name
    );
  end loop;

  execute format(
    'grant execute on function platform_control.issue_agent_launch_v57(uuid,bytea,integer,uuid,platform_control.agent_subject_type,uuid,uuid,text,uuid,integer) to %I',
    selected_app
  );
  execute format(
    'grant execute on function platform_control.exchange_agent_launch_v57(bytea,integer) to %I',
    selected_app
  );
  execute format(
    'grant execute on function platform_control.validate_agent_identity_binding_v57(uuid,text) to %I',
    selected_app
  );
  execute format(
    'grant execute on function platform_control.revoke_agent_identity_binding_v57(uuid,text) to %I',
    selected_app
  );
  execute format(
    'grant execute on function platform_control.get_partner_fae_subject_v57(uuid,text) to %I',
    selected_app
  );
end
$migration$;
