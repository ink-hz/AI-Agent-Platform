-- Independent encrypted C1 intake. Never queues legacy candidate parser work.
CREATE TABLE platform_hr_agent.candidate_batches (
 batch_id uuid PRIMARY KEY, owner_id uuid NOT NULL, position_id uuid,
 sealed_request bytea NOT NULL, sealed_request_key_version integer NOT NULL CHECK(sealed_request_key_version>0),
 created_by_operation uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(owner_id,batch_id), FOREIGN KEY(owner_id,created_by_operation) REFERENCES platform_hr_agent.operations(owner_id,operation_id)
);
CREATE TABLE platform_hr_agent.candidate_intake_items (
 item_id uuid PRIMARY KEY, owner_id uuid NOT NULL, batch_id uuid NOT NULL, attachment_id uuid NOT NULL,
 ordinal integer NOT NULL CHECK(ordinal>=0), source_identity jsonb NOT NULL,
 state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','parsing','profiling','awaiting_review','failed','confirmed')),
 generation bigint NOT NULL DEFAULT 1 CHECK(generation>0), row_version bigint NOT NULL DEFAULT 1 CHECK(row_version>0),
 work_id uuid, result_ref jsonb, error_code text, failed_stage text CHECK(failed_stage IN ('parse','profile')),
 sealed_details bytea NOT NULL, sealed_details_key_version integer NOT NULL CHECK(sealed_details_key_version>0),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(owner_id,item_id), UNIQUE(owner_id,batch_id,attachment_id), UNIQUE(batch_id,ordinal),
 FOREIGN KEY(owner_id,batch_id) REFERENCES platform_hr_agent.candidate_batches(owner_id,batch_id),
 FOREIGN KEY(attachment_id,owner_id) REFERENCES platform_attachments.attachments(attachment_id,owner_internal_user_id),
 FOREIGN KEY(owner_id,work_id) REFERENCES platform_hr_agent.works(owner_id,work_id),
 CHECK((state='failed')=(error_code IS NOT NULL)), CHECK((state='failed')=(failed_stage IS NOT NULL))
);
CREATE TABLE platform_hr_agent.personal_materials (
 owner_id uuid NOT NULL, attachment_id uuid NOT NULL, source_identity jsonb NOT NULL,
 registered_by_item uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(owner_id,attachment_id),
 FOREIGN KEY(owner_id,registered_by_item) REFERENCES platform_hr_agent.candidate_intake_items(owner_id,item_id),
 FOREIGN KEY(attachment_id,owner_id) REFERENCES platform_attachments.attachments(attachment_id,owner_internal_user_id)
);
CREATE TABLE platform_hr_agent.candidates (
 candidate_id uuid PRIMARY KEY, owner_id uuid NOT NULL,
 sealed_profile bytea NOT NULL, sealed_profile_key_version integer NOT NULL CHECK(sealed_profile_key_version>0),
 created_by_operation uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(owner_id,candidate_id), FOREIGN KEY(owner_id,created_by_operation) REFERENCES platform_hr_agent.operations(owner_id,operation_id)
);
CREATE TABLE platform_hr_agent.candidate_documents (
 document_id uuid PRIMARY KEY, owner_id uuid NOT NULL, candidate_id uuid NOT NULL,
 item_id uuid NOT NULL, attachment_id uuid NOT NULL, source_ref jsonb NOT NULL, result_ref jsonb NOT NULL,
 sealed_review bytea NOT NULL, sealed_review_key_version integer NOT NULL CHECK(sealed_review_key_version>0),
 confirmed_by uuid NOT NULL CHECK(confirmed_by=owner_id), created_by_operation uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(owner_id,item_id), UNIQUE(owner_id,document_id),
 FOREIGN KEY(owner_id,candidate_id) REFERENCES platform_hr_agent.candidates(owner_id,candidate_id),
 FOREIGN KEY(owner_id,item_id) REFERENCES platform_hr_agent.candidate_intake_items(owner_id,item_id),
 FOREIGN KEY(attachment_id,owner_id) REFERENCES platform_attachments.attachments(attachment_id,owner_internal_user_id),
 FOREIGN KEY(owner_id,created_by_operation) REFERENCES platform_hr_agent.operations(owner_id,operation_id)
);
CREATE TABLE platform_hr_agent.candidate_positions (
 owner_id uuid NOT NULL, candidate_id uuid NOT NULL, position_id uuid NOT NULL,
 created_by_operation uuid NOT NULL, PRIMARY KEY(owner_id,candidate_id,position_id),
 FOREIGN KEY(owner_id,candidate_id) REFERENCES platform_hr_agent.candidates(owner_id,candidate_id),
 FOREIGN KEY(position_id,owner_id) REFERENCES platform_hr.positions(position_id,owner_internal_user_id),
 FOREIGN KEY(owner_id,created_by_operation) REFERENCES platform_hr_agent.operations(owner_id,operation_id)
);
CREATE INDEX hr_agent_candidate_item_progress ON platform_hr_agent.candidate_intake_items(state,updated_at,item_id);
CREATE INDEX hr_agent_candidate_page ON platform_hr_agent.candidates(owner_id,created_at,candidate_id);
-- Parser attempt numbers stay monotonic across explicit retries: old writes never regain a fence.
ALTER TABLE platform_hr_agent.material_parses ADD COLUMN retry_generation bigint NOT NULL DEFAULT 0 CHECK(retry_generation>=0);
ALTER TABLE platform_hr_agent.material_parses ADD COLUMN generation_attempts integer NOT NULL DEFAULT 0 CHECK(generation_attempts>=0);
DO $$ DECLARE app_role text; selected text; BEGIN
 app_role := CASE current_user WHEN 'platform_control_owner' THEN 'platform_control_app' WHEN 'platform_control_owner_preview' THEN 'platform_control_app_preview' ELSE NULL END;
 IF app_role IS NULL THEN RAISE EXCEPTION 'unsupported HR migration owner'; END IF;
 FOREACH selected IN ARRAY ARRAY['candidate_batches','candidate_intake_items','personal_materials','candidates','candidate_documents','candidate_positions'] LOOP
  EXECUTE format('REVOKE ALL ON platform_hr_agent.%I FROM PUBLIC',selected);
  EXECUTE format('GRANT SELECT,INSERT ON platform_hr_agent.%I TO %I',selected,app_role);
 END LOOP;
 EXECUTE format('GRANT UPDATE ON platform_hr_agent.candidate_intake_items TO %I',app_role);
