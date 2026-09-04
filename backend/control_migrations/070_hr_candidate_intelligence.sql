create table platform_hr.candidate_draft_batches (
  batch_request_id uuid not null,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  position_id uuid not null,
  attachment_ids uuid[] not null check (
    cardinality(attachment_ids) between 1 and 100
  ),
  canonical_payload jsonb not null default '{}'::jsonb
    check (jsonb_typeof(canonical_payload)='object'),
  payload_sha256 bytea not null default sha256(convert_to('{}','UTF8'))
    check (octet_length(payload_sha256)=32),
  created_at timestamptz not null default now(),
  foreign key (position_id,owner_internal_user_id)
    references platform_hr.positions(position_id,owner_internal_user_id),
  unique (batch_request_id,owner_internal_user_id),
  unique (owner_internal_user_id,batch_request_id)
);

create table platform_hr.candidate_drafts (
  draft_id uuid primary key,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  position_id uuid not null,
  attachment_id uuid not null,
  batch_request_id uuid not null,
  client_request_id uuid not null,
  creation_payload jsonb not null default '{}'::jsonb
    check (jsonb_typeof(creation_payload)='object'),
  creation_payload_sha256 bytea not null default sha256(convert_to('{}','UTF8'))
    check (octet_length(creation_payload_sha256)=32),
  last_mutation_request_id uuid,
  state text not null default 'pending'
    check (state in ('pending','processing','ready','failed','confirmed','dismissed')),
  extracted_facts jsonb not null default '{}'::jsonb check (
    jsonb_typeof(extracted_facts)='object'
    and octet_length(extracted_facts::text)<=262144
  ),
  identity_candidates uuid[] not null default '{}'::uuid[] check (
    cardinality(identity_candidates)<=100
  ),
  error_code text check (
    error_code is null or char_length(btrim(error_code)) between 1 and 128
  ),
  row_version bigint not null default 1 check (row_version>0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (position_id,owner_internal_user_id)
    references platform_hr.positions(position_id,owner_internal_user_id),
  foreign key (attachment_id,owner_internal_user_id)
    references platform_attachments.attachments(attachment_id,owner_internal_user_id),
  foreign key (batch_request_id,owner_internal_user_id)
    references platform_hr.candidate_draft_batches(
      batch_request_id,owner_internal_user_id
    ),
  check ((state='failed')=(error_code is not null)),
  unique (draft_id,owner_internal_user_id),
  unique (owner_internal_user_id,client_request_id),
  unique (owner_internal_user_id,batch_request_id,attachment_id)
);

create table platform_hr.candidate_draft_mutation_events (
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  client_request_id uuid not null,
  draft_id uuid not null,
  mutation_kind text not null
    check (mutation_kind in ('start','complete','fail','retry','dismiss')),
  canonical_payload jsonb not null default '{}'::jsonb
    check (jsonb_typeof(canonical_payload)='object'),
  payload_sha256 bytea not null default sha256(convert_to('{}','UTF8'))
    check (octet_length(payload_sha256)=32),
  result_id uuid not null,
  result_snapshot jsonb not null check (jsonb_typeof(result_snapshot)='object'),
  created_at timestamptz not null default now(),
  foreign key (draft_id,owner_internal_user_id)
    references platform_hr.candidate_drafts(draft_id,owner_internal_user_id),
  unique (owner_internal_user_id,client_request_id)
);

create table platform_hr.candidate_draft_processing_attempts (
  attempt_id uuid primary key,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  draft_id uuid not null,
  worker_id text not null
    check (worker_id ~ '^[a-z0-9][a-z0-9._-]{0,63}$'),
  execution_job_id uuid not null
    references platform_control.execution_jobs(job_id),
  conversation_id uuid,
  turn_id uuid,
  state text not null check (state in ('processing','completed','failed','expired')),
  starting_row_version bigint not null check (starting_row_version>0),
  claimed_row_version bigint not null check (claimed_row_version>0),
  claimed_at timestamptz not null default now(),
  lease_expires_at timestamptz not null,
  finished_at timestamptz,
  terminal_request_id uuid,
  canonical_payload jsonb not null default '{}'::jsonb
    check (jsonb_typeof(canonical_payload)='object'),
  payload_sha256 bytea not null default sha256(convert_to('{}','UTF8'))
    check (octet_length(payload_sha256)=32),
  foreign key (draft_id,owner_internal_user_id)
    references platform_hr.candidate_drafts(draft_id,owner_internal_user_id),
  foreign key (conversation_id,owner_internal_user_id)
    references platform_control.conversations(
      conversation_id,owner_internal_user_id
    ),
  foreign key (conversation_id,turn_id)
    references platform_control.conversation_turns(conversation_id,turn_id),
  check ((conversation_id is null)=(turn_id is null)),
  check (
    (state='processing' and finished_at is null and terminal_request_id is null)
    or (state in ('completed','failed') and finished_at is not null
      and terminal_request_id is not null)
    or (state='expired' and finished_at is not null and terminal_request_id is null)
  )
);

create unique index one_processing_candidate_draft_v70
  on platform_hr.candidate_draft_processing_attempts(draft_id)
  where state='processing';

create table platform_hr.candidates (
  candidate_id uuid primary key,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  confirmation_request_id uuid not null,
  stable_name text not null
    check (char_length(btrim(stable_name)) between 1 and 500),
  facts jsonb not null default '{}'::jsonb check (
    jsonb_typeof(facts)='object' and octet_length(facts::text)<=262144
  ),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (candidate_id,owner_internal_user_id),
  unique (owner_internal_user_id,confirmation_request_id)
);

create table platform_hr.candidate_documents (
  document_id uuid primary key,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  candidate_id uuid not null,
  attachment_id uuid not null,
  source_draft_id uuid not null,
  document_kind text not null check (document_kind='resume'),
  version_number bigint not null check (version_number>0),
  content_sha256 text not null check (content_sha256 ~ '^[a-f0-9]{64}$'),
  status text not null default 'active' check (status in ('active','erased')),
  created_at timestamptz not null default now(),
  foreign key (candidate_id,owner_internal_user_id)
    references platform_hr.candidates(candidate_id,owner_internal_user_id),
  foreign key (attachment_id,owner_internal_user_id)
    references platform_attachments.attachments(attachment_id,owner_internal_user_id),
  foreign key (source_draft_id,owner_internal_user_id)
    references platform_hr.candidate_drafts(draft_id,owner_internal_user_id),
  unique (document_id,owner_internal_user_id),
  unique (candidate_id,version_number),
  unique (owner_internal_user_id,candidate_id,attachment_id)
);

create table platform_hr.position_candidates (
  position_candidate_id uuid primary key,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  position_id uuid not null,
  candidate_id uuid not null,
  context_version_id uuid not null,
  source_draft_id uuid not null,
  client_request_id uuid not null,
  status text not null default 'active' check (status in ('active','archived')),
  row_version bigint not null default 1 check (row_version>0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (position_id,owner_internal_user_id)
    references platform_hr.positions(position_id,owner_internal_user_id),
  foreign key (candidate_id,owner_internal_user_id)
    references platform_hr.candidates(candidate_id,owner_internal_user_id),
  foreign key (source_draft_id,owner_internal_user_id)
    references platform_hr.candidate_drafts(draft_id,owner_internal_user_id),
  unique (position_candidate_id,owner_internal_user_id),
  unique (position_candidate_id,owner_internal_user_id,position_id,candidate_id),
  unique (owner_internal_user_id,client_request_id),
  unique (owner_internal_user_id,position_id,candidate_id)
);

create table platform_hr.candidate_analysis_versions (
  analysis_version_id uuid primary key,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  position_candidate_id uuid not null,
  position_id uuid not null,
  candidate_id uuid not null,
  context_version_id uuid not null,
  client_request_id uuid not null,
  version_number bigint not null check (version_number>0),
  analysis_kind text not null check (
    analysis_kind in ('resume_extract','match','candidate_interview_plan','comparison')
  ),
  result jsonb not null check (
    jsonb_typeof(result)='object' and octet_length(result::text)<=524288
  ),
  evidence jsonb not null default '[]'::jsonb check (
    jsonb_typeof(evidence)='array' and octet_length(evidence::text)<=524288
  ),
  unknowns jsonb not null default '[]'::jsonb check (
    jsonb_typeof(unknowns)='array' and octet_length(unknowns::text)<=262144
  ),
  conflicts jsonb not null default '[]'::jsonb check (
    jsonb_typeof(conflicts)='array' and octet_length(conflicts::text)<=262144
  ),
  verification_questions jsonb not null default '[]'::jsonb check (
    jsonb_typeof(verification_questions)='array'
    and octet_length(verification_questions::text)<=262144
  ),
  agent_version text not null
    check (char_length(btrim(agent_version)) between 1 and 128),
  model_version text not null
    check (char_length(btrim(model_version)) between 1 and 128),
  canonical_payload jsonb not null default '{}'::jsonb
    check (jsonb_typeof(canonical_payload)='object'),
  payload_sha256 bytea not null default sha256(convert_to('{}','UTF8'))
    check (octet_length(payload_sha256)=32),
  created_at timestamptz not null default now(),
  foreign key (
    position_candidate_id,owner_internal_user_id,position_id,candidate_id
  ) references platform_hr.position_candidates(
    position_candidate_id,owner_internal_user_id,position_id,candidate_id
  ),
  unique (analysis_version_id,owner_internal_user_id),
  unique (owner_internal_user_id,client_request_id),
  unique (position_candidate_id,version_number)
);

do $context_foreign_keys$
begin
  if to_regclass('platform_hr.position_context_versions') is not null then
    alter table platform_hr.position_candidates
      add constraint position_candidates_context_owner_fk_v70
      foreign key (context_version_id,owner_internal_user_id)
      references platform_hr.position_context_versions(context_version_id,owner_internal_user_id);
    alter table platform_hr.candidate_analysis_versions
      add constraint candidate_analysis_context_owner_fk_v70
      foreign key (context_version_id,owner_internal_user_id)
      references platform_hr.position_context_versions(context_version_id,owner_internal_user_id);
  end if;
end
$context_foreign_keys$;

create table platform_hr.candidate_analysis_documents (
  analysis_version_id uuid not null,
  document_id uuid not null,
  owner_internal_user_id uuid not null,
  created_at timestamptz not null default now(),
  foreign key (analysis_version_id,owner_internal_user_id)
    references platform_hr.candidate_analysis_versions(
      analysis_version_id,owner_internal_user_id
    ),
  foreign key (document_id,owner_internal_user_id)
    references platform_hr.candidate_documents(document_id,owner_internal_user_id),
  unique (analysis_version_id,document_id)
);

create table platform_hr.candidate_confirmation_events (
  client_request_id uuid not null,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  draft_id uuid not null,
  expected_row_version bigint not null check (expected_row_version>0),
  requested_candidate_id uuid not null,
  merge_candidate_id uuid,
  actual_candidate_id uuid not null,
  document_id uuid not null,
  position_candidate_id uuid not null,
  context_version_id uuid not null,
  stable_name text not null
    check (char_length(btrim(stable_name)) between 1 and 500),
  confirmed_facts jsonb not null check (
    jsonb_typeof(confirmed_facts)='object'
    and octet_length(confirmed_facts::text)<=262144
  ),
  canonical_payload jsonb not null check (jsonb_typeof(canonical_payload)='object'),
  payload_sha256 bytea not null check (octet_length(payload_sha256)=32),
  result_snapshot jsonb not null check (jsonb_typeof(result_snapshot)='object'),
  created_at timestamptz not null default now(),
  foreign key (draft_id,owner_internal_user_id)
    references platform_hr.candidate_drafts(draft_id,owner_internal_user_id),
  foreign key (actual_candidate_id,owner_internal_user_id)
    references platform_hr.candidates(candidate_id,owner_internal_user_id),
  foreign key (document_id,owner_internal_user_id)
    references platform_hr.candidate_documents(document_id,owner_internal_user_id),
  foreign key (position_candidate_id,owner_internal_user_id)
    references platform_hr.position_candidates(
      position_candidate_id,owner_internal_user_id
    ),
  unique (owner_internal_user_id,client_request_id)
);

create table platform_hr.human_feedback (
  feedback_id uuid primary key,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  position_candidate_id uuid not null,
  analysis_version_id uuid not null,
  client_request_id uuid not null,
  feedback_kind text not null
    check (feedback_kind in ('accepted','rejected','correction')),
  conclusion_key text not null
    check (char_length(btrim(conclusion_key)) between 1 and 256),
  correction text check (
    correction is null or char_length(btrim(correction)) between 1 and 8000
  ),
  reason text not null check (char_length(btrim(reason)) between 1 and 4000),
  canonical_payload jsonb not null check (jsonb_typeof(canonical_payload)='object'),
  payload_sha256 bytea not null check (octet_length(payload_sha256)=32),
  created_at timestamptz not null default now(),
  foreign key (position_candidate_id,owner_internal_user_id)
    references platform_hr.position_candidates(
      position_candidate_id,owner_internal_user_id
    ),
  foreign key (analysis_version_id,owner_internal_user_id)
    references platform_hr.candidate_analysis_versions(
      analysis_version_id,owner_internal_user_id
    ),
  check ((feedback_kind='correction')=(correction is not null)),
  unique (feedback_id,owner_internal_user_id),
  unique (owner_internal_user_id,client_request_id)
);

create table platform_hr.candidate_analysis_feedback (
  analysis_version_id uuid not null,
  feedback_id uuid not null,
  owner_internal_user_id uuid not null,
  created_at timestamptz not null default now(),
  foreign key (analysis_version_id,owner_internal_user_id)
    references platform_hr.candidate_analysis_versions(
      analysis_version_id,owner_internal_user_id
    ),
  foreign key (feedback_id,owner_internal_user_id)
    references platform_hr.human_feedback(feedback_id,owner_internal_user_id),
  unique (analysis_version_id,feedback_id)
);

create or replace function platform_hr.validate_candidate_task_inputs_v69(
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
  select case
    when selected_candidate_id is null
      and selected_position_candidate_id is null
      and cardinality(selected_document_attachment_ids)=0
      and cardinality(selected_human_feedback_ids)=0
    then true
    when selected_candidate_id is null
      or selected_position_candidate_id is null
      or selected_context_version_id is null
      or cardinality(selected_document_attachment_ids)=0
    then false
    else exists (
      select 1 from platform_hr.position_candidates relation
      where relation.position_candidate_id=selected_position_candidate_id
        and relation.owner_internal_user_id=selected_owner_internal_user_id
        and relation.position_id=selected_position_id
        and relation.candidate_id=selected_candidate_id
        and relation.context_version_id=selected_context_version_id
        and relation.status='active'
    ) and not exists (
      select 1 from unnest(selected_document_attachment_ids) requested(attachment_id)
      where not exists (
        select 1 from platform_hr.candidate_documents document
        join platform_attachments.attachments attachment
          on attachment.attachment_id=document.attachment_id
          and attachment.owner_internal_user_id=document.owner_internal_user_id
        where document.owner_internal_user_id=selected_owner_internal_user_id
          and document.candidate_id=selected_candidate_id
          and document.attachment_id=requested.attachment_id
          and document.status='active'
          and attachment.state='ready' and attachment.deleted_at is null
          and attachment.retained_until>now()
          and not exists (
            select 1 from platform_attachments.erasure_jobs erasure
            where erasure.attachment_id=attachment.attachment_id
          )
      )
    ) and not exists (
      select 1 from unnest(selected_human_feedback_ids) requested(feedback_id)
      where not exists (
        select 1 from platform_hr.human_feedback feedback
        join platform_hr.candidate_analysis_versions analysis
          on analysis.analysis_version_id=feedback.analysis_version_id
          and analysis.owner_internal_user_id=feedback.owner_internal_user_id
        where feedback.feedback_id=requested.feedback_id
          and feedback.owner_internal_user_id=selected_owner_internal_user_id
          and feedback.position_candidate_id=selected_position_candidate_id
          and analysis.position_candidate_id=selected_position_candidate_id
          and analysis.context_version_id=selected_context_version_id
      )
    )
  end
$function$;

create function platform_hr.reject_candidate_history_mutation_v70()
returns trigger language plpgsql
set search_path=pg_catalog,platform_hr
as $function$
begin
  raise check_violation using message='candidate history is immutable';
end
$function$;

create trigger candidate_analysis_versions_immutable_v70
before update or delete on platform_hr.candidate_analysis_versions
for each row execute function platform_hr.reject_candidate_history_mutation_v70();

create trigger human_feedback_immutable_v70
before update or delete on platform_hr.human_feedback
for each row execute function platform_hr.reject_candidate_history_mutation_v70();

create trigger candidate_analysis_documents_immutable_v70
before update or delete on platform_hr.candidate_analysis_documents
for each row execute function platform_hr.reject_candidate_history_mutation_v70();

create trigger candidate_analysis_feedback_immutable_v70
before update or delete on platform_hr.candidate_analysis_feedback
for each row execute function platform_hr.reject_candidate_history_mutation_v70();

create function platform_hr.candidate_json_safe_v70(
  selected_payload jsonb,
  selected_candidate_facts boolean default false
) returns boolean
language plpgsql immutable
set search_path=pg_catalog,platform_hr
as $function$
declare item record;
declare allowed_keys constant text[] := array[
  'stable_name','summary','contact','education','experiences','projects','skills',
  'certifications','languages','awards','publications','unknowns','sources'
];
declare forbidden_keys constant text[] := array[
  'age','birth_date','date_of_birth','disability','ethnicity','gender','health',
  'marital_status','nationality','onboarding','offer_status','pipeline_stage',
  'political_affiliation','pregnancy','race','religion','sexual_orientation',
  'storage_key','storage_path','object_key','object_ref','object_ref_ciphertext',
  'immutable_locator','ats','ats_id','interview_schedule','automatic_rejection',
  'beisen','boss_zhipin','liepin'
];
begin
  if selected_payload is null then return false; end if;
  if selected_candidate_facts and jsonb_typeof(selected_payload)<>'object' then
    return false;
  end if;
  if jsonb_typeof(selected_payload)='object' then
    for item in select key,value from jsonb_each(selected_payload) loop
      if lower(btrim(item.key))=any(forbidden_keys) then return false; end if;
      if selected_candidate_facts and not lower(btrim(item.key))=any(allowed_keys) then
        return false;
      end if;
      if not platform_hr.candidate_json_safe_v70(item.value,false) then return false; end if;
    end loop;
  elsif jsonb_typeof(selected_payload)='array' then
    for item in select value from jsonb_array_elements(selected_payload) loop
      if not platform_hr.candidate_json_safe_v70(item.value,false) then return false; end if;
    end loop;
  end if;
  return true;
end
$function$;

create function platform_hr.candidate_attachment_usable_v70(
  selected_owner_internal_user_id uuid,
  selected_attachment_id uuid
) returns boolean
language sql stable
set search_path=pg_catalog,platform_hr
as $function$
  select exists (
    select 1 from platform_attachments.attachments attachment
    where attachment.attachment_id=selected_attachment_id
      and attachment.owner_internal_user_id=selected_owner_internal_user_id
      and attachment.source_kind='user_input' and attachment.state='ready'
      and attachment.deleted_at is null and attachment.retained_until>now()
      and not exists (
        select 1 from platform_attachments.erasure_jobs erasure
        where erasure.attachment_id=attachment.attachment_id
      )
  )
$function$;

alter table platform_hr.candidate_drafts add check (
  platform_hr.candidate_json_safe_v70(extracted_facts,true)
);
alter table platform_hr.candidates add check (
  platform_hr.candidate_json_safe_v70(facts,true)
);
alter table platform_hr.candidate_analysis_versions add check (
  platform_hr.candidate_json_safe_v70(result,false)
  and platform_hr.candidate_json_safe_v70(evidence,false)
);

create function platform_hr.claim_candidate_draft_v70(
  selected_attempt_id uuid,
  selected_owner_internal_user_id uuid,
  selected_draft_id uuid,
  selected_worker_id text,
  selected_execution_job_id uuid,
  selected_conversation_id uuid,
  selected_turn_id uuid,
  selected_lease_seconds integer
) returns platform_hr.candidate_draft_processing_attempts
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected_attempt platform_hr.candidate_draft_processing_attempts%rowtype;
declare selected_draft platform_hr.candidate_drafts%rowtype;
declare payload jsonb;
begin
  if session_user not in ('platform_brain_worker','platform_brain_worker_preview') then
    raise insufficient_privilege;
  end if;
  if selected_lease_seconds not between 30 and 900 then raise check_violation; end if;
  payload := jsonb_build_object(
    'owner_internal_user_id',selected_owner_internal_user_id,
    'draft_id',selected_draft_id,
    'worker_id',btrim(selected_worker_id),
    'execution_job_id',selected_execution_job_id,
    'conversation_id',selected_conversation_id,'turn_id',selected_turn_id,
    'lease_seconds',selected_lease_seconds
  );
  perform pg_advisory_xact_lock(hashtextextended(
    'candidate-claim:' || selected_attempt_id::text,0
  ));
  select * into selected_attempt
  from platform_hr.candidate_draft_processing_attempts
  where attempt_id=selected_attempt_id;
  if found then
    if selected_attempt.canonical_payload<>payload then
      raise unique_violation using message='candidate claim idempotency mismatch';
    end if;
    return selected_attempt;
  end if;
  with expired as (
    update platform_hr.candidate_draft_processing_attempts set
      state='expired',finished_at=now()
    where state='processing' and lease_expires_at<=now()
    returning draft_id
  )
  update platform_hr.candidate_drafts draft set
    state='pending',row_version=draft.row_version+1,updated_at=now()
  where draft.state='processing'
    and exists (select 1 from expired where expired.draft_id=draft.draft_id);
  select draft.* into selected_draft
  from platform_hr.candidate_drafts draft
  join platform_attachments.attachments attachment
    on attachment.attachment_id=draft.attachment_id
    and attachment.owner_internal_user_id=draft.owner_internal_user_id
  where draft.draft_id=selected_draft_id
    and draft.owner_internal_user_id=selected_owner_internal_user_id
    and draft.state='pending'
    and platform_hr.candidate_attachment_usable_v70(
      draft.owner_internal_user_id,draft.attachment_id
    )
    and not exists (
      select 1 from platform_hr.candidate_draft_processing_attempts active_attempt
      where active_attempt.draft_id=draft.draft_id
        and active_attempt.state='processing'
    )
  for update of draft skip locked limit 1;
  if not found then raise no_data_found; end if;
  perform 1 from platform_control.execution_jobs execution
  join platform_control.mission_runs run on run.run_id=execution.run_id
  join platform_control.missions mission on mission.mission_id=run.mission_id
  join platform_control.conversation_turns turn
    on turn.mission_id=mission.mission_id
    and turn.turn_id=selected_turn_id
    and turn.conversation_id=selected_conversation_id
  join platform_control.conversations conversation
    on conversation.conversation_id=turn.conversation_id
    and conversation.owner_internal_user_id=mission.owner_internal_user_id
  where execution.job_id=selected_execution_job_id
    and execution.agent_id='hr-candidate-bot'
    and execution.status not in ('completed','failed','cancelled','interrupted')
    and run.agent_id='hr-candidate-bot'
    and mission.owner_internal_user_id=selected_owner_internal_user_id
    and turn.client_request_id=selected_draft.client_request_id;
  if not found then raise no_data_found; end if;
  perform 1 from platform_attachments.attachments attachment
  where attachment.attachment_id=selected_draft.attachment_id
    and platform_hr.candidate_attachment_usable_v70(
      selected_draft.owner_internal_user_id,selected_draft.attachment_id
    ) for update;
  if not found then raise no_data_found; end if;
  update platform_hr.candidate_drafts set state='processing',error_code=null,
    row_version=row_version+1,updated_at=now()
  where draft_id=selected_draft.draft_id returning * into selected_draft;
  insert into platform_hr.candidate_draft_processing_attempts(
    attempt_id,owner_internal_user_id,draft_id,worker_id,execution_job_id,
    conversation_id,turn_id,state,starting_row_version,claimed_row_version,
    lease_expires_at,canonical_payload,payload_sha256
  ) values (
    selected_attempt_id,selected_draft.owner_internal_user_id,
    selected_draft.draft_id,selected_worker_id,selected_execution_job_id,
    selected_conversation_id,selected_turn_id,'processing',
    selected_draft.row_version-1,selected_draft.row_version,
    now()+make_interval(secs=>selected_lease_seconds),payload,
    sha256(convert_to(payload::text,'UTF8'))
  ) returning * into selected_attempt;
  return selected_attempt;
end
$function$;

create function platform_hr.read_candidate_draft_attempt_v70(
  selected_owner_internal_user_id uuid,
  selected_attempt_id uuid
) returns platform_hr.candidate_draft_processing_attempts
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.candidate_draft_processing_attempts%rowtype;
begin
  if session_user not in (
    'platform_control_app','platform_control_app_preview',
    'platform_brain_worker','platform_brain_worker_preview'
  ) then raise insufficient_privilege; end if;
  select * into selected from platform_hr.candidate_draft_processing_attempts
  where attempt_id=selected_attempt_id
    and owner_internal_user_id=selected_owner_internal_user_id;
  if not found then raise no_data_found; end if;
  return selected;
end
$function$;

create function platform_hr.register_candidate_draft_batch_v70(
  selected_owner_internal_user_id uuid,
  selected_position_id uuid,
  selected_batch_request_id uuid,
  selected_attachment_ids uuid[]
) returns platform_hr.candidate_draft_batches
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.candidate_draft_batches%rowtype;
declare payload jsonb;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':candidate-batch:' ||
    selected_batch_request_id::text,0
  ));
  payload := jsonb_build_object(
    'position_id',selected_position_id,'attachment_ids',to_jsonb(selected_attachment_ids)
  );
  select * into selected from platform_hr.candidate_draft_batches
  where owner_internal_user_id=selected_owner_internal_user_id
    and batch_request_id=selected_batch_request_id;
  if found then
    if selected.canonical_payload<>payload then
      raise unique_violation using message='candidate batch idempotency mismatch';
    end if;
    return selected;
  end if;
  if cardinality(selected_attachment_ids)<>cardinality(
    array(select distinct value from unnest(selected_attachment_ids) value)
  ) then raise check_violation; end if;
  perform 1 from platform_attachments.attachments attachment
  where attachment.attachment_id=any(selected_attachment_ids)
    and attachment.owner_internal_user_id=selected_owner_internal_user_id
    and platform_hr.candidate_attachment_usable_v70(
      selected_owner_internal_user_id,attachment.attachment_id
    ) for update;
  if not found or (
    select count(*) from platform_attachments.attachments attachment
    where attachment.attachment_id=any(selected_attachment_ids)
      and attachment.owner_internal_user_id=selected_owner_internal_user_id
      and platform_hr.candidate_attachment_usable_v70(
        selected_owner_internal_user_id,attachment.attachment_id
      )
  )<>cardinality(selected_attachment_ids) then raise no_data_found; end if;
  insert into platform_hr.candidate_draft_batches(
    batch_request_id,owner_internal_user_id,position_id,attachment_ids,
    canonical_payload,payload_sha256
  ) values (
    selected_batch_request_id,selected_owner_internal_user_id,
    selected_position_id,selected_attachment_ids,payload,
    sha256(convert_to(payload::text,'UTF8'))
  ) returning * into selected;
  return selected;
end
$function$;

create function platform_hr.create_candidate_draft_v70(
  selected_draft_id uuid,
  selected_owner_internal_user_id uuid,
  selected_position_id uuid,
  selected_attachment_id uuid,
  selected_batch_request_id uuid,
  selected_client_request_id uuid
) returns platform_hr.candidate_drafts
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.candidate_drafts%rowtype;
declare payload jsonb;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':candidate-draft-create:' ||
    selected_client_request_id::text,0
  ));
  payload := jsonb_build_object(
    'draft_id',selected_draft_id,'position_id',selected_position_id,
    'attachment_id',selected_attachment_id,'batch_request_id',selected_batch_request_id
  );
  select * into selected from platform_hr.candidate_drafts
  where owner_internal_user_id=selected_owner_internal_user_id
    and client_request_id=selected_client_request_id;
  if found then
    if selected.creation_payload<>payload then
      raise unique_violation using message='candidate draft idempotency mismatch';
    end if;
    return selected;
  end if;
  perform 1 from platform_hr.positions
  where position_id=selected_position_id
    and owner_internal_user_id=selected_owner_internal_user_id;
  if not found then raise no_data_found; end if;
  perform 1 from platform_attachments.attachments attachment
  where attachment.attachment_id=selected_attachment_id
    and attachment.owner_internal_user_id=selected_owner_internal_user_id
    and platform_hr.candidate_attachment_usable_v70(
      selected_owner_internal_user_id,selected_attachment_id
    ) for update;
  if not found then raise no_data_found; end if;
  insert into platform_hr.candidate_drafts(
    draft_id,owner_internal_user_id,position_id,attachment_id,
    batch_request_id,client_request_id,creation_payload,
    creation_payload_sha256,state
  ) values (
    selected_draft_id,selected_owner_internal_user_id,selected_position_id,
    selected_attachment_id,selected_batch_request_id,
    selected_client_request_id,payload,sha256(convert_to(payload::text,'UTF8')),
    'pending'
  ) on conflict (owner_internal_user_id,batch_request_id,attachment_id)
  do update set batch_request_id=candidate_drafts.batch_request_id
  returning * into selected;
  return selected;
