create table platform_hr.official_position_versions (
  official_position_version_id uuid primary key,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  position_id uuid not null,
  client_request_id uuid not null,
  official_job_id text not null
    check (official_job_id ~ '^J[0-9]{4,12}$'),
  title text not null check (char_length(btrim(title)) between 1 and 500),
  department text check (
    department is null or char_length(btrim(department)) between 1 and 500
  ),
  locations jsonb not null check (
    jsonb_typeof(locations)='array' and octet_length(locations::text)<=32768
  ),
  category text not null check (char_length(btrim(category)) between 1 and 500),
  subcategory text check (
    subcategory is null or char_length(btrim(subcategory)) between 1 and 500
  ),
  headcount integer not null check (headcount>=0),
  degree text check (degree is null or char_length(btrim(degree)) between 1 and 500),
  employment_type text not null
    check (char_length(btrim(employment_type)) between 1 and 500),
  salary text not null check (char_length(btrim(salary)) between 1 and 1000),
  duty text not null check (char_length(btrim(duty)) between 1 and 131072),
  requirement text not null
    check (char_length(btrim(requirement)) between 1 and 131072),
  source_version text not null check (char_length(source_version) between 1 and 256),
  source_changed_at timestamptz not null,
  source_snapshot_at timestamptz not null,
  content_hash text not null check (content_hash ~ '^[a-f0-9]{64}$'),
  first_observed_at timestamptz not null,
  last_observed_at timestamptz not null,
  official_status text not null check (
    official_status in ('active','stale','suspected_inactive','inactive')
  ),
  status_reason text not null
    check (char_length(btrim(status_reason)) between 1 and 1000),
  evidence jsonb not null check (
    jsonb_typeof(evidence)='object' and octet_length(evidence::text)<=65536
  ),
  consecutive_misses integer not null default 0 check (consecutive_misses>=0),
  official_status_code integer not null default 0 check (official_status_code>=0),
  created_at timestamptz not null default now(),
  foreign key (position_id,owner_internal_user_id)
    references platform_hr.positions(position_id,owner_internal_user_id),
  unique (official_position_version_id,owner_internal_user_id),
  unique (official_position_version_id,owner_internal_user_id,position_id),
  unique (owner_internal_user_id,client_request_id),
  unique (owner_internal_user_id,position_id,source_version,content_hash),
  check (first_observed_at<=last_observed_at)
);

create table platform_hr.position_context_versions (
  context_version_id uuid primary key,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  position_id uuid not null,
  client_request_id uuid not null,
  version_number integer not null check (version_number>0),
  state text not null check (state in ('draft','confirmed','superseded')),
  modules jsonb not null check (
    jsonb_typeof(modules)='object' and modules<>'{}'::jsonb
    and octet_length(modules::text)<=524288
    and modules-'mission'-'jd'-'jr'-'competencies'-'talent_profile'
      -'sourcing_strategy'-'interview_standard'-'unknowns'='{}'::jsonb
  ),
  summary text not null check (char_length(btrim(summary)) between 1 and 32768),
  official_position_version_id uuid,
  base_context_version_id uuid,
  source_conversation_id uuid,
  source_turn_id uuid,
  source_artifact_version_id uuid
    references platform_attachments.artifact_versions(artifact_version_id),
  source_material_attachment_ids uuid[] not null default '{}'::uuid[]
    check (cardinality(source_material_attachment_ids)<=100),
  agent_id text check (agent_id is null or char_length(agent_id) between 1 and 128),
  model_version text check (
    model_version is null or char_length(model_version) between 1 and 160
  ),
  created_by uuid not null references platform_control.internal_users(internal_user_id),
  confirmed_by uuid references platform_control.internal_users(internal_user_id),
  confirmed_at timestamptz,
  confirmed_module_names text[] not null default '{}'::text[],
  confirmation_source_draft_id uuid,
  confirmation_source_row_version bigint check (confirmation_source_row_version>0),
  row_version bigint not null default 1 check (row_version>0),
  created_at timestamptz not null default now(),
  foreign key (position_id,owner_internal_user_id)
    references platform_hr.positions(position_id,owner_internal_user_id),
  foreign key (official_position_version_id,owner_internal_user_id)
    references platform_hr.official_position_versions(
      official_position_version_id,owner_internal_user_id
    ),
  foreign key (base_context_version_id,owner_internal_user_id)
    references platform_hr.position_context_versions(
      context_version_id,owner_internal_user_id
    ),
  foreign key (confirmation_source_draft_id,owner_internal_user_id)
    references platform_hr.position_context_versions(
      context_version_id,owner_internal_user_id
    ),
  foreign key (source_conversation_id,owner_internal_user_id)
    references platform_control.conversations(
      conversation_id,owner_internal_user_id
    ),
  foreign key (source_conversation_id,source_turn_id)
    references platform_control.conversation_turns(conversation_id,turn_id),
  unique (context_version_id,owner_internal_user_id),
  unique (context_version_id,owner_internal_user_id,position_id),
  unique (owner_internal_user_id,client_request_id),
  check (source_turn_id is null or source_conversation_id is not null),
  check (
    (state='draft' and confirmed_by is null and confirmed_at is null)
    or (state in ('confirmed','superseded')
      and confirmed_by is not null and confirmed_at is not null)
  ),
  check (
    (confirmation_source_draft_id is null and confirmation_source_row_version is null)
    or (confirmation_source_draft_id is not null and confirmation_source_row_version is not null)
  )
);

create unique index one_current_confirmed_context_v69
  on platform_hr.position_context_versions(position_id)
  where state='confirmed';

