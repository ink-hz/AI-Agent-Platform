create schema platform_attachments authorization current_user;
revoke all on schema platform_attachments from public;

create table platform_attachments.attachments (
  attachment_id uuid primary key,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  conversation_id uuid
    references platform_control.conversations(conversation_id),
  source_kind text not null
    check (source_kind in ('user_input','agent_output')),
  original_name_ciphertext bytea not null
    check (octet_length(original_name_ciphertext) between 29 and 1048576),
  original_name_key_version integer not null
    check (original_name_key_version > 0),
  object_ref_ciphertext bytea not null
    check (octet_length(object_ref_ciphertext) between 29 and 1048576),
  object_ref_key_version integer not null
    check (object_ref_key_version > 0),
  declared_mime text,
  detected_mime text,
  size_bytes bigint not null default 0 check (size_bytes >= 0),
  sha256 bytea check (sha256 is null or octet_length(sha256) = 32),
  retained_until timestamptz not null
    default (now() + interval '365 days'),
  state text not null default 'uploading' check (state in (
    'uploading','validating','scanning','ready','quarantined','rejected','deleted'
  )),
  state_reason text,
  created_at timestamptz not null default now(),
  ready_at timestamptz,
  deleted_at timestamptz,
  check ((state = 'ready') = (ready_at is not null) or state <> 'ready'),
  check ((state = 'deleted') = (deleted_at is not null) or state <> 'deleted')
);

create table platform_attachments.uploads (
  upload_id uuid primary key,
  attachment_id uuid not null unique
    references platform_attachments.attachments(attachment_id),
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  object_ref_ciphertext bytea not null
    check (octet_length(object_ref_ciphertext) between 29 and 1048576),
  object_ref_key_version integer not null check (object_ref_key_version > 0),
  detected_mime text,
  size_bytes bigint not null default 0 check (size_bytes >= 0),
  sha256 bytea check (sha256 is null or octet_length(sha256) = 32),
  retained_until timestamptz not null
    default (now() + interval '365 days'),
  state text not null default 'uploading' check (state in (
    'uploading','validating','scanning','ready','quarantined','rejected','deleted'
  )),
  state_reason text,
  expires_at timestamptz not null default (now() + interval '24 hours'),
  created_at timestamptz not null default now(),
  finalized_at timestamptz
);

create table platform_attachments.bindings (
  binding_id uuid primary key,
  attachment_id uuid not null
    references platform_attachments.attachments(attachment_id),
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  kind text not null check (kind in (
    'conversation_material','message_input','turn_input',
    'task_input','task_output','message_output'
  )),
  conversation_id uuid
    references platform_control.conversations(conversation_id),
  message_id uuid,
  turn_id uuid,
  task_id uuid references platform_control.mission_tasks(task_id),
  agent_id text check (
    agent_id is null or agent_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
  ),
  created_at timestamptz not null default now(),
  foreign key (conversation_id,message_id)
    references platform_control.conversation_messages(conversation_id,message_id),
  foreign key (conversation_id,turn_id)
    references platform_control.conversation_turns(conversation_id,turn_id),
  check (
    (kind = 'conversation_material' and conversation_id is not null
      and message_id is null and turn_id is null and task_id is null
      and agent_id is null)
    or (kind in ('message_input','message_output') and conversation_id is not null
      and message_id is not null and turn_id is null)
    or (kind = 'turn_input' and conversation_id is not null
      and turn_id is not null and message_id is null)
    or (kind in ('task_input','task_output') and task_id is not null
      and agent_id is not null)
  ),
  unique nulls not distinct (
    attachment_id,kind,conversation_id,message_id,turn_id,task_id
  )
);

