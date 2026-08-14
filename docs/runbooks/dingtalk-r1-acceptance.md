# DingTalk Release 1 production acceptance

This checklist is the formal acceptance contract for
`https://agent.orbbec.com.cn/`. It records no AppSecret, OAuth code, token,
Cookie, raw DingTalk identifier, mobile number, email, or customer content.

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
active owner, a directory generation newer than eight hours, a recent healthy
directory-event heartbeat, a public login shell without shared Basic Auth,
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
