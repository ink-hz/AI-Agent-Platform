create schema if not exists execution_worker;

comment on schema execution_worker is
  'agent execution worker schema version 1';

create table if not exists execution_worker.local_runs (
  run_id uuid primary key,
  job_id uuid not null unique,
  agent_id varchar(128) not null check (length(agent_id) > 0),
  metabot_port integer not null check (metabot_port between 1 and 65535),
  callback_token_hash bytea not null
    check (octet_length(callback_token_hash) = 32),
  state varchar(32) not null check (
    state in (
      'leased',
      'dispatching',
      'dispatched',
      'running',
      'completed',
      'failed',
      'cancelled',
      'interrupted'
    )
  ),
  leased_at timestamptz not null,
  dispatched_at timestamptz,
  terminal_at timestamptz
);

create table if not exists execution_worker.event_outbox (
  run_id uuid not null
    references execution_worker.local_runs(run_id),
  seq integer not null check (seq > 0),
  event_json jsonb not null
    check (jsonb_typeof(event_json) = 'object'),
  delivered_at timestamptz,
  primary key (run_id, seq)
);
