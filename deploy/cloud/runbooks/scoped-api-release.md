# Scoped API release from the actual live image

This procedure is for the reviewed cloud-sync API change. It preserves the live
OS, dependencies, image defaults, HR/SDK/manufacturing configuration and all
ordered Compose inputs. It does not use `current`, rebuild the standard image,
run Nginx, migrate databases, provision roles, start the sync worker, or change
source ownership. Those separate gates remain in `cloud-agent-sync.md`.

The helper is `deploy/cloud/scoped-api-release.py` (Python standard library).
Every operation prints only a fixed success/failure stage. Docker/Compose output
is captured; resolved secret values never enter arguments, ordinary output or
receipts. Keep receipts and contexts under owner-only directories, never use
shell tracing, and never print resolved Compose JSON.

## Evidence required before staging

Use a clean checkout at the accepted merged commit, with freshly verified
`origin/master` equal to that commit. The helper checks local HEAD, cached
origin/master and worktree cleanliness; the operator must separately confirm the
remote ref. Build `webui/dist` from that exact checkout. The helper consumes Git
objects for backend code and a prebuilt dist; it cannot independently prove the
build provenance of an arbitrary supplied dist.

Capture the actual running API ID, immutable image ID, runtime release SHA,
ordered `com.docker.compose.project.config_files`, working directory and hashes
of every input. Preserve both immutable original runtime SHA and image ID for
rollback. Confirm affected existing backend files in that exact live image match
the baseline commit, and no code bind mount shadows them. An affected file
receipt is a private JSON array of `{path, same_baseline, actual, baseline}`;
`actual` and `baseline` are SHA-256 values of the live/baseline bytes.

The active frontend baseline receipt must include:

```json
{"algorithm":"active-static-graph-v1","image_id":"sha256:...",
 "baseline_commit":"40 hex characters","same_baseline":true,
 "live_active_sha256":"64 hex characters","baseline_active_sha256":"64 hex characters"}
```

Both hashes must match, covering the active index and complete emitted public
asset graph; retain the detailed file evidence separately. Historical unreferenced
hashed assets are excluded. The supplied new dist is copied additively: its index
and current assets replace matching paths while old hashed assets remain for
cached browser chunks. Preserve the old public asset allowlist in the staged
`.vite/manifest.json`: review a merged manifest whose `load_public_asset_manifest`
result includes every old and new public asset. Retained files alone are insufficient
when the SPA serves only manifest-listed hashed assets. There is no `RUN` instruction in the generated Dockerfile.

The readiness receipt must include `containers`, `systemd_units`,
`nginx_config_files` and `supplement.admin` from the read-only inventory. The
Admin supplement includes each unit's `configuration_files` hashes. For split
captures merge explicitly, preserving both originals:

```sh
python3 - <<'PY'
import json, os
from pathlib import Path
root = Path('/tmp/platform-cloud-sync-20261029')
receipt = json.loads((root/'release-readiness.private.json').read_bytes())
receipt['supplement'] = json.loads((root/'release-readiness-supplement.private.json').read_bytes())
fd = os.open(root/'scoped-readiness.private.json', os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as stream:
    json.dump(receipt, stream)
PY
```

The ordered recipe is private JSON with `project`, `working_dir`, `env_file`,
`files` (all original files in order), `runtime_release_sha` and `image_id`.
The recipe's `env_file` remains the fixed private platform.env path; its stale
image selection is overridden explicitly for both baseline and candidate renders.
No secret values belong in this recipe.

## Offline staging and host build

After review/merge/push, use the exact accepted release checkout and its newly
built dist. All named output directories below must be absent; their parents
must already exist and be private. The baseline for this batch is
`01368dea0766d5b3ce16e80ff511c8815fea48aa`.

```sh
release_sha="$(git rev-parse HEAD)"
python3 deploy/cloud/scoped-api-release.py stage \
  --repository "$PWD" \
  --baseline 01368dea0766d5b3ce16e80ff511c8815fea48aa \
  --release "$release_sha" \
  --readiness /tmp/platform-cloud-sync-20261029/scoped-readiness.private.json \
  --code-baseline /tmp/platform-cloud-sync-20261029/api-affected-code-baseline.private.json \
  --static-baseline /tmp/platform-cloud-sync-20261029/api-static-baseline.private.json \
  --dist "$PWD/webui/dist" \
  --output /tmp/platform-cloud-sync-20261029/api-context
```

Staging creates a private minimal build context, Dockerfile and SHA-256 COPY
manifest. One rooted payload COPY adds one filesystem layer (the observed base
has63 layers); the helper rejects a derived image exceeding128 layers. The
context root stays0700, metadata receipts0600, and payload directories/files
0755/0644 so the inherited runtime user can read them. Only changed committed `backend/app/*.py` files (recursively), the new
`backend/migrations/cloud_agent_sync/001_agent_sources.sql` and dist files enter
the payload. Existing modified Python files require matching live-code proof;
deletions, renames, symlinks and additional context files are rejected. Review
manifest destinations and hashes before transport. Staging does not run Docker.

Transport the helper, context, and recipe to a new root-private host operations
directory by the existing authorized SSH/SCP path. Preserve the0700 context/operations root,0600 metadata and0755/0644
payload directories/files, with root ownership; tar extraction must use `--no-same-owner`.
Do not place output receipts inside the build context. Example host layout:
`$ops/scoped-api-release.py`, `$ops/context`, `$ops/recipe.json`; `$ops` is an
absolute root-owned 0700 directory chosen for this accepted release.

On the host, as root:

