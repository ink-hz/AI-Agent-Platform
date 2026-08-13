alter table platform_control.auth_rate_buckets
  add column bucket_key_version integer;

-- Migration 016 did not persist the HMAC key version. Preserve its counters
-- under a reserved sentinel; v2 adopts a row only when its digest matches the
-- caller's current keyed digest, which avoids guessing and prevents a reset.
update platform_control.auth_rate_buckets
set bucket_key_version=1000000;

alter table platform_control.auth_rate_buckets
  alter column bucket_key_version set not null,
  add constraint auth_rate_buckets_key_version_valid
    check (bucket_key_version between 1 and 1000000),
  drop constraint auth_rate_buckets_pkey,
  add primary key (
    bucket_kind,bucket_key_version,bucket_key,window_started_at
  );

create function platform_control.consume_auth_rate_limit_v2(
  selected_environment text,
  selected_kind text,
  selected_bucket_key bytea,
  selected_bucket_key_version integer,
  selected_rate integer,
  selected_capacity integer
) returns table(allowed boolean, retry_after integer)
language plpgsql
security definer
set search_path = pg_catalog, platform_control
as $function$
declare
  database_now timestamptz := clock_timestamp();
  required_environment text;
  selected_window timestamptz;
  current_count integer;
  current_balance numeric;
  previous_update timestamptz;
  maximum_rate integer;
  maximum_capacity integer;
  is_token_bucket boolean := false;
begin
  required_environment := case current_database()
    when 'agent_platform_control' then 'production'
    when 'agent_platform_control_preview' then 'preview'
    else null
  end;
  if selected_environment is null
     or required_environment is null
     or selected_environment <> required_environment
     or selected_kind is null
     or selected_bucket_key is null
     or octet_length(selected_bucket_key) <> 32
     or selected_bucket_key_version is null
     or selected_bucket_key_version < 1
     or selected_bucket_key_version > 999999
     or selected_rate is null
     or selected_rate <= 0
     or selected_capacity is null
     or selected_capacity <= 0
  then
    raise check_violation using message='rate limit input invalid';
  end if;

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
  if selected_capacity < selected_rate
     or selected_rate > maximum_rate
     or selected_capacity > maximum_capacity
     or (not is_token_bucket and selected_capacity <> selected_rate)
  then
    raise check_violation using message='rate limit input invalid';
  end if;

  if is_token_bucket then
    selected_window := timestamptz '1970-01-01 00:00:00+00';
    update platform_control.auth_rate_buckets
    set bucket_key_version=selected_bucket_key_version
    where bucket_kind=selected_kind
      and bucket_key_version=1000000
      and bucket_key=selected_bucket_key
      and window_started_at=selected_window;
    insert into platform_control.auth_rate_buckets (
      bucket_key,bucket_key_version,bucket_kind,window_started_at,
      request_count,token_balance,updated_at
    ) values (
      selected_bucket_key,selected_bucket_key_version,selected_kind,
      selected_window,0,selected_capacity,database_now
    ) on conflict do nothing;
    select token_balance,updated_at into current_balance,previous_update
    from platform_control.auth_rate_buckets
    where bucket_kind=selected_kind
      and bucket_key_version=selected_bucket_key_version
      and bucket_key=selected_bucket_key
      and window_started_at=selected_window
    for update;
    current_balance := least(
      selected_capacity::numeric,
      current_balance + greatest(
        0,extract(epoch from database_now-previous_update)
      ) * selected_rate::numeric / 60
    );
    if current_balance >= 1 then
      update platform_control.auth_rate_buckets
      set token_balance=current_balance-1,
          request_count=request_count+1,
          updated_at=database_now
      where bucket_kind=selected_kind
        and bucket_key_version=selected_bucket_key_version
        and bucket_key=selected_bucket_key
        and window_started_at=selected_window;
      return query select true,0;
    else
      update platform_control.auth_rate_buckets
      set token_balance=current_balance,updated_at=database_now
      where bucket_kind=selected_kind
        and bucket_key_version=selected_bucket_key_version
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
  update platform_control.auth_rate_buckets
  set bucket_key_version=selected_bucket_key_version
  where bucket_kind=selected_kind
    and bucket_key_version=1000000
    and bucket_key=selected_bucket_key
    and window_started_at=selected_window;
  insert into platform_control.auth_rate_buckets (
    bucket_key,bucket_key_version,bucket_kind,window_started_at,
    request_count,token_balance,updated_at
  ) values (
    selected_bucket_key,selected_bucket_key_version,selected_kind,
    selected_window,0,0,database_now
  ) on conflict do nothing;
  select request_count into current_count
  from platform_control.auth_rate_buckets
  where bucket_kind=selected_kind
    and bucket_key_version=selected_bucket_key_version
    and bucket_key=selected_bucket_key
    and window_started_at=selected_window
  for update;
  if current_count < selected_rate then
    update platform_control.auth_rate_buckets
    set request_count=request_count+1,updated_at=database_now
    where bucket_kind=selected_kind
      and bucket_key_version=selected_bucket_key_version
      and bucket_key=selected_bucket_key
      and window_started_at=selected_window;
    return query select true,0;
  else
    return query select false,greatest(
      1,ceil(extract(epoch from (
        selected_window+interval '1 minute'-database_now
      )))::integer
    );
  end if;