alter table platform_hr.positions
  add column current_official_version_id uuid,
  add column current_context_version_id uuid,
  add foreign key (current_official_version_id,owner_internal_user_id)
    references platform_hr.official_position_versions(
      official_position_version_id,owner_internal_user_id
    ),
  add foreign key (current_context_version_id,owner_internal_user_id)
    references platform_hr.position_context_versions(
      context_version_id,owner_internal_user_id
    );

create table platform_hr.position_task_requests (
  task_request_id uuid primary key,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  position_id uuid not null,
  client_request_id uuid not null,
  canonical_payload_sha256 text not null
    check (canonical_payload_sha256 ~ '^[a-f0-9]{64}$'),
  task_kind text not null check (task_kind in (
    'jd','jr','talent_profile','sourcing_strategy','position_interview_plan',
    'candidate_match','candidate_interview_plan','candidate_comparison','freeform'
  )),
  expected_context_version_id uuid,
  material_attachment_ids uuid[] not null default '{}'::uuid[]
    check (cardinality(material_attachment_ids)<=100),
  candidate_id uuid,
  position_candidate_id uuid,
  status text not null default 'active' check (status in ('active','consumed','cancelled')),
  created_at timestamptz not null default now(),
  foreign key (position_id,owner_internal_user_id)
    references platform_hr.positions(position_id,owner_internal_user_id),
  foreign key (expected_context_version_id,owner_internal_user_id,position_id)
    references platform_hr.position_context_versions(
      context_version_id,owner_internal_user_id,position_id
    ),
  unique (owner_internal_user_id,position_id,client_request_id),
  check ((candidate_id is null)=(position_candidate_id is null)),
  check (
    (task_kind in ('candidate_match','candidate_interview_plan')
      and candidate_id is not null and expected_context_version_id is not null)
    or (task_kind='candidate_comparison' and candidate_id is null)
    or (task_kind not in ('candidate_match','candidate_interview_plan','candidate_comparison')
      and candidate_id is null)
  )
);

create table platform_hr.position_task_records (
  task_record_id uuid primary key,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  position_id uuid not null,
  client_request_id uuid not null,
  task_kind text not null check (task_kind in (
    'jd','jr','talent_profile','sourcing_strategy','position_interview_plan',
    'candidate_match','candidate_interview_plan','candidate_comparison','freeform'
  )),
  official_position_version_id uuid,
  context_version_id uuid,
  material_attachment_ids uuid[] not null default '{}'::uuid[]
    check (cardinality(material_attachment_ids)<=100),
  candidate_id uuid,
  position_candidate_id uuid,
  document_attachment_ids uuid[] not null default '{}'::uuid[]
    check (cardinality(document_attachment_ids)<=100),
  human_feedback_ids uuid[] not null default '{}'::uuid[]
    check (cardinality(human_feedback_ids)<=100),
  conversation_id uuid not null,
  turn_id uuid not null,
  output_artifact_version_id uuid
    references platform_attachments.artifact_versions(artifact_version_id),
  draft_context_version_id uuid,
  prompt_context text not null
    check (char_length(prompt_context) between 1 and 131072),
  canonical_sha256 text not null check (canonical_sha256 ~ '^[a-f0-9]{64}$'),
  created_at timestamptz not null default now(),
  foreign key (position_id,owner_internal_user_id)
    references platform_hr.positions(position_id,owner_internal_user_id),
  foreign key (official_position_version_id,owner_internal_user_id,position_id)
    references platform_hr.official_position_versions(
      official_position_version_id,owner_internal_user_id,position_id
    ),
  foreign key (context_version_id,owner_internal_user_id,position_id)
    references platform_hr.position_context_versions(
      context_version_id,owner_internal_user_id,position_id
    ),
  foreign key (draft_context_version_id,owner_internal_user_id)
    references platform_hr.position_context_versions(
      context_version_id,owner_internal_user_id
    ),
  foreign key (conversation_id,owner_internal_user_id)
    references platform_control.conversations(
      conversation_id,owner_internal_user_id
    ),
  foreign key (conversation_id,turn_id)
    references platform_control.conversation_turns(conversation_id,turn_id),
  unique (owner_internal_user_id,client_request_id),
  unique (owner_internal_user_id,conversation_id,turn_id)
);

create function platform_hr.guard_context_version_immutability_v69()
returns trigger language plpgsql
set search_path=pg_catalog,platform_hr
as $function$
begin
  if tg_op='DELETE' and old.state in ('confirmed','superseded') then
    raise check_violation using message='confirmed context version is immutable';
  end if;
  if tg_op='UPDATE' and old.state in ('confirmed','superseded') then
    if not (
      old.state='confirmed' and new.state='superseded'
      and (to_jsonb(new)-'state')=(to_jsonb(old)-'state')
    ) then
      raise check_violation using message='confirmed context version is immutable';
    end if;
  end if;
  return case when tg_op='DELETE' then old else new end;
end
$function$;

create trigger guard_context_version_immutability_v69
before update or delete on platform_hr.position_context_versions
for each row execute function platform_hr.guard_context_version_immutability_v69();

create function platform_hr.guard_official_version_immutability_v69()
returns trigger language plpgsql
set search_path=pg_catalog,platform_hr
as $function$
begin
  raise check_violation using message='official position version is immutable';
end
$function$;

create trigger guard_official_version_immutability_v69
before update or delete on platform_hr.official_position_versions
for each row execute function platform_hr.guard_official_version_immutability_v69();

