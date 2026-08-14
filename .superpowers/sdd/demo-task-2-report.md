# Minimal DingTalk Demo Task 2 Report

Date: 2026-08-14
Branch: `feat/minimal-dingtalk-demo`
Base commit: `a66c2ce`

## Scope delivered

- Added a Compose overlay that adds only `platform-api-demo-preview`,
  `platform-loopback-demo-preview` and the profiled one-off
  `platform-demo-preview-runner`; it does not redefine or stop any root
  Compose service.
- The preview API keeps fixed internal address `172.30.0.5` for database DNS
  and gains fixed edge address `172.31.0.5` for DingTalk egress. It has no
  `ports` or `expose`, keeps the exact `172.30.0.6/32` trusted immediate proxy,
  and has Uvicorn proxy-header parsing disabled.
- The preview loopback has fixed internal address `172.30.0.6`, an explicit
  source bind back to the API, and the only added host binding is
  `127.0.0.1:8081:8080`. It overwrites upstream forwarding headers and uses
  a normalized health probe.
- Both preview services are non-root, read-only, capability-free and use
  `no-new-privileges`. Only the API mounts the dedicated external preview
  secret volume; the loopback receives no secret mount.
- Preview identity is fixed to the exact route prefix, Cookie name, public
  origin and QR-only deployment declaration. Flywheel, Review and attachment
  access are disabled; the overlay defines no Stream or directory schedule.
- The API copies only runtime credentials from the `0400` volume into a
  private tmpfs as `0600`, satisfying the existing exact runtime secret-file
  validation without weakening it.
- Added a root-only, argument-free, idempotent secret bootstrap for the fixed
  host directory and volume. It validates root ownership, exact `0600`
  source mode, regular-file/non-symlink type, stable userid input, exact
  preview database/role combinations, keyring purposes and pairwise key
  separation. It never prints credential contents.
- App and audit connections use separate preview DSNs. The bootstrap requires
  `platform_control_app_preview` and `platform_audit_append_preview`
  respectively against `agent_platform_control_preview`; real `create_app`
  construction verifies the audit writer accepts that exact split.
- Preview QR-only behavior is enforced by the backend, not just declared in
  Compose: preview configuration rejects combined/in-client mode, no
  in-client provider client is constructed, the exchange route is not
  registered or public, and direct method use fails closed.
- Source secret ingestion rejects a symlinked host directory. Inside the
  container it opens the bind root with `O_DIRECTORY|O_NOFOLLOW`, opens each
  fixed child through that directory FD with `O_NOFOLLOW`, validates regular
  file/owner/mode/size using `fstat`, and reads/copies only the bytes obtained
  from that same descriptor.
- Volume files are owned by UID 10001 with mode `0400`. Runtime credentials
  live under a root-owned/group-10001 `0750` directory. Migrator, directory
  worker and allowlist inputs live under a root-only `0700` directory, so a
  compromised API cannot read offline credentials even though one dedicated
  volume is used.
- The runtime image now contains `backend/control_migrations`; a static image
  command contract records exact preview migration, allowlist bootstrap,
  API/loopback start and minimal-health steps for the target smoke gate.
- Migration and allowlist bootstrap use the profiled runner rather than host
  networking. The runner has no fixed address, port, restart policy or
  dependency, so `compose run` cannot collide with the API's static addresses.
  It mounts the preview secret volume read-only, uses a private root tmpfs,
  drops all capabilities, enables `no-new-privileges`, and joins both the
  internal database network and outbound edge network.

## One-off Compose commands

The runner's default command is `/bin/false`, so it does nothing unless it is
explicitly targeted. A targeted `compose run` automatically enables its
`demo-preview-tools` profile. With `release_path`, `platform_environment` and
`image_ref` set by the release script, migration is invoked exactly as:

```bash
PLATFORM_IMAGE="$image_ref" docker compose \
  --env-file "$platform_environment" \
  -f "$release_path/deploy/cloud/compose.yaml" \
  -f "$release_path/deploy/cloud/compose.demo-preview.yaml" \
  run --rm --no-deps platform-demo-preview-runner /bin/sh -ec '
    install -d -m 0700 /tmp/migrate
    install -m 0600 /run/demo-preview-secrets/offline/preview-control-migrator-database-url /tmp/migrate/database-url
    export PLATFORM_CONTROL_MIGRATOR_DATABASE_URL_FILE=/tmp/migrate/database-url
    export PLATFORM_CONTROL_OWNER_ROLE=platform_control_owner_preview
    export PLATFORM_CONTROL_MIGRATION_DIR=/app/backend/control_migrations
    exec python -m app.control_plane.migrate
  '
```

Allowlist bootstrap is invoked through the same Compose service and networks:

