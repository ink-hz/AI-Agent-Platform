# Minimal DingTalk Demo Task 3 Report

Date: 2026-08-14

## Outcome

Implemented the preview-only Nginx boundary and an idempotent, preview-only
rollback without deploying it to production.

- Added the exact `/_preview/dingtalk-r1/` proxy to loopback port 8081 and an
  exact trailing-slash redirect.
- Disabled Basic Auth only for the two exact preview locations; application
  authentication remains authoritative for all non-public preview routes.
- Normalized forwarding headers, stripped `Forwarded` and `Authorization`,
  retained Cookie forwarding, bounded request size to 1 MiB, used the existing
  query-free redacted log format, and set 330-second upstream timeouts.
- Added one fixed include to the source template immediately before the HTTPS
  Platform root location without changing that location body.
- Added a root-only installer requiring an externally supplied exact live-file
  SHA-256. It parses the live enabled config and inserts the include only in the
  unique `listen 443 ssl` Agent server root whose upstream is
  `127.0.0.1:8080`; it never renders over the live file from the base template.
- The installer snapshots the complete live file, other enabled-site hashes,
  all running-container ID/image/start/restart facts, listeners, and root,
  ADMIN and FAE response codes. It runs `nginx -t`, reloads (never restarts),
  verifies invariants, and restores the original bytes automatically on any
  failure.
- Rollback surgically removes the unique include, removes its snippet, validates
  and reloads Nginx, then stops/removes only the two demo-preview Compose
  services. A repeated rollback is safe.

The supplied target preflight hash remains external input. The implementation
does not embed the observed production hash, so drift fails closed.

## TDD Evidence

RED:

```text
9 failed, 4 passed
```

Failures were the expected missing snippet, installer, rollback and template
include contracts.

GREEN, focused plus deployment regression:

```text
39 passed, 1 skipped in 0.71s
```

The tests include an executable representative live config containing the
existing HTTP redirect, Basic Auth, `limit_req_zone` and `/admin/` route. The
installer patcher adds exactly one include; deleting that insertion reproduces
the input byte for byte. An ambiguous platform root is rejected.

Full backend:

```text
1144 passed, 2 skipped, 31 warnings in 22.15s
```

Additional checks:

```text
bash -n deploy/cloud/install-demo-preview.sh deploy/cloud/rollback-demo-preview.sh
git diff --check
```

Both passed. No `security`, Keychain, `set -x`, Nginx restart, broad Compose
shutdown, FAE stop/restart, or non-preview container stop/remove path is present.

## Deferred Target Gate

Nginx is not installed on the local development machine. Real
`/usr/sbin/nginx -t`, live-hash lock, reload, invariant verification and tested
rollback remain mandatory target-side activation gates in Task 4. No production
files, services, listeners or containers were changed by this task.
