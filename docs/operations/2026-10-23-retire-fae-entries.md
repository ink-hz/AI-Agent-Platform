# FAE duplicate entry retirement — deployed 2026-10-23

Owner instruction: only retain https://fae.orbbec.com.cn/; “直接干掉旧入口”.
Changed layer: channel / ingress. No Agent, model, prompt, data or identity-policy change.

## Result and exact scope

Controlled Nginx configuration transaction succeeded at 2026-10-23T10:48:15Z.
The default HTTP IP and explicitly named public-IP HTTP sites, Platform /fae workspace,
and canonical-host legacy /fae paths are retired. Browser GET/HEAD redirect308 to the fixed
canonical URL, safe conversation links preserve their identifier, and query strings are
dropped. Old APIs/mutations/assets return JSON410 fae_entry_retired without Location;
request bodies are never replayed across origins. Canonical FAE continues optional login.

Four exact actual source files were changed; all other directive bytes were preserved.
Named-IP ACME, IP /health, Platform /fae/manage protection, canonical internal/partner
launch, and SDK-owned literal-IP HTTPS site were retained. Never install the historical
whole Platform template over current live HR/calendar/SDK configuration.

FAE source commits dbda82a and40b3196; Platform4cc325e8, integrated with current master
as891200d11e0fb192a230fb82e81a57854cc4c4cd. FAE runtime remains2636f731fea44a45957a14cbb0789a0bd885731a,
release ai-fae-agent-20261023065512. Platform API image, all24 running container IDs,
public health including English/vision,12 complete page/static-resource fingerprints,
nginx user and7 temporary-directory owner/group/modes remained unchanged. No new
Nginx permission errors. Configuration deployment is separate from application deployment.

## Evidence

- Controller fresh FAE focused suite68 passed, one inherited Starlette warning.
- Platform scoped deployment suite151 passed,2 skipped,2 baseline SDK cases deselected;
  both SDK failures independently reproduced on exact d5e9401b baseline. Local Nginx
  skips are covered by independent Docker runtime checks. Merged-master focused3 passed.
- All6 enabled sites tested together in read-only network-isolated Docker, with fake
  upstreams: baseline10 passed/72 expected failures; final82 passed/0 failed. Includes
  default-vs-named IP selection, SDK443 ownership, ACME, method/prefix/deep-link behavior,
  and runtime security headers. These are ingress tests, not model/answer-quality evals.
- Production V3 receipt succeeded,54/54 checks passed. Actual full default nginx -t,
  atomic same-owner/mode installation under both shared deployment locks, controlled
  reload, before/after complete resource bytes, protected endpoints and error monitoring.
- Separate external public-DNS HTTP validation6/6 passed: both old entry redirects308,
  both old chat endpoints410, canonical page200, canonical empty chat body422. No valid
  question or model call was sent. No computer use.
- Independent final review GO, no critical/important findings; source/candidate hashes,
  source regeneration, idempotence, preservation and transaction checked independently.

Private production evidence: /opt/orbbec-agent-platform/private/fae-entry-retire-20261023/
production-receipt-v3.json plus original configs, isolated receipts and earlier failure
receipts. Directory root:root0700; no credentials included in this report.

## Preserved rollout failures

Attempt1 restored all source bytes automatically: the explicit public-IP HTTP vhost
fae-ip-http.conf overrode the default site and was missing from the initial3-file set.
The fix added its own guarded transform/template and expanded isolation to all6 sites.
Attempt2 also restored automatically: the controller incorrectly expected anonymous
/fae/manage/200; its correct existing protected response is401. V3 explicitly requires
manage401/health200/SDK302 before changes and compares the complete original control
responses afterward. No management permission was relaxed. No candidate changed between
attempt2 and3; only the erroneous checker was corrected. Both failed receipts remain.

Historical17:10:49 and17:20:21 chat401 requests did not reach Agent trace/persistence;
recorded logs lack hostname/session/question. This closes duplicate-entry use but does
not identify those exact callers or claim every401 is a bug. Real external DingTalk
registration eligibility/account acceptance remains pending; public FAE stays enabled.

## Exact candidate SHA-256

- named-ip: 4c58a6eaa2111c66afe0b897dd475dd4f56d33c525efad6ecc49c35b3dd3286b
- ip: 72502a0bfce03a90d2e00c7fd2cad47dbbcc05e8b4ebebb9e5f1ebb71df71bb4
- agent: ff83eb990c1f8a54d5825f77d43a1e643940428fb4933f23f822548872e36584
- canonical: e8f1071bced967a0425f5d67343314bbb71a2c8aca706f35a5fe38867fde409c
