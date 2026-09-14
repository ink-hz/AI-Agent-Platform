The final-1/final-2 `ruff check three source files` shorthand denotes this exact argv, executed with cwd `/Users/neo/Developer/work/AI-Agent-Platform/.worktrees/hr-cloud-loop-e-release`:

```text
backend/.venv/bin/ruff check artifacts/2026-09-14-hr-launch/config-install/incremental_install.py artifacts/2026-09-14-hr-launch/config-install/test_incremental_install.py artifacts/2026-09-14-hr-launch/config-install/validate_local.py
```

Both invocations exited 0; raw output is retained in each `ruff.log`. Pytest and local-load commands are already recorded exactly in their respective receipts. Final-2 supersedes final-1 only as the latest source state; previous artifacts remain unchanged.
