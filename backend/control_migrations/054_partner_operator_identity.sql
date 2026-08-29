alter table platform_control.provider_identity_key_policies
  drop constraint provider_identity_key_policies_provider_check;
alter table platform_control.provider_identity_key_policies
  add constraint provider_identity_key_policies_provider_check
  check (provider in ('dingtalk','partner'));

create or replace function platform_control.set_provider_identity_key_policy(
  selected_provider text,
  selected_versions integer[]
) returns void
language plpgsql
security definer
set search_path=pg_catalog,platform_control
as $function$
begin
  if selected_provider not in ('dingtalk','partner')
     or selected_versions is null
     or array_ndims(selected_versions)<>1
     or array_lower(selected_versions,1)<>1
     or cardinality(selected_versions) not between 1 and 3
     or selected_versions[1] is null
     or selected_versions[1]<=0
     or (
       cardinality(selected_versions)>=2
       and (
         selected_versions[2] is null
         or selected_versions[2]<>selected_versions[1]+1
       )
     )
     or (
       cardinality(selected_versions)>=3
       and (
         selected_versions[3] is null
         or selected_versions[3]<>selected_versions[2]+1
       )
     )
  then
    raise check_violation using
      message='provider identity key policy invalid';
  end if;
  perform pg_advisory_xact_lock(1229998928);
  if selected_provider='partner' and (
    exists (
      select 1
      from platform_control.partner_provider_identities identity
      where not identity.lookup_key_version=any(selected_versions)
    )
    or exists (
      select 1
      from platform_control.partner_identity_binding_requests request
      where request.status='pending'
        and request.expires_at>clock_timestamp()
        and request.lookup_transition_versions is distinct from selected_versions
    )
  ) then
    raise check_violation using
      message='partner identity key policy rollover unsafe';
  end if;
  insert into platform_control.provider_identity_key_policies(
    provider,lookup_transition_versions
  ) values (
    selected_provider,selected_versions
  ) on conflict (provider) do update set
    lookup_transition_versions=excluded.lookup_transition_versions,
    updated_at=now();
end
$function$;

create table platform_control.partner_organizations (
  partner_organization_id uuid primary key,
  status text not null check (status in ('active','suspended','disabled')),
  name_ciphertext bytea not null check (octet_length(name_ciphertext)>=28),
  name_key_version integer not null check (name_key_version>0),
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  invalidated_at timestamptz
);

create table platform_control.partner_operators (
  partner_operator_id uuid primary key,
  subject_id uuid not null unique
    references platform_control.agent_access_subjects(subject_id) on delete restrict,
  partner_organization_id uuid not null
    references platform_control.partner_organizations(partner_organization_id)
    on delete restrict,
  status text not null check (status in ('active','suspended','disabled')),
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  invalidated_at timestamptz
);
create index partner_operators_organization_v54
  on platform_control.partner_operators(partner_organization_id);

create table platform_control.partner_provider_identities (
  provider_identity_id uuid primary key,
  partner_operator_id uuid not null
    references platform_control.partner_operators(partner_operator_id)
    on delete restrict,
  provider_kind text not null
    check (provider_kind<>'' and provider_kind=btrim(provider_kind)
      and position(':' in provider_kind)=0),
  provider_subject_lookup_hmac bytea not null
    check (octet_length(provider_subject_lookup_hmac)=32),
  lookup_key_version integer not null check (lookup_key_version>0),
  provider_subject_ciphertext bytea not null
    check (octet_length(provider_subject_ciphertext)>=28),
  encryption_key_version integer not null check (encryption_key_version>0),
  verified_at timestamptz not null,
  created_at timestamptz not null default clock_timestamp(),
  revoked_at timestamptz,
  unique (
    provider_kind,
    provider_subject_lookup_hmac,
    lookup_key_version
  )
);
create index partner_provider_identities_operator_v54
  on platform_control.partner_provider_identities(partner_operator_id);

create table platform_control.partner_identity_binding_requests (
  binding_request_id uuid primary key,
  provider_kind text not null
    check (provider_kind<>'' and provider_kind=btrim(provider_kind)
      and position(':' in provider_kind)=0),
  provider_subject_lookup_hmac bytea not null
    check (octet_length(provider_subject_lookup_hmac)=32),
  lookup_key_version integer not null check (lookup_key_version>0),
  lookup_transition_versions integer[] not null,
  provider_subject_lookup_hmac_candidates bytea[] not null,
  provider_subject_ciphertext bytea not null
    check (octet_length(provider_subject_ciphertext)>=28),
  encryption_key_version integer not null check (encryption_key_version>0),
  display_name_ciphertext bytea,
  display_name_key_version integer,
  verified_at timestamptz not null,
  status text not null default 'pending'
    check (status in ('pending','linked','rejected','expired')),
  requested_at timestamptz not null default clock_timestamp(),
  expires_at timestamptz not null default clock_timestamp()+interval '24 hours',
  resolved_at timestamptz,
  linked_partner_operator_id uuid
    references platform_control.partner_operators(partner_operator_id)
    on delete restrict,
  check (num_nonnulls(display_name_ciphertext,display_name_key_version) in (0,2)),
  check (array_ndims(lookup_transition_versions)=1),
  check (array_lower(lookup_transition_versions,1)=1),
  check (cardinality(lookup_transition_versions) between 1 and 3),
  check (array_position(lookup_transition_versions,null) is null),
  check (array_ndims(provider_subject_lookup_hmac_candidates)=1),
  check (array_lower(provider_subject_lookup_hmac_candidates,1)=1),
  check (
    cardinality(provider_subject_lookup_hmac_candidates)
      =cardinality(lookup_transition_versions)
  ),
  check (array_position(provider_subject_lookup_hmac_candidates,null) is null),
  check (display_name_key_version is null or display_name_key_version>0),
  check (display_name_ciphertext is null or octet_length(display_name_ciphertext)>=28),
  check (expires_at>requested_at),
  check (
    (status='pending' and resolved_at is null
      and linked_partner_operator_id is null)
    or
    (status='linked' and resolved_at is not null
      and linked_partner_operator_id is not null)
    or
    (status in ('rejected','expired') and resolved_at is not null
      and linked_partner_operator_id is null)
  )
);
create unique index one_pending_partner_binding_request_v54
  on platform_control.partner_identity_binding_requests(
    provider_kind,provider_subject_lookup_hmac,lookup_key_version
  ) where status='pending';
