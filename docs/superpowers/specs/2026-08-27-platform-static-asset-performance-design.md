# Platform static asset performance design

## Goal

Make direct and repeated visits to Agent Platform workspaces load quickly without changing identity, VOC behavior, or the backend static-asset allowlist.

## Evidence

Production account and VOC bootstrap requests complete in 53–90 ms, while the 556 KB JavaScript entry took 5.799 seconds on a real request. The platform catch-all Nginx location disables proxy buffering and adds `Cache-Control: no-store`, so fingerprinted JavaScript and CSS are neither compressed nor reusable from browser cache.

## Design

Add a dedicated `location ^~ /assets/` before the catch-all platform proxy. It will:

- proxy to the existing platform API, preserving the manifest-based asset allowlist;
- preserve the backend cache policy so successful assets remain immutable while rejected assets remain `no-store`, and remove upstream cookie headers;
- enable gzip for the backend's `text/javascript` response as well as JavaScript, CSS, fonts, SVG, and JSON assets;
- retain the platform security headers;
- leave HTML, APIs, authentication, and non-fingerprinted paths on the existing no-store catch-all.

No CDN or frontend route splitting is included. Route-level code splitting can be evaluated separately if compression and caching do not provide sufficient first-load performance.

## Failure behavior

Unknown or non-manifest assets continue to be rejected by the backend. The immutable cache header is not emitted for rejection responses, so authorization failures cannot be cached as successful assets.

## Verification

- A deployment configuration test must fail before the route exists and pass after it is added.
- Existing Agent domain and deployment tests must remain green.
- Production `GET /assets/...` with compression support must return `200`, `Content-Encoding: gzip`, and immutable cache headers without `no-store`.
- Platform and VOC containers must remain healthy after deployment.
