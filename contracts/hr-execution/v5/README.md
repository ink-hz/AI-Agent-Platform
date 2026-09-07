# HR execution v5 contract freeze

This directory is the shared P01 wire-contract and fixture gate. It does not
enable a v5 sender, expose a production ingress route, authorize a channel
principal, persist an idempotency decision, or implement the MetaBot runtime.
Platform outbound collaboration remains v3/v4-only until later rollout gates.

The five Draft 2020-12 schemas enforce bounded structural shapes and reject
unknown fields. Python models additionally enforce cross-field and byte-level
semantics: loopback callback/run binding, stable logical session binding,
permission-scope equality, grant subject/path constraints, canonical command
hashes, event type/payload pairing, executor-stop proof consistency, and ACK
cursor continuity. UUID values participating in command or intake hashes must
use lowercase canonical `8-4-4-4-12` wire form; alternate spellings are
rejected before hashing so Python and TypeScript cannot normalize differently.
Schema acceptance alone is not an authorization decision.

`turn-intake.contentHash` is SHA-256 over UTF-8 JSON with recursively sorted
keys and no insignificant spaces. Its exact top-level projection is
`operation`, `identity`, `senderIdentity`, `chatId`, `threadKey`,
`conversationId`, `principalRef`, `text`, and the ordered `attachments` list.
Identity contains `tenantId/appId/botId/messageId`, sender contains
`openId/unionId`, and each attachment contains only
`attachmentId/index/sha256`. Unicode and whitespace are preserved without
normalization; the projection contains no floats. `requestId`, derived
`contentHash`, retry/receive timestamps, signatures, authorization values, and
rotating upload tokens are excluded. Those transport values belong to an
authenticated wrapper and are forbidden as extras in the strict wire body.
Display name, MIME type, and size stay authoritative in the platform attachment
record bound by attachment identity and SHA rather than extending this body.

Terminal result, error, cancelled, and interrupted payloads are disjoint typed
objects. `run_heartbeat` is not user-visible progress. `raw_progress` preserves
the recognized legacy vocabulary only as a bounded `visibility: private`
payload and is never public-answer authority. Frozen artifact intents contain
an opaque spool reference and never a local filesystem path.

Untrusted event callers must use `parse_v5_event`, which applies the bounded,
strict camelCase wire boundary and returns only a generic `V5ContractError` on
failure. Callers must not validate raw event models and log Pydantic diagnostics,
because rejected answers and private progress may contain confidential content.

`cases.json` is shared by Python and the native TypeScript parity verifier. The
verifier requires Node.js 23.6 or newer for built-in type stripping (verified
with Node.js 26.5.0), adds no runtime dependency, and checks UUIDv5, canonical
business hashing, transport-rotation exclusions, attachment ordering, and
replay outcomes. It is not the future MetaBot M02 TypeScript runtime parser.

Channel bridge fixtures cover exactly the six `/hr-bridge/v1` operations.
`principalRef` is a server-issued opaque reference to verify, never client
authority to select an owner. Runtime configuration fixtures contain only
credential/DSN file paths, are fixed to `hr-bot`, and model the v4-only
capability gap as `executor_capability_missing`.
