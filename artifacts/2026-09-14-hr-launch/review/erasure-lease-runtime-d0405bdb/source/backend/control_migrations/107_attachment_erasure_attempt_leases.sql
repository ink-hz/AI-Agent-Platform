-- Recover crashed erasure workers without allowing a late attempt to commit over
-- a newer claim. Only an exact current, unexpired token may renew or record.
ALTER TABLE platform_attachments.erasure_jobs
  ADD COLUMN max_attempts integer NOT NULL DEFAULT 5
    CHECK (max_attempts BETWEEN 1 AND 10),
  ADD COLUMN attempt_token uuid NOT NULL DEFAULT gen_random_uuid(),
  ADD COLUMN lease_expires_at timestamptz;

-- No v107 worker existed before this migration. Existing running claims are
-- therefore fenced with an immediately expired lease and a new unknown token.
UPDATE platform_attachments.erasure_jobs SET
  claimed_by=CASE WHEN state='running' THEN coalesce(claimed_by,'legacy-v64') ELSE NULL END,
  claimed_at=CASE WHEN state='running' THEN coalesce(claimed_at,clock_timestamp()) ELSE NULL END,
  lease_expires_at=CASE WHEN state='running' THEN clock_timestamp() ELSE NULL END;

ALTER TABLE platform_attachments.erasure_jobs
  ADD CONSTRAINT erasure_running_lease_v107 CHECK (
    state<>'running' OR (
      claimed_by IS NOT NULL AND claimed_at IS NOT NULL
      AND lease_expires_at IS NOT NULL
    )
  );

CREATE INDEX erasure_jobs_lease_claim_v107
  ON platform_attachments.erasure_jobs(
    state,available_at,lease_expires_at,created_at
  );

