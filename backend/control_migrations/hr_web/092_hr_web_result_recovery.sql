-- Opt-in HR production inventory 2026-09-08: target 087; apply base 088 first.
-- Promoted unchanged SQL from the reviewed, disposable-tested HR WEB contract.
-- Link publication and late native evidence to the one existing command binding.
alter table platform_control.direct_command_bindings
  add column progress_source_seq bigint not null default 0 check (progress_source_seq >= 0),
  add column terminal_source_seq bigint check (terminal_source_seq > 0),
  add column published_message_id uuid references platform_control.conversation_messages(message_id),
  add column executor_stop_proof_ref text check (length(executor_stop_proof_ref) between 1 and 256),
  add column recovery_observed_at timestamptz,
  add column recovery_reason text check (length(recovery_reason) between 1 and 128),
  add column recovery_poll_after timestamptz,
  add constraint direct_result_requires_source check (published_message_id is null or terminal_source_seq is not null);

-- A Result consumer, not another execution queue. Names and spool references
-- remain in the encrypted, authenticated source; only lookup metadata is indexed.
create table platform_control.result_artifact_intents (
  run_id uuid not null,
  source_seq bigint not null,
  intent_index integer not null check (intent_index between 0 and 19),
  message_id uuid not null references platform_control.conversation_messages(message_id),
  grant_id uuid references platform_attachments.task_grants(grant_id),
  artifact_key text not null check (artifact_key ~ '^artifact-[0-9a-f]{24}$'),
  producer_version_id text not null check (producer_version_id ~ '^[0-9a-f]{64}$'),
  declared_mime text not null,
  size_bytes bigint not null check (size_bytes between 1 and 262144000),
  expected_sha256 bytea not null check (octet_length(expected_sha256)=32),
  status text not null check (status in ('pending','ready','failed')),
  attachment_id uuid references platform_attachments.attachments(attachment_id),
  next_attempt_at timestamptz not null default clock_timestamp(),
  last_error_code text check (last_error_code in (
    'artifact_intent_invalid','artifact_grant_expired','artifact_rejected','artifact_unavailable'
  )),
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  primary key (run_id,intent_index),
  foreign key (run_id,source_seq) references platform_control.v5_source_events(run_id,seq),
  check (status <> 'ready' or (attachment_id is not null and last_error_code is null)),
  check (status <> 'pending' or grant_id is not null)
);
create index result_artifact_intents_due on platform_control.result_artifact_intents(next_attempt_at)
  where status='pending';
create index result_artifact_intents_message on platform_control.result_artifact_intents(message_id);
create function platform_control.preserve_result_artifact_intent()
returns trigger language plpgsql set search_path=pg_catalog,platform_control
as $function$
begin
  if (new.run_id,new.source_seq,new.intent_index,new.message_id,new.grant_id,new.artifact_key,
      new.producer_version_id,new.declared_mime,new.size_bytes,new.expected_sha256,new.created_at)
     is distinct from
     (old.run_id,old.source_seq,old.intent_index,old.message_id,old.grant_id,old.artifact_key,
      old.producer_version_id,old.declared_mime,old.size_bytes,old.expected_sha256,old.created_at)
     or (old.status='ready' and (new.status,new.attachment_id) is distinct from (old.status,old.attachment_id)) then
    raise check_violation using message='result artifact identity is immutable';
  end if;
  return new;
end
$function$;
create trigger preserve_result_artifact_intent before update on platform_control.result_artifact_intents
  for each row execute function platform_control.preserve_result_artifact_intent();
revoke all on platform_control.result_artifact_intents from public;
do $grants$
declare selected_app name;
begin
  case current_user
    when 'platform_control_owner' then selected_app := 'platform_control_app';
    when 'platform_control_owner_preview' then selected_app := 'platform_control_app_preview';
    else raise insufficient_privilege using message='result artifact environment invalid';
  end case;
  execute format('grant select,insert,update on platform_control.result_artifact_intents to %I', selected_app);
end
$grants$;

