-- Close the attachment object-key set before physical erasure. References stay
-- recoverable until record_attachment_erasure_result_v64 completes every key.
CREATE OR REPLACE FUNCTION platform_attachments.claim_attachment_erasure_job_v64(
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

  SELECT * INTO selected_job FROM platform_attachments.erasure_jobs
  WHERE state IN ('queued','partial') AND available_at <= now()
  ORDER BY available_at,created_at FOR UPDATE SKIP LOCKED LIMIT 1;
  IF NOT FOUND THEN RETURN NULL; END IF;

  PERFORM 1 FROM platform_attachments.attachments
  WHERE attachment_id=selected_job.attachment_id
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE no_data_found USING MESSAGE='Erasure attachment unavailable';
  END IF;
  UPDATE platform_attachments.attachments SET
    state='deleted',state_reason='erasure_pending',
    deleted_at=coalesce(deleted_at,now())
  WHERE attachment_id=selected_job.attachment_id;
  UPDATE platform_attachments.erasure_jobs SET
    state='running',claimed_by=selected_worker_id,claimed_at=now(),
    attempt_count=attempt_count+1
  WHERE erasure_job_id=selected_job.erasure_job_id RETURNING * INTO selected_job;
  RETURN selected_job;
END
$function$;

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
    'GRANT SELECT (attachment_id, processing_job_id, job_kind, derivative_kind) '
    'ON platform_attachments.processing_jobs TO %I',
    maintenance_role
  );
END
$migration$;