end
$function$;

create function platform_hr.start_candidate_draft_v70(
  selected_owner_internal_user_id uuid,
  selected_draft_id uuid,
  selected_client_request_id uuid,
  selected_expected_row_version bigint
) returns platform_hr.candidate_drafts
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.candidate_drafts%rowtype;
declare prior_event platform_hr.candidate_draft_mutation_events%rowtype;
declare payload jsonb;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  payload := jsonb_build_object(
    'draft_id',selected_draft_id,'expected_row_version',selected_expected_row_version
  );
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':candidate-draft-mutation:' ||
    selected_client_request_id::text,0
  ));
  select * into prior_event from platform_hr.candidate_draft_mutation_events
  where owner_internal_user_id=selected_owner_internal_user_id
    and client_request_id=selected_client_request_id;
  if found then
    if prior_event.draft_id<>selected_draft_id
       or prior_event.mutation_kind<>'start'
       or prior_event.canonical_payload<>payload then
      raise unique_violation using message='candidate draft idempotency mismatch';
    end if;
    select * into selected from jsonb_populate_record(
      null::platform_hr.candidate_drafts,prior_event.result_snapshot
    );
    return selected;
  end if;
  select * into selected from platform_hr.candidate_drafts
  where draft_id=selected_draft_id
    and owner_internal_user_id=selected_owner_internal_user_id for update;
  if not found then raise no_data_found; end if;
  perform 1 from platform_attachments.attachments attachment
  where attachment.attachment_id=selected.attachment_id
    and platform_hr.candidate_attachment_usable_v70(
      selected_owner_internal_user_id,selected.attachment_id
    ) for update;
  if not found then raise no_data_found; end if;
  if selected.row_version<>selected_expected_row_version
     or selected.state not in ('pending','failed') then raise serialization_failure; end if;
  update platform_hr.candidate_drafts set
    state='processing',error_code=null,
    last_mutation_request_id=selected_client_request_id,
    row_version=row_version+1,updated_at=now()
  where draft_id=selected_draft_id returning * into selected;
  insert into platform_hr.candidate_draft_mutation_events(
    owner_internal_user_id,client_request_id,draft_id,mutation_kind,
    canonical_payload,payload_sha256,result_id,result_snapshot
  ) values (
    selected_owner_internal_user_id,selected_client_request_id,selected_draft_id,
    'start',payload,sha256(convert_to(payload::text,'UTF8')),
    selected.draft_id,to_jsonb(selected)
  );
  return selected;
