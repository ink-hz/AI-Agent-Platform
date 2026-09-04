create table platform_hr.candidate_drafts (
  draft_id uuid primary key,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  position_id uuid not null,
  attachment_id uuid not null,
  batch_request_id uuid not null,
  client_request_id uuid not null,
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
  check ((state='failed')=(error_code is not null)),
  unique (draft_id,owner_internal_user_id),
  unique (owner_internal_user_id,client_request_id),
  unique (owner_internal_user_id,batch_request_id,attachment_id)
);

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
  foreign key (context_version_id,owner_internal_user_id)
    references platform_hr.position_context_versions(context_version_id,owner_internal_user_id),
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
  created_at timestamptz not null default now(),
  foreign key (
    position_candidate_id,owner_internal_user_id,position_id,candidate_id
  ) references platform_hr.position_candidates(
    position_candidate_id,owner_internal_user_id,position_id,candidate_id
  ),
  foreign key (context_version_id,owner_internal_user_id)
    references platform_hr.position_context_versions(context_version_id,owner_internal_user_id),
  unique (analysis_version_id,owner_internal_user_id),
  unique (owner_internal_user_id,client_request_id),
  unique (position_candidate_id,version_number)
);

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

create function platform_hr.reject_candidate_history_mutation_v68()
returns trigger language plpgsql
set search_path=pg_catalog,platform_hr
as $function$
begin
  raise check_violation using message='candidate history is immutable';
end
$function$;

create trigger candidate_analysis_versions_immutable_v68
before update or delete on platform_hr.candidate_analysis_versions
for each row execute function platform_hr.reject_candidate_history_mutation_v68();

create trigger human_feedback_immutable_v68
before update or delete on platform_hr.human_feedback
for each row execute function platform_hr.reject_candidate_history_mutation_v68();

create trigger candidate_analysis_documents_immutable_v68
before update or delete on platform_hr.candidate_analysis_documents
for each row execute function platform_hr.reject_candidate_history_mutation_v68();

create trigger candidate_analysis_feedback_immutable_v68
before update or delete on platform_hr.candidate_analysis_feedback
for each row execute function platform_hr.reject_candidate_history_mutation_v68();

