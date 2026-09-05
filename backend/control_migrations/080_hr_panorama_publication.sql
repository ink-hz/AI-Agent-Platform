create table platform_hr.panorama_production_batches (
  batch_id uuid primary key,
  producer_owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  client_request_id uuid not null,
  selected_source_ids uuid[] not null check (
    cardinality(selected_source_ids) between 1 and 100
    and platform_hr.uuid_array_is_unique_v79(selected_source_ids)
  ),
  trigger_kind text not null check (trigger_kind in ('schedule','operator')),
  state text not null default 'queued' check (
    state in ('queued','running','analyzing','published','failed')
  ),
  analyzer_version text not null check (
    char_length(btrim(analyzer_version)) between 1 and 160
  ),
  source_failures jsonb not null default '{}'::jsonb check (
    platform_hr.source_failures_are_valid_v79(
      source_failures,selected_source_ids
    )
  ),
  error_code text check (
    error_code is null or error_code ~ '^[a-z][a-z0-9_]{0,63}$'
  ),
  row_version bigint not null default 1 check (row_version>0),
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (batch_id,producer_owner_internal_user_id),
  unique (producer_owner_internal_user_id,client_request_id),
  check (
    (state='queued' and started_at is null and finished_at is null
      and error_code is null and source_failures='{}'::jsonb)
    or (state in ('running','analyzing') and started_at is not null
      and finished_at is null and error_code is null)
    or (state='published' and started_at is not null
      and finished_at is not null and error_code is null)
    or (state='failed' and started_at is not null
      and finished_at is not null and error_code is not null)
  )
);

create table platform_hr.panorama_production_batch_sources (
  batch_id uuid not null,
  producer_owner_internal_user_id uuid not null,
  source_id uuid not null,
  source_ordinal integer not null check (source_ordinal between 1 and 100),
  foreign key (batch_id,producer_owner_internal_user_id)
    references platform_hr.panorama_production_batches(
      batch_id,producer_owner_internal_user_id
    ),
  foreign key (source_id,producer_owner_internal_user_id)
    references platform_hr.talent_sources(
      source_id,owner_internal_user_id
    ),
  unique (batch_id,source_id),
  unique (batch_id,source_ordinal)
);

create index panorama_production_batches_state_v80
on platform_hr.panorama_production_batches(state,created_at,batch_id)
where state in ('queued','running','analyzing');

create table platform_hr.panorama_source_attempts (
  attempt_id uuid primary key,
  batch_id uuid not null,
  producer_owner_internal_user_id uuid not null,
  source_id uuid not null,
  source_url text not null check (
    platform_hr.public_https_url_is_valid_v79(source_url)
  ),
  attempt_number integer not null check (attempt_number between 1 and 3),
  state text not null check (state in ('succeeded','failed')),
  error_code text check (
    error_code is null or error_code ~ '^[a-z][a-z0-9_]{0,63}$'
  ),
  evidence_sha256 text check (
    evidence_sha256 is null
    or evidence_sha256 ~ '^[a-f0-9]{64}$'
  ),
  evidence_locator text check (
    evidence_locator is null
    or evidence_locator ~ '^sha256/[a-f0-9]{2}/[a-f0-9]{64}$'
  ),
  evidence_mime text check (
    evidence_mime is null
    or char_length(evidence_mime) between 1 and 255
  ),
  evidence_size_bytes bigint check (
    evidence_size_bytes is null
    or evidence_size_bytes between 0 and 10485760
  ),
  normalized_job_count integer not null check (
    normalized_job_count between 0 and 10000
  ),
  observed_at timestamptz not null,
  created_at timestamptz not null default now(),
  foreign key (batch_id,producer_owner_internal_user_id)
    references platform_hr.panorama_production_batches(
      batch_id,producer_owner_internal_user_id
    ),
  foreign key (batch_id,source_id)
    references platform_hr.panorama_production_batch_sources(
      batch_id,source_id
    ),
  unique (attempt_id,producer_owner_internal_user_id),
  unique (batch_id,source_id,source_url,attempt_number),
  -- Failed responses may retain raw evidence when HTTP retrieval succeeded but
  -- parsing or normalization failed. Network failures have no evidence tuple.
  check (
    (state='succeeded' and error_code is null
      and evidence_sha256 is not null and evidence_locator is not null
      and evidence_mime is not null and evidence_size_bytes is not null)
    or (state='failed' and error_code is not null
      and (
        (evidence_sha256 is null and evidence_locator is null
          and evidence_mime is null and evidence_size_bytes is null)
        or (evidence_sha256 is not null and evidence_locator is not null
          and evidence_mime is not null and evidence_size_bytes is not null)
      )
      and normalized_job_count=0)
  )
);

