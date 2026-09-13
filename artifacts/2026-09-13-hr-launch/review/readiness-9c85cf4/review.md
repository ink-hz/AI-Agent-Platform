# Independent readiness review

Object: `9c85cf416edd7b83446c52071175f3cefba72468`, source paths fixed in `source-fingerprints.json`; reviewer root did not implement these paths. This is a code and saved-evidence review, not a production verdict.

No Critical or Important issue found in the scoped change. Owner-only route classification, current-role verification and fail-closed audit precede the probe. Responses use no-store and fixed failure codes. The shared liveness endpoint retains its existing purpose.

The API probe preserves loaded settings and initial file/knowledge identities and compares their current values without adopting drift as a new configuration. Database checking runs read-only with bounded statements and the actual role. The Dockerfile supplies the release SHA; it is not missing merely because Compose does not repeat it. A legacy/draining phase can be dependency-ready while new admission remains closed.

Worker health connects to the existing process through a private socket and validates PID/start/nonce against its 0600 receipt. Serving a health request never refreshes progress. Only a successful poll or validated work lease renewal updates progress; the tests exercise stale/dead/mismatched instance, changed configuration/schema, and a real Worker whose local replacement SSE call outlasts its lease interval. The heartbeat-removal mutation fails as expected. This is stronger than starting a new process that checks environment files.

Saved verification: readiness README links the RED/GREEN runs, 23-test combined run and 3-test recheck. The persisted-session HTTP case substitutes only the external identity exchange while real session issuance, current directory role, HR grant and database audit run locally. Other HTTP cases explicitly use test identity fixtures. No test count is a production or real-provider quality claim.

Limits retained: long candidate/parser processing without a validated work heartbeat can make health conservatively stale; this change does not add a false progress ticker. Compose reports health but does not itself implement the entire deployment gate. Production must compare API/Worker identities, explicitly reject an unhealthy deployment, and run authenticated functionality. Root's later optional policy fields and 105 floor are a separate reviewed commit, not retroactively part of this object.