end
$function$;

create function platform_hr.complete_candidate_draft_v70(
  selected_owner_internal_user_id uuid,
  selected_draft_id uuid,
  selected_client_request_id uuid,
  selected_expected_row_version bigint,
  selected_extracted_facts jsonb,
  selected_identity_candidates uuid[]
) returns platform_hr.candidate_drafts
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.candidate_drafts%rowtype;
declare prior_event platform_hr.candidate_draft_mutation_events%rowtype;
declare payload jsonb;
begin
  if session_user not in (
    'platform_control_app','platform_control_app_preview',
    'platform_brain_worker','platform_brain_worker_preview'
  ) then
    raise insufficient_privilege;
  end if;
  if not platform_hr.candidate_json_safe_v70(selected_extracted_facts,true) then
    raise check_violation using message='candidate facts contain forbidden fields';
  end if;
  payload := jsonb_build_object(
    'draft_id',selected_draft_id,'expected_row_version',selected_expected_row_version,
    'extracted_facts',selected_extracted_facts,
    'identity_candidates',to_jsonb(selected_identity_candidates)
  );
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':candidate-draft-mutation:' ||
    selected_client_request_id::text,0
  ));
  select * into prior_event from platform_hr.candidate_draft_mutation_events
  where owner_internal_user_id=selected_owner_internal_user_id
    and client_request_id=selected_client_request_id;
  if found then
    if prior_event.draft_id<>selected_draft_id
       or prior_event.mutation_kind<>'complete'
       or prior_event.canonical_payload<>payload then
      raise unique_violation using message='candidate draft idempotency mismatch';
    end if;
    select * into selected from jsonb_populate_record(
      null::platform_hr.candidate_drafts,prior_event.result_snapshot
    );
    return selected;
  end if;
  select * into selected from platform_hr.candidate_drafts
  where draft_id=selected_draft_id
    and owner_internal_user_id=selected_owner_internal_user_id for update;
  if not found then raise no_data_found; end if;
  perform 1 from platform_attachments.attachments attachment
  where attachment.attachment_id=selected.attachment_id
    and platform_hr.candidate_attachment_usable_v70(
      selected_owner_internal_user_id,selected.attachment_id
    ) for update;
  if not found then raise no_data_found; end if;
  if selected.row_version<>selected_expected_row_version
     or selected.state<>'processing' then raise serialization_failure; end if;
  update platform_hr.candidate_drafts set
    state='ready',extracted_facts=selected_extracted_facts,
    identity_candidates=selected_identity_candidates,error_code=null,
    last_mutation_request_id=selected_client_request_id,
    row_version=row_version+1,updated_at=now()
  where draft_id=selected_draft_id returning * into selected;
  insert into platform_hr.candidate_draft_mutation_events(
    owner_internal_user_id,client_request_id,draft_id,mutation_kind,
    canonical_payload,payload_sha256,result_id,result_snapshot
  ) values (
    selected_owner_internal_user_id,selected_client_request_id,selected_draft_id,
    'complete',payload,sha256(convert_to(payload::text,'UTF8')),
    selected.draft_id,to_jsonb(selected)
  );
  return selected;
