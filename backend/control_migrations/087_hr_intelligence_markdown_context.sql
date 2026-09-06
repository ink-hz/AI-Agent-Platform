alter table platform_hr.intelligence_bundles
  drop constraint if exists intelligence_bundles_schema_version_check;
alter table platform_hr.intelligence_bundles
  add constraint intelligence_bundles_schema_version_check
  check (schema_version in (1,2));
alter table platform_hr.intelligence_bundles
  add column agent_chunk_index jsonb not null default '[]'::jsonb
  check (jsonb_typeof(agent_chunk_index)='array');
alter table platform_hr.intelligence_bundles
  add column agent_document_index jsonb not null default '{}'::jsonb
  check (jsonb_typeof(agent_document_index)='object');

create table platform_hr.conversation_intelligence_bundle_references (
  reference_id uuid primary key,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  client_request_id uuid not null,
  conversation_id uuid not null,
  turn_id uuid not null,
  bundle_id uuid not null,
  observed_at timestamptz not null,
  context_document jsonb not null check (
    jsonb_typeof(context_document)='object'
    and octet_length(context_document::text)<=32768
    and context_document->>'bundle_id'=bundle_id::text
  ),
  created_at timestamptz not null default now(),
  foreign key (conversation_id,owner_internal_user_id)
    references platform_control.conversations(
      conversation_id,owner_internal_user_id
    ),
  foreign key (conversation_id,turn_id)
    references platform_control.conversation_turns(conversation_id,turn_id),
  foreign key (bundle_id,owner_internal_user_id)
    references platform_hr.intelligence_bundles(
      bundle_id,owner_internal_user_id
    ) on delete restrict,
  unique(owner_internal_user_id,client_request_id),
  unique(owner_internal_user_id,conversation_id,turn_id)
);

create function platform_hr.guard_conversation_intelligence_reference_v87()
returns trigger language plpgsql
set search_path=pg_catalog,platform_hr
as $function$
begin
  raise check_violation using message='conversation intelligence reference is immutable';
end
$function$;

create trigger guard_conversation_intelligence_reference_v87
before update or delete
on platform_hr.conversation_intelligence_bundle_references
for each row execute function
  platform_hr.guard_conversation_intelligence_reference_v87();

