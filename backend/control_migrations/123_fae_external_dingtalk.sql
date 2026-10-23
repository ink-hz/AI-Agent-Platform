-- Personal DingTalk identities are server-provisioned FAE-only principals.
-- No owner identity is fabricated and the existing audit dispatcher is unchanged.
alter table platform_control.partner_organizations add column external_provider_kind text
  check (external_provider_kind is null or external_provider_kind='dingtalk_external');
create unique index one_external_dingtalk_realm_v123
  on platform_control.partner_organizations(external_provider_kind)
  where external_provider_kind is not null;
alter table platform_control.partner_agent_grants
  alter column created_by_internal_user_id drop not null,
  add column grant_origin text not null default 'owner',
  add constraint partner_grant_origin_v123 check (
    (grant_origin='owner' and created_by_internal_user_id is not null)
    or (grant_origin='external_oauth' and created_by_internal_user_id is null)
  );
alter table platform_control.partner_login_attempts
  add column browser_nonce_digest bytea check (octet_length(browser_nonce_digest)=32),
  add column provisioned_subject_id uuid references platform_control.agent_access_subjects(subject_id),
  add column provisioned_at timestamptz,
  add constraint external_attempt_provision_complete_v123 check (
    num_nonnulls(provisioned_subject_id,provisioned_at) in (0,2)
  );

create function platform_control.create_external_dingtalk_attempt_v123(
  selected_state bytea, selected_version integer, selected_nonce bytea
) returns uuid language plpgsql security definer
set search_path=pg_catalog,platform_control as $function$
declare attempt_id uuid := gen_random_uuid(); moment timestamptz := clock_timestamp();
begin
  perform platform_control.require_partner_app_v54();
  if selected_state is null or octet_length(selected_state)<>32
     or selected_nonce is null or octet_length(selected_nonce)<>32
     or selected_version is null or selected_version<=0 then
    raise check_violation using message='partner_auth_state_invalid';
  end if;
  insert into platform_control.partner_login_attempts(
    login_attempt_id,provider_kind,state_digest,state_key_version,browser_nonce_digest,
    status,created_at,expires_at
  ) values (attempt_id,'dingtalk_external',selected_state,selected_version,selected_nonce,
    'pending',moment,moment+interval '5 minutes');
  return attempt_id;
end $function$;

create function platform_control.consume_external_dingtalk_attempt_v123(
  selected_state bytea, selected_version integer, selected_nonce bytea
) returns uuid language plpgsql security definer
set search_path=pg_catalog,platform_control as $function$
declare attempt platform_control.partner_login_attempts%rowtype;
begin
  perform platform_control.require_partner_app_v54();
  select * into attempt from platform_control.partner_login_attempts a
  where a.provider_kind='dingtalk_external' and a.state_digest=selected_state
    and a.state_key_version=selected_version and a.browser_nonce_digest=selected_nonce
  for update;
  if attempt.login_attempt_id is null then
    raise check_violation using message='partner_auth_state_invalid';
  end if;
  if attempt.status<>'pending' then
    raise check_violation using message='partner_auth_state_replay';
  end if;
  if attempt.expires_at<=clock_timestamp() then
    raise check_violation using message='partner_auth_state_expired';
  end if;
  update platform_control.partner_login_attempts set status='consumed',consumed_at=clock_timestamp()
  where login_attempt_id=attempt.login_attempt_id;
  return attempt.login_attempt_id;
end $function$;

create function platform_control.provision_external_dingtalk_v123(
  selected_attempt uuid, selected_nonce bytea,
  selected_versions integer[], selected_lookups bytea[],
  selected_lookup bytea, selected_lookup_version integer,
  selected_ciphertext bytea, selected_encryption_version integer,
  selected_subject uuid, selected_display bytea, selected_display_version integer,
  selected_realm_display bytea, selected_realm_display_version integer,
  selected_verified_at timestamptz
) returns table(subject_id uuid,partner_operator_id uuid,partner_organization_id uuid)
language plpgsql security definer
set search_path=pg_catalog,platform_control as $function$
declare
  attempt platform_control.partner_login_attempts%rowtype;
  identity_row platform_control.partner_provider_identities%rowtype;
  operator_row platform_control.partner_operators%rowtype;
  subject_row platform_control.agent_access_subjects%rowtype;
  realm platform_control.partner_organizations%rowtype;
  realm_id constant uuid := 'c6cb4d5c-3d10-4bc7-b487-f6ffdf724a81';
  identity_count integer;
  candidate record;
  moment timestamptz := clock_timestamp();