create index partner_binding_requests_status_expiry_v54
  on platform_control.partner_identity_binding_requests(status,expires_at);

create table platform_control.partner_agent_grants (
  grant_id uuid primary key,
  subject_id uuid not null
    references platform_control.agent_access_subjects(subject_id) on delete restrict,
  agent_id text not null check (agent_id='ai-fae-agent'),
  created_by_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id) on delete restrict,
  created_at timestamptz not null default clock_timestamp(),
  revoked_at timestamptz,
  revoked_by_internal_user_id uuid
    references platform_control.internal_users(internal_user_id) on delete restrict,
  check (num_nonnulls(revoked_at,revoked_by_internal_user_id) in (0,2))
);
create unique index one_active_partner_agent_grant_v54
  on platform_control.partner_agent_grants(subject_id,agent_id)
  where revoked_at is null;

create table platform_control.partner_login_attempts (
  login_attempt_id uuid primary key,
  provider_kind text not null
    check (provider_kind<>'' and provider_kind=btrim(provider_kind)
      and position(':' in provider_kind)=0),
  state_digest bytea not null check (octet_length(state_digest)=32),
  state_key_version integer not null check (state_key_version>0),
  status text not null default 'pending'
    check (status in ('pending','claimed','consumed','rejected','expired')),
  binding_request_id uuid
    references platform_control.partner_identity_binding_requests(binding_request_id)
    on delete restrict,
  created_at timestamptz not null default clock_timestamp(),
  expires_at timestamptz not null,
  consumed_at timestamptz,
  check (expires_at>created_at),
  check ((status in ('pending','claimed') and consumed_at is null)
    or (status in ('consumed','rejected','expired') and consumed_at is not null))
);
create unique index partner_login_attempt_state_v54
  on platform_control.partner_login_attempts(state_digest,state_key_version);

create function platform_control.guard_partner_binding_request_v54()
returns trigger
language plpgsql
set search_path=pg_catalog,platform_control
as $function$
begin
  if tg_op='UPDATE' then
    if old.status<>'pending' and new is distinct from old then
      raise check_violation using message='Pending binding transition invalid';
    end if;
    if old.status='pending' and new.status not in (
      'pending','linked','rejected','expired'
    ) then
      raise check_violation using message='Pending binding transition invalid';
    end if;
    if new.provider_kind is distinct from old.provider_kind
       or new.provider_subject_lookup_hmac
          is distinct from old.provider_subject_lookup_hmac
       or new.lookup_key_version is distinct from old.lookup_key_version
       or new.lookup_transition_versions
          is distinct from old.lookup_transition_versions
       or new.provider_subject_lookup_hmac_candidates
          is distinct from old.provider_subject_lookup_hmac_candidates
       or new.provider_subject_ciphertext
          is distinct from old.provider_subject_ciphertext
       or new.encryption_key_version is distinct from old.encryption_key_version
       or new.verified_at is distinct from old.verified_at
       or new.requested_at is distinct from old.requested_at
       or new.expires_at is distinct from old.expires_at
       or new.display_name_ciphertext is distinct from old.display_name_ciphertext
       or new.display_name_key_version is distinct from old.display_name_key_version
    then
      raise check_violation using message='Pending binding identity immutable';
    end if;
  end if;
  return new;
end
$function$;

create trigger guard_partner_binding_request_v54
before update on platform_control.partner_identity_binding_requests
for each row execute function platform_control.guard_partner_binding_request_v54();

create function platform_control.guard_partner_operator_subject_v54()
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
  if selected_subject_type is distinct from 'partner_operator' then
    raise check_violation using
      message='Partner operator subject type required';
  end if;
  return new;
end
$function$;

create trigger guard_partner_operator_subject_v54
before insert or update on platform_control.partner_operators
for each row execute function platform_control.guard_partner_operator_subject_v54();

create function platform_control.guard_partner_subject_type_v54()
returns trigger
language plpgsql
set search_path=pg_catalog,platform_control
as $function$
begin
  if new.subject_type is distinct from old.subject_type
     and new.subject_type<>'partner_operator' and exists (
    select 1 from platform_control.partner_operators operator
    where operator.subject_id=old.subject_id
    for update
  ) then
    raise check_violation using
      message='Partner operator subject type required';
  end if;
  return new;
end
$function$;

create trigger guard_partner_subject_type_v54
before update on platform_control.agent_access_subjects
for each row execute function platform_control.guard_partner_subject_type_v54();

create function platform_control.require_partner_app_v54()
returns void
language plpgsql
security definer
set search_path=pg_catalog,platform_control
as $function$
declare
  expected_session name;
begin
  expected_session := case current_database()
    when 'agent_platform_control' then 'platform_control_app'
    when 'agent_platform_control_preview' then 'platform_control_app_preview'
    else null
  end;
  if expected_session is null or session_user<>expected_session then
    raise insufficient_privilege using
      message='partner owner mutation caller invalid';
  end if;
end
$function$;

create function platform_control.require_partner_identity_key_policy_v54(
  selected_versions integer[]
) returns void
language plpgsql
security definer
set search_path=pg_catalog,platform_control
as $function$
declare
  stored_versions integer[];
begin
  perform platform_control.require_partner_app_v54();
  if selected_versions is null
     or array_ndims(selected_versions)<>1
     or array_lower(selected_versions,1)<>1
     or cardinality(selected_versions) not between 1 and 3
     or selected_versions[1] is null
     or selected_versions[1]<=0
     or (
       cardinality(selected_versions)>=2
       and (
         selected_versions[2] is null
         or selected_versions[2]<>selected_versions[1]+1
       )
     )
     or (
       cardinality(selected_versions)>=3
       and (
         selected_versions[3] is null
         or selected_versions[3]<>selected_versions[2]+1
       )
     )
  then
    raise check_violation using
      message='partner identity key policy invalid';
  end if;
  perform pg_advisory_xact_lock(1229998928);
  insert into platform_control.provider_identity_key_policies(
    provider,lookup_transition_versions
  ) values (
    'partner',selected_versions
  ) on conflict (provider) do nothing;
  select policy.lookup_transition_versions into stored_versions
  from platform_control.provider_identity_key_policies policy
  where policy.provider='partner'
  for share;
  if stored_versions is distinct from selected_versions then
    raise check_violation using
      message='partner identity key policy mismatch';
  end if;
end
$function$;

