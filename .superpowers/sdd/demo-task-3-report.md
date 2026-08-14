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
  ADMIN and FAE response codes. Before any live Nginx path is touched it renders
  a complete isolated Nginx configuration, rewrites the candidate's include to
  the staged snippet, and requires staged `nginx -t` to pass. It then arms the
  restoration trap before entering a dedicated atomic two-file transaction,
  validates the live config again, reloads (never restarts), verifies
  invariants, and restores the original bytes automatically on any failure or
  handled interruption.
- Rollback surgically removes the unique include, removes its snippet, validates
  and reloads Nginx, then stops/removes only the two demo-preview Compose
  services. A repeated rollback is safe.
- If rollback state is missing, the command returns `already-absent` only after
  proving both the live include and live snippet are absent. Either orphaned
  artifact fails closed without changing Nginx.
- Both preview locations repeat the root HSTS policy because their local
  `add_header` directives replace inherited headers under Nginx semantics.

The supplied target preflight hash remains external input. The implementation
does not embed the observed production hash, so drift fails closed.

## TDD Evidence

RED:

```text
9 failed, 4 passed
```

Failures were the expected missing snippet, installer, rollback and template
include contracts.

GREEN after independent-review fixes, focused plus deployment regression:

```text
43 passed, 1 skipped in 0.71s
```

The tests include an executable representative live config containing the
existing HTTP redirect, Basic Auth, `limit_req_zone` and `/admin/` route. The
installer patcher adds exactly one include; deleting that insertion reproduces
the input byte for byte. An ambiguous platform root is rejected. The atomic
file transaction is exercised at six injected write/interruption points; every
failure restores the original config bytes and removes the snippet and both
`.part` files.

Full backend:

```text
1148 passed, 2 skipped, 31 warnings in 26.10s
```

Additional checks:

```text
bash -n deploy/cloud/install-demo-preview.sh deploy/cloud/rollback-demo-preview.sh
python3 -m py_compile deploy/cloud/demo_preview_nginx_transaction.py
git diff --check
```

Both passed. No `security`, Keychain, `set -x`, Nginx restart, broad Compose
shutdown, FAE stop/restart, or non-preview container stop/remove path is present.

## Transaction Important Follow-up

An additional review found two narrow shell-orchestration windows and they were
closed without deployment or Task 4 changes:

- `reload_attempted=1` is now set before `systemctl reload nginx`. Therefore a
  reload that is accepted and then returns nonzero, or a handled signal while
  reload is in flight, restores the original files, validates the restored
  Nginx configuration, and attempts a second reload of those restored bytes.
- While the transaction remains armed, recovery deletes both `active-backup`
  and `active-backup.part`. This covers failure or a handled signal after the
  active-state move and before disarm. Cleanup stays inside the armed branch so
  a signal after commit cannot remove valid rollback state while leaving the
  preview live.

Follow-up TDD evidence:

```text
RED: 3 failed, 16 passed
RED (armed-cleanup placement): 1 failed, 2 passed
GREEN focused: 19 passed
GREEN relevant: 32 passed, 1 skipped
GREEN full backend: 1162 passed, 2 skipped, 31 warnings in 22.78s
```

The new failpoint contracts cover reload-return failure, a signal during
reload, and the point immediately after the active-state move. Final
`bash -n` and `git diff --check` also passed.

## Deferred Target Gate

Nginx is not installed on the local development machine. Real
`/usr/sbin/nginx -t`, live-hash lock, reload, invariant verification and tested
rollback remain mandatory target-side activation gates in Task 4. No production
files, services, listeners or containers were changed by this task.