create function platform_hr.guard_position_task_record_immutability_v69()
returns trigger language plpgsql
set search_path=pg_catalog,platform_hr
as $function$
begin
  if tg_op='DELETE' then
    if session_user in (
      'platform_control_app','platform_control_app_preview',
      'platform_brain_worker','platform_brain_worker_preview'
    ) then
      raise check_violation using message='position task record is immutable';
    end if;
    return old;
  end if;
  if not (
    (to_jsonb(new)-'output_artifact_version_id'-'draft_context_version_id')=
      (to_jsonb(old)-'output_artifact_version_id'-'draft_context_version_id')
    and (old.output_artifact_version_id is null
      or new.output_artifact_version_id=old.output_artifact_version_id)
    and (old.draft_context_version_id is null
      or new.draft_context_version_id=old.draft_context_version_id)
  ) then
    raise check_violation using message='position task record is immutable';
  end if;
  return new;
end
$function$;

create trigger guard_position_task_record_immutability_v69
before update or delete on platform_hr.position_task_records
for each row execute function platform_hr.guard_position_task_record_immutability_v69();

create function platform_hr.project_official_version_v69(
  selected_official_position_version_id uuid,
  selected_owner_internal_user_id uuid,
  selected_position_id uuid,
  selected_client_request_id uuid,
  selected_official_job_id text,
  selected_title text,
  selected_department text,
  selected_locations jsonb,
  selected_category text,
  selected_subcategory text,
  selected_headcount integer,
  selected_degree text,
  selected_employment_type text,
  selected_salary text,
  selected_duty text,
  selected_requirement text,
  selected_source_version text,
  selected_source_changed_at timestamptz,
  selected_content_hash text,
  selected_first_observed_at timestamptz,
  selected_last_observed_at timestamptz,
  selected_official_status text,
  selected_status_reason text,
  selected_evidence jsonb,
  selected_consecutive_misses integer,
  selected_official_status_code integer,
  selected_source_snapshot_at timestamptz
) returns platform_hr.official_position_versions
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.official_position_versions%rowtype;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':official-version:' ||
    selected_position_id::text,0
  ));
  select * into selected from platform_hr.official_position_versions
  where owner_internal_user_id=selected_owner_internal_user_id
    and client_request_id=selected_client_request_id;
  if found then
    if selected.position_id<>selected_position_id
      or selected.content_hash<>selected_content_hash
      or selected.official_position_version_id<>selected_official_position_version_id
      or selected.official_job_id<>selected_official_job_id
      or selected.title<>btrim(selected_title)
      or selected.department is distinct from nullif(btrim(selected_department),'')
      or selected.locations<>selected_locations
      or selected.category<>btrim(selected_category)
      or selected.subcategory is distinct from nullif(btrim(selected_subcategory),'')
      or selected.headcount<>selected_headcount
      or selected.degree is distinct from nullif(btrim(selected_degree),'')
      or selected.employment_type<>btrim(selected_employment_type)
      or selected.salary<>btrim(selected_salary)
      or selected.duty<>btrim(selected_duty)
      or selected.requirement<>btrim(selected_requirement)
      or selected.source_version<>selected_source_version
      or selected.source_changed_at<>selected_source_changed_at
      or selected.first_observed_at<>selected_first_observed_at
      or selected.last_observed_at<>selected_last_observed_at
      or selected.official_status<>selected_official_status
      or selected.status_reason<>btrim(selected_status_reason)
      or selected.evidence<>selected_evidence
      or selected.consecutive_misses<>selected_consecutive_misses
      or selected.official_status_code<>selected_official_status_code
      or selected.source_snapshot_at<>selected_source_snapshot_at then
      raise unique_violation using message='official version idempotency payload mismatch';
    end if;
    return selected;
  end if;
  perform 1 from platform_hr.positions
  where position_id=selected_position_id
    and owner_internal_user_id=selected_owner_internal_user_id
    and source_kind='official_site' and official_job_id=selected_official_job_id;
  if not found then raise no_data_found; end if;
  insert into platform_hr.official_position_versions(
      official_position_version_id,owner_internal_user_id,position_id,
      client_request_id,official_job_id,title,department,locations,category,
      subcategory,headcount,degree,employment_type,salary,duty,requirement,
      source_version,source_changed_at,source_snapshot_at,content_hash,first_observed_at,
      last_observed_at,official_status,status_reason,evidence,
      consecutive_misses,official_status_code
    ) values (
      selected_official_position_version_id,selected_owner_internal_user_id,
      selected_position_id,selected_client_request_id,selected_official_job_id,
      btrim(selected_title),nullif(btrim(selected_department),''),selected_locations,
      btrim(selected_category),nullif(btrim(selected_subcategory),''),
      selected_headcount,nullif(btrim(selected_degree),''),
      btrim(selected_employment_type),btrim(selected_salary),btrim(selected_duty),
      btrim(selected_requirement),selected_source_version,
      selected_source_changed_at,selected_source_snapshot_at,
      selected_content_hash,selected_first_observed_at,
      selected_last_observed_at,selected_official_status,
      btrim(selected_status_reason),selected_evidence,
      selected_consecutive_misses,selected_official_status_code
    ) returning * into selected;
  update platform_hr.positions set
    current_official_version_id=selected.official_position_version_id
  where position_id=selected_position_id
    and owner_internal_user_id=selected_owner_internal_user_id
    and (
      current_official_version_id is null
      or not exists (
        select 1 from platform_hr.official_position_versions current_version
        where current_version.official_position_version_id=current_official_version_id
          and current_version.owner_internal_user_id=selected_owner_internal_user_id
          and (
            current_version.source_snapshot_at,
            current_version.last_observed_at,
            current_version.source_changed_at,
            current_version.source_version
          ) >= (
            selected_source_snapshot_at,selected_last_observed_at,
            selected_source_changed_at,selected_source_version
          )
      )
    );
  return selected;