create function platform_control.require_partner_owner_v54(selected_actor_id uuid)
returns void
language plpgsql
security definer
set search_path=pg_catalog,platform_control
as $function$
declare
  locked_actor uuid;
begin
  perform platform_control.require_partner_app_v54();
  select internal_user_id into locked_actor
  from platform_control.internal_users
  where internal_user_id=selected_actor_id
    and role='platform_owner'
    and status='active'
    and locally_invalidated_at is null
  for update;
  if locked_actor is null then
    raise insufficient_privilege using message='active platform owner required';
  end if;
end
$function$;

create function platform_control.validate_partner_audit_event_v54(
  actor_id uuid,
  event_name text,
  target_name text,
  target_id text,
  correlation_id uuid,
  event_result text,
  reason text,
  details jsonb
) returns void
language plpgsql
security definer
set search_path=pg_catalog,platform_control
as $function$
declare
  expected_target text;
  expected_keys text[];
  actual_keys text[];
begin
  case event_name
    when 'partner_organization_created' then
      expected_target := 'partner_organization';
      expected_keys := array[
        'operation_id','partner_organization_id','status'
      ];
    when 'partner_organization_status_changed' then
      expected_target := 'partner_organization';
      expected_keys := array[
        'new_status','operation_id','partner_organization_id','previous_status'
      ];
    when 'partner_operator_created' then
      expected_target := 'partner_operator';
      expected_keys := array[
        'operation_id','partner_operator_id','partner_organization_id',
        'status','subject_id'
      ];
    when 'partner_operator_status_changed' then
      expected_target := 'partner_operator';
      expected_keys := array[
        'new_status','operation_id','partner_operator_id','previous_status',
        'subject_id'
      ];
    when 'partner_fae_granted','partner_fae_revoked' then
      expected_target := 'agent_access_subject';
      expected_keys := array[
        'agent_id','operation_id','partner_operator_id','subject_id'
      ];
    when 'partner_identity_linked' then
      expected_target := 'partner_binding_request';
      expected_keys := array[
        'binding_request_id','operation_id','partner_operator_id',
        'provider_identity_id','subject_id'
      ];
    when 'partner_identity_rejected' then
      expected_target := 'partner_binding_request';
      expected_keys := array[
        'binding_request_id','operation_id','provider_kind','status'
      ];
    else
      raise check_violation using message='partner audit event invalid';
  end case;

  if actor_id is null or correlation_id is null or target_id is null
     or target_name<>expected_target or event_result<>'completed'
     or reason is null or reason='' or length(reason)>512
     or jsonb_typeof(details)<>'object'
  then
    raise check_violation using message='partner audit event invalid';
  end if;
  perform target_id::uuid;
  select array_agg(value order by value) into actual_keys
  from jsonb_object_keys(details) key(value);
  if actual_keys is distinct from expected_keys
     or details->>'operation_id'<>correlation_id::text
  then
    raise check_violation using message='partner audit metadata invalid';
  end if;
  if exists (
    select 1 from jsonb_each(details) item
    where jsonb_typeof(item.value)='null'
  ) then
    raise check_violation using message='partner audit metadata invalid';
  end if;
  if details ? 'status' and (
    (event_name='partner_identity_rejected'
      and details->>'status'<>'rejected')
    or
    (event_name<>'partner_identity_rejected'
      and details->>'status' not in ('active','suspended','disabled'))
  )
  then
    raise check_violation using message='partner audit metadata invalid';
  end if;
  if details ? 'previous_status'
     and details->>'previous_status' not in ('active','suspended','disabled')
  then
    raise check_violation using message='partner audit metadata invalid';
  end if;
  if details ? 'new_status'
     and details->>'new_status' not in ('active','suspended','disabled')
  then
    raise check_violation using message='partner audit metadata invalid';
  end if;
  if details ? 'agent_id' and details->>'agent_id'<>'ai-fae-agent' then
    raise check_violation using message='partner audit metadata invalid';
  end if;
exception
  when invalid_text_representation then
    raise check_violation using message='partner audit metadata invalid';
end
$function$;

create or replace function platform_control.append_audit_event(
  event_id uuid,
  actor_id uuid,
  event_name text,
  target_name text,
  target_id text,
  correlation_id uuid,
  event_result text,
  reason text,
  details jsonb
) returns uuid
language plpgsql
security definer
set search_path=pg_catalog,platform_control
as $function$
declare
  stored platform_control.audit_events%rowtype;
  summary_keys constant text[] := array[
    'linked_audit_event_id','new_role','new_scope_count','new_scope_sha256',
    'operation_id','previous_role','previous_scope_count',
    'previous_scope_sha256','result','row_version','session_revocation_count'
  ];
  actual_keys text[];
begin
  select array_agg(value order by value) into actual_keys
  from jsonb_object_keys(details) key(value);
  if event_name like 'partner_%' then
    perform platform_control.validate_partner_audit_event_v54(
      actor_id,event_name,target_name,target_id,correlation_id,
      event_result,reason,details
    );
  elsif event_name like 'admin_role_%' then
    perform platform_control.validate_admin_audit_event_v25(
      actor_id,event_name,target_name,target_id,correlation_id,
      event_result,reason,details
    );
  elsif event_name='viewer_role_revocation_completed'
     and actual_keys=summary_keys
  then
    perform platform_control.validate_viewer_revocation_summary(
      actor_id,event_name,target_name,target_id,correlation_id,
      event_result,reason,details
    );
  else
    perform platform_control.validate_audit_event_v2(
      actor_id,event_name,target_name,target_id,correlation_id,
      event_result,reason,details
    );
  end if;
  insert into platform_control.audit_events(
    audit_event_id,actor_internal_user_id,event_type,target_type,
    target_internal_id,request_id,result,reason_code,sanitized_before_after
  ) values (
    event_id,actor_id,event_name,target_name,target_id,
    correlation_id,event_result,reason,details
  ) on conflict (audit_event_id) do nothing;
  select * into strict stored from platform_control.audit_events
  where audit_event_id=event_id;
  if stored.actor_internal_user_id is distinct from actor_id
     or stored.event_type is distinct from event_name
     or stored.target_type is distinct from target_name
     or stored.target_internal_id is distinct from target_id
     or stored.request_id is distinct from correlation_id
     or stored.result is distinct from event_result
     or stored.reason_code is distinct from reason
     or stored.sanitized_before_after is distinct from details
  then
    raise unique_violation using message='audit event identity collision';
  end if;
  return event_id;