begin
  perform platform_control.require_partner_app_v54();
  perform platform_control.require_partner_identity_key_policy_v54(selected_versions);
  select * into attempt from platform_control.partner_login_attempts a
    where a.login_attempt_id=selected_attempt for update;
  moment := clock_timestamp();
  if attempt.login_attempt_id is null or attempt.provider_kind<>'dingtalk_external'
    or attempt.status<>'consumed' or attempt.browser_nonce_digest is distinct from selected_nonce
    or selected_nonce is null or octet_length(selected_nonce)<>32
    or attempt.provisioned_at is not null or attempt.consumed_at<moment-interval '60 seconds'
    or attempt.consumed_at>moment or attempt.expires_at<=moment
    or selected_verified_at is null or selected_verified_at<moment-interval '60 seconds'
    or selected_verified_at>moment or selected_verified_at<attempt.created_at then
    raise check_violation using message='partner_auth_state_invalid';
  end if;
  if selected_subject is null or selected_ciphertext is null or octet_length(selected_ciphertext)<28
    or selected_encryption_version is null or selected_encryption_version<=0
    or selected_display is null or octet_length(selected_display)<28
    or selected_display_version is null or selected_display_version<=0
    or selected_realm_display is null or octet_length(selected_realm_display)<28
    or selected_realm_display_version is null or selected_realm_display_version<=0
    or selected_lookups is null or array_ndims(selected_lookups)<>1
    or array_lower(selected_lookups,1)<>1
    or cardinality(selected_lookups)<>cardinality(selected_versions)
    or array_position(selected_lookups,null) is not null
    or exists(select 1 from unnest(selected_lookups) v where octet_length(v)<>32)
    or not exists(select 1 from unnest(selected_versions,selected_lookups) c(v,h)
      where c.v=selected_lookup_version and c.h=selected_lookup) then
    raise check_violation using message='partner_identity_conflict';
  end if;
  -- Lock every transition candidate, in deterministic order. The key policy lock
  -- also serializes rotation, so readers cannot race an older lookup version.
  for candidate in select v,h from unnest(selected_versions,selected_lookups) c(v,h) order by v,h loop
    perform pg_advisory_xact_lock(hashtextextended('dingtalk_external:'||candidate.v||':'||encode(candidate.h,'hex'),0));
  end loop;
  select count(*) into identity_count from platform_control.partner_provider_identities i
    join unnest(selected_versions,selected_lookups) c(v,h)
      on i.lookup_key_version=c.v and i.provider_subject_lookup_hmac=c.h
    where i.provider_kind='dingtalk_external';
  if identity_count>1 then raise check_violation using message='partner_identity_conflict'; end if;
  select i.* into identity_row from platform_control.partner_provider_identities i
    join unnest(selected_versions,selected_lookups) c(v,h)
      on i.lookup_key_version=c.v and i.provider_subject_lookup_hmac=c.h
    where i.provider_kind='dingtalk_external' for update of i;
  select * into realm from platform_control.partner_organizations o
    where o.partner_organization_id=realm_id for update;
  if realm.partner_organization_id is null then
    insert into platform_control.partner_organizations(partner_organization_id,status,name_ciphertext,name_key_version,external_provider_kind)
      values(realm_id,'active',selected_realm_display,selected_realm_display_version,'dingtalk_external');
  elsif realm.status<>'active' or realm.invalidated_at is not null
      or realm.external_provider_kind is distinct from 'dingtalk_external' then
    raise check_violation using message='organization_inactive';
  end if;
  if identity_row.provider_identity_id is not null then
    if identity_row.revoked_at is not null then
      raise check_violation using message='provider_identity_inactive';
    end if;
    select * into operator_row from platform_control.partner_operators o
      where o.partner_operator_id=identity_row.partner_operator_id for update;
    select * into subject_row from platform_control.agent_access_subjects s
      where s.subject_id=operator_row.subject_id for update;
    if operator_row.partner_organization_id<>realm_id or operator_row.subject_id<>selected_subject then
      raise check_violation using message='partner_identity_conflict';
    end if;
    if operator_row.status<>'active' or operator_row.invalidated_at is not null then
      raise check_violation using message='operator_inactive';
    end if;
    if subject_row.status<>'active' or subject_row.invalidated_at is not null then
      raise check_violation using message='subject_inactive';
    end if;
    perform 1 from platform_control.partner_agent_grants g where g.subject_id=selected_subject
      and g.agent_id='ai-fae-agent' and g.revoked_at is null for update;
    if not found then raise check_violation using message='fae_access_denied'; end if;
    update platform_control.partner_provider_identities set verified_at=selected_verified_at,
      provider_subject_ciphertext=selected_ciphertext,encryption_key_version=selected_encryption_version
      where provider_identity_id=identity_row.provider_identity_id;
    update platform_control.agent_access_subjects set display_name_ciphertext=selected_display,
      display_name_key_version=selected_display_version,updated_at=moment where agent_access_subjects.subject_id=selected_subject;
  else
    -- Previous pending/rejected manual bindings are never implicitly resurrected.
    if exists(select 1 from platform_control.partner_identity_binding_requests r
      join unnest(selected_versions,selected_lookups) c(v,h)
      on r.lookup_key_version=c.v and r.provider_subject_lookup_hmac=c.h
      where r.provider_kind='dingtalk_external') then
      raise check_violation using message='partner_identity_conflict';
    end if;
    insert into platform_control.agent_access_subjects(subject_id,subject_type,status,display_name_ciphertext,display_name_key_version)
      values(selected_subject,'partner_operator','active',selected_display,selected_display_version);
    operator_row.partner_operator_id := gen_random_uuid();
    insert into platform_control.partner_operators(partner_operator_id,subject_id,partner_organization_id,status)
      values(operator_row.partner_operator_id,selected_subject,realm_id,'active');
    insert into platform_control.partner_provider_identities(provider_identity_id,partner_operator_id,provider_kind,
      provider_subject_lookup_hmac,lookup_key_version,provider_subject_ciphertext,encryption_key_version,verified_at)
      values(gen_random_uuid(),operator_row.partner_operator_id,'dingtalk_external',selected_lookup,selected_lookup_version,
        selected_ciphertext,selected_encryption_version,selected_verified_at);
    insert into platform_control.partner_agent_grants(grant_id,subject_id,agent_id,created_by_internal_user_id,grant_origin)
      values(gen_random_uuid(),selected_subject,'ai-fae-agent',null,'external_oauth');
  end if;
  update platform_control.partner_login_attempts set provisioned_subject_id=selected_subject,provisioned_at=moment
    where login_attempt_id=selected_attempt;
  -- Strict system audit: all event fields and metadata are server-selected.
  -- This does not expand append_audit_event or any owner-only mutation contract.
  insert into platform_control.audit_events(audit_event_id,actor_internal_user_id,event_type,target_type,
    target_internal_id,request_id,result,reason_code,sanitized_before_after)
    values(gen_random_uuid(),null,'fae_external_authenticated','agent_access_subject',selected_subject::text,
      selected_attempt,'completed','verified_external_oauth',jsonb_build_object(
        'agent_id','ai-fae-agent','provider_kind','dingtalk_external','subject_id',selected_subject::text));
  return query select selected_subject,operator_row.partner_operator_id,realm_id;
