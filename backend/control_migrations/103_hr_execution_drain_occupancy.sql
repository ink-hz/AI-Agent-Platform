-- Additive correction to 102; preserve its ledger checksum and control API.
-- Drain terminality does not claim business success or rewrite old job records.
-- CREATE OR REPLACE preserves the existing function owner and EXECUTE grants.
CREATE OR REPLACE FUNCTION platform_control.hr_execution_cutover_counts_v102()
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
     OR to_regclass('platform_control.direct_command_bindings') IS NULL
     OR to_regclass('platform_control.mission_runs') IS NULL
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
        WHERE j.agent_id='hr-bot' AND (
          -- A terminal Relay status does not acknowledge executor stop.
          (j.stop_requested_status IS NOT NULL AND j.stop_acknowledged_at IS NULL)
          OR EXISTS (
            SELECT 1 FROM platform_control.conversation_turns jt
            WHERE (
              EXISTS (SELECT 1 FROM platform_control.direct_command_bindings jb
                      JOIN platform_control.turn_attempts ja USING(attempt_id)
                      WHERE jb.job_id=j.job_id AND ja.turn_id=jt.turn_id
                        AND jb.conversation_id=jt.conversation_id)
              OR EXISTS (SELECT 1 FROM platform_control.mission_runs jr
                         WHERE jr.run_id=j.run_id AND jr.mission_id=jt.mission_id)
            ) AND (
              jt.status NOT IN ('completed','failed','cancelled','interrupted')
              OR EXISTS (SELECT 1 FROM platform_control.turn_attempts ja
                         WHERE ja.turn_id=jt.turn_id
                           AND ja.status NOT IN ('completed','failed','cancelled','interrupted'))
            )
          )
          OR CASE WHEN j.job_kind='worker_direct_v5' THEN
            -- v5 jobs store immutable transport envelopes. Normal fenced result
            -- publication terminates Attempt/Turn while this row stays queued.
            -- Exclude only an exact, terminal lineage; orphan/unknown blocks.
            j.status NOT IN ('queued','completed','failed','cancelled','interrupted')
            OR NOT EXISTS (
              SELECT 1 FROM platform_control.direct_command_bindings jb
              JOIN platform_control.turn_attempts ja USING(attempt_id)
              JOIN platform_control.conversation_turns jt USING(turn_id)
              WHERE jb.job_id=j.job_id AND ja.transport_run_id=j.run_id
                AND jb.conversation_id=jt.conversation_id
                AND ja.executor_kind='worker_direct' AND jt.execution_owner='worker_direct'
                AND ja.status IN ('completed','failed','cancelled','interrupted')
                AND jt.status IN ('completed','failed','cancelled','interrupted')
            )
          ELSE
            j.status NOT IN ('completed','failed','cancelled','interrupted')
            OR (j.status='interrupted' AND j.terminal_at IS NULL)
          END
        ))
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
