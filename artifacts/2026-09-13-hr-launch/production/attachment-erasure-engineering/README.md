# Independent attachment erasure canary

`../attachment_erasure_canary.py` uses only `/api/v1/account` and the existing attachment init/content/complete/GET/DELETE endpoints. It has no HR readiness, work, knowledge or HR schema requirement. It imports the frozen `api_canary.py` private-file, journal and credential helpers; ship both sibling scripts together. No production calls were executed during implementation.

The parent approved three separate stages. Supply the same independent absolute, regular0600 owner configuration documented in `../api-canary-engineering/README.md`: owner_id, session_cookie (existing `__Host-platform_session` token value), csrf, public_origin, api_base_url. The two URLs are identical HTTPS origins without paths. Sessions are never minted, Origin/CSRF are sent, redirects/proxy environment are disabled, and raw account/credential values are not logged.

```sh
backend/.venv/bin/python artifacts/2026-09-13-hr-launch/production/attachment_erasure_canary.py prepare --private-config /absolute/private/canary.json --evidence-dir /absolute/private/new-attachment-run
```

`prepare` creates exactly one synthetic text file with a fresh run UUID in both its name and bytes, uploads it through real HTTP, and waits through the actual `validating`/`scanning` states to `ready`. It then stops at `prepared`. Its private ledger binds the accepted upload/attachment UUIDs, name, size, declared MIME, exact outgoing-byte SHA256, and immutable prepared metadata digest. **The HTTP metadata endpoint does not expose the server byte SHA**; outgoing SHA does not certify stored bytes. The root operator should now collect read-only evidence for precisely this attachment's object graph, including authoritative byte hash and object-reference hashes, and establish the actual objects exist before deletion. The script does not enumerate other attachments or query SQL/S3.

```sh
backend/.venv/bin/python artifacts/2026-09-13-hr-launch/production/attachment_erasure_canary.py erase --private-config /absolute/private/canary.json --evidence-dir /absolute/private/existing-attachment-run
```

`erase` revalidates actual account owner/role/freshness and the same prepared attachment's ID/name/size/MIME, then sends exactly one DELETE to that ID. The actual endpoint returns **204 with no body**, not200. Its local response receipt is `{"http_status":204}`; that object describes the empty HTTP response and is not claimed as a server JSON body. The final script status is `awaiting_external_erasure_evidence`, even if a later GET is404. There is no automatic recreate, retry of DELETE, cancellation or deletion of arbitrary IDs.

The **300-second mutation deadline includes all preparation and operator pauses**. It is saved once and never extended on resume. After it expires, prepare/erase cannot send a new mutation. The root operator must coordinate the pre-delete evidence within that window; do not edit the deadline in production evidence.

```sh
backend/.venv/bin/python artifacts/2026-09-13-hr-launch/production/attachment_erasure_canary.py observe --private-config /absolute/private/canary.json --evidence-dir /absolute/private/existing-attachment-run
```

`observe` uses a separate aggregate20-second GET-only window, including after the original deadline. It does not renew mutation authority. Requests use at most20seconds each; prepare polling sleeps at most2seconds within the original deadline. Observations explicitly retain `physical_erasure_verified=false`.

Upload initialization has no server idempotency: its generated UUID is only a trace marker. A lost accepted upload response is journaled as unknown and cannot trigger another initialization. An interrupted prepare with known receipts can explicitly use `prepare --resume-prepare` against the same ledger; any uncertain upload stage stops. A lost DELETE response similarly stops, and subsequent erase never resends it; use read-only observe plus the root's independent evidence. An already recorded204 is reused without another DELETE. Account or own-metadata mismatch blocks mutations.

## Evidence needed outside this script

Identity, ready state, DELETE204, GET404, or a label are not physical erasure proof. The attachment access repository intentionally hides a record as soon as an erasure job exists. The root must separately bind this ledger's exact attachment ID to:

1. Before/after exact object-reference hashes and original/derivative/write-attempt graph; never print decrypted object refs or credentials in public receipts.
2. Actual object-store absence for each authorized exact object (including versions where applicable), with any failed lookup distinguished from verified absence.
3. Actual erasure job terminal state and cleanup counts via authorized read-only maintenance evidence.

Do not set a forged SQL success record. No script code calls SQL, S3, models, workers, shell commands or a browser.

## Local engineering scope

`red-1` captures three failures for the absent implementation. `red-2-validating` captures a real delayed processor window exposing an omitted `validating` state (1 failed /3passed). The final implementation polls only actual transient states `validating` and `scanning`; terminal quarantine/rejection does not get treated as ready.

`green-final-1` contains final commands, exact sources (including the frozen helper dependency), hashes and synthetic per-test HTTP ledgers. Tests use real local DingTalkWebAuth/WebSessionRepository, current owner authorization, CSRF/Origin, attachment HTTP services, encryption codec, PostgreSQL and real attachment/maintenance erasure processors. They assert `platform_hr_agent` namespace is absent. Only external login exchange, a local MemoryStore and explicitly injected lost HTTP replies are substituted. A decisive test observes GET404 while the local object still exists, then runs the genuine erasure service and reads the actual job as completed after deletion. This tests local control flow, **not production S3 erasure**. No real model, production, browser or process restart was tested. TestClient timeout deprecation warnings remain visible; real httpx honors request timeouts.
