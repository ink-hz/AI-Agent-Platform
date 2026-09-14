BEGIN;
SET LOCAL ROLE platform_control_maintenance;
SET LOCAL lock_timeout='2s';
SET LOCAL statement_timeout='3s';
SELECT json_build_object('session_user',session_user,'current_user',current_user,'result',row_to_json(r)) FROM platform_control.initialize_hr_execution_cutover_v102('2e3bf81c-7f0e-4ff1-bf49-996e3a584bbf'::uuid) r;
COMMIT;
