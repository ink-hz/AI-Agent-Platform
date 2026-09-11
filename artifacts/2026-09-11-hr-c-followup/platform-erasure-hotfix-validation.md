# Platform attachment erasure hotfix validation

- Source worktree baseline: `47f577f160ba0dd613a3865c6bbb56a1a37119b7`
- Independent apply target: local `master` at `2bddc77f49bb823bfc4d0271605990eb8bebad46`
- Patch: `docs/runbooks/2026-09-11-platform-erasure-hotfix.patch`
- Production actions: none. No service stop, migration, deployment, object access, or mutation was performed.

## Source-worktree regression

```text
$ backend/.venv/bin/python -m pytest -q backend/tests/test_attachment_erasure_hotfix_database.py
....                                                                     [100%]
4 passed in 1.43s
```

## Independent master apply and regression

The validation used a fresh `/tmp/platform-erasure-hotfix.XXXXXX/repo` local clone, checked out `master`, then ran:

```text
$ git apply --check /absolute/path/docs/runbooks/2026-09-11-platform-erasure-hotfix.patch
$ git apply /absolute/path/docs/runbooks/2026-09-11-platform-erasure-hotfix.patch
$ python -m pytest -q backend/tests/test_attachment_erasure_hotfix_database.py
....                                                                     [100%]
4 passed in 1.50s
```

After apply, the isolated checkout contained exactly the expected hotfix paths:

```text
 M backend/app/attachments/erasure.py
?? backend/control_migrations/100_attachment_erasure_worker_access.sql
?? backend/tests/test_attachment_erasure_hotfix_database.py
```

The regression starts disposable real PostgreSQL production and preview databases through the existing platform migration fixture. It proves the legacy single-row null attachment result, legacy multi-job splicing/two claims, the fixed repository's one-job claim plus original/canonical/stale-attempt/derivative object deletion and terminal database state, and migration 100's exact six-column maintenance access. It imports no `app.hr_agent` module. The object boundary uses the production `AttachmentObjectWriter.delete` implementation with a deterministic injected S3 client; it does not claim a live MinIO or production object-store test.