end
$function$;

create function platform_hr.create_context_draft_v69(
  selected_context_version_id uuid,
  selected_owner_internal_user_id uuid,
  selected_position_id uuid,
  selected_client_request_id uuid,
  selected_base_context_version_id uuid,
  selected_official_position_version_id uuid,
  selected_modules jsonb,
  selected_summary text,
  selected_source_conversation_id uuid,
  selected_source_turn_id uuid,
  selected_source_artifact_version_id uuid,
  selected_source_material_attachment_ids uuid[],
  selected_agent_id text,
  selected_model_version text,
  selected_created_by uuid
) returns platform_hr.position_context_versions
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.position_context_versions%rowtype;
declare current_context_id uuid;
declare next_version integer;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':context-draft:' ||
    selected_client_request_id::text,0
  ));
  select * into selected from platform_hr.position_context_versions
  where owner_internal_user_id=selected_owner_internal_user_id
    and client_request_id=selected_client_request_id;
  if found then
    if selected.context_version_id<>selected_context_version_id
      or selected.position_id<>selected_position_id
      or selected.base_context_version_id is distinct from selected_base_context_version_id
      or selected.official_position_version_id is distinct from selected_official_position_version_id
      or selected.modules<>selected_modules
      or selected.summary<>btrim(selected_summary)
      or selected.source_conversation_id is distinct from selected_source_conversation_id
      or selected.source_turn_id is distinct from selected_source_turn_id
      or selected.source_artifact_version_id is distinct from selected_source_artifact_version_id
      or selected.source_material_attachment_ids<>selected_source_material_attachment_ids
      or selected.agent_id is distinct from selected_agent_id
      or selected.model_version is distinct from selected_model_version
      or selected.created_by<>selected_created_by then
      raise unique_violation using message='context draft idempotency payload mismatch';
    end if;
    return selected;
  end if;
  select current_context_version_id into current_context_id
  from platform_hr.positions where position_id=selected_position_id
    and owner_internal_user_id=selected_owner_internal_user_id for update;
  if not found then raise no_data_found; end if;
  if current_context_id is distinct from selected_base_context_version_id then
    raise serialization_failure using message='context baseline conflict';
  end if;
  if selected_official_position_version_id is not null then
    perform 1 from platform_hr.official_position_versions
    where official_position_version_id=selected_official_position_version_id
      and owner_internal_user_id=selected_owner_internal_user_id
      and position_id=selected_position_id;
    if not found then raise no_data_found; end if;
  end if;
  if selected_source_conversation_id is not null then
    perform 1 from platform_hr.position_conversations binding
    where binding.owner_internal_user_id=selected_owner_internal_user_id
      and binding.position_id=selected_position_id
      and binding.conversation_id=selected_source_conversation_id;
    if not found then raise no_data_found; end if;
  end if;
  if selected_source_artifact_version_id is not null then
    perform 1
    from platform_attachments.artifact_versions version
    join platform_attachments.artifacts artifact
      on artifact.artifact_id=version.artifact_id
    join platform_attachments.attachments attachment
      on attachment.attachment_id=version.attachment_id
      and attachment.owner_internal_user_id=artifact.owner_internal_user_id
    join platform_hr.position_artifacts position_artifact
      on position_artifact.artifact_id=artifact.artifact_id
      and position_artifact.owner_internal_user_id=artifact.owner_internal_user_id
    where version.artifact_version_id=selected_source_artifact_version_id
      and artifact.owner_internal_user_id=selected_owner_internal_user_id
      and position_artifact.position_id=selected_position_id
      and version.state='ready' and version.result_status='succeeded'
      and version.retained_until>now() and version.immutable_locator is not null
      and attachment.state='ready' and attachment.deleted_at is null
      and attachment.retained_until>now() and attachment.immutable_locator is not null
      and not exists (
        select 1 from platform_attachments.erasure_jobs erasure
        where erasure.attachment_id=attachment.attachment_id
      );
    if not found then raise no_data_found; end if;
  end if;
  if exists (
    select 1 from unnest(selected_source_material_attachment_ids)
      as selected_attachment(attachment_id)
    left join platform_hr.position_materials material
      on material.attachment_id=selected_attachment.attachment_id
      and material.position_id=selected_position_id
      and material.owner_internal_user_id=selected_owner_internal_user_id
      and material.active
    left join platform_attachments.attachments attachment
      on attachment.attachment_id=material.attachment_id
      and attachment.owner_internal_user_id=material.owner_internal_user_id
      and attachment.state='ready' and attachment.deleted_at is null
      and attachment.retained_until>now() and attachment.immutable_locator is not null
    where material.attachment_id is null or attachment.attachment_id is null
      or exists (
        select 1 from platform_attachments.erasure_jobs erasure
        where erasure.attachment_id=selected_attachment.attachment_id
      )
  ) then raise no_data_found; end if;
  select coalesce(max(version_number),0)+1 into next_version
  from platform_hr.position_context_versions
  where position_id=selected_position_id and state in ('confirmed','superseded');
  insert into platform_hr.position_context_versions(
    context_version_id,owner_internal_user_id,position_id,client_request_id,
    version_number,state,modules,summary,official_position_version_id,
    base_context_version_id,source_conversation_id,source_turn_id,
    source_artifact_version_id,source_material_attachment_ids,agent_id,
    model_version,created_by
  ) values (
    selected_context_version_id,selected_owner_internal_user_id,
    selected_position_id,selected_client_request_id,next_version,'draft',
    selected_modules,btrim(selected_summary),selected_official_position_version_id,
    selected_base_context_version_id,selected_source_conversation_id,
    selected_source_turn_id,selected_source_artifact_version_id,
    selected_source_material_attachment_ids,selected_agent_id,
    selected_model_version,selected_created_by
  ) returning * into selected;
  return selected;
