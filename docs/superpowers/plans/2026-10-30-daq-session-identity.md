# DAQ questioner identity implementation plan

> **For agentic workers:** Use executing-plans to implement the authorized tasks in this session. Review the final code independently before publication.

**Goal:** Authorized Platform lists and details identify the verified submitter of DAQ questions.

**Architecture:** Reuse direct cloud sync and encrypted verified_subject references. Resolve each Agent through its own database authorization; preserve source records and opaque business keys.

**Tech Stack:** Python/FastAPI/psycopg, PostgreSQL security-definer functions, React/TypeScript/Vitest, existing scoped image release.

## Global constraints

- No camera repository or shared DAQ src changes; no answering-model, prompt, budget or knowledge changes.
- No model calls in production or test questions written into production.
- No attribution from nickname, client metadata or missing ownership. Preserve session IDs, questions, answers, feedback and reviews.
- Preserve existing sync cadence, encryption, producer isolation, active deployment overlays and rollback targets.

### Task 1: Producer and production audit

- [x] Inspect verified PlatformSubject→DAQ persistence and normal-DSN owner aggregates.
- [x] Freeze actual image IDs and distinguish inherited labels from deployed source.
- [x] Baseline platform source/identity tests: 145 passed.
- [ ] Add real DAQ login/persistence/account-switch regression and preserve evidence.

### Task 2: Trusted projection and encrypted transport

Files: cloud_sync/projection.py, sync_remote/importer.py, cloud_replica/models.py and exporter.py; tests/test_daq_session_identity.py.

- [ ] RED: DAQ verified owner is absent; malformed/forged ownership never gains a reference.
- [ ] Add enterprise-only DAQ owner projection and allowlisted wire scope; leave FAE owner policy intact.
- [ ] Preserve DAQ user/session/turn keys with identity opt-in; clear imported metadata claims.
- [ ] GREEN: run new tests plus affected source, importer and replica suites.

### Task 3: Per-Agent authorization and consumers

Files: control_migrations/127_agent_session_identity.sql, observability/identity.py, frontend senderIdentity and session components.

- [ ] RED: DAQ list/detail lacks identity and per-Agent DB function is absent.
- [ ] Add read_agent_session_subjects_v127(session UUID, agent TEXT, subject UUID[]) with exact scope checks and read-only profile projection.
- [ ] Resolve per Agent; clear unknown/spoofed names; render trusted DAQ identity in list/replay/each question.
- [ ] GREEN: real DB permission matrix, cookie HTTP list/detail and frontend static component assertions; reuse camera suite.

### Task 4: Review, publish and observable history

- [ ] Independent review exact diff; fix any material identity/isolation finding.
- [ ] Merge clean code to master, push exact release ref, build frontend once.
- [ ] Capture fresh code/static/Compose/source data baselines and backups; stage reviewed increment from live immutable image.
- [ ] Apply 127 additively, activate API and existing cloud-sync worker, preserve all non-targets.
- [ ] Normal sync backfill; compare existing keys/body/feedback/review and verify one actual DAQ session through authorized profile resolution and API data path.
- [ ] Record exact versions, test results, unknown-history counts, rollback and browser/production-model exclusions.