end
$function$;

create function platform_hr.fail_candidate_draft_v70(
  selected_owner_internal_user_id uuid,
  selected_draft_id uuid,
  selected_client_request_id uuid,
  selected_expected_row_version bigint,
  selected_error_code text
) returns platform_hr.candidate_drafts
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.candidate_drafts%rowtype;
declare prior_event platform_hr.candidate_draft_mutation_events%rowtype;
declare payload jsonb;
begin
  if session_user not in (
    'platform_control_app','platform_control_app_preview',
    'platform_brain_worker','platform_brain_worker_preview'
  ) then
    raise insufficient_privilege;
  end if;
  payload := jsonb_build_object(
    'draft_id',selected_draft_id,'expected_row_version',selected_expected_row_version,
    'error_code',btrim(selected_error_code)
  );
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':candidate-draft-mutation:' ||
    selected_client_request_id::text,0
  ));
  select * into prior_event from platform_hr.candidate_draft_mutation_events
  where owner_internal_user_id=selected_owner_internal_user_id
    and client_request_id=selected_client_request_id;
  if found then
    if prior_event.draft_id<>selected_draft_id or prior_event.mutation_kind<>'fail'
       or prior_event.canonical_payload<>payload then
      raise unique_violation using message='candidate draft idempotency mismatch';
    end if;
    select * into selected from jsonb_populate_record(
      null::platform_hr.candidate_drafts,prior_event.result_snapshot
    );
    return selected;
  end if;
  select * into selected from platform_hr.candidate_drafts
  where draft_id=selected_draft_id
    and owner_internal_user_id=selected_owner_internal_user_id for update;
  if not found then raise no_data_found; end if;
  perform 1 from platform_attachments.attachments attachment
  where attachment.attachment_id=selected.attachment_id
    and platform_hr.candidate_attachment_usable_v70(
      selected_owner_internal_user_id,selected.attachment_id
    ) for update;
  if not found then raise no_data_found; end if;
  if selected.row_version<>selected_expected_row_version
     or selected.state<>'processing' then raise serialization_failure; end if;
  update platform_hr.candidate_drafts set
    state='failed',error_code=btrim(selected_error_code),
    last_mutation_request_id=selected_client_request_id,
    row_version=row_version+1,updated_at=now()
  where draft_id=selected_draft_id returning * into selected;
  insert into platform_hr.candidate_draft_mutation_events(
    owner_internal_user_id,client_request_id,draft_id,mutation_kind,
    canonical_payload,payload_sha256,result_id,result_snapshot
  ) values (
    selected_owner_internal_user_id,selected_client_request_id,selected_draft_id,
    'fail',payload,sha256(convert_to(payload::text,'UTF8')),
    selected.draft_id,to_jsonb(selected)
  );
  return selected;