end
$function$;

create function platform_hr.confirm_context_modules_v69(
  selected_owner_internal_user_id uuid,
  selected_position_id uuid,
  selected_draft_context_version_id uuid,
  selected_client_request_id uuid,
  selected_expected_current_context_version_id uuid,
  selected_expected_draft_row_version bigint,
  selected_module_names text[],
  selected_confirmed_by uuid
) returns platform_hr.position_context_versions
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare draft platform_hr.position_context_versions%rowtype;
declare current_context platform_hr.position_context_versions%rowtype;
declare selected platform_hr.position_context_versions%rowtype;
declare confirmed_modules jsonb;
declare remaining_modules jsonb;
declare new_context_id uuid;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':context-confirm:' ||
    selected_position_id::text,0
  ));
  select * into selected from platform_hr.position_context_versions
  where owner_internal_user_id=selected_owner_internal_user_id
    and client_request_id=selected_client_request_id;
  if found then
    if selected.position_id<>selected_position_id
      or selected.confirmation_source_draft_id<>selected_draft_context_version_id
      or selected.base_context_version_id is distinct from selected_expected_current_context_version_id
      or selected.confirmed_module_names<>selected_module_names
      or selected.confirmed_by<>selected_confirmed_by
      or selected.confirmation_source_row_version
        is distinct from selected_expected_draft_row_version then
      raise unique_violation using message='context confirmation idempotency payload mismatch';
    end if;
    return selected;
  end if;
  select * into draft from platform_hr.position_context_versions
  where context_version_id=selected_draft_context_version_id
    and owner_internal_user_id=selected_owner_internal_user_id
    and position_id=selected_position_id and state='draft' for update;
  if not found then raise no_data_found; end if;
  if draft.row_version<>selected_expected_draft_row_version then
    raise serialization_failure using message='context draft row conflict';
  end if;
  perform 1 from unnest(selected_module_names) module_name
  where not (draft.modules ? module_name);
  if found or cardinality(selected_module_names)=0 then
    raise check_violation using message='context selected modules invalid';
  end if;
  select * into current_context from platform_hr.position_context_versions
  where context_version_id=selected_expected_current_context_version_id
    and owner_internal_user_id=selected_owner_internal_user_id
    and position_id=selected_position_id and state='confirmed';
  if selected_expected_current_context_version_id is not null and not found then
    raise serialization_failure using message='context baseline conflict';
  end if;
  perform 1 from platform_hr.positions
  where position_id=selected_position_id
    and owner_internal_user_id=selected_owner_internal_user_id
    and current_context_version_id is not distinct from
      selected_expected_current_context_version_id for update;
  if not found then
    raise serialization_failure using message='context baseline conflict';
  end if;
  select coalesce(jsonb_object_agg(key,value),'{}'::jsonb)
  into confirmed_modules from jsonb_each(draft.modules)
  where key=any(selected_module_names);
  remaining_modules := draft.modules - selected_module_names;
  new_context_id := md5(
    selected_owner_internal_user_id::text || selected_client_request_id::text
  )::uuid;
  if selected_expected_current_context_version_id is not null then
    update platform_hr.position_context_versions set state='superseded'
    where context_version_id=selected_expected_current_context_version_id;
  end if;
  insert into platform_hr.position_context_versions(
    context_version_id,owner_internal_user_id,position_id,client_request_id,
    version_number,state,modules,summary,official_position_version_id,
    base_context_version_id,source_conversation_id,source_turn_id,
    source_artifact_version_id,source_material_attachment_ids,agent_id,
    model_version,created_by,confirmed_by,confirmed_at,
    confirmed_module_names,confirmation_source_draft_id,
    confirmation_source_row_version
  ) values (
    new_context_id,selected_owner_internal_user_id,selected_position_id,
    selected_client_request_id,
    coalesce(current_context.version_number,0)+1,'confirmed',
    coalesce(current_context.modules,'{}'::jsonb)||confirmed_modules,
    draft.summary,draft.official_position_version_id,
    selected_expected_current_context_version_id,draft.source_conversation_id,
    draft.source_turn_id,draft.source_artifact_version_id,
    draft.source_material_attachment_ids,draft.agent_id,draft.model_version,
    draft.created_by,selected_confirmed_by,now(),selected_module_names,
    selected_draft_context_version_id,selected_expected_draft_row_version
  ) returning * into selected;
  if remaining_modules='{}'::jsonb then
    update platform_hr.position_context_versions set state='superseded',
      confirmed_by=selected_confirmed_by,confirmed_at=now(),row_version=row_version+1
    where context_version_id=selected_draft_context_version_id;
  else
    update platform_hr.position_context_versions set modules=remaining_modules,
      row_version=row_version+1
    where context_version_id=selected_draft_context_version_id;
  end if;
  update platform_hr.positions set current_context_version_id=new_context_id
  where position_id=selected_position_id
    and owner_internal_user_id=selected_owner_internal_user_id;
  return selected;
