-- A result-enrichment failure must never roll back a committed text answer.
create table platform_control.conversation_result_deliveries (
  mission_id uuid primary key
    references platform_control.missions(mission_id) on delete cascade,
  message_id uuid not null unique
    references platform_control.conversation_messages(message_id) on delete cascade,
  status text not null default 'pending'
    check (status in ('pending','completed','failed')),
  attempts integer not null default 0 check (attempts between 0 and 8),
  next_attempt_at timestamptz not null default now(),
  last_error_code text check (last_error_code in ('result_projection_unavailable')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (status <> 'pending' or attempts < 8),
  check (status <> 'completed' or last_error_code is null)
);
create index conversation_result_deliveries_due_v88
  on platform_control.conversation_result_deliveries(next_attempt_at,mission_id)
  where status='pending';
revoke all on platform_control.conversation_result_deliveries from public;
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
    raise insufficient_privilege using message='result delivery environment invalid';
  end if;
  execute format(
    'grant select,insert,update on platform_control.conversation_result_deliveries to %I',
    selected_app
  );
end
$migration$;

-- Existing upload reservations may finish after successful HR execution.
-- This does not authorize registration, new grants, other Bots, or failed tasks.
create function platform_attachments.is_direct_hr_task_v88(selected_task_id uuid)
returns boolean language sql stable security definer
set search_path=pg_catalog,platform_control
as $function$
  select exists (
    select 1 from platform_control.mission_tasks task
    join platform_control.missions mission using(mission_id)
    where task.task_id=selected_task_id and task.agent_id='hr-bot'
      and mission.mode='direct_agent' and mission.direct_agent_id='hr-bot'
  )
$function$;
revoke all on function platform_attachments.is_direct_hr_task_v88(uuid) from public;

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
        when keep_uploads and grant_row.scope='write_output' and grant_row.file_count>0
          then null else clock_timestamp() end,
      expires_at=case
        when keep_uploads and grant_row.scope='write_output' and grant_row.file_count>0
          then least(grant_row.expires_at,clock_timestamp()+interval '15 minutes')
        else grant_row.expires_at end
    where grant_row.task_id=new.task_id and grant_row.revoked_at is null;
  end if;
  return new;
end
$function$;

create or replace function platform_attachments.claim_artifact_upload_write_v64(
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
    and (task.task_status in (
      'queued','dispatched','running','waiting_input','waiting_confirmation'
    ) or (task.task_status='completed'
      and platform_attachments.is_direct_hr_task_v88(artifact.task_id)))
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

create or replace function platform_attachments.finalize_artifact_upload_v64(
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
    and (task.task_status in (
      'queued','dispatched','running','waiting_input','waiting_confirmation'
    ) or (task.task_status='completed'
      and platform_attachments.is_direct_hr_task_v88(artifact.task_id)))
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
