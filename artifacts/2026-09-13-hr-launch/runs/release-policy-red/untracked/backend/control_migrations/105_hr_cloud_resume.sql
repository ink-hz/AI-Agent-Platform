-- Resume the same cloud lane after an operational pause. Existing work stays
-- with its owner; legacy occupancy must still be zero. Same lock, permission
-- and request-id replay contract as 102. Historical migrations stay unchanged.

CREATE OR REPLACE FUNCTION platform_control.transition_hr_execution_cutover_v102(
  selected_phase text, selected_request_id uuid
) RETURNS platform_control.hr_execution_cutover
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, platform_control, platform_hr, platform_hr_agent
AS $function$
DECLARE current_row platform_control.hr_execution_cutover; counts record; prior record;
BEGIN
  IF selected_request_id IS NULL THEN RAISE EXCEPTION 'request id required'; END IF;
  PERFORM pg_advisory_xact_lock(79518187931988);
  SELECT * INTO prior FROM platform_control.hr_execution_cutover_operations
   WHERE request_id=selected_request_id;
  IF FOUND THEN
    IF prior.target_phase<>selected_phase THEN RAISE EXCEPTION 'HR cutover request replay mismatch'; END IF;
    current_row.singleton:=true; current_row.phase:=prior.target_phase;
    current_row.epoch:=prior.result_epoch; current_row.transition_request_id:=selected_request_id;
    current_row.transitioned_at:=prior.result_transitioned_at;
    current_row.row_version:=prior.result_row_version; RETURN current_row;
  END IF;
  SELECT * INTO current_row FROM platform_control.hr_execution_cutover WHERE singleton FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'hr execution cutover is not initialized'; END IF;
  IF (current_row.phase,selected_phase) NOT IN (
    ('legacy','draining_legacy'),('draining_legacy','cloud'),
    ('cloud','draining_cloud'),('draining_cloud','legacy'),
    ('draining_cloud','cloud')) THEN
    RAISE EXCEPTION 'invalid HR execution transition';
  END IF;
  IF selected_phase IN ('cloud','legacy') THEN
    SELECT * INTO counts FROM platform_control.hr_execution_cutover_counts_v102();
    IF (selected_phase='cloud' AND counts.legacy_nonterminal<>0)
       OR (selected_phase='legacy' AND counts.cloud_nonterminal<>0) THEN
      RAISE EXCEPTION 'HR execution drain is incomplete';
    END IF;
  END IF;
  UPDATE platform_control.hr_execution_cutover
     SET phase=selected_phase,epoch=epoch+1,transition_request_id=selected_request_id,
         transitioned_at=clock_timestamp(),row_version=row_version+1
   WHERE singleton RETURNING * INTO current_row;
  INSERT INTO platform_control.hr_execution_cutover_operations
    (request_id,target_phase,result_epoch,result_transitioned_at,result_row_version)
    VALUES(selected_request_id,selected_phase,current_row.epoch,current_row.transitioned_at,current_row.row_version);
  RETURN current_row;
END
$function$;
