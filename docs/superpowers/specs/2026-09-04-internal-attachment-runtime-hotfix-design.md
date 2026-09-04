# Internal Attachment Runtime Hotfix Design

## Decision

The production Platform currently serves a small, explicitly authorized internal user set. Malware scanning is therefore removed from the R1.2 runtime critical path. The attachment pipeline continues to enforce configured size limits, accepted file types, SHA-256 integrity, immutable object reads, and sandboxed derivative generation.

Production sets `PLATFORM_ATTACHMENT_SCAN_MODE=trusted-internal`. In that mode the scan stage consumes the entire immutable object so the existing processor can verify byte count and SHA-256, then records a clean transition without contacting ClamAV. Unknown modes fail closed. The ClamAV container, dependency, environment variables, and acceptance assertion are removed from the production Compose stack.

The post-cutover FAE invariance checks remain mandatory. Their HTTP reads gain bounded retry for transient connection resets; content digests must still match exactly.

## Acceptance

- The attachment worker becomes healthy without a ClamAV process.
- An integrity mismatch is still rejected before an attachment becomes ready.
- Production Compose has no ClamAV service or dependency.
- FAE identity, configuration, response content, Nginx configuration, and listeners remain unchanged.
- Only Platform/HR is deployed; no shared Nginx or other application is modified.
