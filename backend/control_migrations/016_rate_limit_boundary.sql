alter table platform_control.login_attempts
  add column browser_challenge_hash bytea,
  add column browser_challenge_hash_key_version integer,
  add constraint login_attempt_browser_challenge_hash_valid
    check (
      (browser_challenge_hash is null and browser_challenge_hash_key_version is null)
      or (
        octet_length(browser_challenge_hash) = 32
        and browser_challenge_hash_key_version > 0
      )
    );

create index login_attempts_browser_challenge_active
  on platform_control.login_attempts (
    browser_challenge_hash_key_version,
    browser_challenge_hash,
    created_at desc
  )
  where browser_challenge_hash is not null;

create function platform_control.consume_auth_rate_limit(
  selected_kind text,
  selected_bucket_key bytea,
  selected_rate integer,
  selected_capacity integer
) returns table(allowed boolean, retry_after integer)
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  database_now timestamptz := clock_timestamp();
  selected_window timestamptz;
  current_count integer;
  current_balance numeric;
  previous_update timestamptz;
  maximum_rate integer;
  maximum_capacity integer;
  is_token_bucket boolean := false;
begin
  case selected_kind
    when 'edge_login' then
      maximum_rate := 600;
      maximum_capacity := 1200;
      is_token_bucket := true;
    when 'edge_callback' then
      maximum_rate := 1200;
      maximum_capacity := 1200;
    when 'oauth_exchange' then
      maximum_rate := 3000;
      maximum_capacity := 3000;
    when 'authenticated_read' then
      maximum_rate := 300;
      maximum_capacity := 300;
    when 'authenticated_mutation' then
      maximum_rate := 60;
      maximum_capacity := 60;
    else
      raise check_violation using message='rate limit kind invalid';
  end case;
  if octet_length(selected_bucket_key) <> 32
     or selected_rate is null or selected_rate <= 0
     or selected_capacity is null or selected_capacity < selected_rate
     or selected_rate > maximum_rate
     or selected_capacity > maximum_capacity
     or (not is_token_bucket and selected_capacity <> selected_rate)
  then
    raise check_violation using message='rate limit input invalid';
  end if;

  delete from platform_control.auth_rate_buckets
  where ctid in (
    select ctid from platform_control.auth_rate_buckets
    where updated_at < database_now - interval '1 day'
    order by updated_at
    limit 100
  );

  if is_token_bucket then
    selected_window := timestamptz '1970-01-01 00:00:00+00';
    insert into platform_control.auth_rate_buckets (
      bucket_key,bucket_kind,window_started_at,request_count,
      token_balance,updated_at
    ) values (
      selected_bucket_key,selected_kind,selected_window,0,
      selected_capacity,database_now
    ) on conflict do nothing;
    select token_balance,updated_at into current_balance,previous_update
    from platform_control.auth_rate_buckets
    where bucket_kind=selected_kind
      and bucket_key=selected_bucket_key
      and window_started_at=selected_window
    for update;
    current_balance := least(
      selected_capacity::numeric,
      current_balance + greatest(
        0,
        extract(epoch from database_now-previous_update)
      ) * selected_rate::numeric / 60
    );
    if current_balance >= 1 then
      update platform_control.auth_rate_buckets
      set token_balance=current_balance-1,
          request_count=request_count+1,
          updated_at=database_now
      where bucket_kind=selected_kind
        and bucket_key=selected_bucket_key
        and window_started_at=selected_window;
      return query select true,0;
    else
      update platform_control.auth_rate_buckets
      set token_balance=current_balance,updated_at=database_now
      where bucket_kind=selected_kind
        and bucket_key=selected_bucket_key
        and window_started_at=selected_window;
      return query select false,greatest(
        1,ceil((1-current_balance)*60/selected_rate)::integer
      );
    end if;
    return;
  end if;

  selected_window := to_timestamp(
    floor(extract(epoch from database_now)/60)*60
  );
  insert into platform_control.auth_rate_buckets (
    bucket_key,bucket_kind,window_started_at,request_count,
    token_balance,updated_at
  ) values (
    selected_bucket_key,selected_kind,selected_window,0,0,database_now
  ) on conflict do nothing;
  select request_count into current_count
  from platform_control.auth_rate_buckets
  where bucket_kind=selected_kind
    and bucket_key=selected_bucket_key
    and window_started_at=selected_window
  for update;
  if current_count < selected_rate then
    update platform_control.auth_rate_buckets
    set request_count=request_count+1,updated_at=database_now
    where bucket_kind=selected_kind
      and bucket_key=selected_bucket_key
      and window_started_at=selected_window;
    return query select true,0;
  else
    return query select false,greatest(
      1,ceil(extract(epoch from selected_window+interval '1 minute'-database_now))::integer
    );
  end if;
