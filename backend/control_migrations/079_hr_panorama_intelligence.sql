create function platform_hr.public_https_url_is_valid_v79(selected_url text)
returns boolean
language plpgsql immutable
set search_path=pg_catalog
as $function$
declare authority text;
declare selected_host text;
declare selected_port text;
begin
  if selected_url is null or char_length(selected_url) not between 9 and 2048
    or selected_url !~ '^https://[^/?#]+([/?#][^[:space:]]*)?$' then
    return false;
  end if;
  authority := substring(selected_url from '^https://([^/?#]+)');
  if authority is null or authority like '%@%' or authority like '[%' then
    return false;
  end if;
  if authority like '%:%' then
    if authority !~ '^[^:]+:[0-9]{1,5}$' then return false; end if;
    selected_host := lower(split_part(authority,':',1));
    selected_port := split_part(authority,':',2);
    if selected_port::integer not between 1 and 65535 then return false; end if;
  else
    selected_host := lower(authority);
  end if;
  if selected_host='localhost' or selected_host like '%.localhost'
    or selected_host ~ '^[0-9]+([.][0-9]+){3}$'
    or selected_host !~
      '^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)([.]'
      '([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?))+$' then
    return false;
  end if;
  return true;
end
$function$;

create function platform_hr.jsonb_string_array_v79(
  selected_value jsonb,
  selected_max_items integer,
  selected_max_item_length integer
) returns boolean
language sql immutable
set search_path=pg_catalog
as $function$
  select selected_value is not null
    and jsonb_typeof(selected_value)='array'
    and jsonb_array_length(selected_value)<=selected_max_items
    and not exists (
      select 1
      from jsonb_array_elements(selected_value) item
      where jsonb_typeof(item)<>'string'
        or char_length(btrim(item #>> '{}')) not between 1 and selected_max_item_length
    )
$function$;

create function platform_hr.jsonb_https_url_array_v79(
  selected_value jsonb,
  selected_max_items integer
) returns boolean
language sql immutable
set search_path=pg_catalog
as $function$
  select selected_value is not null
    and jsonb_typeof(selected_value)='array'
    and jsonb_array_length(selected_value) between 1 and selected_max_items
    and jsonb_array_length(selected_value)=(
      select count(distinct item #>> '{}')
      from jsonb_array_elements(selected_value) item
    )
    and not exists (
      select 1
      from jsonb_array_elements(selected_value) item
      where jsonb_typeof(item)<>'string'
        or char_length(item #>> '{}') not between 9 and 2048
        or not platform_hr.public_https_url_is_valid_v79(item #>> '{}')
    )
$function$;

create function platform_hr.url_is_approved_v79(
  selected_url text,
  selected_approved_urls jsonb
) returns boolean
language sql immutable
set search_path=pg_catalog
as $function$
  select platform_hr.public_https_url_is_valid_v79(selected_url)
    and exists (
      select 1
      from jsonb_array_elements_text(selected_approved_urls) approved_url
      where selected_url=approved_url
        or selected_url like rtrim(approved_url,'/') || '/%'
    )
$function$;

create function platform_hr.uuid_array_is_unique_v79(selected_value uuid[])
returns boolean
language sql immutable
set search_path=pg_catalog
as $function$
  select cardinality(selected_value)=(
    select count(distinct item) from unnest(selected_value) item
  )
$function$;

create function platform_hr.jsonb_object_size_v79(selected_value jsonb)
returns integer
language sql immutable
set search_path=pg_catalog
as $function$
  select count(*)::integer from jsonb_each(selected_value)
$function$;

create function platform_hr.source_failures_are_valid_v79(
  selected_value jsonb,
  selected_source_ids uuid[]
) returns boolean
language sql immutable
set search_path=pg_catalog
as $function$
  select selected_value is not null
    and jsonb_typeof(selected_value)='object'
    and octet_length(selected_value::text)<=8192
    and (select count(*) from jsonb_each(selected_value))<=100
    and not exists (
      select 1
      from (
        select source_id,reason_value,reason_value #>> '{}' as reason_code
        from jsonb_each(selected_value) failure(source_id,reason_value)
      ) failure
      where failure.source_id !~ '^[a-f0-9-]{36}$'
        or failure.source_id<>all(selected_source_ids::text[])
        or jsonb_typeof(failure.reason_value)<>'string'
        or failure.reason_code !~ '^[a-z][a-z0-9_]{0,63}$'
    )
$function$;

create function platform_hr.facts_have_https_urls_v79(selected_value jsonb)
returns boolean
language sql immutable
set search_path=pg_catalog
as $function$
  select selected_value is not null
    and jsonb_typeof(selected_value)='array'
    and not exists (
      select 1 from jsonb_array_elements(selected_value) fact
      where jsonb_typeof(fact)<>'object'
        or jsonb_typeof(fact->'source_url')<>'string'
        or not platform_hr.public_https_url_is_valid_v79(fact->>'source_url')
    )
$function$;

create function platform_hr.insight_payload_is_valid_v79(
  selected_facts jsonb,
  selected_inferences jsonb,
  selected_unknowns jsonb
) returns boolean
language plpgsql immutable
set search_path=pg_catalog,platform_hr
as $function$
declare fact jsonb;
declare inference jsonb;
declare unknown_item jsonb;
declare basis_id jsonb;
declare fact_ids text[] := '{}'::text[];
begin
  if jsonb_typeof(selected_facts) is distinct from 'array'
    or jsonb_typeof(selected_inferences) is distinct from 'array'
    or jsonb_typeof(selected_unknowns) is distinct from 'array' then
    return false;
  end if;
  if jsonb_array_length(selected_facts) not between 1 and 1000
    or jsonb_array_length(selected_inferences)>1000
    or jsonb_array_length(selected_unknowns)>1000 then
    return false;
  end if;
  for fact in select value from jsonb_array_elements(selected_facts) loop
    if jsonb_typeof(fact) is distinct from 'object'
      or jsonb_typeof(fact->'fact_id') is distinct from 'string'
      or jsonb_typeof(fact->'text') is distinct from 'string'
      or jsonb_typeof(fact->'source_url') is distinct from 'string'
      or jsonb_typeof(fact->'observed_at') is distinct from 'string' then
      return false;
    end if;
    if char_length(btrim(fact->>'fact_id')) not between 1 and 128
      or char_length(btrim(fact->>'text')) not between 1 and 8000
      or char_length(fact->>'observed_at') not between 20 and 35
      or fact->>'observed_at' !~
        '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$' then
      return false;
    end if;
    fact_ids := array_append(fact_ids,btrim(fact->>'fact_id'));
  end loop;
  if cardinality(fact_ids)<>(
    select count(distinct fact_id) from unnest(fact_ids) fact_id
  ) then
    return false;
  end if;
  for inference in
    select value from jsonb_array_elements(selected_inferences)
  loop
    if jsonb_typeof(inference) is distinct from 'object'
      or jsonb_typeof(inference->'text') is distinct from 'string'
      or jsonb_typeof(inference->'basis_fact_ids') is distinct from 'array' then
      return false;
    end if;
    if char_length(btrim(inference->>'text')) not between 1 and 8000
      or jsonb_array_length(inference->'basis_fact_ids') not between 1 and 100
      or not platform_hr.jsonb_string_array_v79(
        inference->'basis_fact_ids',100,128
      ) then
      return false;
    end if;
    for basis_id in
      select value from jsonb_array_elements(inference->'basis_fact_ids')
    loop
      if (basis_id #>> '{}')<>all(fact_ids) then return false; end if;
    end loop;
  end loop;
  for unknown_item in
    select value from jsonb_array_elements(selected_unknowns)
  loop
    if jsonb_typeof(unknown_item) is distinct from 'object'
      or jsonb_typeof(unknown_item->'text') is distinct from 'string'
      or char_length(btrim(unknown_item->>'text')) not between 1 and 8000 then
      return false;
    end if;
  end loop;
  return true;
end
$function$;

create table platform_hr.talent_sources (
  source_id uuid primary key,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  client_request_id uuid not null,
  source_kind text not null default 'company' check (source_kind='company'),
  company_key text not null check (
    company_key ~ '^[a-z0-9][a-z0-9._-]{0,127}$'
  ),
  canonical_name text not null check (
    char_length(btrim(canonical_name)) between 1 and 500
  ),
  aliases jsonb not null default '[]'::jsonb check (
    platform_hr.jsonb_string_array_v79(aliases,20,500)
    and octet_length(aliases::text)<=32768
  ),
  approved_public_urls jsonb not null check (
    platform_hr.jsonb_https_url_array_v79(approved_public_urls,20)
    and octet_length(approved_public_urls::text)<=65536
  ),
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (source_id,owner_internal_user_id),
  unique (owner_internal_user_id,client_request_id),
  unique (owner_internal_user_id,company_key)
);

create table platform_hr.panorama_runs (
  run_id uuid primary key,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  client_request_id uuid not null,
  selected_source_ids uuid[] not null check (
    cardinality(selected_source_ids) between 1 and 100
    and platform_hr.uuid_array_is_unique_v79(selected_source_ids)
  ),
  conversation_id uuid not null,
  state text not null default 'queued' check (
    state in (
      'queued','running','completed','partially_completed','failed'
    )
  ),
  error_code text check (
    error_code is null or error_code ~ '^[a-z][a-z0-9_]{0,63}$'
  ),
  source_failures jsonb not null default '{}'::jsonb check (
    platform_hr.source_failures_are_valid_v79(
      source_failures,selected_source_ids
    )
  ),
  row_version bigint not null default 1 check (row_version>0),
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (conversation_id,owner_internal_user_id)
    references platform_control.conversations(
      conversation_id,owner_internal_user_id
    ),
  check (
    (state='queued' and started_at is null and finished_at is null
      and error_code is null and source_failures='{}'::jsonb)
    or (state='running' and started_at is not null and finished_at is null
      and error_code is null and source_failures='{}'::jsonb)
    or (state='completed' and started_at is not null and finished_at is not null
      and error_code is null and source_failures='{}'::jsonb)
    or (state='partially_completed' and started_at is not null
      and finished_at is not null and error_code is null
      and source_failures<>'{}'::jsonb
      and platform_hr.jsonb_object_size_v79(source_failures)
        < cardinality(selected_source_ids))
    or (state='failed' and started_at is not null and finished_at is not null
      and error_code is not null)
  ),
  unique (run_id,owner_internal_user_id),
  unique (owner_internal_user_id,client_request_id)
);

create table platform_hr.panorama_run_sources (
  run_id uuid not null,
  owner_internal_user_id uuid not null,
  source_id uuid not null,
  source_ordinal integer not null check (source_ordinal between 1 and 100),
  foreign key (run_id,owner_internal_user_id)
    references platform_hr.panorama_runs(run_id,owner_internal_user_id),
  foreign key (source_id,owner_internal_user_id)
    references platform_hr.talent_sources(
      source_id,owner_internal_user_id
    ),
  unique (run_id,source_id),
  unique (run_id,source_ordinal)
);

create table platform_hr.panorama_run_transition_events (
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  client_request_id uuid not null,
  run_id uuid not null,
  expected_row_version bigint not null check (expected_row_version>0),
  previous_state text not null check (
    previous_state in ('queued','running')
  ),
  selected_state text not null check (
    selected_state in (
      'running','completed','partially_completed','failed'
    )
  ),
  selected_error_code text check (
    selected_error_code is null
    or selected_error_code ~ '^[a-z][a-z0-9_]{0,63}$'
  ),
  selected_source_failures jsonb not null check (
    jsonb_typeof(selected_source_failures)='object'
    and octet_length(selected_source_failures::text)<=8192
  ),
  payload_sha256 bytea not null check (octet_length(payload_sha256)=32),
  created_at timestamptz not null default now(),
  foreign key (run_id,owner_internal_user_id)
    references platform_hr.panorama_runs(run_id,owner_internal_user_id),
  unique (owner_internal_user_id,client_request_id)
);

create table platform_hr.public_job_snapshots (
  snapshot_id uuid primary key,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  origin_client_request_id uuid not null,
  run_id uuid not null,
  source_id uuid not null,
  public_job_key text not null check (
    char_length(btrim(public_job_key)) between 1 and 512
  ),
  title text not null check (char_length(btrim(title)) between 1 and 1000),
  location text not null check (
    char_length(btrim(location)) between 1 and 1000
  ),
  duty_excerpt text not null check (
    char_length(btrim(duty_excerpt)) between 1 and 32768
  ),
  requirement_excerpt text not null check (
    char_length(btrim(requirement_excerpt)) between 1 and 32768
  ),
  source_url text not null check (
    platform_hr.public_https_url_is_valid_v79(source_url)
  ),
  observed_at timestamptz not null,
  content_sha256 text not null check (content_sha256 ~ '^[a-f0-9]{64}$'),
  status text not null check (status in ('open','closed','unknown')),
  created_at timestamptz not null default now(),
  foreign key (run_id,owner_internal_user_id)
    references platform_hr.panorama_runs(run_id,owner_internal_user_id),
  foreign key (source_id,owner_internal_user_id)
    references platform_hr.talent_sources(
      source_id,owner_internal_user_id
    ),
  unique (snapshot_id,owner_internal_user_id),
  unique (owner_internal_user_id,source_id,public_job_key,content_sha256)
);

create index public_job_snapshots_observed_v79
  on platform_hr.public_job_snapshots(
    owner_internal_user_id,source_id,observed_at desc,snapshot_id
  );

create function platform_hr.guard_public_job_snapshot_immutability_v79()
returns trigger language plpgsql
set search_path=pg_catalog,platform_hr
as $function$
begin
  raise check_violation using message='public job snapshot is immutable';
end
$function$;

create trigger guard_public_job_snapshot_immutability_v79
before update or delete on platform_hr.public_job_snapshots
for each row execute function
  platform_hr.guard_public_job_snapshot_immutability_v79();

create table platform_hr.public_job_snapshot_requests (
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  client_request_id uuid not null,
  requested_snapshot_id uuid not null,
  run_id uuid not null,
  source_id uuid not null,
  public_job_key text not null check (
    char_length(btrim(public_job_key)) between 1 and 512
  ),
  result_snapshot_id uuid not null,
  source_url text not null check (
    platform_hr.public_https_url_is_valid_v79(source_url)
  ),
  observed_at timestamptz not null,
  status text not null check (status in ('open','closed','unknown')),
  payload_sha256 bytea not null check (octet_length(payload_sha256)=32),
  created_at timestamptz not null default now(),
  foreign key (run_id,owner_internal_user_id)
    references platform_hr.panorama_runs(run_id,owner_internal_user_id),
  foreign key (source_id,owner_internal_user_id)
    references platform_hr.talent_sources(
      source_id,owner_internal_user_id
    ),
  foreign key (result_snapshot_id,owner_internal_user_id)
    references platform_hr.public_job_snapshots(
      snapshot_id,owner_internal_user_id
    ),
  unique (owner_internal_user_id,client_request_id),
  unique (
    owner_internal_user_id,client_request_id,source_id,public_job_key,
    result_snapshot_id
  )
);

create function platform_hr.guard_public_job_observation_immutability_v79()
returns trigger language plpgsql
set search_path=pg_catalog,platform_hr
as $function$
begin
  raise check_violation using message='public job observation is immutable';
end
$function$;

create trigger guard_public_job_observation_immutability_v79
before update or delete on platform_hr.public_job_snapshot_requests
for each row execute function
  platform_hr.guard_public_job_observation_immutability_v79();

create table platform_hr.public_job_current_snapshots (
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  source_id uuid not null,
  public_job_key text not null check (
    char_length(btrim(public_job_key)) between 1 and 512
  ),
  snapshot_id uuid not null,
  latest_observation_client_request_id uuid not null,
  updated_at timestamptz not null default now(),
  foreign key (source_id,owner_internal_user_id)
    references platform_hr.talent_sources(
      source_id,owner_internal_user_id
    ),
  foreign key (snapshot_id,owner_internal_user_id)
    references platform_hr.public_job_snapshots(
      snapshot_id,owner_internal_user_id
    ),
  foreign key (
    owner_internal_user_id,latest_observation_client_request_id,
    source_id,public_job_key,snapshot_id
  ) references platform_hr.public_job_snapshot_requests(
    owner_internal_user_id,client_request_id,source_id,public_job_key,
    result_snapshot_id
  ),
  primary key (owner_internal_user_id,source_id,public_job_key)
);

create table platform_hr.talent_insight_versions (
  insight_version_id uuid primary key,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  client_request_id uuid not null,
  run_id uuid not null,
  version_number bigint not null check (version_number>0),
  selected_source_ids uuid[] not null check (
    cardinality(selected_source_ids) between 1 and 100
    and platform_hr.uuid_array_is_unique_v79(selected_source_ids)
  ),
  snapshot_ids uuid[] not null check (
    cardinality(snapshot_ids) between 1 and 1000
    and platform_hr.uuid_array_is_unique_v79(snapshot_ids)
  ),
  facts jsonb not null check (
    platform_hr.facts_have_https_urls_v79(facts)
    and octet_length(facts::text)<=262144
  ),
  inferences jsonb not null check (
    jsonb_typeof(inferences)='array'
    and octet_length(inferences::text)<=262144
  ),
  unknowns jsonb not null check (
    jsonb_typeof(unknowns)='array'
    and octet_length(unknowns::text)<=131072
  ),
  direction_clusters jsonb not null check (
    jsonb_typeof(direction_clusters)='object'
    and octet_length(direction_clusters::text)<=131072
  ),
  summary text not null check (
    char_length(btrim(summary)) between 1 and 32768
  ),
  source_conversation_id uuid not null,
  source_turn_id uuid not null,
  agent_id text not null check (
    char_length(btrim(agent_id)) between 1 and 128
  ),
  model_version text not null check (
    char_length(btrim(model_version)) between 1 and 160
  ),
  created_at timestamptz not null default now(),
  foreign key (run_id,owner_internal_user_id)
    references platform_hr.panorama_runs(run_id,owner_internal_user_id),
  foreign key (source_conversation_id,owner_internal_user_id)
    references platform_control.conversations(
      conversation_id,owner_internal_user_id
    ),
  foreign key (source_conversation_id,source_turn_id)
    references platform_control.conversation_turns(conversation_id,turn_id),
  check (platform_hr.insight_payload_is_valid_v79(facts,inferences,unknowns)),
  unique (insight_version_id,owner_internal_user_id),
  unique (owner_internal_user_id,client_request_id),
  unique (owner_internal_user_id,version_number)
);

create table platform_hr.talent_insight_sources (
  insight_version_id uuid not null,
  owner_internal_user_id uuid not null,
  source_id uuid not null,
  source_ordinal integer not null check (source_ordinal between 1 and 100),
  foreign key (insight_version_id,owner_internal_user_id)
    references platform_hr.talent_insight_versions(
      insight_version_id,owner_internal_user_id
    ),
  foreign key (source_id,owner_internal_user_id)
    references platform_hr.talent_sources(
      source_id,owner_internal_user_id
    ),
  unique (insight_version_id,source_id),
  unique (insight_version_id,source_ordinal)
);

create table platform_hr.talent_insight_snapshots (
  insight_version_id uuid not null,
  owner_internal_user_id uuid not null,
  snapshot_id uuid not null,
  snapshot_ordinal integer not null check (snapshot_ordinal between 1 and 1000),
  foreign key (insight_version_id,owner_internal_user_id)
    references platform_hr.talent_insight_versions(
      insight_version_id,owner_internal_user_id
    ),
  foreign key (snapshot_id,owner_internal_user_id)
    references platform_hr.public_job_snapshots(
      snapshot_id,owner_internal_user_id
    ),
  unique (insight_version_id,snapshot_id),
  unique (insight_version_id,snapshot_ordinal)
);

create index talent_insight_versions_created_v79
  on platform_hr.talent_insight_versions(
    owner_internal_user_id,created_at desc,insight_version_id
  );

create table platform_hr.position_insight_retrievals (
  retrieval_id uuid primary key,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  client_request_id uuid not null,
  position_id uuid not null,
  conversation_id uuid not null,
  turn_id uuid not null,
  insight_version_ids uuid[] not null check (
    cardinality(insight_version_ids) between 1 and 5
    and platform_hr.uuid_array_is_unique_v79(insight_version_ids)
  ),
  query_sha256 text not null check (query_sha256 ~ '^[a-f0-9]{64}$'),
  retrieved_excerpts jsonb not null check (
    jsonb_typeof(retrieved_excerpts)='array'
    and jsonb_array_length(retrieved_excerpts)<=100
    and octet_length(retrieved_excerpts::text)<=32768
  ),
  created_at timestamptz not null default now(),
  foreign key (position_id,owner_internal_user_id)
    references platform_hr.positions(position_id,owner_internal_user_id),
  foreign key (conversation_id,owner_internal_user_id)
    references platform_control.conversations(
      conversation_id,owner_internal_user_id
    ),
  foreign key (conversation_id,turn_id)
    references platform_control.conversation_turns(conversation_id,turn_id),
  unique (retrieval_id,owner_internal_user_id),
  unique (owner_internal_user_id,client_request_id)
);

create table platform_hr.position_insight_retrieval_versions (
  retrieval_id uuid not null,
  owner_internal_user_id uuid not null,
  insight_version_id uuid not null,
  insight_ordinal integer not null check (insight_ordinal between 1 and 5),
  foreign key (retrieval_id,owner_internal_user_id)
    references platform_hr.position_insight_retrievals(
      retrieval_id,owner_internal_user_id
    ),
  foreign key (insight_version_id,owner_internal_user_id)
    references platform_hr.talent_insight_versions(
      insight_version_id,owner_internal_user_id
    ),
  unique (retrieval_id,insight_version_id),
  unique (retrieval_id,insight_ordinal)
);

create index position_insight_retrievals_position_v79
  on platform_hr.position_insight_retrievals(
    owner_internal_user_id,position_id,created_at desc,retrieval_id
  );

create function platform_hr.guard_talent_insight_immutability_v79()
returns trigger language plpgsql
set search_path=pg_catalog,platform_hr
as $function$
begin
  raise check_violation using message='talent insight version is immutable';
end
$function$;

create trigger guard_talent_insight_immutability_v79
before update or delete on platform_hr.talent_insight_versions
for each row execute function
  platform_hr.guard_talent_insight_immutability_v79();

create trigger guard_talent_insight_sources_immutability_v79
before update or delete on platform_hr.talent_insight_sources
for each row execute function
  platform_hr.guard_talent_insight_immutability_v79();

create trigger guard_talent_insight_snapshots_immutability_v79
before update or delete on platform_hr.talent_insight_snapshots
for each row execute function
  platform_hr.guard_talent_insight_immutability_v79();

create function platform_hr.create_talent_source_v79(
  selected_source_id uuid,
  selected_owner_internal_user_id uuid,
  selected_client_request_id uuid,
  selected_company_key text,
  selected_canonical_name text,
  selected_aliases jsonb,
  selected_approved_public_urls jsonb,
  selected_active boolean
) returns platform_hr.talent_sources
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.talent_sources%rowtype;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <>
        (session_user='platform_control_app') then
    raise insufficient_privilege;
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':talent-source-request:' ||
    selected_client_request_id::text,0
  ));
  select * into selected from platform_hr.talent_sources source
  where source.owner_internal_user_id=selected_owner_internal_user_id
    and source.client_request_id=selected_client_request_id;
  if found then
    if selected.source_id is distinct from selected_source_id
      or selected.company_key is distinct from btrim(selected_company_key)
      or selected.canonical_name is distinct from btrim(selected_canonical_name)
      or selected.aliases is distinct from selected_aliases
      or selected.approved_public_urls
        is distinct from selected_approved_public_urls
      or selected.active is distinct from selected_active then
      raise unique_violation using
        message='talent source idempotency payload mismatch';
    end if;
    return selected;
  end if;
  insert into platform_hr.talent_sources(
    source_id,owner_internal_user_id,client_request_id,company_key,
    canonical_name,aliases,approved_public_urls,active
  ) values (
    selected_source_id,selected_owner_internal_user_id,
    selected_client_request_id,btrim(selected_company_key),
    btrim(selected_canonical_name),selected_aliases,
    selected_approved_public_urls,selected_active
  ) returning * into selected;
  return selected;
end
$function$;

create function platform_hr.list_talent_sources_v79(
  selected_owner_internal_user_id uuid,
  selected_include_inactive boolean,
  selected_limit integer
) returns setof platform_hr.talent_sources
language plpgsql stable security definer
set search_path=pg_catalog,platform_hr
as $function$
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <>
        (session_user='platform_control_app') then
    raise insufficient_privilege;
  end if;
  if selected_limit is null or selected_limit not between 1 and 100 then
    raise check_violation;
  end if;
  return query
    select source.* from platform_hr.talent_sources source
    where source.owner_internal_user_id=selected_owner_internal_user_id
      and (selected_include_inactive or source.active)
    order by source.created_at desc,source.source_id
    limit selected_limit;
end
$function$;

create function platform_hr.create_panorama_run_v79(
  selected_run_id uuid,
  selected_owner_internal_user_id uuid,
  selected_client_request_id uuid,
  selected_source_ids uuid[],
  selected_conversation_id uuid
) returns platform_hr.panorama_runs
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.panorama_runs%rowtype;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <>
        (session_user='platform_control_app') then
    raise insufficient_privilege;
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':panorama-run-request:' ||
    selected_client_request_id::text,0
  ));
  select * into selected from platform_hr.panorama_runs run
  where run.owner_internal_user_id=selected_owner_internal_user_id
    and run.client_request_id=selected_client_request_id;
  if found then
    if selected.run_id is distinct from selected_run_id
      or selected.selected_source_ids is distinct from selected_source_ids
      or selected.conversation_id is distinct from selected_conversation_id then
      raise unique_violation using
        message='panorama run idempotency payload mismatch';
    end if;
    return selected;
  end if;
  if cardinality(selected_source_ids) not between 1 and 100
    or not platform_hr.uuid_array_is_unique_v79(selected_source_ids) then
    raise check_violation using message='panorama source selection invalid';
  end if;
  perform 1 from platform_control.conversations conversation
  where conversation.conversation_id=selected_conversation_id
    and conversation.owner_internal_user_id=selected_owner_internal_user_id;
  if not found then raise no_data_found; end if;
  if (
    select count(*) from platform_hr.talent_sources source
    where source.owner_internal_user_id=selected_owner_internal_user_id
      and source.source_id=any(selected_source_ids) and source.active
  )<>cardinality(selected_source_ids) then
    raise no_data_found;
  end if;
  insert into platform_hr.panorama_runs(
    run_id,owner_internal_user_id,client_request_id,selected_source_ids,
    conversation_id
  ) values (
    selected_run_id,selected_owner_internal_user_id,
    selected_client_request_id,selected_source_ids,selected_conversation_id
  ) returning * into selected;
  insert into platform_hr.panorama_run_sources(
    run_id,owner_internal_user_id,source_id,source_ordinal
  )
  select selected_run_id,selected_owner_internal_user_id,source_id,ordinality
  from unnest(selected_source_ids) with ordinality source(source_id,ordinality);
  return selected;
end
$function$;

create function platform_hr.list_panorama_runs_v79(
  selected_owner_internal_user_id uuid,
  selected_limit integer
) returns setof platform_hr.panorama_runs
language plpgsql stable security definer
set search_path=pg_catalog,platform_hr
as $function$
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <>
        (session_user='platform_control_app') then
    raise insufficient_privilege;
  end if;
  if selected_limit is null or selected_limit not between 1 and 100 then
    raise check_violation;
  end if;
  return query
    select run.* from platform_hr.panorama_runs run
    where run.owner_internal_user_id=selected_owner_internal_user_id
    order by run.created_at desc,run.run_id
    limit selected_limit;
end
$function$;

create function platform_hr.transition_panorama_run_v79(
  selected_owner_internal_user_id uuid,
  selected_run_id uuid,
  selected_client_request_id uuid,
  selected_expected_row_version bigint,
  selected_state text,
  selected_error_code text,
  selected_source_failures jsonb
) returns platform_hr.panorama_runs
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare old platform_hr.panorama_runs%rowtype;
declare selected platform_hr.panorama_runs%rowtype;
declare replay platform_hr.panorama_run_transition_events%rowtype;
declare payload jsonb;
declare payload_hash bytea;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <>
        (session_user='platform_control_app') then
    raise insufficient_privilege;
  end if;
  payload := jsonb_build_object(
    'run_id',selected_run_id,'expected_row_version',selected_expected_row_version,
    'state',selected_state,'error_code',selected_error_code,
    'source_failures',selected_source_failures
  );
  payload_hash := sha256(convert_to(payload::text,'UTF8'));
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':panorama-transition-request:' ||
    selected_client_request_id::text,0
  ));
  select * into replay from platform_hr.panorama_run_transition_events event
  where event.owner_internal_user_id=selected_owner_internal_user_id
    and event.client_request_id=selected_client_request_id;
  if found then
    if replay.payload_sha256<>payload_hash then
      raise unique_violation using
        message='panorama run transition idempotency payload mismatch';
    end if;
    select * into selected from platform_hr.panorama_runs run
    where run.run_id=replay.run_id
      and run.owner_internal_user_id=selected_owner_internal_user_id;
    return selected;
  end if;
  select * into old from platform_hr.panorama_runs run
  where run.run_id=selected_run_id
    and run.owner_internal_user_id=selected_owner_internal_user_id
  for update;
  if not found then raise no_data_found; end if;
  if old.row_version<>selected_expected_row_version then
    raise serialization_failure using message='panorama run transition conflict';
  end if;
  if not (
    (old.state='queued' and selected_state='running')
    or (old.state='running' and selected_state in (
      'completed','partially_completed','failed'
    ))
  ) then
    raise check_violation using message='panorama run transition invalid';
  end if;
  if not platform_hr.source_failures_are_valid_v79(
    selected_source_failures,old.selected_source_ids
  ) then
    raise check_violation using message='panorama source failures invalid';
  end if;
  update platform_hr.panorama_runs set
    state=selected_state,
    error_code=selected_error_code,
    source_failures=selected_source_failures,
    row_version=row_version+1,
    started_at=case when selected_state='running' then now() else started_at end,
    finished_at=case when selected_state in (
      'completed','partially_completed','failed'
    ) then now() else null end,
    updated_at=now()
  where run_id=selected_run_id
    and owner_internal_user_id=selected_owner_internal_user_id
  returning * into selected;
  insert into platform_hr.panorama_run_transition_events(
    owner_internal_user_id,client_request_id,run_id,expected_row_version,
    previous_state,selected_state,selected_error_code,
    selected_source_failures,payload_sha256
  ) values (
    selected_owner_internal_user_id,selected_client_request_id,selected_run_id,
    selected_expected_row_version,old.state,selected_state,
    selected_error_code,selected_source_failures,payload_hash
  );
  return selected;