end
$function$;

create function platform_hr.complete_claimed_candidate_draft_v70(
  selected_attempt_id uuid,
  selected_client_request_id uuid,
  selected_expected_row_version bigint,
  selected_extracted_facts jsonb,
  selected_identity_candidates uuid[]
) returns platform_hr.candidate_drafts
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected_attempt platform_hr.candidate_draft_processing_attempts%rowtype;
declare selected platform_hr.candidate_drafts%rowtype;
begin
  if session_user not in ('platform_brain_worker','platform_brain_worker_preview') then
    raise insufficient_privilege;
  end if;
  select * into selected_attempt
  from platform_hr.candidate_draft_processing_attempts
  where attempt_id=selected_attempt_id for update;
  if not found then raise no_data_found; end if;
  if selected_attempt.state<>'processing' then
    if selected_attempt.state='completed'
       and selected_attempt.terminal_request_id=selected_client_request_id then
      return platform_hr.complete_candidate_draft_v70(
        selected_attempt.owner_internal_user_id,selected_attempt.draft_id,
        selected_client_request_id,selected_expected_row_version,
        selected_extracted_facts,selected_identity_candidates
      );
    end if;
    raise serialization_failure;
  end if;
  if selected_attempt.lease_expires_at<=now()
     or selected_attempt.claimed_row_version<>selected_expected_row_version then
    raise serialization_failure;
  end if;
  selected := platform_hr.complete_candidate_draft_v70(
    selected_attempt.owner_internal_user_id,selected_attempt.draft_id,
    selected_client_request_id,selected_expected_row_version,
    selected_extracted_facts,selected_identity_candidates
  );
  update platform_hr.candidate_draft_processing_attempts set
    state='completed',finished_at=now(),terminal_request_id=selected_client_request_id
  where attempt_id=selected_attempt_id;
  return selected;
end
$function$;

create function platform_hr.fail_claimed_candidate_draft_v70(
  selected_attempt_id uuid,
  selected_client_request_id uuid,
  selected_expected_row_version bigint,
  selected_error_code text
) returns platform_hr.candidate_drafts
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected_attempt platform_hr.candidate_draft_processing_attempts%rowtype;
declare selected platform_hr.candidate_drafts%rowtype;
begin
  if session_user not in ('platform_brain_worker','platform_brain_worker_preview') then
    raise insufficient_privilege;
  end if;
  select * into selected_attempt
  from platform_hr.candidate_draft_processing_attempts
  where attempt_id=selected_attempt_id for update;
  if not found then raise no_data_found; end if;
  if selected_attempt.state<>'processing' then
    if selected_attempt.state='failed'
       and selected_attempt.terminal_request_id=selected_client_request_id then
      return platform_hr.fail_candidate_draft_v70(
        selected_attempt.owner_internal_user_id,selected_attempt.draft_id,
        selected_client_request_id,selected_expected_row_version,selected_error_code
      );
    end if;
    raise serialization_failure;
  end if;
  if selected_attempt.lease_expires_at<=now()
     or selected_attempt.claimed_row_version<>selected_expected_row_version then
    raise serialization_failure;
  end if;
  selected := platform_hr.fail_candidate_draft_v70(
    selected_attempt.owner_internal_user_id,selected_attempt.draft_id,
    selected_client_request_id,selected_expected_row_version,selected_error_code
  );
  update platform_hr.candidate_draft_processing_attempts set
    state='failed',finished_at=now(),terminal_request_id=selected_client_request_id
  where attempt_id=selected_attempt_id;
  return selected;
end
$function$;