end
$function$;

create function platform_control.append_partner_audit_v54(
  selected_audit_event_id uuid,
  selected_actor_id uuid,
  selected_event_name text,
  selected_target_name text,
  selected_target_id uuid,
  selected_request_id uuid,
  selected_reason text,
  selected_details jsonb
) returns void
language plpgsql
security definer
set search_path=pg_catalog,platform_control
as $function$
begin
  perform platform_control.append_audit_event(
    selected_audit_event_id,selected_actor_id,selected_event_name,
    selected_target_name,selected_target_id::text,selected_request_id,
    'completed',selected_reason,selected_details
  );
exception
  when others then
    raise exception using
      errcode='P0001',message='required_audit_unavailable';
end
$function$;

create function platform_control.create_partner_organization_v54(
  selected_partner_organization_id uuid,
  selected_actor_id uuid,
  selected_name_ciphertext bytea,
  selected_name_key_version integer,
  selected_reason text,
  selected_request_id uuid,
  selected_audit_event_id uuid
) returns uuid
language plpgsql
security definer
set search_path=pg_catalog,platform_control
as $function$
begin
  perform platform_control.require_partner_owner_v54(selected_actor_id);
  if selected_partner_organization_id is null
     or selected_name_ciphertext is null
     or octet_length(selected_name_ciphertext)<28
     or selected_name_key_version is null or selected_name_key_version<=0
     or selected_request_id is null or selected_audit_event_id is null
  then
    raise check_violation using message='partner organization invalid';
  end if;
  insert into platform_control.partner_organizations(
    partner_organization_id,status,name_ciphertext,name_key_version
  ) values (
    selected_partner_organization_id,'active',selected_name_ciphertext,
    selected_name_key_version
  );
  perform platform_control.append_partner_audit_v54(
    selected_audit_event_id,selected_actor_id,'partner_organization_created',
    'partner_organization',selected_partner_organization_id,
    selected_request_id,selected_reason,
    jsonb_build_object(
      'operation_id',selected_request_id::text,
      'partner_organization_id',selected_partner_organization_id::text,
      'status','active'
    )
  );
  return selected_partner_organization_id;
end
$function$;

create function platform_control.create_partner_operator_v54(
  selected_partner_operator_id uuid,
  selected_subject_id uuid,
  selected_partner_organization_id uuid,
  selected_actor_id uuid,
  selected_display_name_ciphertext bytea,
  selected_display_name_key_version integer,
  selected_reason text,
  selected_status text,
  selected_request_id uuid,
  selected_audit_event_id uuid
) returns table(
  partner_operator_id uuid,
  subject_id uuid,
  partner_organization_id uuid,
  status text
)
language plpgsql
security definer
set search_path=pg_catalog,platform_control
as $function$
declare
  organization_status text;
begin
  perform platform_control.require_partner_owner_v54(selected_actor_id);
  select organization.status into organization_status
  from platform_control.partner_organizations organization
  where organization.partner_organization_id=selected_partner_organization_id
  for update;
  if organization_status is distinct from 'active' then
    raise check_violation using message='organization_inactive';
  end if;
  if selected_status<>'active' or selected_partner_operator_id is null
     or selected_subject_id is null or selected_display_name_ciphertext is null
     or octet_length(selected_display_name_ciphertext)<28
     or selected_display_name_key_version is null
     or selected_display_name_key_version<=0 or selected_request_id is null
     or selected_audit_event_id is null
  then
    raise check_violation using message='partner operator invalid';
  end if;
  insert into platform_control.agent_access_subjects(
    subject_id,subject_type,status,display_name_ciphertext,
    display_name_key_version
  ) values (
    selected_subject_id,'partner_operator','active',
    selected_display_name_ciphertext,selected_display_name_key_version
  );
  insert into platform_control.partner_operators(
    partner_operator_id,subject_id,partner_organization_id,status
  ) values (
    selected_partner_operator_id,selected_subject_id,
    selected_partner_organization_id,'active'
  );
  perform platform_control.append_partner_audit_v54(
    selected_audit_event_id,selected_actor_id,'partner_operator_created',
    'partner_operator',selected_partner_operator_id,selected_request_id,
    selected_reason,jsonb_build_object(
      'operation_id',selected_request_id::text,
      'partner_operator_id',selected_partner_operator_id::text,
      'partner_organization_id',selected_partner_organization_id::text,
      'status','active','subject_id',selected_subject_id::text
    )
  );
  return query select selected_partner_operator_id,selected_subject_id,
    selected_partner_organization_id,'active'::text;
end
$function$;

create function platform_control.set_partner_organization_status_v54(
  selected_actor_id uuid,
  selected_partner_organization_id uuid,
  selected_status text,
  selected_reason text,
  selected_request_id uuid,
  selected_audit_event_id uuid
) returns table(partner_organization_id uuid,status text)
language plpgsql
security definer
set search_path=pg_catalog,platform_control
as $function$
declare
  previous_status text;
begin
  perform platform_control.require_partner_owner_v54(selected_actor_id);
  select organization.status into previous_status
  from platform_control.partner_organizations organization
  where organization.partner_organization_id=selected_partner_organization_id
  for update;
  if previous_status is null
     or selected_status not in ('active','suspended','disabled')
     or selected_request_id is null or selected_audit_event_id is null
  then
    raise check_violation using message='partner organization invalid';
  end if;
  update platform_control.partner_organizations organization
  set status=selected_status,updated_at=clock_timestamp(),
      invalidated_at=case when selected_status='active' then null
        else coalesce(organization.invalidated_at,clock_timestamp()) end
  where organization.partner_organization_id=selected_partner_organization_id;
  perform platform_control.append_partner_audit_v54(
    selected_audit_event_id,selected_actor_id,
    'partner_organization_status_changed','partner_organization',
    selected_partner_organization_id,selected_request_id,selected_reason,
    jsonb_build_object(
      'new_status',selected_status,'operation_id',selected_request_id::text,
      'partner_organization_id',selected_partner_organization_id::text,
      'previous_status',previous_status
    )
  );
  return query select selected_partner_organization_id,selected_status;
end
$function$;

create function platform_control.set_partner_operator_status_v54(
  selected_actor_id uuid,
  selected_partner_operator_id uuid,
  selected_status text,
  selected_reason text,
  selected_request_id uuid,
  selected_audit_event_id uuid
) returns table(
  partner_operator_id uuid,
  subject_id uuid,
  partner_organization_id uuid,
  status text
)
language plpgsql
security definer
set search_path=pg_catalog,platform_control
as $function$
declare
  previous_status text;
  selected_subject_id uuid;
  selected_organization_id uuid;
