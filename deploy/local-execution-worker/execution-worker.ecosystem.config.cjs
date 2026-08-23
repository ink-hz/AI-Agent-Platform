module.exports = {
  apps: [{
    name: 'orbbec-agent-execution-worker',
    script: '/Users/agentops/AgentRuntime/platform/backend/.venv/bin/python',
    args: ['-m', 'app.execution_relay.worker'],
    cwd: '/Users/agentops/AgentRuntime/platform/backend',
    autorestart: true,
    restart_delay: 30000,
    max_restarts: 10,
    min_uptime: '10s',
    out_file: '/Users/agentops/AgentRuntime/log/execution-worker.out.log',
    error_file: '/Users/agentops/AgentRuntime/log/execution-worker.err.log',
    env: {
      PLATFORM_WORKER_ID: 'agentops-mac-primary',
      PLATFORM_WORKER_KEY_ID: 'worker-v1',
      PLATFORM_WORKER_PRIVATE_KEY_FILE: '/Users/agentops/AgentRuntime/private/execution-worker-ed25519.key',
      PLATFORM_WORKER_DATABASE_URL_FILE: '/Users/agentops/AgentRuntime/private/execution-worker-postgres-dsn',
      PLATFORM_WORKER_CALLBACK_PORT: '9120',
      PLATFORM_WORKER_CLOUD_URL: 'https://agent.orbbec.com.cn',
      PLATFORM_METABOT_RUNTIME_CONTRACT: '/Users/agentops/AgentRuntime/metabot/runtime-contract.json',
      PLATFORM_METABOT_API_SECRET_FILE: '/Users/agentops/AgentRuntime/private/metabot-api-token',
    },
  }],
};
