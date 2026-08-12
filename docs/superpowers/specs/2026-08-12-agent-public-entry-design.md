# Agent Platform Public Entry Design

**Date:** 2026-08-12  
**Status:** Approved for implementation  
**Scope:** Temporary administrator-only public entry for the sanitized cloud replica

## Goal

Publish the existing read-only cloud replica at
`https://agent.orbbec.com.cn` without exposing port 8080, weakening the
sanitization boundary, changing FAE behavior, or using macOS Keychain. The
release provides a temporary shared administrator login until DingTalk or
Feishu identity and per-user authorization are available.

## Security boundary

- Nginx is the only public entry. Platform continues to bind to
  `127.0.0.1:8080`; PostgreSQL and the importer remain private.
- Every HTTPS path, including `/api/health`, static assets and all read APIs,
  requires HTTP Basic authentication. HTTP serves only ACME challenges and a
  permanent redirect to HTTPS.
- The credential is for a small administrator group only. It is not an
  employee identity, department authorization, or HR data-sharing mechanism.
- The username is `agentadmin`. The password is generated locally, stored in
  an owner-only mode-0600 file outside Git, and streamed to the cloud over the
  existing SSH channel. Nginx stores only a salted password hash in a
  mode-0640 `root:www-data` file so its unprivileged workers can authenticate.
  Neither plaintext nor hash is printed.
- The deployment is non-interactive and never invokes Keychain, a password
  dialog, or a browser credential helper.
- The application remains `cloud-replica` and `read_only=true`. Its deployment
  status reports `auth=basic-auth` while the public entry is enabled and keeps
  the previous `ssh-tunnel` default for existing and rollback releases.
- A one-second failed-auth delay reduces online guessing. TLS is limited to
  1.2 and 1.3; the domain sends HSTS, `X-Content-Type-Options`,
  `X-Frame-Options`, and `Referrer-Policy` headers.

## Components and flow

The repository owns three release assets:

1. A tested Nginx template with exact `agent.orbbec.com.cn` HTTP and HTTPS
   virtual hosts. HTTPS authenticates before proxying to `127.0.0.1:8080`.
2. A remote installer that validates inputs, backs up Nginx and Platform
   environment state, installs the password hash, obtains or reuses the
   Let's Encrypt certificate, replaces only the prior Agent deny blocks,
   reloads Nginx, switches Platform's reported auth mode, and verifies the
   authenticated and unauthenticated paths.
3. A local publisher that validates a mode-0600 deployment configuration and
   password file, transfers the reviewed release assets with non-interactive
   SSH, invokes the installer with the password on standard input, and then
   performs public acceptance checks.

The current FAE HTTP and HTTPS server blocks remain functionally unchanged.
The installer removes only the exact temporary Agent HTTP 404 block from
`fae-domain-http.conf` and disables only `agent-domain-deny.conf`, both of
which were created to reserve the Agent hostname before this release. It
captures backups before those changes and writes a root-only rollback script.

## Failure and rollback behavior

The installer is fail-closed. Before the final authenticated virtual host is
ready, HTTPS continues to reject the Agent hostname. Any invalid configuration,
certificate failure, failed Nginx test, failed Platform restart, or failed
acceptance check restores the prior Nginx files, Platform environment, and
running Platform release, then reloads Nginx. It never restarts or recreates
the FAE container.

The rollback script restores the saved Nginx tree and Platform environment,
restarts only `platform-api` and `platform-loopback` when necessary, validates
Nginx, reloads it, and confirms the original FAE and loopback listener
invariants.

## Acceptance criteria

1. `http://agent.orbbec.com.cn/` returns 308 to the same HTTPS path.
2. Unauthenticated and incorrect-password HTTPS requests return 401 and never
   contain Platform page or API content.
3. Authenticated `/`, static assets, `/api/health`, `/api/deployment`,
   `/api/agents`, and `/api/sessions` succeed.
4. `/api/deployment` reports `cloud-replica`, `read_only=true`, and
   `auth=basic-auth`.
5. TLS certificate and hostname validation succeed; TLS 1.0 and 1.1 fail while
   TLS 1.2 and 1.3 succeed.
6. Port 8080 remains loopback-only and PostgreSQL/importer remain unpublished.
7. FAE's container identity, image, start time, health, domain behavior and
   legacy IP behavior are unchanged.
8. No plaintext password, password hash, source payload, provider ID, customer
   name, candidate name, attachment bytes or Keychain operation appears in
   repository files or acceptance output.
9. Nginx configuration passes `nginx -t`; Certbot renewal remains enabled and
   its deploy hook validates and reloads Nginx.
10. A root-owned backup and executable rollback script exist before the entry
    is accepted.

## Explicit non-goals

- No DingTalk, Feishu, SSO, MFA, per-user, department, or HR authorization in
  this release.
- No public write APIs, Agent controls, Review mutation, replay, or attachment
  download.
- No change to sanitization, one-year retention, signing, encryption, backup,
  importer, or one-way synchronization protocols.
- No real-data backfill or five-minute synchronization enablement as part of
  the domain publication. Until those separate gates pass, the public UI may
  show the current empty replica and `freshness=unavailable`.

## Future replacement

DingTalk or Feishu identity will replace Basic Auth behind the existing
`AuthProvider` boundary. That release must add named users, administrator /
department owner / internal user roles, explicit Agent assignments, and a
separate HR permission domain before the credential is distributed beyond the
small administrator group.