begin
  perform platform_control.require_partner_owner_v54(selected_actor_id);
  select operator.status,operator.subject_id,operator.partner_organization_id
  into previous_status,selected_subject_id,selected_organization_id
  from platform_control.partner_operators operator
  where operator.partner_operator_id=selected_partner_operator_id
  for update;
  if previous_status is null
     or selected_status not in ('active','suspended','disabled')
     or selected_request_id is null or selected_audit_event_id is null
  then
    raise check_violation using message='partner operator invalid';
  end if;
  update platform_control.partner_operators operator
  set status=selected_status,updated_at=clock_timestamp(),
      invalidated_at=case when selected_status='active' then null
        else coalesce(operator.invalidated_at,clock_timestamp()) end
  where operator.partner_operator_id=selected_partner_operator_id;
  perform platform_control.append_partner_audit_v54(
    selected_audit_event_id,selected_actor_id,
    'partner_operator_status_changed','partner_operator',
    selected_partner_operator_id,selected_request_id,selected_reason,
    jsonb_build_object(
      'new_status',selected_status,'operation_id',selected_request_id::text,
      'partner_operator_id',selected_partner_operator_id::text,
      'previous_status',previous_status,'subject_id',selected_subject_id::text
    )
  );
  return query select selected_partner_operator_id,selected_subject_id,
    selected_organization_id,selected_status;
end
$function$;

create function platform_control.grant_partner_fae_v54(
  selected_grant_id uuid,
  selected_actor_id uuid,
  selected_partner_operator_id uuid,
  selected_reason text,
  selected_request_id uuid,
  selected_audit_event_id uuid
) returns uuid
language plpgsql
security definer
set search_path=pg_catalog,platform_control
as $function$
declare
  selected_subject_id uuid;
  selected_operator_status text;
  selected_organization_id uuid;
  selected_organization_status text;
  selected_subject_status text;
begin
  perform platform_control.require_partner_owner_v54(selected_actor_id);
  select operator.subject_id,operator.status,operator.partner_organization_id
  into selected_subject_id,selected_operator_status,selected_organization_id
  from platform_control.partner_operators operator
  where operator.partner_operator_id=selected_partner_operator_id
  for update;
  select organization.status into selected_organization_status
  from platform_control.partner_organizations organization
  where organization.partner_organization_id=selected_organization_id
  for update;
  select subject.status into selected_subject_status
  from platform_control.agent_access_subjects subject
  where subject.subject_id=selected_subject_id for update;
  if selected_subject_status is distinct from 'active'
     or selected_organization_status is distinct from 'active'
     or selected_operator_status is distinct from 'active'
     or selected_grant_id is null or selected_request_id is null
     or selected_audit_event_id is null
  then
    raise check_violation using message='operator_inactive';
  end if;
  insert into platform_control.partner_agent_grants(
    grant_id,subject_id,agent_id,created_by_internal_user_id
  ) values (
    selected_grant_id,selected_subject_id,'ai-fae-agent',selected_actor_id
  );
  perform platform_control.append_partner_audit_v54(
    selected_audit_event_id,selected_actor_id,'partner_fae_granted',
    'agent_access_subject',selected_subject_id,selected_request_id,
    selected_reason,jsonb_build_object(
      'agent_id','ai-fae-agent','operation_id',selected_request_id::text,
      'partner_operator_id',selected_partner_operator_id::text,
      'subject_id',selected_subject_id::text
    )
  );
  return selected_grant_id;
end
$function$;

create function platform_control.revoke_partner_fae_v54(
  selected_actor_id uuid,
  selected_partner_operator_id uuid,
  selected_reason text,
  selected_request_id uuid,
  selected_audit_event_id uuid
) returns uuid
language plpgsql
security definer
set search_path=pg_catalog,platform_control
as $function$
declare
  selected_subject_id uuid;
  selected_grant_id uuid;
begin
  perform platform_control.require_partner_owner_v54(selected_actor_id);
  select operator.subject_id into selected_subject_id
  from platform_control.partner_operators operator
  where operator.partner_operator_id=selected_partner_operator_id
  for update;
  select grant_row.grant_id into selected_grant_id
  from platform_control.partner_agent_grants grant_row
  where grant_row.subject_id=selected_subject_id
    and grant_row.agent_id='ai-fae-agent' and grant_row.revoked_at is null
  for update;
  if selected_grant_id is null or selected_request_id is null
     or selected_audit_event_id is null
  then
    raise check_violation using message='fae_access_denied';
  end if;
  update platform_control.partner_agent_grants
  set revoked_at=clock_timestamp(),revoked_by_internal_user_id=selected_actor_id
  where grant_id=selected_grant_id;
  perform platform_control.append_partner_audit_v54(
    selected_audit_event_id,selected_actor_id,'partner_fae_revoked',
    'agent_access_subject',selected_subject_id,selected_request_id,
    selected_reason,jsonb_build_object(
      'agent_id','ai-fae-agent','operation_id',selected_request_id::text,
      'partner_operator_id',selected_partner_operator_id::text,
      'subject_id',selected_subject_id::text
    )
  );
  return selected_grant_id;
end
$function$;

create function platform_control.record_partner_binding_request_v54(
  selected_binding_request_id uuid,
  selected_provider_kind text,
  selected_lookup_hmac bytea,
  selected_lookup_key_version integer,
  selected_lookup_transition_versions integer[],
  selected_lookup_hmac_candidates bytea[],
  selected_provider_subject_ciphertext bytea,
  selected_encryption_key_version integer,
  selected_display_name_ciphertext bytea,
  selected_display_name_key_version integer,
  selected_verified_at timestamptz
) returns table(binding_request_id uuid,status text,expires_at timestamptz)
language plpgsql
security definer
set search_path=pg_catalog,platform_control
as $function$
#variable_conflict use_column
declare
  candidate record;
