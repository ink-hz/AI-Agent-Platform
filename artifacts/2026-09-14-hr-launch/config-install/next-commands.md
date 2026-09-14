# Exact inputs and next operations for root coordination

Preparation inputs already transported by root: `/opt/orbbec-agent-platform/private/hr-incremental-ad5-inputs`, root 0700, six unchanged HR config files and `knowledge.tar.gz`, root 0600. Do not rerun the ce6 provisioner.

After independent source review and exact source SHA installation into the maintenance directory, invoke the new helper with a fresh persistent 32-hex run ID:

```text
python3 <reviewed-host-path>/incremental_install.py --inputs /opt/orbbec-agent-platform/private/hr-incremental-ad5-inputs --run-id <new-32-hex-run-id> --execute
```

No service starts. A partial directory/volume is retained and rerun refuses it; root must inspect disposition rather than auto-delete/adopt. Exact generation files:

```text
/data/orbbec-agent-platform/hr-generations/ad5f3cac253a6a28d3c764db/runtime.env
/data/orbbec-agent-platform/hr-generations/ad5f3cac253a6a28d3c764db/compose.hr-generation.json
/data/orbbec-agent-platform/hr-generations/ad5f3cac253a6a28d3c764db/prepared.json
```

The override SHA is `2c68d48690642c011a1ab13a858d748f37943ff8364bfa174783d912d0919a7a`; runtime.env SHA is `ee28426ef21f22d9c6b7a84b1f47532dd81acb7ee600dc6a92e13e3ebd408ab5`. Both must be rechecked on host. Compose argv (capture protected JSON, never emit rendered secrets):

```text
/usr/bin/docker compose --project-name orbbec-agent-platform --env-file /data/orbbec-agent-platform/hr-generations/ad5f3cac253a6a28d3c764db/runtime.env -f /opt/orbbec-agent-platform/releases/394c9ecd96a292db4cd6c4a0ea0066a3acc3cd76/deploy/cloud/compose.yaml -f /opt/orbbec-agent-platform/releases/394c9ecd96a292db4cd6c4a0ea0066a3acc3cd76/deploy/cloud/compose.hr-agent.yaml -f /data/orbbec-agent-platform/hr-generations/ad5f3cac253a6a28d3c764db/compose.hr-generation.json --profile hr-agent config --format json
```

Use clean process environment as the installer does. Root's joint executor owns create/start and actual mount inspection. API and HR Worker expected mounts: volume Name `orbbec-agent-platform-hr-agent-secrets-ad5f3cac253a6a28d3c764db`, destination `/run/hr-agent-secrets`, RW false; bind source `/data/orbbec-agent-platform/hr-generations/ad5f3cac253a6a28d3c764db/knowledge`, destination `/data/hr-knowledge`, RW false. Attachments have no new HR mount. No `compose up` whole project/fleet.

During the already-planned common stopped-writer window, root first completes root migrations including exact 106, then HR production-only migration via the separately reviewed host Supervisor (not the older helper embedded in an immutable release):

```text
python3 <reviewed-host-helper>/hr_agent_migrate.py /opt/orbbec-agent-platform/releases/394c9ecd96a292db4cd6c4a0ea0066a3acc3cd76 /opt/orbbec-agent-platform/private sha256:cbe282151ff678b9e0573102267b71ba1c32888603b4d818fa07be22ccc50442 orbbec-agent-platform-platform-postgres-1 --migration-set hr --environment production --migration-timeout 900 --command-timeout 10 --receipt-dir <fresh-absolute-protected-receipt-dir>
```

Retain the real Supervisor's named-container / four-signal / membership-session-zero cleanup. Root106 SHA must equal `7c4ae854dccb96292d33ed66af2070d8f555fcdf5539150fd7a592fedcd0cabb`. Existing HR preflight's migration list alone does not include106; require the attachment schema/permission readiness and exact root-ledger gate independently.

Next, run this image entry point in an owned bounded disposable container, on the exact internal network, mounting new HR secret volume, API secret volume `/run/secrets:ro`, new knowledge bind and HR work directory. Root's supervisor must own container cleanup:

```text
python -m tools.hr_agent.preflight --scope public-only --api-env-file /run/hr-agent-secrets/api-runtime.env --worker-env-file /run/hr-agent-secrets/worker-runtime.env --database-url-file /run/hr-agent-secrets/control-database-url
```

Use the existing reviewed `docs/runbooks/2026-09-11-hr-cloud-loop-cutover.md` executable init/count/transition transactions with their exact request IDs, SET LOCAL lock_timeout='2s' and statement_timeout='3s'. Never jump the phase sequence or infer counts from this installer. Stop/persist exclusion of old HR-web worker and establish real zero legacy occupancy before cloud transition; Feishu remains outside this release. Root's joint executor controls new API/attachments/MinIO ordering and accurate schema106 checks. Start the new HR worker only after compatible schema/configuration and the appropriate gate; perform real worker readiness separately. Owner canary remains separate from low-traffic deployment readiness. Failure must not resume old bare-PUT writers or claim business acceptance.