end
$function$;

create function platform_hr.validate_position_materials_v69(
  selected_owner_internal_user_id uuid,
  selected_position_id uuid,
  selected_attachment_ids uuid[]
) returns boolean
language sql stable security definer
set search_path=pg_catalog,platform_hr
as $function$
  select cardinality(selected_attachment_ids)=(
    select count(distinct selected_attachment.attachment_id)
    from unnest(selected_attachment_ids) selected_attachment(attachment_id)
    join platform_hr.position_materials material
      on material.attachment_id=selected_attachment.attachment_id
      and material.owner_internal_user_id=selected_owner_internal_user_id
      and material.position_id=selected_position_id and material.active
    join platform_attachments.attachments attachment
      on attachment.attachment_id=material.attachment_id
      and attachment.owner_internal_user_id=material.owner_internal_user_id
    where attachment.state='ready' and attachment.deleted_at is null
      and attachment.retained_until>now() and attachment.immutable_locator is not null
      and not exists (
        select 1 from platform_attachments.erasure_jobs erasure
        where erasure.attachment_id=attachment.attachment_id
      )
  )
$function$;

create function platform_hr.create_position_task_request_v69(
  selected_task_request_id uuid,
  selected_owner_internal_user_id uuid,
  selected_position_id uuid,
  selected_client_request_id uuid,
  selected_canonical_payload_sha256 text,
  selected_task_kind text,
  selected_expected_context_version_id uuid,
  selected_material_attachment_ids uuid[],
  selected_candidate_id uuid,
  selected_position_candidate_id uuid
) returns platform_hr.position_task_requests
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.position_task_requests%rowtype;
declare current_context_id uuid;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':position-task-request:' ||
    selected_position_id::text || ':' || selected_client_request_id::text,0
  ));
  select * into selected from platform_hr.position_task_requests
  where owner_internal_user_id=selected_owner_internal_user_id
    and position_id=selected_position_id
    and client_request_id=selected_client_request_id;
  if found then
    if selected.task_request_id<>selected_task_request_id
      or selected.canonical_payload_sha256<>selected_canonical_payload_sha256
      or selected.task_kind<>selected_task_kind
      or selected.expected_context_version_id
        is distinct from selected_expected_context_version_id
      or selected.material_attachment_ids<>selected_material_attachment_ids
      or selected.candidate_id is distinct from selected_candidate_id
      or selected.position_candidate_id is distinct from selected_position_candidate_id then
      raise unique_violation using message='position task request payload mismatch';
    end if;
    return selected;
  end if;
  select current_context_version_id into current_context_id
  from platform_hr.positions
  where owner_internal_user_id=selected_owner_internal_user_id
    and position_id=selected_position_id for update;
  if not found then raise no_data_found; end if;
  if current_context_id is distinct from selected_expected_context_version_id then
    raise serialization_failure using message='position task request context conflict';
  end if;
  if not platform_hr.validate_position_materials_v69(
    selected_owner_internal_user_id,selected_position_id,
    selected_material_attachment_ids
  ) then raise no_data_found; end if;
  insert into platform_hr.position_task_requests(
    task_request_id,owner_internal_user_id,position_id,client_request_id,
    canonical_payload_sha256,task_kind,expected_context_version_id,
    material_attachment_ids,candidate_id,position_candidate_id
  ) values (
    selected_task_request_id,selected_owner_internal_user_id,
    selected_position_id,selected_client_request_id,
    selected_canonical_payload_sha256,selected_task_kind,
    selected_expected_context_version_id,selected_material_attachment_ids,
    selected_candidate_id,selected_position_candidate_id
  ) returning * into selected;
  return selected;
end
$function$;

create function platform_hr.read_position_task_request_v69(
  selected_owner_internal_user_id uuid,
  selected_position_id uuid,
  selected_client_request_id uuid
) returns setof platform_hr.position_task_requests
language sql stable security definer
set search_path=pg_catalog,platform_hr
as $function$
  select request.* from platform_hr.position_task_requests request
  where request.owner_internal_user_id=selected_owner_internal_user_id
    and request.position_id=selected_position_id
    and request.client_request_id=selected_client_request_id
$function$;

-- Migration 070 replaces this hook with candidate/document/feedback ownership,
-- lifecycle, and exact-context validation. Until then candidate inputs fail closed.
create function platform_hr.validate_candidate_task_inputs_v69(
  selected_owner_internal_user_id uuid,
  selected_position_id uuid,
  selected_context_version_id uuid,
  selected_candidate_id uuid,
  selected_position_candidate_id uuid,
  selected_document_attachment_ids uuid[],
  selected_human_feedback_ids uuid[]
) returns boolean
language sql stable security definer
set search_path=pg_catalog,platform_hr
as $function$
  select selected_candidate_id is null
    and selected_position_candidate_id is null
    and cardinality(selected_document_attachment_ids)=0
    and cardinality(selected_human_feedback_ids)=0
$function$;

