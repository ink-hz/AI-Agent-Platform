/usr/bin/docker run --cap-drop ALL --security-opt no-new-privileges:true --rm --read-only --user 10001:10001 \
  --network orbbec-agent-platform-internal \
  --tmpfs /tmp:rw,noexec,nosuid,size=8m,uid=10001,gid=10001,mode=0700 \
  -v orbbec-agent-platform-hr-agent-secrets:/run/hr-agent-secrets:ro \
  -v orbbec-agent-platform-api-secrets:/run/secrets:ro \
  -v /data/orbbec-agent-platform/hr-knowledge:/data/hr-knowledge:ro \
  -v /data/orbbec-agent-platform/hr-work:/data/hr-work:ro \
  PLATFORM_IMAGE_SHA python -m tools.hr_agent.preflight \
  --scope public-only \
  --api-env-file /run/hr-agent-secrets/api-runtime.env \
  --worker-env-file /run/hr-agent-secrets/worker-runtime.env \
  --database-url-file /run/hr-agent-secrets/control-database-url
