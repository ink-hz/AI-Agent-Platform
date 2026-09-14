# HR activation SQL independent review

No important issue found in the captured final SQL. This review did not execute SQL or rerun existing function tests.

Initialize, draining_legacy and cloud invoke only the existing 102/105 restricted functions, with separate fixed UUIDs recorded in operations.json. Hashes match all four SQL files. Existing functions provide exclusive advisory locking, request UUID replay, phase validation, and occupancy checking before cloud. The diagnostic counts transaction explicitly takes the same exclusive key 79518187931988 and now runs counts once; the earlier two-call EXPLAIN+SELECT form was corrected before this snapshot.

Each transaction applies SET LOCAL ROLE platform_control_maintenance, 2s lock_timeout and 3s statement_timeout. This is the existing authenticated platform_owner operational connection narrowed locally for the transaction, not a maintenance-role login or a fabricated end-user session. There is no GRANT or direct gate/record mutation in these SQL files. SECURITY DEFINER function authority remains the pre-existing migration contract.

Execution contract confirmed by root: psql -X -qAt -v ON_ERROR_STOP=1, successful command exit plus gate/operation read-back before treating commit as complete. A SELECT receipt printed before COMMIT is not alone proof of commit. Ambiguous results retain the original request UUID for durable operation lookup. The psql timing is measured timing, not a transaction-wide 3-second guarantee. Migration/readiness/real occupancy precede the respective activation stages; these files neither prove nor bypass those prerequisites.

Production execution, actual empty-lane evidence, and authenticated owner acceptance are separate root-owned receipts. No new approval is introduced by this review.
