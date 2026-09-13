-- Erasure reads each encrypted original/upload version to delete its object.
-- Preserve ordinary app permissions and grant only the maintenance read columns.
DO $migration$
DECLARE maintenance_role text;
BEGIN
  maintenance_role := CASE current_user
    WHEN 'platform_control_owner' THEN 'platform_control_maintenance'
    WHEN 'platform_control_owner_preview' THEN 'platform_control_maintenance_preview'
    ELSE NULL
  END;
  IF maintenance_role IS NULL THEN
    RAISE EXCEPTION 'unsupported attachment migration owner';
  END IF;
  EXECUTE format(
    'GRANT SELECT (attachment_id, write_attempt_id) ON platform_attachments.uploads TO %I',
    maintenance_role
  );
  EXECUTE format(
    'GRANT SELECT (attachment_id, attempt_id, object_ref_ciphertext, object_ref_key_version) '
    'ON platform_attachments.upload_write_attempts TO %I',
    maintenance_role
  );
END
$migration$;
