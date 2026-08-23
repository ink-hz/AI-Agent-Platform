# DingTalk Release 1 production acceptance

This checklist is the formal acceptance contract for
`https://agent.orbbec.com.cn/`. It records no AppSecret, OAuth code, token,
Cookie, raw DingTalk identifier, mobile number, email, or customer content.

## Pre-cutover trusted gender gates

Platform deploys first. Keep AI ADMIN on its previous release while the
existing Platform authentication boundary remains in place. Before Platform
publish/cutover, run the provider-coverage probe only in the directory
container, which reads its existing file-backed secrets internally:

```bash
environment_path=/opt/orbbec-agent-platform/private/platform.env
compose_path=/opt/orbbec-agent-platform/current/deploy/cloud/compose.yaml
compose=(docker compose --env-file "$environment_path" -f "$compose_path")
directory_id="$("${compose[@]}" ps -q platform-directory)"
test -n "$directory_id" || exit 1
test "$(docker inspect --format '{{.State.Health.Status}}' "$directory_id")" = healthy || exit 1
gender_probe_json="$(docker exec "$directory_id" python -m app.control_plane.gender_probe)" || exit 1
python3 -c \
  'import json,sys; assert json.loads(sys.stdin.read()).get("ready") is True' \
  <<<"$gender_probe_json" || exit 1
unset gender_probe_json
```

A nonzero container exit or JSON `ready` other than literal `true` stops the
release. Do not print the captured JSON and do not copy secrets to the
controller. Require a completed active generation with source schema version exactly `2`. Before any Nginx replacement or reload,
`publish-dingtalk-production.sh` uses `docker exec` to run one consistent SQL snapshot. The single gate covers the owner-bootstrap-aware owner count, the
fresh complete active schema-v2 generation, directory-event heartbeat, and
gender coverage. It emits only
`owner:fresh_generation:heartbeat:active:valid:null_invalid` and requires
`active > 0`, `active = valid`, `null_invalid = 0`, and every active member to
satisfy `gender in ('male','female')`. Its coverage segment remains the fixed
aggregate `active:valid:null_invalid`, and the null/invalid count is zero. Post-cutover
`accept-dingtalk-production.sh` rechecks the same one consistent SQL snapshot
through `docker exec`. Evidence contains only fixed aggregate counts/status,
never employee names, gender values, provider identifiers, mobile numbers,
ciphertext, raw rows, or provider payloads.

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
required owner state (one active owner, or zero only during the explicit
owner-bootstrap stage), a completed schema-v2 directory generation newer than eight
hours, aggregate valid gender coverage for every active member, a recent
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
