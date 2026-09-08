# HR v6 contract release

Authority: `backend/app/execution_relay/contracts_v6.py`. Generate with
`python scripts/publish_hr_v6_contracts.py --metabot /path/to/metabot-checkout`.
`manifest.json` pins the SHA-256 of each wire asset. MetaBot consumes these exact
bytes; schema changes must regenerate the release, never edit a runtime copy.

The command freezes prompt, scope, rolePackage and methodSelection in contextHash.
commandHash additionally covers lifecycle identity, declared toolCapabilities and
attachment/output authority metadata. Lease epoch, callback location and renewable
bearer credentials are excluded from that business hash, as in the retained
execution transport. They must still be authenticated and checked against the
current persisted attempt/lease on every request. No bearer token belongs in the
prompt or public events.

The three tool requests and typed result payloads are the root union in
business-tools.schema.json. Its definitions also contain HrReadContextReply,
HrSubmitResultReply, HrConfirmStandardReply and HrToolFailure. Read contentText is
source content (including serialized official facts), not an instruction channel.
A model-provided readId, resultId or confirmationMessageId alone never grants access:
the business handler verifies ownership, turn membership, immutable content and
actual authenticated human confirmation.

Terminal callbacks contain resultRefs, not arbitrary business payloads. Results
are persisted before references are acknowledged; repeated operation IDs return
the original receipt or reject a differing payload. Only durable business services
can determine whether a reference exists and belongs to the current turn.

Existing Python lifecycle and grant value types are reused for validation. This is
not a v5 compatibility consumer: a v5 wire envelope is rejected by the v6 parser.
Other bots' contracts are unchanged.