end
$function$;

create function platform_hr.create_public_job_snapshot_v79(
  selected_snapshot_id uuid,
  selected_owner_internal_user_id uuid,
  selected_client_request_id uuid,
  selected_run_id uuid,
  selected_source_id uuid,
  selected_public_job_key text,
  selected_title text,
  selected_location text,
  selected_duty_excerpt text,
  selected_requirement_excerpt text,
  selected_source_url text,
  selected_observed_at timestamptz,
  selected_content_sha256 text,
  selected_status text
) returns platform_hr.public_job_snapshots
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare run platform_hr.panorama_runs%rowtype;
declare source platform_hr.talent_sources%rowtype;
declare selected platform_hr.public_job_snapshots%rowtype;
declare replay platform_hr.public_job_snapshot_requests%rowtype;
declare payload jsonb;
declare payload_hash bytea;
declare current_observed_at timestamptz;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <>
        (session_user='platform_control_app') then
    raise insufficient_privilege;
  end if;
  payload := jsonb_build_object(
    'snapshot_id',selected_snapshot_id,'run_id',selected_run_id,
    'source_id',selected_source_id,'public_job_key',selected_public_job_key,
    'title',selected_title,'location',selected_location,
    'duty_excerpt',selected_duty_excerpt,
    'requirement_excerpt',selected_requirement_excerpt,
    'source_url',selected_source_url,'observed_at',selected_observed_at,
    'content_sha256',selected_content_sha256,'status',selected_status
  );
  payload_hash := sha256(convert_to(payload::text,'UTF8'));
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':public-job-request:' ||
    selected_client_request_id::text,0
  ));
  select * into replay from platform_hr.public_job_snapshot_requests request
  where request.owner_internal_user_id=selected_owner_internal_user_id
    and request.client_request_id=selected_client_request_id;
  if found then
    if replay.payload_sha256<>payload_hash then
      raise unique_violation using
        message='public job snapshot idempotency payload mismatch';
    end if;
    select * into selected from platform_hr.public_job_snapshots snapshot
    where snapshot.snapshot_id=replay.result_snapshot_id
      and snapshot.owner_internal_user_id=selected_owner_internal_user_id;
    return selected;
  end if;
  select * into run from platform_hr.panorama_runs run_record
  where run_record.run_id=selected_run_id
    and run_record.owner_internal_user_id=selected_owner_internal_user_id
    and run_record.state='running'
    and selected_source_id=any(run_record.selected_source_ids);
  if not found then raise no_data_found; end if;
  select * into source from platform_hr.talent_sources source_record
  where source_record.source_id=selected_source_id
    and source_record.owner_internal_user_id=selected_owner_internal_user_id
    and source_record.active;
  if not found then raise no_data_found; end if;
  if not platform_hr.url_is_approved_v79(
    selected_source_url,source.approved_public_urls
  ) then
    raise check_violation using message='public job source URL is not approved';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':public-job:' ||
    selected_source_id::text || ':' || btrim(selected_public_job_key),0
  ));
  select * into selected from platform_hr.public_job_snapshots snapshot
  where snapshot.owner_internal_user_id=selected_owner_internal_user_id
    and snapshot.source_id=selected_source_id
    and snapshot.public_job_key=btrim(selected_public_job_key)
    and snapshot.content_sha256=selected_content_sha256;
  if found then
    if selected.title is distinct from btrim(selected_title)
      or selected.location is distinct from btrim(selected_location)
      or selected.duty_excerpt is distinct from btrim(selected_duty_excerpt)
      or selected.requirement_excerpt
        is distinct from btrim(selected_requirement_excerpt) then
      raise check_violation using
        message='public job snapshot hash collision';
    end if;
  else
    insert into platform_hr.public_job_snapshots(
      snapshot_id,owner_internal_user_id,origin_client_request_id,run_id,
      source_id,public_job_key,title,location,duty_excerpt,
      requirement_excerpt,source_url,observed_at,content_sha256,status
    ) values (
      selected_snapshot_id,selected_owner_internal_user_id,
      selected_client_request_id,selected_run_id,selected_source_id,
      btrim(selected_public_job_key),btrim(selected_title),
      btrim(selected_location),btrim(selected_duty_excerpt),
      btrim(selected_requirement_excerpt),selected_source_url,
      selected_observed_at,selected_content_sha256,selected_status
    ) returning * into selected;
  end if;
  insert into platform_hr.public_job_snapshot_requests(
    owner_internal_user_id,client_request_id,requested_snapshot_id,run_id,
    source_id,public_job_key,result_snapshot_id,source_url,observed_at,status,
    payload_sha256
  ) values (
    selected_owner_internal_user_id,selected_client_request_id,
    selected_snapshot_id,selected_run_id,selected_source_id,
    btrim(selected_public_job_key),selected.snapshot_id,selected_source_url,
    selected_observed_at,selected_status,payload_hash
  );
  select observation.observed_at into current_observed_at
  from platform_hr.public_job_current_snapshots current_snapshot
  join platform_hr.public_job_snapshot_requests observation
    on observation.owner_internal_user_id=
      current_snapshot.owner_internal_user_id
    and observation.client_request_id=
      current_snapshot.latest_observation_client_request_id
  where current_snapshot.owner_internal_user_id=
      selected_owner_internal_user_id
    and current_snapshot.source_id=selected_source_id
    and current_snapshot.public_job_key=btrim(selected_public_job_key);
  if not found or selected_observed_at>=current_observed_at then
    insert into platform_hr.public_job_current_snapshots(
      owner_internal_user_id,source_id,public_job_key,snapshot_id,
      latest_observation_client_request_id
    ) values (
      selected_owner_internal_user_id,selected_source_id,
      btrim(selected_public_job_key),selected.snapshot_id,
      selected_client_request_id
    ) on conflict (owner_internal_user_id,source_id,public_job_key)
      do update set
        snapshot_id=excluded.snapshot_id,
        latest_observation_client_request_id=
          excluded.latest_observation_client_request_id,
        updated_at=now();
  end if;
  return selected;
