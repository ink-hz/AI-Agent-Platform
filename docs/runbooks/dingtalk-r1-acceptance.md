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
gender_probe_json="$(docker compose --env-file "$environment_path" \
  -f "$compose_path" run --rm --no-deps platform-directory \
  python -m app.control_plane.gender_probe)" || exit 1
python3 -c \
  'import json,sys; assert json.loads(sys.stdin.read()).get("ready") is True' \
  <<<"$gender_probe_json" || exit 1
unset gender_probe_json
```

A nonzero container exit or JSON `ready` other than literal `true` stops the
release. Do not print the captured JSON and do not copy secrets to the
controller. Require a completed active generation with source schema version exactly `2`.
The database gate emits only
`active:valid:null_invalid`: `active` is positive and equals `valid`, every
active member satisfies `gender in ('male','female')`, and the null/invalid count is zero.
Evidence contains only fixed aggregate counts/status, never
employee names, gender values, provider identifiers, mobile numbers,
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

The automated acceptance requires all five Platform services healthy, one
active owner, a completed schema-v2 directory generation newer than eight
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
