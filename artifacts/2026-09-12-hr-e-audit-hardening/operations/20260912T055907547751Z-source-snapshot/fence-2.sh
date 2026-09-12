hr_compose=(
  /usr/bin/docker compose
  --env-file /opt/orbbec-agent-platform/private/hr-agent/runtime.env
  -f /opt/orbbec-agent-platform/current/deploy/cloud/compose.yaml
  -f /opt/orbbec-agent-platform/current/deploy/cloud/compose.hr-agent.yaml
  --profile hr-agent
)