create function platform_hr.create_candidate_draft_v68(
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
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  select * into selected from platform_hr.candidate_drafts
  where owner_internal_user_id=selected_owner_internal_user_id
    and client_request_id=selected_client_request_id;
  if found then
    if selected.position_id<>selected_position_id
       or selected.attachment_id<>selected_attachment_id
       or selected.batch_request_id<>selected_batch_request_id then
      raise unique_violation using message='candidate draft idempotency mismatch';
    end if;
    return selected;
  end if;
  perform 1 from platform_hr.positions
  where position_id=selected_position_id
    and owner_internal_user_id=selected_owner_internal_user_id;
  if not found then raise no_data_found; end if;
  perform 1 from platform_attachments.attachments
  where attachment_id=selected_attachment_id
    and owner_internal_user_id=selected_owner_internal_user_id
    and source_kind='user_input' and state='ready'
    and deleted_at is null and retained_until>now();
  if not found then raise no_data_found; end if;
  insert into platform_hr.candidate_drafts(
    draft_id,owner_internal_user_id,position_id,attachment_id,
    batch_request_id,client_request_id,state
  ) values (
    selected_draft_id,selected_owner_internal_user_id,selected_position_id,
    selected_attachment_id,selected_batch_request_id,
    selected_client_request_id,'pending'
  ) on conflict (owner_internal_user_id,batch_request_id,attachment_id)
  do update set batch_request_id=candidate_drafts.batch_request_id
  returning * into selected;
  return selected;
end
$function$;

create function platform_hr.start_candidate_draft_v68(
  selected_owner_internal_user_id uuid,
  selected_draft_id uuid,
  selected_client_request_id uuid,
  selected_expected_row_version bigint
) returns platform_hr.candidate_drafts
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.candidate_drafts%rowtype;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  select * into selected from platform_hr.candidate_drafts
  where draft_id=selected_draft_id
    and owner_internal_user_id=selected_owner_internal_user_id for update;
  if not found then raise no_data_found; end if;
  if selected.last_mutation_request_id=selected_client_request_id then return selected; end if;
  if selected.row_version<>selected_expected_row_version
     or selected.state not in ('pending','failed') then raise serialization_failure; end if;
  update platform_hr.candidate_drafts set
    state='processing',error_code=null,
    last_mutation_request_id=selected_client_request_id,
    row_version=row_version+1,updated_at=now()
  where draft_id=selected_draft_id returning * into selected;
  return selected;
end
$function$;

create function platform_hr.complete_candidate_draft_v68(
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
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  select * into selected from platform_hr.candidate_drafts
  where draft_id=selected_draft_id
    and owner_internal_user_id=selected_owner_internal_user_id for update;
  if not found then raise no_data_found; end if;
  if selected.last_mutation_request_id=selected_client_request_id then return selected; end if;
  if selected.row_version<>selected_expected_row_version
     or selected.state<>'processing' then raise serialization_failure; end if;
  update platform_hr.candidate_drafts set
    state='ready',extracted_facts=selected_extracted_facts,
    identity_candidates=selected_identity_candidates,error_code=null,
    last_mutation_request_id=selected_client_request_id,
    row_version=row_version+1,updated_at=now()
  where draft_id=selected_draft_id returning * into selected;
  return selected;
end
$function$;

create function platform_hr.fail_candidate_draft_v68(
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
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  select * into selected from platform_hr.candidate_drafts
  where draft_id=selected_draft_id
    and owner_internal_user_id=selected_owner_internal_user_id for update;
  if not found then raise no_data_found; end if;
  if selected.last_mutation_request_id=selected_client_request_id then return selected; end if;
  if selected.row_version<>selected_expected_row_version
     or selected.state<>'processing' then raise serialization_failure; end if;
  update platform_hr.candidate_drafts set
    state='failed',error_code=btrim(selected_error_code),
    last_mutation_request_id=selected_client_request_id,
    row_version=row_version+1,updated_at=now()
  where draft_id=selected_draft_id returning * into selected;
  return selected;
end
$function$;

create function platform_hr.retry_candidate_draft_v68(
  selected_owner_internal_user_id uuid,
  selected_draft_id uuid,
  selected_client_request_id uuid,
  selected_expected_row_version bigint
) returns platform_hr.candidate_drafts
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.candidate_drafts%rowtype;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  select * into selected from platform_hr.candidate_drafts
  where draft_id=selected_draft_id
    and owner_internal_user_id=selected_owner_internal_user_id for update;
  if not found then raise no_data_found; end if;
  if selected.last_mutation_request_id=selected_client_request_id then return selected; end if;
  if selected.row_version<>selected_expected_row_version
     or selected.state<>'failed' then raise serialization_failure; end if;
  update platform_hr.candidate_drafts set
    state='pending',error_code=null,
    last_mutation_request_id=selected_client_request_id,
    row_version=row_version+1,updated_at=now()
  where draft_id=selected_draft_id returning * into selected;
  return selected;
end
$function$;

create function platform_hr.confirm_candidate_draft_v68(
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
declare actual_candidate_id uuid;
declare next_document_version bigint;
declare selected_content_sha256 text;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  select * into selected_relation from platform_hr.position_candidates
  where owner_internal_user_id=selected_owner_internal_user_id
    and client_request_id=selected_client_request_id;
  if found then return selected_relation; end if;
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
  perform 1 from platform_hr.position_context_versions
  where context_version_id=selected_context_version_id
    and owner_internal_user_id=selected_owner_internal_user_id
    and position_id=selected_draft.position_id and status='confirmed';
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
      raise no_data_found;
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
    status='active',updated_at=now()
  returning * into selected_relation;
  update platform_hr.candidate_drafts set
    state='confirmed',error_code=null,
    last_mutation_request_id=selected_client_request_id,
    row_version=row_version+1,updated_at=now()
  where draft_id=selected_draft_id;
  return selected_relation;
end
$function$;

create function platform_hr.dismiss_candidate_draft_v68(
  selected_owner_internal_user_id uuid,
  selected_draft_id uuid,
  selected_client_request_id uuid,
  selected_expected_row_version bigint
) returns platform_hr.candidate_drafts
language plpgsql security definer
set search_path=pg_catalog,platform_hr
as $function$
declare selected platform_hr.candidate_drafts%rowtype;
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  select * into selected from platform_hr.candidate_drafts
  where draft_id=selected_draft_id
    and owner_internal_user_id=selected_owner_internal_user_id for update;
  if not found then raise no_data_found; end if;
  if selected.last_mutation_request_id=selected_client_request_id then return selected; end if;
  if selected.row_version<>selected_expected_row_version
     or selected.state not in ('pending','ready','failed') then
    raise serialization_failure;
  end if;
  update platform_hr.candidate_drafts set
    state='dismissed',error_code=null,
    last_mutation_request_id=selected_client_request_id,
    row_version=row_version+1,updated_at=now()
  where draft_id=selected_draft_id returning * into selected;
  return selected;
end
$function$;

create function platform_hr.create_candidate_analysis_v68(
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
begin
  if session_user not in (
    'platform_control_app','platform_control_app_preview',
    'platform_brain_worker','platform_brain_worker_preview'
  ) then raise insufficient_privilege; end if;
  select * into selected from platform_hr.candidate_analysis_versions
  where owner_internal_user_id=selected_owner_internal_user_id
    and client_request_id=selected_client_request_id;
  if found then
    if selected.position_candidate_id<>selected_position_candidate_id
       or selected.context_version_id<>selected_context_version_id
       or selected.analysis_kind<>selected_analysis_kind then
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
      );
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
    verification_questions,agent_version,model_version
  ) values (
    selected_analysis_version_id,selected_owner_internal_user_id,
    selected_position_candidate_id,relation.position_id,relation.candidate_id,
    selected_context_version_id,selected_client_request_id,next_version,
    selected_analysis_kind,selected_result,selected_evidence,selected_unknowns,
    selected_conflicts,selected_verification_questions,
    btrim(selected_agent_version),btrim(selected_model_version)
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

create function platform_hr.append_human_feedback_v68(
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
begin
  if session_user not in ('platform_control_app','platform_control_app_preview') then
    raise insufficient_privilege;
  end if;
  select * into selected from platform_hr.human_feedback
  where owner_internal_user_id=selected_owner_internal_user_id
    and client_request_id=selected_client_request_id;
  if found then
    if selected.position_candidate_id<>selected_position_candidate_id
       or selected.analysis_version_id<>selected_analysis_version_id
       or selected.feedback_kind<>selected_feedback_kind
       or selected.conclusion_key<>btrim(selected_conclusion_key) then
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
    conclusion_key,correction,reason
  ) values (
    selected_feedback_id,selected_owner_internal_user_id,
    selected_position_candidate_id,selected_analysis_version_id,
    selected_client_request_id,selected_feedback_kind,
    btrim(selected_conclusion_key),
    case when selected_correction is null then null else btrim(selected_correction) end,
    btrim(selected_reason)
  ) returning * into selected;
  return selected;
end
$function$;

revoke all on all tables in schema platform_hr from public;
revoke all on all functions in schema platform_hr from public;
revoke all on function platform_hr.create_candidate_draft_v68(
  uuid,uuid,uuid,uuid,uuid,uuid
) from public;
revoke all on function platform_hr.start_candidate_draft_v68(
  uuid,uuid,uuid,bigint
) from public;
revoke all on function platform_hr.complete_candidate_draft_v68(
  uuid,uuid,uuid,bigint,jsonb,uuid[]
) from public;
revoke all on function platform_hr.fail_candidate_draft_v68(
  uuid,uuid,uuid,bigint,text
) from public;
revoke all on function platform_hr.retry_candidate_draft_v68(
  uuid,uuid,uuid,bigint
) from public;
revoke all on function platform_hr.confirm_candidate_draft_v68(
  uuid,uuid,uuid,bigint,uuid,uuid,uuid,uuid,uuid,text,jsonb
) from public;
revoke all on function platform_hr.dismiss_candidate_draft_v68(
  uuid,uuid,uuid,bigint
) from public;
revoke all on function platform_hr.create_candidate_analysis_v68(
  uuid,uuid,uuid,uuid,uuid,text,uuid[],uuid[],jsonb,jsonb,jsonb,jsonb,jsonb,text,text
) from public;
revoke all on function platform_hr.append_human_feedback_v68(
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
  execute format('grant select on all tables in schema platform_hr to %I,%I',selected_app,selected_brain);
  execute format(
    'grant execute on function platform_hr.create_candidate_draft_v68('
    'uuid,uuid,uuid,uuid,uuid,uuid) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.start_candidate_draft_v68('
    'uuid,uuid,uuid,bigint) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.complete_candidate_draft_v68('
    'uuid,uuid,uuid,bigint,jsonb,uuid[]) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.fail_candidate_draft_v68('
    'uuid,uuid,uuid,bigint,text) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.retry_candidate_draft_v68('
    'uuid,uuid,uuid,bigint) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.confirm_candidate_draft_v68('
    'uuid,uuid,uuid,bigint,uuid,uuid,uuid,uuid,uuid,text,jsonb) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.dismiss_candidate_draft_v68('
    'uuid,uuid,uuid,bigint) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_hr.create_candidate_analysis_v68('
    'uuid,uuid,uuid,uuid,uuid,text,uuid[],uuid[],jsonb,jsonb,jsonb,jsonb,jsonb,text,text) to %I,%I',
    selected_app,selected_brain
  );
  execute format(
    'grant execute on function platform_hr.append_human_feedback_v68('
    'uuid,uuid,uuid,uuid,uuid,text,text,text,text) to %I',selected_app
  );
end
$migration$;
