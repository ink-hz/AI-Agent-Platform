# Attachment writer boto compatibility fix

Scope: local implementation, deterministic HTTP tests, and documentation only. No production or real object-store write was performed by this change.

## Confirmed failure

Independent diagnostic receipt `../s3-fence-real-local-diagnostic-1/receipt.json` captured the unsanitized synthetic traceback at the adapter boundary. Botocore 1.42.97 selected its request header checksum path and called `fileobj.tell()` before sending HTTP. `_DigestingReader` implemented only `read`, so the first legitimate `AttachmentObjectWriter.put_stream` failed with `AttributeError` before a request reached MinIO. The preceding real 1001-version purge/final-fence check had passed, but that run did not reach its duplicate, two-eraser, or partial-body cases.

The same old control flow had a separate Suspended-bucket hazard: input-size validation occurred only after `put_object` returned. If that response said `VersionId='null'`, a concurrent eraser could replace the mutable null payload with a null fence before cleanup; deleting `VersionId='null'` could then remove the fence.

## Change

`AttachmentObjectWriter.put_stream` now copies the caller stream in chunks of at most 1 MiB into `tempfile.SpooledTemporaryFile(max_size=1 MiB)`. During that bounded copy it computes SHA-256, requires the exact declared byte count, reads one extra byte to reject oversize input, and seeks to zero. Only an exactly validated stream is passed to boto with `ContentLength` and `IfNoneMatch='*'`.

The temporary file spills beyond 1 MiB, remains seekable for botocore checksum calculation and retries, and is closed on every path. Digest calculation happens once during staging, so boto pre-reads or retransmissions cannot double-count it. Local short/long/type/read failures happen before any remote PUT.

The obsolete post-PUT `_best_effort_delete` path was removed. A client exception or malformed non-dict success response is sanitized and leaves the database write-attempt reference for the existing orphan/fence erasure flow; it cannot delete an existing canonical payload or fence. A successful response has no later local receipt calculation that can fail. This also eliminates treating `VersionId='null'` as an immutable version identity.

Both root HR documents now state the seekable bounded staging order and the `null` rule. They preserve the role/provenance additions from commit `7a7d7d8be945eb8b518108cf972167fba3a8447f`.

## RED/GREEN evidence

`red/run_old_source.py` loads the exact object-writer source from fence commit `a608970bb2d1283e16d3f787101fe3555d674fda` without replacing the worktree file. Against the new tests it records **4 failed**:

- real botocore checksum serialization rejects the non-seekable wrapper;
- oversize input reaches the fake S3 client before local validation;
- the Suspended `null` case reaches PUT/delete cleanup;
- the versioned mismatch case reaches PUT/delete cleanup.

Final receipts under `green/` record:

- focused real-botocore HTTP/checksum/retry, 50 MB spill/limit, 412/transport, size, and null-fence boundaries: **7 passed in 0.77s**;
- every `backend/tests/test_attachment*.py`: **318 passed**, 5 existing Starlette `TestClient(timeout=...)` deprecation warnings, in 16.97s;
- Ruff on the writer and changed tests: **passed**;
- source-only `git diff --check`: **passed**.

The botocore test uses a disposable loopback HTTP server, static fictional credentials, a 1.4 MB non-seekable generated input, the real boto3 client/signing/checksum stack, and a first 500 response followed by success. It proves both transmitted bodies are complete and equal, both carry `If-None-Match: *`, a checksum header is present, and the returned SHA is exact. It does not substitute for the independently supervised real MinIO fence suite, which must be rerun against the resulting commit and recorded separately.

Correction: an earlier coordination message expanded the short SHA `a608970b` incorrectly. The actual frozen fence commit is `a608970bb2d1283e16d3f787101fe3555d674fda`; no committed prior report contained the incorrect expansion.
