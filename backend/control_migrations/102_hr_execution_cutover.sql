CREATE TABLE platform_control.hr_execution_cutover (
  singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
  phase text NOT NULL CHECK (phase IN ('legacy','draining_legacy','cloud','draining_cloud')),
  epoch bigint NOT NULL CHECK (epoch > 0),
  transition_request_id uuid NOT NULL UNIQUE,
  transitioned_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  row_version bigint NOT NULL DEFAULT 1 CHECK (row_version > 0)
);

CREATE TABLE platform_control.hr_execution_cutover_operations (
  request_id uuid PRIMARY KEY,
  target_phase text NOT NULL CHECK (target_phase IN ('legacy','draining_legacy','cloud','draining_cloud')),
  result_epoch bigint NOT NULL,
  result_transitioned_at timestamptz NOT NULL,
  result_row_version bigint NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE FUNCTION platform_control.hr_execution_cutover_counts_v102()
RETURNS TABLE(legacy_nonterminal bigint, cloud_nonterminal bigint)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, platform_control, platform_hr, platform_hr_agent
AS $function$
BEGIN
  -- Missing coverage is an error, never an inferred zero. These are all
  -- required by the release that may initialize this gate.
  IF to_regclass('platform_control.conversations') IS NULL
     OR to_regclass('platform_control.turn_attempts') IS NULL
     OR to_regclass('platform_control.execution_jobs') IS NULL
     OR to_regclass('platform_hr.candidate_drafts') IS NULL
     OR to_regclass('platform_hr.candidate_draft_batches') IS NULL
     OR to_regclass('platform_hr.position_conversations') IS NULL
     OR to_regclass('platform_hr.candidate_draft_processing_attempts') IS NULL
     OR to_regclass('platform_hr_agent.works') IS NULL
     OR to_regclass('platform_hr_agent.material_parses') IS NULL
     OR to_regclass('platform_hr_agent.candidate_intake_items') IS NULL THEN
    RAISE EXCEPTION 'hr execution inventory unavailable';
  END IF;

  RETURN QUERY
  SELECT
    (SELECT count(DISTINCT c.conversation_id)
       FROM platform_control.conversations c
       LEFT JOIN platform_control.conversation_turns t USING (conversation_id)
       LEFT JOIN platform_control.turn_attempts a USING (turn_id)
      WHERE ((c.mode='direct_agent' AND c.direct_agent_id='hr-bot')
             OR EXISTS (SELECT 1 FROM platform_hr.position_conversations pc
                         WHERE pc.conversation_id=c.conversation_id))
        AND (t.status NOT IN ('completed','failed','cancelled','interrupted')
             OR a.status NOT IN ('completed','failed','cancelled','interrupted')))
    + (SELECT count(*) FROM platform_control.execution_jobs j
        WHERE j.agent_id='hr-bot' AND j.status NOT IN ('completed','failed','cancelled','interrupted'))
    + (SELECT count(*) FROM platform_hr.candidate_drafts d
        WHERE d.state NOT IN ('ready','failed','confirmed','dismissed'))
    + (SELECT count(*) FROM platform_hr.candidate_draft_batches b
        WHERE cardinality(b.attachment_ids) <>
          (SELECT count(*) FROM platform_hr.candidate_drafts d
            WHERE d.owner_internal_user_id=b.owner_internal_user_id
              AND d.batch_request_id=b.batch_request_id))
    + (SELECT count(*) FROM platform_hr.candidate_draft_processing_attempts p
        WHERE p.state NOT IN ('completed','failed','expired')),
    (SELECT count(*) FROM platform_hr_agent.works w
      WHERE w.state NOT IN ('completed','cancelled','failed'))
    + (SELECT count(*) FROM platform_hr_agent.material_parses p
        WHERE p.state NOT IN ('ready','failed','unsupported'))
    + (SELECT count(*) FROM platform_hr_agent.candidate_intake_items i
        WHERE i.state NOT IN ('awaiting_review','failed','confirmed'));
EXCEPTION WHEN undefined_table OR undefined_column THEN
  RAISE EXCEPTION 'hr execution inventory unavailable';
END
$function$;

CREATE FUNCTION platform_control.verify_legacy_candidate_parser_admission_v102(
  selected_attempt_id uuid, selected_owner_id uuid
) RETURNS boolean
LANGUAGE sql SECURITY DEFINER
SET search_path = pg_catalog, platform_hr
AS $function$
  SELECT EXISTS (
    SELECT 1 FROM platform_hr.candidate_draft_processing_attempts
     WHERE attempt_id=selected_attempt_id
       AND owner_internal_user_id=selected_owner_id
       AND state='processing'
     FOR SHARE
  )
$function$;

CREATE FUNCTION platform_control.initialize_hr_execution_cutover_v102(
  selected_request_id uuid
) RETURNS platform_control.hr_execution_cutover
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, platform_control, platform_hr, platform_hr_agent
AS $function$
DECLARE counts record; result platform_control.hr_execution_cutover; prior record;
BEGIN
  IF selected_request_id IS NULL THEN RAISE EXCEPTION 'request id required'; END IF;
  PERFORM pg_advisory_xact_lock(79518187931988);
  SELECT * INTO prior FROM platform_control.hr_execution_cutover_operations
   WHERE request_id=selected_request_id;
  IF FOUND THEN
    IF prior.target_phase<>'legacy' THEN RAISE EXCEPTION 'HR cutover request replay mismatch'; END IF;
    result.singleton:=true; result.phase:=prior.target_phase; result.epoch:=prior.result_epoch;
    result.transition_request_id:=selected_request_id;
    result.transitioned_at:=prior.result_transitioned_at; result.row_version:=prior.result_row_version;
    RETURN result;
  END IF;
  IF EXISTS (SELECT 1 FROM platform_control.hr_execution_cutover) THEN
    SELECT * INTO result FROM platform_control.hr_execution_cutover;
    IF result.transition_request_id <> selected_request_id THEN
      RAISE EXCEPTION 'hr execution cutover already initialized';
    END IF;
    RETURN result;
  END IF;
  SELECT * INTO counts FROM platform_control.hr_execution_cutover_counts_v102();
  IF counts.cloud_nonterminal <> 0 THEN
    RAISE EXCEPTION 'cloud HR work must be terminal before legacy initialization';
  END IF;
  INSERT INTO platform_control.hr_execution_cutover(singleton,phase,epoch,transition_request_id)
  VALUES(true,'legacy',1,selected_request_id) RETURNING * INTO result;
  INSERT INTO platform_control.hr_execution_cutover_operations
    (request_id,target_phase,result_epoch,result_transitioned_at,result_row_version)
    VALUES(selected_request_id,'legacy',result.epoch,result.transitioned_at,result.row_version);
  RETURN result;
END
$function$;

CREATE FUNCTION platform_control.transition_hr_execution_cutover_v102(
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
    ('cloud','draining_cloud'),('draining_cloud','legacy')) THEN
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

REVOKE ALL ON platform_control.hr_execution_cutover FROM PUBLIC;
REVOKE ALL ON platform_control.hr_execution_cutover_operations FROM PUBLIC;
REVOKE ALL ON FUNCTION platform_control.hr_execution_cutover_counts_v102() FROM PUBLIC;
REVOKE ALL ON FUNCTION platform_control.verify_legacy_candidate_parser_admission_v102(uuid,uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION platform_control.initialize_hr_execution_cutover_v102(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION platform_control.transition_hr_execution_cutover_v102(text,uuid) FROM PUBLIC;

DO $grant$
DECLARE app_role text; maintenance_role text;
BEGIN
  app_role := CASE current_user WHEN 'platform_control_owner' THEN 'platform_control_app'
    WHEN 'platform_control_owner_preview' THEN 'platform_control_app_preview' END;
  maintenance_role := CASE current_user WHEN 'platform_control_owner' THEN 'platform_control_maintenance'
    WHEN 'platform_control_owner_preview' THEN 'platform_control_maintenance_preview' END;
  IF app_role IS NULL OR maintenance_role IS NULL THEN
    RAISE EXCEPTION 'unsupported HR cutover migration owner';
  END IF;
  EXECUTE format('GRANT SELECT ON platform_control.hr_execution_cutover TO %I',app_role);
  EXECUTE format('GRANT EXECUTE ON FUNCTION platform_control.verify_legacy_candidate_parser_admission_v102(uuid,uuid) TO %I',app_role);
  EXECUTE format('GRANT EXECUTE ON FUNCTION platform_control.hr_execution_cutover_counts_v102(), platform_control.initialize_hr_execution_cutover_v102(uuid), platform_control.transition_hr_execution_cutover_v102(text,uuid) TO %I',maintenance_role);
END
$grant$;
