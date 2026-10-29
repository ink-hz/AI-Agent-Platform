-- Names are resolved from trusted control-plane subjects, never replica metadata.
create function platform_control.read_fae_session_subjects_v126(
  selected_session_id uuid, selected_subject_ids uuid[]
) returns table(
  subject_id uuid, subject_type text, display_name text,
  display_name_ciphertext bytea, display_name_key_version integer
)
language plpgsql
security definer
set search_path=pg_catalog,platform_control
as $function$
begin
  if selected_subject_ids is null
     or cardinality(selected_subject_ids) not between 1 and 100
     or array_ndims(selected_subject_ids)<>1
     or array_position(selected_subject_ids,null) is not null
  then raise check_violation using message='FAE identity query invalid'; end if;

  perform 1 from platform_control.web_sessions session
  join platform_control.internal_users users
    on users.internal_user_id=session.internal_user_id
  where session.session_id=selected_session_id
    and session.revoked_at is null
    and session.idle_expires_at>clock_timestamp()
    and session.absolute_expires_at>clock_timestamp()
    and users.status='active' and users.locally_invalidated_at is null
    and (users.role in ('platform_owner','platform_admin')
      or platform_control.has_observation_scope_v23(users.internal_user_id,'ai-fae-agent'));
  if not found then
    raise insufficient_privilege using message='FAE observation permission required';
  end if;

  -- Retain historical attribution even when the questioner's access is revoked.
  return query
  select subject.subject_id,subject.subject_type::text,
    case when subject.subject_type='enterprise_member' then users.display_name end,
    subject.display_name_ciphertext,subject.display_name_key_version
  from platform_control.agent_access_subjects subject
  left join platform_control.enterprise_subject_links link
    on link.subject_id=subject.subject_id
  left join platform_control.internal_users users
    on users.internal_user_id=link.internal_user_id
  where subject.subject_id=any(selected_subject_ids);
end
$function$;

revoke all on function platform_control.read_fae_session_subjects_v126(uuid,uuid[]) from public;
do $migration$
declare selected_app name;
begin
  if current_database()='agent_platform_control' and current_user='platform_control_owner'
  then selected_app:='platform_control_app';
  elsif current_database()='agent_platform_control_preview' and current_user='platform_control_owner_preview'
  then selected_app:='platform_control_app_preview';
  else raise insufficient_privilege using message='FAE identity projection owner/environment mismatch';
  end if;
  execute format('grant execute on function platform_control.read_fae_session_subjects_v126(uuid,uuid[]) to %I',selected_app);
end
$migration$;