create index panorama_source_attempts_batch_v80
on platform_hr.panorama_source_attempts(
  batch_id,source_id,source_url,attempt_number desc
);

alter table platform_hr.public_job_snapshots
  add column production_batch_id uuid;
alter table platform_hr.public_job_snapshots
  alter column run_id drop not null;
alter table platform_hr.public_job_snapshots
  add constraint public_job_snapshot_batch_owner_v80
  foreign key (production_batch_id,owner_internal_user_id)
  references platform_hr.panorama_production_batches(
    batch_id,producer_owner_internal_user_id
  );
alter table platform_hr.public_job_snapshots
  add constraint public_job_snapshot_origin_v80
  check ((run_id is not null) <> (production_batch_id is not null));

alter table platform_hr.talent_insight_versions
  add column production_batch_id uuid;
alter table platform_hr.talent_insight_versions
  alter column run_id drop not null;
alter table platform_hr.talent_insight_versions
  alter column source_conversation_id drop not null;
alter table platform_hr.talent_insight_versions
  alter column source_turn_id drop not null;
alter table platform_hr.talent_insight_versions
  add constraint talent_insight_batch_owner_v80
  foreign key (production_batch_id,owner_internal_user_id)
  references platform_hr.panorama_production_batches(
    batch_id,producer_owner_internal_user_id
  );
alter table platform_hr.talent_insight_versions
  add constraint talent_insight_origin_v80 check (
    ((run_id is not null) and production_batch_id is null
      and source_conversation_id is not null and source_turn_id is not null)
    or ((run_id is null) and production_batch_id is not null
      and source_conversation_id is null and source_turn_id is null)
  );

create table platform_hr.panorama_publications (
  publication_id uuid primary key,
  workspace_key text not null check (workspace_key='hr'),
  client_request_id uuid not null,
  batch_id uuid not null,
  producer_owner_internal_user_id uuid not null,
  insight_version_id uuid not null,
  coverage_state text not null check (
    coverage_state in ('complete','partial')
  ),
  source_coverage jsonb not null check (
    jsonb_typeof(source_coverage)='array'
    and jsonb_array_length(source_coverage) between 1 and 100
    and octet_length(source_coverage::text)<=131072
  ),
  published_at timestamptz not null default now(),
  foreign key (batch_id,producer_owner_internal_user_id)
    references platform_hr.panorama_production_batches(
      batch_id,producer_owner_internal_user_id
    ),
  foreign key (insight_version_id,producer_owner_internal_user_id)
    references platform_hr.talent_insight_versions(
      insight_version_id,owner_internal_user_id
    ),
  unique (publication_id,workspace_key),
  unique (workspace_key,client_request_id),
  unique (workspace_key,batch_id),
  unique (workspace_key,insight_version_id)
);

create table platform_hr.panorama_current_publications (
  workspace_key text primary key check (workspace_key='hr'),
  publication_id uuid not null,
  updated_at timestamptz not null default now(),
  foreign key (publication_id,workspace_key)
    references platform_hr.panorama_publications(
      publication_id,workspace_key
    )
);