create function platform_hr.import_intelligence_bundle_v87(
  selected_owner_internal_user_id uuid,
  selected_bundle_id uuid,
  selected_manifest_sha256 text,
  selected_bundle_locator text,
  selected_generated_at timestamptz,
  selected_manifest jsonb,
  selected_source_catalog jsonb,
  selected_source_coverage jsonb,
  selected_jobs jsonb,
  selected_aggregates jsonb,
  selected_analysis jsonb,
  selected_analysis_usage jsonb,
  selected_evidence_index jsonb,
  selected_agent_chunk_index jsonb,
  selected_agent_document_index jsonb
) returns platform_hr.intelligence_bundles
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.intelligence_bundles%rowtype;
declare selected_job jsonb;
declare selected_company_count integer;
declare selected_job_count integer;
declare selected_schema_version integer;
begin
  if session_user<>'platform_control_app'
     or current_database()<>'agent_platform_control' then
    raise insufficient_privilege;
  end if;
  selected_schema_version=(selected_manifest->>'schema_version')::integer;
  if selected_owner_internal_user_id is null
    or selected_bundle_id is null
    or selected_manifest_sha256 !~ '^[a-f0-9]{64}$'
    or selected_bundle_locator <> 'bundles/' || selected_bundle_id::text
    or selected_generated_at is null
    or jsonb_typeof(selected_manifest)<>'object'
    or (selected_manifest->>'bundle_id')::uuid<>selected_bundle_id
    or selected_schema_version not in (1,2)
    or jsonb_typeof(selected_source_catalog)<>'object'
    or jsonb_typeof(selected_source_coverage)<>'object'
    or jsonb_typeof(selected_jobs)<>'array'
    or jsonb_array_length(selected_jobs)>100000
    or jsonb_typeof(selected_aggregates)<>'object'
    or jsonb_typeof(selected_analysis)<>'array'
    or jsonb_array_length(selected_analysis)>10000
    or jsonb_typeof(selected_analysis_usage)<>'array'
    or jsonb_typeof(selected_evidence_index)<>'array'
    or jsonb_array_length(selected_evidence_index)>100000
    or jsonb_typeof(selected_agent_chunk_index)<>'array'
    or jsonb_array_length(selected_agent_chunk_index)>100000
    or jsonb_typeof(selected_agent_document_index)<>'object'
    or (selected_schema_version=1 and (
      selected_agent_chunk_index<>'[]'::jsonb
      or selected_agent_document_index<>'{}'::jsonb
    ))
    or (selected_schema_version=2 and (
      (selected_manifest->>'agent_chunk_count')::integer<>
        jsonb_array_length(selected_agent_chunk_index)
      or selected_manifest->'agent_document_index'<>
        selected_agent_document_index
    )) then
    raise check_violation using message='HR intelligence bundle import invalid';
  end if;
  selected_company_count=jsonb_array_length(
    selected_source_catalog->'companies'
  );
  selected_job_count=jsonb_array_length(selected_jobs);
  if selected_company_count not between 1 and 100
    or (selected_manifest->>'company_count')::integer<>selected_company_count
    or (selected_manifest->>'job_count')::integer<>selected_job_count
    or (selected_manifest->>'analysis_count')::integer<>
      jsonb_array_length(selected_analysis)
    or (selected_manifest->>'evidence_count')::integer<>
      jsonb_array_length(selected_evidence_index) then
    raise check_violation using message='HR intelligence bundle count invalid';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':hr-intelligence-import',0
  ));
  select * into selected from platform_hr.intelligence_bundles bundle
  where bundle.owner_internal_user_id=selected_owner_internal_user_id
    and bundle.manifest_sha256=selected_manifest_sha256;
  if found then
    if selected.bundle_id<>selected_bundle_id
      or selected.bundle_locator<>selected_bundle_locator
      or selected.manifest<>selected_manifest
      or selected.agent_chunk_index<>selected_agent_chunk_index
      or selected.agent_document_index<>selected_agent_document_index then
      raise unique_violation using message='HR intelligence idempotency mismatch';
    end if;
    return selected;
  end if;
  insert into platform_hr.intelligence_bundles(
    bundle_id,owner_internal_user_id,manifest_sha256,bundle_locator,
    schema_version,generated_at,company_count,job_count,analysis_count,
    evidence_count,manifest,source_catalog,source_coverage,aggregates,
    analysis,analysis_usage,evidence_index,document_index,
    agent_chunk_index,agent_document_index
  ) values (
    selected_bundle_id,selected_owner_internal_user_id,
    selected_manifest_sha256,selected_bundle_locator,selected_schema_version,
    selected_generated_at,selected_company_count,selected_job_count,
    jsonb_array_length(selected_analysis),
    jsonb_array_length(selected_evidence_index),selected_manifest,
    selected_source_catalog,selected_source_coverage,selected_aggregates,
    selected_analysis,selected_analysis_usage,selected_evidence_index,
    selected_manifest->'document_index',selected_agent_chunk_index,
    selected_agent_document_index
  ) returning * into selected;
  for selected_job in select value from jsonb_array_elements(selected_jobs)
  loop
    if jsonb_typeof(selected_job)<>'object'
      or selected_job->>'job_id' is null
      or selected_job->>'company_key' is null then
      raise check_violation using message='HR intelligence job invalid';
    end if;
    insert into platform_hr.intelligence_bundle_jobs(
      bundle_id,owner_internal_user_id,job_id,company_key,job
    ) values (
      selected_bundle_id,selected_owner_internal_user_id,
      (selected_job->>'job_id')::uuid,selected_job->>'company_key',selected_job
    );
  end loop;
  insert into platform_hr.intelligence_current_publication(
    workspace_key,bundle_id,owner_internal_user_id,updated_at
  ) values ('hr',selected_bundle_id,selected_owner_internal_user_id,now())
  on conflict (workspace_key) do update set
    bundle_id=excluded.bundle_id,
    owner_internal_user_id=excluded.owner_internal_user_id,
    updated_at=excluded.updated_at;
  return selected;
end
$function$;

create function platform_hr.read_intelligence_bundle_chunks_v87(
  selected_bundle_id uuid
) returns jsonb
language sql security definer stable
set search_path=pg_catalog,platform_hr
as $function$
  select bundle.agent_chunk_index
  from platform_hr.intelligence_bundles bundle
  where session_user in ('platform_control_app','platform_control_app_preview')
    and (current_database()='agent_platform_control') =
      (session_user='platform_control_app')
    and bundle.bundle_id=selected_bundle_id
$function$;