end
$function$;

create function platform_control.create_rate_limited_web_login_attempt_v2(
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
     or selected_environment is null
     or selected_environment <> required_environment
     or selected_attempt_id is null
     or selected_kind is null
     or selected_kind not in ('qr','in_client')
     or selected_state_hash is null
     or octet_length(selected_state_hash) <> 32
     or selected_state_key_version is null
     or selected_state_key_version < 1
     or selected_state_key_version > 999999
     or selected_pkce_hash is null
     or octet_length(selected_pkce_hash) <> 32
     or selected_pkce_key_version is null
     or selected_pkce_key_version < 1
     or selected_pkce_key_version > 999999
     or selected_verifier_ciphertext is null
     or octet_length(selected_verifier_ciphertext) < 29
     or octet_length(selected_verifier_ciphertext) > 8192
     or selected_ttl_seconds is null
     or selected_ttl_seconds <> 300
     or selected_browser_challenge_hash is null
     or octet_length(selected_browser_challenge_hash) <> 32
     or selected_browser_challenge_key_version is null
     or selected_browser_challenge_key_version < 1
     or selected_browser_challenge_key_version > 999999
     or selected_edge_bucket_key is null
     or octet_length(selected_edge_bucket_key) <> 32
     or selected_edge_key_version is null
     or selected_edge_key_version < 1
     or selected_edge_key_version > 999999
     or selected_state_key_version <> selected_pkce_key_version
     or selected_state_key_version <> selected_browser_challenge_key_version
     or selected_challenge_limit is null
     or selected_challenge_limit <= 0
     or selected_challenge_limit > 5
     or selected_challenge_window_seconds is null
     or selected_challenge_window_seconds <> 600
     or selected_active_limit is null
     or selected_active_limit <= 0
     or selected_active_limit > 3
     or selected_edge_rate is null
     or selected_edge_rate <= 0
     or selected_edge_rate > 600
     or selected_edge_capacity is null
     or selected_edge_capacity < selected_edge_rate
     or selected_edge_capacity > 1200
     or selected_return_path is null
     or length(selected_return_path) > 2048
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

  perform pg_advisory_xact_lock(
    hashtextextended(
      selected_environment || ':' ||
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
    and environment=selected_environment
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
        and environment=selected_environment
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
        and environment=selected_environment
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

  select * into edge_decision
  from platform_control.consume_auth_rate_limit_v2(
    selected_environment,'edge_login',selected_edge_bucket_key,
    selected_edge_key_version,selected_edge_rate,selected_edge_capacity
  );
  if not edge_decision.allowed then
    return query select null::uuid,false,edge_decision.retry_after;
    return;
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

create function platform_control.maintain_auth_rate_buckets(
  selected_environment text,
  selected_active_key_version integer,
  selected_ttl_seconds integer,
  selected_batch_size integer
) returns integer
language plpgsql
security definer
set search_path = pg_catalog, platform_control
set lock_timeout = '250ms'
as $function$
declare
  required_environment text;
  deleted_count integer;
begin
  required_environment := case current_database()
    when 'agent_platform_control' then 'production'
    when 'agent_platform_control_preview' then 'preview'
    else null
  end;
  if selected_environment is null
     or required_environment is null
     or selected_environment <> required_environment
     or selected_active_key_version is null
     or selected_active_key_version < 1
     or selected_active_key_version > 999999
     or selected_ttl_seconds is null
     or selected_ttl_seconds < 3600
     or selected_ttl_seconds > 604800
     or selected_batch_size is null
     or selected_batch_size < 1
     or selected_batch_size > 1000
  then
    raise check_violation using message='rate limit maintenance input invalid';
  end if;

  with selected as (
    select bucket_kind,bucket_key_version,bucket_key,window_started_at
    from platform_control.auth_rate_buckets
    where updated_at < clock_timestamp()-
      selected_ttl_seconds*interval '1 second'
    order by
      (bucket_key_version <> selected_active_key_version) desc,
      updated_at
    for update skip locked
    limit selected_batch_size
  ), deleted as (
    delete from platform_control.auth_rate_buckets buckets
    using selected
    where buckets.bucket_kind=selected.bucket_kind
      and buckets.bucket_key_version=selected.bucket_key_version
      and buckets.bucket_key=selected.bucket_key
      and buckets.window_started_at=selected.window_started_at
    returning 1
  ) select count(*)::integer into deleted_count from deleted;
  return deleted_count;
end
$function$;

revoke all on function platform_control.consume_auth_rate_limit_v2(
  text,text,bytea,integer,integer,integer
) from public;
revoke all on function platform_control.create_rate_limited_web_login_attempt_v2(
  uuid,text,bytea,integer,bytea,integer,bytea,text,text,integer,
  bytea,integer,bytea,integer,integer,integer,integer,integer,integer
) from public;
revoke all on function platform_control.maintain_auth_rate_buckets(
  text,integer,integer,integer
) from public;

do $migration$
declare
  selected_app name;
  selected_maintenance name;
  role_name text;
begin
  select app_role,maintenance_role into selected_app,selected_maintenance
  from (values
    ('agent_platform_control','platform_control_app','platform_control_maintenance'),
    ('agent_platform_control_preview','platform_control_app_preview','platform_control_maintenance_preview')
  ) roles(database_name,app_role,maintenance_role)
  where database_name=current_database();
  if selected_app is null or selected_maintenance is null then
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
      'revoke all on function platform_control.consume_auth_rate_limit('
      'text,bytea,integer,integer) from %I',role_name
    );
    execute format(
      'revoke all on function platform_control.create_rate_limited_web_login_attempt('
      'uuid,text,bytea,integer,bytea,integer,bytea,text,text,integer,'
      'bytea,integer,bytea,integer,integer,integer,integer,integer,integer) '
      'from %I',role_name
    );
    execute format(
      'revoke all on function platform_control.consume_auth_rate_limit_v2('
      'text,text,bytea,integer,integer,integer) from %I',role_name
    );
    execute format(
      'revoke all on function platform_control.create_rate_limited_web_login_attempt_v2('
      'uuid,text,bytea,integer,bytea,integer,bytea,text,text,integer,'
      'bytea,integer,bytea,integer,integer,integer,integer,integer,integer) '
      'from %I',role_name
    );
    execute format(
      'revoke all on function platform_control.maintain_auth_rate_buckets('
      'text,integer,integer,integer) from %I',role_name
    );
  end loop;
  execute format(
    'grant execute on function platform_control.consume_auth_rate_limit_v2('
    'text,text,bytea,integer,integer,integer) to %I',selected_app
  );
  execute format(
    'grant execute on function platform_control.create_rate_limited_web_login_attempt_v2('
    'uuid,text,bytea,integer,bytea,integer,bytea,text,text,integer,'
    'bytea,integer,bytea,integer,integer,integer,integer,integer,integer) '
    'to %I',selected_app
  );
  execute format(
    'grant execute on function platform_control.maintain_auth_rate_buckets('
    'text,integer,integer,integer) to %I',selected_maintenance
  );
end
$migration$;