-- Same v64 reservation implementation and limits, with one additional late
-- reservation predicate tied to a published v5 Result's original fixed intent.
create or replace function platform_attachments.create_artifact_upload_v64(
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
  ) or (task.task_status='completed' and selected_agent_id='hr-bot'
    and exists (
      select 1 from platform_control.result_artifact_intents intent
      join platform_attachments.task_grants grant_row using(grant_id)
      join platform_control.conversation_messages message using(message_id)
      join platform_control.direct_command_bindings binding
        on binding.published_message_id=message.message_id
      join platform_control.execution_jobs job on job.job_id=binding.job_id
      where intent.run_id=selected_task_id and job.run_id=intent.run_id
        and job.job_kind='worker_direct_v5'
        and binding.terminal_source_seq=intent.source_seq
        and message.conversation_id=task.conversation_id
        and message.role='assistant' and message.delivery_status='completed'
        and grant_row.token_sha256=selected_token_sha256
        and grant_row.task_id=intent.run_id and grant_row.agent_id=selected_agent_id
        and intent.status in ('pending','ready')
        and intent.artifact_key=selected_artifact_key
        and intent.producer_version_id=selected_producer_version_id
        and intent.declared_mime=selected_declared_mime
        and intent.size_bytes=selected_size_bytes
        and intent.expected_sha256=selected_expected_sha256
    ));
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
    and grant_row.revoked_at is null and grant_row.expires_at > clock_timestamp()
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

  -- Predicates evaluated before a row-lock wait are not a current-time grant
  -- check. Revalidate after BOTH grant and artifact locks, including replay.
  perform 1 from platform_attachments.task_grants grant_row
  where grant_row.grant_id=selected_grant_id and grant_row.revoked_at is null
    and grant_row.expires_at>clock_timestamp();
  if not found then
    raise insufficient_privilege using message='Artifact output grant unavailable';
  end if;

  if existing_artifact_id is not null then
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
    and grant_row.revoked_at is null and grant_row.expires_at > clock_timestamp()
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

-- Keep only the original, still-valid grant for fixed Result intents. No new
-- grant, expiry extension, or task reopening; v4 reservations retain v88 behavior.
create or replace function platform_attachments.revoke_terminal_task_grants_v64()
returns trigger language plpgsql security definer
set search_path=pg_catalog,platform_attachments
as $function$
declare keep_uploads boolean := false;
begin
  if old.terminal_at is null and new.terminal_at is not null then
    if tg_table_schema='platform_control' and new.status='completed'
       and new.agent_id='hr-bot' then
      keep_uploads := platform_attachments.is_direct_hr_task_v88(new.task_id)
        and exists (
          select 1 from platform_attachments.artifacts artifact
          join platform_attachments.artifact_versions version using(artifact_id)
          join platform_attachments.uploads upload using(attachment_id)
          where artifact.task_id=new.task_id and artifact.agent_id=new.agent_id
            and version.state='uploading' and upload.state='uploading'
            and upload.expires_at>clock_timestamp()
        );
    end if;
    update platform_attachments.task_grants grant_row set
      revoked_at=case
        when new.status='completed' and new.agent_id='hr-bot'
          and grant_row.scope='write_output' and grant_row.expires_at>clock_timestamp()
          and exists (select 1 from platform_control.result_artifact_intents intent
            where intent.run_id=new.task_id and intent.grant_id=grant_row.grant_id and intent.status='pending')
          then null
        when keep_uploads and grant_row.scope='write_output' and grant_row.file_count>0
          then null else clock_timestamp() end,
      expires_at=case
        when exists (select 1 from platform_control.result_artifact_intents intent
          where intent.run_id=new.task_id and intent.grant_id=grant_row.grant_id and intent.status='pending')
          then grant_row.expires_at
        when keep_uploads and grant_row.scope='write_output' and grant_row.file_count>0
          then least(grant_row.expires_at,clock_timestamp()+interval '15 minutes')
        else grant_row.expires_at end
    where grant_row.task_id=new.task_id and grant_row.revoked_at is null;
  end if;
  return new;
end
$function$;