create function platform_hr.retry_candidate_draft_v70(
  selected_owner_internal_user_id uuid,
  selected_draft_id uuid,
  selected_client_request_id uuid,
  selected_expected_row_version bigint
) returns platform_hr.candidate_drafts
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.candidate_drafts%rowtype;
declare prior_event platform_hr.candidate_draft_mutation_events%rowtype;
declare payload jsonb;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  payload := jsonb_build_object(
    'draft_id',selected_draft_id,'expected_row_version',selected_expected_row_version
  );
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':candidate-draft-mutation:' ||
    selected_client_request_id::text,0
  ));
  select * into prior_event from platform_hr.candidate_draft_mutation_events
  where owner_internal_user_id=selected_owner_internal_user_id
    and client_request_id=selected_client_request_id;
  if found then
    if prior_event.draft_id<>selected_draft_id or prior_event.mutation_kind<>'retry'
       or prior_event.canonical_payload<>payload then
      raise unique_violation using message='candidate draft idempotency mismatch';
    end if;
    select * into selected from jsonb_populate_record(
      null::platform_hr.candidate_drafts,prior_event.result_snapshot
    );
    return selected;
  end if;
  select * into selected from platform_hr.candidate_drafts
  where draft_id=selected_draft_id
    and owner_internal_user_id=selected_owner_internal_user_id for update;
  if not found then raise no_data_found; end if;
  perform 1 from platform_attachments.attachments attachment
  where attachment.attachment_id=selected.attachment_id
    and platform_hr.candidate_attachment_usable_v70(
      selected_owner_internal_user_id,selected.attachment_id
    ) for update;
  if not found then raise no_data_found; end if;
  if selected.row_version<>selected_expected_row_version
     or selected.state<>'failed' then raise serialization_failure; end if;
  update platform_hr.candidate_drafts set
    state='pending',error_code=null,
    last_mutation_request_id=selected_client_request_id,
    row_version=row_version+1,updated_at=now()
  where draft_id=selected_draft_id returning * into selected;
  insert into platform_hr.candidate_draft_mutation_events(
    owner_internal_user_id,client_request_id,draft_id,mutation_kind,
    canonical_payload,payload_sha256,result_id,result_snapshot
  ) values (
    selected_owner_internal_user_id,selected_client_request_id,selected_draft_id,
    'retry',payload,sha256(convert_to(payload::text,'UTF8')),
    selected.draft_id,to_jsonb(selected)
  );
  return selected;
end
$function$;

create function platform_hr.confirm_candidate_draft_v70(
  selected_owner_internal_user_id uuid,
  selected_draft_id uuid,
  selected_client_request_id uuid,
  selected_expected_row_version bigint,
  selected_candidate_id uuid,
  selected_merge_candidate_id uuid,
  selected_document_id uuid,
  selected_position_candidate_id uuid,
  selected_context_version_id uuid,
  selected_stable_name text,
  selected_confirmed_facts jsonb
) returns platform_hr.position_candidates
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected_draft platform_hr.candidate_drafts%rowtype;
declare selected_relation platform_hr.position_candidates%rowtype;
declare prior_event platform_hr.candidate_confirmation_events%rowtype;
declare actual_candidate_id uuid;
declare next_document_version bigint;
declare selected_content_sha256 text;
declare payload jsonb;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  if not platform_hr.candidate_json_safe_v70(selected_confirmed_facts,true) then
    raise check_violation using message='candidate facts contain forbidden fields';
  end if;
  payload := jsonb_build_object(
    'draft_id',selected_draft_id,
    'expected_row_version',selected_expected_row_version,
    'candidate_id',selected_candidate_id,
    'merge_candidate_id',selected_merge_candidate_id,
    'document_id',selected_document_id,
    'position_candidate_id',selected_position_candidate_id,
    'context_version_id',selected_context_version_id,
    'stable_name',btrim(selected_stable_name),
    'confirmed_facts',selected_confirmed_facts
  );
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':candidate-confirm:' ||
    selected_client_request_id::text,0
  ));
  select * into prior_event from platform_hr.candidate_confirmation_events
  where owner_internal_user_id=selected_owner_internal_user_id
    and client_request_id=selected_client_request_id;
  if found then
    if prior_event.canonical_payload<>payload then
      raise unique_violation using message='candidate confirmation idempotency mismatch';
    end if;
    select * into selected_relation from jsonb_populate_record(
      null::platform_hr.position_candidates,prior_event.result_snapshot
    );
    return selected_relation;
  end if;
  select * into selected_draft from platform_hr.candidate_drafts
  where draft_id=selected_draft_id
    and owner_internal_user_id=selected_owner_internal_user_id for update;
  if not found then raise no_data_found; end if;
  if selected_draft.row_version<>selected_expected_row_version
     or selected_draft.state<>'ready' then raise serialization_failure; end if;
  if cardinality(selected_draft.identity_candidates)>0
     and selected_merge_candidate_id is null then
    raise unique_violation using message='candidate identity requires explicit merge target';
  end if;
  perform 1 from platform_attachments.attachments attachment
  where attachment.attachment_id=selected_draft.attachment_id
    and platform_hr.candidate_attachment_usable_v70(
      selected_owner_internal_user_id,selected_draft.attachment_id
    ) for update;
  if not found then raise no_data_found; end if;
  perform 1 from platform_hr.position_context_versions
  where context_version_id=selected_context_version_id
    and owner_internal_user_id=selected_owner_internal_user_id
    and position_id=selected_draft.position_id and state='confirmed';
  if not found then raise no_data_found; end if;
  if selected_merge_candidate_id is null then
    actual_candidate_id := selected_candidate_id;
    insert into platform_hr.candidates(
      candidate_id,owner_internal_user_id,confirmation_request_id,
      stable_name,facts
    ) values (
      actual_candidate_id,selected_owner_internal_user_id,
      selected_client_request_id,btrim(selected_stable_name),selected_confirmed_facts
    );
  else
    actual_candidate_id := selected_merge_candidate_id;
    perform 1 from platform_hr.candidates
    where candidate_id=actual_candidate_id
      and owner_internal_user_id=selected_owner_internal_user_id;
    if not found then raise no_data_found; end if;
    if not actual_candidate_id=any(selected_draft.identity_candidates) then
      raise unique_violation using message='candidate merge target was not proposed';
    end if;
  end if;
  select coalesce(max(version_number),0)+1 into next_document_version
  from platform_hr.candidate_documents
  where candidate_id=actual_candidate_id;
  select encode(sha256,'hex') into selected_content_sha256
  from platform_attachments.attachments
  where attachment_id=selected_draft.attachment_id
    and owner_internal_user_id=selected_owner_internal_user_id
    and state='ready' and sha256 is not null
    and deleted_at is null and retained_until>now();
  if not found then raise no_data_found; end if;
  insert into platform_hr.candidate_documents(
    document_id,owner_internal_user_id,candidate_id,attachment_id,
    source_draft_id,document_kind,version_number,content_sha256,status
  ) values (
    selected_document_id,selected_owner_internal_user_id,actual_candidate_id,
    selected_draft.attachment_id,selected_draft_id,'resume',
    next_document_version,selected_content_sha256,'active'
  );
  insert into platform_hr.position_candidates(
    position_candidate_id,owner_internal_user_id,position_id,candidate_id,
    context_version_id,source_draft_id,client_request_id,status
  ) values (
    selected_position_candidate_id,selected_owner_internal_user_id,
    selected_draft.position_id,actual_candidate_id,selected_context_version_id,
    selected_draft_id,selected_client_request_id,'active'
  ) on conflict (owner_internal_user_id,position_id,candidate_id) do update set
    context_version_id=excluded.context_version_id,
    source_draft_id=excluded.source_draft_id,
    client_request_id=excluded.client_request_id,
    status='active',row_version=position_candidates.row_version+1,updated_at=now()
  returning * into selected_relation;
  update platform_hr.candidate_drafts set
    state='confirmed',error_code=null,
    last_mutation_request_id=selected_client_request_id,
    row_version=row_version+1,updated_at=now()
  where draft_id=selected_draft_id;
  insert into platform_hr.candidate_confirmation_events(
    client_request_id,owner_internal_user_id,draft_id,expected_row_version,
    requested_candidate_id,merge_candidate_id,actual_candidate_id,
    document_id,position_candidate_id,context_version_id,stable_name,
    confirmed_facts,canonical_payload,payload_sha256,result_snapshot
  ) values (
    selected_client_request_id,selected_owner_internal_user_id,
    selected_draft_id,selected_expected_row_version,selected_candidate_id,
    selected_merge_candidate_id,actual_candidate_id,selected_document_id,
    selected_relation.position_candidate_id,selected_context_version_id,
    btrim(selected_stable_name),selected_confirmed_facts,payload,
    sha256(convert_to(payload::text,'UTF8')),to_jsonb(selected_relation)
  );
  return selected_relation;
