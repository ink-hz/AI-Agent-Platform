# Independent 107 lease design review

The proposed token+unexpired-lease CAS, bounded committed attempts, late callback rejection, and legacy v64 EXECUTE denial address the identified stuck-running and late-writer problem. No design-level blocker remains provided the implementation preserves these explicit contracts. This is a design review only, not runtime/test completion or deployment acceptance.

The old implementation was inspected: 106 claims only queued/partial and has no expiry; 64 record requires only job ID and running. Claim and reference decryption share a transaction, so decode failure previously rolled back attempt charging. The new committed claim followed by separately charged reference loading closes that retry loophole.

Review feedback to implementation: exhaust due rows through bounded/SKIP LOCKED selection, not an unbounded blocking UPDATE; revoke legacy and PUBLIC/incorrect-role grants; reject expired tokens even before reassignment; never permit final synchronous renewal to erase a previously observed lease-loss flag; stop/join heartbeat within a bounded interval; only explicit exhausted-job recovery may reset a bounded counter with durable recovery UUID semantics. References and diagnostic evidence survive expiry, failure and exhaustion, and only current unexpired completion may scrub them.

Required focused evidence is real PostgreSQL claim/reclaim/late-success/late-partial and max-attempt exhaustion, a real killed claimant process, reference-load failure accounting, and deterministic heartbeat-loss behavior. Existing S3 fence properties must remain unchanged; no redundant model/MinIO/production calls were made by this reviewer. Current old-image joint executor remains frozen and not eligible for execution after this planned floor change.