end $function$;

create function platform_control.read_external_dingtalk_identity_v123(
  selected_actor uuid, selected_subject uuid, selected_request uuid
) returns table(provider_kind text,provider_subject_lookup_hmac bytea,lookup_key_version integer,
  provider_subject_ciphertext bytea,encryption_key_version integer,verified_at timestamptz,
  revoked_at timestamptz,display_name_ciphertext bytea,display_name_key_version integer)
language plpgsql security definer set search_path=pg_catalog,platform_control as $function$
begin
  perform platform_control.require_partner_owner_v54(selected_actor);
  if selected_subject is null or selected_request is null then
    raise check_violation using message='partner_identity_conflict';
  end if;
  insert into platform_control.audit_events(audit_event_id,actor_internal_user_id,event_type,target_type,
    target_internal_id,request_id,result,reason_code,sanitized_before_after)
    values(gen_random_uuid(),selected_actor,'fae_external_identity_read','agent_access_subject',
      selected_subject::text,selected_request,'completed','owner_identity_trace',
      jsonb_build_object('provider_kind','dingtalk_external','subject_id',selected_subject::text));
  return query select i.provider_kind,i.provider_subject_lookup_hmac,i.lookup_key_version,
    i.provider_subject_ciphertext,i.encryption_key_version,i.verified_at,i.revoked_at,
    s.display_name_ciphertext,s.display_name_key_version
    from platform_control.partner_provider_identities i
    join platform_control.partner_operators o using(partner_operator_id)
    join platform_control.agent_access_subjects s on s.subject_id=o.subject_id
    where o.subject_id=selected_subject and i.provider_kind='dingtalk_external';
end $function$;

do $migration$
declare function_id regprocedure; role_name name; selected_app name;
begin
  selected_app := case when current_database()='agent_platform_control' and current_user='platform_control_owner'
    then 'platform_control_app' when current_database()='agent_platform_control_preview' and current_user='platform_control_owner_preview'
    then 'platform_control_app_preview' else null end;
  if selected_app is null then raise insufficient_privilege using message='External DingTalk migration owner invalid'; end if;
  for function_id in select oid::regprocedure from pg_proc where pronamespace='platform_control'::regnamespace
    and proname in ('create_external_dingtalk_attempt_v123','consume_external_dingtalk_attempt_v123',
      'provision_external_dingtalk_v123','read_external_dingtalk_identity_v123') loop
    execute format('revoke all on function %s from public',function_id);
    for role_name in select rolname from pg_roles where rolname like 'platform_%' loop
      execute format('revoke all on function %s from %I',function_id,role_name);
    end loop;
    execute format('grant execute on function %s to %I',function_id,selected_app);
  end loop;
end $migration$;