end
$function$;

create function platform_hr.list_public_job_snapshots_v79(
  selected_owner_internal_user_id uuid,
  selected_source_id uuid,
  selected_limit integer
) returns setof platform_hr.public_job_snapshots
language plpgsql stable security definer
set search_path=pg_catalog,platform_hr
as $function$
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <>
        (session_user='platform_control_app') then
    raise insufficient_privilege;
  end if;
  if selected_limit is null or selected_limit not between 1 and 100 then
    raise check_violation;
  end if;
  return query
    select snapshot.* from platform_hr.public_job_snapshots snapshot
    left join platform_hr.public_job_current_snapshots current_snapshot
      on current_snapshot.owner_internal_user_id=
        snapshot.owner_internal_user_id
      and current_snapshot.source_id=snapshot.source_id
      and current_snapshot.public_job_key=snapshot.public_job_key
      and current_snapshot.snapshot_id=snapshot.snapshot_id
    where snapshot.owner_internal_user_id=selected_owner_internal_user_id
      and snapshot.source_id=selected_source_id
    order by (current_snapshot.snapshot_id is not null) desc,
      snapshot.observed_at desc,snapshot.created_at desc,
      snapshot.snapshot_id
    limit selected_limit;
end
$function$;

create function platform_hr.create_talent_insight_version_v79(
  selected_insight_version_id uuid,
  selected_owner_internal_user_id uuid,
  selected_client_request_id uuid,
  selected_run_id uuid,
  selected_source_ids uuid[],
  selected_snapshot_ids uuid[],
  selected_facts jsonb,
  selected_inferences jsonb,
  selected_unknowns jsonb,
  selected_direction_clusters jsonb,
  selected_summary text,
  selected_source_conversation_id uuid,
  selected_source_turn_id uuid,
  selected_agent_id text,
  selected_model_version text
) returns platform_hr.talent_insight_versions
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare run platform_hr.panorama_runs%rowtype;
declare selected platform_hr.talent_insight_versions%rowtype;
declare next_version bigint;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <>
        (session_user='platform_control_app') then
    raise insufficient_privilege;
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':talent-insight-request:' ||
    selected_client_request_id::text,0
  ));
  select * into selected from platform_hr.talent_insight_versions insight
  where insight.owner_internal_user_id=selected_owner_internal_user_id
    and insight.client_request_id=selected_client_request_id;
  if found then
    if selected.insight_version_id is distinct from selected_insight_version_id
      or selected.run_id is distinct from selected_run_id
      or selected.selected_source_ids is distinct from selected_source_ids
      or selected.snapshot_ids is distinct from selected_snapshot_ids
      or selected.facts is distinct from selected_facts
      or selected.inferences is distinct from selected_inferences
      or selected.unknowns is distinct from selected_unknowns
      or selected.direction_clusters is distinct from selected_direction_clusters
      or selected.summary is distinct from btrim(selected_summary)
      or selected.source_conversation_id
        is distinct from selected_source_conversation_id
      or selected.source_turn_id is distinct from selected_source_turn_id
      or selected.agent_id is distinct from btrim(selected_agent_id)
      or selected.model_version is distinct from btrim(selected_model_version) then
      raise unique_violation using
        message='talent insight idempotency payload mismatch';
    end if;
    return selected;
  end if;
  select * into run from platform_hr.panorama_runs run_record
  where run_record.run_id=selected_run_id
    and run_record.owner_internal_user_id=selected_owner_internal_user_id
    and run_record.conversation_id=selected_source_conversation_id
    and run_record.state in ('running','completed','partially_completed')
  for update;
  if not found then raise no_data_found; end if;
  if cardinality(selected_source_ids) not between 1 and 100
    or not platform_hr.uuid_array_is_unique_v79(selected_source_ids)
    or not selected_source_ids<@run.selected_source_ids then
    raise check_violation using message='talent insight source selection invalid';
  end if;
  if cardinality(selected_snapshot_ids) not between 1 and 1000
    or not platform_hr.uuid_array_is_unique_v79(selected_snapshot_ids)
    or (
      select count(*) from platform_hr.public_job_snapshots snapshot
      where snapshot.owner_internal_user_id=selected_owner_internal_user_id
        and snapshot.snapshot_id=any(selected_snapshot_ids)
        and snapshot.source_id=any(selected_source_ids)
    )<>cardinality(selected_snapshot_ids) then
    raise no_data_found;
  end if;
  perform 1 from platform_control.conversation_turns turn_record
  where turn_record.conversation_id=selected_source_conversation_id
    and turn_record.turn_id=selected_source_turn_id
    and turn_record.status='completed';
  if not found then raise no_data_found; end if;
  if not platform_hr.insight_payload_is_valid_v79(
      selected_facts,selected_inferences,selected_unknowns
    )
    or not platform_hr.facts_have_https_urls_v79(selected_facts)
    or exists (
      select 1 from jsonb_array_elements(selected_facts) fact
      where not exists (
        select 1 from platform_hr.talent_sources source
        where source.owner_internal_user_id=selected_owner_internal_user_id
          and source.source_id=any(selected_source_ids)
          and platform_hr.url_is_approved_v79(
            fact->>'source_url',source.approved_public_urls
          )
      )
    ) then
    raise check_violation using message='talent insight fact source invalid';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':talent-insight-version',0
  ));
  select coalesce(max(version_number),0)+1 into next_version
  from platform_hr.talent_insight_versions insight
  where insight.owner_internal_user_id=selected_owner_internal_user_id;
  insert into platform_hr.talent_insight_versions(
    insight_version_id,owner_internal_user_id,client_request_id,run_id,
    version_number,selected_source_ids,snapshot_ids,
    facts,inferences,unknowns,direction_clusters,
    summary,source_conversation_id,source_turn_id,agent_id,model_version
  ) values (
    selected_insight_version_id,selected_owner_internal_user_id,
    selected_client_request_id,selected_run_id,next_version,
    selected_source_ids,selected_snapshot_ids,selected_facts,selected_inferences,
    selected_unknowns,selected_direction_clusters,btrim(selected_summary),
    selected_source_conversation_id,selected_source_turn_id,
    btrim(selected_agent_id),btrim(selected_model_version)
  ) returning * into selected;
  insert into platform_hr.talent_insight_sources(
    insight_version_id,owner_internal_user_id,source_id,source_ordinal
  )
  select selected_insight_version_id,selected_owner_internal_user_id,
    source_id,ordinality
  from unnest(selected_source_ids) with ordinality source(source_id,ordinality);
  insert into platform_hr.talent_insight_snapshots(
    insight_version_id,owner_internal_user_id,snapshot_id,snapshot_ordinal
  )
  select selected_insight_version_id,selected_owner_internal_user_id,
    snapshot_id,ordinality
  from unnest(selected_snapshot_ids)
    with ordinality snapshot(snapshot_id,ordinality);
  return selected;