```sh
python3 "$ops/scoped-api-release.py" build --stage "$ops/context" --output "$ops/build"
python3 "$ops/scoped-api-release.py" plan --stage "$ops/context" \
  --build-receipt "$ops/build/build.json" --recipe "$ops/recipe.json" \
  --output "$ops/transaction"
python3 "$ops/scoped-api-release.py" inspect-plan --transaction "$ops/transaction"
```

Build explicitly uses the local Unix Docker socket, classic builder
(`DOCKER_BUILDKIT=0`), `FROM sha256:<actual live image ID>`, `--pull=false`,
`--network=none` and no package installation. Classic builder is necessary for a
local image ID without a registry manifest reference. There is no automatic
alternate builder or base image. A daemon without classic-builder support must
stop for review. The derived image must inherit every base layer and every image
configuration field, except `PLATFORM_RELEASE_SHA`; the final immutable image ID
is recorded. Build selects a manifest-bound application reference
`orbbec-agent-platform:scoped-api-<release40>-<manifest64>` and refuses to
overwrite any existing matching tag. The private build receipt records this
reference, full manifest hash and immutable image ID. Compose uses the application
reference so the existing runtime-bound backup accepts API `Config.Image`; all
identity gates still use the immutable ID. Do not replace it with a raw image ID,
retag it, or weaken the installed backup boundary. No service is created or
restarted by build or plan.

Plan revalidates the original API identity/config, every ordered input hash, the
Admin process/unit/config hashes and shared Nginx file hashes. It renders the
original chain twice with explicit old/candidate image references and release
overrides. Rollback preserves the exact original `Config.Image` application tag
separately from its immutable image ID and runtime SHA. Plan/inspect-plan and
immediately-before-apply/rollback gates verify that recorded references still
resolve to their expected IDs; postcheck verifies the selected reference resolves
correctly and is the recreated API's exact `Config.Image`. Missing/reassigned
references stop the transaction; no implicit retagging or raw-ID fallback occurs.
The old render must reproduce actual effective environment, command, entrypoint,
user, working directory, mount sources/targets/access and attached networks. It
also compares read-only/privilege/capability/security/port policy, zero resource/PID
limits, restart/log/tmpfs/namespace defaults, image-plus-Compose healthcheck and
endpoint static IPs/aliases/driver options/gateway priority. Unhandled rendered
policy fails closed. Logging defaults are read from the host daemon configuration
and checked against its active logging driver. The original full HostConfig hash
is checked against the captured runtime and must be identical after recreation.
The entire candidate render must differ only in API image and release SHA;
unrelated services and all remaining Compose fields must compare equal. A
parity failure stops here and requires investigation; do not weaken the gate or
recreate services to make the baseline match.

## Controlled API transaction and explicit rollback

Apply the atomic no-takeover migration first, then plan/apply the API before
creating any worker container. The helper intentionally rejects new unrelated
containers. For later API rollback, stop and remove only this rollout's worker
container, preserving private keys/data and external networks, to restore that
container baseline; publisher ownership must be separately rolled back through
its controlled procedure before API recovery.

Before apply, require the fresh verified encrypted backup, separate encrypted
source ACL/config receipts, accepted migration/office-path ordering and coordination
with concurrent security work. The helper does not fulfill those independent
operational gates. Source apps must remain untouched.

```sh
python3 "$ops/scoped-api-release.py" apply --transaction "$ops/transaction"
```

The helper acquires both standard deployment/action locks with its recorded
owner, rechecks inputs/runtime/guardrails, and executes exactly:
`docker compose <all original ordered inputs plus candidate override> up -d
--no-deps --no-build --pull never --force-recreate platform-api`.
No other service is selected. It waits for the API's existing healthcheck,
verifies image/release/effective configuration, and compares every other
container's identity/config/mount/PID/start/restart state plus Admin and shared
Nginx evidence. Only a verified completion releases its own locks. Complete
receipts do not replace the cloud-sync business/API acceptance gates.

Failure is explicit. There is no automatic retry, downgrade or rollback. Locks
and the started receipt remain if completion is uncertain; preserve evidence and
inspect the actual API before deciding recovery. Never remove foreign locks.
An explicit authorized rollback uses the exact original application image
reference, verifies its immutable image ID, and restores the original runtime SHA
through the same complete original chain:

```sh
python3 "$ops/scoped-api-release.py" rollback --transaction "$ops/transaction"
```

A missing/stopped API after a failed recreate can be recovered only with this
transaction's matching apply-started receipt and both still-owned locks. A
foreign/new image or changed unrelated resource blocks recovery. Successful
rollback is independently verified and recorded. Retain all overrides/receipts;
restoring API code does not reverse schema changes or publisher ownership.

## Local verification

```sh
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python \
  -m pytest backend/tests/test_scoped_api_release.py -q
```

Tests use disposable Git/filesystem fixtures and replace only the Docker/OS
process boundary for transaction cases. They perform no SSH, production Docker,
model calls, migrations or business writes. Real host build, Compose parity,
API recreation and production acceptance remain separate gates.

## HostConfig 比较状态版本

原始运行快照仍按原始哈希核对；重建后的比较只对唯一目标的 Binds/Mounts 列表排序，保留每个条目和其他字段，重复目标拒绝。新事务状态必须带 `hostconfig_hash_scheme=unique-mount-lists-v1`；旧原始哈希状态在任何重建前拒绝。需要恢复旧事务时，必须取得与旧哈希精确匹配的原始 HostConfig，保存原始状态，显式原子迁移仅相关哈希/版本字段并保留 raw 证据，重新验证全部配置、身份、锁、其他服务和健康；不得直接删锁或跳过未知差异。