begin
  perform platform_control.require_partner_app_v54();
  if selected_binding_request_id is null or selected_provider_kind is null
     or selected_provider_kind='' or selected_provider_kind<>btrim(selected_provider_kind)
     or position(':' in selected_provider_kind)>0
     or selected_lookup_hmac is null or octet_length(selected_lookup_hmac)<>32
     or selected_lookup_key_version is null or selected_lookup_key_version<=0
     or selected_lookup_transition_versions is null
     or selected_lookup_hmac_candidates is null
     or array_ndims(selected_lookup_hmac_candidates)<>1
     or array_lower(selected_lookup_hmac_candidates,1)<>1
     or cardinality(selected_lookup_hmac_candidates)
        <>cardinality(selected_lookup_transition_versions)
     or exists (
       select 1
       from unnest(selected_lookup_hmac_candidates) lookup_value
       where lookup_value is null or octet_length(lookup_value)<>32
     )
     or not exists (
       select 1
       from unnest(
         selected_lookup_transition_versions,
         selected_lookup_hmac_candidates
       ) item(key_version,lookup_value)
       where item.key_version=selected_lookup_key_version
         and item.lookup_value=selected_lookup_hmac
     )
     or selected_provider_subject_ciphertext is null
     or octet_length(selected_provider_subject_ciphertext)<28
     or selected_encryption_key_version is null
     or selected_encryption_key_version<=0 or selected_verified_at is null
     or num_nonnulls(
       selected_display_name_ciphertext,selected_display_name_key_version
     ) not in (0,2)
     or (
       selected_display_name_ciphertext is not null
       and octet_length(selected_display_name_ciphertext)<28
     )
     or (
       selected_display_name_key_version is not null
       and selected_display_name_key_version<=0
     )
  then
    raise check_violation using message='partner binding request invalid';
  end if;
  perform platform_control.require_partner_identity_key_policy_v54(
    selected_lookup_transition_versions
  );
  for candidate in
    select item.key_version,item.lookup_value
    from unnest(
      selected_lookup_transition_versions,
      selected_lookup_hmac_candidates
    ) item(key_version,lookup_value)
    order by item.key_version
  loop
    perform pg_advisory_xact_lock(hashtextextended(
      selected_provider_kind || ':' || candidate.key_version::text || ':' ||
      encode(candidate.lookup_value,'hex'),54
    ));
  end loop;
  if exists (
    select 1
    from platform_control.partner_provider_identities identity
    join unnest(
      selected_lookup_transition_versions,
      selected_lookup_hmac_candidates
    ) mapped_identity_candidate(key_version,lookup_value)
      on identity.lookup_key_version=mapped_identity_candidate.key_version
      and identity.provider_subject_lookup_hmac
        =mapped_identity_candidate.lookup_value
    where identity.provider_kind=selected_provider_kind
  ) then
    raise unique_violation using
      message='partner_identity_already_linked';
  end if;
  update platform_control.partner_identity_binding_requests request
  set status='expired',resolved_at=clock_timestamp()
  where request.provider_kind=selected_provider_kind
    and exists (
      select 1
      from unnest(
        request.lookup_transition_versions,
        request.provider_subject_lookup_hmac_candidates
      ) stored(key_version,lookup_value)
      join unnest(
        selected_lookup_transition_versions,
        selected_lookup_hmac_candidates
      ) selected(key_version,lookup_value) using (key_version,lookup_value)
    )
    and request.status='pending' and request.expires_at<=clock_timestamp();
  if not exists (
    select 1
    from platform_control.partner_identity_binding_requests request
    where request.provider_kind=selected_provider_kind
      and request.status='pending'
      and exists (
        select 1
        from unnest(
          request.lookup_transition_versions,
          request.provider_subject_lookup_hmac_candidates
        ) stored(key_version,lookup_value)
        join unnest(
          selected_lookup_transition_versions,
          selected_lookup_hmac_candidates
        ) selected(key_version,lookup_value) using (key_version,lookup_value)
      )
  ) then
    insert into platform_control.partner_identity_binding_requests(
      binding_request_id,provider_kind,provider_subject_lookup_hmac,
      lookup_key_version,lookup_transition_versions,
      provider_subject_lookup_hmac_candidates,provider_subject_ciphertext,
      encryption_key_version,display_name_ciphertext,display_name_key_version,
      verified_at,status,expires_at
    ) values (
      selected_binding_request_id,selected_provider_kind,selected_lookup_hmac,
      selected_lookup_key_version,selected_lookup_transition_versions,
      selected_lookup_hmac_candidates,selected_provider_subject_ciphertext,
      selected_encryption_key_version,selected_display_name_ciphertext,
      selected_display_name_key_version,selected_verified_at,'pending',
      clock_timestamp()+interval '24 hours'
    ) on conflict (
      provider_kind,provider_subject_lookup_hmac,lookup_key_version
    ) where status='pending' do nothing;
  end if;
  return query
  select request.binding_request_id,request.status,request.expires_at
  from platform_control.partner_identity_binding_requests request
  where request.provider_kind=selected_provider_kind
    and exists (
      select 1
      from unnest(
        request.lookup_transition_versions,
        request.provider_subject_lookup_hmac_candidates
      ) stored(key_version,lookup_value)
      join unnest(
        selected_lookup_transition_versions,
        selected_lookup_hmac_candidates
      ) selected(key_version,lookup_value) using (key_version,lookup_value)
    )
    and request.status='pending'
  for update;
end
$function$;

create function platform_control.decide_partner_fae_access_v54(
  selected_subject_id uuid
) returns text
language plpgsql
security definer
set search_path=pg_catalog,platform_control
as $function$
declare
  selected_subject_status text;
  selected_organization_status text;
  selected_operator_status text;
  fae_granted boolean;
begin
  perform platform_control.require_partner_app_v54();
  select subject.status,organization.status,operator.status,
    exists(
      select 1 from platform_control.partner_agent_grants grant_row
      where grant_row.subject_id=subject.subject_id
        and grant_row.agent_id='ai-fae-agent' and grant_row.revoked_at is null
    )
  into selected_subject_status,selected_organization_status,
    selected_operator_status,fae_granted
  from platform_control.agent_access_subjects subject
  left join platform_control.partner_operators operator
    on operator.subject_id=subject.subject_id
  left join platform_control.partner_organizations organization
    on organization.partner_organization_id=operator.partner_organization_id
  where subject.subject_id=selected_subject_id;
  if selected_subject_status is distinct from 'active' then
    return 'subject_inactive';
  elsif selected_organization_status is null
     or selected_operator_status is null
  then
    return 'fae_access_denied';
  elsif selected_organization_status<>'active' then
    return 'organization_inactive';
  elsif selected_operator_status<>'active' then
    return 'operator_inactive';
  elsif not fae_granted then
    return 'fae_access_denied';
  end if;
  return 'active';