end
$function$;

create function platform_control.create_rate_limited_web_login_attempt(
  selected_attempt_id uuid,
  selected_kind text,
  selected_state_hash bytea,
  selected_state_key_version integer,
  selected_pkce_hash bytea,
  selected_pkce_key_version integer,
  selected_verifier_ciphertext bytea,
  selected_return_path text,
  selected_environment text,
  selected_ttl_seconds integer,
  selected_browser_challenge_hash bytea,
  selected_browser_challenge_key_version integer,
  selected_edge_bucket_key bytea,
  selected_edge_key_version integer,
  selected_challenge_limit integer,
  selected_challenge_window_seconds integer,
  selected_active_limit integer,
  selected_edge_rate integer,
  selected_edge_capacity integer
) returns table(attempt_id uuid, allowed boolean, retry_after integer)
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  database_now timestamptz := clock_timestamp();
  required_environment text;
  edge_decision record;
  accepted_count integer;
  active_count integer;
  latest_start timestamptz;
  required_backoff integer;
begin
  required_environment := case current_database()
    when 'agent_platform_control' then 'production'
    when 'agent_platform_control_preview' then 'preview'
    else null
  end;
  if required_environment is null
     or selected_environment <> required_environment
     or selected_attempt_id is null
     or selected_kind not in ('qr','in_client')
     or octet_length(selected_state_hash) <> 32
     or selected_state_key_version is null or selected_state_key_version <= 0
     or octet_length(selected_pkce_hash) <> 32
     or selected_pkce_key_version is null or selected_pkce_key_version <= 0
     or octet_length(selected_verifier_ciphertext) < 29
     or selected_ttl_seconds <> 300
     or octet_length(selected_browser_challenge_hash) <> 32
     or selected_browser_challenge_key_version is null
     or selected_browser_challenge_key_version <= 0
     or octet_length(selected_edge_bucket_key) <> 32
     or selected_edge_key_version is null or selected_edge_key_version <= 0
     or selected_state_key_version <> selected_pkce_key_version
     or selected_state_key_version <> selected_browser_challenge_key_version
     or selected_state_key_version <> selected_edge_key_version
     or selected_challenge_limit <= 0 or selected_challenge_limit > 5
     or selected_challenge_window_seconds <> 600
     or selected_active_limit <= 0 or selected_active_limit > 3
     or selected_return_path is null
     or selected_return_path !~ '^/'
     or selected_return_path ~ E'[\\r\\n\\x00]'
     or selected_return_path ~ '[\\\\%?#]'
     or selected_return_path ~ '(^|/)\\.{1,2}(/|$)'
     or selected_return_path ~ '^//'
     or (
       required_environment='preview'
       and selected_return_path <> '/_preview/dingtalk-r1'
       and selected_return_path !~ '^/_preview/dingtalk-r1/'
     )
  then
    raise check_violation using message='rate limited login attempt invalid';
  end if;

  select * into edge_decision
  from platform_control.consume_auth_rate_limit(
    'edge_login',selected_edge_bucket_key,selected_edge_rate,
    selected_edge_capacity
  );
  if not edge_decision.allowed then
    return query select null::uuid,false,edge_decision.retry_after;
    return;
  end if;

  perform pg_advisory_xact_lock(
    hashtextextended(
      encode(selected_browser_challenge_hash,'hex') || ':' ||
      selected_browser_challenge_key_version::text,
      1380272972
    )
  );
  select count(*),max(created_at),count(*) filter (
    where consumed_at is null and failed_at is null and expires_at > database_now
  ) into accepted_count,latest_start,active_count
  from platform_control.login_attempts
  where browser_challenge_hash=selected_browser_challenge_hash
    and browser_challenge_hash_key_version=selected_browser_challenge_key_version
    and created_at > database_now-
      selected_challenge_window_seconds*interval '1 second';

  if accepted_count >= selected_challenge_limit then
    return query select null::uuid,false,greatest(
      1,ceil(extract(epoch from (
        min(created_at)+selected_challenge_window_seconds*interval '1 second'
        -database_now
      )))::integer
    ) from platform_control.login_attempts
      where browser_challenge_hash=selected_browser_challenge_hash
        and browser_challenge_hash_key_version=selected_browser_challenge_key_version
        and created_at > database_now-
          selected_challenge_window_seconds*interval '1 second';
    return;
  end if;
  if active_count >= selected_active_limit then
    return query select null::uuid,false,greatest(
      1,ceil(extract(epoch from min(expires_at)-database_now))::integer
    ) from platform_control.login_attempts
      where browser_challenge_hash=selected_browser_challenge_hash
        and browser_challenge_hash_key_version=selected_browser_challenge_key_version
        and consumed_at is null and failed_at is null
        and expires_at > database_now;
    return;
  end if;
  if accepted_count > 0 then
    required_backoff := power(2,accepted_count-1)::integer;
    if latest_start + required_backoff*interval '1 second' > database_now then
      return query select null::uuid,false,greatest(
        1,ceil(extract(epoch from (
          latest_start+required_backoff*interval '1 second'-database_now
        )))::integer
      );
      return;
    end if;
  end if;

  insert into platform_control.login_attempts (
    login_attempt_id,attempt_kind,state_hash,state_hash_key_version,
    challenge_hash,challenge_hash_key_version,verifier_ciphertext,
    return_path,environment,expires_at,browser_challenge_hash,
    browser_challenge_hash_key_version
  ) values (
    selected_attempt_id,selected_kind,selected_state_hash,selected_state_key_version,
    selected_pkce_hash,selected_pkce_key_version,selected_verifier_ciphertext,
    selected_return_path,selected_environment,database_now+interval '300 seconds',
    selected_browser_challenge_hash,selected_browser_challenge_key_version
  );
  return query select selected_attempt_id,true,0;
