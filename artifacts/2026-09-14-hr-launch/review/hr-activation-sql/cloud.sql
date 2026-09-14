BEGIN;
SET LOCAL ROLE platform_control_maintenance;
SET LOCAL lock_timeout='2s';
SET LOCAL statement_timeout='3s';
SELECT json_build_object('session_user',session_user,'current_user',current_user,'result',row_to_json(r)) FROM platform_control.transition_hr_execution_cutover_v102('cloud','3a61df7e-9ab1-41ce-93f0-a9eedc8f0a3f'::uuid) r;
COMMIT;
