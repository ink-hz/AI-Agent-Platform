# External DingTalk browser-state isolation (2026-10-23)

Owner reports partner_auth_state_invalid after external login. Existing user authorization
covers diagnosis, repair, tests and safe rollout. Keep DingTalk website SNS identity,
FAE-only virtual identity mapping, optional canonical FAE public access and internal login.

## Evidence and failure layer

Production31f7de2f5bc66b1c15050104209f66b029f89477 retains the SNS authentication source
fromd0dea5d3. Read-only control records show completed verified/provisioned attempts at
10:14:50UTC and11:05:03UTC, followed by pending starts11:07:52,11:11:35,11:11:53UTC.
Latest callback401 responses stop before provider exchange. Historic logs have no cookies
or state; exact last user failure branch cannot be inferred. No identity content was read.

A deterministic local HTTP reproduction proves two starts overwrite the single
__Host-fae-dt-nonce cookie, so the first valid state can no longer match its stored nonce.
The provider was never invoked. This is a browser-authentication boundary error, not
DingTalk registration eligibility, model, or FAE question handling. Missing/expired cookies
also currently produce raw partner_auth_state_invalid with no usable browser recovery.

## Approved task behavior

Bind each pending external attempt to its own Secure HttpOnly SameSite=Lax __Host cookie,
with an opaque deterministic suffix derived from that attempt's validated state. State,
nonce and five-minute expiry remain server-validated; no anonymous/enterprise fallback,
no provider protocol switch, no callback replay and no client-provided identity. Each
callback reads/clears only its own cookie. Multiple active QR pages in one browser must
work independently, while another browser still cannot complete them. Bound pending
cookies to3, matching existing internal-login concurrency intent; when full, create no
new attempt and explicitly report pending-limit429, retaining existing valid QR flows.
Cookies expire after300seconds; clear only completed attempt. Do not reuse one global
nonce and do not adopt a client-supplied nonce to relax browser binding.

For browser requests explicitly accepting text/html, rejected invalid/expired/replayed
external callbacks return an explicit failure page and status401, fixed same-browser
instructions, a fixed canonical external-login retry link, and a canonical FAE return
link. No automatic retry, redirect loop or authorization downgrade. API clients keep
existing JSON status/code shape. Add no-store, no-referrer and restrictive CSP; no state,
code, nonce, callback query, account or provider payload is echoed or logged. Emit safe
fixed enum diagnostics for missing cookie vs invalid shape vs storage rejection only.
The recovery page and limits must not consume/retry valid auth or invoke provider calls.

## Scope and acceptance

Preserve SNS endpoints/credentials/signatures, enterprise routing, DB single-use/expiry,
revocation, FAE-only grants, structured encrypted identity and canonical launch. No DB
migration, Nginx edit, FAE rebuild, model call or production eval. Code must be reviewed,
focused tests use disposable databases, and a runtime process/container test covers
actual start/callback HTTP. Deploy only reviewed authentication files in a layer based
on the exact CURRENT production Platform image, preserving VOC and all other code/assets,
env, mounts, Compose inputs and services. Shared deployment locks, backup, source hashes,
controlled API-only replacement, healthy acceptance and rollback are required. No computer
use. A fresh real-user scan is a separate acceptance item; do not call it passed from
fake provider or deliberately invalid code tests. Preserve failed reproduction evidence.