create function platform_hr.create_position_task_record_v69(
  selected_task_record_id uuid,
  selected_owner_internal_user_id uuid,
  selected_position_id uuid,
  selected_client_request_id uuid,
  selected_task_kind text,
  selected_official_position_version_id uuid,
  selected_context_version_id uuid,
  selected_material_attachment_ids uuid[],
  selected_candidate_id uuid,
  selected_position_candidate_id uuid,
  selected_document_attachment_ids uuid[],
  selected_human_feedback_ids uuid[],
  selected_conversation_id uuid,
  selected_turn_id uuid,
  selected_prompt_context text,
  selected_canonical_sha256 text
) returns platform_hr.position_task_records
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.position_task_records%rowtype;
declare request platform_hr.position_task_requests%rowtype;
declare current_official_id uuid;
declare current_context_id uuid;
declare turn_request_id uuid;
declare bound_material_ids uuid[];
declare request_is_explicit boolean := false;
begin
  if session_user not in (
    'platform_control_app','platform_control_app_preview',
    'platform_brain_worker','platform_brain_worker_preview'
  ) then raise insufficient_privilege; end if;
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':position-task:' ||
    selected_client_request_id::text,0
  ));
  select * into selected from platform_hr.position_task_records
  where owner_internal_user_id=selected_owner_internal_user_id
    and client_request_id=selected_client_request_id;
  if found then
    if selected.task_record_id<>selected_task_record_id
      or selected.position_id<>selected_position_id
      or selected.task_kind<>selected_task_kind
      or selected.official_position_version_id
        is distinct from selected_official_position_version_id
      or selected.context_version_id is distinct from selected_context_version_id
      or selected.material_attachment_ids<>selected_material_attachment_ids
      or selected.candidate_id is distinct from selected_candidate_id
      or selected.position_candidate_id is distinct from selected_position_candidate_id
      or selected.document_attachment_ids<>selected_document_attachment_ids
      or selected.human_feedback_ids<>selected_human_feedback_ids
      or selected.conversation_id<>selected_conversation_id
      or selected.turn_id<>selected_turn_id
      or selected.prompt_context<>selected_prompt_context
      or selected.canonical_sha256<>selected_canonical_sha256 then
      raise unique_violation using message='position task idempotency payload mismatch';
    end if;
    return selected;
  end if;
  select turn.client_request_id into turn_request_id
  from platform_hr.position_conversations binding
  join platform_control.conversation_turns turn
    on turn.conversation_id=binding.conversation_id
  where binding.owner_internal_user_id=selected_owner_internal_user_id
    and binding.position_id=selected_position_id
    and binding.conversation_id=selected_conversation_id
    and turn.turn_id=selected_turn_id;
  if not found then raise no_data_found; end if;
  if turn_request_id<>selected_client_request_id then
    raise unique_violation using message='position task turn request mismatch';
  end if;
  select * into request from platform_hr.position_task_requests
  where owner_internal_user_id=selected_owner_internal_user_id
    and position_id=selected_position_id
    and client_request_id=selected_client_request_id
    and status='active' for update;
  if found then
    request_is_explicit := true;
    if request.task_kind<>selected_task_kind
      or request.expected_context_version_id is distinct from selected_context_version_id
      or request.material_attachment_ids<>selected_material_attachment_ids
      or request.candidate_id is distinct from selected_candidate_id
      or request.position_candidate_id is distinct from selected_position_candidate_id then
      raise unique_violation using message='position task selection mismatch';
    end if;
  elsif selected_task_kind<>'freeform'
    or selected_candidate_id is not null
    or selected_position_candidate_id is not null
    or cardinality(selected_document_attachment_ids)<>0
    or cardinality(selected_human_feedback_ids)<>0 then
    raise no_data_found using message='explicit position task request required';
  end if;
  select current_official_version_id,current_context_version_id
    into current_official_id,current_context_id
  from platform_hr.positions
  where owner_internal_user_id=selected_owner_internal_user_id
    and position_id=selected_position_id for update;
  if current_official_id is distinct from selected_official_position_version_id then
    raise serialization_failure using message='official position task baseline conflict';
  end if;
  if not request_is_explicit
    and current_context_id is distinct from selected_context_version_id then
    raise serialization_failure using message='implicit position task context conflict';
  end if;
  if selected_context_version_id is not null then
    perform 1 from platform_hr.position_context_versions
    where context_version_id=selected_context_version_id
      and owner_internal_user_id=selected_owner_internal_user_id
      and position_id=selected_position_id and state in ('confirmed','superseded');
    if not found then raise no_data_found; end if;
  end if;
  select coalesce(array_agg(binding.attachment_id order by binding.attachment_id),'{}'::uuid[])
    into bound_material_ids
  from platform_attachments.bindings binding
  where binding.owner_internal_user_id=selected_owner_internal_user_id
    and binding.conversation_id=selected_conversation_id
    and binding.turn_id=selected_turn_id and binding.kind='turn_input';
  if request_is_explicit then
    if request.material_attachment_ids<>selected_material_attachment_ids
      or not bound_material_ids<@selected_material_attachment_ids
      or not platform_hr.validate_position_materials_v69(
        selected_owner_internal_user_id,selected_position_id,
        selected_material_attachment_ids
      ) then raise no_data_found; end if;
  elsif bound_material_ids<>selected_material_attachment_ids
    or not platform_hr.validate_position_materials_v69(
      selected_owner_internal_user_id,selected_position_id,bound_material_ids
    ) then raise no_data_found; end if;
  if not platform_hr.validate_candidate_task_inputs_v69(
    selected_owner_internal_user_id,selected_position_id,
    selected_context_version_id,selected_candidate_id,
    selected_position_candidate_id,selected_document_attachment_ids,
    selected_human_feedback_ids
  ) then raise no_data_found; end if;
  if not request_is_explicit then
    insert into platform_hr.position_task_requests(
      task_request_id,owner_internal_user_id,position_id,client_request_id,
      canonical_payload_sha256,task_kind,expected_context_version_id,
      material_attachment_ids,candidate_id,position_candidate_id,status
    ) values (
      md5(selected_owner_internal_user_id::text || ':' ||
        selected_client_request_id::text || ':implicit-freeform')::uuid,
      selected_owner_internal_user_id,selected_position_id,
      selected_client_request_id,selected_canonical_sha256,'freeform',
      selected_context_version_id,selected_material_attachment_ids,
      null,null,'consumed'
    ) returning * into request;
  end if;
  insert into platform_hr.position_task_records(
    task_record_id,owner_internal_user_id,position_id,client_request_id,
    task_kind,official_position_version_id,context_version_id,
    material_attachment_ids,candidate_id,position_candidate_id,
    document_attachment_ids,human_feedback_ids,conversation_id,turn_id,
    prompt_context,canonical_sha256
  ) values (
    selected_task_record_id,selected_owner_internal_user_id,
    selected_position_id,selected_client_request_id,selected_task_kind,
    selected_official_position_version_id,selected_context_version_id,
    selected_material_attachment_ids,selected_candidate_id,
    selected_position_candidate_id,selected_document_attachment_ids,
    selected_human_feedback_ids,selected_conversation_id,selected_turn_id,
    selected_prompt_context,selected_canonical_sha256
  ) returning * into selected;
  if request_is_explicit then
    update platform_hr.position_task_requests set status='consumed'
    where task_request_id=request.task_request_id;
  end if;
  return selected;