end
$function$;

create function platform_hr.list_talent_insight_versions_v79(
  selected_owner_internal_user_id uuid,
  selected_limit integer
) returns setof platform_hr.talent_insight_versions
language plpgsql stable security definer
set search_path=pg_catalog,platform_hr
as $function$
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <>
        (session_user='platform_control_app') then
    raise insufficient_privilege;
  end if;
  if selected_limit is null or selected_limit not between 1 and 100 then
    raise check_violation;
  end if;
  return query
    select insight.* from platform_hr.talent_insight_versions insight
    where insight.owner_internal_user_id=selected_owner_internal_user_id
    order by insight.version_number desc,insight.insight_version_id
    limit selected_limit;
end
$function$;

create function platform_hr.create_position_insight_retrieval_v79(
  selected_retrieval_id uuid,
  selected_owner_internal_user_id uuid,
  selected_client_request_id uuid,
  selected_position_id uuid,
  selected_conversation_id uuid,
  selected_turn_id uuid,
  selected_insight_version_ids uuid[],
  selected_query_sha256 text,
  selected_retrieved_excerpts jsonb
) returns platform_hr.position_insight_retrievals
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.position_insight_retrievals%rowtype;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <>
        (session_user='platform_control_app') then
    raise insufficient_privilege;
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':insight-retrieval-request:' ||
    selected_client_request_id::text,0
  ));
  select * into selected from platform_hr.position_insight_retrievals retrieval
  where retrieval.owner_internal_user_id=selected_owner_internal_user_id
    and retrieval.client_request_id=selected_client_request_id;
  if found then
    if selected.retrieval_id is distinct from selected_retrieval_id
      or selected.position_id is distinct from selected_position_id
      or selected.conversation_id is distinct from selected_conversation_id
      or selected.turn_id is distinct from selected_turn_id
      or selected.insight_version_ids
        is distinct from selected_insight_version_ids
      or selected.query_sha256 is distinct from selected_query_sha256
      or selected.retrieved_excerpts
        is distinct from selected_retrieved_excerpts then
      raise unique_violation using
        message='position insight retrieval idempotency payload mismatch';
    end if;
    return selected;
  end if;
  if cardinality(selected_insight_version_ids) not between 1 and 5
    or not platform_hr.uuid_array_is_unique_v79(
      selected_insight_version_ids
    ) then
    raise check_violation using message='insight retrieval selection invalid';
  end if;
  perform 1 from platform_hr.position_conversations binding
  where binding.position_id=selected_position_id
    and binding.conversation_id=selected_conversation_id
    and binding.owner_internal_user_id=selected_owner_internal_user_id;
  if not found then raise no_data_found; end if;
  perform 1 from platform_control.conversation_turns turn_record
  where turn_record.conversation_id=selected_conversation_id
    and turn_record.turn_id=selected_turn_id;
  if not found then raise no_data_found; end if;
  if (
    select count(*) from platform_hr.talent_insight_versions insight
    where insight.owner_internal_user_id=selected_owner_internal_user_id
      and insight.insight_version_id=any(selected_insight_version_ids)
  )<>cardinality(selected_insight_version_ids) then
    raise no_data_found;
  end if;
  insert into platform_hr.position_insight_retrievals(
    retrieval_id,owner_internal_user_id,client_request_id,position_id,
    conversation_id,turn_id,insight_version_ids,query_sha256,retrieved_excerpts
  ) values (
    selected_retrieval_id,selected_owner_internal_user_id,
    selected_client_request_id,selected_position_id,selected_conversation_id,
    selected_turn_id,selected_insight_version_ids,selected_query_sha256,
    selected_retrieved_excerpts
  ) returning * into selected;
  insert into platform_hr.position_insight_retrieval_versions(
    retrieval_id,owner_internal_user_id,insight_version_id,insight_ordinal
  )
  select selected_retrieval_id,selected_owner_internal_user_id,
    insight_version_id,ordinality
  from unnest(selected_insight_version_ids)
    with ordinality insight(insight_version_id,ordinality);
  return selected;
