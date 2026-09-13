# stage_build_v3 read-only review

Result: **PASS — no findings in the bounded v3 delta.**

Reviewed `production/stage_build_v3.py` and `test_stage_build_v3.py` against the previously reviewed v2 files. After replacing only the declared fixed inputs and names, both files are byte-identical to v2:

- release: `ce6c0f343c5aa8c9725cc599cfa80ee6e3eaacf7` → `394c9ecd96a292db4cd6c4a0ea0066a3acc3cd76`
- tree: `a7489e2a43d09ee8727748488abdcbf64f45b9e9` → `fee0cd931c7e66cb1e681c16c3c7345821939a67`
- local run directory: `stage-build-v2-runs` → `stage-build-v3-runs`
- embedded remote path allowlists and CLI/test identifiers use the same v3 release/name substitution.

No remote operation changed. The script still stages a fixed `git archive`, validates its manifest and tree, acquires the existing locks, runs only `docker build` plus image inspection, and installs the new release directory. It does not run compose, start/stop/restart a service, invoke a migration, switch `current`, or mutate a database. `services_changed` remains hard-coded `False` in its receipt.

The fixed commit exists and resolves exactly to the declared tree. Its tracked objects include the final attachment writer, worker adapter, S3 erasure helper, version-erasure tests, and the committed attachment-suite receipt showing `321 passed, 5 warnings in 16.53s`. The final S3 helper SHA-256 is `42c230ff413fd53402176dd988ff78b511a79a28489130890808d7331cc44933`, matching the independently completed real-MinIO run4 receipt. The fixed tree has zero tracked symlinks.

Script SHA-256:

- `stage_build_v3.py`: `54ca7b74f807b5a48110b89fd47f1384222a392856b89af04b9509a7ca0b7111`
- `test_stage_build_v3.py`: `58bb03111e87eee314834c1388f2ea254adb7e2a28a7eef0c49372a2cf4cf9ac`

This review performed no SSH connection, build, service action, migration, or test execution. It does not claim the concurrently running root unittest result; that result must retain its own receipt. Exact hashes and normalized comparison booleans are in `fingerprints.json`.