create function platform_hr.create_panorama_production_batch_v80(
  selected_batch_id uuid,
  selected_producer_owner_internal_user_id uuid,
  selected_client_request_id uuid,
  selected_source_ids uuid[],
  selected_trigger_kind text,
  selected_analyzer_version text
) returns platform_hr.panorama_production_batches
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.panorama_production_batches%rowtype;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <>
        (session_user='platform_control_app') then
    raise insufficient_privilege;
  end if;
  if selected_batch_id is null
    or selected_producer_owner_internal_user_id is null
    or selected_client_request_id is null
    or selected_source_ids is null
    or cardinality(selected_source_ids) not between 1 and 100
    or not platform_hr.uuid_array_is_unique_v79(selected_source_ids)
    or selected_trigger_kind not in ('schedule','operator')
    or char_length(btrim(selected_analyzer_version)) not between 1 and 160 then
    raise check_violation using message='panorama production batch invalid';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    selected_producer_owner_internal_user_id::text ||
    ':panorama-production-request:' || selected_client_request_id::text,0
  ));
  select * into selected from platform_hr.panorama_production_batches batch
  where batch.producer_owner_internal_user_id=
      selected_producer_owner_internal_user_id
    and batch.client_request_id=selected_client_request_id;
  if found then
    if selected.batch_id is distinct from selected_batch_id
      or selected.selected_source_ids is distinct from selected_source_ids
      or selected.trigger_kind is distinct from selected_trigger_kind
      or selected.analyzer_version is distinct from
        btrim(selected_analyzer_version) then
      raise unique_violation using
        message='panorama production batch idempotency mismatch';
    end if;
    return selected;
  end if;
  if exists (
    select 1 from unnest(selected_source_ids) requested(source_id)
    left join platform_hr.talent_sources source
      on source.source_id=requested.source_id
      and source.owner_internal_user_id=
        selected_producer_owner_internal_user_id
      and source.active
    where source.source_id is null
  ) then
    raise foreign_key_violation using message='panorama source unavailable';
  end if;
  insert into platform_hr.panorama_production_batches(
    batch_id,producer_owner_internal_user_id,client_request_id,
    selected_source_ids,trigger_kind,analyzer_version
  ) values (
    selected_batch_id,selected_producer_owner_internal_user_id,
    selected_client_request_id,selected_source_ids,selected_trigger_kind,
    btrim(selected_analyzer_version)
  ) returning * into selected;
  insert into platform_hr.panorama_production_batch_sources(
    batch_id,producer_owner_internal_user_id,source_id,source_ordinal
  ) select selected_batch_id,selected_producer_owner_internal_user_id,
      requested.source_id,requested.ordinality
    from unnest(selected_source_ids)
      with ordinality requested(source_id,ordinality);
  return selected;
end
$function$;

create function platform_hr.transition_panorama_production_batch_v80(
  selected_producer_owner_internal_user_id uuid,
  selected_batch_id uuid,
  selected_expected_row_version bigint,
  selected_state text,
  selected_error_code text,
  selected_source_failures jsonb
) returns platform_hr.panorama_production_batches
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare current_batch platform_hr.panorama_production_batches%rowtype;
declare selected platform_hr.panorama_production_batches%rowtype;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <>
        (session_user='platform_control_app') then
    raise insufficient_privilege;
  end if;
  select * into current_batch
  from platform_hr.panorama_production_batches batch
  where batch.batch_id=selected_batch_id
    and batch.producer_owner_internal_user_id=
      selected_producer_owner_internal_user_id
  for update;
  if not found then raise no_data_found; end if;
  if current_batch.row_version<>selected_expected_row_version then
    raise serialization_failure using message=format(
      'panorama batch version conflict: current=%s expected=%s',
      current_batch.row_version,selected_expected_row_version
    );
  end if;
  if not (
    (current_batch.state='queued' and selected_state in ('running','failed'))
    or (current_batch.state='running'
      and selected_state in ('analyzing','failed'))
    or (current_batch.state='analyzing' and selected_state='failed')
  ) then
    raise check_violation using message='panorama batch transition invalid';
  end if;
  if selected_state='failed' then
    if selected_error_code is null
      or selected_error_code !~ '^[a-z][a-z0-9_]{0,63}$' then
      raise check_violation using message='panorama batch error invalid';
    end if;
  elsif selected_error_code is not null then
    raise check_violation using message='panorama batch error invalid';
  end if;
  if not platform_hr.source_failures_are_valid_v79(
    selected_source_failures,current_batch.selected_source_ids
  ) then
    raise check_violation using message='panorama batch failures invalid';
  end if;
  update platform_hr.panorama_production_batches batch set
    state=selected_state,
    error_code=selected_error_code,
    source_failures=selected_source_failures,
    row_version=batch.row_version+1,
    started_at=case when batch.state='queued' then now() else batch.started_at end,
    finished_at=case when selected_state='failed' then now() else null end,
    updated_at=now()
  where batch.batch_id=selected_batch_id
    and batch.producer_owner_internal_user_id=
      selected_producer_owner_internal_user_id
  returning * into selected;
  return selected;
end
$function$;