END $$;
-- App has SELECT-only attachment privileges. A narrow definer can lock and verify,
-- without granting arbitrary attachment UPDATE just for FOR SHARE.
CREATE FUNCTION platform_hr_agent.lock_candidate_source(selected_owner uuid, selected_attachment uuid, expected_identity jsonb)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,platform_hr_agent AS $$
DECLARE a record; current_identity jsonb;
BEGIN
 IF session_user NOT IN ('platform_control_app','platform_control_app_preview') THEN RAISE insufficient_privilege; END IF;
 SELECT attachment.*,upload.write_attempt_id INTO a FROM platform_attachments.attachments attachment
 LEFT JOIN platform_attachments.uploads upload USING(attachment_id)
 WHERE attachment.owner_internal_user_id=selected_owner AND attachment.attachment_id=selected_attachment
 FOR SHARE OF attachment;
 IF NOT FOUND OR a.state<>'ready' OR a.retained_until<=now() OR EXISTS(SELECT 1 FROM platform_attachments.erasure_jobs e WHERE e.attachment_id=selected_attachment) THEN RETURN false; END IF;
 current_identity := jsonb_build_object('immutable_locator',a.immutable_locator::text,'write_attempt_id',coalesce(a.write_attempt_id::text,'None'),'detected_mime',a.detected_mime::text,'size_bytes',a.size_bytes::text,'sha256',encode(a.sha256,'hex'));
 RETURN current_identity=expected_identity;
END $$;
REVOKE ALL ON FUNCTION platform_hr_agent.lock_candidate_source(uuid,uuid,jsonb) FROM PUBLIC;
DO $$ DECLARE app_role text; BEGIN
 app_role := CASE current_user WHEN 'platform_control_owner' THEN 'platform_control_app' WHEN 'platform_control_owner_preview' THEN 'platform_control_app_preview' ELSE NULL END;
 EXECUTE format('GRANT EXECUTE ON FUNCTION platform_hr_agent.lock_candidate_source(uuid,uuid,jsonb) TO %I',app_role);
END $$;
