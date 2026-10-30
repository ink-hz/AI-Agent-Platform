# Safe host-loopback runtime observer

This is a deployment procedure for the parent release operator. Task 3 only ran
local HTTP/file/process tests; it did not install units or access production.
The binding contract is the [runtime dashboard design](../../../docs/superpowers/specs/2026-10-30-complete-runtime-dashboard-design.md).

The standalone Python standard-library collector GETs exactly HR
`http://127.0.0.1:8012/health/ready` and administration
`http://127.0.0.1:8011/health`. These identities/paths are fixed in source, and the
CLI accepts only an output path. It connects directly to numeric IPv4 loopback,
uses no environment proxies, never follows redirects and never retries. Each
request has a two-second absolute deadline across connect, headers and body;
response bodies are capped at 16384 bytes and total received bytes at 32768.
The 10-second unit limit also bounds an entire failed process. No HTTP listener,
SSH access, Docker socket, business database, model calls or credentials are used.

Every successful collection cycle atomically replaces the complete schema-1
snapshot. Each of the two records has its own completion timestamp, state,
stable error code and optional version. A failed probe writes its current
failure; it cannot reuse an earlier healthy state. HTTP errors, redirects,
timeouts, invalid JSON and size limits are explicit. Only HR `ready: true` with
a lower-case 40-hex `release_sha` supplies version evidence. A missing or invalid
SHA leaves a healthy service's version unknown. Administration reports healthy
only for `status: ok`. Unknown fields, identities and response/error bodies are
never copied to the snapshot or journal.

Probe failure is a successful *observation* cycle (exit 0), not a claim of
business availability. Successful publication logs only
`runtime_observations_written`. Publication failure exits 1 and logs only
`runtime_observations_write_failed`; the previous complete file remains until a
later explicit timer cycle. A killed process never implicitly retries. The
consumer makes any old snapshot unknown after 90 seconds. Invalid CLI arguments
exit 2 with a fixed error code, without echoing their contents.

## Parent-operated installation

Before applying, verify the actual host Python (`/usr/bin/python3`, expected
Linux Python 3.12), systemd version, current units, running HR/admin listeners
and exact release commit. Preserve their existing units and configuration.
These commands are a reviewable installation recipe, not a record of execution:

```sh
sudo install -d -o root -g root -m 0755 /usr/local/libexec/orbbec-platform
sudo install -o root -g root -m 0644 deploy/cloud/collect-runtime-observations.py /usr/local/libexec/orbbec-platform/collect-runtime-observations.py
sudo install -o root -g root -m 0644 deploy/cloud/systemd/orbbec-platform-runtime-observer.service /etc/systemd/system/orbbec-platform-runtime-observer.service
sudo install -o root -g root -m 0644 deploy/cloud/systemd/orbbec-platform-runtime-observer.timer /etc/systemd/system/orbbec-platform-runtime-observer.timer
sudo systemd-analyze verify /etc/systemd/system/orbbec-platform-runtime-observer.service /etc/systemd/system/orbbec-platform-runtime-observer.timer
sudo systemctl daemon-reload
sudo systemctl start orbbec-platform-runtime-observer.service
```

Check every ancestor of the fixed libexec path is root-owned and traversable;
never point `ExecStart` at a root-0700 private candidate directory. The script
needs read permission, not an executable bit, because `/usr/bin/python3` runs it.
Do not fix a DynamicUser permission problem by using root or assigning a fixed
UID. `StateDirectory=orbbec-platform-runtime` owns the output directory with
0755 mode; the collector rejects a foreign-owned or group/world-writable
destination directory, symlinks/FIFOs/directories at `snapshot.json`, and uses
an exclusively created same-directory temp, 0644, file fsync, atomic replace and
directory fsync. A temporary-name collision fails without deleting that file.
Directory-descriptor release is independent of temporary-file cleanup success.
After an uncatchable kill, an orphaned temp may remain; only an operator may
inspect and remove a proven orphan while the unit is stopped. The next cycle
does not sweep other files. Never blanket-delete this directory.

`DynamicUser=yes` may expose StateDirectory as a symlink through
`/var/lib/private`. Keep the systemd-controlled private parent permissions;
do not chmod that parent or grant the API write access. The service intentionally
has access to the host loopback (do not add `PrivateNetwork=yes`); address-family
and cgroup IP restrictions restrict it to IPv4 loopback. Verify these restrictions
and other hardening on the actual Linux host; local static checks do not prove
systemd or cgroup behavior.

## Publication, consumption and release checks

Inspect the complete generated JSON and ownership/modes without reading full
business health bodies into logs. Confirm both IDs, per-record timestamps and
actual states, and confirm HR's version matches the safe ready endpoint's
reported SHA. Check the journal for fixed codes and service result. Only then
enable the schedule:

```sh
sudo systemctl enable --now orbbec-platform-runtime-observer.timer
sudo systemctl list-timers orbbec-platform-runtime-observer.timer
sudo systemctl status orbbec-platform-runtime-observer.service orbbec-platform-runtime-observer.timer
```

The API container receives one approved **directory** bind:
`/var/lib/orbbec-platform-runtime` -> `/run/platform-runtime`, read-only. A file
bind pins the old inode and breaks atomic replacement. Have the privileged
container manager resolve the StateDirectory symlink when creating the bind;
the API UID 10001 needs read/traverse access only within the mounted directory,
not access to the host private parent. Verify the mounted directory sees a later
replacement inode and that API UID 10001 reads schema-1 snapshots but cannot
create, modify or remove files. Do not weaken private-parent permissions or
change the dynamic UID to make a bind work.

Task 5 owns the container change: preserve the entire original ordered input
chain and full HostConfig; whitelist only this exact additional readonly bind
and the separately approved exact FAE health environment setting when absent.
Record full original/new hashes and compare all other mounts/environment/user/
capabilities/network/services unchanged. Do not relax general release parity.
The actual `HostObservationReader` reads `/run/platform-runtime/snapshot.json`,
rejects unsafe files and invalid schema, and expires individual rows after 90s;
missing, future or corrupt evidence becomes unknown rather than healthy/offline.

Parent production acceptance must cover safe host requests, repeated timer
cycles, DynamicUser read/write/permissions, cgroup loopback restrictions, API
readonly access across atomic replacement, stale/missing failure, restored
service behavior and the management overview HTTP DTO. Keep production checks
separate from local fixtures. No production checks or browser acceptance were
performed by the Task 3 implementer.

## Rollback

Stop/disable only `orbbec-platform-runtime-observer.timer` and stop its service.
Restore the original complete API container inputs and HostConfig from the
release record, removing only the new bind (and that release's added FAE setting
if applicable). Keep HR/admin services unchanged. Preserve the snapshot and
owned unit/script files as evidence until the operator confirms rollback and
no consumer needs them; remove only those owned files if cleanup is authorized.
The reader reports unknown when evidence is absent or expires.

## Local verification

Run from the repository root with the fixed local interpreter:

```sh
/Users/neo/Developer/work/AI-Agent-Platform/backend/.venv/bin/python -m pytest backend/tests/test_runtime_host_collector.py -q
```

The fixture supplies trusted temporary loopback ports without exposing URL
options in the deployed CLI. Tests exercise actual HTTP, actual file publication,
actual `HostObservationReader`/DTO, and a real killed subprocess followed by an
explicit successful subprocess. Unit assertions are static contract checks;
Linux/Python-3.12/systemd/production behavior remains a parent acceptance step.