create function platform_hr.record_panorama_source_attempt_v80(
  selected_attempt_id uuid,
  selected_batch_id uuid,
  selected_producer_owner_internal_user_id uuid,
  selected_source_id uuid,
  selected_source_url text,
  selected_attempt_number integer,
  selected_state text,
  selected_error_code text,
  selected_evidence_sha256 text,
  selected_evidence_locator text,
  selected_evidence_mime text,
  selected_evidence_size_bytes bigint,
  selected_normalized_job_count integer,
  selected_observed_at timestamptz
) returns platform_hr.panorama_source_attempts
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.panorama_source_attempts%rowtype;
declare approved_urls jsonb;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <>
        (session_user='platform_control_app') then
    raise insufficient_privilege;
  end if;
  select source.approved_public_urls into approved_urls
  from platform_hr.panorama_production_batch_sources binding
  join platform_hr.talent_sources source
    on source.source_id=binding.source_id
    and source.owner_internal_user_id=binding.producer_owner_internal_user_id
  join platform_hr.panorama_production_batches batch
    on batch.batch_id=binding.batch_id
    and batch.producer_owner_internal_user_id=
      binding.producer_owner_internal_user_id
  where binding.batch_id=selected_batch_id
    and binding.producer_owner_internal_user_id=
      selected_producer_owner_internal_user_id
    and binding.source_id=selected_source_id
    and batch.state='running';
  if not found or not platform_hr.url_is_approved_v79(
    selected_source_url,approved_urls
  ) then
    raise foreign_key_violation using message='panorama attempt source invalid';
  end if;
  insert into platform_hr.panorama_source_attempts(
    attempt_id,batch_id,producer_owner_internal_user_id,source_id,
    source_url,attempt_number,state,error_code,evidence_sha256,
    evidence_locator,evidence_mime,evidence_size_bytes,
    normalized_job_count,observed_at
  ) values (
    selected_attempt_id,selected_batch_id,
    selected_producer_owner_internal_user_id,selected_source_id,
    selected_source_url,selected_attempt_number,selected_state,
    selected_error_code,selected_evidence_sha256,selected_evidence_locator,
    selected_evidence_mime,selected_evidence_size_bytes,
    selected_normalized_job_count,selected_observed_at
  ) on conflict (batch_id,source_id,source_url,attempt_number)
    do nothing returning * into selected;
  if selected.attempt_id is null then
    select * into selected from platform_hr.panorama_source_attempts attempt
    where attempt.batch_id=selected_batch_id
      and attempt.source_id=selected_source_id
      and attempt.source_url=selected_source_url
      and attempt.attempt_number=selected_attempt_number;
    if selected.attempt_id is distinct from selected_attempt_id
      or selected.state is distinct from selected_state
      or selected.error_code is distinct from selected_error_code
      or selected.evidence_sha256 is distinct from selected_evidence_sha256
      or selected.normalized_job_count is distinct from
        selected_normalized_job_count then
      raise unique_violation using
        message='panorama source attempt idempotency mismatch';
    end if;
  end if;
  return selected;
end
$function$;