end
$function$;

create function platform_hr.list_position_insight_retrievals_v79(
  selected_owner_internal_user_id uuid,
  selected_position_id uuid,
  selected_limit integer
) returns setof platform_hr.position_insight_retrievals
language plpgsql stable security definer
set search_path=pg_catalog,platform_hr
as $function$
begin
  if session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <>
        (session_user='platform_control_app') then
    raise insufficient_privilege;
  end if;
  if selected_limit is null or selected_limit not between 1 and 100 then
    raise check_violation;
  end if;
  return query
    select retrieval.* from platform_hr.position_insight_retrievals retrieval
    where retrieval.owner_internal_user_id=selected_owner_internal_user_id
      and retrieval.position_id=selected_position_id
    order by retrieval.created_at desc,retrieval.retrieval_id
    limit selected_limit;
end
$function$;

revoke all on all tables in schema platform_hr from public;
revoke all on all functions in schema platform_hr from public;
revoke all on function platform_hr.create_talent_source_v79(
  uuid,uuid,uuid,text,text,jsonb,jsonb,boolean
) from public;
revoke all on function platform_hr.list_talent_sources_v79(
  uuid,boolean,integer
) from public;
revoke all on function platform_hr.create_panorama_run_v79(
  uuid,uuid,uuid,uuid[],uuid
) from public;
revoke all on function platform_hr.list_panorama_runs_v79(
  uuid,integer
) from public;
revoke all on function platform_hr.transition_panorama_run_v79(
  uuid,uuid,uuid,bigint,text,text,jsonb
) from public;
revoke all on function platform_hr.create_public_job_snapshot_v79(
  uuid,uuid,uuid,uuid,uuid,text,text,text,text,text,text,timestamptz,text,text
) from public;
revoke all on function platform_hr.list_public_job_snapshots_v79(
  uuid,uuid,integer
) from public;
revoke all on function platform_hr.create_talent_insight_version_v79(
  uuid,uuid,uuid,uuid,uuid[],uuid[],jsonb,jsonb,jsonb,jsonb,text,
  uuid,uuid,text,text
) from public;
revoke all on function platform_hr.list_talent_insight_versions_v79(
  uuid,integer
) from public;
revoke all on function platform_hr.create_position_insight_retrieval_v79(
  uuid,uuid,uuid,uuid,uuid,uuid,uuid[],text,jsonb
) from public;
revoke all on function platform_hr.list_position_insight_retrievals_v79(
  uuid,uuid,integer
) from public;

