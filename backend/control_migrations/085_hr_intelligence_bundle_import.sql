create table platform_hr.intelligence_bundles (
  bundle_id uuid primary key,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  manifest_sha256 text not null check (manifest_sha256 ~ '^[a-f0-9]{64}$'),
  bundle_locator text not null check (
    bundle_locator ~ '^bundles/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
  ),
  schema_version integer not null check (schema_version=1),
  generated_at timestamptz not null,
  company_count integer not null check (company_count between 1 and 100),
  job_count integer not null check (job_count between 0 and 100000),
  analysis_count integer not null check (analysis_count between 0 and 10000),
  evidence_count integer not null check (evidence_count between 0 and 100000),
  manifest jsonb not null check (jsonb_typeof(manifest)='object'),
  source_catalog jsonb not null check (jsonb_typeof(source_catalog)='object'),
  source_coverage jsonb not null check (jsonb_typeof(source_coverage)='object'),
  aggregates jsonb not null check (jsonb_typeof(aggregates)='object'),
  analysis jsonb not null check (jsonb_typeof(analysis)='array'),
  analysis_usage jsonb not null check (jsonb_typeof(analysis_usage)='array'),
  evidence_index jsonb not null check (jsonb_typeof(evidence_index)='array'),
  document_index jsonb not null check (jsonb_typeof(document_index)='object'),
  imported_at timestamptz not null default now(),
  unique (bundle_id,owner_internal_user_id),
  unique (owner_internal_user_id,manifest_sha256)
);

create table platform_hr.intelligence_bundle_jobs (
  bundle_id uuid not null,
  owner_internal_user_id uuid not null,
  job_id uuid not null,
  company_key text not null check (
    company_key ~ '^[a-z0-9][a-z0-9_-]{0,127}$'
  ),
  job jsonb not null check (jsonb_typeof(job)='object'),
  primary key (bundle_id,job_id),
  foreign key (bundle_id,owner_internal_user_id)
    references platform_hr.intelligence_bundles(
      bundle_id,owner_internal_user_id
    ) on delete restrict
);

create index intelligence_bundle_jobs_company_v85
on platform_hr.intelligence_bundle_jobs(bundle_id,company_key,job_id);

create table platform_hr.intelligence_current_publication (
  workspace_key text primary key check (workspace_key='hr'),
  bundle_id uuid not null,
  owner_internal_user_id uuid not null,
  updated_at timestamptz not null default now(),
  foreign key (bundle_id,owner_internal_user_id)
    references platform_hr.intelligence_bundles(
      bundle_id,owner_internal_user_id
    ) on delete restrict
);

