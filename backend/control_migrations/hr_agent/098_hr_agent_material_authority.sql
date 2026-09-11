CREATE TABLE platform_hr_agent.material_authority_proofs (
 owner_id uuid NOT NULL,
 attachment_id uuid NOT NULL,
 text_revision text NOT NULL CHECK(length(text_revision) BETWEEN 1 AND 200),
 text_sha256 bytea NOT NULL CHECK(octet_length(text_sha256)=32),
 source_identity_sha bytea NOT NULL CHECK(octet_length(source_identity_sha)=32),
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(owner_id,attachment_id,text_revision,text_sha256,source_identity_sha),
 FOREIGN KEY(attachment_id,owner_id)
  REFERENCES platform_attachments.attachments(attachment_id,owner_internal_user_id)
  ON DELETE CASCADE
);
REVOKE ALL ON platform_hr_agent.material_authority_proofs FROM PUBLIC;
DO $$ DECLARE app_role text; BEGIN
 app_role := CASE current_user WHEN 'platform_control_owner' THEN 'platform_control_app' WHEN 'platform_control_owner_preview' THEN 'platform_control_app_preview' ELSE NULL END;
 IF app_role IS NULL THEN RAISE EXCEPTION 'unsupported HR migration owner'; END IF;
 EXECUTE format('GRANT SELECT, INSERT ON platform_hr_agent.material_authority_proofs TO %I',app_role);
END $$;