end
$function$;

create function platform_hr.dismiss_candidate_draft_v70(
  selected_owner_internal_user_id uuid,
  selected_draft_id uuid,
  selected_client_request_id uuid,
  selected_expected_row_version bigint
) returns platform_hr.candidate_drafts
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.candidate_drafts%rowtype;
declare prior_event platform_hr.candidate_draft_mutation_events%rowtype;
declare payload jsonb;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  payload := jsonb_build_object(
    'draft_id',selected_draft_id,'expected_row_version',selected_expected_row_version
  );
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':candidate-draft-mutation:' ||
    selected_client_request_id::text,0
  ));
  select * into prior_event from platform_hr.candidate_draft_mutation_events
  where owner_internal_user_id=selected_owner_internal_user_id
    and client_request_id=selected_client_request_id;
  if found then
    if prior_event.draft_id<>selected_draft_id or prior_event.mutation_kind<>'dismiss'
       or prior_event.canonical_payload<>payload then
      raise unique_violation using message='candidate draft idempotency mismatch';
    end if;
    select * into selected from jsonb_populate_record(
      null::platform_hr.candidate_drafts,prior_event.result_snapshot
    );
    return selected;
  end if;
  select * into selected from platform_hr.candidate_drafts
  where draft_id=selected_draft_id
    and owner_internal_user_id=selected_owner_internal_user_id for update;
  if not found then raise no_data_found; end if;
  perform 1 from platform_attachments.attachments attachment
  where attachment.attachment_id=selected.attachment_id
    and platform_hr.candidate_attachment_usable_v70(
      selected_owner_internal_user_id,selected.attachment_id
    ) for update;
  if not found then raise no_data_found; end if;
  if selected.row_version<>selected_expected_row_version
     or selected.state not in ('pending','ready','failed') then
    raise serialization_failure;
  end if;
  update platform_hr.candidate_drafts set
    state='dismissed',error_code=null,
    last_mutation_request_id=selected_client_request_id,
    row_version=row_version+1,updated_at=now()
  where draft_id=selected_draft_id returning * into selected;
  insert into platform_hr.candidate_draft_mutation_events(
    owner_internal_user_id,client_request_id,draft_id,mutation_kind,
    canonical_payload,payload_sha256,result_id,result_snapshot
  ) values (
    selected_owner_internal_user_id,selected_client_request_id,selected_draft_id,
    'dismiss',payload,sha256(convert_to(payload::text,'UTF8')),
    selected.draft_id,to_jsonb(selected)
  );
  return selected;
end
$function$;

create function platform_hr.create_candidate_analysis_v70(
  selected_analysis_version_id uuid,
  selected_owner_internal_user_id uuid,
  selected_position_candidate_id uuid,
  selected_context_version_id uuid,
  selected_client_request_id uuid,
  selected_analysis_kind text,
  selected_document_ids uuid[],
  selected_feedback_ids uuid[],
  selected_result jsonb,
  selected_evidence jsonb,
  selected_unknowns jsonb,
  selected_conflicts jsonb,
  selected_verification_questions jsonb,
  selected_agent_version text,
  selected_model_version text
) returns platform_hr.candidate_analysis_versions
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.candidate_analysis_versions%rowtype;
declare relation platform_hr.position_candidates%rowtype;
declare next_version bigint;
declare selected_document_id uuid;
declare selected_feedback_id uuid;
declare payload jsonb;
begin
  if session_user not in (
    'platform_control_app','platform_control_app_preview',
    'platform_brain_worker','platform_brain_worker_preview'
  ) then raise insufficient_privilege; end if;
  if not platform_hr.candidate_json_safe_v70(selected_result,false)
     or not platform_hr.candidate_json_safe_v70(selected_evidence,false) then
    raise check_violation using message='candidate analysis contains forbidden fields';
  end if;
  payload := jsonb_build_object(
    'analysis_version_id',selected_analysis_version_id,
    'position_candidate_id',selected_position_candidate_id,
    'context_version_id',selected_context_version_id,
    'analysis_kind',selected_analysis_kind,
    'document_ids',to_jsonb(selected_document_ids),
    'feedback_ids',to_jsonb(selected_feedback_ids),
    'result',selected_result,'evidence',selected_evidence,
    'unknowns',selected_unknowns,'conflicts',selected_conflicts,
    'verification_questions',selected_verification_questions,
    'agent_version',btrim(selected_agent_version),
    'model_version',btrim(selected_model_version)
  );
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':candidate-analysis:' ||
    selected_client_request_id::text,0
  ));
  select * into selected from platform_hr.candidate_analysis_versions
  where owner_internal_user_id=selected_owner_internal_user_id
    and client_request_id=selected_client_request_id;
  if found then
    if selected.canonical_payload<>payload
       or selected.position_candidate_id<>selected_position_candidate_id
       or selected.context_version_id<>selected_context_version_id
       or selected.analysis_kind<>selected_analysis_kind
       or selected.result<>selected_result
       or selected.evidence<>selected_evidence
       or selected.unknowns<>selected_unknowns
       or selected.conflicts<>selected_conflicts
       or selected.verification_questions<>selected_verification_questions
       or selected.agent_version<>btrim(selected_agent_version)
       or selected.model_version<>btrim(selected_model_version)
       or coalesce((
         select array_agg(link.document_id order by link.document_id)
         from platform_hr.candidate_analysis_documents link
         where link.analysis_version_id=selected.analysis_version_id
       ),'{}'::uuid[])<>coalesce((
         select array_agg(value order by value)
         from unnest(selected_document_ids) value
       ),'{}'::uuid[])
       or coalesce((
         select array_agg(link.feedback_id order by link.feedback_id)
         from platform_hr.candidate_analysis_feedback link
         where link.analysis_version_id=selected.analysis_version_id
       ),'{}'::uuid[])<>coalesce((
         select array_agg(value order by value)
         from unnest(selected_feedback_ids) value
       ),'{}'::uuid[]) then
      raise unique_violation using message='candidate analysis idempotency mismatch';
    end if;
    return selected;
  end if;
  select * into relation from platform_hr.position_candidates
  where position_candidate_id=selected_position_candidate_id
    and owner_internal_user_id=selected_owner_internal_user_id
    and status='active';
  if not found or relation.context_version_id<>selected_context_version_id then
    raise no_data_found;
  end if;
  if cardinality(selected_document_ids)<1 then raise check_violation; end if;
  foreach selected_document_id in array selected_document_ids loop
    perform 1 from platform_hr.candidate_documents document
    join platform_attachments.attachments attachment
      on attachment.attachment_id=document.attachment_id
      and attachment.owner_internal_user_id=document.owner_internal_user_id
    where document.document_id=selected_document_id
      and document.owner_internal_user_id=selected_owner_internal_user_id
      and document.status='active'
      and attachment.state='ready' and attachment.deleted_at is null
      and attachment.retained_until>now()
      and not exists (
        select 1 from platform_attachments.erasure_jobs erasure
        where erasure.attachment_id=attachment.attachment_id
      )
      and (
        document.candidate_id=relation.candidate_id
        or (
          selected_analysis_kind='comparison'
          and exists (
            select 1 from platform_hr.position_candidates subject
            where subject.owner_internal_user_id=selected_owner_internal_user_id
              and subject.position_id=relation.position_id
              and subject.context_version_id=selected_context_version_id
              and subject.candidate_id=document.candidate_id
              and subject.status='active'
          )
        )
      ) for update of attachment;
    if not found then raise no_data_found; end if;
  end loop;
  foreach selected_feedback_id in array selected_feedback_ids loop
    perform 1 from platform_hr.human_feedback feedback
    join platform_hr.position_candidates feedback_relation
      on feedback_relation.position_candidate_id=feedback.position_candidate_id
      and feedback_relation.owner_internal_user_id=feedback.owner_internal_user_id
    where feedback.feedback_id=selected_feedback_id
      and feedback.owner_internal_user_id=selected_owner_internal_user_id
      and (
        feedback.position_candidate_id=selected_position_candidate_id
        or (
          selected_analysis_kind='comparison'
          and feedback_relation.position_id=relation.position_id
          and feedback_relation.context_version_id=selected_context_version_id
          and feedback_relation.status='active'
        )
      );
    if not found then raise no_data_found; end if;
  end loop;
  select coalesce(max(version_number),0)+1 into next_version
  from platform_hr.candidate_analysis_versions
  where position_candidate_id=selected_position_candidate_id;
  insert into platform_hr.candidate_analysis_versions(
    analysis_version_id,owner_internal_user_id,position_candidate_id,
    position_id,candidate_id,context_version_id,client_request_id,
    version_number,analysis_kind,result,evidence,unknowns,conflicts,
    verification_questions,agent_version,model_version,
    canonical_payload,payload_sha256
  ) values (
    selected_analysis_version_id,selected_owner_internal_user_id,
    selected_position_candidate_id,relation.position_id,relation.candidate_id,
    selected_context_version_id,selected_client_request_id,next_version,
    selected_analysis_kind,selected_result,selected_evidence,selected_unknowns,
    selected_conflicts,selected_verification_questions,
    btrim(selected_agent_version),btrim(selected_model_version),payload,
    sha256(convert_to(payload::text,'UTF8'))
  ) returning * into selected;
  insert into platform_hr.candidate_analysis_documents(
    analysis_version_id,document_id,owner_internal_user_id
  ) select selected_analysis_version_id,value,selected_owner_internal_user_id
  from unnest(selected_document_ids) value;
  insert into platform_hr.candidate_analysis_feedback(
    analysis_version_id,feedback_id,owner_internal_user_id
  ) select selected_analysis_version_id,value,selected_owner_internal_user_id
  from unnest(selected_feedback_ids) value;
  return selected;
