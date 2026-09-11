CREATE TABLE platform_hr_agent.candidate_interview_records (
 record_id uuid PRIMARY KEY, owner_id uuid NOT NULL, candidate_id uuid NOT NULL,
 attachment_id uuid NOT NULL, material_ref jsonb NOT NULL, source_identity jsonb NOT NULL,
 position_id uuid, interview_plan_ref jsonb,
 sealed_metadata bytea NOT NULL, sealed_metadata_key_version integer NOT NULL CHECK(sealed_metadata_key_version>0),
 created_by_operation uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(owner_id,record_id),
 FOREIGN KEY(owner_id,candidate_id) REFERENCES platform_hr_agent.candidates(owner_id,candidate_id),
 FOREIGN KEY(attachment_id,owner_id) REFERENCES platform_attachments.attachments(attachment_id,owner_internal_user_id),
 FOREIGN KEY(owner_id,candidate_id,position_id) REFERENCES platform_hr_agent.candidate_positions(owner_id,candidate_id,position_id),
 FOREIGN KEY(owner_id,created_by_operation) REFERENCES platform_hr_agent.operations(owner_id,operation_id)
);
CREATE INDEX hr_agent_interview_record_page ON platform_hr_agent.candidate_interview_records(owner_id,candidate_id,created_at,record_id);
ALTER TABLE platform_hr_agent.personal_materials ADD COLUMN registered_by_record uuid;
ALTER TABLE platform_hr_agent.personal_materials ALTER COLUMN registered_by_item DROP NOT NULL;
ALTER TABLE platform_hr_agent.personal_materials ADD CONSTRAINT personal_materials_one_registration CHECK ((registered_by_item IS NULL) <> (registered_by_record IS NULL));
ALTER TABLE platform_hr_agent.personal_materials ADD FOREIGN KEY(owner_id,registered_by_record) REFERENCES platform_hr_agent.candidate_interview_records(owner_id,record_id);

CREATE FUNCTION platform_hr_agent.lock_user_input_source(selected_owner uuid, selected_attachment uuid, expected_identity jsonb)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform_hr_agent AS $$
DECLARE a record; current_identity jsonb;
BEGIN
 IF session_user NOT IN ('platform_control_app','platform_control_app_preview') THEN RAISE insufficient_privilege; END IF;
 SELECT attachment.*,upload.write_attempt_id INTO a FROM platform_attachments.attachments attachment
 LEFT JOIN platform_attachments.uploads upload USING(attachment_id)
 WHERE attachment.owner_internal_user_id=selected_owner AND attachment.attachment_id=selected_attachment
 FOR SHARE OF attachment;
 IF NOT FOUND OR a.source_kind<>'user_input' OR a.state<>'ready' OR a.retained_until<=now()
    OR EXISTS(SELECT 1 FROM platform_attachments.erasure_jobs e WHERE e.attachment_id=selected_attachment) THEN RETURN false; END IF;
 current_identity := jsonb_build_object('immutable_locator',a.immutable_locator::text,'write_attempt_id',coalesce(a.write_attempt_id::text,'None'),'detected_mime',a.detected_mime::text,'size_bytes',a.size_bytes::text,'sha256',encode(a.sha256,'hex'));
 RETURN current_identity=expected_identity;
END $$;
REVOKE ALL ON FUNCTION platform_hr_agent.lock_user_input_source(uuid,uuid,jsonb) FROM PUBLIC;
DO $$ DECLARE app_role text; BEGIN
 app_role := CASE current_user WHEN 'platform_control_owner' THEN 'platform_control_app' WHEN 'platform_control_owner_preview' THEN 'platform_control_app_preview' ELSE NULL END;
 IF app_role IS NULL THEN RAISE EXCEPTION 'unsupported HR migration owner'; END IF;
 REVOKE ALL ON platform_hr_agent.candidate_interview_records FROM PUBLIC;
 EXECUTE format('GRANT SELECT,INSERT ON platform_hr_agent.candidate_interview_records TO %I',app_role);
 EXECUTE format('GRANT EXECUTE ON FUNCTION platform_hr_agent.lock_user_input_source(uuid,uuid,jsonb) TO %I',app_role);
END $$;