create function platform_hr.import_intelligence_bundle_v85(
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
  selected_evidence_index jsonb
) returns platform_hr.intelligence_bundles
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.intelligence_bundles%rowtype;
declare selected_job jsonb;
declare selected_company_count integer;
declare selected_job_count integer;
begin
  if session_user<>'platform_control_app'
     or current_database()<>'agent_platform_control' then
    raise insufficient_privilege;
  end if;
  if selected_owner_internal_user_id is null
    or selected_bundle_id is null
    or selected_manifest_sha256 !~ '^[a-f0-9]{64}$'
    or selected_bundle_locator <> 'bundles/' || selected_bundle_id::text
    or selected_generated_at is null
    or jsonb_typeof(selected_manifest)<>'object'
    or (selected_manifest->>'bundle_id')::uuid<>selected_bundle_id
    or selected_manifest->>'schema_version'<>'1'
    or jsonb_typeof(selected_source_catalog)<>'object'
    or jsonb_typeof(selected_source_coverage)<>'object'
    or jsonb_typeof(selected_jobs)<>'array'
    or jsonb_array_length(selected_jobs)>100000
    or jsonb_typeof(selected_aggregates)<>'object'
    or jsonb_typeof(selected_analysis)<>'array'
    or jsonb_array_length(selected_analysis)>10000
    or jsonb_typeof(selected_analysis_usage)<>'array'
    or jsonb_typeof(selected_evidence_index)<>'array'
    or jsonb_array_length(selected_evidence_index)>100000 then
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
      or selected.manifest<>selected_manifest then
      raise unique_violation using message='HR intelligence idempotency mismatch';
    end if;
    return selected;
  end if;
  insert into platform_hr.intelligence_bundles(
    bundle_id,owner_internal_user_id,manifest_sha256,bundle_locator,
    schema_version,generated_at,company_count,job_count,analysis_count,
    evidence_count,manifest,source_catalog,source_coverage,aggregates,
    analysis,analysis_usage,evidence_index,document_index
  ) values (
    selected_bundle_id,selected_owner_internal_user_id,
    selected_manifest_sha256,selected_bundle_locator,1,selected_generated_at,
    selected_company_count,selected_job_count,
    jsonb_array_length(selected_analysis),
    jsonb_array_length(selected_evidence_index),selected_manifest,
    selected_source_catalog,selected_source_coverage,selected_aggregates,
    selected_analysis,selected_analysis_usage,selected_evidence_index,
    selected_manifest->'document_index'
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
      (selected_job->>'job_id')::uuid,selected_job->>'company_key',
      selected_job
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

create function platform_hr.read_intelligence_bundle_by_manifest_v85(
  selected_owner_internal_user_id uuid,
  selected_manifest_sha256 text
) returns setof platform_hr.intelligence_bundles
language sql security definer stable
set search_path=pg_catalog,platform_hr
as $function$
  select bundle.* from platform_hr.intelligence_bundles bundle
  where session_user in ('platform_control_app','platform_control_app_preview')
    and (current_database()='agent_platform_control') =
      (session_user='platform_control_app')
    and bundle.owner_internal_user_id=selected_owner_internal_user_id
    and bundle.manifest_sha256=selected_manifest_sha256
$function$;

create function platform_hr.read_current_intelligence_bundle_v85()
returns setof platform_hr.intelligence_bundles
language sql security definer stable
set search_path=pg_catalog,platform_hr
as $function$
  select bundle.*
  from platform_hr.intelligence_current_publication publication
  join platform_hr.intelligence_bundles bundle
    on bundle.bundle_id=publication.bundle_id
    and bundle.owner_internal_user_id=publication.owner_internal_user_id
  where session_user in ('platform_control_app','platform_control_app_preview')
    and (current_database()='agent_platform_control') =
      (session_user='platform_control_app')
    and publication.workspace_key='hr'
$function$;

create function platform_hr.read_intelligence_bundle_v85(
  selected_bundle_id uuid
) returns setof platform_hr.intelligence_bundles
language sql security definer stable
set search_path=pg_catalog,platform_hr
as $function$
  select bundle.* from platform_hr.intelligence_bundles bundle
  where session_user in ('platform_control_app','platform_control_app_preview')
    and (current_database()='agent_platform_control') =
      (session_user='platform_control_app')
    and bundle.bundle_id=selected_bundle_id
$function$;

create function platform_hr.list_intelligence_bundles_v85(selected_limit integer)
returns setof platform_hr.intelligence_bundles
language sql security definer stable
set search_path=pg_catalog,platform_hr
as $function$
  select bundle.* from platform_hr.intelligence_bundles bundle
  where session_user in ('platform_control_app','platform_control_app_preview')
    and (current_database()='agent_platform_control') =
      (session_user='platform_control_app')
    and selected_limit between 1 and 100
  order by bundle.imported_at desc,bundle.bundle_id desc
  limit selected_limit
$function$;

create function platform_hr.read_intelligence_bundle_jobs_v85(
  selected_bundle_id uuid
) returns setof platform_hr.intelligence_bundle_jobs
language sql security definer stable
set search_path=pg_catalog,platform_hr
as $function$
  select selected.* from platform_hr.intelligence_bundle_jobs selected
  where session_user in ('platform_control_app','platform_control_app_preview')
    and (current_database()='agent_platform_control') =
      (session_user='platform_control_app')
    and selected.bundle_id=selected_bundle_id
  order by selected.company_key,selected.job_id
$function$;

revoke all on platform_hr.intelligence_bundles from public;
revoke all on platform_hr.intelligence_bundles from platform_control_app,platform_control_app_preview;
revoke all on platform_hr.intelligence_bundle_jobs from public;
revoke all on platform_hr.intelligence_bundle_jobs from platform_control_app,platform_control_app_preview;
revoke all on platform_hr.intelligence_current_publication from public;
revoke all on platform_hr.intelligence_current_publication from platform_control_app,platform_control_app_preview;

revoke all on function platform_hr.import_intelligence_bundle_v85(
  uuid,uuid,text,text,timestamptz,jsonb,jsonb,jsonb,jsonb,jsonb,jsonb,jsonb,jsonb
) from public;
grant execute on function platform_hr.import_intelligence_bundle_v85(
  uuid,uuid,text,text,timestamptz,jsonb,jsonb,jsonb,jsonb,jsonb,jsonb,jsonb,jsonb
) to platform_control_app;

revoke all on function platform_hr.read_intelligence_bundle_by_manifest_v85(uuid,text) from public;
grant execute on function platform_hr.read_intelligence_bundle_by_manifest_v85(uuid,text) to platform_control_app,platform_control_app_preview;
revoke all on function platform_hr.read_current_intelligence_bundle_v85() from public;
grant execute on function platform_hr.read_current_intelligence_bundle_v85() to platform_control_app,platform_control_app_preview;
revoke all on function platform_hr.read_intelligence_bundle_v85(uuid) from public;
grant execute on function platform_hr.read_intelligence_bundle_v85(uuid) to platform_control_app,platform_control_app_preview;
revoke all on function platform_hr.list_intelligence_bundles_v85(integer) from public;
grant execute on function platform_hr.list_intelligence_bundles_v85(integer) to platform_control_app,platform_control_app_preview;
revoke all on function platform_hr.read_intelligence_bundle_jobs_v85(uuid) from public;
grant execute on function platform_hr.read_intelligence_bundle_jobs_v85(uuid) to platform_control_app,platform_control_app_preview;

revoke all on function platform_hr.create_panorama_run_v79(uuid,uuid,uuid,uuid[],uuid) from platform_control_app;
revoke all on function platform_hr.create_panorama_run_v79(uuid,uuid,uuid,uuid[],uuid) from platform_control_app_preview;
revoke all on function platform_hr.transition_panorama_run_v79(uuid,uuid,uuid,bigint,text,text,jsonb) from platform_control_app;
revoke all on function platform_hr.transition_panorama_run_v79(uuid,uuid,uuid,bigint,text,text,jsonb) from platform_control_app_preview;
revoke all on function platform_hr.read_panorama_run_runtime_v79(uuid) from platform_control_app;
revoke all on function platform_hr.read_panorama_run_runtime_v79(uuid) from platform_control_app_preview;
revoke all on function platform_hr.claim_next_panorama_run_v79(integer) from platform_control_app;
revoke all on function platform_hr.claim_next_panorama_run_v79(integer) from platform_control_app_preview;
revoke all on function platform_hr.retry_panorama_analysis_v82(uuid,uuid,bigint) from platform_control_app;
revoke all on function platform_hr.retry_panorama_analysis_v82(uuid,uuid,bigint) from platform_control_app_preview;
revoke all on function platform_hr.create_panorama_production_batch_v80(uuid,uuid,uuid,uuid[],text,text) from platform_control_app;
revoke all on function platform_hr.create_panorama_production_batch_v80(uuid,uuid,uuid,uuid[],text,text) from platform_control_app_preview;
revoke all on function platform_hr.transition_panorama_production_batch_v80(uuid,uuid,bigint,text,text,jsonb) from platform_control_app;
revoke all on function platform_hr.transition_panorama_production_batch_v80(uuid,uuid,bigint,text,text,jsonb) from platform_control_app_preview;
revoke all on function platform_hr.publish_panorama_version_v80(uuid,uuid,uuid,uuid,text,jsonb) from platform_control_app;
revoke all on function platform_hr.publish_panorama_version_v80(uuid,uuid,uuid,uuid,text,jsonb) from platform_control_app_preview;