end
$function$;

create function platform_control.link_partner_binding_request_v54(
  selected_provider_identity_id uuid,
  selected_actor_id uuid,
  selected_binding_request_id uuid,
  selected_partner_operator_id uuid,
  selected_reason text,
  selected_request_id uuid,
  selected_audit_event_id uuid
) returns table(
  subject_id uuid,
  partner_operator_id uuid,
  partner_organization_id uuid,
  provider_identity_id uuid
)
language plpgsql
security definer
set search_path=pg_catalog,platform_control
as $function$
declare
  binding platform_control.partner_identity_binding_requests%rowtype;
  locked_candidate record;
  selected_subject_id uuid;
  selected_operator_status text;
  selected_organization_id uuid;
  selected_organization_status text;
  selected_subject_status text;
begin
  perform platform_control.require_partner_owner_v54(selected_actor_id);
  select * into binding
  from platform_control.partner_identity_binding_requests request
  where request.binding_request_id=selected_binding_request_id;
  if not found or binding.status<>'pending'
     or binding.expires_at<=clock_timestamp()
  then
    raise check_violation using message='binding_request_unavailable';
  end if;
  perform platform_control.require_partner_identity_key_policy_v54(
    binding.lookup_transition_versions
  );
  for locked_candidate in
    select item.key_version,item.lookup_value
    from unnest(
      binding.lookup_transition_versions,
      binding.provider_subject_lookup_hmac_candidates
    ) item(key_version,lookup_value)
    order by item.key_version
  loop
    perform pg_advisory_xact_lock(hashtextextended(
      binding.provider_kind || ':' || locked_candidate.key_version::text || ':' ||
      encode(locked_candidate.lookup_value,'hex'),54
    ));
  end loop;
  select * into binding
  from platform_control.partner_identity_binding_requests request
  where request.binding_request_id=selected_binding_request_id
  for update;
  if not found or binding.status<>'pending'
     or binding.expires_at<=clock_timestamp()
  then
    raise check_violation using message='binding_request_unavailable';
  end if;
  select operator.subject_id,operator.status,operator.partner_organization_id
  into selected_subject_id,selected_operator_status,selected_organization_id
  from platform_control.partner_operators operator
  where operator.partner_operator_id=selected_partner_operator_id
  for update;
  select organization.status into selected_organization_status
  from platform_control.partner_organizations organization
  where organization.partner_organization_id=selected_organization_id
  for update;
  select subject.status into selected_subject_status
  from platform_control.agent_access_subjects subject
  where subject.subject_id=selected_subject_id for update;
  if selected_operator_status is distinct from 'active'
     or selected_organization_status is distinct from 'active'
     or selected_subject_status is distinct from 'active'
  then
    raise check_violation using message='operator_inactive';
  end if;
  perform 1 from platform_control.partner_provider_identities identity
  join unnest(
    binding.lookup_transition_versions,
    binding.provider_subject_lookup_hmac_candidates
  ) mapped_candidate(key_version,lookup_value)
    on identity.lookup_key_version=mapped_candidate.key_version
    and identity.provider_subject_lookup_hmac=mapped_candidate.lookup_value
  where identity.provider_kind=binding.provider_kind
  for update;
  if found then
    raise unique_violation using message='partner_identity_conflict';
  end if;
  insert into platform_control.partner_provider_identities(
    provider_identity_id,partner_operator_id,provider_kind,
    provider_subject_lookup_hmac,lookup_key_version,
    provider_subject_ciphertext,encryption_key_version,verified_at
  ) values (
    selected_provider_identity_id,selected_partner_operator_id,
    binding.provider_kind,binding.provider_subject_lookup_hmac,
    binding.lookup_key_version,binding.provider_subject_ciphertext,
    binding.encryption_key_version,binding.verified_at
  );
  update platform_control.partner_identity_binding_requests
  set status='linked',resolved_at=clock_timestamp(),
      linked_partner_operator_id=selected_partner_operator_id
  where binding_request_id=selected_binding_request_id;
  perform platform_control.append_partner_audit_v54(
    selected_audit_event_id,selected_actor_id,'partner_identity_linked',
    'partner_binding_request',selected_binding_request_id,
    selected_request_id,selected_reason,jsonb_build_object(
      'binding_request_id',selected_binding_request_id::text,
      'operation_id',selected_request_id::text,
      'partner_operator_id',selected_partner_operator_id::text,
      'provider_identity_id',selected_provider_identity_id::text,
      'subject_id',selected_subject_id::text
    )
  );
  return query select selected_subject_id,selected_partner_operator_id,
    selected_organization_id,selected_provider_identity_id;
end
$function$;

create function platform_control.reject_partner_binding_request_v54(
  selected_actor_id uuid,
  selected_binding_request_id uuid,
  selected_reason text,
  selected_request_id uuid,
  selected_audit_event_id uuid
) returns table(
  binding_request_id uuid,
  status text,
  expires_at timestamptz
)
language plpgsql
security definer
set search_path=pg_catalog,platform_control
as $function$
declare
  binding platform_control.partner_identity_binding_requests%rowtype;
begin
  perform platform_control.require_partner_owner_v54(selected_actor_id);
  select request.* into binding
  from platform_control.partner_identity_binding_requests request
  where request.binding_request_id=selected_binding_request_id
  for update;
  if binding.binding_request_id is null
     or binding.status<>'pending'
     or binding.expires_at<=clock_timestamp()
     or selected_request_id is null
     or selected_audit_event_id is null
  then
    raise check_violation using message='binding_request_unavailable';
  end if;
  update platform_control.partner_identity_binding_requests request
  set status='rejected',resolved_at=clock_timestamp()
  where request.binding_request_id=selected_binding_request_id;
  perform platform_control.append_partner_audit_v54(
    selected_audit_event_id,selected_actor_id,'partner_identity_rejected',
    'partner_binding_request',selected_binding_request_id,
    selected_request_id,selected_reason,jsonb_build_object(
      'binding_request_id',selected_binding_request_id::text,
      'operation_id',selected_request_id::text,
      'provider_kind',binding.provider_kind,'status','rejected'
    )
  );
  return query select selected_binding_request_id,'rejected'::text,
    binding.expires_at;
end
$function$;

