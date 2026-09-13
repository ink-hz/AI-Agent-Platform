(
set -euo pipefail
umask 077
# HR_ATTACHMENT_HOTFIX
export attachment_release=/private/var/folders/f2/_6ql657j53ddvq65fsm6m_tw0000gn/T/pytest-of-neo/pytest-9540/test_attachment_sequence_fails3/release
export attachment_release_sha=cccccccccccccccccccccccccccccccccccccccc
attachment_private=/opt/orbbec-agent-platform/private
export attachment_evidence=/private/var/folders/f2/_6ql657j53ddvq65fsm6m_tw0000gn/T/pytest-of-neo/pytest-9540/test_attachment_sequence_fails3
export attachment_image=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
export attachment_old_image=sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
export attachment_old_id=old
export attachment_source_sha=c9f6434031084cf419c913981e8ee48965e98459347d9b7fadf67113fe342351
export attachment_migration_sha=15355874fce1ea58d00056eb07233a0fb3ef4a3cd7e807ea6e6608deb3668177
export attachment_runbook_sha=4bba157d64b5cfedfac2a264aeffee54277bf284a4f5466abebe61bebe120e0d
attachment_postgres=APPROVED_POSTGRES_CONTAINER_ID
test -d "$attachment_evidence"
export PLATFORM_IMAGE="$attachment_image"
attachment_compose=(/private/var/folders/f2/_6ql657j53ddvq65fsm6m_tw0000gn/T/pytest-of-neo/pytest-9540/test_attachment_sequence_fails3/mock-docker compose --env-file "$attachment_private/runtime.env" -f "$attachment_release/deploy/cloud/compose.yaml")
# Verify the exact approved text and release inputs before stopping anything.
python3 - <<'PY'
import hashlib, os, pathlib, re
assert re.fullmatch('[0-9a-f]{40}', os.environ['attachment_release_sha'])
root = pathlib.Path(os.environ['attachment_evidence'])
assert not any((root / name).exists() for name in ('compose-before.json', 'attachment-hotfix-receipt.json', 'attachment-stop-identity.json'))
release = pathlib.Path(os.environ['attachment_release'])
for path, expected in (
    (release / 'docs/runbooks/2026-09-11-hr-cloud-loop-cutover.md', os.environ['attachment_runbook_sha']),
    (release / 'backend/control_migrations/100_attachment_erasure_worker_access.sql', os.environ['attachment_migration_sha']),
):
    assert path.is_file() and not path.is_symlink()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected
assert os.environ['attachment_image'].startswith('sha256:') and len(os.environ['attachment_image']) == 71
PY
test "$(/private/var/folders/f2/_6ql657j53ddvq65fsm6m_tw0000gn/T/pytest-of-neo/pytest-9540/test_attachment_sequence_fails3/mock-docker image inspect --format '{{.Id}}' "$attachment_image")" = "$attachment_image"
"${attachment_compose[@]}" config --format json | python3 -c '
import json, os, pathlib, sys
image = json.load(sys.stdin)["services"]["platform-attachments"]["image"]
assert image == os.environ["attachment_image"]
with (pathlib.Path(os.environ["attachment_evidence"]) / "compose-before.json").open("x") as stream:
    json.dump({"service": "platform-attachments", "image": image}, stream)
'
test "$(/private/var/folders/f2/_6ql657j53ddvq65fsm6m_tw0000gn/T/pytest-of-neo/pytest-9540/test_attachment_sequence_fails3/mock-docker run --cap-drop ALL --security-opt no-new-privileges:true --rm --read-only --user 10001:10001 --network none "$attachment_image" python -c 'import hashlib,pathlib; print(hashlib.sha256(pathlib.Path("/app/backend/app/attachments/erasure.py").read_bytes()).hexdigest())')" = "$attachment_source_sha"
test "$(/private/var/folders/f2/_6ql657j53ddvq65fsm6m_tw0000gn/T/pytest-of-neo/pytest-9540/test_attachment_sequence_fails3/mock-docker inspect --format '{{.Image}}' "$attachment_old_id")" = "$attachment_old_image"
test "$(/private/var/folders/f2/_6ql657j53ddvq65fsm6m_tw0000gn/T/pytest-of-neo/pytest-9540/test_attachment_sequence_fails3/mock-docker inspect --format '{{.State.Running}}' "$attachment_old_id")" = true
# Explicit stop plus restart-policy removal prevent daemon restart of the old image.
/private/var/folders/f2/_6ql657j53ddvq65fsm6m_tw0000gn/T/pytest-of-neo/pytest-9540/test_attachment_sequence_fails3/mock-docker update --restart=no "$attachment_old_id"
/private/var/folders/f2/_6ql657j53ddvq65fsm6m_tw0000gn/T/pytest-of-neo/pytest-9540/test_attachment_sequence_fails3/mock-docker stop "$attachment_old_id"
test "$(/private/var/folders/f2/_6ql657j53ddvq65fsm6m_tw0000gn/T/pytest-of-neo/pytest-9540/test_attachment_sequence_fails3/mock-docker inspect --format '{{.State.Running}}' "$attachment_old_id")" = false
python3 - <<'PY'
import datetime, json, os, pathlib
with (pathlib.Path(os.environ['attachment_evidence']) / 'attachment-stop-identity.json').open('x') as stream:
    json.dump({'oldContainerId': os.environ['attachment_old_id'], 'oldImageId': os.environ['attachment_old_image'],
               'stoppedAt': datetime.datetime.now(datetime.timezone.utc).isoformat()}, stream)
PY
# After this boundary every failure keeps attachment workers stopped.
attachment_done=0
attachment_cleanup() {
  status=$?
  if test "$attachment_done" != 1; then
    /private/var/folders/f2/_6ql657j53ddvq65fsm6m_tw0000gn/T/pytest-of-neo/pytest-9540/test_attachment_sequence_fails3/mock-docker stop "$attachment_old_id" >/dev/null 2>&1 || true
    for candidate in $("${attachment_compose[@]}" ps -q platform-attachments); do
      /private/var/folders/f2/_6ql657j53ddvq65fsm6m_tw0000gn/T/pytest-of-neo/pytest-9540/test_attachment_sequence_fails3/mock-docker stop "$candidate" >/dev/null 2>&1 || true
    done
    echo ATTACHMENT_HOTFIX_FAILED_KEEP_STOPPED >&2
  fi
  exit "$status"
}
trap attachment_cleanup EXIT
/private/var/folders/f2/_6ql657j53ddvq65fsm6m_tw0000gn/T/pytest-of-neo/pytest-9540/test_attachment_sequence_fails3/mock-bootstrap "$attachment_release" "$attachment_private" "$attachment_image" "$attachment_postgres"
# Verify100 and each of its six column privileges before starting any worker.
attachment_ledger=$(/private/var/folders/f2/_6ql657j53ddvq65fsm6m_tw0000gn/T/pytest-of-neo/pytest-9540/test_attachment_sequence_fails3/mock-docker exec "$attachment_postgres" psql -X -A -t -v ON_ERROR_STOP=1 -U platform_owner -d agent_platform_control -c "select sha256 || '|' || (has_column_privilege('platform_control_maintenance','platform_attachments.uploads','attachment_id','SELECT') and has_column_privilege('platform_control_maintenance','platform_attachments.uploads','write_attempt_id','SELECT') and has_column_privilege('platform_control_maintenance','platform_attachments.upload_write_attempts','attachment_id','SELECT') and has_column_privilege('platform_control_maintenance','platform_attachments.upload_write_attempts','attempt_id','SELECT') and has_column_privilege('platform_control_maintenance','platform_attachments.upload_write_attempts','object_ref_ciphertext','SELECT') and has_column_privilege('platform_control_maintenance','platform_attachments.upload_write_attempts','object_ref_key_version','SELECT'))::text from platform_control.schema_migrations where version=100")
test "$attachment_ledger" = "$attachment_migration_sha|true"
"${attachment_compose[@]}" up -d --no-deps --force-recreate platform-attachments
export attachment_new_id
attachment_new_id=$("${attachment_compose[@]}" ps -q platform-attachments)
test -n "$attachment_new_id"
test "$attachment_new_id" != "$attachment_old_id"
test "$(/private/var/folders/f2/_6ql657j53ddvq65fsm6m_tw0000gn/T/pytest-of-neo/pytest-9540/test_attachment_sequence_fails3/mock-docker inspect --format '{{.Image}}' "$attachment_new_id")" = "$attachment_image"
test "$(/private/var/folders/f2/_6ql657j53ddvq65fsm6m_tw0000gn/T/pytest-of-neo/pytest-9540/test_attachment_sequence_fails3/mock-docker inspect --format '{{.State.Running}}' "$attachment_new_id")" = true
python3 - <<'PY'
import datetime, json, os, pathlib
receipt = {key: os.environ[value] for key, value in {
    'runbookSha256': 'attachment_runbook_sha', 'releasePath': 'attachment_release', 'releaseSha': 'attachment_release_sha',
    'imageId': 'attachment_image', 'oldImageId': 'attachment_old_image',
    'oldContainerId': 'attachment_old_id', 'newContainerId': 'attachment_new_id',
    'sourceSha256': 'attachment_source_sha', 'migration100Sha256': 'attachment_migration_sha',
}.items()}
receipt['stoppedAt'] = json.loads((pathlib.Path(os.environ['attachment_evidence']) / 'attachment-stop-identity.json').read_text())['stoppedAt']
receipt.update(block='HR_ATTACHMENT_HOTFIX', checkedAt=datetime.datetime.now(datetime.timezone.utc).isoformat(),
               limitation='Identity/order verification only; real erasure canary is separately required')
with (pathlib.Path(os.environ['attachment_evidence']) / 'attachment-hotfix-receipt.json').open('x') as stream:
    json.dump(receipt, stream, indent=2)
PY
attachment_done=1
)
