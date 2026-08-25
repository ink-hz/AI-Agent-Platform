# DingTalk Multi-App Silent SSO Design

**Date:** 2026-08-25

## 1. Goal

Make the existing DingTalk internal application **AI行政小助理** a mobile and desktop workbench entry for:

`https://agent.orbbec.com.cn/office/`

An active employee opening that entry inside DingTalk must reach AI ADMIN without typing a username or password, scanning a QR code, or confirming a second login. Agent Platform remains the only identity and employee-directory authority, and all applications resolve to the same `internal_user_id`.

## 2. Confirmed Product Boundary

- The DingTalk application contains the administrative portal only.
- Its mobile and desktop homepages are both `/office/`.
- `/office/agent/` remains the administrative-agent conversation entry inside the portal.
- Agent Platform pages and administration are not embedded in the DingTalk application.
- Opening the URL outside DingTalk continues to use the existing Platform login experience.
- “Silent login” means trusted DingTalk in-client identity exchange, never anonymous access.

## 3. Current State

Agent Platform already:

- synchronizes the corporate DingTalk directory;
- owns the canonical employee and `internal_user_id` mapping;
- issues the host-wide `__Host-platform_session` cookie;
- supports `dd.requestAuthCode` for its own DingTalk application;
- redirects a session-less AI ADMIN user to `/login?return_path=%2Foffice%2F`.

The existing **AI行政小助理** application has a different AppKey from Agent Platform. Its AppKey matches the AI ADMIN robot/work-notification application, so the application should be reused rather than duplicated.

## 4. Architecture

Agent Platform becomes a generic multi-application DingTalk in-client identity gateway.

### 4.1 Application registry

The Platform application remains the built-in default login application. Additional trusted in-client applications are loaded from one root-only JSON secret file configured by:

`PLATFORM_DINGTALK_IN_CLIENT_APPS_FILE`

Schema version 1:

```json
{
  "schema_version": 1,
  "apps": [
    {
      "id": "office",
      "app_key": "public-dingtalk-app-key",
      "app_secret": "secret-value",
      "return_paths": ["/office/"]
    }
  ]
}
```

Rules:

- `platform` is reserved for the existing built-in application.
- Application IDs match `^[a-z][a-z0-9_-]{0,31}$`.
- AppKeys and AppSecrets are non-empty bounded strings.
- Return paths are exact local paths accepted by the existing return-path validator.
- No two additional applications may own the same return path.
- Every registered return path must pass the existing Platform return-path validator; adding a future application is a root-only registry change, not an application-code change.
- The registry path and contents are never returned by an API or written to logs.
- When the optional registry is absent, current single-application behavior is unchanged.

### 4.2 Login selection

The existing Platform login page already receives `return_path=/office/`. It passes this validated return path to the public DingTalk configuration endpoint.

- `/office/` selects application ID `office`.
- All existing Platform return paths select the built-in `platform` application.
- Selection occurs on the server. The browser does not submit an AppKey or AppSecret.

The public configuration response becomes:

```json
{
  "client_id": "selected-public-app-key",
  "corp_id": "corporate-id",
  "app_id": "office"
}
```

The response remains `Cache-Control: no-store`.

### 4.3 In-client exchange

The browser calls `dd.requestAuthCode` with the selected public AppKey and corporate ID, then submits:

```json
{
  "code": "single-use-dingtalk-code",
  "app_id": "office"
}
```

Platform resolves `app_id` only through its immutable server-side registry and exchanges the code with that application's AppSecret. The resulting DingTalk `userid` and `unionid` are resolved through the existing directory snapshot and identity resolver. The same Platform session and CSRF policy are used for every application.

For rolling and cached-client compatibility, an omitted `app_id` continues to select `platform`.

### 4.4 AI ADMIN boundary

AI ADMIN remains unchanged:

- it receives the Platform cookie only through the existing same-host request path;
- it calls Platform `/api/v1/account` through the existing trusted adapter;
- it never receives an AppSecret, raw DingTalk token, authorization code, `userid`, or `unionid`;
- its administrative authorization continues to use the Platform identity projection and existing allowlists.

## 5. Security

- AppSecrets are available only to the Platform API container through a read-only secret volume.
- Directory and stream workers retain their existing primary credentials and do not receive the additional application registry.
- Unknown application IDs, malformed registries, duplicate routes, organization mismatches, inactive employees, and invalid codes fail closed.
- Public requests cannot register applications, override AppKeys, or supply AppSecrets.
- Existing login rate limits, one-time attempts, trusted-proxy handling, session limits, and CSRF enforcement remain in force.
- Authentication failures return existing controlled login errors without provider payloads or secrets.
- No Nginx, FAE, database schema, or AI ADMIN model changes are required.

## 6. DingTalk Application Configuration

For **AI行政小助理**:

- Capability: 网页应用
- AgentId: `4698337019`
- Effective clients: 移动端、PC端
- Mobile homepage: `https://agent.orbbec.com.cn/office/`
- PC homepage: `https://agent.orbbec.com.cn/office/`
- Management homepage: unset
- Availability: active employees who should use administrative services

The application version is published only after Platform production contains the trusted `office` registration.

## 7. Deployment

1. Build and test Platform with the optional registry absent to prove backward compatibility.
2. Install the root-only registry secret on the target without printing it.
3. Populate only the `platform-api-secrets` volume with the registry.
4. Release Platform through its existing deployment workflow.
5. Verify existing Platform login and account access.
6. Verify the public config selects `office` only for `/office/`.
7. Publish the DingTalk webpage capability.
8. Verify first-use and existing-session entry on DingTalk PC and mobile.

No FAE container, image, configuration, route, or process is modified or restarted.

## 8. Acceptance Criteria

- A first-time active employee opens **AI行政小助理** in DingTalk and reaches `/office/` without visible login interaction.
- The employee maps to the same `internal_user_id` already stored by Platform.
- Existing Platform in-client and QR login continue to work.
- A normal browser without a Platform session is not granted anonymous access.
- Ordinary employees cannot enter administrative management pages unless existing AI ADMIN policy allows them.
- Unknown or mismatched application IDs fail closed.
- AppSecret, authorization code, cookies, `userid`, and `unionid` do not appear in responses or logs.
- Platform backend and frontend test suites pass.
- AI ADMIN business code, Nginx, and FAE remain unchanged.

## 9. Rollback

- Unpublish or remove the DingTalk webpage capability if the workbench entry must be disabled.
- Remove the registry-file environment variable and redeploy the prior Platform release to restore single-application login.
- Do not alter employee-directory data or revoke existing sessions solely for rollback.
- Do not restart or modify FAE.