revoke all on platform_control.partner_organizations from public;
revoke all on platform_control.partner_operators from public;
revoke all on platform_control.partner_provider_identities from public;
revoke all on platform_control.partner_identity_binding_requests from public;
revoke all on platform_control.partner_agent_grants from public;
revoke all on platform_control.partner_login_attempts from public;
revoke all on function platform_control.guard_partner_binding_request_v54()
  from public;
revoke all on function platform_control.guard_partner_operator_subject_v54()
  from public;
revoke all on function platform_control.guard_partner_subject_type_v54()
  from public;
revoke all on function platform_control.require_partner_app_v54() from public;
revoke all on function platform_control.require_partner_identity_key_policy_v54(
  integer[]
) from public;
revoke all on function platform_control.require_partner_owner_v54(uuid)
  from public;
revoke all on function platform_control.validate_partner_audit_event_v54(
  uuid,text,text,text,uuid,text,text,jsonb
) from public;
revoke all on function platform_control.append_partner_audit_v54(
  uuid,uuid,text,text,uuid,uuid,text,jsonb
) from public;
revoke all on function platform_control.create_partner_organization_v54(
  uuid,uuid,bytea,integer,text,uuid,uuid
) from public;
revoke all on function platform_control.create_partner_operator_v54(
  uuid,uuid,uuid,uuid,bytea,integer,text,text,uuid,uuid
) from public;
revoke all on function platform_control.set_partner_organization_status_v54(
  uuid,uuid,text,text,uuid,uuid
) from public;
revoke all on function platform_control.set_partner_operator_status_v54(
  uuid,uuid,text,text,uuid,uuid
) from public;
revoke all on function platform_control.grant_partner_fae_v54(
  uuid,uuid,uuid,text,uuid,uuid
) from public;
revoke all on function platform_control.revoke_partner_fae_v54(
  uuid,uuid,text,uuid,uuid
) from public;
revoke all on function platform_control.record_partner_binding_request_v54(
  uuid,text,bytea,integer,integer[],bytea[],bytea,integer,bytea,integer,
  timestamptz
) from public;
revoke all on function platform_control.decide_partner_fae_access_v54(uuid)
  from public;
revoke all on function platform_control.link_partner_binding_request_v54(
  uuid,uuid,uuid,uuid,text,uuid,uuid
) from public;
revoke all on function platform_control.reject_partner_binding_request_v54(
  uuid,uuid,text,uuid,uuid
) from public;

do $migration$
declare
  selected_app name;
  role_name name;
  table_name text;
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
      message='Partner operator migration owner invalid';
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
    foreach table_name in array array[
      'partner_organizations','partner_operators',
      'partner_provider_identities','partner_identity_binding_requests',
      'partner_agent_grants','partner_login_attempts'
    ] loop
      execute format(
        'revoke all on platform_control.%I from %I',table_name,role_name
      );
    end loop;
    execute format(
      'revoke all on function platform_control.require_partner_app_v54() from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.require_partner_identity_key_policy_v54(integer[]) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.guard_partner_operator_subject_v54() from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.guard_partner_subject_type_v54() from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.require_partner_owner_v54(uuid) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.validate_partner_audit_event_v54(uuid,text,text,text,uuid,text,text,jsonb) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.append_partner_audit_v54(uuid,uuid,text,text,uuid,uuid,text,jsonb) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.create_partner_organization_v54(uuid,uuid,bytea,integer,text,uuid,uuid) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.create_partner_operator_v54(uuid,uuid,uuid,uuid,bytea,integer,text,text,uuid,uuid) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.set_partner_organization_status_v54(uuid,uuid,text,text,uuid,uuid) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.set_partner_operator_status_v54(uuid,uuid,text,text,uuid,uuid) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.grant_partner_fae_v54(uuid,uuid,uuid,text,uuid,uuid) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.revoke_partner_fae_v54(uuid,uuid,text,uuid,uuid) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.record_partner_binding_request_v54(uuid,text,bytea,integer,integer[],bytea[],bytea,integer,bytea,integer,timestamptz) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.decide_partner_fae_access_v54(uuid) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.link_partner_binding_request_v54(uuid,uuid,uuid,uuid,text,uuid,uuid) from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.reject_partner_binding_request_v54(uuid,uuid,text,uuid,uuid) from %I',
      role_name
    );
  end loop;

  execute format(
    'grant select on platform_control.partner_organizations, '
    'platform_control.partner_operators, '
    'platform_control.partner_provider_identities, '
    'platform_control.partner_identity_binding_requests, '
    'platform_control.partner_agent_grants, '
    'platform_control.partner_login_attempts to %I',selected_app
  );
  execute format(
    'grant execute on function platform_control.create_partner_organization_v54(uuid,uuid,bytea,integer,text,uuid,uuid) to %I',
    selected_app
  );
  execute format(
    'grant execute on function platform_control.create_partner_operator_v54(uuid,uuid,uuid,uuid,bytea,integer,text,text,uuid,uuid) to %I',
    selected_app
  );
  execute format(
    'grant execute on function platform_control.set_partner_organization_status_v54(uuid,uuid,text,text,uuid,uuid) to %I',
    selected_app
  );
  execute format(
    'grant execute on function platform_control.set_partner_operator_status_v54(uuid,uuid,text,text,uuid,uuid) to %I',
    selected_app
  );
  execute format(
    'grant execute on function platform_control.grant_partner_fae_v54(uuid,uuid,uuid,text,uuid,uuid) to %I',
    selected_app
  );
  execute format(
    'grant execute on function platform_control.revoke_partner_fae_v54(uuid,uuid,text,uuid,uuid) to %I',
    selected_app
  );
  execute format(
    'grant execute on function platform_control.require_partner_identity_key_policy_v54(integer[]) to %I',
    selected_app
  );
  execute format(
    'grant execute on function platform_control.record_partner_binding_request_v54(uuid,text,bytea,integer,integer[],bytea[],bytea,integer,bytea,integer,timestamptz) to %I',
    selected_app
  );
  execute format(
    'grant execute on function platform_control.decide_partner_fae_access_v54(uuid) to %I',
    selected_app
  );
  execute format(
    'grant execute on function platform_control.link_partner_binding_request_v54(uuid,uuid,uuid,uuid,text,uuid,uuid) to %I',
    selected_app
  );
  execute format(
    'grant execute on function platform_control.reject_partner_binding_request_v54(uuid,uuid,text,uuid,uuid) to %I',
    selected_app
  );
end
$migration$;
