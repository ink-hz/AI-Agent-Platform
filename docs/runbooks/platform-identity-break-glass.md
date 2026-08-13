# Platform identity owner administration and break-glass

This runbook is the only owner bind/replacement path. There is deliberately no
Web API for replacing the platform owner. Viewer role and exact Agent
observation scopes are managed by the authenticated owner in the Web
application.

Phase one provides application-immutable audit evidence, not an independent
external audit sink and not cryptographic proof that approvers independently
signed. The root-operated receipt binds two recorded stable approver IDs; it
does not create external approval. An owner can revoke a viewer, and the former
viewer immediately loses audit access.

## Preconditions

1. Work on the Platform host as an authorized OS root operator. Never run this
   from a developer laptop and never use production identities in tests.
2. Put the exact stable DingTalk identity in a current-user-only mode-0600 file.
   Never select a target by display name, mobile, email, or department and do
   not place the provider identity on the command line.
3. Confirm control database time health is `healthy`, WAL archiving is healthy
   and within the 15-minute RPO, and free space is above protective thresholds.
4. Complete and verify a control database backup. Record its constrained token
   (`BACKUP_...`), a constrained incident token (`INC_...`), and two distinct
   stable approver IDs (`uid:<number>`, approved OS identity, or internal UUID).
5. Prefer the current active, fresh, complete directory generation. Task 4 does
   not provide hard-stale Web mutation continuity. If no trustworthy complete
   generation exists, stop and use the database-incident/two-person recovery
   process; never construct an identity from a name.

All DSNs and identity keys remain in deployment-owned mode-0600 files. Supply a
dedicated root-owned mode-0600 HMAC keyring with purpose
`offline-owner-receipt-hmac`, a positive key version, and a 32-byte base64 key.
Machine-readable output never contains a provider identity.

## Inspect the selected generation

```bash
cd /opt/orbbec-agent-platform/backend
.venv/bin/python -m app.control_plane.admin_cli show-directory-generation
```

Copy the complete generation UUID and independently compare it with directory
health. Pass that UUID explicitly on every bind or replacement.

## Initial owner bind

Run a dry run first. It creates a signed, fsynced, root-owned mode-0600 receipt
valid for 15 minutes:

```bash
.venv/bin/python -m app.control_plane.admin_cli bind-owner \
  --provider-id-file /run/secrets/platform-owner-provider-id \
  --generation-id 00000000-0000-0000-0000-000000000000 \
  --incident-reference INC_2026_001 \
  --backup-reference BACKUP_2026_001 \
  --approver uid:1001 --approver uid:1002 \
  --receipt-file /run/platform-control/bind-owner-receipt.json \
  --receipt-key-file /run/secrets/platform-owner-receipt-keyring \
  --receipt-key-version 1
```

Check the generation digest, protected target lookup hash/version, owner/target
row-version precondition, operation UUID, expiry, backup/incident tokens, and
stable approver IDs. Confirm with the existing receipt path. There is no
first-invocation confirmation shortcut; confirmation atomically consumes the
receipt so it cannot confirm twice.

```bash
.venv/bin/python -m app.control_plane.admin_cli bind-owner \
  --provider-id-file /run/secrets/platform-owner-provider-id \
  --generation-id 00000000-0000-0000-0000-000000000000 \
  --incident-reference INC_2026_001 \
  --backup-reference BACKUP_2026_001 \
  --approver uid:1001 --approver uid:1002 \
  --receipt-file /run/platform-control/bind-owner-receipt.json \
  --receipt-key-file /run/secrets/platform-owner-receipt-keyring \
  --receipt-key-version 1 \
  --confirm /run/platform-control/bind-owner-receipt.json
```

## Normal offline owner replacement

The old owner must have departed or be under an approved incident response.
First create the replacement receipt:

```bash
.venv/bin/python -m app.control_plane.admin_cli replace-owner \
  --provider-id-file /run/secrets/platform-replacement-provider-id \
  --generation-id 00000000-0000-0000-0000-000000000000 \
  --incident-reference INC_2026_002 \
  --backup-reference BACKUP_2026_002 \
  --approver uid:1001 --approver uid:1002 \
  --receipt-file /run/platform-control/replace-owner-receipt.json \
  --receipt-key-file /run/secrets/platform-owner-receipt-keyring \
  --receipt-key-version 1
```

```bash
.venv/bin/python -m app.control_plane.admin_cli replace-owner \
  --provider-id-file /run/secrets/platform-replacement-provider-id \
  --generation-id 00000000-0000-0000-0000-000000000000 \
  --incident-reference INC_2026_002 \
  --backup-reference BACKUP_2026_002 \
  --approver uid:1001 --approver uid:1002 \
  --receipt-file /run/platform-control/replace-owner-receipt.json \
  --receipt-key-file /run/secrets/platform-owner-receipt-keyring \
  --receipt-key-version 1 \
  --confirm /run/platform-control/replace-owner-receipt.json
```

The database transaction finds and demotes the owner-role row even when it is
inactive, promotes the selected
active target, links both changes to the immutable requested audit ID, and
revokes both users' active Web Sessions. The unique index permits at most one
owner-role row regardless of status; `bind-owner` therefore refuses an
inactive/departed owner and requires reviewed replacement.

## Verification and reconciliation

1. Verify exactly one active owner by internal UUID, zero active Sessions for
   the old and new owner, and the selected generation/mapping linkage. Do not
   display encrypted provider mappings.
2. Verify requested and completed audit events share the returned request ID
   and that the role row stores the requested event ID. Never update the
   requested row.
3. If outcome append failed after the role transaction, treat the response as
   `503`/indeterminate. Reconcile using the operation UUID in the consumed
   receipt and mutation ledger before issuing another receipt.
   Do not undo a proven role mutation merely because the outcome was initially
   unavailable.
4. Require the replacement owner to authenticate again and verify the previous
   Sessions remain revoked.

## Rollback

Do not edit roles or audit rows manually. If the selected replacement is wrong,
perform another reviewed `replace-owner` using the previous stable identity,
the same fresh-generation checks, a new incident reason, a newly verified
backup, and two named approvers. If the control database is unreadable or the
target is absent from all complete generations, enter maintenance mode and use
the tested control-only PITR process: restore physical backup/WAL in isolation,
validate identity/audit continuity, logically restore only the control
database, revoke all restored Web Sessions, and reconcile post-target
revocations before leaving maintenance mode.

## Fixed retention maintenance

Retention has no cutoff or days override. Run it only after independent probes
report both time and WAL health as healthy:

```bash
cd /opt/orbbec-agent-platform/backend
.venv/bin/python -m app.control_plane.maintenance_cli purge-expired \
  --time-health healthy \
  --wal-health healthy
```

The maintenance role alone executes the fixed database-time cutoff of exactly
365 days and removes expired login attempts, Web Sessions, and rate buckets.
The app, audit-append, directory-worker, and stream-ingest roles cannot delete
audit rows. Unknown or breached time/WAL health stops the purge.
