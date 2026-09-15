create or replace function platform_control.create_rate_limited_web_login_attempt_v2(
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
     or selected_return_path ~ '[\\\\%#]'
     or (
       selected_return_path ~ '[?]'
       and selected_return_path !~ (
         case required_environment
           when 'production' then
             '^/(?:hr|hr/|hr/agent)[?](?:'
             'position=[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'
             '(?:&work=[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})?'
             '|work=[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'
             '(?:&position=[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})?'
             ')$'
           when 'preview' then
             '^/_preview/dingtalk-r1/(?:hr|hr/|hr/agent)[?](?:'
             'position=[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'
             '(?:&work=[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})?'
             '|work=[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'
             '(?:&position=[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})?'
             ')$'
           else ''
         end
       )
     )
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