do $migration$
declare selected_app name;
begin
  if current_database()='agent_platform_control'
     and current_user='platform_control_owner' then
    selected_app := 'platform_control_app';
  elsif current_database()='agent_platform_control_preview'
        and current_user='platform_control_owner_preview' then
    selected_app := 'platform_control_app_preview';
  else
    raise insufficient_privilege using
      message='HR Panorama migration owner/environment mismatch';
  end if;
  execute format(
    'grant execute on function platform_hr.create_talent_source_v79('
    'uuid,uuid,uuid,text,text,jsonb,jsonb,boolean) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.list_talent_sources_v79('
    'uuid,boolean,integer) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.create_panorama_run_v79('
    'uuid,uuid,uuid,uuid[],uuid) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.list_panorama_runs_v79('
    'uuid,integer) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.transition_panorama_run_v79('
    'uuid,uuid,uuid,bigint,text,text,jsonb) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.create_public_job_snapshot_v79('
    'uuid,uuid,uuid,uuid,uuid,text,text,text,text,text,text,timestamptz,text,text) '
    'to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.list_public_job_snapshots_v79('
    'uuid,uuid,integer) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.create_talent_insight_version_v79('
    'uuid,uuid,uuid,uuid,uuid[],uuid[],jsonb,jsonb,jsonb,jsonb,text,'
    'uuid,uuid,text,text) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.list_talent_insight_versions_v79('
    'uuid,integer) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.create_position_insight_retrieval_v79('
    'uuid,uuid,uuid,uuid,uuid,uuid,uuid[],text,jsonb) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.list_position_insight_retrievals_v79('
    'uuid,uuid,integer) to %I',selected_app
  );
end
$migration$;
