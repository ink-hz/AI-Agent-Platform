-- Bound ZenTao human accounts and one-use browser handoff tickets.
create table sdk.sso_bindings (
  internal_user_id uuid primary key references sdk.members(internal_user_id),
  account text not null unique check (
    account ~ '^[a-z][a-z0-9._-]{1,39}$'
    and account not in ('admin', 'sdkadmin', 'sdk-api')
  ),
  bound_by uuid not null references platform_control.internal_users(internal_user_id),
  bound_at timestamptz not null default clock_timestamp()
);

create table sdk.sso_tickets (
  token_digest bytea primary key check (octet_length(token_digest) = 32),
  internal_user_id uuid not null references platform_control.internal_users(internal_user_id),
  session_id uuid not null references platform_control.web_sessions(session_id) on delete cascade,
  expires_at timestamptz not null,
  consumed_at timestamptz,
  created_at timestamptz not null default clock_timestamp()
);
create index sdk_sso_tickets_expires_at on sdk.sso_tickets(expires_at);

-- ZenTao's legacy entry compares calledTime as ten-digit seconds. Concurrent
-- logins need a durable, strictly increasing allocator for this one entry.
create table sdk.sso_clock (
  singleton boolean primary key default true check (singleton),
  last_time bigint not null check (last_time >= 0 and last_time < 4000000000)
);
insert into sdk.sso_clock(singleton,last_time) values (true,0);

create function sdk.next_sso_time() returns bigint
language sql security definer
set search_path = pg_catalog, sdk
as $function$
  update sdk.sso_clock
  set last_time=greatest(last_time + 1, extract(epoch from clock_timestamp())::bigint)
  where singleton
  returning last_time;
$function$;

create function sdk.issue_sso_ticket(
  selected_user uuid, selected_session uuid, selected_digest bytea
) returns boolean
language plpgsql security definer
set search_path = pg_catalog, sdk, platform_control
as $function$
begin
  if selected_user is null or selected_session is null or
     selected_digest is null or octet_length(selected_digest) <> 32 then
    return false;
  end if;
  delete from sdk.sso_tickets where expires_at < clock_timestamp() - interval '1 hour';
  insert into sdk.sso_tickets(token_digest,internal_user_id,session_id,expires_at)
  select selected_digest, selected_user, selected_session,
         clock_timestamp() + interval '45 seconds'
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

create function sdk.consume_sso_ticket(selected_digest bytea) returns text
language sql security definer
set search_path = pg_catalog, sdk, platform_control
as $function$
  update sdk.sso_tickets ticket
  set consumed_at=clock_timestamp()
  from sdk.sso_bindings binding,
       sdk.members member,
       platform_control.web_sessions session,
       platform_control.internal_users person
  where ticket.token_digest=selected_digest
    and ticket.consumed_at is null
    and ticket.expires_at > clock_timestamp()
    and binding.internal_user_id=ticket.internal_user_id
    and member.internal_user_id=ticket.internal_user_id
    and session.session_id=ticket.session_id
    and session.internal_user_id=ticket.internal_user_id
    and session.revoked_at is null
    and session.idle_expires_at > clock_timestamp()
    and session.absolute_expires_at > clock_timestamp()
    and not session.hard_stale_read_only
    and person.internal_user_id=ticket.internal_user_id
    and person.status='active'
    and person.locally_invalidated_at is null
  returning binding.account;
$function$;

revoke all on sdk.sso_bindings, sdk.sso_tickets, sdk.sso_clock from public;
revoke all on function sdk.issue_sso_ticket(uuid,uuid,bytea) from public;
revoke all on function sdk.consume_sso_ticket(bytea) from public;
revoke all on function sdk.next_sso_time() from public;

do $migration$
declare selected_app name;
begin
  if current_database() = 'agent_platform_control' and current_user = 'platform_control_owner' then
    selected_app := 'platform_control_app';
  elsif current_database() = 'agent_platform_control_preview' and current_user = 'platform_control_owner_preview' then
    selected_app := 'platform_control_app_preview';
  else
    raise insufficient_privilege using message = 'SDK SSO migration owner/environment mismatch';
  end if;
  execute format('grant select, insert, update on sdk.sso_bindings to %I', selected_app);
  execute format('grant execute on function sdk.issue_sso_ticket(uuid,uuid,bytea) to %I', selected_app);
  execute format('grant execute on function sdk.consume_sso_ticket(bytea) to %I', selected_app);
  execute format('grant execute on function sdk.next_sso_time() to %I', selected_app);
end
$migration$;
