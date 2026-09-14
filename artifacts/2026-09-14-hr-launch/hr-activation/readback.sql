BEGIN READ ONLY;
SET LOCAL lock_timeout='2s';
SET LOCAL statement_timeout='3s';
SELECT json_build_object(
 'database',current_database(), 'session_user',session_user,
 'cutover',(SELECT row_to_json(c) FROM platform_control.hr_execution_cutover c WHERE singleton),
 'operations',(SELECT json_agg(row_to_json(o) ORDER BY result_epoch) FROM platform_control.hr_execution_cutover_operations o WHERE request_id IN ('2e3bf81c-7f0e-4ff1-bf49-996e3a584bbf','9e1ea788-bd77-4efa-b81a-47ca78eb9a37','3a61df7e-9ab1-41ce-93f0-a9eedc8f0a3f')),
 'erasure_states',(SELECT json_agg(row_to_json(e) ORDER BY state) FROM (SELECT state,count(*) as count FROM platform_attachments.erasure_jobs GROUP BY state) e),
 'erasure_expired_running',(SELECT count(*) FROM platform_attachments.erasure_jobs WHERE state='running' AND lease_expires_at<=clock_timestamp()),
 'erasure_exhausted',(SELECT count(*) FROM platform_attachments.erasure_jobs WHERE state_reason='erasure_attempts_exhausted'),
 'root107',(SELECT sha256 FROM platform_control.schema_migrations WHERE version=107),
 'migrator_sessions',(SELECT count(*) FROM pg_stat_activity WHERE usename IN ('platform_control_migrator','platform_control_migrator_preview'))
);
COMMIT;
