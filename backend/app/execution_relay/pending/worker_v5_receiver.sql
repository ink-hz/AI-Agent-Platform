-- Unapplied extension draft. O02 assigns deployed ordering; v1 remains unchanged.
DO $guard$
BEGIN
  IF (SELECT version FROM execution_worker.schema_migrations WHERE singleton) IS DISTINCT FROM 1
     OR to_regclass('execution_worker.local_runs') IS NULL
     OR to_regclass('execution_worker.event_outbox') IS NULL THEN
    RAISE EXCEPTION 'v5 receiver requires worker schema v1';
  END IF;
  IF (SELECT array_agg(column_name::text ORDER BY ordinal_position)
      FROM information_schema.columns WHERE table_schema='execution_worker' AND table_name='local_runs')
      IS DISTINCT FROM ARRAY['run_id','job_id','agent_id','metabot_port','callback_token_hash','state','leased_at','dispatched_at','terminal_at']
     OR (SELECT array_agg(column_name::text ORDER BY ordinal_position)
      FROM information_schema.columns WHERE table_schema='execution_worker' AND table_name='event_outbox')
      IS DISTINCT FROM ARRAY['run_id','seq','event_json','delivered_at'] THEN
    RAISE EXCEPTION 'v5 receiver base layout invalid';
  END IF;
END $guard$;
CREATE TABLE execution_worker.v5_callback_metadata (
  singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
  version integer NOT NULL CHECK(version=1)
);
INSERT INTO execution_worker.v5_callback_metadata(version) VALUES (1);
CREATE TABLE execution_worker.v5_callback_runs (
  run_id uuid PRIMARY KEY,
  command_id uuid NOT NULL UNIQUE,
  attempt_id uuid NOT NULL UNIQUE,
  command_hash text NOT NULL,
  worker_id text NOT NULL,
  launch_lease_epoch bigint NOT NULL CHECK(launch_lease_epoch BETWEEN 1 AND 9007199254740991),
  transport_lease_epoch bigint NOT NULL CHECK(transport_lease_epoch >= launch_lease_epoch),
  callback_origin text NOT NULL,
  token_hash bytea NOT NULL CHECK(octet_length(token_hash)=32),
  accepted_through bigint NOT NULL DEFAULT 0 CHECK(accepted_through BETWEEN 0 AND 9007199254740990),
  terminal_seq bigint
);
CREATE TABLE execution_worker.v5_callback_events (
  run_id uuid NOT NULL REFERENCES execution_worker.v5_callback_runs(run_id),
  seq bigint NOT NULL CHECK(seq BETWEEN 1 AND 9007199254740990),
  event_type text NOT NULL CHECK(event_type IN ('run_heartbeat','raw_progress','result','error','cancelled','interrupted')),
  event_json text NOT NULL,
  PRIMARY KEY(run_id,seq)
);
CREATE UNIQUE INDEX v5_callback_one_terminal ON execution_worker.v5_callback_events(run_id)
  WHERE event_type IN ('result','error','cancelled','interrupted');
REVOKE ALL ON execution_worker.v5_callback_metadata, execution_worker.v5_callback_runs,
  execution_worker.v5_callback_events FROM PUBLIC;
