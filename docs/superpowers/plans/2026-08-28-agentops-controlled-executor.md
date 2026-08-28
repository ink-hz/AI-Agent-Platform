# AgentOps Controlled Executor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace repeated macOS administrator-password prompts with a root-owned, allowlisted executor that lets Platform acceptance invoke only six fixed `agentops` operations without storing any password.

**Architecture:** A root-owned dispatcher under `/Library/PrivilegedHelperTools` validates its own installation and maps six names to fixed commands, paths, arguments and environments. A root-owned sudoers fragment grants `neo` passwordless access only to those exact dispatcher invocations. Platform acceptance calls the dispatcher by name; a dedicated, source-restricted `agentops` SSH key replaces the temporary copy of Neo's key.

**Tech Stack:** Bash 3.2, macOS `sudo`/`visudo`, OpenSSH Ed25519, pytest on Python 3.11, existing Platform cloud acceptance scripts.

## Global Constraints

- Never save, extract, forward or log a macOS password.
- Never use `sudo -S`, `security find-generic-password -w`, a generic `sudo -u agentops /usr/bin/env *` rule, or an arbitrary shell entry point.
- The dispatcher is root-owned, non-symlink, mode `0755`; the sudoers fragment is root-owned, non-symlink, mode `0440`.
- The dispatcher accepts exactly `relay-canary`, `worker-stop`, `worker-restore`, `metabot-release-sha`, `agent-team-release-sha`, or `status`, each with zero additional arguments.
- AI ADMIN `/office/`, FAE, Nginx, business databases and Agent business logic remain unchanged.
- Preserve the user's unrelated untracked files.
- Development is local; only `master` may be pushed.

---

### Task 1: Root-owned AgentOps command dispatcher

**Files:**
- Create: `deploy/local-execution-worker/agentops-control.sh`
- Create: `backend/tests/test_agentops_control.py`

**Interfaces:**
- Consumes: fixed `agentops` runtime at `/Users/agentops/AgentRuntime` and the existing `accept.sh`/`worker-pm2.sh` scripts.
- Produces: `agentops-control.sh <allowed-command>` with stable stdout and exit status; no free-form command API.

- [ ] **Step 1: Write failing dispatcher contract tests**

Add tests that read the production script and materialize a test copy with `required_user` and fixed paths replaced by temporary fake executables. Cover exact success commands, unknown commands, extra arguments, wrong user, symlink self-path, unexpected owner/mode, sanitized environment, fixed working directory and redacted audit output.

```python
ALLOWED = {
    "relay-canary",
    "worker-stop",
    "worker-restore",
    "metabot-release-sha",
    "agent-team-release-sha",
    "status",
}

def test_dispatcher_exposes_only_the_frozen_command_set() -> None:
    source = CONTROL.read_text(encoding="utf-8")
    for command in ALLOWED:
        assert f"{command})" in source
    assert "eval " not in source
    assert 'exec "$@"' not in source
    assert "sudo -S" not in source
    assert "find-generic-password" not in source

def test_dispatcher_rejects_unknown_command_and_extra_arguments(tmp_path: Path) -> None:
    dispatcher = materialize_dispatcher(tmp_path)
    assert run(dispatcher, "unknown").returncode == 1
    assert run(dispatcher, "status", "extra").returncode == 1
```

- [ ] **Step 2: Run the new test file and confirm red**

Run:

```bash
cd backend
.venv/bin/python -m pytest -q tests/test_agentops_control.py
```

Expected: FAIL because `agentops-control.sh` does not exist.

- [ ] **Step 3: Implement the fixed dispatcher**

The script must begin with `set -eEuo pipefail`, `umask 077`, require one argument, require user `agentops`, validate `HOME/USER/LOGNAME`, validate its production path with `lstat` semantics, and dispatch with a `case`. Each command runs under `/usr/bin/env -i` with fixed `HOME`, `USER`, `LOGNAME`, `PATH` and `cwd`. Use `/usr/bin/logger -t orbbec-agentops-control` for `{actor,command,phase,exit_code}` only.

