\timing on
BEGIN;
SET LOCAL ROLE platform_control_maintenance;
SET LOCAL lock_timeout='2s';
SET LOCAL statement_timeout='3s';
SELECT pg_advisory_xact_lock(79518187931988);
SELECT json_build_object('session_user',session_user,'current_user',current_user,'counts',row_to_json(c)) FROM platform_control.hr_execution_cutover_counts_v102() c;
COMMIT;
