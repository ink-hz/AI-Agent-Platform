BEGIN;
SET LOCAL ROLE platform_control_maintenance;
SET LOCAL lock_timeout='2s';
SET LOCAL statement_timeout='3s';
SELECT json_build_object('session_user',session_user,'current_user',current_user,'result',row_to_json(r)) FROM platform_control.transition_hr_execution_cutover_v102('draining_legacy','9e1ea788-bd77-4efa-b81a-47ca78eb9a37'::uuid) r;
COMMIT;
