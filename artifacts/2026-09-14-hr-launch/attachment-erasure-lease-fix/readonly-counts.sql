BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '3s';
SET LOCAL lock_timeout = '1s';

SELECT state,
       count(*) AS job_count,
       count(*) FILTER (
         WHERE state='running' AND lease_expires_at <= clock_timestamp()
       ) AS expired_running_count,
       count(*) FILTER (
         WHERE state='failed' AND state_reason='erasure_attempts_exhausted'
       ) AS exhausted_count
FROM platform_attachments.erasure_jobs
GROUP BY state
ORDER BY state;

SELECT erasure_job_id,
       attachment_id,
       state,
       state_reason,
       attempt_count,
       max_attempts,
       claimed_by,
       claimed_at,
       lease_expires_at,
       CASE
         WHEN state='running' AND lease_expires_at <= clock_timestamp()
         THEN 'expired'
         WHEN state='running' THEN 'active'
         ELSE 'not_running'
       END AS lease_state
FROM platform_attachments.erasure_jobs
WHERE state IN ('queued','running','partial')
   OR (state='failed' AND state_reason='erasure_attempts_exhausted')
ORDER BY created_at,erasure_job_id;

COMMIT;