```bash
case "$command" in
  relay-canary)
    run_fixed "$runtime/platform/deploy/local-execution-worker/accept.sh" \
      "$private/acceptance-config.json"
    ;;
  worker-stop)
    run_fixed "$runtime/platform/deploy/local-execution-worker/worker-pm2.sh" stop
    ;;
  worker-restore)
    run_fixed "$runtime/platform/deploy/local-execution-worker/worker-pm2.sh" restore online
    ;;
  metabot-release-sha)
    run_fixed /usr/bin/git -C "$runtime/metabot" rev-parse HEAD
    ;;
  agent-team-release-sha)
    run_fixed /usr/bin/git -C /Users/agentops/Developer/work/Orbbec-Agent-Team rev-parse HEAD
    ;;
  status)
    /usr/bin/printf '%s\n' 'AGENTOPS_CONTROL_OK commands=6'
    ;;
  *) fail ;;
esac
```

- [ ] **Step 4: Run dispatcher tests and shell syntax checks**

Run:

```bash
/bin/bash -n deploy/local-execution-worker/agentops-control.sh
cd backend
.venv/bin/python -m pytest -q tests/test_agentops_control.py
```

Expected: all tests PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add deploy/local-execution-worker/agentops-control.sh backend/tests/test_agentops_control.py
git commit -m "feat: add controlled agentops dispatcher"
```

---

### Task 2: Transactional system installer and uninstaller

**Files:**
- Create: `deploy/local-execution-worker/agentops-control.sudoers`
- Create: `deploy/local-execution-worker/install-agentops-control.sh`
- Create: `deploy/local-execution-worker/remove-agentops-control.sh`
- Modify: `backend/tests/test_agentops_control.py`

**Interfaces:**
- Consumes: Task 1 dispatcher at a clean Git `HEAD`.
- Produces: one-time root installer and root uninstaller; exact installed paths `/Library/PrivilegedHelperTools/orbbec-agentops-control` and `/etc/sudoers.d/orbbec-agentops-control`.

- [ ] **Step 1: Add failing installer policy tests**

Assert the sudoers file has six exact lines and no wildcard, shell, `env`, `ALL=(ALL)`, or password suppression outside the named dispatcher commands.

```python
def test_sudoers_allows_only_exact_dispatcher_subcommands() -> None:
    source = SUDOERS.read_text(encoding="utf-8")
    for command in sorted(ALLOWED):
        assert (
            "neo ALL=(agentops) NOPASSWD: "
            f"/Library/PrivilegedHelperTools/orbbec-agentops-control {command}"
        ) in source
    for forbidden in ("*", "/bin/sh", "/usr/bin/env", "ALL=(ALL)", "sudo -S"):
        assert forbidden not in source
