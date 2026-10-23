# Required-login FAE production acceptance — 2026-10-23

Owner instruction: “那我们把匿名访问收缩掉吧”, after two real external SNS/FAE
session/history successes at20:00 and20:01CST. This explicitly supersedes temporary public
consultation. Scope: channel/ingress and the necessary internal browser launch handoff.
No Agent/model/prompt/knowledge/evaluation-quality behavior change.

## Deployed behavior

FAE_PUBLIC_ACCESS_ENABLED=false. Canonical shell and health report authentication_required
true. Anonymous chat,history,feedback,attachment upload/read/delete,authenticated history,
Review and docs reject401 before work. Login/static/health remain accessible. Only valid
internal enterprise or external SNS identities can use business APIs; ownership,CSRF,
revocation and separate administration remain enforced. No old anonymous-session
reassignment/data deletion or invalid-cookie downgrade.

Internal button now uses https://agent.orbbec.com.cn/agents/ai-fae-agent instead of the
retired /fae/ return. The exact public GET returns only a static handoff shell and fixed
same-origin script. The browser loads private account data, obtains its existing CSRF,
then POSTs the existing protected launch API and accepts only the literal canonical
platform_launch URL. No GET launch mutation,CORS expansion,inline script,dynamic redirect,
automatic uncertain-POST retry or anonymous fallback. External /partner/login is retained.

Platform code5412eea6 + prefix consumer correction9926939e deployed first, image
sha256:8b91139c37b5de95310d468f155823f1349ab74495c35f426ea3e54baf8c01d0.
Exact basec0e13591 image retains the SNS nonce repair and VOC; only3 Python files changed,
622 other files including123 frontend assets retained.35 prior Compose inputs retained,
one API-only image/release overlay appended. Its16 live checks passed with no rollback.

FAE standard release ai-fae-agent-20261023122837, runtimef4fc465c060dbf6ec119a9cb2d8ebb90db27273c,
manifest succeeded,no rollback. This source is a descendant of prior runtime2636f731,
so October English,vision,feedback and session fixes remain in lineage. Only the internal
frontend URL changed in app code; environment diff is exactly public_access true→false.
Once applied, any operational rollback must retain false rather than reopening anonymous.

## Verification

- Platform focused261 passed before prefix follow-up; tiny-fix run58 passed (39 handoff
  plus19 affected auth cases,188 deselected). Root/prefixed handoff executes actual JS and
  actual existing auth.ts loginReturnPath/routePrefix/platformPath/startQrLogin consumers.
  Independent reviewer GO after resolving prefix double-application. Merged-main39 passed.
- FAE focused identity/ownership199, controller auth/config/server255 and frontend72
  passed; overlaps are not summed. Required/optional internal and partner identity,
  owner isolation,CSRF,revoke and no-App-before-identity are covered with test providers.
- Standard full FAE gate3724 passed,3 existing requires_llm deselected,6 inherited warnings;
 278 frontend passed;TypeScript/Vite,artifact audit,backup,deploy,health/resource/log gates passed.
- Actual Platform API process:10/10 static/public/private-boundary checks in read-only,
  network-none container with fake auth; no production secrets/provider exchanges.
- Dedicated closure acceptance46/46 passed: mandatory meta/health,new compiled internal
  URL,anonymous business methods401 incl correct HEAD,no privateAPI bypass,external SNS
  redirects/internal bridge available,old IP/Platform entries still308 canonical,exact
  unrelated resource fingerprints,all6 Nginx file hashes,7 temp ownership/modes,23 other
  container IDs,ingress services and6 existing internal/partner session records preserved.
  English/vision/model/catalog health fields unchanged. No new Nginx permission errors.
- Public-DNS independent HTTP7/7 passed:health+canonical page200,anonymous chat/history/
  attachment401,external302 andinternalbridge200. No valid production question/model call,
  production eval or computer use occurred.

The independent code review is /private/tmp/fae-require-login-20261023/security-review.md.
Initial P2 preview return_path mismatch was reproduced against actual login-page consumer,
fixed using local suffix and single prefix application,and re-reviewed GO. No critical or
important finding remains. Runtime security is enforced by existing APIs,not this shell.

## Operational evidence

Private FAE backup/config/acceptance:
/opt/ai-fae-agent/backups/require-login-20261023/
- env.production.before,snapshot.json,env-change.json,prior-inspect.json,compose.before.json;
- post-bridge inventory.before-fae-release.json,sessions.before.json,acceptance.json.
DB backup: /opt/ai-fae-agent/backups/ai_fae_20261023203159.sql.gz.
Standard manifest: dist/release/manifests/ai-fae-agent-20261023122837.json.
Platform bridge receipt:
/opt/orbbec-agent-platform/private/fae-login-bridge-9926939e8342-3d4ca270/receipt.json;
same directory/future-maintenance.json retains all36 current Compose inputs.
Image/process proof: /opt/orbbec-agent-platform/private/fae-require-login-20261023/.
No credentials/account content is present in this report; backups remain private.

Two post-release probe assumptions were corrected without application changes or reopening
anonymous: attachment GC deleted_total is a process-local counter(initialized0),so its29→0
restart reset is recorded separately from feature configuration; generic curl -X HEAD
waited for an advertised body,so the HEAD acceptance uses --head and confirms401. All other
health fields/config and source checks remain exact. Failed probe evidence is retained in
the controller transcript; final acceptance records the explicit counter reset.

## Real-user acceptance boundary

Existing six authenticated records of both subject types are preserved. Two external
real-user successes predate this flag-only restriction and remain valid evidence of the
SNS/mapping/FAE exchange path. A fresh actual internal-login click through the new bridge
is still pending user response; an optional test link was provided during rollout. No
existing cookie was extracted or impersonated to manufacture a production positive case.
Do not conflate mocked/Node/process checks with that final browser observation. Anonymous
restriction itself is live and verified; already-open anonymous pages must refresh/login.