create table platform_attachments.artifacts (
  artifact_id uuid primary key,
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  conversation_id uuid not null
    references platform_control.conversations(conversation_id),
  task_id uuid not null references platform_control.mission_tasks(task_id),
  agent_id text not null
    check (agent_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'),
  label_ciphertext bytea
    check (label_ciphertext is null or octet_length(label_ciphertext) between 29 and 1048576),
  label_key_version integer check (label_key_version is null or label_key_version > 0),
  created_at timestamptz not null default now(),
  check (num_nonnulls(label_ciphertext,label_key_version) in (0,2))
);

create table platform_attachments.artifact_versions (
  artifact_version_id uuid primary key,
  artifact_id uuid not null
    references platform_attachments.artifacts(artifact_id),
  attachment_id uuid not null unique
    references platform_attachments.attachments(attachment_id),
  version_no integer not null check (version_no > 0),
  original_name_ciphertext bytea not null
    check (octet_length(original_name_ciphertext) between 29 and 1048576),
  original_name_key_version integer not null check (original_name_key_version > 0),
  object_ref_ciphertext bytea not null
    check (octet_length(object_ref_ciphertext) between 29 and 1048576),
  object_ref_key_version integer not null check (object_ref_key_version > 0),
  detected_mime text,
  size_bytes bigint not null default 0 check (size_bytes >= 0),
  sha256 bytea check (sha256 is null or octet_length(sha256) = 32),
  retained_until timestamptz not null
    default (now() + interval '365 days'),
  state text not null default 'validating' check (state in (
    'uploading','validating','scanning','ready','quarantined','rejected','deleted'
  )),
  state_reason text,
  result_status text not null default 'pending'
    check (result_status in ('pending','succeeded','failed')),
  created_at timestamptz not null default now(),
  unique (artifact_id,version_no)
);

create view platform_attachments.current_artifact_versions as
select ranked.*
from (
  select version.*,
    row_number() over (
      partition by artifact_id order by version_no desc,created_at desc
    ) as current_rank
  from platform_attachments.artifact_versions version
  where state = 'ready' and result_status = 'succeeded'
) ranked
where ranked.current_rank = 1;

create table platform_attachments.derivatives (
  derivative_id uuid primary key,
  attachment_id uuid not null
    references platform_attachments.attachments(attachment_id),
  kind text not null check (kind in ('thumbnail','preview','text','ocr')),
  object_ref_ciphertext bytea not null
    check (octet_length(object_ref_ciphertext) between 29 and 1048576),
  object_ref_key_version integer not null check (object_ref_key_version > 0),
  detected_mime text,
  size_bytes bigint not null default 0 check (size_bytes >= 0),
  sha256 bytea not null check (octet_length(sha256) = 32),
  retained_until timestamptz not null
    default (now() + interval '365 days'),
  state text not null default 'validating' check (state in (
    'uploading','validating','scanning','ready','quarantined','rejected','deleted'
  )),
  state_reason text,
  created_at timestamptz not null default now(),
  unique (attachment_id,kind)
);

create table platform_attachments.task_grants (
  grant_id uuid primary key,
  token_sha256 bytea not null unique check (octet_length(token_sha256) = 32),
  task_id uuid not null references platform_control.mission_tasks(task_id),
  attachment_id uuid not null
    references platform_attachments.attachments(attachment_id),
  agent_id text not null
    check (agent_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'),
  scope text not null check (scope in ('read','write_output')),
  expires_at timestamptz not null,
  max_reads integer not null check (max_reads > 0),
  read_count integer not null default 0
    check (read_count >= 0 and read_count <= max_reads),
  max_bytes bigint not null check (max_bytes > 0),
  bytes_read bigint not null default 0
    check (bytes_read >= 0 and bytes_read <= max_bytes),
  created_at timestamptz not null default now(),
  revoked_at timestamptz,
  unique (task_id,attachment_id,agent_id,scope,grant_id)
);

create table platform_attachments.access_events (
  access_event_id uuid primary key,
  attachment_id uuid not null
    references platform_attachments.attachments(attachment_id),
  grant_id uuid references platform_attachments.task_grants(grant_id),
  actor_internal_user_id uuid
    references platform_control.internal_users(internal_user_id),
  task_id uuid references platform_control.mission_tasks(task_id),
  agent_id text,
  operation text not null check (operation in (
    'preview','download','agent_read','agent_write','erase'
  )),
  result text not null check (result in ('allowed','denied','completed','failed')),
  byte_count bigint not null default 0 check (byte_count >= 0),
  metadata_sha256 bytea check (metadata_sha256 is null or octet_length(metadata_sha256) = 32),
  created_at timestamptz not null default now()
);

create table platform_attachments.processing_jobs (
  processing_job_id uuid primary key,
  attachment_id uuid not null
    references platform_attachments.attachments(attachment_id),
  job_kind text not null check (job_kind in ('validate','scan','derive')),
  state text not null default 'queued'
    check (state in ('queued','running','completed','failed')),
  state_reason text,
  attempt_count integer not null default 0 check (attempt_count >= 0),
  available_at timestamptz not null default now(),
  claimed_by text,
  claimed_at timestamptz,
  created_at timestamptz not null default now(),
  completed_at timestamptz,
  unique (attachment_id,job_kind)
);

create table platform_attachments.erasure_jobs (
  erasure_job_id uuid primary key,
  attachment_id uuid not null
    references platform_attachments.attachments(attachment_id),
  requested_by_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  reason_ciphertext bytea not null
    check (octet_length(reason_ciphertext) between 29 and 1048576),
  reason_key_version integer not null check (reason_key_version > 0),
  reason_sha256 bytea not null check (octet_length(reason_sha256) = 32),
  state text not null default 'queued'
    check (state in ('queued','running','completed','partial','failed')),
  state_reason text,
  attempt_count integer not null default 0 check (attempt_count >= 0),
  available_at timestamptz not null default now(),
  claimed_by text,
  claimed_at timestamptz,
  downstream_cleanup_status jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  completed_at timestamptz
);

create unique index one_active_erasure_job_v64
  on platform_attachments.erasure_jobs(attachment_id)
  where state in ('queued','running');

create table platform_attachments.message_citations (
  citation_id uuid primary key,
  conversation_id uuid not null
    references platform_control.conversations(conversation_id),
  message_id uuid not null,
  ordinal integer not null check (ordinal > 0),
  url_ciphertext bytea not null
    check (octet_length(url_ciphertext) between 29 and 1048576),
  url_key_version integer not null check (url_key_version > 0),
  title_ciphertext bytea
    check (title_ciphertext is null or octet_length(title_ciphertext) between 29 and 1048576),
  title_key_version integer check (title_key_version is null or title_key_version > 0),
  retrieved_at timestamptz not null,
  created_at timestamptz not null default now(),
  foreign key (conversation_id,message_id)
    references platform_control.conversation_messages(conversation_id,message_id),
  check (num_nonnulls(title_ciphertext,title_key_version) in (0,2)),
  unique (message_id,ordinal)
);

create table platform_attachments.conversation_read_state (
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  conversation_id uuid not null
    references platform_control.conversations(conversation_id),
  last_read_message_seq integer not null default 0
    check (last_read_message_seq >= 0),
  last_read_at timestamptz not null default now(),
  primary key (owner_internal_user_id,conversation_id)
);

create index attachments_owner_created_v64
  on platform_attachments.attachments(owner_internal_user_id,created_at desc);
create index bindings_conversation_kind_v64
  on platform_attachments.bindings(conversation_id,kind,created_at);
create index bindings_attachment_kind_v64
  on platform_attachments.bindings(attachment_id,kind);
create index artifact_versions_artifact_state_v64
  on platform_attachments.artifact_versions(artifact_id,state,version_no desc);
create index task_grants_token_v64
  on platform_attachments.task_grants(token_sha256)
  where revoked_at is null;
create unique index one_active_task_grant_v64
  on platform_attachments.task_grants(task_id,attachment_id,agent_id,scope)
  where revoked_at is null;
create index processing_jobs_claim_v64
  on platform_attachments.processing_jobs(state,available_at,created_at)
  where state = 'queued';
create index erasure_jobs_claim_v64
  on platform_attachments.erasure_jobs(state,available_at,created_at)
  where state = 'queued';
create index message_citations_message_ordinal_v64
  on platform_attachments.message_citations(message_id,ordinal);

alter table platform_control.conversation_feedback
  drop constraint conversation_feedback_reason_v44,
  add constraint conversation_feedback_reason_v64 check (
    reason is null or reason in (
      'inaccurate','incomplete','unclear','unresolved',
      'file_format','source_timeliness','other'
    )
  ),
  add column triage_status text not null default 'pending_triage'
    check (triage_status in ('pending_triage','triaged','dismissed'));

create function platform_attachments.finalize_upload_v64(
  selected_upload_id uuid,
  selected_owner_internal_user_id uuid,
  selected_detected_mime text,
  selected_size_bytes bigint,
  selected_sha256 bytea
) returns uuid
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
declare selected_attachment_id uuid;
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <> (session_user='platform_control_app')
  then raise insufficient_privilege using message='Attachment upload caller invalid'; end if;
  if selected_detected_mime is null or selected_size_bytes < 0
     or octet_length(selected_sha256) <> 32
  then raise check_violation using message='Attachment upload result invalid'; end if;
  select attachment_id into selected_attachment_id
  from platform_attachments.uploads
  where upload_id=selected_upload_id
    and owner_internal_user_id=selected_owner_internal_user_id
    and state='uploading' and expires_at > now()
  for update;
  if not found then raise no_data_found using message='Upload unavailable'; end if;
  update platform_attachments.uploads set
    detected_mime=selected_detected_mime,size_bytes=selected_size_bytes,
    sha256=selected_sha256,state='validating',finalized_at=now()
  where upload_id=selected_upload_id;
  update platform_attachments.attachments set
    detected_mime=selected_detected_mime,size_bytes=selected_size_bytes,
    sha256=selected_sha256,state='validating'
  where attachment_id=selected_attachment_id;
  insert into platform_attachments.processing_jobs(
    processing_job_id,attachment_id,job_kind
  ) values (gen_random_uuid(),selected_attachment_id,'validate');
  return selected_attachment_id;
end
$function$;

create function platform_attachments.claim_attachment_processing_job_v64(
  selected_worker_id text
) returns platform_attachments.processing_jobs
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
declare selected_job platform_attachments.processing_jobs%rowtype;
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_brain_worker','platform_brain_worker_preview')
     or (current_database()='agent_platform_control') <> (session_user='platform_brain_worker')
  then raise insufficient_privilege using message='Attachment processing caller invalid'; end if;
  if selected_worker_id is null or char_length(selected_worker_id) not between 1 and 128
  then raise check_violation using message='Attachment worker invalid'; end if;
  select * into selected_job from platform_attachments.processing_jobs
  where state='queued' and available_at <= now()
  order by available_at,created_at for update skip locked limit 1;
  if not found then return null; end if;
  update platform_attachments.processing_jobs set
    state='running',claimed_by=selected_worker_id,claimed_at=now(),
    attempt_count=attempt_count+1
  where processing_job_id=selected_job.processing_job_id returning * into selected_job;
  return selected_job;
end
$function$;

create function platform_attachments.record_attachment_processing_result_v64(
  selected_processing_job_id uuid,
  selected_attachment_state text,
  selected_state_reason text
) returns void
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
declare selected_attachment_id uuid;
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_brain_worker','platform_brain_worker_preview')
     or (current_database()='agent_platform_control') <> (session_user='platform_brain_worker')
  then raise insufficient_privilege using message='Attachment processing caller invalid'; end if;
  if selected_attachment_state not in (
    'validating','scanning','ready','quarantined','rejected'
  ) then raise check_violation using message='Attachment processing result invalid'; end if;
  update platform_attachments.processing_jobs set
    state=case when selected_attachment_state in ('quarantined','rejected')
      then 'failed' else 'completed' end,
    state_reason=selected_state_reason,completed_at=now()
  where processing_job_id=selected_processing_job_id and state='running'
  returning attachment_id into selected_attachment_id;
  if not found then raise no_data_found using message='Processing job unavailable'; end if;
  update platform_attachments.attachments set
    state=selected_attachment_state,state_reason=selected_state_reason,
    ready_at=case when selected_attachment_state='ready' then now() else ready_at end
  where attachment_id=selected_attachment_id;
end
$function$;

create function platform_attachments.issue_task_grant_v64(
  selected_grant_id uuid,
  selected_token_sha256 bytea,
  selected_task_id uuid,
  selected_attachment_id uuid,
  selected_agent_id text,
  selected_scope text,
  selected_expires_at timestamptz,
  selected_max_reads integer,
  selected_max_bytes bigint
) returns uuid
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <> (session_user='platform_control_app')
  then raise insufficient_privilege using message='Attachment grant issuer invalid'; end if;
  if octet_length(selected_token_sha256) <> 32
     or selected_scope not in ('read','write_output')
     or selected_expires_at <= now() or selected_max_reads <= 0
     or selected_max_bytes <= 0
  then raise check_violation using message='Attachment grant invalid'; end if;
  if not exists (
    select 1 from platform_control.mission_tasks task
    where task.task_id=selected_task_id and task.agent_id=selected_agent_id
  ) or not exists (
    select 1 from platform_attachments.attachments attachment
    where attachment.attachment_id=selected_attachment_id
      and attachment.state='ready' and attachment.retained_until > now()
  ) then raise check_violation using message='Attachment grant target invalid'; end if;
  insert into platform_attachments.task_grants(
    grant_id,token_sha256,task_id,attachment_id,agent_id,scope,
    expires_at,max_reads,max_bytes
  ) values (
    selected_grant_id,selected_token_sha256,selected_task_id,
    selected_attachment_id,selected_agent_id,selected_scope,
    selected_expires_at,selected_max_reads,selected_max_bytes
  );
  return selected_grant_id;
end
$function$;

create function platform_attachments.consume_task_grant_v64(
  selected_token_sha256 bytea,
  selected_task_id uuid,
  selected_attachment_id uuid,
  selected_agent_id text,
  selected_scope text,
  selected_byte_count bigint
) returns uuid
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
declare selected_grant_id uuid;
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_brain_worker','platform_brain_worker_preview')
     or (current_database()='agent_platform_control') <> (session_user='platform_brain_worker')
  then raise insufficient_privilege using message='Attachment grant consumer invalid'; end if;
  if octet_length(selected_token_sha256) <> 32 or selected_byte_count < 0
  then raise check_violation using message='Attachment grant consumption invalid'; end if;
  update platform_attachments.task_grants grant_row set
    read_count=read_count+1,bytes_read=bytes_read+selected_byte_count
  from platform_attachments.attachments attachment
  where grant_row.token_sha256=selected_token_sha256
    and grant_row.task_id=selected_task_id
    and grant_row.attachment_id=selected_attachment_id
    and grant_row.agent_id=selected_agent_id and grant_row.scope=selected_scope
    and grant_row.revoked_at is null and grant_row.expires_at > now()
    and grant_row.read_count < grant_row.max_reads
    and grant_row.bytes_read+selected_byte_count <= grant_row.max_bytes
    and attachment.attachment_id=grant_row.attachment_id
    and attachment.state='ready' and attachment.retained_until > now()
  returning grant_row.grant_id into selected_grant_id;
  if not found then raise insufficient_privilege using message='Attachment grant unavailable'; end if;
  return selected_grant_id;
end
$function$;

create function platform_attachments.revoke_task_grant_v64(
  selected_grant_id uuid
) returns boolean
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <> (session_user='platform_control_app')
  then raise insufficient_privilege using message='Attachment grant revoker invalid'; end if;
  update platform_attachments.task_grants set revoked_at=now()
  where grant_id=selected_grant_id and revoked_at is null;
  return found;
end
$function$;

create function platform_attachments.bind_artifact_version_v64(
  selected_artifact_version_id uuid,
  selected_artifact_id uuid,
  selected_attachment_id uuid,
  selected_version_no integer
) returns uuid
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_brain_worker','platform_brain_worker_preview')
     or (current_database()='agent_platform_control') <> (session_user='platform_brain_worker')
  then raise insufficient_privilege using message='Artifact version caller invalid'; end if;
  if selected_version_no <= 0 then
    raise check_violation using message='Artifact version invalid';
  end if;
  insert into platform_attachments.artifact_versions(
    artifact_version_id,artifact_id,attachment_id,version_no,
    original_name_ciphertext,original_name_key_version,
    object_ref_ciphertext,object_ref_key_version,detected_mime,size_bytes,
    sha256,retained_until,state,state_reason,result_status
  ) select
    selected_artifact_version_id,selected_artifact_id,attachment.attachment_id,
    selected_version_no,attachment.original_name_ciphertext,
    attachment.original_name_key_version,attachment.object_ref_ciphertext,
    attachment.object_ref_key_version,attachment.detected_mime,
    attachment.size_bytes,attachment.sha256,attachment.retained_until,
    attachment.state,attachment.state_reason,
    case when attachment.state='ready' then 'succeeded' else 'pending' end
  from platform_attachments.attachments attachment
  join platform_attachments.artifacts artifact
    on artifact.artifact_id=selected_artifact_id
  where attachment.attachment_id=selected_attachment_id
    and attachment.owner_internal_user_id=artifact.owner_internal_user_id;
  if not found then raise check_violation using message='Artifact attachment invalid'; end if;
  return selected_artifact_version_id;
end
$function$;

create function platform_attachments.append_attachment_access_event_v64(
  selected_access_event_id uuid,
  selected_attachment_id uuid,
  selected_grant_id uuid,
  selected_actor_internal_user_id uuid,
  selected_task_id uuid,
  selected_agent_id text,
  selected_operation text,
  selected_result text,
  selected_byte_count bigint,
  selected_metadata_sha256 bytea
) returns uuid
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_audit_append','platform_audit_append_preview')
     or (current_database()='agent_platform_control') <> (session_user='platform_audit_append')
  then raise insufficient_privilege using message='Attachment audit caller invalid'; end if;
  insert into platform_attachments.access_events(
    access_event_id,attachment_id,grant_id,actor_internal_user_id,
    task_id,agent_id,operation,result,byte_count,metadata_sha256
  ) values (
    selected_access_event_id,selected_attachment_id,selected_grant_id,
    selected_actor_internal_user_id,selected_task_id,selected_agent_id,
    selected_operation,selected_result,selected_byte_count,selected_metadata_sha256
  );
  return selected_access_event_id;
end
$function$;

create function platform_attachments.upsert_conversation_read_state_v64(
  selected_owner_internal_user_id uuid,
  selected_conversation_id uuid,
  selected_last_read_message_seq integer
) returns integer
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <> (session_user='platform_control_app')
  then raise insufficient_privilege using message='Conversation read-state caller invalid'; end if;
  if selected_last_read_message_seq < 0 or not exists (
    select 1 from platform_control.conversations conversation
    where conversation.conversation_id=selected_conversation_id
      and conversation.owner_internal_user_id=selected_owner_internal_user_id
  ) then raise check_violation using message='Conversation read-state invalid'; end if;
  insert into platform_attachments.conversation_read_state(
    owner_internal_user_id,conversation_id,last_read_message_seq
  ) values (
    selected_owner_internal_user_id,selected_conversation_id,
    selected_last_read_message_seq
  ) on conflict (owner_internal_user_id,conversation_id) do update set
    last_read_message_seq=greatest(
      platform_attachments.conversation_read_state.last_read_message_seq,
      excluded.last_read_message_seq
    ),last_read_at=now();
  return selected_last_read_message_seq;
end
$function$;

create function platform_attachments.claim_attachment_erasure_job_v64(
  selected_worker_id text
) returns platform_attachments.erasure_jobs
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
declare selected_job platform_attachments.erasure_jobs%rowtype;
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_control_maintenance','platform_control_maintenance_preview')
     or (current_database()='agent_platform_control') <> (session_user='platform_control_maintenance')
  then raise insufficient_privilege using message='Attachment erasure caller invalid'; end if;
  if selected_worker_id is null or char_length(selected_worker_id) not between 1 and 128
  then raise check_violation using message='Erasure worker invalid'; end if;
  select * into selected_job from platform_attachments.erasure_jobs
  where state='queued' and available_at <= now()
  order by available_at,created_at for update skip locked limit 1;
  if not found then return null; end if;
  update platform_attachments.erasure_jobs set
    state='running',claimed_by=selected_worker_id,claimed_at=now(),
    attempt_count=attempt_count+1
  where erasure_job_id=selected_job.erasure_job_id returning * into selected_job;
  return selected_job;
end
$function$;

create function platform_attachments.record_attachment_erasure_result_v64(
  selected_erasure_job_id uuid,
  selected_state text,
  selected_state_reason text,
  selected_downstream_cleanup_status jsonb
) returns void
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
declare selected_attachment_id uuid;
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_control_maintenance','platform_control_maintenance_preview')
     or (current_database()='agent_platform_control') <> (session_user='platform_control_maintenance')
  then raise insufficient_privilege using message='Attachment erasure caller invalid'; end if;
  if selected_state not in ('completed','partial','failed')
     or jsonb_typeof(selected_downstream_cleanup_status) <> 'object'
  then raise check_violation using message='Attachment erasure result invalid'; end if;
  update platform_attachments.erasure_jobs set state=selected_state,
    state_reason=selected_state_reason,
    downstream_cleanup_status=selected_downstream_cleanup_status,
    completed_at=now()
  where erasure_job_id=selected_erasure_job_id and state='running'
  returning attachment_id into selected_attachment_id;
  if not found then raise no_data_found using message='Erasure job unavailable'; end if;
  if selected_state in ('completed','partial') then
    update platform_attachments.attachments set
      state='deleted',state_reason=selected_state_reason,deleted_at=now()
    where attachment_id=selected_attachment_id;
    update platform_attachments.task_grants set revoked_at=now()
    where attachment_id=selected_attachment_id and revoked_at is null;
  end if;
end
$function$;

revoke all on all tables in schema platform_attachments from public;
revoke all on all functions in schema platform_attachments from public;

do $migration$
declare
  selected_app name;
  selected_brain name;
  selected_maintenance name;
  selected_audit name;
  role_name name;
begin
  if current_database()='agent_platform_control'
     and current_user='platform_control_owner'
  then
    selected_app := 'platform_control_app';
    selected_brain := 'platform_brain_worker';
    selected_maintenance := 'platform_control_maintenance';
    selected_audit := 'platform_audit_append';
  elsif current_database()='agent_platform_control_preview'
        and current_user='platform_control_owner_preview'
  then
    selected_app := 'platform_control_app_preview';
    selected_brain := 'platform_brain_worker_preview';
    selected_maintenance := 'platform_control_maintenance_preview';
    selected_audit := 'platform_audit_append_preview';
  else
    raise insufficient_privilege using
      message='Attachment migration owner/environment mismatch';
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
    execute format('revoke all on schema platform_attachments from %I',role_name);
    execute format(
      'revoke all on all tables in schema platform_attachments from %I',role_name
    );
    execute format(
      'revoke all on all functions in schema platform_attachments from %I',role_name
    );
  end loop;

  execute format(
    'grant usage on schema platform_attachments to %I,%I,%I,%I',
    selected_app,selected_brain,selected_maintenance,selected_audit
  );
  execute format(
    'grant select on platform_attachments.attachments, '
    'platform_attachments.uploads,platform_attachments.bindings, '
    'platform_attachments.artifacts,platform_attachments.artifact_versions, '
    'platform_attachments.current_artifact_versions, '
    'platform_attachments.derivatives,platform_attachments.task_grants, '
    'platform_attachments.processing_jobs,platform_attachments.erasure_jobs, '
    'platform_attachments.message_citations, '
    'platform_attachments.conversation_read_state to %I',selected_app
  );
  execute format(
    'grant insert on platform_attachments.attachments, '
    'platform_attachments.uploads,platform_attachments.bindings, '
    'platform_attachments.artifacts,platform_attachments.erasure_jobs, '
    'platform_attachments.message_citations to %I',selected_app
  );
  execute format(
    'grant update (triage_status) on '
    'platform_control.conversation_feedback to %I',selected_app
  );
  execute format(
    'grant select on platform_attachments.attachments, '
    'platform_attachments.bindings,platform_attachments.artifacts, '
    'platform_attachments.artifact_versions, '
    'platform_attachments.current_artifact_versions, '
    'platform_attachments.derivatives,platform_attachments.task_grants, '
    'platform_attachments.processing_jobs to %I',selected_brain
  );
  execute format(
    'grant select on platform_attachments.attachments, '
    'platform_attachments.derivatives,platform_attachments.task_grants, '
    'platform_attachments.erasure_jobs to %I',selected_maintenance
  );

  execute format(
    'grant execute on function platform_attachments.finalize_upload_v64('
    'uuid,uuid,text,bigint,bytea), '
    'platform_attachments.issue_task_grant_v64('
    'uuid,bytea,uuid,uuid,text,text,timestamptz,integer,bigint), '
    'platform_attachments.revoke_task_grant_v64(uuid), '
    'platform_attachments.upsert_conversation_read_state_v64('
    'uuid,uuid,integer) to %I',selected_app
  );
  execute format(
    'grant execute on function '
    'platform_attachments.claim_attachment_processing_job_v64(text), '
    'platform_attachments.record_attachment_processing_result_v64('
    'uuid,text,text),platform_attachments.consume_task_grant_v64('
    'bytea,uuid,uuid,text,text,bigint), '
    'platform_attachments.bind_artifact_version_v64('
    'uuid,uuid,uuid,integer) to %I',selected_brain
  );
  execute format(
    'grant execute on function '
    'platform_attachments.append_attachment_access_event_v64('
    'uuid,uuid,uuid,uuid,uuid,text,text,text,bigint,bytea) to %I',
    selected_audit
  );
  execute format(
    'grant execute on function '
    'platform_attachments.claim_attachment_erasure_job_v64(text), '
    'platform_attachments.record_attachment_erasure_result_v64('
    'uuid,text,text,jsonb) to %I',selected_maintenance
  );
end
$migration$;