CREATE TABLE platform_attachments.erasure_recoveries (
  recovery_id uuid PRIMARY KEY,
  erasure_job_id uuid NOT NULL
    REFERENCES platform_attachments.erasure_jobs(erasure_job_id),
  max_attempts integer NOT NULL CHECK (max_attempts BETWEEN 1 AND 10),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX erasure_recoveries_job_v107
  ON platform_attachments.erasure_recoveries(erasure_job_id,created_at);
REVOKE ALL ON platform_attachments.erasure_recoveries FROM PUBLIC;

CREATE FUNCTION platform_attachments.claim_attachment_erasure_job_v107(
  selected_worker_id text
) RETURNS platform_attachments.erasure_jobs
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, platform_attachments
AS $function$
DECLARE selected_job platform_attachments.erasure_jobs%rowtype;
BEGIN
  IF current_user NOT IN ('platform_control_owner','platform_control_owner_preview')
     OR session_user NOT IN ('platform_control_maintenance','platform_control_maintenance_preview')
     OR (current_database()='agent_platform_control') <>
        (session_user='platform_control_maintenance')
  THEN RAISE insufficient_privilege USING MESSAGE='Attachment erasure caller invalid'; END IF;
  IF selected_worker_id IS NULL OR char_length(selected_worker_id) NOT BETWEEN 1 AND 128
  THEN RAISE check_violation USING MESSAGE='Erasure worker invalid'; END IF;

  -- Converge a bounded, non-blocking batch. This includes the last attempt that
  -- crashed and became eligible only when its lease expired.
  WITH exhausted AS (
    SELECT erasure_job_id
    FROM platform_attachments.erasure_jobs
    WHERE attempt_count >= max_attempts AND (
      (state IN ('queued','partial') AND available_at <= clock_timestamp())
      OR (state='running' AND lease_expires_at <= clock_timestamp())
    )
    ORDER BY coalesce(lease_expires_at,available_at),created_at
    FOR UPDATE SKIP LOCKED LIMIT 100
  )
  UPDATE platform_attachments.erasure_jobs job SET
    state='failed',state_reason='erasure_attempts_exhausted',
    claimed_by=NULL,claimed_at=NULL,lease_expires_at=NULL,
    completed_at=clock_timestamp(),
    downstream_cleanup_status=job.downstream_cleanup_status ||
      jsonb_build_object(
        'exhausted',true,'attempt_count',job.attempt_count,
        'max_attempts',job.max_attempts
      )
  FROM exhausted WHERE job.erasure_job_id=exhausted.erasure_job_id;

  SELECT * INTO selected_job FROM platform_attachments.erasure_jobs
  WHERE attempt_count < max_attempts AND (
    (state IN ('queued','partial') AND available_at <= clock_timestamp())
    OR (state='running' AND lease_expires_at <= clock_timestamp())
  )
  ORDER BY coalesce(lease_expires_at,available_at),created_at
  FOR UPDATE SKIP LOCKED LIMIT 1;
  IF NOT FOUND THEN RETURN NULL; END IF;

  PERFORM 1 FROM platform_attachments.attachments
  WHERE attachment_id=selected_job.attachment_id
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE no_data_found USING MESSAGE='Erasure attachment unavailable';
  END IF;
  UPDATE platform_attachments.attachments SET
    state='deleted',state_reason='erasure_pending',
    deleted_at=coalesce(deleted_at,clock_timestamp())
  WHERE attachment_id=selected_job.attachment_id;
  UPDATE platform_attachments.erasure_jobs SET
    state='running',claimed_by=selected_worker_id,claimed_at=clock_timestamp(),
    lease_expires_at=clock_timestamp() + interval '5 minutes',
    attempt_count=attempt_count+1,attempt_token=gen_random_uuid(),
    completed_at=NULL
  WHERE erasure_job_id=selected_job.erasure_job_id RETURNING * INTO selected_job;
  RETURN selected_job;
END
$function$;

CREATE FUNCTION platform_attachments.renew_attachment_erasure_lease_v107(
  selected_erasure_job_id uuid,
  selected_attempt_token uuid
) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, platform_attachments
AS $function$
DECLARE affected integer;
BEGIN
  IF current_user NOT IN ('platform_control_owner','platform_control_owner_preview')
     OR session_user NOT IN ('platform_control_maintenance','platform_control_maintenance_preview')
     OR (current_database()='agent_platform_control') <>
        (session_user='platform_control_maintenance')
  THEN RAISE insufficient_privilege USING MESSAGE='Attachment erasure caller invalid'; END IF;
  IF selected_erasure_job_id IS NULL OR selected_attempt_token IS NULL
  THEN RAISE check_violation USING MESSAGE='Erasure lease invalid'; END IF;
  UPDATE platform_attachments.erasure_jobs SET
    lease_expires_at=clock_timestamp() + interval '5 minutes'
  WHERE erasure_job_id=selected_erasure_job_id
    AND state='running' AND attempt_token=selected_attempt_token
    AND lease_expires_at > clock_timestamp();
  GET DIAGNOSTICS affected = ROW_COUNT;
  RETURN affected=1;
END
$function$;

CREATE FUNCTION platform_attachments.record_attachment_erasure_result_v107(
  selected_erasure_job_id uuid,
  selected_attempt_token uuid,
  selected_state text,
  selected_state_reason text,
  selected_downstream_cleanup_status jsonb
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, platform_attachments
AS $function$
DECLARE
  selected_job platform_attachments.erasure_jobs%rowtype;
  persisted_state text;
  persisted_reason text;
  persisted_status jsonb;
BEGIN
  IF current_user NOT IN ('platform_control_owner','platform_control_owner_preview')
     OR session_user NOT IN ('platform_control_maintenance','platform_control_maintenance_preview')
     OR (current_database()='agent_platform_control') <>
        (session_user='platform_control_maintenance')
  THEN RAISE insufficient_privilege USING MESSAGE='Attachment erasure caller invalid'; END IF;
  IF selected_erasure_job_id IS NULL OR selected_attempt_token IS NULL
     OR selected_state NOT IN ('completed','partial','failed')
     OR selected_state_reason IS NULL OR char_length(selected_state_reason) > 512
     OR jsonb_typeof(selected_downstream_cleanup_status) <> 'object'
  THEN RAISE check_violation USING MESSAGE='Attachment erasure result invalid'; END IF;

  SELECT * INTO selected_job FROM platform_attachments.erasure_jobs
  WHERE erasure_job_id=selected_erasure_job_id AND state='running'
    AND attempt_token=selected_attempt_token
    AND lease_expires_at > clock_timestamp()
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE serialization_failure USING MESSAGE='Erasure attempt unavailable';
  END IF;

  persisted_state := selected_state;
  persisted_reason := selected_state_reason;
  persisted_status := selected_downstream_cleanup_status;
  IF selected_state='partial' AND selected_job.attempt_count >= selected_job.max_attempts THEN
    persisted_state := 'failed';
    persisted_reason := 'erasure_attempts_exhausted';
    persisted_status := persisted_status || jsonb_build_object(
      'exhausted',true,'attempt_count',selected_job.attempt_count,
      'max_attempts',selected_job.max_attempts
    );
  END IF;

  PERFORM platform_attachments.record_attachment_erasure_result_v64(
    selected_erasure_job_id,persisted_state,persisted_reason,persisted_status
  );
  UPDATE platform_attachments.erasure_jobs SET
    claimed_by=NULL,claimed_at=NULL,lease_expires_at=NULL
  WHERE erasure_job_id=selected_erasure_job_id
    AND attempt_token=selected_attempt_token;
END
$function$;

CREATE FUNCTION platform_attachments.recover_attachment_erasure_job_v107(
  selected_erasure_job_id uuid,
  selected_recovery_id uuid,
  selected_max_attempts integer
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, platform_attachments
AS $function$
DECLARE
  selected_job platform_attachments.erasure_jobs%rowtype;
  persisted_recovery platform_attachments.erasure_recoveries%rowtype;
BEGIN
  IF current_user NOT IN ('platform_control_owner','platform_control_owner_preview')
     OR session_user NOT IN ('platform_control_maintenance','platform_control_maintenance_preview')
     OR (current_database()='agent_platform_control') <>
        (session_user='platform_control_maintenance')
  THEN RAISE insufficient_privilege USING MESSAGE='Attachment erasure caller invalid'; END IF;
  IF selected_erasure_job_id IS NULL OR selected_recovery_id IS NULL
     OR selected_max_attempts NOT BETWEEN 1 AND 10
  THEN RAISE check_violation USING MESSAGE='Erasure recovery invalid'; END IF;
  SELECT * INTO persisted_recovery
  FROM platform_attachments.erasure_recoveries
  WHERE recovery_id=selected_recovery_id;
  IF FOUND THEN
    IF persisted_recovery.erasure_job_id<>selected_erasure_job_id
       OR persisted_recovery.max_attempts<>selected_max_attempts
    THEN RAISE check_violation USING MESSAGE='Erasure recovery replay changed'; END IF;
    RETURN selected_erasure_job_id;
  END IF;
  SELECT * INTO selected_job FROM platform_attachments.erasure_jobs
  WHERE erasure_job_id=selected_erasure_job_id FOR UPDATE;
  IF NOT FOUND THEN RAISE no_data_found USING MESSAGE='Erasure job unavailable'; END IF;
  SELECT * INTO persisted_recovery
  FROM platform_attachments.erasure_recoveries
  WHERE recovery_id=selected_recovery_id;
  IF FOUND THEN
    IF persisted_recovery.erasure_job_id<>selected_erasure_job_id
       OR persisted_recovery.max_attempts<>selected_max_attempts
    THEN RAISE check_violation USING MESSAGE='Erasure recovery replay changed'; END IF;
    RETURN selected_erasure_job_id;
  END IF;
  IF selected_job.state<>'failed'
     OR selected_job.state_reason<>'erasure_attempts_exhausted'
  THEN RAISE check_violation USING MESSAGE='Erasure job not recoverable'; END IF;
  INSERT INTO platform_attachments.erasure_recoveries(
    recovery_id,erasure_job_id,max_attempts
  ) VALUES (
    selected_recovery_id,selected_erasure_job_id,selected_max_attempts
  );
  UPDATE platform_attachments.erasure_jobs SET
    state='partial',state_reason='operator_recovery_pending',
    attempt_count=0,max_attempts=selected_max_attempts,
    available_at=clock_timestamp(),claimed_by=NULL,claimed_at=NULL,
    lease_expires_at=NULL,attempt_token=gen_random_uuid(),completed_at=NULL,
    downstream_cleanup_status=downstream_cleanup_status || jsonb_build_object(
      'last_recovery_id',selected_recovery_id::text,
      'recovery_count',(SELECT count(*) FROM platform_attachments.erasure_recoveries
        WHERE erasure_job_id=selected_erasure_job_id)
    )
  WHERE erasure_job_id=selected_erasure_job_id;
  RETURN selected_erasure_job_id;
END
$function$;

REVOKE EXECUTE ON FUNCTION
  platform_attachments.claim_attachment_erasure_job_v64(text),
  platform_attachments.record_attachment_erasure_result_v64(uuid,text,text,jsonb)
FROM platform_control_maintenance,platform_control_maintenance_preview;

REVOKE ALL ON FUNCTION
  platform_attachments.claim_attachment_erasure_job_v107(text),
  platform_attachments.renew_attachment_erasure_lease_v107(uuid,uuid),
  platform_attachments.record_attachment_erasure_result_v107(uuid,uuid,text,text,jsonb),
  platform_attachments.recover_attachment_erasure_job_v107(uuid,uuid,integer)
FROM PUBLIC;

DO $migration$
DECLARE maintenance_role text;
BEGIN
  maintenance_role := CASE current_user
    WHEN 'platform_control_owner' THEN 'platform_control_maintenance'
    WHEN 'platform_control_owner_preview' THEN 'platform_control_maintenance_preview'
    ELSE NULL
  END;
  IF maintenance_role IS NULL THEN
    RAISE EXCEPTION 'unsupported attachment migration owner';
  END IF;
  EXECUTE format(
    'GRANT EXECUTE ON FUNCTION '
    'platform_attachments.claim_attachment_erasure_job_v107(text),'
    'platform_attachments.renew_attachment_erasure_lease_v107(uuid,uuid),'
    'platform_attachments.record_attachment_erasure_result_v107(uuid,uuid,text,text,jsonb),'
    'platform_attachments.recover_attachment_erasure_job_v107(uuid,uuid,integer) TO %I',
    maintenance_role
  );
END
$migration$;
