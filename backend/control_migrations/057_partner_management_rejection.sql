create or replace function platform_control.validate_partner_audit_event_v54(
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

revoke all on function platform_control.reject_partner_binding_request_v54(
  uuid,uuid,text,uuid,uuid
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
      message='Partner management rejection migration owner invalid';
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
      'revoke all on function platform_control.reject_partner_binding_request_v54(uuid,uuid,text,uuid,uuid) from %I',
      role_name
    );
  end loop;

  execute format(
    'grant execute on function platform_control.reject_partner_binding_request_v54(uuid,uuid,text,uuid,uuid) to %I',
    selected_app
  );
end
$migration$;
