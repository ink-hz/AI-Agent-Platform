create schema platform_attachments authorization current_user;
revoke all on schema platform_attachments from public;

create unique index conversation_owner_identity_v64
  on platform_control.conversations(conversation_id,owner_internal_user_id);

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
  immutable_locator text check (
    immutable_locator is null or (
      char_length(immutable_locator) between 9 and 1008
      and immutable_locator ~ '^(version|etag):[^[:space:][:cntrl:]]+$'
    )
  ),
  coverage_metadata jsonb check (
    coverage_metadata is null or (
      jsonb_typeof(coverage_metadata)='object'
      and octet_length(coverage_metadata::text) <= 1024
    )
  ),
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
  foreign key (conversation_id,owner_internal_user_id)
    references platform_control.conversations(
      conversation_id,owner_internal_user_id
    ),
  check ((state = 'ready') = (ready_at is not null) or state <> 'ready'),
  check ((state = 'deleted') = (deleted_at is not null) or state <> 'deleted'),
  unique (attachment_id,owner_internal_user_id),
  unique nulls not distinct (
    attachment_id,owner_internal_user_id,conversation_id
  )
);

create table platform_attachments.uploads (
  upload_id uuid primary key,
  attachment_id uuid not null unique
    references platform_attachments.attachments(attachment_id),
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  conversation_id uuid,
  object_ref_ciphertext bytea not null
    check (octet_length(object_ref_ciphertext) between 29 and 1048576),
  object_ref_key_version integer not null check (object_ref_key_version > 0),
  declared_mime text,
  detected_mime text,
  immutable_locator text check (
    immutable_locator is null or (
      char_length(immutable_locator) between 9 and 1008
      and immutable_locator ~ '^(version|etag):[^[:space:][:cntrl:]]+$'
    )
  ),
  coverage_metadata jsonb check (
    coverage_metadata is null or (
      jsonb_typeof(coverage_metadata)='object'
      and octet_length(coverage_metadata::text) <= 1024
    )
  ),
  size_bytes bigint not null default 0 check (size_bytes >= 0),
  sha256 bytea check (sha256 is null or octet_length(sha256) = 32),
  expected_sha256 bytea
    check (expected_sha256 is null or octet_length(expected_sha256) = 32),
  retained_until timestamptz not null
    default (now() + interval '365 days'),
  state text not null default 'uploading' check (state in (
    'uploading','validating','scanning','ready','quarantined','rejected','deleted'
  )),
  state_reason text,
  expires_at timestamptz not null default (now() + interval '24 hours'),
  created_at timestamptz not null default now(),
  finalized_at timestamptz,
  write_attempt_id uuid,
  write_lease_expires_at timestamptz,
  check ((write_attempt_id is null) = (write_lease_expires_at is null)),
  foreign key (attachment_id,owner_internal_user_id)
    references platform_attachments.attachments(
      attachment_id,owner_internal_user_id
    ),
  foreign key (attachment_id,owner_internal_user_id,conversation_id)
    references platform_attachments.attachments(
      attachment_id,owner_internal_user_id,conversation_id
    )
);