create function platform_hr.create_conversation_intelligence_reference_v87(
  selected_reference_id uuid,
  selected_owner_internal_user_id uuid,
  selected_client_request_id uuid,
  selected_conversation_id uuid,
  selected_turn_id uuid,
  selected_bundle_id uuid,
  selected_observed_at timestamptz,
  selected_context_document jsonb
) returns platform_hr.conversation_intelligence_bundle_references
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.conversation_intelligence_bundle_references%rowtype;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <>
        (session_user='platform_control_app') then
    raise insufficient_privilege;
  end if;
  if selected_reference_id is null
    or selected_owner_internal_user_id is null
    or selected_client_request_id is null
    or selected_conversation_id is null
    or selected_turn_id is null
    or selected_bundle_id is null
    or selected_observed_at is null
    or jsonb_typeof(selected_context_document)<>'object'
    or octet_length(selected_context_document::text)>32768
    or selected_context_document->>'bundle_id'<>selected_bundle_id::text then
    raise check_violation using message='conversation intelligence reference invalid';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':conversation-intelligence:' ||
    selected_turn_id::text,0
  ));
  select * into selected
  from platform_hr.conversation_intelligence_bundle_references reference
  where reference.owner_internal_user_id=selected_owner_internal_user_id
    and reference.conversation_id=selected_conversation_id
    and reference.turn_id=selected_turn_id;
  if found then
    if selected.reference_id is distinct from selected_reference_id
      or selected.client_request_id is distinct from selected_client_request_id
      or selected.bundle_id is distinct from selected_bundle_id
      or selected.observed_at is distinct from selected_observed_at
      or selected.context_document is distinct from selected_context_document then
      raise unique_violation using message='conversation intelligence reference mismatch';
    end if;
    return selected;
  end if;
  perform 1 from platform_control.conversation_turns turn_record
  join platform_control.conversations conversation
    on conversation.conversation_id=turn_record.conversation_id
  where turn_record.conversation_id=selected_conversation_id
    and turn_record.turn_id=selected_turn_id
    and conversation.owner_internal_user_id=selected_owner_internal_user_id;
  if not found then raise no_data_found; end if;
  perform 1 from platform_hr.intelligence_bundles bundle
  where bundle.bundle_id=selected_bundle_id
    and bundle.owner_internal_user_id=selected_owner_internal_user_id;
  if not found then raise no_data_found; end if;
  insert into platform_hr.conversation_intelligence_bundle_references(
    reference_id,owner_internal_user_id,client_request_id,conversation_id,
    turn_id,bundle_id,observed_at,context_document
  ) values (
    selected_reference_id,selected_owner_internal_user_id,
    selected_client_request_id,selected_conversation_id,selected_turn_id,
    selected_bundle_id,selected_observed_at,selected_context_document
  ) returning * into selected;
  return selected;
end
$function$;

create function platform_hr.read_conversation_intelligence_reference_v87(
  selected_owner_internal_user_id uuid,
  selected_conversation_id uuid,
  selected_turn_id uuid
) returns setof platform_hr.conversation_intelligence_bundle_references
language sql security definer stable
set search_path=pg_catalog,platform_hr
as $function$
  select reference.*
  from platform_hr.conversation_intelligence_bundle_references reference
  where session_user in ('platform_control_app','platform_control_app_preview')
    and (current_database()='agent_platform_control') =
      (session_user='platform_control_app')
    and reference.owner_internal_user_id=selected_owner_internal_user_id
    and reference.conversation_id=selected_conversation_id
    and reference.turn_id=selected_turn_id
$function$;

revoke all on platform_hr.conversation_intelligence_bundle_references from public;
revoke all on platform_hr.conversation_intelligence_bundle_references
  from platform_control_app,platform_control_app_preview;

revoke all on function platform_hr.import_intelligence_bundle_v87(
  uuid,uuid,text,text,timestamptz,jsonb,jsonb,jsonb,jsonb,jsonb,jsonb,jsonb,
  jsonb,jsonb,jsonb
) from public;
grant execute on function platform_hr.import_intelligence_bundle_v87(
  uuid,uuid,text,text,timestamptz,jsonb,jsonb,jsonb,jsonb,jsonb,jsonb,jsonb,
  jsonb,jsonb,jsonb
) to platform_control_app;
revoke all on function platform_hr.read_intelligence_bundle_chunks_v87(uuid)
  from public;
grant execute on function platform_hr.read_intelligence_bundle_chunks_v87(uuid)
  to platform_control_app,platform_control_app_preview;
revoke all on function platform_hr.create_conversation_intelligence_reference_v87(
  uuid,uuid,uuid,uuid,uuid,uuid,timestamptz,jsonb
) from public;
grant execute on function platform_hr.create_conversation_intelligence_reference_v87(
  uuid,uuid,uuid,uuid,uuid,uuid,timestamptz,jsonb
) to platform_control_app,platform_control_app_preview;
revoke all on function platform_hr.read_conversation_intelligence_reference_v87(
  uuid,uuid,uuid
) from public;
grant execute on function platform_hr.read_conversation_intelligence_reference_v87(
  uuid,uuid,uuid
) to platform_control_app,platform_control_app_preview;
