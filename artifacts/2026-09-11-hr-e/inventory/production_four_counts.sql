BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout='15s';
SET LOCAL lock_timeout='2s';
SELECT json_build_object(
 'transaction_read_only',current_setting('transaction_read_only')='on',
 'role_verified',current_user='platform_owner',
 'old_result_projections',(SELECT count(state) FROM platform_hr.hr_task_result_projections),
 'intelligence_bundles',(SELECT count(bundle_id) FROM platform_hr.intelligence_bundles),
 'intelligence_jobs',(SELECT count(job_id) FROM platform_hr.intelligence_bundle_jobs),
 'intelligence_current',(SELECT count(bundle_id) FROM platform_hr.intelligence_current_publication)
);
ROLLBACK;
