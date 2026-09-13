# Read-only late-PUT erasure fence assessment

The proposed permanent nonpersonal object fence is viable **only with complete key discovery, conditional-only writers, and protection against deletion of the fence itself**. Adding IfNoneMatch to writers plus deleting every version is insufficient. This is a design assessment, not implemented or tested behavior. Exact inspected source bytes are in source/ with fingerprints.json; the parallel version-purge change may continue independently. No production, model, credentials, or mutation was used.

## Grounded race

`object_writer.py:158` and `worker_runtime.py:391` currently use unconditional PUT. `worker.py:288` computes derivative keys deterministically from processing_job_id and derivative kind, then writes before recording the derivative. `erasure.py:83` discovers only recorded derivative rows. A crash after PUT but before record leaves an undiscovered derivative; a delayed PUT can finish after all-version listing returns empty. 064 cancellation's abandoned state does not stop that external I/O. 064 record_attachment_erasure_result_v64 destroys encrypted refs on completed; falsely declaring completion makes recovery harder. Repeating eight sweeps is a bound on retries, not a future-write fence.

## S3 contract and algorithm

AWS documents that If-None-Match=* checks the current version only: a current delete marker permits writing, while an existing ordinary object returns 412. Concurrent conditional writes elect the first completion. Delete races can return 409; multipart completion needs separate conditional handling. Therefore a delete marker is not the proposed fence. [AWS conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html).

Use a zero-byte ordinary object with fixed nonpersonal metadata as the permanent current fence. For an absent/current-delete-marker key, create it with IfNoneMatch=*. For an existing data object, the same operation would 412: obtain its current ETag and replace it with IfMatch=ETag. On CAS conflict, re-observe and retry boundedly. On unknown response, inspect the fixed marker/body and actual version; never declare success from timeout. Preserve that confirmed fence version and delete all other exact-key versions and delete markers. Verify the current object is still the fence and no nonfence versions remain. Never issue unversioned DELETE against a fenced key. This conditional-replacement design is an inference using the documented PutObject IfMatch and conflict semantics. [AWS PutObject API](https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutObject.html).

A writer that finishes first leaves data for the eraser's CAS to replace; a fence that finishes first makes late conditional writers fail. This reasoning requires **every** upload/artifact/derivative write to use IfNoneMatch=*, no late unconditional writer, and no operation/lifecycle rule later removing or hiding the fence. Keep the fixed fence indefinitely while delayed writes can exist. The acceptance observation changes from “zero versions” to “exactly the nonpersonal fence, zero personal versions”; observer and canary must explicitly distinguish these states.

## Minimal application integration obligations

1. Preserve original upload attempt keys already durably recorded before PUT. Freeze issuance of new write attempts/derive jobs before taking the erasure key set; serialize this against the actual DB transition that can create a next processing job. A plain claim of an erasure job does not establish this exclusion.
2. Also enumerate every derive processing_job for the attachment and compute its existing deterministic key, including jobs with no derivative row and terminal jobs. Reuse one exact key function in worker and eraser. Do not introduce new random per-retry keys without durable pre-registration: that enlarges the crash window.
3. A derivative retry encounters its existing immutable key. A 412 is not unconditional success: distinguish a verified matching preexisting payload from a fence or mismatched body, and verify current authorized job/attachment state before finalizing. Unknown PUT outcomes retain the key and job for reconciliation; do not clean it or invent another key and forget the first. Matching size alone/ETag-as-SHA alone is insufficient.
4. Audit writer exception cleanup and worker rollback paths. The existing version-specific success cleanup can remove a confirmed own data version; the no-VersionId fallback DELETE can destroy an unversioned fence or create a latest delete marker. Every cleanup path needs the same fence-aware contract, not just erasure.delete.
5. Only record erasure completed after all possible keys are fenced and verified, pending creation is excluded, and all older personal versions are gone. Any unknown/list/permission/CAS timeout keeps durable refs and a retryable non-completed state. A crash after fencing but before DB completion is recoverable by re-observing the fence; do not depend solely on an in-memory fence VersionId.
6. Restrict lifecycle/ordinary delete access so the permanent marker cannot be expired into a delete marker. Supporting version-enabled, suspended, and never-versioned buckets requires explicit tests; versioning configuration changes during cleanup must fail closed. If multipart ever enters this bounded path, conditional completion and incomplete multipart parts need separate handling; current inspected adapters use PutObject.

## Root-100 independent release compatibility

An old unconditional PUT process can overwrite any tombstone. Deploying only a new erasure reader against root100 does not provide the fence guarantee. Stop/exclude old attachment writers before enabling conditional-only replacements, including cleanup code and derivative workers; do not silently broaden this to HR/Feishu execution ownership.

064 grants maintenance SELECT on attachments/derivatives/task_grants/erasure_jobs; 100 adds specified uploads/upload_write_attempts columns, **not processing_jobs**. The necessary unrecorded derivative-key enumeration is therefore not available under the inspected root100 maintenance grants. A minimal appended migration granting only needed job identity/kind/attachment columns, or an equally scoped checked function, is required for the general live-worker solution. Do not mutate 064/100 bytes or substitute an app/owner credential. If the authorized release must remain root100 only, a smaller operational solution is to quiesce and keep all relevant writers stopped, prove no request can still finish, inventory outstanding keys under separately authorized read access and erase before resumption; it does not establish continuous online erasure safety. “Stopped process” alone cannot prove no already accepted remote PUT will complete.

## Necessary falsification tests before claiming this solves the race

- Real object-store delayed original PUT completes after eraser's initial empty observation: fence wins or replaces its data; no personal version remains.
- Existing data, no current object, latest delete marker, and duplicate concurrent erasers; fence acquisition unknown response followed by restart.
- Derivative PUT crashes before row insertion and retry hits the same deterministic key; eraser discovers it independently from processing_jobs.
- Cancellation competes with scan completion creating a derive job; all created keys appear in the closed set or creation is rejected.
- Cleanup after size mismatch/unknown PUT cannot delete a fence; unknown results retain recovery refs.
- Bucket permission failure, unsupported conditional implementation, exhausted bounded retries, lifecycle/config drift and old unconditional writer all fail the claim, not turn into completed.

AWS semantics are the reference contract, not evidence that the deployed S3-compatible endpoint or local MinIO version implements them identically. The specific endpoint needs controlled concurrency tests. This review intentionally proposes no source or migration edits.
