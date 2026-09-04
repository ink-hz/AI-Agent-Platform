# HR R1.2 Historical Resource Import Report

Date: 2026-09-04
Branch: `feat/hr-r12-resource-import`
Base: `afd81cd`

## Result

The existing HR position import now discovers historical resources for the exact
owner and the HR conversations already selected by position discovery. It combines
persisted `position_conversations` bindings with newly discovered exact official-job
bindings. Matching bindings are deduplicated; conflicting position ownership remains
ambiguous and is never applied automatically.

Dry-run reads and reports without invoking any mutation. Apply mode uses the existing
position material promoter and artifact linker with deterministic per-resource request
IDs. It neither copies attachment bytes nor synthesizes missing resources. User-input
attachments and artifact current versions must retain ready bytes, immutable locators,
and no erasure request before they enter discovery.

The summary now includes `exact_materials`, `exact_artifacts`,
`ambiguous_attachments`, `ambiguous_artifacts`, `applied`, and `noop`.

## Boundaries

- Resource reads are restricted to the supplied owner and the exact set of HR
  conversation IDs.
- The adapter reads only resource identity/readiness fields and position bindings.
- Apply delegates to `promote_material` and `link_artifact`; it issues no broad DML.
- Cross-owner adapter output is rejected before discovery or mutation.
- No production apply was run, and the preserved backend virtual environment was not
  created, changed, or removed.

## TDD evidence

Initial CLI wiring RED:

```text
python -m pytest -q tests/test_hr_position_import_cli.py
4 failed, 1 passed
TypeError: execute_import() got an unexpected keyword argument 'resource_repository'
```

Adapter RED:

```text
python -m pytest -q tests/test_hr_position_import_cli.py
collection error: PsycopgHistoricalResourceRepository was absent
```

Focused GREEN:

```text
python -m pytest -q tests/test_hr_position_import_cli.py
8 passed

python -m pytest -q tests/test_hr_resource_backfill.py \
  tests/test_hr_position_import_cli.py tests/test_hr_position_importers.py
22 passed
```

HR regression before final commit:

```text
python -m pytest -q tests/test_hr_*.py
95 passed, 10 pre-existing Starlette cookie warnings
```

Final static verification is recorded with the commit handoff.