end
$function$;

create function platform_hr.append_human_feedback_v70(
  selected_feedback_id uuid,
  selected_owner_internal_user_id uuid,
  selected_position_candidate_id uuid,
  selected_analysis_version_id uuid,
  selected_client_request_id uuid,
  selected_feedback_kind text,
  selected_conclusion_key text,
  selected_correction text,
  selected_reason text
) returns platform_hr.human_feedback
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.human_feedback%rowtype;
declare payload jsonb;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  payload := jsonb_build_object(
    'feedback_id',selected_feedback_id,
    'position_candidate_id',selected_position_candidate_id,
    'analysis_version_id',selected_analysis_version_id,
    'feedback_kind',selected_feedback_kind,
    'conclusion_key',btrim(selected_conclusion_key),
    'correction',case when selected_correction is null then null
      else btrim(selected_correction) end,
    'reason',btrim(selected_reason)
  );
  perform pg_advisory_xact_lock(hashtextextended(
    selected_owner_internal_user_id::text || ':candidate-feedback:' ||
    selected_client_request_id::text,0
  ));
  select * into selected from platform_hr.human_feedback
  where owner_internal_user_id=selected_owner_internal_user_id
    and client_request_id=selected_client_request_id;
  if found then
    if selected.canonical_payload<>payload
       or selected.position_candidate_id<>selected_position_candidate_id
       or selected.analysis_version_id<>selected_analysis_version_id
       or selected.feedback_kind<>selected_feedback_kind
       or selected.conclusion_key<>btrim(selected_conclusion_key)
       or selected.correction is distinct from (case
         when selected_correction is null then null else btrim(selected_correction)
       end)
       or selected.reason<>btrim(selected_reason) then
      raise unique_violation using message='candidate feedback idempotency mismatch';
    end if;
    return selected;
  end if;
  perform 1 from platform_hr.candidate_analysis_versions analysis
  where analysis.analysis_version_id=selected_analysis_version_id
    and analysis.owner_internal_user_id=selected_owner_internal_user_id
    and analysis.position_candidate_id=selected_position_candidate_id;
  if not found then raise no_data_found; end if;
  insert into platform_hr.human_feedback(
    feedback_id,owner_internal_user_id,position_candidate_id,
    analysis_version_id,client_request_id,feedback_kind,
    conclusion_key,correction,reason,canonical_payload,payload_sha256
  ) values (
    selected_feedback_id,selected_owner_internal_user_id,
    selected_position_candidate_id,selected_analysis_version_id,
    selected_client_request_id,selected_feedback_kind,
    btrim(selected_conclusion_key),
    case when selected_correction is null then null else btrim(selected_correction) end,
    btrim(selected_reason),payload,sha256(convert_to(payload::text,'UTF8'))
  ) returning * into selected;
  return selected;
end
$function$;

revoke all on all tables in schema platform_hr from public;
revoke all on all functions in schema platform_hr from public;
revoke all on function platform_hr.claim_candidate_draft_v70(
  uuid,uuid,uuid,text,uuid,uuid,uuid,integer
) from public;
revoke all on function platform_hr.read_candidate_draft_attempt_v70(
  uuid,uuid
) from public;
revoke all on function platform_hr.complete_claimed_candidate_draft_v70(
  uuid,uuid,bigint,jsonb,uuid[]
) from public;
revoke all on function platform_hr.fail_claimed_candidate_draft_v70(
  uuid,uuid,bigint,text
) from public;
revoke all on function platform_hr.register_candidate_draft_batch_v70(
  uuid,uuid,uuid,uuid[]
) from public;
revoke all on function platform_hr.create_candidate_draft_v70(
  uuid,uuid,uuid,uuid,uuid,uuid
) from public;
revoke all on function platform_hr.start_candidate_draft_v70(
  uuid,uuid,uuid,bigint
) from public;
revoke all on function platform_hr.complete_candidate_draft_v70(
  uuid,uuid,uuid,bigint,jsonb,uuid[]
) from public;
revoke all on function platform_hr.fail_candidate_draft_v70(
  uuid,uuid,uuid,bigint,text
) from public;
revoke all on function platform_hr.retry_candidate_draft_v70(
  uuid,uuid,uuid,bigint
) from public;
revoke all on function platform_hr.confirm_candidate_draft_v70(
  uuid,uuid,uuid,bigint,uuid,uuid,uuid,uuid,uuid,text,jsonb
) from public;
revoke all on function platform_hr.dismiss_candidate_draft_v70(
  uuid,uuid,uuid,bigint
) from public;
revoke all on function platform_hr.create_candidate_analysis_v70(
  uuid,uuid,uuid,uuid,uuid,text,uuid[],uuid[],jsonb,jsonb,jsonb,jsonb,jsonb,text,text
) from public;
revoke all on function platform_hr.append_human_feedback_v70(
  uuid,uuid,uuid,uuid,uuid,text,text,text,text
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
      message='HR candidate migration owner/environment mismatch';
  end if;
  execute format('grant usage on schema platform_hr to %I,%I',selected_app,selected_brain);
  execute format('grant select on all tables in schema platform_hr to %I',selected_app);
  execute format(
    'grant execute on function platform_hr.claim_candidate_draft_v70('
    'uuid,uuid,uuid,text,uuid,uuid,uuid,integer) to %I',selected_brain
  );
  execute format(
    'grant execute on function platform_hr.read_candidate_draft_attempt_v70('
    'uuid,uuid) to %I,%I',selected_app,selected_brain
  );
  execute format(
    'grant execute on function platform_hr.complete_claimed_candidate_draft_v70('
    'uuid,uuid,bigint,jsonb,uuid[]) to %I',selected_brain
  );
  execute format(
    'grant execute on function platform_hr.fail_claimed_candidate_draft_v70('
    'uuid,uuid,bigint,text) to %I',selected_brain
  );
  execute format(
    'grant execute on function platform_hr.register_candidate_draft_batch_v70('
    'uuid,uuid,uuid,uuid[]) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.create_candidate_draft_v70('
    'uuid,uuid,uuid,uuid,uuid,uuid) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.start_candidate_draft_v70('
    'uuid,uuid,uuid,bigint) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.complete_candidate_draft_v70('
    'uuid,uuid,uuid,bigint,jsonb,uuid[]) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.fail_candidate_draft_v70('
    'uuid,uuid,uuid,bigint,text) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.retry_candidate_draft_v70('
    'uuid,uuid,uuid,bigint) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.confirm_candidate_draft_v70('
    'uuid,uuid,uuid,bigint,uuid,uuid,uuid,uuid,uuid,text,jsonb) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.dismiss_candidate_draft_v70('
    'uuid,uuid,uuid,bigint) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.create_candidate_analysis_v70('
    'uuid,uuid,uuid,uuid,uuid,text,uuid[],uuid[],jsonb,jsonb,jsonb,jsonb,jsonb,text,text) to %I,%I',
    selected_app,selected_brain
  );
  execute format(
    'grant execute on function platform_hr.append_human_feedback_v70('
    'uuid,uuid,uuid,uuid,uuid,text,text,text,text) to %I',selected_app
  );
end
$migration$;
