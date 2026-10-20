-- Bind a single-use SSO ticket to one server-owned SDK work destination.
alter table sdk.sso_tickets add column target text not null default 'home'
  check (target in ('home','requirements','tasks','tests','releases'));

create function sdk.issue_sso_ticket(
  selected_user uuid, selected_session uuid, selected_digest bytea, selected_target text
) returns boolean
language plpgsql security definer
set search_path = pg_catalog, sdk, platform_control
as $function$
begin
  if selected_user is null or selected_session is null or
     selected_digest is null or octet_length(selected_digest) <> 32 or
     selected_target is null or
     selected_target not in ('home','requirements','tasks','tests','releases') then
    return false;
  end if;
  delete from sdk.sso_tickets where expires_at < clock_timestamp() - interval '1 hour';
  insert into sdk.sso_tickets(token_digest,internal_user_id,session_id,expires_at,target)
  select selected_digest, selected_user, selected_session,
         clock_timestamp() + interval '45 seconds', selected_target
  from platform_control.web_sessions session
  join platform_control.internal_users person
    on person.internal_user_id=session.internal_user_id
  join sdk.members member on member.internal_user_id=person.internal_user_id
  join sdk.sso_bindings binding on binding.internal_user_id=member.internal_user_id
  where session.session_id=selected_session
    and session.internal_user_id=selected_user
    and session.revoked_at is null
    and session.idle_expires_at > clock_timestamp()
    and session.absolute_expires_at > clock_timestamp()
    and not session.hard_stale_read_only
    and person.status='active'
    and person.locally_invalidated_at is null
  on conflict do nothing;
  return found;
end
$function$;

revoke all on function sdk.issue_sso_ticket(uuid,uuid,bytea,text) from public;
do $migration$
declare selected_app name;
begin
  if current_database() = 'agent_platform_control' and current_user = 'platform_control_owner' then
    selected_app := 'platform_control_app';
  elsif current_database() = 'agent_platform_control_preview' and current_user = 'platform_control_owner_preview' then
    selected_app := 'platform_control_app_preview';
  else
    raise insufficient_privilege using message = 'SDK SSO target migration owner/environment mismatch';
  end if;
  execute format('grant select(token_digest,target) on sdk.sso_tickets to %I', selected_app);
  execute format('grant execute on function sdk.issue_sso_ticket(uuid,uuid,bytea,text) to %I', selected_app);
end
$migration$;
