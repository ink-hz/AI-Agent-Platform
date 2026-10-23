# 2026-10-23 FAE internal login recovery

## Evidence and cause

Newest QR start requests returned200 then403/37bytes twice, with one pending OAuth attempt and no failed exchange. The valid Platform session still rendered /login; frontend QR and in-client mutations omitted the active session CSRF header, producing CSRF verification failed before the provider exchange.

Fix: GET/login validates and resumes the existing session to the requested local workspace, preserving preview-local or already-prefixed targets, and renews the browser challenge. Cached login actions bootstrap CSRF from the protected account endpoint; only401 continues anonymously. Middleware Origin/CSRF, enterprise corp/directory checks and revocations remain intact.

## Verification and publication

- RED: six initial server and four frontend credential cases failed as expected. Preview review added three failing cases before correction.
- GREEN:250 affected API/identity tests,156 auth/login frontend tests, production TypeScript/Vite build. After preview correction15 affected cases passed. Merged external feature+hotfix220 auth/integration tests passed later (overlapping runs).
- Independent code review approved16d8c5b8 after preview contract fix. Baseline three BLE001 issues in unchanged routes were not altered; import checks and diff whitespace passed.
- Exact production release16d8c5b8afcc1c8015d67e612052352787d04274 is on origin/master. API-only transaction reused28 existing Compose files. Candidate diff limited to platform-api image and PLATFORM_RELEASE_SHA; runtime environment/command/mount parity checked.
- First preflight stopped before switch because Compose JSON preserves dollar escaping. Confirmed every command argument exactly equal after Compose dollar unescaping; retry passed. No production failure or rollback occurred.
- API healthy,restart0; all other running container IDs unchanged; Nginx unchanged. Public login no-store200; script /assets/index-CmdZrz-k.js has1080227 bytes and SHA256 matches the container file; anonymous account401, platform health200, anonymous FAE history401.
- User explicitly confirmed: 内部登录OK了. This is the real internal acceptance; external real-account acceptance remains separate.

Private transaction receipts and prior config are under /opt/orbbec-agent-platform/private/fae-login-16d8c5b8afcc-* with0600 files. Prior API image a3cac583e40f30256ace6a6a26075e4a8afec771 and all28 Compose files are retained. No database migration or model/eval question was sent for this fix.
