\set ON_ERROR_STOP on
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '10s';
SET LOCAL lock_timeout = '1s';

-- Aggregate only: no attachment/job/user IDs, ciphertext, object references, or source text.
SELECT state, count(*) AS jobs,
       min(created_at) AS oldest_created_at,
       max(created_at) AS newest_created_at
FROM platform_attachments.erasure_jobs
GROUP BY state ORDER BY state;

SELECT state,
       CASE
         WHEN age(now(), coalesce(claimed_at, created_at)) < interval '15 minutes' THEN '<15m'
         WHEN age(now(), coalesce(claimed_at, created_at)) < interval '1 hour' THEN '15m-1h'
         WHEN age(now(), coalesce(claimed_at, created_at)) < interval '24 hours' THEN '1h-24h'
         ELSE '>=24h'
       END AS age_bucket,
       count(*) AS jobs,
       min(attempt_count) AS min_attempts,
       max(attempt_count) AS max_attempts
FROM platform_attachments.erasure_jobs
GROUP BY state, age_bucket ORDER BY state, age_bucket;

SELECT job.state AS job_state, attachment.state AS attachment_state,
       count(*) AS pairs,
       coalesce(sum(attachment.size_bytes), 0) AS declared_original_bytes
FROM platform_attachments.erasure_jobs job
JOIN platform_attachments.attachments attachment USING (attachment_id)
GROUP BY job.state, attachment.state ORDER BY job.state, attachment.state;

WITH selected AS (
  SELECT DISTINCT attachment_id FROM platform_attachments.erasure_jobs
), attempts AS (
  SELECT attachment_id, count(*) AS rows
  FROM platform_attachments.upload_write_attempts
  WHERE attachment_id IN (SELECT attachment_id FROM selected)
  GROUP BY attachment_id
), derivatives AS (
  SELECT attachment_id, count(*) AS rows,
         coalesce(sum(size_bytes), 0) AS bytes
  FROM platform_attachments.derivatives
  WHERE attachment_id IN (SELECT attachment_id FROM selected)
  GROUP BY attachment_id
)
SELECT count(*) FILTER (WHERE upload.attachment_id IS NOT NULL) AS upload_rows,
       coalesce(sum(attempts.rows), 0) AS write_attempt_rows,
       coalesce(sum(derivatives.rows), 0) AS derivative_rows,
       coalesce(sum(attachment.size_bytes), 0) AS declared_original_bytes,
       coalesce(sum(derivatives.bytes), 0) AS declared_derivative_bytes
FROM selected
JOIN platform_attachments.attachments attachment USING (attachment_id)
LEFT JOIN platform_attachments.uploads upload USING (attachment_id)
LEFT JOIN attempts USING (attachment_id)
LEFT JOIN derivatives USING (attachment_id);

SELECT version, sha256, applied_at,
       sha256 = '15355874fce1ea58d00056eb07233a0fb3ef4a3cd7e807ea6e6608deb3668177'
         AS checksum_matches_reviewed_file
FROM platform_control.schema_migrations WHERE version = 100;

WITH expected(role_name) AS (
  VALUES (CASE WHEN current_database() = 'agent_platform_control_preview'
               THEN 'platform_control_maintenance_preview'
               ELSE 'platform_control_maintenance' END)
)
SELECT has_column_privilege(role_name,
         'platform_attachments.uploads','attachment_id','SELECT') AS uploads_attachment,
       has_column_privilege(role_name,
         'platform_attachments.uploads','write_attempt_id','SELECT') AS uploads_attempt,
       has_column_privilege(role_name,
         'platform_attachments.upload_write_attempts','attachment_id','SELECT') AS attempts_attachment,
       has_column_privilege(role_name,
         'platform_attachments.upload_write_attempts','attempt_id','SELECT') AS attempts_id,
       has_column_privilege(role_name,
         'platform_attachments.upload_write_attempts','object_ref_ciphertext','SELECT') AS attempts_ciphertext,
       has_column_privilege(role_name,
         'platform_attachments.upload_write_attempts','object_ref_key_version','SELECT') AS attempts_key_version
FROM expected;
ROLLBACK;