```

Add subprocess tests using a temporary root prefix and fake `visudo` to cover install, repeated install, injected failure rollback, wrong source owner/mode, symlink target and removal.

- [ ] **Step 2: Confirm the installer tests fail**

Run:

```bash
cd backend
.venv/bin/python -m pytest -q tests/test_agentops_control.py -k 'sudoers or install or remove'
```

Expected: FAIL because the installer assets do not exist.

- [ ] **Step 3: Implement exact sudoers policy**

Write one command specification per subcommand; do not use aliases with wildcard arguments.

```sudoers
neo ALL=(agentops) NOPASSWD: /Library/PrivilegedHelperTools/orbbec-agentops-control relay-canary
neo ALL=(agentops) NOPASSWD: /Library/PrivilegedHelperTools/orbbec-agentops-control worker-stop
neo ALL=(agentops) NOPASSWD: /Library/PrivilegedHelperTools/orbbec-agentops-control worker-restore
neo ALL=(agentops) NOPASSWD: /Library/PrivilegedHelperTools/orbbec-agentops-control metabot-release-sha
neo ALL=(agentops) NOPASSWD: /Library/PrivilegedHelperTools/orbbec-agentops-control agent-team-release-sha
neo ALL=(agentops) NOPASSWD: /Library/PrivilegedHelperTools/orbbec-agentops-control status
```

- [ ] **Step 4: Implement transactional install/remove scripts**

Both scripts require `id -u == 0`, accept no arguments and use exact absolute targets. The installer verifies the three source files are tracked and byte-identical to `git show HEAD:<path>`, stages same-directory candidates, runs `visudo -cf` on the candidate and complete sudoers tree, then atomically renames. It keeps root-only backups until `status` succeeds as `agentops`; any error restores both targets. The uninstaller removes only those two exact installed files and revalidates `/etc/sudoers`.

```bash
[[ $# -eq 0 && "$(/usr/bin/id -u)" == 0 ]] || fail
/usr/bin/install -o root -g wheel -m 0755 "$dispatcher_source" "$dispatcher_candidate"
/usr/bin/install -o root -g wheel -m 0440 "$sudoers_source" "$sudoers_candidate"
/usr/sbin/visudo -cf "$sudoers_candidate" >/dev/null || fail
/bin/mv -f "$dispatcher_candidate" "$dispatcher_target"
/bin/mv -f "$sudoers_candidate" "$sudoers_target"
/usr/sbin/visudo -cf /etc/sudoers >/dev/null || fail
/usr/bin/sudo -n -u agentops "$dispatcher_target" status \
  | /usr/bin/grep -Fqx 'AGENTOPS_CONTROL_OK commands=6' || fail
```

- [ ] **Step 5: Run policy and transaction tests**

Run:

```bash
/bin/bash -n deploy/local-execution-worker/install-agentops-control.sh
/bin/bash -n deploy/local-execution-worker/remove-agentops-control.sh
/usr/sbin/visudo -cf deploy/local-execution-worker/agentops-control.sudoers
cd backend
.venv/bin/python -m pytest -q tests/test_agentops_control.py
```

Expected: all tests PASS.

- [ ] **Step 6: Commit Task 2**

```bash
git add deploy/local-execution-worker/agentops-control.sudoers \
  deploy/local-execution-worker/install-agentops-control.sh \
  deploy/local-execution-worker/remove-agentops-control.sh \
  backend/tests/test_agentops_control.py
git commit -m "feat: install controlled agentops privilege boundary"
```

---

### Task 3: Migrate Agent Brain acceptance to named operations

**Files:**
- Modify: `deploy/cloud/accept.sh:79-84,183-190,738-743,1055,1105-1106`
- Modify: `backend/tests/test_agent_brain_deployment.py`
- Modify: `backend/tests/test_agent_brain_v2_acceptance.py`

**Interfaces:**
- Consumes: installed dispatcher subcommands from Tasks 1–2.
- Produces: `run_agentops_control <name>`; arbitrary path/argument execution is removed.

- [ ] **Step 1: Add failing acceptance-boundary tests**

```python
def test_cloud_acceptance_uses_only_agentops_control_commands() -> None:
    source = ACCEPT.read_text(encoding="utf-8")
    assert "run_agentops()" not in source
    assert "/usr/bin/env -i" not in source
    assert "/bin/sh -c 'cd /Users/agentops" not in source
    for command in (
        "relay-canary", "worker-stop", "worker-restore",
        "metabot-release-sha", "agent-team-release-sha",
    ):
        assert f'run_agentops_control {command}' in source
```

- [ ] **Step 2: Confirm the tests fail**

Run:

```bash
cd backend
.venv/bin/python -m pytest -q \
  tests/test_agent_brain_deployment.py \
  tests/test_agent_brain_v2_acceptance.py -k agentops
```

Expected: FAIL because `accept.sh` still exposes generic `run_agentops`.

- [ ] **Step 3: Replace the generic runner with exact dispatcher calls**

```bash
agentops_control=/Library/PrivilegedHelperTools/orbbec-agentops-control
run_agentops_control() {
  [[ $# -eq 1 ]] || fail
  case "$1" in
    relay-canary|worker-stop|worker-restore|metabot-release-sha|agent-team-release-sha) ;;
    *) fail ;;
  esac
  /usr/bin/sudo -n -u agentops "$agentops_control" "$1"
}
```

Map the five call sites exactly. Preserve the exact Relay success marker and restore trap semantics.

- [ ] **Step 4: Run focused deployment and acceptance tests**

Run:

```bash
cd backend
.venv/bin/python -m pytest -q \
  tests/test_agent_brain_deployment.py \
  tests/test_agent_brain_v2_acceptance.py \
  tests/test_agent_brain_live_acceptance.py
```

Expected: all tests PASS.

- [ ] **Step 5: Commit Task 3**

```bash
git add deploy/cloud/accept.sh \
  backend/tests/test_agent_brain_deployment.py \
  backend/tests/test_agent_brain_v2_acceptance.py
git commit -m "refactor: route acceptance through agentops control"
```

---

### Task 4: Dedicated source-restricted AgentOps cloud key

**Files:**
- Create: `deploy/cloud/provision-agentops-acceptance-key.sh`
- Create: `deploy/cloud/revoke-agentops-acceptance-key.sh`
- Create: `backend/tests/test_agentops_acceptance_key.py`
- Modify: `deploy/local-execution-worker/install-agentops-control.sh`

**Interfaces:**
- Consumes: Neo's existing `/Users/neo/.ssh/orbbec_aliyun_ed25519` only during provisioning.
- Produces: an `agentops`-owned `0600` key at `/Users/agentops/AgentRuntime/private/cloud-admin-ed25519` and one managed `restrict,from="<observed-source-ip>"` entry in cloud root `authorized_keys`.

- [ ] **Step 1: Write failing key-lifecycle tests**

Tests must assert Ed25519 only, empty passphrase only for the machine account key, source IP derived from the authenticated remote `SSH_CONNECTION`, one begin/end managed block, atomic `authorized_keys` rewrite, fingerprint verification before local installation, and exact revocation without touching unrelated keys.

```python
def test_key_policy_is_dedicated_restricted_and_atomic() -> None:
    source = PROVISION.read_text(encoding="utf-8")
    assert "ssh-keygen -q -t ed25519" in source
    assert 'source_ip="${SSH_CONNECTION%% *}"' in source
    assert 'restrict,from="${source_ip}"' in source
    assert "BEGIN ORBBEC AGENTOPS ACCEPTANCE KEY" in source
    assert "END ORBBEC AGENTOPS ACCEPTANCE KEY" in source
    assert "orbbec_aliyun_ed25519" in source
    assert "cloud-admin-ed25519.pending" in source
```

- [ ] **Step 2: Run key tests and confirm red**

Run:

```bash
cd backend
.venv/bin/python -m pytest -q tests/test_agentops_acceptance_key.py
```

Expected: FAIL because lifecycle scripts do not exist.

- [ ] **Step 3: Implement provisioning and revocation**

Provisioning runs as `neo`, acquires the existing Platform action lock, generates a pending key under the private acceptance directory, sends only the public key to a root SSH transaction, derives the actual source IP from `SSH_CONNECTION`, and replaces a single managed block. It then verifies a fresh SSH connection using the pending private key before exposing it to the installer.

The remote managed line has this shape:

```text
restrict,from="203.0.113.10" ssh-ed25519 AAAA... orbbec-agentops-acceptance:<fingerprint>
```

Revocation removes only the managed block after validating its exact marker count and leaves unrelated `authorized_keys` bytes unchanged.

- [ ] **Step 4: Extend the root installer to atomically install the pending key**

The installer requires a regular Neo-owned `0600` pending file, validates its fingerprint against the public receipt, backs up any existing exact target, installs it as `agentops:staff` `0600`, and restores the prior target on any later failure. Successful installation securely removes the pending private file through the root transaction. It never copies Neo's original key.

- [ ] **Step 5: Run lifecycle and installer regression tests**

Run:

```bash
/bin/bash -n deploy/cloud/provision-agentops-acceptance-key.sh
/bin/bash -n deploy/cloud/revoke-agentops-acceptance-key.sh
cd backend
.venv/bin/python -m pytest -q \
  tests/test_agentops_acceptance_key.py \
  tests/test_agentops_control.py
```

Expected: all tests PASS.

- [ ] **Step 6: Commit Task 4**

```bash
git add deploy/cloud/provision-agentops-acceptance-key.sh \
  deploy/cloud/revoke-agentops-acceptance-key.sh \
  deploy/local-execution-worker/install-agentops-control.sh \
  backend/tests/test_agentops_acceptance_key.py \
  backend/tests/test_agentops_control.py
git commit -m "feat: provision dedicated agentops cloud key"
```

---

### Task 5: Runbook, governance gates and complete regression

**Files:**
- Create: `docs/runbooks/agentops-controlled-executor.md`
- Modify: `README.md`
- Modify: `backend/tests/test_agentops_control.py`
- Modify: `backend/tests/test_agent_brain_deployment.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: operator procedure and repository gates that prevent password storage or a return to generic sudo execution.

- [ ] **Step 1: Add failing governance tests**

Scan production deploy sources for forbidden credential patterns and require the runbook's install, verify, rotate, revoke, rollback and emergency-removal commands.

```python
FORBIDDEN = (
    "sudo -S",
    "find-generic-password -w",
    "SUDO_PASSWORD",
    "ADMIN_PASSWORD",
    "neo ALL=(ALL)",
    "NOPASSWD: /usr/bin/env",
)

def test_deploy_sources_never_store_password_or_grant_generic_sudo() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in DEPLOY_SOURCES)
    for text in FORBIDDEN:
        assert text not in source
```

- [ ] **Step 2: Confirm governance tests fail**

Run:

```bash
cd backend
.venv/bin/python -m pytest -q tests/test_agentops_control.py -k governance
```

Expected: FAIL because the runbook and README link do not exist.

- [ ] **Step 3: Write the operational runbook**

Document exact commands for preflight, one-time installation, `sudo -n -u agentops ... status`, Relay Canary, formal Brain acceptance, key rotation, key revocation, uninstallation and failure rollback. Explicitly state that `/office/` and FAE must be checked before and after each acceptance.

- [ ] **Step 4: Run complete local verification**

Run:

```bash
cd backend
.venv/bin/python -m pytest -q
cd ../webui
npm test -- --run
npm run build
cd ..
git diff --check
```

Expected: backend has zero failures; frontend tests and build pass; `git diff --check` has no output.

- [ ] **Step 5: Commit Task 5**

```bash
git add docs/runbooks/agentops-controlled-executor.md README.md \
  backend/tests/test_agentops_control.py \
  backend/tests/test_agent_brain_deployment.py
git commit -m "docs: operate controlled agentops executor"
```

---

### Task 6: Production installation and Agent Brain release

**Files:**
- Runtime install only; no new source files.
- Evidence: `/Users/neo/.orbbec-agent-platform/brain-v2-2f61471/agent-brain-v2-evidence.txt`

**Interfaces:**
- Consumes: tested `master`, dedicated pending key, Platform owner cookies and existing acceptance configuration.
- Produces: installed permanent control boundary, successful Relay Canary, enabled Brain V2 and sanitized release evidence.

- [ ] **Step 1: Verify exact release and production baseline**

Run exact read-only checks for local `HEAD == origin/master == /opt/orbbec-agent-platform/current`, Brain flags, six service health states, action/deploy locks, migrations `1-51`, `/office/?view=services`, FAE domain, legacy FAE IP and FAE container invariance tuple.

Expected: Brain remains `0/0`; Platform Stage A, AI ADMIN and FAE are healthy.

- [ ] **Step 2: Provision and verify the dedicated cloud key**

Run:

```bash
deploy/cloud/provision-agentops-acceptance-key.sh
```

Expected exact marker: `AGENTOPS_ACCEPTANCE_KEY_STAGED_OK` and a verified dedicated-key SSH connection.

- [ ] **Step 3: Perform the single administrator-authorized installation**

Invoke the root installer once through the macOS authorization dialog. After it returns, all remaining checks use:

```bash
sudo -n -u agentops /Library/PrivilegedHelperTools/orbbec-agentops-control status
```

Expected: `AGENTOPS_CONTROL_OK commands=6`, no password prompt, installed root/agentops ownership and modes exactly match the design.

- [ ] **Step 4: Run Relay Canary without a password prompt**

```bash
sudo -n -u agentops \
  /Library/PrivilegedHelperTools/orbbec-agentops-control relay-canary
```

Expected exact marker:

```text
AGENT_EXECUTION_RELAY_OK worker=agentops-mac-primary agents=7 accepted_job_kinds=direct_agent,metabot_local public_ports_added=0 duplicate_dispatches=0
```

- [ ] **Step 5: Generate independent quality evidence and run formal release**

Run the approved HR and Marketing scenarios through the temporarily enabled Brain, inspect the final answers independently, write a `0600` `quality-review.json` only if both are materially correct, return Brain to `0/0`, then run:

```bash
deploy/cloud/accept.sh \
  /Users/neo/.orbbec-agent-platform/brain-v2-2f61471/acceptance-config.json \
  release
```

Expected exact marker: `AGENT_BRAIN_V2_ACCEPTANCE_OK`.

- [ ] **Step 6: Verify production and remove obsolete temporary credentials**

Verify Brain flags `1/1`, root workspace 200 for the owner, `/office/?view=services` 200, FAE invariance, all services healthy, no locks and no stuck Loop/Task. Assert the installed `agentops` key fingerprint is not Neo's key fingerprint, then remove any old Neo-key copy and local staging material. Do not remove the permanent controlled executor or dedicated `agentops` key.

- [ ] **Step 7: Push only master**

```bash
git status --short
git push origin master
```

Expected: only the preserved user-owned untracked files remain; no feature branch is pushed.