```bash
PLATFORM_IMAGE="$image_ref" docker compose \
  --env-file "$platform_environment" \
  -f "$release_path/deploy/cloud/compose.yaml" \
  -f "$release_path/deploy/cloud/compose.demo-preview.yaml" \
  run --rm --no-deps platform-demo-preview-runner /bin/sh -ec '
    install -d -m 0700 /tmp/bootstrap
    for name in dingtalk-app-key dingtalk-corp-id dingtalk-app-secret preview-identity-encryption-keyring preview-identity-hmac-keyring; do
      install -m 0600 "/run/demo-preview-secrets/runtime/$name" "/tmp/bootstrap/$name"
    done
    for name in preview-control-directory-worker-database-url demo-userids; do
      install -m 0600 "/run/demo-preview-secrets/offline/$name" "/tmp/bootstrap/$name"
    done
    export PLATFORM_CONTROL_DIRECTORY_DATABASE_URL_FILE=/tmp/bootstrap/preview-control-directory-worker-database-url
    export PLATFORM_DINGTALK_APP_KEY_FILE=/tmp/bootstrap/dingtalk-app-key
    export PLATFORM_DINGTALK_CORP_ID_FILE=/tmp/bootstrap/dingtalk-corp-id
    export PLATFORM_DINGTALK_APP_SECRET_FILE=/tmp/bootstrap/dingtalk-app-secret
    export PLATFORM_IDENTITY_ENCRYPTION_KEYRING_FILE=/tmp/bootstrap/preview-identity-encryption-keyring
    export PLATFORM_IDENTITY_HMAC_KEYRING_FILE=/tmp/bootstrap/preview-identity-hmac-keyring
    exec python -m app.control_plane.demo_bootstrap --userid-file /tmp/bootstrap/demo-userids
  '
```

Neither command uses `--network host`; database DNS resolves on
`platform-internal`, while external callbacks use `platform-edge`.

## TDD evidence

Initial RED:

- Command: `.venv/bin/python -m pytest tests/test_demo_preview_deployment.py tests/test_cloud_deployment.py tests/test_cloud_loopback_proxy.py -q`
- Result: `9 failed, 10 passed, 1 skipped` because the overlay/bootstrap files
  and control-migration image copy did not exist.

Additional RED:

- Static image smoke contract: `1 failed` because the migrate/bootstrap
  command contract did not exist.
- Offline-secret isolation contract: `1 failed` because the first draft put
  all volume files at one API-readable level. The implementation was changed
  to runtime/offline directories before completion.
- Independent review repair RED: `8 failed, 99 passed, 1 skipped`. These
  reproduced the shared app/audit DSN, dead login-flow variable/public
  in-client exchange, and path-reopen TOCTOU boundary. One unrelated keyring
  fixture transition error was corrected before evaluating production code.
- Network egress repair RED: `6 failed, 10 passed, 1 skipped`. The failures
  specifically proved that the API lacked `platform-edge`, the one-off runner
  and runner contract were absent, and edge address `172.31.0.5` was not in the
  merged static-address set.

Final GREEN:

- Network egress focused group: `16 passed, 1 skipped`.
- Network egress related deployment/config group: `165 passed, 1 skipped`.
- Full git-tracked backend suite after the egress repair: `1154 passed, 2 skipped`
  with 31 pre-existing Starlette/httpx deprecation warnings.
- Focused deployment/auth/config group after review repair: `146 passed, 1 skipped`.
- Full backend after review repair: `1132 passed, 2 skipped`, with 31 pre-existing
  Starlette/httpx deprecation warnings.
- The focused skip is the real Docker Compose/image test because Docker is
  not installed on this workstation.

## Verification

- `bash -n deploy/cloud/bootstrap-demo-preview-secrets.sh`: passed.
- Embedded Python bootstrap body compilation: passed.
- `python -m compileall -q app tests`: passed.
- PyYAML overlay/base static merge: passed; three additive services, unique
  static IPs, API internal plus edge connectivity, and one loopback-only host
  port. Root services remain byte-for-byte equal after the static merge.
- Non-root execution of the secret bootstrap: exit 1, empty stdout, stable
  redacted stderr.
- `git diff --check`: passed.
- Runtime-file Keychain command scan: no matches.
- Modified-file credential/private-key pattern scan: no matches.
- A whole-worktree run also collected another agent's untracked Task 4 test and
  reported `3 failed, 1165 passed, 2 skipped`; all three failures are confined
  to that agent's untracked prerequisite/deploy scripts. The tracked-only full
  suite above is the isolation-preserving Task 2 result.

## Required target gate / deferred evidence

- Docker is unavailable locally. Before activation, the target must run real
  `docker compose ... config`, build the immutable image, execute preview-only
  migration and allowlist bootstrap, start both preview services and prove the
  loopback health payload is exactly `{"status":"ok"}`.
- No real secret, DingTalk endpoint, Docker volume, database or production
  service was accessed or modified in this task.
- The overlay packages only the identity demo boundary. It does not activate
  Nginx or deploy; those remain Tasks 3 and 4.
