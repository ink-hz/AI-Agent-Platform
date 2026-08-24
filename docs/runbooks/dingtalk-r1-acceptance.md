# DingTalk Release 1 production acceptance

This checklist is the formal acceptance contract for
`https://agent.orbbec.com.cn/`. It records no AppSecret, OAuth code, token,
Cookie, raw DingTalk identifier, mobile number, email, or customer content.

## Pre-cutover employee-profile gates

Platform deploys first. Keep AI ADMIN on its previous release while the
existing Platform authentication boundary remains in place. Before Platform
publish/cutover, run the employee-profile aggregate probe only in the directory
container, which reads its existing file-backed secrets internally:

```bash
environment_path=/opt/orbbec-agent-platform/private/platform.env
compose_path=/opt/orbbec-agent-platform/current/deploy/cloud/compose.yaml
compose=(docker compose --env-file "$environment_path" -f "$compose_path")
directory_id="$("${compose[@]}" ps -q platform-directory)"
test -n "$directory_id" || exit 1
test "$(docker inspect --format '{{.State.Health.Status}}' "$directory_id")" = healthy || exit 1
profile_probe_json="$(docker exec "$directory_id" python -m app.control_plane.employee_profile_probe)" || exit 1
python3 -c 'import json,sys; p=json.load(sys.stdin); active=p["active_employee_count"]; raise SystemExit(not (active > 0 and p["primary_department_present_count"] == active))' \
  <<<"$profile_probe_json" || exit 1
unset profile_probe_json
```

A nonzero container exit, malformed aggregate output, zero employees, or incomplete
primary-department coverage stops the release. Do not print the captured JSON and
do not copy secrets to the controller. Require a completed active generation with
source schema version exactly `3`. Before any Nginx replacement or reload,
`publish-dingtalk-production.sh` uses `docker exec` to run one consistent SQL snapshot. The single gate covers the owner-bootstrap-aware owner count, the
fresh complete active schema-v3 generation, and the directory-event heartbeat. It
emits only `owner:fresh_generation:heartbeat`. The employee-profile probe separately
requires `active_employee_count > 0` and
`primary_department_present_count = active_employee_count`. Generation validation
proves authoritative employee-count agreement and required display names, so
nickname completeness is 100% and primary-department completeness is 100%.
The real-name and mobile coverage are reported through `real_name_present_count`
and `mobile_present_count` without a completeness requirement; gender coverage is not a lodging release gate
because AI ADMIN supports locked local fallback. Post-cutover
`accept-dingtalk-production.sh` rechecks the same one consistent SQL snapshot
through `docker exec`. Evidence contains only fixed aggregate counts/status,
never employee names, gender values, provider identifiers, mobile numbers,
ciphertext, raw rows, or provider payloads.

The pre-cutover bootstrap may have zero active owners only during the explicit
unbound-owner publish stage. After owner binding, formal post-cutover acceptance
requires exactly one active owner; a recorded bootstrap state never permits
zero owners during this formal acceptance.

Only after this reconciliation may Platform publish/cutover and the
authenticated account proof run. Deploy the AI ADMIN strict consumer last.
Rollback AI ADMIN first, then use the Platform compatibility rollback without
deleting synchronized directory data.

## Automated gates

Run before merge and deployment:

```bash
cd backend
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q
cd ../webui
npm test -- --run
npm run build
cd ..
bash -n deploy/cloud/*.sh
git diff --check
```

On the cloud host, after owner binding and publication:

```bash
/opt/orbbec-agent-platform/current/deploy/cloud/accept-dingtalk-production.sh
```

The automated acceptance requires all five Platform services healthy, the
formal post-cutover owner state of exactly one active owner, a completed
schema-v3 directory generation newer than eight hours, complete nickname and
primary-department coverage, reported real-name/mobile aggregate coverage, a recent
healthy directory-event heartbeat, a public login shell without shared Basic Auth,
unauthenticated account rejection, preserved independent `/admin`
authentication, private port 8080, no public PostgreSQL, a valid certificate,
and unchanged FAE container identity/start time.

## Real DingTalk checks

Use the designated active Orbbec member account.

1. Open the workbench entry inside DingTalk. It must complete in-client login
   without a password and show the same internal account on refresh.
2. In a clean ordinary browser, start QR login, scan with DingTalk, and confirm
   the callback cannot be replayed.
3. Confirm an ordinary member sees Account only and receives backend `403` for
   direct management URLs.
4. Confirm the bound owner sees Agent, Session, Review, Operations, Identity,
   and Governance navigation and can read the sanitized management data.
5. Confirm `/admin/` still requests its independent credential and FAE remains
   available through both its domain and legacy IP route.
6. Confirm a Stream reconnect leaves the consumer healthy and a directory user
   change is persisted before acknowledgement.
7. Confirm public and company DNS both resolve `agent.orbbec.com.cn`, and the
   company HTTPS proxy permits the certificate and callback path.

## Rollback gate

During the acceptance window, rehearse the fixed rollback command once if the
business window permits. It must restore the exact prior Nginx file and prior
Platform release while preserving PostgreSQL and FAE. Re-publishing requires a
fresh deployment/cutover state; never hand-edit the state file.

## Evidence to report

Report only the release SHA, automated pass counts, public URL, directory
freshness class, service health, rollback path, certificate validity, and any
known phase-one limitation. Do not paste raw database rows or provider
identifiers into the report.
