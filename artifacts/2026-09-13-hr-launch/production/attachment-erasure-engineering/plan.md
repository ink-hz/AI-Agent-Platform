# Attachment Erasure Canary Implementation Plan

**Goal:** Prepare and request deletion of one synthetic attachment before any HR assembly, with independent physical-erasure evidence required.
**Architecture:** Reuse frozen private configuration/journal helpers; separate account/attachment-only HTTP workflow. Parent approved prepare → erase → observe and a non-renewing 300-second mutation deadline; post-expiry GET-only observation is separately bounded to20seconds.
**Tech Stack:** Python/httpx; disposable real PG/session/auth/attachment services, local object store.

- [x] RED: missing script; genuine HTTP prepare/erase and expired mutation deadline cases.
- [x] Implement three stages, exact own metadata/content digest binding, 204 handling, conservative transport ambiguity, no HR/SQL/model/service commands.
- [x] GREEN: real identity/PG/attachment processor and erasure service; retain API404 before local object removal to disprove physical-erasure overclaim. Test original ID preserved, no HR schema, deadline observer and uncertain DELETE.
- [x] Save exact sources/logs/hashes and private synthetic receipts; commit only owned new files, with physical S3/production unverified.