create table platform_attachments.upload_write_attempts (
  attempt_id uuid primary key,
  upload_id uuid not null
    references platform_attachments.uploads(upload_id) on delete cascade,
  attachment_id uuid not null
    references platform_attachments.attachments(attachment_id),
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  object_ref_ciphertext bytea not null
    check (octet_length(object_ref_ciphertext) between 29 and 1048576),
  object_ref_key_version integer not null check (object_ref_key_version > 0),
  lease_expires_at timestamptz not null,
  state text not null default 'claimed'
    check (state in ('claimed','canonical','superseded','abandoned','cleaned')),
  size_bytes bigint check (size_bytes is null or size_bytes >= 0),
  sha256 bytea check (sha256 is null or octet_length(sha256) = 32),
  created_at timestamptz not null default now(),
  finalized_at timestamptz,
  cleaned_at timestamptz,
  foreign key (attachment_id,owner_internal_user_id)
    references platform_attachments.attachments(
      attachment_id,owner_internal_user_id
    ),
  unique (upload_id,attempt_id)
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
  task_id uuid,
  agent_id text check (
    agent_id is null or agent_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
  ),
  created_at timestamptz not null default now(),
  foreign key (conversation_id,message_id)
    references platform_control.conversation_messages(conversation_id,message_id),
  foreign key (conversation_id,turn_id)
    references platform_control.conversation_turns(conversation_id,turn_id),
  foreign key (attachment_id,owner_internal_user_id)
    references platform_attachments.attachments(
      attachment_id,owner_internal_user_id
    ),
  foreign key (attachment_id,owner_internal_user_id,conversation_id)
    references platform_attachments.attachments(
      attachment_id,owner_internal_user_id,conversation_id
    ),
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
  artifact_key text not null
    check (artifact_key ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'),
  owner_internal_user_id uuid not null
    references platform_control.internal_users(internal_user_id),
  conversation_id uuid not null
    references platform_control.conversations(conversation_id),
  task_id uuid not null,
  agent_id text not null
    check (agent_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'),
  label_ciphertext bytea
    check (label_ciphertext is null or octet_length(label_ciphertext) between 29 and 1048576),
  label_key_version integer check (label_key_version is null or label_key_version > 0),
  created_at timestamptz not null default now(),
  foreign key (conversation_id,owner_internal_user_id)
    references platform_control.conversations(
      conversation_id,owner_internal_user_id
    ),
  check (num_nonnulls(label_ciphertext,label_key_version) in (0,2)),
  unique (task_id,artifact_key)
);

create table platform_attachments.artifact_versions (
  artifact_version_id uuid primary key,
  artifact_id uuid not null
    references platform_attachments.artifacts(artifact_id),
  attachment_id uuid not null unique
    references platform_attachments.attachments(attachment_id),
  version_no integer not null check (version_no > 0),
  producer_version_id text not null
    check (char_length(producer_version_id) between 1 and 160),
  original_name_ciphertext bytea not null
    check (octet_length(original_name_ciphertext) between 29 and 1048576),
  original_name_key_version integer not null check (original_name_key_version > 0),
  object_ref_ciphertext bytea not null
    check (octet_length(object_ref_ciphertext) between 29 and 1048576),
  object_ref_key_version integer not null check (object_ref_key_version > 0),
  detected_mime text,
  immutable_locator text check (
    immutable_locator is null or (
      char_length(immutable_locator) between 9 and 1008
      and immutable_locator ~ '^(version|etag):[^[:space:][:cntrl:]]+$'
    )
  ),
  coverage_metadata jsonb check (
    coverage_metadata is null or (
      jsonb_typeof(coverage_metadata)='object'
      and octet_length(coverage_metadata::text) <= 1024
    )
  ),
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
  unique (artifact_id,version_no),
  unique (artifact_id,producer_version_id)
);

create view platform_attachments.current_artifact_versions as
select ranked.*
from (
  select version.*,
    row_number() over (
      partition by version.artifact_id
      order by version.version_no desc,version.created_at desc
    ) as current_rank
  from platform_attachments.artifact_versions version
  join platform_attachments.attachments attachment
    on attachment.attachment_id=version.attachment_id
  where version.state = 'ready' and version.result_status = 'succeeded'
    and attachment.state = 'ready' and attachment.deleted_at is null
) ranked
where ranked.current_rank = 1;

create table platform_attachments.derivatives (
  derivative_id uuid primary key,
  attachment_id uuid not null
    references platform_attachments.attachments(attachment_id),
  kind text not null check (kind in ('thumbnail','preview','metadata','text','ocr')),
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
  task_id uuid not null,
  attachment_id uuid
    references platform_attachments.attachments(attachment_id),
  agent_id text not null
    check (agent_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'),
  scope text not null check (scope in ('read','write_output')),
  expires_at timestamptz not null,
  max_reads integer not null check (max_reads >= 0),
  read_count integer not null default 0
    check (read_count >= 0 and read_count <= max_reads),
  max_bytes bigint not null check (max_bytes > 0),
  bytes_read bigint not null default 0
    check (bytes_read >= 0 and bytes_read <= max_bytes),
  max_files integer not null default 0 check (max_files >= 0),
  file_count integer not null default 0
    check (file_count >= 0 and file_count <= max_files),
  max_file_bytes bigint not null default 0 check (max_file_bytes >= 0),
  created_at timestamptz not null default now(),
  revoked_at timestamptz,
  check (
    (scope='read' and attachment_id is not null and max_reads > 0
      and max_files=0 and file_count=0 and max_file_bytes=0)
    or (scope='write_output' and attachment_id is null
      and max_reads=0 and read_count=0 and max_files > 0
      and max_file_bytes > 0 and max_file_bytes <= max_bytes)
  ),
  unique (task_id,attachment_id,agent_id,scope,grant_id)
);

create table platform_attachments.access_events (
  access_event_id uuid primary key,
  attachment_id uuid not null
    references platform_attachments.attachments(attachment_id),
  grant_id uuid references platform_attachments.task_grants(grant_id),
  actor_internal_user_id uuid
    references platform_control.internal_users(internal_user_id),
  task_id uuid,
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
  derivative_kind text
    check (derivative_kind is null or derivative_kind in (
      'thumbnail','preview','metadata','text','ocr'
    )),
  state text not null default 'queued'
    check (state in ('queued','running','completed','failed')),
  state_reason text,
  attempt_count integer not null default 0 check (attempt_count >= 0),
  max_attempts integer not null default 3
    check (max_attempts between 1 and 10),
  available_at timestamptz not null default now(),
  claimed_by text,
  claimed_at timestamptz,
  attempt_token uuid not null default gen_random_uuid(),
  created_at timestamptz not null default now(),
  completed_at timestamptz,
  check (
    (job_kind='derive' and derivative_kind is not null)
    or (job_kind in ('validate','scan') and derivative_kind is null)
  )
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
  citation_key text not null
    check (citation_key ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'),
  url_ciphertext bytea not null
    check (octet_length(url_ciphertext) between 29 and 1048576),
  url_key_version integer not null check (url_key_version > 0),
  site_ciphertext bytea not null
    check (octet_length(site_ciphertext) between 29 and 1048576),
  site_key_version integer not null check (site_key_version > 0),
  title_ciphertext bytea
    check (title_ciphertext is null or octet_length(title_ciphertext) between 29 and 1048576),
  title_key_version integer check (title_key_version is null or title_key_version > 0),
  supported_claim_locations jsonb not null,
  retrieved_at timestamptz not null,
  created_at timestamptz not null default now(),
  foreign key (conversation_id,message_id)
    references platform_control.conversation_messages(conversation_id,message_id),
  check (num_nonnulls(title_ciphertext,title_key_version) in (0,2)),
  check (
    jsonb_typeof(supported_claim_locations)='array'
    and jsonb_array_length(supported_claim_locations) > 0
  ),
  unique (message_id,ordinal),
  unique (message_id,citation_key)
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
create index upload_write_attempts_cleanup_v64
  on platform_attachments.upload_write_attempts(state,lease_expires_at,created_at)
  where state in ('claimed','superseded','abandoned');
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
  on platform_attachments.task_grants(
    task_id,coalesce(attachment_id,'00000000-0000-0000-0000-000000000000'::uuid),
    agent_id,scope
  )
  where revoked_at is null;
create unique index one_active_processing_job_v64
  on platform_attachments.processing_jobs(
    attachment_id,job_kind,coalesce(derivative_kind,'')
  )
  where state in ('queued','running');
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
  add column triage_status text,
  add column triaged_by_internal_user_id uuid
    references platform_control.internal_users(internal_user_id),
  add column triaged_at timestamptz;

update platform_control.conversation_feedback
set triage_status='pending_triage'
where rating='unhelpful';

alter table platform_control.conversation_feedback
  add constraint conversation_feedback_triage_v64 check (
    (rating='helpful' and triage_status is null)
    or (rating='unhelpful' and triage_status in (
      'pending_triage','triaged','dismissed'
    ))
  ),
  add constraint conversation_feedback_triage_audit_v64 check (
    ((triage_status is null or triage_status='pending_triage')
      and triaged_by_internal_user_id is null and triaged_at is null)
    or (triage_status in ('triaged','dismissed')
      and triaged_by_internal_user_id is not null and triaged_at is not null)
  );

create function platform_control.default_conversation_feedback_triage_v64()
returns trigger
language plpgsql security definer
set search_path = pg_catalog, platform_control
as $function$
begin
  if new.rating='helpful' then
    new.triage_status := null;
  elsif new.triage_status is null then
    new.triage_status := 'pending_triage';
  end if;
  return new;
end
$function$;

create trigger default_conversation_feedback_triage_v64
before insert or update of rating,triage_status
on platform_control.conversation_feedback
for each row execute function
  platform_control.default_conversation_feedback_triage_v64();

create function platform_attachments.task_context_v64(
  selected_task_id uuid,
  selected_agent_id text
) returns table (
  task_status text,
  owner_internal_user_id uuid,
  conversation_id uuid
)
language sql security definer stable
set search_path = pg_catalog, platform_attachments
as $function$
  select task.status,mission.owner_internal_user_id,mission.conversation_id
  from platform_control.mission_tasks task
  join platform_control.missions mission on mission.mission_id=task.mission_id
  where task.task_id=selected_task_id and task.agent_id=selected_agent_id
  union all
  select task.status,conversation.owner_internal_user_id,loop.conversation_id
  from platform_brain.agent_tasks task
  join platform_brain.brain_loops loop on loop.loop_id=task.loop_id
  join platform_control.conversations conversation
    on conversation.conversation_id=loop.conversation_id
  where task.task_id=selected_task_id and task.agent_id=selected_agent_id
  limit 1
$function$;

create function platform_attachments.enforce_binding_task_context_v64()
returns trigger
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
begin
  if new.kind in ('task_input','task_output') and not exists (
    select 1
    from platform_attachments.task_context_v64(new.task_id,new.agent_id) task
    where task.owner_internal_user_id=new.owner_internal_user_id
      and task.conversation_id=new.conversation_id
  ) then
    raise foreign_key_violation using message='Attachment binding task context invalid';
  end if;
  return new;
end
$function$;

create trigger enforce_binding_task_context_v64
before insert or update on platform_attachments.bindings
for each row execute function
  platform_attachments.enforce_binding_task_context_v64();

create function platform_attachments.enforce_artifact_task_context_v64()
returns trigger
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
begin
  if not exists (
    select 1
    from platform_attachments.task_context_v64(new.task_id,new.agent_id) task
    where task.owner_internal_user_id=new.owner_internal_user_id
      and task.conversation_id=new.conversation_id
  ) then
    raise foreign_key_violation using message='Artifact task context invalid';
  end if;
  return new;
end
$function$;

create trigger enforce_artifact_task_context_v64
before insert or update on platform_attachments.artifacts
for each row execute function
  platform_attachments.enforce_artifact_task_context_v64();

create function platform_attachments.create_upload_v64(
  selected_upload_id uuid,
  selected_attachment_id uuid,
  selected_owner_internal_user_id uuid,
  selected_conversation_id uuid,
  selected_original_name_ciphertext bytea,
  selected_original_name_key_version integer,
  selected_object_ref_ciphertext bytea,
  selected_object_ref_key_version integer,
  selected_declared_mime text,
  selected_size_bytes bigint,
  selected_expires_at timestamptz,
  selected_max_file_bytes bigint,
  selected_max_conversation_files integer,
  selected_max_conversation_bytes bigint
) returns platform_attachments.uploads
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
declare
  selected_upload platform_attachments.uploads%rowtype;
  selected_file_count bigint;
  selected_total_bytes bigint;
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <> (session_user='platform_control_app')
  then raise insufficient_privilege using message='Attachment upload creator invalid'; end if;
  if selected_upload_id is null or selected_attachment_id is null
     or selected_owner_internal_user_id is null
     or selected_original_name_ciphertext is null
     or octet_length(selected_original_name_ciphertext) not between 29 and 1048576
     or selected_original_name_key_version is null
     or selected_original_name_key_version <= 0
     or selected_object_ref_ciphertext is null
     or octet_length(selected_object_ref_ciphertext) not between 29 and 1048576
     or selected_object_ref_key_version is null
     or selected_object_ref_key_version <= 0
     or selected_declared_mime is null
     or octet_length(selected_declared_mime) not between 1 and 255
     or selected_declared_mime <> btrim(selected_declared_mime)
     or selected_declared_mime ~ '[[:space:]]'
     or selected_declared_mime !~ '^[A-Za-z0-9!#$%&''*+.^_`|~-]+/[A-Za-z0-9!#$%&''*+.^_`|~-]+$'
     or selected_size_bytes is null or selected_size_bytes <= 0
     or selected_max_file_bytes is null
     or selected_size_bytes > selected_max_file_bytes
     or selected_max_file_bytes <= 0 or selected_max_file_bytes > 52428800
     or selected_max_conversation_files is null
     or selected_max_conversation_files <= 0 or selected_max_conversation_files > 50
     or selected_max_conversation_bytes is null
     or selected_max_conversation_bytes <= 0 or selected_max_conversation_bytes > 524288000
     or selected_expires_at is null or selected_expires_at <= now()
     or selected_expires_at > now() + interval '24 hours'
  then raise check_violation using message='Attachment upload reservation invalid'; end if;
  if selected_conversation_id is null then
    perform 1 from platform_control.internal_users
    where internal_user_id=selected_owner_internal_user_id and status='active'
    for key share;
  else
    perform 1 from platform_control.conversations
    where conversation_id=selected_conversation_id
      and owner_internal_user_id=selected_owner_internal_user_id
      and status='active'
    for update;
  end if;
  if not found then raise no_data_found using message='Attachment owner unavailable'; end if;
  if selected_conversation_id is not null then
    select count(*),coalesce(sum(attachment.size_bytes),0)
      into selected_file_count,selected_total_bytes
    from platform_attachments.attachments attachment
    left join platform_attachments.uploads upload
      on upload.attachment_id=attachment.attachment_id
    where attachment.owner_internal_user_id=selected_owner_internal_user_id
      and attachment.conversation_id=selected_conversation_id
      and attachment.source_kind='user_input'
      and attachment.state <> 'deleted'
      and not (attachment.state='uploading'
        and (upload.expires_at is null or upload.expires_at <= now()));
    if selected_file_count >= selected_max_conversation_files
    then raise program_limit_exceeded using message='Attachment Conversation files quota exceeded'; end if;
    if selected_total_bytes + selected_size_bytes > selected_max_conversation_bytes
    then raise program_limit_exceeded using message='Attachment Conversation bytes quota exceeded'; end if;
  end if;
  insert into platform_attachments.attachments(
    attachment_id,owner_internal_user_id,conversation_id,source_kind,
    original_name_ciphertext,original_name_key_version,
    object_ref_ciphertext,object_ref_key_version,declared_mime,size_bytes
  ) values (
    selected_attachment_id,selected_owner_internal_user_id,
    selected_conversation_id,'user_input',selected_original_name_ciphertext,
    selected_original_name_key_version,selected_object_ref_ciphertext,
    selected_object_ref_key_version,selected_declared_mime,selected_size_bytes
  );
  insert into platform_attachments.uploads(
    upload_id,attachment_id,owner_internal_user_id,conversation_id,
    object_ref_ciphertext,object_ref_key_version,declared_mime,size_bytes,
    expires_at
  ) values (
    selected_upload_id,selected_attachment_id,selected_owner_internal_user_id,
    selected_conversation_id,selected_object_ref_ciphertext,
    selected_object_ref_key_version,selected_declared_mime,selected_size_bytes,
    selected_expires_at
  ) returning * into selected_upload;
  return selected_upload;
end
$function$;

create function platform_attachments.claim_upload_write_v64(
  selected_upload_id uuid,
  selected_owner_internal_user_id uuid,
  selected_write_attempt_id uuid,
  selected_object_ref_ciphertext bytea,
  selected_object_ref_key_version integer,
  selected_write_lease_expires_at timestamptz
) returns uuid
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
declare
  selected_attachment_id uuid;
  selected_conversation_id uuid;
  previous_write_attempt_id uuid;
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <> (session_user='platform_control_app')
  then raise insufficient_privilege using message='Attachment upload writer invalid'; end if;
  if selected_write_attempt_id is null
     or octet_length(selected_object_ref_ciphertext) not between 29 and 1048576
     or selected_object_ref_key_version <= 0
     or selected_write_lease_expires_at <= now()
     or selected_write_lease_expires_at > now() + interval '5 minutes'
  then raise check_violation using message='Attachment upload write lease invalid'; end if;
  select upload.attachment_id,upload.conversation_id
    into selected_attachment_id,selected_conversation_id
  from platform_attachments.uploads upload
  where upload.upload_id=selected_upload_id
    and upload.owner_internal_user_id=selected_owner_internal_user_id;
  if not found then raise no_data_found using message='Upload write lease unavailable'; end if;
  perform 1 from platform_attachments.attachments attachment
  where attachment.attachment_id=selected_attachment_id
    and attachment.owner_internal_user_id=selected_owner_internal_user_id
    and attachment.conversation_id is not distinct from selected_conversation_id
    and attachment.state='uploading'
    and not exists (
      select 1 from platform_attachments.erasure_jobs erasure
      where erasure.attachment_id=attachment.attachment_id
    )
  for update;
  if not found then raise no_data_found using message='Upload write lease unavailable'; end if;
  select upload.write_attempt_id into previous_write_attempt_id
  from platform_attachments.uploads upload
  where upload.upload_id=selected_upload_id
    and upload.attachment_id=selected_attachment_id
    and upload.owner_internal_user_id=selected_owner_internal_user_id
    and upload.conversation_id is not distinct from selected_conversation_id
    and upload.state='uploading' and upload.expires_at > now()
    and not exists (
      select 1 from platform_attachments.erasure_jobs erasure
      where erasure.attachment_id=upload.attachment_id
    )
    and selected_write_lease_expires_at <= upload.expires_at
    and (upload.write_attempt_id is null or upload.write_lease_expires_at <= now())
  for update;
  if not found then raise no_data_found using message='Upload write lease unavailable'; end if;
  update platform_attachments.upload_write_attempts set state='superseded'
  where attempt_id=previous_write_attempt_id and state='claimed';
  insert into platform_attachments.upload_write_attempts(
    attempt_id,upload_id,attachment_id,owner_internal_user_id,
    object_ref_ciphertext,object_ref_key_version,lease_expires_at
  ) values (
    selected_write_attempt_id,selected_upload_id,selected_attachment_id,
    selected_owner_internal_user_id,selected_object_ref_ciphertext,
    selected_object_ref_key_version,selected_write_lease_expires_at
  );
  update platform_attachments.uploads set
    object_ref_ciphertext=selected_object_ref_ciphertext,
    object_ref_key_version=selected_object_ref_key_version,
    write_attempt_id=selected_write_attempt_id,
    write_lease_expires_at=selected_write_lease_expires_at
  where upload_id=selected_upload_id;
  update platform_attachments.attachments set
    object_ref_ciphertext=selected_object_ref_ciphertext,
    object_ref_key_version=selected_object_ref_key_version
  where attachment_id=selected_attachment_id;
  return selected_attachment_id;
end
$function$;

create function platform_attachments.abandon_upload_write_v64(
  selected_upload_id uuid,
  selected_owner_internal_user_id uuid,
  selected_write_attempt_id uuid
) returns uuid
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
declare selected_attachment_id uuid;
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <> (session_user='platform_control_app')
  then raise insufficient_privilege using message='Attachment upload abandoner invalid'; end if;
  if selected_upload_id is null or selected_owner_internal_user_id is null
     or selected_write_attempt_id is null
  then raise check_violation using message='Attachment upload abandonment invalid'; end if;
  select upload.attachment_id into selected_attachment_id
  from platform_attachments.uploads upload
  join platform_attachments.upload_write_attempts attempt
    on attempt.attempt_id=upload.write_attempt_id
   and attempt.upload_id=upload.upload_id
   and attempt.attachment_id=upload.attachment_id
   and attempt.owner_internal_user_id=upload.owner_internal_user_id
   and attempt.object_ref_ciphertext=upload.object_ref_ciphertext
   and attempt.object_ref_key_version=upload.object_ref_key_version
   and attempt.state='claimed'
  where upload.upload_id=selected_upload_id
    and upload.owner_internal_user_id=selected_owner_internal_user_id
    and upload.write_attempt_id=selected_write_attempt_id
    and upload.state='uploading'
  for update of upload,attempt;
  if not found then raise no_data_found using message='Upload abandonment unavailable'; end if;
  update platform_attachments.upload_write_attempts set state='abandoned'
  where attempt_id=selected_write_attempt_id and state='claimed';
  update platform_attachments.uploads set
    write_attempt_id=null,write_lease_expires_at=null
  where upload_id=selected_upload_id
    and write_attempt_id=selected_write_attempt_id;
  return selected_attachment_id;
end
$function$;

create function platform_attachments.cancel_upload_v64(
  selected_upload_id uuid,
  selected_owner_internal_user_id uuid,
  selected_expected_conversation_id uuid,
  selected_erasure_job_id uuid,
  selected_reason_ciphertext bytea,
  selected_reason_key_version integer,
  selected_reason_sha256 bytea
) returns uuid
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
declare
  selected_attachment_id uuid;
  selected_conversation_id uuid;
  selected_upload platform_attachments.uploads%rowtype;
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <> (session_user='platform_control_app')
  then raise insufficient_privilege using message='Attachment upload canceller invalid'; end if;
  if selected_upload_id is null or selected_owner_internal_user_id is null
     or selected_erasure_job_id is null
     or selected_reason_ciphertext is null
     or octet_length(selected_reason_ciphertext) not between 29 and 1048576
     or selected_reason_key_version is null or selected_reason_key_version <= 0
     or selected_reason_sha256 is null
     or octet_length(selected_reason_sha256) <> 32
  then raise check_violation using message='Attachment upload cancellation invalid'; end if;
  select upload.attachment_id into selected_attachment_id
  from platform_attachments.uploads upload
  where upload.upload_id=selected_upload_id
    and upload.owner_internal_user_id=selected_owner_internal_user_id;
  if not found then raise no_data_found using message='Attachment upload unavailable'; end if;
  select attachment.conversation_id into selected_conversation_id
  from platform_attachments.attachments attachment
  where attachment.attachment_id=selected_attachment_id
    and attachment.owner_internal_user_id=selected_owner_internal_user_id
  for update;
  if not found then raise no_data_found using message='Attachment upload unavailable'; end if;
  if selected_conversation_id is distinct from selected_expected_conversation_id then
    raise serialization_failure using message='Attachment changed during cancellation';
  end if;
  select upload.* into selected_upload
  from platform_attachments.uploads upload
  where upload.upload_id=selected_upload_id
    and upload.attachment_id=selected_attachment_id
    and upload.owner_internal_user_id=selected_owner_internal_user_id
    and upload.conversation_id is not distinct from selected_conversation_id
  for update;
  if not found then raise no_data_found using message='Attachment upload unavailable'; end if;
  update platform_attachments.uploads set expires_at=least(expires_at,now())
  where upload_id=selected_upload.upload_id;
  if selected_upload.state='uploading'
     and selected_upload.write_attempt_id is not null then
    update platform_attachments.upload_write_attempts set state='abandoned'
    where attempt_id=selected_upload.write_attempt_id
      and upload_id=selected_upload.upload_id
      and attachment_id=selected_upload.attachment_id
      and owner_internal_user_id=selected_upload.owner_internal_user_id
      and state='claimed';
    if found then
      update platform_attachments.uploads set
        write_attempt_id=null,write_lease_expires_at=null
      where upload_id=selected_upload.upload_id
        and write_attempt_id=selected_upload.write_attempt_id;
    end if;
  end if;
  if exists (
    select 1 from platform_attachments.erasure_jobs erasure
    where erasure.attachment_id=selected_upload.attachment_id
  ) then return selected_upload.attachment_id; end if;
  insert into platform_attachments.erasure_jobs(
    erasure_job_id,attachment_id,requested_by_internal_user_id,
    reason_ciphertext,reason_key_version,reason_sha256
  ) values (
    selected_erasure_job_id,selected_upload.attachment_id,
    selected_owner_internal_user_id,selected_reason_ciphertext,
    selected_reason_key_version,selected_reason_sha256
  ) on conflict (attachment_id) where state in ('queued','running')
    do nothing;
  return selected_upload.attachment_id;
end
$function$;

create function platform_attachments.request_attachment_erasure_v64(
  selected_attachment_id uuid,
  selected_owner_internal_user_id uuid,
  selected_expected_conversation_id uuid,
  selected_erasure_job_id uuid,
  selected_reason_ciphertext bytea,
  selected_reason_key_version integer,
  selected_reason_sha256 bytea
) returns uuid
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
declare selected_conversation_id uuid;
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <>
        (session_user='platform_control_app')
  then raise insufficient_privilege using message='Attachment erasure requester invalid'; end if;
  if selected_attachment_id is null
     or selected_owner_internal_user_id is null
     or selected_erasure_job_id is null
     or selected_reason_ciphertext is null
     or octet_length(selected_reason_ciphertext) not between 29 and 1048576
     or selected_reason_key_version is null or selected_reason_key_version <= 0
     or selected_reason_sha256 is null
     or octet_length(selected_reason_sha256) <> 32
  then raise check_violation using message='Attachment erasure request invalid'; end if;
  select attachment.conversation_id into selected_conversation_id
  from platform_attachments.attachments attachment
  where attachment.attachment_id=selected_attachment_id
    and attachment.owner_internal_user_id=selected_owner_internal_user_id
  for update;
  if not found then
    raise no_data_found using message='Attachment unavailable';
  end if;
  if selected_conversation_id is distinct from selected_expected_conversation_id then
    raise serialization_failure using message='Attachment changed during erasure request';
  end if;
  if exists (
    select 1 from platform_attachments.erasure_jobs erasure
    where erasure.attachment_id=selected_attachment_id
  ) then return selected_attachment_id; end if;
  insert into platform_attachments.erasure_jobs(
    erasure_job_id,attachment_id,requested_by_internal_user_id,
    reason_ciphertext,reason_key_version,reason_sha256
  ) values (
    selected_erasure_job_id,selected_attachment_id,
    selected_owner_internal_user_id,selected_reason_ciphertext,
    selected_reason_key_version,selected_reason_sha256
  ) on conflict (attachment_id) where state in ('queued','running')
    do nothing;
  return selected_attachment_id;
end
$function$;

create function platform_attachments.finalize_upload_v64(
  selected_upload_id uuid,
  selected_owner_internal_user_id uuid,
  selected_write_attempt_id uuid,
  selected_declared_mime text,
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
  if selected_upload_id is null or selected_owner_internal_user_id is null
     or selected_write_attempt_id is null or selected_declared_mime is null
     or selected_size_bytes is null or selected_size_bytes < 0
     or selected_size_bytes > 52428800
     or selected_sha256 is null or octet_length(selected_sha256) <> 32
  then raise check_violation using message='Attachment upload result invalid'; end if;
  select upload.attachment_id into selected_attachment_id
  from platform_attachments.uploads upload
  join platform_attachments.upload_write_attempts attempt
    on attempt.attempt_id=upload.write_attempt_id
   and attempt.upload_id=upload.upload_id
   and attempt.attachment_id=upload.attachment_id
   and attempt.owner_internal_user_id=upload.owner_internal_user_id
   and attempt.object_ref_ciphertext=upload.object_ref_ciphertext
   and attempt.object_ref_key_version=upload.object_ref_key_version
   and attempt.state='claimed'
  join platform_attachments.attachments attachment
    on attachment.attachment_id=upload.attachment_id
   and attachment.owner_internal_user_id=upload.owner_internal_user_id
   and attachment.conversation_id is not distinct from upload.conversation_id
   and attachment.object_ref_ciphertext=upload.object_ref_ciphertext
   and attachment.object_ref_key_version=upload.object_ref_key_version
   and attachment.declared_mime=upload.declared_mime
   and attachment.size_bytes=upload.size_bytes
  where upload.upload_id=selected_upload_id
    and upload.owner_internal_user_id=selected_owner_internal_user_id
    and upload.write_attempt_id=selected_write_attempt_id
    and upload.declared_mime=selected_declared_mime
    and upload.size_bytes=selected_size_bytes
    and upload.state='uploading' and upload.expires_at > now()
    and not exists (
      select 1 from platform_attachments.erasure_jobs erasure
      where erasure.attachment_id=upload.attachment_id
    )
    and attachment.state='uploading'
  for update;
  if not found then raise no_data_found using message='Upload unavailable'; end if;
  update platform_attachments.upload_write_attempts set
    state='canonical',size_bytes=selected_size_bytes,sha256=selected_sha256,
    finalized_at=now()
  where attempt_id=selected_write_attempt_id;
  update platform_attachments.uploads set
    size_bytes=selected_size_bytes,sha256=selected_sha256,
    state='validating',finalized_at=now()
  where upload_id=selected_upload_id;
  update platform_attachments.attachments set
    size_bytes=selected_size_bytes,sha256=selected_sha256,state='validating'
  where attachment_id=selected_attachment_id;
  insert into platform_attachments.processing_jobs(
    processing_job_id,attachment_id,job_kind
  ) values (gen_random_uuid(),selected_attachment_id,'validate');
  return selected_attachment_id;
end
$function$;

create function platform_attachments.acknowledge_upload_write_cleanup_v64(
  selected_write_attempt_id uuid
) returns uuid
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
declare
  selected_attempt_state text;
  selected_upload_state text;
  selected_upload_expires_at timestamptz;
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <> (session_user='platform_control_app')
  then raise insufficient_privilege using message='Attachment cleanup acknowledger invalid'; end if;
  if selected_write_attempt_id is null
  then raise check_violation using message='Attachment cleanup acknowledgement invalid'; end if;
  select attempt.state,upload.state,upload.expires_at
    into selected_attempt_state,selected_upload_state,selected_upload_expires_at
  from platform_attachments.upload_write_attempts attempt
  join platform_attachments.uploads upload on upload.upload_id=attempt.upload_id
  where attempt.attempt_id=selected_write_attempt_id
  for update of attempt,upload;
  if not found then raise no_data_found using message='Attachment cleanup unavailable'; end if;
  if selected_attempt_state='cleaned' then return selected_write_attempt_id; end if;
  if selected_attempt_state not in ('superseded','abandoned')
     and not (selected_attempt_state='claimed'
       and selected_upload_state='uploading'
       and selected_upload_expires_at <= now())
  then raise no_data_found using message='Attachment cleanup unavailable'; end if;
  update platform_attachments.upload_write_attempts set
    state='cleaned',cleaned_at=now()
  where attempt_id=selected_write_attempt_id;
  return selected_write_attempt_id;
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
    attempt_count=attempt_count+1,attempt_token=gen_random_uuid()
  where processing_job_id=selected_job.processing_job_id returning * into selected_job;
  return selected_job;
end
$function$;

create function platform_attachments.record_attachment_processing_result_v64(
  selected_processing_job_id uuid,
  selected_attempt_token uuid,
  selected_attachment_state text,
  selected_state_reason text,
  selected_detected_mime text default null,
  selected_coverage_metadata jsonb default null,
  selected_immutable_locator text default null
) returns void
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
declare
  selected_job platform_attachments.processing_jobs%rowtype;
  selected_current_state text;
  selected_current_detected_mime text;
  next_job_kind text;
  next_derivative_kind text;
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_brain_worker','platform_brain_worker_preview')
     or (current_database()='agent_platform_control') <> (session_user='platform_brain_worker')
  then raise insufficient_privilege using message='Attachment processing caller invalid'; end if;
  select * into selected_job from platform_attachments.processing_jobs
  where processing_job_id=selected_processing_job_id and state='running'
    and attempt_token=selected_attempt_token
  for update;
  if not found then raise no_data_found using message='Processing job unavailable'; end if;
  select state,detected_mime
    into selected_current_state,selected_current_detected_mime
  from platform_attachments.attachments
  where attachment_id=selected_job.attachment_id
  for update;
  if not found or selected_current_state='deleted' then
    raise check_violation using message='Attachment deleted or unavailable';
  end if;
  if (selected_job.job_kind='validate' and selected_current_state <> 'validating')
     or (selected_job.job_kind='scan' and selected_current_state <> 'scanning')
     or (selected_job.job_kind='derive' and selected_current_state <> 'ready')
  then raise check_violation using message='Attachment processing predecessor invalid'; end if;

  if selected_job.job_kind='validate' and selected_attachment_state='scanning' then
    if selected_detected_mime is null
       or octet_length(selected_detected_mime) not between 3 and 255
       or selected_detected_mime <> btrim(selected_detected_mime)
       or selected_detected_mime ~ '[[:space:]]'
       or selected_detected_mime !~
         '^[A-Za-z0-9!#$%&''*+.^_`|~-]+/[A-Za-z0-9!#$%&''*+.^_`|~-]+$'
       or selected_coverage_metadata is null
       or jsonb_typeof(selected_coverage_metadata) <> 'object'
       or octet_length(selected_coverage_metadata::text) > 1024
       or selected_immutable_locator is null
       or char_length(selected_immutable_locator) not between 9 and 1008
       or selected_immutable_locator !~
         '^(version|etag):[^[:space:][:cntrl:]]+$'
    then raise check_violation using message='Attachment validation result invalid'; end if;
    if not selected_coverage_metadata ?&
         array['coverage','download','inline_preview']
       or (select count(*) from jsonb_object_keys(selected_coverage_metadata)) <> 3
       or selected_coverage_metadata->>'coverage' not in (
         'safe_thumbnail','first_page','metadata_only'
       )
       or jsonb_typeof(selected_coverage_metadata->'download') <> 'boolean'
       or selected_coverage_metadata->>'download' <> 'true'
       or jsonb_typeof(selected_coverage_metadata->'inline_preview') <> 'boolean'
       or (
         (selected_coverage_metadata->>'coverage'='metadata_only') <>
         (selected_coverage_metadata->>'inline_preview'='false')
       )
       or not (
         (selected_detected_mime in ('image/png','image/jpeg')
           and selected_coverage_metadata->>'coverage'='safe_thumbnail')
         or (selected_detected_mime='application/pdf'
           and selected_coverage_metadata->>'coverage'='first_page')
         or (selected_detected_mime in (
             'text/plain',
             'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
             'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
             'application/vnd.openxmlformats-officedocument.presentationml.presentation'
           ) and selected_coverage_metadata->>'coverage'='metadata_only')
       )
    then raise check_violation using message='Attachment validation result invalid'; end if;
    update platform_attachments.attachments set
      detected_mime=selected_detected_mime,
      coverage_metadata=selected_coverage_metadata,
      immutable_locator=selected_immutable_locator
    where attachment_id=selected_job.attachment_id;
    update platform_attachments.uploads set
      detected_mime=selected_detected_mime,
      coverage_metadata=selected_coverage_metadata,
      immutable_locator=selected_immutable_locator
    where attachment_id=selected_job.attachment_id;
  elsif selected_detected_mime is not null or selected_coverage_metadata is not null
     or selected_immutable_locator is not null then
    raise check_violation using message='Attachment processing metadata invalid';
  end if;

  if selected_attachment_state='retry' then
    if selected_job.attempt_count < selected_job.max_attempts then
      update platform_attachments.processing_jobs set
        state='queued',state_reason=selected_state_reason,
        available_at=now() + make_interval(
          secs => least(300,power(2,selected_job.attempt_count-1)::integer)
        ),
        claimed_by=null,claimed_at=null,completed_at=null
      where processing_job_id=selected_processing_job_id;
    else
      update platform_attachments.processing_jobs set
        state='failed',state_reason=selected_state_reason,completed_at=now()
      where processing_job_id=selected_processing_job_id;
      if selected_job.job_kind <> 'derive' then
        update platform_attachments.attachments set
          state='rejected',state_reason='processing_retries_exhausted'
        where attachment_id=selected_job.attachment_id;
        update platform_attachments.uploads set
          state='rejected',state_reason='processing_retries_exhausted'
        where attachment_id=selected_job.attachment_id;
        update platform_attachments.artifact_versions set
          state='rejected',state_reason='processing_retries_exhausted',
          result_status='failed'
        where attachment_id=selected_job.attachment_id
          and result_status='pending';
      end if;
    end if;
    return;
  end if;

  if (selected_job.job_kind='validate' and selected_attachment_state='scanning') then
    next_job_kind := 'scan';
  elsif (selected_job.job_kind='scan' and selected_attachment_state='ready') then
    next_job_kind := 'derive';
    next_derivative_kind := case
      when selected_current_detected_mime in ('image/png','image/jpeg') then 'thumbnail'
      when selected_current_detected_mime='application/pdf' then 'preview'
      else 'metadata'
    end;
  elsif not (
    (selected_job.job_kind in ('validate','scan')
      and selected_attachment_state in ('quarantined','rejected'))
    or (selected_job.job_kind='derive'
      and selected_attachment_state='rejected'
      and selected_state_reason='integrity_mismatch')
  ) then
    raise check_violation using message='Attachment processing transition invalid';
  end if;

  update platform_attachments.processing_jobs set
    state=case when selected_attachment_state in ('quarantined','rejected')
      then 'failed' else 'completed' end,
    state_reason=selected_state_reason,completed_at=now()
  where processing_job_id=selected_processing_job_id;
  update platform_attachments.attachments set
    state=selected_attachment_state,state_reason=selected_state_reason,
    ready_at=case when selected_attachment_state='ready' then now() else ready_at end
  where attachment_id=selected_job.attachment_id;
  update platform_attachments.uploads set
    state=selected_attachment_state,state_reason=selected_state_reason
  where attachment_id=selected_job.attachment_id;
  if selected_attachment_state in ('ready','quarantined','rejected') then
    update platform_attachments.artifact_versions version set
      original_name_ciphertext=attachment.original_name_ciphertext,
      original_name_key_version=attachment.original_name_key_version,
      object_ref_ciphertext=attachment.object_ref_ciphertext,
      object_ref_key_version=attachment.object_ref_key_version,
      detected_mime=attachment.detected_mime,
      coverage_metadata=attachment.coverage_metadata,
      immutable_locator=attachment.immutable_locator,
      size_bytes=attachment.size_bytes,
      sha256=attachment.sha256,
      retained_until=attachment.retained_until,
      state=selected_attachment_state,
      state_reason=selected_state_reason,
      result_status=case when selected_attachment_state='ready'
        then 'succeeded' else 'failed' end
    from platform_attachments.attachments attachment
    where version.attachment_id=selected_job.attachment_id
      and attachment.attachment_id=version.attachment_id
      and version.result_status='pending';
  end if;
  if next_job_kind is not null then
    insert into platform_attachments.processing_jobs(
      processing_job_id,attachment_id,job_kind,derivative_kind
    ) values (
      gen_random_uuid(),selected_job.attachment_id,next_job_kind,
      next_derivative_kind
    );
  end if;
end
$function$;

create function platform_attachments.record_attachment_derivative_v64(
  selected_processing_job_id uuid,
  selected_attempt_token uuid,
  selected_derivative_id uuid,
  selected_kind text,
  selected_object_ref_ciphertext bytea,
  selected_object_ref_key_version integer,
  selected_detected_mime text,
  selected_size_bytes bigint,
  selected_sha256 bytea,
  selected_state_reason text
) returns uuid
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
declare
  selected_job platform_attachments.processing_jobs%rowtype;
  selected_current_state text;
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_brain_worker','platform_brain_worker_preview')
     or (current_database()='agent_platform_control') <> (session_user='platform_brain_worker')
  then raise insufficient_privilege using message='Attachment derivative caller invalid'; end if;
  select * into selected_job from platform_attachments.processing_jobs
  where processing_job_id=selected_processing_job_id and state='running'
    and attempt_token=selected_attempt_token
    and job_kind='derive' and derivative_kind=selected_kind
  for update;
  if not found then raise no_data_found using message='Derivative job unavailable'; end if;
  select state into selected_current_state
  from platform_attachments.attachments
  where attachment_id=selected_job.attachment_id
  for update;
  if not found or selected_current_state <> 'ready' then
    raise check_violation using message='Derivative attachment unavailable';
  end if;
  if octet_length(selected_object_ref_ciphertext) < 29
     or selected_object_ref_key_version <= 0
     or octet_length(selected_sha256) <> 32
     or not (
       (selected_kind in ('thumbnail','preview')
         and selected_detected_mime='image/png'
         and selected_size_bytes between 1 and 10485760)
       or (selected_kind='metadata'
         and selected_detected_mime='application/json'
         and selected_size_bytes between 1 and 1024)
     )
  then raise check_violation using message='Attachment derivative invalid'; end if;
  insert into platform_attachments.derivatives(
    derivative_id,attachment_id,kind,object_ref_ciphertext,
    object_ref_key_version,detected_mime,size_bytes,sha256,
    retained_until,state,state_reason
  ) select selected_derivative_id,attachment.attachment_id,selected_kind,
    selected_object_ref_ciphertext,selected_object_ref_key_version,
    selected_detected_mime,selected_size_bytes,selected_sha256,
    attachment.retained_until,'ready',selected_state_reason
  from platform_attachments.attachments attachment
  where attachment.attachment_id=selected_job.attachment_id
    and attachment.state='ready';
  if not found then raise check_violation using message='Derivative attachment unavailable'; end if;
  update platform_attachments.processing_jobs set
    state='completed',state_reason=selected_state_reason,completed_at=now()
  where processing_job_id=selected_processing_job_id;
  return selected_derivative_id;
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
  selected_max_bytes bigint,
  selected_max_files integer default 0,
  selected_max_file_bytes bigint default 0
) returns uuid
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
declare
  selected_task_status text;
  selected_owner_internal_user_id uuid;
  selected_conversation_id uuid;
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in (
       'platform_control_app','platform_control_app_preview',
       'platform_brain_worker','platform_brain_worker_preview'
     )
     or (current_database()='agent_platform_control') <>
       (session_user in ('platform_control_app','platform_brain_worker'))
  then raise insufficient_privilege using message='Attachment grant issuer invalid'; end if;
  if octet_length(selected_token_sha256) <> 32
     or selected_scope not in ('read','write_output')
     or selected_expires_at <= now() or selected_max_bytes <= 0
     or (selected_scope='read' and (
       selected_attachment_id is null or selected_max_reads <= 0
       or selected_max_files <> 0 or selected_max_file_bytes <> 0
     )) or (selected_scope='write_output' and (
       selected_attachment_id is not null or selected_max_reads <> 0
       or selected_max_files <= 0 or selected_max_file_bytes <= 0
       or selected_max_file_bytes > selected_max_bytes
       or selected_max_files > 20
       or selected_max_bytes > 262144000
       or selected_max_file_bytes > 52428800
     ))
  then raise check_violation using message='Attachment grant invalid'; end if;
  select task.task_status,task.owner_internal_user_id,task.conversation_id
    into selected_task_status,selected_owner_internal_user_id,
         selected_conversation_id
  from platform_attachments.task_context_v64(
    selected_task_id,selected_agent_id
  ) task;
  if not found or selected_task_status not in (
    'queued','dispatched','running','waiting_input','waiting_confirmation'
  ) then
    raise check_violation using message='Attachment grant requires active task';
  end if;
  if selected_scope='read' then
    insert into platform_attachments.bindings(
      binding_id,attachment_id,owner_internal_user_id,kind,
      conversation_id,task_id,agent_id
    )
    select gen_random_uuid(),turn_binding.attachment_id,
      selected_owner_internal_user_id,'task_input',selected_conversation_id,
      selected_task_id,selected_agent_id
    from platform_attachments.bindings turn_binding
    join platform_attachments.attachments attachment
      on attachment.attachment_id=turn_binding.attachment_id
    where turn_binding.attachment_id=selected_attachment_id
      and turn_binding.owner_internal_user_id=selected_owner_internal_user_id
      and turn_binding.conversation_id=selected_conversation_id
      and turn_binding.kind='turn_input'
      and attachment.state='ready' and attachment.retained_until>now()
      and attachment.deleted_at is null
    on conflict do nothing;
  end if;
  if selected_scope='read' and not exists (
    select 1
    from platform_attachments.attachments attachment
    join platform_attachments.bindings binding
      on binding.attachment_id=attachment.attachment_id
     and binding.owner_internal_user_id=attachment.owner_internal_user_id
     and binding.conversation_id=attachment.conversation_id
    where attachment.attachment_id=selected_attachment_id
      and attachment.owner_internal_user_id=selected_owner_internal_user_id
      and attachment.conversation_id=selected_conversation_id
      and attachment.state='ready' and attachment.retained_until > now()
      and binding.kind='task_input' and binding.task_id=selected_task_id
      and binding.agent_id=selected_agent_id
  ) then raise check_violation using message='Attachment task_input binding invalid'; end if;
  update platform_attachments.task_grants set revoked_at=now()
  where task_id=selected_task_id and agent_id=selected_agent_id
    and scope=selected_scope
    and attachment_id is not distinct from selected_attachment_id
    and revoked_at is null and expires_at <= now();
  insert into platform_attachments.task_grants(
    grant_id,token_sha256,task_id,attachment_id,agent_id,scope,
    expires_at,max_reads,max_bytes,max_files,max_file_bytes
  ) values (
    selected_grant_id,selected_token_sha256,selected_task_id,
    selected_attachment_id,selected_agent_id,selected_scope,
    selected_expires_at,selected_max_reads,selected_max_bytes,
    selected_max_files,selected_max_file_bytes
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
  if not exists (
    select 1
    from platform_attachments.task_context_v64(
      selected_task_id,selected_agent_id
    ) task
    where task.task_status in (
      'queued','dispatched','running','waiting_input','waiting_confirmation'
    )
  ) then
    raise insufficient_privilege using message='Attachment task terminal';
  end if;
  update platform_attachments.task_grants grant_row set
    read_count=read_count+1,bytes_read=bytes_read+selected_byte_count
  from platform_attachments.attachments attachment
  where grant_row.token_sha256=selected_token_sha256
    and grant_row.task_id=selected_task_id
    and grant_row.attachment_id=selected_attachment_id
    and grant_row.agent_id=selected_agent_id and grant_row.scope='read'
    and selected_scope='read'
    and grant_row.revoked_at is null and grant_row.expires_at > now()
    and grant_row.read_count < grant_row.max_reads
    and grant_row.bytes_read+selected_byte_count <= grant_row.max_bytes
    and attachment.attachment_id=grant_row.attachment_id
    and attachment.state='ready' and attachment.retained_until > now()
    and exists (
      select 1 from platform_attachments.bindings binding
      where binding.attachment_id=grant_row.attachment_id
        and binding.kind='task_input' and binding.task_id=grant_row.task_id
        and binding.agent_id=grant_row.agent_id
    )
  returning grant_row.grant_id into selected_grant_id;
  if not found then raise insufficient_privilege using message='Attachment grant unavailable'; end if;
  return selected_grant_id;
end
$function$;

create function platform_attachments.consume_output_write_grant_v64(
  selected_token_sha256 bytea,
  selected_task_id uuid,
  selected_agent_id text,
  selected_file_size_bytes bigint
) returns uuid
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
declare selected_grant_id uuid;
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_brain_worker','platform_brain_worker_preview')
     or (current_database()='agent_platform_control') <> (session_user='platform_brain_worker')
  then raise insufficient_privilege using message='Output grant consumer invalid'; end if;
  if octet_length(selected_token_sha256) <> 32 or selected_file_size_bytes < 0
  then raise check_violation using message='Output grant consumption invalid'; end if;
  if not exists (
    select 1
    from platform_attachments.task_context_v64(
      selected_task_id,selected_agent_id
    ) task
    where task.task_status in (
      'queued','dispatched','running','waiting_input','waiting_confirmation'
    )
  ) then raise insufficient_privilege using message='Output task terminal'; end if;
  update platform_attachments.task_grants set
    file_count=file_count+1,bytes_read=bytes_read+selected_file_size_bytes
  where token_sha256=selected_token_sha256 and task_id=selected_task_id
    and attachment_id is null and agent_id=selected_agent_id
    and scope='write_output' and revoked_at is null and expires_at > now()
    and max_files <= 20 and max_bytes <= 262144000
    and max_file_bytes <= 52428800
    and file_count < max_files and file_count < 20
    and selected_file_size_bytes <= max_file_bytes
    and selected_file_size_bytes <= 52428800
    and bytes_read+selected_file_size_bytes <= max_bytes
    and bytes_read+selected_file_size_bytes <= 262144000
  returning grant_id into selected_grant_id;
  if not found then raise insufficient_privilege using message='Output grant unavailable'; end if;
  return selected_grant_id;
end
$function$;

create function platform_attachments.consume_task_grant_gateway_v64(
  selected_token_sha256 bytea,
  selected_attachment_id uuid,
  selected_byte_count bigint
) returns uuid
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
declare selected_grant_id uuid;
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <> (session_user='platform_control_app')
  then raise insufficient_privilege using message='Attachment gateway caller invalid'; end if;
  if selected_token_sha256 is null
     or octet_length(selected_token_sha256) <> 32
     or selected_attachment_id is null
     or selected_byte_count is null or selected_byte_count <= 0
  then raise check_violation using message='Attachment gateway request invalid'; end if;
  if exists (
    select 1
    from platform_attachments.task_grants grant_row
    cross join lateral platform_attachments.task_context_v64(
      grant_row.task_id,grant_row.agent_id
    ) task
    where grant_row.token_sha256=selected_token_sha256
      and grant_row.attachment_id=selected_attachment_id
      and grant_row.scope='read'
      and task.task_status not in (
        'queued','dispatched','running','waiting_input','waiting_confirmation'
      )
  ) then
    raise insufficient_privilege using message='Attachment task terminal';
  end if;
  update platform_attachments.task_grants grant_row set
    read_count=grant_row.read_count+1,
    bytes_read=grant_row.bytes_read+selected_byte_count
  from platform_attachments.attachments attachment
  where grant_row.token_sha256=selected_token_sha256
    and grant_row.attachment_id=selected_attachment_id
    and grant_row.scope='read'
    and grant_row.revoked_at is null and grant_row.expires_at > now()
    and grant_row.read_count < grant_row.max_reads
    and grant_row.bytes_read+selected_byte_count <= grant_row.max_bytes
    and attachment.attachment_id=grant_row.attachment_id
    and attachment.state='ready' and attachment.retained_until > now()
    and selected_byte_count=attachment.size_bytes
    and exists (
      select 1 from platform_attachments.task_context_v64(
        grant_row.task_id,grant_row.agent_id
      ) task
      where task.task_status in (
        'queued','dispatched','running','waiting_input','waiting_confirmation'
      )
        and task.owner_internal_user_id=attachment.owner_internal_user_id
        and task.conversation_id=attachment.conversation_id
    )
    and exists (
      select 1 from platform_attachments.bindings binding
      where binding.attachment_id=attachment.attachment_id
        and binding.owner_internal_user_id=attachment.owner_internal_user_id
        and binding.conversation_id=attachment.conversation_id
        and binding.kind='task_input'
        and binding.task_id=grant_row.task_id
        and binding.agent_id=grant_row.agent_id
    )
  returning grant_row.grant_id into selected_grant_id;
  if not found then
    raise insufficient_privilege using message='Attachment grant unavailable';
  end if;
  return selected_grant_id;
end
$function$;

create function platform_attachments.create_artifact_upload_v64(
  selected_token_sha256 bytea,
  selected_task_id uuid,
  selected_agent_id text,
  selected_upload_id uuid,
  selected_attachment_id uuid,
  selected_artifact_id uuid,
  selected_artifact_version_id uuid,
  selected_artifact_key text,
  selected_producer_version_id text,
  selected_original_name_ciphertext bytea,
  selected_original_name_key_version integer,
  selected_object_ref_ciphertext bytea,
  selected_object_ref_key_version integer,
  selected_declared_mime text,
  selected_size_bytes bigint,
  selected_expected_sha256 bytea,
  selected_expires_at timestamptz
) returns table(
  upload_id uuid,
  attachment_id uuid,
  artifact_id uuid,
  artifact_version_id uuid,
  version_no integer,
  replayed boolean
)
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
declare
  selected_owner_internal_user_id uuid;
  selected_conversation_id uuid;
  selected_grant_id uuid;
  existing_artifact_id uuid;
  existing_upload_id uuid;
  existing_attachment_id uuid;
  existing_artifact_version_id uuid;
  existing_version_no integer;
  existing_agent_id text;
  existing_declared_mime text;
  existing_size_bytes bigint;
  existing_expected_sha256 bytea;
  next_version_no integer;
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <> (session_user='platform_control_app')
  then raise insufficient_privilege using message='Artifact upload creator invalid'; end if;
  if selected_token_sha256 is null or octet_length(selected_token_sha256) <> 32
     or selected_task_id is null or selected_agent_id is null
     or selected_agent_id !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
     or selected_upload_id is null or selected_attachment_id is null
     or selected_artifact_id is null or selected_artifact_version_id is null
     or selected_artifact_key is null
     or selected_artifact_key !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
     or selected_producer_version_id is null
     or octet_length(selected_producer_version_id) not between 1 and 160
     or selected_producer_version_id ~ '[[:cntrl:]]'
     or selected_original_name_ciphertext is null
     or octet_length(selected_original_name_ciphertext) not between 29 and 1048576
     or selected_original_name_key_version is null
     or selected_original_name_key_version <= 0
     or selected_object_ref_ciphertext is null
     or octet_length(selected_object_ref_ciphertext) not between 29 and 1048576
     or selected_object_ref_key_version is null
     or selected_object_ref_key_version <= 0
     or selected_declared_mime is null
     or octet_length(selected_declared_mime) not between 1 and 255
     or selected_declared_mime <> btrim(selected_declared_mime)
     or selected_declared_mime ~ '[[:space:]]'
     or selected_declared_mime !~ '^[A-Za-z0-9!#$%&''*+.^_`|~-]+/[A-Za-z0-9!#$%&''*+.^_`|~-]+$'
     or selected_size_bytes is null or selected_size_bytes <= 0
     or selected_size_bytes > 52428800
     or selected_expected_sha256 is null
     or octet_length(selected_expected_sha256) <> 32
     or selected_expires_at is null or selected_expires_at <= now()
     or selected_expires_at > now() + interval '24 hours'
  then raise check_violation using message='Artifact upload reservation invalid'; end if;

  select task.owner_internal_user_id,task.conversation_id
    into selected_owner_internal_user_id,selected_conversation_id
  from platform_attachments.task_context_v64(
    selected_task_id,selected_agent_id
  ) task
  where task.task_status in (
    'queued','dispatched','running','waiting_input','waiting_confirmation'
  );
  if not found then
    raise insufficient_privilege using message='Artifact task unavailable';
  end if;

  select grant_row.grant_id into selected_grant_id
  from platform_attachments.task_grants grant_row
  where grant_row.token_sha256=selected_token_sha256
    and grant_row.task_id=selected_task_id
    and grant_row.attachment_id is null
    and grant_row.agent_id=selected_agent_id
    and grant_row.scope='write_output'
    and grant_row.revoked_at is null and grant_row.expires_at > now()
    and grant_row.max_files <= 20 and grant_row.max_bytes <= 262144000
    and grant_row.max_file_bytes <= 52428800
  for update;
  if not found then
    raise insufficient_privilege using message='Artifact output grant unavailable';
  end if;

  select artifact.artifact_id into existing_artifact_id
  from platform_attachments.artifacts artifact
  where artifact.task_id=selected_task_id
    and artifact.artifact_key=selected_artifact_key
  for update;

  if found then
    select upload.upload_id,attachment.attachment_id,
           version.artifact_version_id,version.version_no,
           artifact.agent_id,upload.declared_mime,upload.size_bytes,
           upload.expected_sha256
      into existing_upload_id,existing_attachment_id,
           existing_artifact_version_id,existing_version_no,
           existing_agent_id,existing_declared_mime,existing_size_bytes,
           existing_expected_sha256
    from platform_attachments.artifacts artifact
    join platform_attachments.artifact_versions version
      on version.artifact_id=artifact.artifact_id
    join platform_attachments.attachments attachment
      on attachment.attachment_id=version.attachment_id
    join platform_attachments.uploads upload
      on upload.attachment_id=attachment.attachment_id
    where artifact.artifact_id=existing_artifact_id
      and version.producer_version_id=selected_producer_version_id;
    if found then
      if existing_agent_id <> selected_agent_id
         or existing_declared_mime <> selected_declared_mime
         or existing_size_bytes <> selected_size_bytes
         or existing_expected_sha256 <> selected_expected_sha256
      then raise unique_violation using message='Artifact upload replay conflict'; end if;
      return query select existing_upload_id,existing_attachment_id,
        existing_artifact_id,existing_artifact_version_id,
        existing_version_no,true;
      return;
    end if;
  end if;

  update platform_attachments.task_grants grant_row set
    file_count=grant_row.file_count+1,
    bytes_read=grant_row.bytes_read+selected_size_bytes
  where grant_row.grant_id=selected_grant_id
    and grant_row.token_sha256=selected_token_sha256
    and grant_row.revoked_at is null and grant_row.expires_at > now()
    and grant_row.file_count < grant_row.max_files
    and grant_row.file_count < 20
    and selected_size_bytes <= grant_row.max_file_bytes
    and selected_size_bytes <= 52428800
    and grant_row.bytes_read+selected_size_bytes <= grant_row.max_bytes
    and grant_row.bytes_read+selected_size_bytes <= 262144000
  returning grant_row.grant_id into selected_grant_id;
  if not found then
    raise insufficient_privilege using message='Artifact output grant unavailable';
  end if;

  if existing_artifact_id is null then
    insert into platform_attachments.artifacts(
      artifact_id,artifact_key,owner_internal_user_id,conversation_id,
      task_id,agent_id
    ) values (
      selected_artifact_id,selected_artifact_key,
      selected_owner_internal_user_id,selected_conversation_id,
      selected_task_id,selected_agent_id
    );
    existing_artifact_id := selected_artifact_id;
    next_version_no := 1;
  else
    select coalesce(max(version.version_no),0)+1 into next_version_no
    from platform_attachments.artifact_versions version
    where version.artifact_id=existing_artifact_id;
  end if;

  insert into platform_attachments.attachments(
    attachment_id,owner_internal_user_id,conversation_id,source_kind,
    original_name_ciphertext,original_name_key_version,
    object_ref_ciphertext,object_ref_key_version,declared_mime,size_bytes,state
  ) values (
    selected_attachment_id,selected_owner_internal_user_id,
    selected_conversation_id,'agent_output',
    selected_original_name_ciphertext,selected_original_name_key_version,
    selected_object_ref_ciphertext,selected_object_ref_key_version,
    selected_declared_mime,selected_size_bytes,'uploading'
  );
  insert into platform_attachments.uploads(
    upload_id,attachment_id,owner_internal_user_id,conversation_id,
    object_ref_ciphertext,object_ref_key_version,declared_mime,size_bytes,
    expected_sha256,expires_at,state
  ) values (
    selected_upload_id,selected_attachment_id,
    selected_owner_internal_user_id,selected_conversation_id,
    selected_object_ref_ciphertext,selected_object_ref_key_version,
    selected_declared_mime,selected_size_bytes,selected_expected_sha256,
    selected_expires_at,'uploading'
  );
  insert into platform_attachments.bindings(
    binding_id,attachment_id,owner_internal_user_id,kind,
    conversation_id,task_id,agent_id
  ) values (
    gen_random_uuid(),selected_attachment_id,selected_owner_internal_user_id,
    'task_output',selected_conversation_id,selected_task_id,selected_agent_id
  );
  insert into platform_attachments.artifact_versions(
    artifact_version_id,artifact_id,attachment_id,version_no,
    producer_version_id,original_name_ciphertext,original_name_key_version,
    object_ref_ciphertext,object_ref_key_version,size_bytes,state,result_status
  ) values (
    selected_artifact_version_id,existing_artifact_id,selected_attachment_id,
    next_version_no,selected_producer_version_id,
    selected_original_name_ciphertext,selected_original_name_key_version,
    selected_object_ref_ciphertext,selected_object_ref_key_version,
    selected_size_bytes,'uploading','pending'
  );
  return query select selected_upload_id,selected_attachment_id,
    existing_artifact_id,selected_artifact_version_id,next_version_no,false;
end
$function$;

create function platform_attachments.claim_artifact_upload_write_v64(
  selected_token_sha256 bytea,
  selected_upload_id uuid,
  selected_write_attempt_id uuid,
  selected_object_ref_ciphertext bytea,
  selected_object_ref_key_version integer,
  selected_write_lease_expires_at timestamptz
) returns uuid
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
declare
  selected_attachment_id uuid;
  selected_owner_internal_user_id uuid;
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <> (session_user='platform_control_app')
  then raise insufficient_privilege using message='Artifact upload writer invalid'; end if;
  select attachment.attachment_id,attachment.owner_internal_user_id
    into selected_attachment_id,selected_owner_internal_user_id
  from platform_attachments.uploads upload
  join platform_attachments.attachments attachment
    on attachment.attachment_id=upload.attachment_id
  join platform_attachments.artifact_versions version
    on version.attachment_id=attachment.attachment_id
  join platform_attachments.artifacts artifact
    on artifact.artifact_id=version.artifact_id
  join platform_attachments.task_grants grant_row
    on grant_row.task_id=artifact.task_id and grant_row.agent_id=artifact.agent_id
  cross join lateral platform_attachments.task_context_v64(
    artifact.task_id,artifact.agent_id
  ) task
  where upload.upload_id=selected_upload_id
    and grant_row.token_sha256=selected_token_sha256
    and grant_row.scope='write_output'
    and grant_row.revoked_at is null and grant_row.expires_at > now()
    and task.task_status in (
      'queued','dispatched','running','waiting_input','waiting_confirmation'
    )
    and attachment.source_kind='agent_output';
  if not found then
    raise insufficient_privilege using message='Artifact upload unavailable';
  end if;
  perform platform_attachments.claim_upload_write_v64(
    selected_upload_id,selected_owner_internal_user_id,
    selected_write_attempt_id,selected_object_ref_ciphertext,
    selected_object_ref_key_version,selected_write_lease_expires_at
  );
  return selected_attachment_id;
end
$function$;

create function platform_attachments.abandon_artifact_upload_write_v64(
  selected_token_sha256 bytea,
  selected_upload_id uuid,
  selected_write_attempt_id uuid
) returns uuid
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
declare
  selected_attachment_id uuid;
  selected_owner_internal_user_id uuid;
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <> (session_user='platform_control_app')
  then raise insufficient_privilege using message='Artifact upload abandoner invalid'; end if;
  select attachment.attachment_id,attachment.owner_internal_user_id
    into selected_attachment_id,selected_owner_internal_user_id
  from platform_attachments.uploads upload
  join platform_attachments.attachments attachment
    on attachment.attachment_id=upload.attachment_id
  join platform_attachments.artifact_versions version
    on version.attachment_id=attachment.attachment_id
  join platform_attachments.artifacts artifact
    on artifact.artifact_id=version.artifact_id
  join platform_attachments.task_grants grant_row
    on grant_row.task_id=artifact.task_id and grant_row.agent_id=artifact.agent_id
  where upload.upload_id=selected_upload_id
    and grant_row.token_sha256=selected_token_sha256
    and grant_row.scope='write_output';
  if not found then
    raise insufficient_privilege using message='Artifact upload unavailable';
  end if;
  perform platform_attachments.abandon_upload_write_v64(
    selected_upload_id,selected_owner_internal_user_id,selected_write_attempt_id
  );
  return selected_attachment_id;
end
$function$;

create function platform_attachments.finalize_artifact_upload_v64(
  selected_token_sha256 bytea,
  selected_upload_id uuid,
  selected_write_attempt_id uuid,
  selected_declared_mime text,
  selected_size_bytes bigint,
  selected_sha256 bytea
) returns uuid
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
declare
  selected_attachment_id uuid;
  selected_owner_internal_user_id uuid;
  expected_sha256 bytea;
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <> (session_user='platform_control_app')
  then raise insufficient_privilege using message='Artifact upload finalizer invalid'; end if;
  select attachment.attachment_id,attachment.owner_internal_user_id,
         upload.expected_sha256
    into selected_attachment_id,selected_owner_internal_user_id,expected_sha256
  from platform_attachments.uploads upload
  join platform_attachments.attachments attachment
    on attachment.attachment_id=upload.attachment_id
  join platform_attachments.artifact_versions version
    on version.attachment_id=attachment.attachment_id
  join platform_attachments.artifacts artifact
    on artifact.artifact_id=version.artifact_id
  join platform_attachments.task_grants grant_row
    on grant_row.task_id=artifact.task_id and grant_row.agent_id=artifact.agent_id
  cross join lateral platform_attachments.task_context_v64(
    artifact.task_id,artifact.agent_id
  ) task
  where upload.upload_id=selected_upload_id
    and grant_row.token_sha256=selected_token_sha256
    and grant_row.scope='write_output'
    and grant_row.revoked_at is null and grant_row.expires_at > now()
    and task.task_status in (
      'queued','dispatched','running','waiting_input','waiting_confirmation'
    )
    and attachment.source_kind='agent_output';
  if not found then
    raise insufficient_privilege using message='Artifact upload unavailable';
  end if;
  if selected_sha256 is null or octet_length(selected_sha256) <> 32
     or expected_sha256 is null or expected_sha256 <> selected_sha256
  then raise check_violation using message='Artifact upload digest mismatch'; end if;
  perform platform_attachments.finalize_upload_v64(
    selected_upload_id,selected_owner_internal_user_id,
    selected_write_attempt_id,selected_declared_mime,
    selected_size_bytes,selected_sha256
  );
  update platform_attachments.artifact_versions version set state='validating'
  where version.attachment_id=selected_attachment_id
    and version.state='uploading' and version.result_status='pending';
  if not found then
    raise check_violation using message='Artifact version unavailable';
  end if;
  return selected_attachment_id;
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

create function platform_attachments.revoke_terminal_task_grants_v64()
returns trigger
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
begin
  if old.terminal_at is null and new.terminal_at is not null then
    update platform_attachments.task_grants set revoked_at=clock_timestamp()
    where task_id=new.task_id and revoked_at is null;
  end if;
  return new;
end
$function$;

create trigger revoke_terminal_mission_task_grants_v64
after update of terminal_at on platform_control.mission_tasks
for each row execute function
  platform_attachments.revoke_terminal_task_grants_v64();

create trigger revoke_terminal_brain_task_grants_v64
after update of terminal_at on platform_brain.agent_tasks
for each row execute function
  platform_attachments.revoke_terminal_task_grants_v64();

create function platform_attachments.bind_artifact_version_v64(
  selected_artifact_version_id uuid,
  selected_artifact_id uuid,
  selected_attachment_id uuid,
  selected_version_no integer,
  selected_producer_version_id text
) returns uuid
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
declare
  selected_artifact platform_attachments.artifacts%rowtype;
  selected_attachment platform_attachments.attachments%rowtype;
  existing_version platform_attachments.artifact_versions%rowtype;
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_brain_worker','platform_brain_worker_preview')
     or (current_database()='agent_platform_control') <> (session_user='platform_brain_worker')
  then raise insufficient_privilege using message='Artifact version caller invalid'; end if;
  if selected_version_no <= 0
     or char_length(selected_producer_version_id) not between 1 and 160 then
    raise check_violation using message='Artifact version invalid';
  end if;

  select * into selected_artifact
  from platform_attachments.artifacts
  where artifact_id=selected_artifact_id
  for update;
  if not found then raise check_violation using message='Artifact invalid'; end if;

  select * into existing_version
  from platform_attachments.artifact_versions
  where artifact_id=selected_artifact_id
    and producer_version_id=selected_producer_version_id;
  if found then
    if existing_version.artifact_version_id=selected_artifact_version_id
       and existing_version.attachment_id=selected_attachment_id
       and existing_version.version_no=selected_version_no then
      return existing_version.artifact_version_id;
    end if;
    raise check_violation using message='Artifact version replay conflict';
  end if;

  select * into selected_attachment
  from platform_attachments.attachments
  where attachment_id=selected_attachment_id
  for update;
  if not found
     or selected_attachment.owner_internal_user_id <>
       selected_artifact.owner_internal_user_id
     or selected_attachment.conversation_id <> selected_artifact.conversation_id
  then raise check_violation using message='Artifact attachment invalid'; end if;
  if selected_attachment.source_kind <> 'agent_output' then
    raise check_violation using message='Artifact attachment must be agent_output';
  end if;
  if selected_attachment.state not in ('uploading','validating','scanning','ready') then
    raise check_violation using message='Artifact attachment terminal';
  end if;
  if not exists (
    select 1 from platform_attachments.bindings binding
    where binding.attachment_id=selected_attachment_id
      and binding.kind='task_output'
      and binding.owner_internal_user_id=selected_artifact.owner_internal_user_id
      and binding.conversation_id=selected_artifact.conversation_id
      and binding.task_id=selected_artifact.task_id
      and binding.agent_id=selected_artifact.agent_id
  ) then
    raise check_violation using message='Artifact task_output binding invalid';
  end if;

  insert into platform_attachments.artifact_versions(
    artifact_version_id,artifact_id,attachment_id,version_no,
    producer_version_id,
    original_name_ciphertext,original_name_key_version,
    object_ref_ciphertext,object_ref_key_version,detected_mime,immutable_locator,
    coverage_metadata,size_bytes,sha256,retained_until,state,state_reason,
    result_status
  ) values (
    selected_artifact_version_id,selected_artifact_id,selected_attachment.attachment_id,
    selected_version_no,selected_producer_version_id,
    selected_attachment.original_name_ciphertext,
    selected_attachment.original_name_key_version,
    selected_attachment.object_ref_ciphertext,
    selected_attachment.object_ref_key_version,selected_attachment.detected_mime,
    selected_attachment.immutable_locator,
    selected_attachment.coverage_metadata,selected_attachment.size_bytes,
    selected_attachment.sha256,
    selected_attachment.retained_until,selected_attachment.state,
    selected_attachment.state_reason,
    case when selected_attachment.state='ready' then 'succeeded' else 'pending' end
  );
  return selected_artifact_version_id;
end
$function$;

create function platform_attachments.fail_artifact_version_v64(
  selected_artifact_version_id uuid,
  selected_failure_state text,
  selected_state_reason text
) returns uuid
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
declare
  selected_attachment_id uuid;
  selected_result_status text;
  selected_current_state text;
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_brain_worker','platform_brain_worker_preview')
     or (current_database()='agent_platform_control') <> (session_user='platform_brain_worker')
  then raise insufficient_privilege using message='Artifact failure caller invalid'; end if;
  if selected_failure_state not in ('quarantined','rejected')
     or selected_state_reason is null or char_length(selected_state_reason) > 512
  then raise check_violation using message='Artifact failure invalid'; end if;

  select attachment_id into selected_attachment_id
  from platform_attachments.artifact_versions
  where artifact_version_id=selected_artifact_version_id;
  if not found then raise no_data_found using message='Artifact version unavailable'; end if;
  perform 1 from platform_attachments.processing_jobs
  where attachment_id=selected_attachment_id and state in ('queued','running')
  order by processing_job_id
  for update;
  select state into selected_current_state
  from platform_attachments.attachments
  where attachment_id=selected_attachment_id
  for update;
  if not found or selected_current_state='deleted' then
    raise check_violation using message='Artifact attachment deleted or unavailable';
  end if;
  select result_status into selected_result_status
  from platform_attachments.artifact_versions
  where artifact_version_id=selected_artifact_version_id
  for update;
  if selected_result_status='failed' then return selected_artifact_version_id; end if;
  if selected_result_status <> 'pending' then
    raise check_violation using message='Artifact version already succeeded';
  end if;

  update platform_attachments.attachments set
    state=selected_failure_state,state_reason=selected_state_reason
  where attachment_id=selected_attachment_id;
  update platform_attachments.uploads set
    state=selected_failure_state,state_reason=selected_state_reason
  where attachment_id=selected_attachment_id;
  update platform_attachments.processing_jobs set
    state='failed',state_reason=selected_state_reason,completed_at=now()
  where attachment_id=selected_attachment_id
    and state in ('queued','running');
  update platform_attachments.artifact_versions set
    state=selected_failure_state,state_reason=selected_state_reason,
    result_status='failed'
  where artifact_version_id=selected_artifact_version_id;
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

create function platform_attachments.authorize_review_attachment_access_v64(
  selected_actor_internal_user_id uuid,
  selected_attachment_id uuid,
  selected_operation text
) returns uuid
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
declare selected_owner_internal_user_id uuid;
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <> (session_user='platform_control_app')
  then raise insufficient_privilege using message='Review attachment caller invalid'; end if;
  perform platform_control.require_platform_owner(selected_actor_internal_user_id);
  if selected_operation not in ('preview','download') then
    raise check_violation using message='Review attachment operation invalid';
  end if;
  select attachment.owner_internal_user_id into selected_owner_internal_user_id
  from platform_attachments.attachments attachment
  where attachment.attachment_id=selected_attachment_id
    and attachment.state='ready'
    and attachment.retained_until>now()
    and attachment.immutable_locator is not null
    and (
      selected_operation='download'
      or attachment.detected_mime not in (
        'image/png','image/jpeg','application/pdf'
      )
      or exists (
        select 1 from platform_attachments.derivatives derivative
        where derivative.attachment_id=attachment.attachment_id
          and derivative.state='ready'
          and derivative.kind in ('thumbnail','preview')
      )
    )
    and not exists (
      select 1 from platform_attachments.erasure_jobs erasure
      where erasure.attachment_id=attachment.attachment_id
    );
  if selected_owner_internal_user_id is null then
    raise no_data_found using message='Review attachment unavailable';
  end if;
  insert into platform_attachments.access_events(
    access_event_id,attachment_id,actor_internal_user_id,
    operation,result,byte_count
  ) values (
    gen_random_uuid(),selected_attachment_id,selected_actor_internal_user_id,
    selected_operation,'allowed',0
  );
  return selected_owner_internal_user_id;
end
$function$;

create function platform_control.triage_conversation_feedback_v64(
  selected_actor_internal_user_id uuid,
  selected_feedback_id uuid,
  selected_triage_status text
) returns uuid
language plpgsql security definer
set search_path = pg_catalog, platform_control
as $function$
declare selected_result uuid;
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <> (session_user='platform_control_app')
  then raise insufficient_privilege using message='Conversation feedback triage caller invalid'; end if;
  perform platform_control.require_platform_owner(selected_actor_internal_user_id);
  if selected_triage_status not in ('triaged','dismissed') then
    raise check_violation using message='Conversation feedback triage invalid';
  end if;
  update platform_control.conversation_feedback set
    triage_status=selected_triage_status,
    triaged_by_internal_user_id=selected_actor_internal_user_id,
    triaged_at=now()
  where feedback_id=selected_feedback_id and rating='unhelpful'
  returning feedback_id into selected_result;
  if selected_result is null then
    raise no_data_found using message='Conversation feedback unavailable';
  end if;
  return selected_result;
end
$function$;

revoke all on function platform_control.triage_conversation_feedback_v64(
  uuid,uuid,text
) from public;

create function platform_attachments.upsert_conversation_read_state_v64(
  selected_owner_internal_user_id uuid,
  selected_conversation_id uuid,
  selected_last_read_message_seq integer
) returns integer
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
declare persisted_last_read_message_seq integer;
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
    ),last_read_at=now()
  returning last_read_message_seq into persisted_last_read_message_seq;
  return persisted_last_read_message_seq;
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
  select attachment_id into selected_attachment_id
  from platform_attachments.erasure_jobs
  where erasure_job_id=selected_erasure_job_id and state='running'
  for update;
  if not found then raise no_data_found using message='Erasure job unavailable'; end if;
  perform 1 from platform_attachments.processing_jobs
  where attachment_id=selected_attachment_id and state in ('queued','running')
  order by processing_job_id
  for update;
  perform 1 from platform_attachments.attachments
  where attachment_id=selected_attachment_id
  for update;
  if not found then raise no_data_found using message='Erasure attachment unavailable'; end if;
  update platform_attachments.erasure_jobs set state=selected_state,
    state_reason=selected_state_reason,
    downstream_cleanup_status=selected_downstream_cleanup_status,
    completed_at=now()
  where erasure_job_id=selected_erasure_job_id;
  if selected_state in ('completed','partial') then
    update platform_attachments.attachments set
      state='deleted',state_reason=selected_state_reason,deleted_at=now()
    where attachment_id=selected_attachment_id;
    update platform_attachments.uploads set
      state='deleted',state_reason=selected_state_reason
    where attachment_id=selected_attachment_id;
    update platform_attachments.task_grants set revoked_at=now()
    where attachment_id=selected_attachment_id and revoked_at is null;
    update platform_attachments.processing_jobs set
      state='failed',state_reason='attachment_erased',completed_at=now()
    where attachment_id=selected_attachment_id
      and state in ('queued','running');
    update platform_attachments.artifact_versions set
      state='deleted',state_reason=selected_state_reason,
      result_status=case when result_status='pending'
        then 'failed' else result_status end
    where attachment_id=selected_attachment_id;
    update platform_attachments.derivatives set
      state='deleted',state_reason=selected_state_reason
    where attachment_id=selected_attachment_id and state <> 'deleted';
  end if;
end
$function$;

create function platform_attachments.claim_conversation_attachment_v64(
  selected_attachment_id uuid,
  selected_owner_internal_user_id uuid,
  selected_conversation_id uuid
) returns uuid
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <>
        (session_user='platform_control_app')
  then
    raise insufficient_privilege using
      message='Conversation attachment claimant invalid';
  end if;
  if selected_attachment_id is null
     or selected_owner_internal_user_id is null
     or selected_conversation_id is null
  then
    raise check_violation using
      message='Conversation attachment claim invalid';
  end if;
  perform 1 from platform_control.conversations
  where conversation_id=selected_conversation_id
    and owner_internal_user_id=selected_owner_internal_user_id
    and status='active'
  for update;
  if not found then
    raise no_data_found using message='Conversation attachment unavailable';
  end if;
  perform 1 from platform_attachments.attachments attachment
  where attachment.attachment_id=selected_attachment_id
    and attachment.owner_internal_user_id=selected_owner_internal_user_id
    and attachment.source_kind='user_input'
    and attachment.conversation_id is null
    and attachment.state='ready'
    and attachment.ready_at is not null
    and attachment.retained_until>now()
    and attachment.immutable_locator is not null
    and not exists (
      select 1 from platform_attachments.erasure_jobs erasure
      where erasure.attachment_id=attachment.attachment_id
    )
  for update;
  if found then
    update platform_attachments.attachments
    set conversation_id=selected_conversation_id
    where attachment_id=selected_attachment_id and conversation_id is null;
    update platform_attachments.uploads
    set conversation_id=selected_conversation_id
    where attachment_id=selected_attachment_id and conversation_id is null;
    return selected_attachment_id;
  end if;
  perform 1 from platform_attachments.attachments attachment
  where attachment.attachment_id=selected_attachment_id
    and attachment.owner_internal_user_id=selected_owner_internal_user_id
    and attachment.source_kind='user_input'
    and attachment.conversation_id=selected_conversation_id
    and attachment.state='ready'
    and attachment.ready_at is not null
    and attachment.retained_until>now()
    and attachment.immutable_locator is not null
    and not exists (
      select 1 from platform_attachments.erasure_jobs erasure
      where erasure.attachment_id=attachment.attachment_id
    )
  for update;
  if not found then
    raise no_data_found using message='Conversation attachment unavailable';
  end if;
  return selected_attachment_id;
end
$function$;

create function platform_attachments.bind_conversation_turn_v64(
  selected_owner_internal_user_id uuid,
  selected_conversation_id uuid,
  selected_message_id uuid,
  selected_turn_id uuid,
  selected_attachment_ids uuid[],
  selected_active_attachment_ids uuid[],
  selected_agent_id text,
  selected_agent_supports_attachments boolean,
  selected_max_message_files integer,
  selected_max_message_bytes bigint,
  selected_max_conversation_files integer,
  selected_max_conversation_bytes bigint
) returns void
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
declare
  selected_attachment_id uuid;
  selected_active_ids uuid[];
  selected_message_total bigint;
  selected_conversation_count bigint;
  selected_conversation_total bigint;
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <>
        (session_user='platform_control_app')
  then
    raise insufficient_privilege using
      message='Conversation attachment binder invalid';
  end if;
  if selected_owner_internal_user_id is null
     or selected_conversation_id is null
     or selected_message_id is null
     or selected_turn_id is null
     or selected_attachment_ids is null
     or selected_active_attachment_ids is null
     or selected_agent_supports_attachments is null
     or selected_max_message_files not between 1 and 5
     or selected_max_message_bytes not between 1 and 52428800
     or selected_max_conversation_files not between 1 and 50
     or selected_max_conversation_bytes not between 1 and 524288000
     or cardinality(selected_attachment_ids)>selected_max_message_files
     or cardinality(selected_active_attachment_ids)>selected_max_conversation_files
     or cardinality(selected_attachment_ids)<>(
       select count(distinct value) from unnest(selected_attachment_ids) value
     )
     or cardinality(selected_active_attachment_ids)<>(
       select count(distinct value) from unnest(selected_active_attachment_ids) value
     )
     or not selected_attachment_ids <@ selected_active_attachment_ids
     or (selected_agent_id is not null and
         selected_agent_id !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
     or (cardinality(selected_active_attachment_ids)>0
         and selected_agent_id is not null
         and not selected_agent_supports_attachments)
  then
    raise check_violation using message='Conversation attachment binding invalid';
  end if;
  perform 1 from platform_control.conversations
  where conversation_id=selected_conversation_id
    and owner_internal_user_id=selected_owner_internal_user_id
    and status='active'
  for update;
  if not found then
    raise no_data_found using message='Conversation attachment unavailable';
  end if;
  perform 1 from platform_control.conversation_messages
  where conversation_id=selected_conversation_id
    and message_id=selected_message_id and turn_id=selected_turn_id;
  if not found then
    raise no_data_found using message='Conversation attachment message unavailable';
  end if;
  foreach selected_attachment_id in array selected_attachment_ids loop
    perform platform_attachments.claim_conversation_attachment_v64(
      selected_attachment_id,
      selected_owner_internal_user_id,
      selected_conversation_id
    );
  end loop;
  select coalesce(array_agg(locked.attachment_id order by locked.attachment_id),'{}'),
         coalesce(sum(locked.size_bytes) filter (
           where locked.attachment_id=any(selected_attachment_ids)
         ),0)
    into selected_active_ids,selected_message_total
  from (
    select attachment.attachment_id,attachment.size_bytes
    from platform_attachments.attachments attachment
    where attachment.attachment_id=any(selected_active_attachment_ids)
      and attachment.owner_internal_user_id=selected_owner_internal_user_id
      and attachment.conversation_id=selected_conversation_id
      and attachment.state='ready' and attachment.ready_at is not null
      and attachment.retained_until>now()
      and attachment.immutable_locator is not null
      and not exists (
        select 1 from platform_attachments.erasure_jobs erasure
        where erasure.attachment_id=attachment.attachment_id
      )
    for update
  ) locked;
  if selected_active_ids<>(
       select coalesce(array_agg(value order by value),'{}')
       from unnest(selected_active_attachment_ids) value
     )
  then
    raise check_violation using message='Conversation attachment selection unavailable';
  end if;
  if selected_message_total>selected_max_message_bytes then
    raise program_limit_exceeded using
      message='Conversation message attachment quota exceeded';
  end if;
  select count(*),coalesce(sum(locked.size_bytes),0)
    into selected_conversation_count,selected_conversation_total
  from (
    select attachment.size_bytes
    from platform_attachments.attachments attachment
    where attachment.owner_internal_user_id=selected_owner_internal_user_id
      and attachment.conversation_id=selected_conversation_id
      and attachment.source_kind='user_input'
      and attachment.state<>'deleted'
      and not exists (
        select 1 from platform_attachments.erasure_jobs erasure
        where erasure.attachment_id=attachment.attachment_id
      )
    for update
  ) locked;
  if selected_conversation_count>selected_max_conversation_files
     or selected_conversation_total>selected_max_conversation_bytes
  then
    raise program_limit_exceeded using
      message='Conversation attachment quota exceeded';
  end if;
  insert into platform_attachments.bindings(
    binding_id,attachment_id,owner_internal_user_id,kind,
    conversation_id,message_id,agent_id
  ) select gen_random_uuid(),value,selected_owner_internal_user_id,
    'message_input',selected_conversation_id,selected_message_id,selected_agent_id
  from unnest(selected_attachment_ids) value;
  insert into platform_attachments.bindings(
    binding_id,attachment_id,owner_internal_user_id,kind,
    conversation_id,turn_id,agent_id
  ) select gen_random_uuid(),value,selected_owner_internal_user_id,
    'turn_input',selected_conversation_id,selected_turn_id,selected_agent_id
  from unnest(selected_active_attachment_ids) value;
end
$function$;

create function platform_attachments.bind_brain_answer_artifacts_v64(
  selected_loop_id uuid,
  selected_message_id uuid,
  selected_attachment_ids uuid[]
) returns integer
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
declare
  selected_owner_internal_user_id uuid;
  selected_conversation_id uuid;
  selected_turn_id uuid;
  selected_count integer;
begin
  if current_user not in ('platform_control_owner','platform_control_owner_preview')
     or session_user not in ('platform_brain_worker','platform_brain_worker_preview')
     or (current_database()='agent_platform_control') <>
       (session_user='platform_brain_worker')
  then raise insufficient_privilege using message='Brain answer artifact binder invalid'; end if;
  if selected_loop_id is null or selected_message_id is null
     or selected_attachment_ids is null
     or cardinality(selected_attachment_ids) not between 1 and 32
     or cardinality(selected_attachment_ids)<>(
       select count(distinct value) from unnest(selected_attachment_ids) value
     )
  then raise check_violation using message='Brain answer artifacts invalid'; end if;

  select conversation.owner_internal_user_id,loop.conversation_id,loop.turn_id
    into selected_owner_internal_user_id,selected_conversation_id,selected_turn_id
  from platform_brain.brain_loops loop
  join platform_control.conversations conversation
    on conversation.conversation_id=loop.conversation_id
  where loop.loop_id=selected_loop_id;
  if not found then raise no_data_found using message='Brain answer Loop unavailable'; end if;
  if not exists (
    select 1 from platform_control.conversation_messages message
    where message.message_id=selected_message_id
      and message.conversation_id=selected_conversation_id
      and message.turn_id=selected_turn_id and message.role='assistant'
  ) then raise check_violation using message='Brain answer message invalid'; end if;

  select count(*) into selected_count
  from unnest(selected_attachment_ids) selected(attachment_id)
  join platform_attachments.current_artifact_versions version
    on version.attachment_id=selected.attachment_id
  join platform_attachments.artifacts artifact
    on artifact.artifact_id=version.artifact_id
  join platform_brain.agent_tasks task on task.task_id=artifact.task_id
  join platform_attachments.attachments attachment
    on attachment.attachment_id=version.attachment_id
  where task.loop_id=selected_loop_id
    and artifact.owner_internal_user_id=selected_owner_internal_user_id
    and artifact.conversation_id=selected_conversation_id
    and attachment.owner_internal_user_id=selected_owner_internal_user_id
    and attachment.conversation_id=selected_conversation_id
    and attachment.state='ready' and attachment.ready_at is not null
    and attachment.immutable_locator is not null
    and attachment.retained_until>now() and attachment.deleted_at is null;
  if selected_count<>cardinality(selected_attachment_ids) then
    raise check_violation using message='Brain answer artifact unavailable';
  end if;

  insert into platform_attachments.bindings(
    binding_id,attachment_id,owner_internal_user_id,kind,
    conversation_id,message_id,agent_id
  )
  select gen_random_uuid(),selected.attachment_id,
    selected_owner_internal_user_id,'message_output',selected_conversation_id,
    selected_message_id,artifact.agent_id
  from unnest(selected_attachment_ids) selected(attachment_id)
  join platform_attachments.current_artifact_versions version
    on version.attachment_id=selected.attachment_id
  join platform_attachments.artifacts artifact
    on artifact.artifact_id=version.artifact_id
  join platform_brain.agent_tasks task on task.task_id=artifact.task_id
  where task.loop_id=selected_loop_id
  on conflict do nothing;
  return cardinality(selected_attachment_ids);
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
    execute format(
      'revoke all on function platform_control.triage_conversation_feedback_v64('
      'uuid,uuid,text) from %I',role_name
    );
  end loop;

  execute format(
    'grant usage on schema platform_attachments to %I,%I,%I,%I',
    selected_app,selected_brain,selected_maintenance,selected_audit
  );
  execute format(
    'grant select on platform_attachments.attachments, '
    'platform_attachments.uploads,platform_attachments.upload_write_attempts, '
    'platform_attachments.bindings, '
    'platform_attachments.artifacts,platform_attachments.artifact_versions, '
    'platform_attachments.current_artifact_versions, '
    'platform_attachments.derivatives,platform_attachments.task_grants, '
    'platform_attachments.processing_jobs,platform_attachments.erasure_jobs, '
    'platform_attachments.message_citations, '
    'platform_attachments.conversation_read_state to %I',selected_app
  );
  execute format(
    'grant insert on platform_attachments.bindings, '
    'platform_attachments.artifacts, '
    'platform_attachments.message_citations to %I',selected_app
  );
  execute format(
    'grant select on platform_attachments.attachments, '
    'platform_attachments.uploads, '
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
    'grant execute on function platform_attachments.create_upload_v64('
    'uuid,uuid,uuid,uuid,bytea,integer,bytea,integer,text,bigint,timestamptz,'
    'bigint,integer,bigint), '
    'platform_attachments.bind_conversation_turn_v64('
    'uuid,uuid,uuid,uuid,uuid[],uuid[],text,boolean,integer,bigint,integer,bigint), '
    'platform_attachments.claim_upload_write_v64('
    'uuid,uuid,uuid,bytea,integer,timestamptz), '
    'platform_attachments.abandon_upload_write_v64(uuid,uuid,uuid), '
    'platform_attachments.cancel_upload_v64('
    'uuid,uuid,uuid,uuid,bytea,integer,bytea), '
    'platform_attachments.request_attachment_erasure_v64('
    'uuid,uuid,uuid,uuid,bytea,integer,bytea), '
    'platform_attachments.finalize_upload_v64('
    'uuid,uuid,uuid,text,bigint,bytea), '
    'platform_attachments.acknowledge_upload_write_cleanup_v64(uuid), '
    'platform_attachments.issue_task_grant_v64('
    'uuid,bytea,uuid,uuid,text,text,timestamptz,integer,bigint,integer,bigint), '
    'platform_attachments.consume_task_grant_gateway_v64(bytea,uuid,bigint), '
    'platform_attachments.create_artifact_upload_v64('
    'bytea,uuid,text,uuid,uuid,uuid,uuid,text,text,bytea,integer,bytea,integer,'
    'text,bigint,bytea,timestamptz), '
    'platform_attachments.claim_artifact_upload_write_v64('
    'bytea,uuid,uuid,bytea,integer,timestamptz), '
    'platform_attachments.abandon_artifact_upload_write_v64(bytea,uuid,uuid), '
    'platform_attachments.finalize_artifact_upload_v64('
    'bytea,uuid,uuid,text,bigint,bytea), '
    'platform_attachments.revoke_task_grant_v64(uuid), '
    'platform_attachments.authorize_review_attachment_access_v64('
    'uuid,uuid,text), '
    'platform_attachments.upsert_conversation_read_state_v64('
    'uuid,uuid,integer), '
    'platform_control.triage_conversation_feedback_v64('
    'uuid,uuid,text) to %I',selected_app
  );
  execute format(
    'grant execute on function '
    'platform_attachments.issue_task_grant_v64('
    'uuid,bytea,uuid,uuid,text,text,timestamptz,integer,bigint,integer,bigint), '
    'platform_attachments.claim_attachment_processing_job_v64(text), '
    'platform_attachments.record_attachment_processing_result_v64('
    'uuid,uuid,text,text,text,jsonb,text),'
    'platform_attachments.record_attachment_derivative_v64('
    'uuid,uuid,uuid,text,bytea,integer,text,bigint,bytea,text), '
    'platform_attachments.consume_task_grant_v64('
    'bytea,uuid,uuid,text,text,bigint), '
    'platform_attachments.consume_output_write_grant_v64('
    'bytea,uuid,text,bigint), '
    'platform_attachments.bind_artifact_version_v64('
    'uuid,uuid,uuid,integer,text), '
    'platform_attachments.fail_artifact_version_v64('
    'uuid,text,text), '
    'platform_attachments.bind_brain_answer_artifacts_v64('
    'uuid,uuid,uuid[]) to %I',selected_brain
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