end
$function$;

create function platform_hr.read_position_context_versions_v69(
  selected_owner_internal_user_id uuid,
  selected_position_id uuid
) returns setof platform_hr.position_context_versions
language sql stable security definer
set search_path=pg_catalog,platform_hr
as $function$
  select version.* from platform_hr.position_context_versions version
  where version.owner_internal_user_id=selected_owner_internal_user_id
    and version.position_id=selected_position_id
  order by version.version_number desc,version.created_at desc
$function$;

revoke all on all tables in schema platform_hr from public;
revoke all on all functions in schema platform_hr from public;
revoke all on function platform_hr.project_official_version_v69(
  uuid,uuid,uuid,uuid,text,text,text,jsonb,text,text,integer,text,text,text,
  text,text,text,timestamptz,text,timestamptz,timestamptz,text,text,jsonb,integer,integer,timestamptz
) from public;
revoke all on function platform_hr.create_context_draft_v69(
  uuid,uuid,uuid,uuid,uuid,uuid,jsonb,text,uuid,uuid,uuid,uuid[],text,text,uuid
) from public;
revoke all on function platform_hr.confirm_context_modules_v69(
  uuid,uuid,uuid,uuid,uuid,bigint,text[],uuid
) from public;
revoke all on function platform_hr.create_position_task_record_v69(
  uuid,uuid,uuid,uuid,text,uuid,uuid,uuid[],uuid,uuid,uuid[],uuid[],uuid,uuid,text,text
) from public;
revoke all on function platform_hr.create_position_task_request_v69(
  uuid,uuid,uuid,uuid,text,text,uuid,uuid[],uuid,uuid
) from public;
revoke all on function platform_hr.read_position_task_request_v69(
  uuid,uuid,uuid
) from public;
revoke all on function platform_hr.read_position_context_versions_v69(
  uuid,uuid
) from public;

do $migration$
declare selected_app name;
declare selected_brain name;
begin
  if current_database()='agent_platform_control'
     and current_user='platform_control_owner' then
    selected_app := 'platform_control_app';
    selected_brain := 'platform_brain_worker';
  elsif current_database()='agent_platform_control_preview'
        and current_user='platform_control_owner_preview' then
    selected_app := 'platform_control_app_preview';
    selected_brain := 'platform_brain_worker_preview';
  else
    raise insufficient_privilege using
      message='HR position intelligence migration owner/environment mismatch';
  end if;
  execute format('grant usage on schema platform_hr to %I,%I',selected_app,selected_brain);
  execute format('grant select on all tables in schema platform_hr to %I',selected_app);
  execute format(
    'grant execute on function platform_hr.project_official_version_v69('
    'uuid,uuid,uuid,uuid,text,text,text,jsonb,text,text,integer,text,text,text,'
    'text,text,text,timestamptz,text,timestamptz,timestamptz,text,text,jsonb,integer,integer,timestamptz) to %I',
    selected_app
  );
  execute format(
    'grant execute on function platform_hr.create_context_draft_v69('
    'uuid,uuid,uuid,uuid,uuid,uuid,jsonb,text,uuid,uuid,uuid,uuid[],text,text,uuid) to %I',
    selected_app
  );
  execute format(
    'grant execute on function platform_hr.confirm_context_modules_v69('
    'uuid,uuid,uuid,uuid,uuid,bigint,text[],uuid) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.create_position_task_request_v69('
    'uuid,uuid,uuid,uuid,text,text,uuid,uuid[],uuid,uuid) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.read_position_task_request_v69('
    'uuid,uuid,uuid) to %I,%I',selected_app,selected_brain
  );
  execute format(
    'grant execute on function platform_hr.create_position_task_record_v69('
    'uuid,uuid,uuid,uuid,text,uuid,uuid,uuid[],uuid,uuid,uuid[],uuid[],uuid,uuid,text,text) to %I,%I',
    selected_app,selected_brain
  );
  execute format(
    'grant execute on function platform_hr.read_position_context_versions_v69('
    'uuid,uuid) to %I,%I',selected_app,selected_brain
  );
  execute format('grant select on platform_hr.position_task_records to %I',selected_brain);
end
$migration$;