create function platform_hr.publish_panorama_version_v80(
  selected_publication_id uuid,
  selected_client_request_id uuid,
  selected_batch_id uuid,
  selected_insight_version_id uuid,
  selected_coverage_state text,
  selected_source_coverage jsonb
) returns platform_hr.panorama_publications
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected_batch platform_hr.panorama_production_batches%rowtype;
declare selected platform_hr.panorama_publications%rowtype;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <>
        (session_user='platform_control_app') then
    raise insufficient_privilege;
  end if;
  if selected_coverage_state not in ('complete','partial')
    or jsonb_typeof(selected_source_coverage) is distinct from 'array'
    or jsonb_array_length(selected_source_coverage) not between 1 and 100
    or octet_length(selected_source_coverage::text)>131072 then
    raise check_violation using message='panorama publication invalid';
  end if;
  perform pg_advisory_xact_lock(hashtextextended('hr:panorama-publication',0));
  select * into selected_batch
  from platform_hr.panorama_production_batches batch
  where batch.batch_id=selected_batch_id
  for update;
  if not found or selected_batch.state<>'analyzing' then
    raise check_violation using message='panorama batch not publishable';
  end if;
  if not exists (
    select 1 from platform_hr.talent_insight_versions insight
    where insight.insight_version_id=selected_insight_version_id
      and insight.owner_internal_user_id=
        selected_batch.producer_owner_internal_user_id
      and insight.production_batch_id=selected_batch_id
  ) then
    raise foreign_key_violation using message='panorama insight not publishable';
  end if;
  if exists (
    select 1
    from platform_hr.talent_insight_versions insight
    cross join lateral jsonb_array_elements(insight.facts) fact
    left join platform_hr.public_job_snapshots snapshot
      on snapshot.snapshot_id=(fact->>'snapshot_id')::uuid
      and snapshot.owner_internal_user_id=insight.owner_internal_user_id
    where insight.insight_version_id=selected_insight_version_id
      and (
        snapshot.snapshot_id is null
        or snapshot.production_batch_id is distinct from selected_batch_id
        or snapshot.source_url is distinct from fact->>'source_url'
        or snapshot.observed_at is distinct from
          (fact->>'observed_at')::timestamptz
      )
  ) then
    raise check_violation using message='panorama publication evidence invalid';
  end if;
  select * into selected from platform_hr.panorama_publications publication
  where publication.workspace_key='hr'
    and publication.client_request_id=selected_client_request_id;
  if found then
    if selected.publication_id is distinct from selected_publication_id
      or selected.batch_id is distinct from selected_batch_id
      or selected.insight_version_id is distinct from
        selected_insight_version_id
      or selected.coverage_state is distinct from selected_coverage_state
      or selected.source_coverage is distinct from selected_source_coverage then
      raise unique_violation using
        message='panorama publication idempotency mismatch';
    end if;
    return selected;
  end if;
  insert into platform_hr.panorama_publications(
    publication_id,workspace_key,client_request_id,batch_id,
    producer_owner_internal_user_id,insight_version_id,coverage_state,
    source_coverage
  ) values (
    selected_publication_id,'hr',selected_client_request_id,
    selected_batch_id,selected_batch.producer_owner_internal_user_id,
    selected_insight_version_id,selected_coverage_state,
    selected_source_coverage
  ) returning * into selected;
  insert into platform_hr.panorama_current_publications(
    workspace_key,publication_id
  ) values ('hr',selected_publication_id)
  on conflict (workspace_key) do update set
    publication_id=excluded.publication_id,updated_at=now();
  update platform_hr.panorama_production_batches batch set
    state='published',row_version=batch.row_version+1,
    finished_at=now(),updated_at=now()
  where batch.batch_id=selected_batch_id;
  return selected;
end
$function$;

create function platform_hr.read_current_panorama_publication_v80(
  selected_workspace_key text
) returns setof platform_hr.panorama_publications
language plpgsql stable security definer
set search_path=pg_catalog,platform_hr
as $function$
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <>
        (session_user='platform_control_app') then
    raise insufficient_privilege;
  end if;
  if selected_workspace_key is distinct from 'hr' then
    raise check_violation using message='panorama workspace invalid';
  end if;
  return query
    select publication.*
    from platform_hr.panorama_current_publications current_publication
    join platform_hr.panorama_publications publication
      on publication.publication_id=current_publication.publication_id
      and publication.workspace_key=current_publication.workspace_key
    where current_publication.workspace_key=selected_workspace_key;
end
$function$;

revoke all on all tables in schema platform_hr from public;
revoke all on function platform_hr.create_panorama_production_batch_v80(
  uuid,uuid,uuid,uuid[],text,text
) from public;
revoke all on function platform_hr.transition_panorama_production_batch_v80(
  uuid,uuid,bigint,text,text,jsonb
) from public;
revoke all on function platform_hr.record_panorama_source_attempt_v80(
  uuid,uuid,uuid,uuid,text,integer,text,text,text,text,text,bigint,integer,
  timestamptz
) from public;
revoke all on function platform_hr.publish_panorama_version_v80(
  uuid,uuid,uuid,uuid,text,jsonb
) from public;
revoke all on function platform_hr.read_current_panorama_publication_v80(text)
  from public;

grant execute on function platform_hr.create_panorama_production_batch_v80(
  uuid,uuid,uuid,uuid[],text,text
) to platform_control_app,platform_control_app_preview;
grant execute on function platform_hr.transition_panorama_production_batch_v80(
  uuid,uuid,bigint,text,text,jsonb
) to platform_control_app,platform_control_app_preview;
grant execute on function platform_hr.record_panorama_source_attempt_v80(
  uuid,uuid,uuid,uuid,text,integer,text,text,text,text,text,bigint,integer,
  timestamptz
) to platform_control_app,platform_control_app_preview;
grant execute on function platform_hr.publish_panorama_version_v80(
  uuid,uuid,uuid,uuid,text,jsonb
) to platform_control_app,platform_control_app_preview;
grant execute on function platform_hr.read_current_panorama_publication_v80(text)
  to platform_control_app,platform_control_app_preview;
