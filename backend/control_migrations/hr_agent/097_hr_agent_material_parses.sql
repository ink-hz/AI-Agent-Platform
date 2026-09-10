CREATE TABLE platform_hr_agent.material_parses (
 parse_id uuid PRIMARY KEY, owner_id uuid NOT NULL, attachment_id uuid NOT NULL,
 source_sha256 bytea NOT NULL CHECK(octet_length(source_sha256)=32), parser_release text NOT NULL,
 source_identity jsonb NOT NULL, state text NOT NULL CHECK(state IN ('queued','processing','ready','failed','unsupported')),
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0), lease_owner text, lease_until timestamptz,
 sealed_content bytea, sealed_content_key_version integer, error_code text,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(owner_id,attachment_id,source_sha256,parser_release),
 CHECK((lease_owner IS NULL)=(lease_until IS NULL)),
 CHECK((sealed_content IS NULL)=(sealed_content_key_version IS NULL))
);
CREATE TABLE platform_hr_agent.material_parse_requests (
 owner_id uuid NOT NULL, request_key text NOT NULL, attachment_id uuid NOT NULL,
 parse_id uuid NOT NULL REFERENCES platform_hr_agent.material_parses(parse_id), receipt jsonb NOT NULL,
 PRIMARY KEY(owner_id,request_key)
);
CREATE INDEX hr_agent_parse_claim ON platform_hr_agent.material_parses(state,lease_until,created_at);
REVOKE ALL ON platform_hr_agent.material_parses,platform_hr_agent.material_parse_requests FROM PUBLIC;
DO $$ DECLARE app_role text; BEGIN
 app_role := CASE current_user WHEN 'platform_control_owner' THEN 'platform_control_app' WHEN 'platform_control_owner_preview' THEN 'platform_control_app_preview' ELSE NULL END;
 IF app_role IS NULL THEN RAISE EXCEPTION 'unsupported HR migration owner'; END IF;
 EXECUTE format('GRANT SELECT, INSERT, UPDATE ON platform_hr_agent.material_parses TO %I',app_role);
 EXECUTE format('GRANT SELECT, INSERT ON platform_hr_agent.material_parse_requests TO %I',app_role);
END $$;
