# External DingTalk pending-browser-state repair — production

Owner report: partner_auth_state_invalid. Architecture layer: browser authentication / channel.
This release does not change DingTalk SNS registration/protocol, account provisioning,
FAE-only grants, enterprise login, FAE optional public access, or model/Agent behavior.

## Diagnosis and scope

The single global __Host-fae-dt-nonce cookie was replaced at every external-start request.
Real disposable PostgreSQL and HTTP cookie-jar reproduction confirmed two valid pending
QR pages in one browser made the first callback fail401 before the provider was called.
Historical production error records lacked cookie/state diagnostics, so this exact last
user failure cannot be attributed uniquely. Read-only production metadata also showed
successful verified/provisioned attempts at10:14:50UTC and11:05:03UTC before this repair;
these do not certify the current user's account or cross-organization eligibility.

Each attempt now owns a state-derived opaque __Host cookie, Secure/HttpOnly/Lax,300second
TTL; callback reads and clears only that cookie. DB nonce binding,5minute expiry,single
use/replay,revocation,server-verified provider identity and FAE-only access remain intact.
Request-level admission rejects a request presenting>=3 pending cookies before creating
another attempt. This is advisory under simultaneous request snapshots: overlapping
starts may exceed3, but all remain independent and subsequent>=3 requests reject. No
new DB/global coordination or browser-identity fallback is introduced.

Browser invalid/expired/replayed callbacks now receive an explicit401 HTML recovery
page with fixed fresh-login/canonical return links and no-store/no-referrer/CSP. JSON API
clients retain stable status/code shape. Missing/malformed/duplicate/legacy cookies never
bypass binding. Only safe rejection enums are logged; no state/code/nonce/account payload.

## Verification

- Auth implementation e0373571:13 focused files688 passed,0 failed,0 skipped;140 inherited
  deprecation warnings. Includes real disposable PostgreSQL/SNS-mocked integration.
- Concurrency test1cfab378: updated browser suite33 passed (overlaps688); identical2-cookie
  request snapshots create distinct additional states, strangers fail, all4 valid callbacks
  independently complete, later>=3 start rejects, and replay fails. No runtime-code change.
- Independent final/security review GO for sourcec0e13591a3577cdba553b6754118fd6783a1dfb2,
  no critical/important findings; advisory admission limitation explicitly accepted.
- Actual candidate API process in read-only network-none Docker:22/22 checks passed with
  fake provider/store, no production credentials mounted. These are not real-user scans.
- Image is an exact layer on current31f7de2f VOC API image; only3 Python files changed,
 621 existing files and all123 frontend assets retained, base layers/runtime config intact.
- Production receipt succeeded,29/29 acceptance checks passed, no rollback.34 existing
  Compose inputs retained and one API-only image/release overlay appended. Command,
  mounts, non-release environment,23 other containers, all6 Nginx site files,7 temp-directory
  permissions,12 complete page/static fingerprints, FAE build/English/vision/public policy
  and protected management/SDK controls preserved. Control DB/config snapshot retained.
- Live acceptance covers3 independently scoped cookies and advisory429, missing/malformed/
  duplicate/legacy-cookie callback401 HTML recovery and JSON API behavior, no callback
  token reflection, internal modern OAuth login-start200, old-entry308 and canonical
  guest empty chat422. It never sends a valid-single-cookie+code callback to the provider
  and never follows provider redirects. No production model calls/evals or computer use.

## Exact deployment

Source patch: c0e13591a3577cdba553b6754118fd6783a1dfb2 (final code/app files unchanged sincee0373571).
Base image: sha256:df5ecbefc2763f5206eb9d3f149713a1d7d0306600cc9137eb0c604bfa54adad
Candidate image: sha256:365663d9ec88bad705dfceba5cf0ade213d198293b22b5177daaec46646d0c69
Private receipt: /opt/orbbec-agent-platform/private/fae-browser-state-c0e13591a357-e2a81af5/receipt.json
Future maintenance: same directory/future-maintenance.json; preserve all35 Compose inputs
rather than reconstructing from the base file. Source/build/process proof retained in
/opt/orbbec-agent-platform/private/fae-external-browser-state-20261023/. No credentials
or real identity content are included in this report.

## Remaining acceptance

A fresh real-user scan is pending. The owner has been asked to reopen
https://fae.orbbec.com.cn/partner/login and use a newly generated QR. Do not reuse an
expired/old pre-release QR or claim a user's successful final FAE session from mock
provider/process/negative production checks. Missing-cookie browser handoff failures
remain explicit and require restarting in the same browser, never a nonce bypass.