end
$function$;

revoke all on function platform_control.consume_auth_rate_limit(
  text,bytea,integer,integer
) from public;
revoke all on function platform_control.create_rate_limited_web_login_attempt(
  uuid,text,bytea,integer,bytea,integer,bytea,text,text,integer,
  bytea,integer,bytea,integer,integer,integer,integer,integer,integer
) from public;

do $migration$
declare
  selected_app name;
  role_name text;
begin
  selected_app := case current_database()
    when 'agent_platform_control' then 'platform_control_app'
    when 'agent_platform_control_preview' then 'platform_control_app_preview'
    else null
  end;
  if selected_app is null then
    raise insufficient_privilege using message='unsupported control environment';
  end if;
  foreach role_name in array array[
    'platform_control_migrator','platform_control_app','platform_directory_worker',
    'platform_stream_ingest','platform_audit_append','platform_control_maintenance',
    'platform_control_migrator_preview','platform_control_app_preview',
    'platform_directory_worker_preview','platform_stream_ingest_preview',
    'platform_audit_append_preview','platform_control_maintenance_preview'
  ] loop
    execute format(
      'revoke select,insert,update,delete on '
      'platform_control.auth_rate_buckets from %I',
      role_name
    );
    execute format(
      'revoke all on function platform_control.consume_auth_rate_limit('
      'text,bytea,integer,integer) from %I',role_name
    );
    execute format(
      'revoke all on function platform_control.create_rate_limited_web_login_attempt('
      'uuid,text,bytea,integer,bytea,integer,bytea,text,text,integer,'
      'bytea,integer,bytea,integer,integer,integer,integer,integer,integer) '
      'from %I',role_name
    );
  end loop;
  execute format(
    'grant execute on function platform_control.consume_auth_rate_limit('
    'text,bytea,integer,integer) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_control.create_rate_limited_web_login_attempt('
    'uuid,text,bytea,integer,bytea,integer,bytea,text,text,integer,'
    'bytea,integer,bytea,integer,integer,integer,integer,integer,integer) '
    'to %I',selected_app
  );
end
$migration$;
