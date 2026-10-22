# DAQ runtime health in the Platform admin views

The DAQ runtime probe is opt in. Set `PLATFORM_DAQ_HEALTH_URL` to the reviewed,
reachable DAQ production `/health` URL in the Platform API environment. The
reviewed internal endpoint for the current deployment is
`http://172.31.0.9:8081/health`. An unset or blank value keeps the existing
camera FAE and ADMIN monitor behavior. The URL must use HTTP or HTTPS, target
`/health`, and contain no credentials, query, parameters, or fragment.

In local mode, the existing remote polling cycle includes the configured DAQ
GET. In `cloud-replica` mode, only the configured DAQ HTTP poller starts; camera
and ADMIN SSH polling and local runtime pollers stay disabled. Both modes use
`PLATFORM_REMOTE_POLL_INTERVAL` and `PLATFORM_PROBE_TIMEOUT`. `start_poller=False`
also disables the DAQ poller. The application lifespan cancels it on shutdown.

DAQ starts as `unknown` with `not_checked`. A successful GET is healthy only if
the JSON object reports `status=ok`, `agent_id=ai-daq-fae-agent`, and
`environment=production`, without `local_dev_only=true`. Wrong identity or
environment, invalid JSON, HTTP errors, and service errors remain visible as
failed checks. Transport failures and timeouts replace the previous DAQ status
with `unknown`; previous healthy data is not reused as current evidence. HTTP
errors retain their actual status code in `details.http_status`; redirects are
not followed.

The returned health object is retained in `RemoteAgentStatus.details`, including
runtime, main knowledge and SDK knowledge release identifiers. `provider_mode`
supplies `backend`. A model is populated only from a real string `llm_model`
field; the current DAQ health response has no model field. Engine and uptime are
not inferred. `source_checked_records=0` is preserved as reported and does not
establish that a knowledge release is empty. Session trace model observations
remain a separate source of actual model evidence.

DAQ usage freshness follows the remote FAE/ADMIN rule: sync data is fresh for
36 hours and stale when its sync timestamp is absent or older. Recent usage
does not override an unknown or failed runtime state.

Local regression coverage is in `backend/tests/test_daq_remote_health.py`,
`backend/tests/test_daq_runtime_config.py`, and the DAQ cases in
`backend/tests/test_fleet_service.py`. The HTTP adapter fixtures use a loopback
HTTP server and do not call a model or a production service. Runtime health
alone does not establish data ingestion, session availability, or task support.
