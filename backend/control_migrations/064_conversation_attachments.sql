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
  task_id uuid references platform_control.mission_tasks(task_id),
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
  task_id uuid not null references platform_control.mission_tasks(task_id),
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
  task_id uuid not null references platform_control.mission_tasks(task_id),
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
  add column triage_status text;

update platform_control.conversation_feedback
set triage_status='pending_triage'
where rating='unhelpful';

alter table platform_control.conversation_feedback
  add constraint conversation_feedback_triage_v64 check (
    (rating='helpful' and triage_status is null)
    or (rating='unhelpful' and triage_status in (
      'pending_triage','triaged','dismissed'
    ))
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

create function platform_attachments.enforce_binding_task_context_v64()
returns trigger
language plpgsql security definer
set search_path = pg_catalog, platform_attachments
as $function$
begin
  if new.kind in ('task_input','task_output') and not exists (
    select 1
    from platform_control.mission_tasks task
    join platform_control.missions mission on mission.mission_id=task.mission_id
    where task.task_id=new.task_id and task.agent_id=new.agent_id
      and mission.owner_internal_user_id=new.owner_internal_user_id
      and mission.conversation_id=new.conversation_id
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
    from platform_control.mission_tasks task
    join platform_control.missions mission on mission.mission_id=task.mission_id
    where task.task_id=new.task_id and task.agent_id=new.agent_id
      and mission.owner_internal_user_id=new.owner_internal_user_id
      and mission.conversation_id=new.conversation_id
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
  select upload.attachment_id,upload.write_attempt_id
    into selected_attachment_id,previous_write_attempt_id
  from platform_attachments.uploads upload
  join platform_attachments.attachments attachment
    on attachment.attachment_id=upload.attachment_id
   and attachment.owner_internal_user_id=upload.owner_internal_user_id
   and attachment.conversation_id is not distinct from upload.conversation_id
  where upload.upload_id=selected_upload_id
    and upload.owner_internal_user_id=selected_owner_internal_user_id
    and upload.state='uploading' and upload.expires_at > now()
    and selected_write_lease_expires_at <= upload.expires_at
    and (upload.write_attempt_id is null or upload.write_lease_expires_at <= now())
    and attachment.state='uploading'
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
    attempt_count=attempt_count+1
  where processing_job_id=selected_job.processing_job_id returning * into selected_job;
  return selected_job;
end
$function$;

create function platform_attachments.record_attachment_processing_result_v64(
  selected_processing_job_id uuid,
  selected_attachment_state text,
  selected_state_reason text,
  selected_detected_mime text default null,
  selected_coverage_metadata jsonb default null
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
      coverage_metadata=selected_coverage_metadata
    where attachment_id=selected_job.attachment_id;
    update platform_attachments.uploads set
      detected_mime=selected_detected_mime,
      coverage_metadata=selected_coverage_metadata
    where attachment_id=selected_job.attachment_id;
  elsif selected_detected_mime is not null or selected_coverage_metadata is not null then
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
     or session_user not in ('platform_control_app','platform_control_app_preview')
     or (current_database()='agent_platform_control') <> (session_user='platform_control_app')
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
  select task.status,mission.owner_internal_user_id,mission.conversation_id
    into selected_task_status,selected_owner_internal_user_id,
         selected_conversation_id
  from platform_control.mission_tasks task
  join platform_control.missions mission on mission.mission_id=task.mission_id
  where task.task_id=selected_task_id and task.agent_id=selected_agent_id
  for update of task;
  if not found or selected_task_status not in ('queued','running') then
    raise check_violation using message='Attachment grant requires active task';
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
  if exists (
    select 1 from platform_control.mission_tasks task
    where task.task_id=selected_task_id and task.agent_id=selected_agent_id
      and task.status not in ('queued','running')
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
    select 1 from platform_control.mission_tasks task
    where task.task_id=selected_task_id and task.agent_id=selected_agent_id
      and task.status in ('queued','running')
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
    object_ref_ciphertext,object_ref_key_version,detected_mime,
    coverage_metadata,size_bytes,sha256,retained_until,state,state_reason,
    result_status
  ) values (
    selected_artifact_version_id,selected_artifact_id,selected_attachment.attachment_id,
    selected_version_no,selected_producer_version_id,
    selected_attachment.original_name_ciphertext,
    selected_attachment.original_name_key_version,
    selected_attachment.object_ref_ciphertext,
    selected_attachment.object_ref_key_version,selected_attachment.detected_mime,
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
    'platform_attachments.artifacts,platform_attachments.erasure_jobs, '
    'platform_attachments.message_citations to %I',selected_app
  );
  execute format(
    'grant update (triage_status) on '
    'platform_control.conversation_feedback to %I',selected_app
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
    'platform_attachments.claim_upload_write_v64('
    'uuid,uuid,uuid,bytea,integer,timestamptz), '
    'platform_attachments.abandon_upload_write_v64(uuid,uuid,uuid), '
    'platform_attachments.finalize_upload_v64('
    'uuid,uuid,uuid,text,bigint,bytea), '
    'platform_attachments.acknowledge_upload_write_cleanup_v64(uuid), '
    'platform_attachments.issue_task_grant_v64('
    'uuid,bytea,uuid,uuid,text,text,timestamptz,integer,bigint,integer,bigint), '
    'platform_attachments.revoke_task_grant_v64(uuid), '
    'platform_attachments.upsert_conversation_read_state_v64('
    'uuid,uuid,integer) to %I',selected_app
  );
  execute format(
    'grant execute on function '
    'platform_attachments.claim_attachment_processing_job_v64(text), '
    'platform_attachments.record_attachment_processing_result_v64('
    'uuid,text,text,text,jsonb),'
    'platform_attachments.record_attachment_derivative_v64('
    'uuid,uuid,text,bytea,integer,text,bigint,bytea,text), '
    'platform_attachments.consume_task_grant_v64('
    'bytea,uuid,uuid,text,text,bigint), '
    'platform_attachments.consume_output_write_grant_v64('
    'bytea,uuid,text,bigint), '
    'platform_attachments.bind_artifact_version_v64('
    'uuid,uuid,uuid,integer,text), '
    'platform_attachments.fail_artifact_version_v64('
    'uuid,text,text) to %I',selected_brain
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
