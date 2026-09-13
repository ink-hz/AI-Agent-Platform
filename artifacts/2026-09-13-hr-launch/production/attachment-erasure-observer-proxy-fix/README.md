# Observer environment-proxy correction

Root review found the observer created httpx.Client with its default trust_env=True. The observer now explicitly uses trust_env=False so ambient HTTP(S)_PROXY settings cannot route the real owner Cookie/CSRF through an environment-configured proxy. Existing fixed-origin validation and disabled redirects remain in place.

The CLI engineering test wraps the real HTTPX constructor and asserts the exact trust_env=False argument. RED captures [None] versus [False] before the one-line implementation change; GREEN runs all 11 observer tests, passing with one existing TestClient deprecation warning. This assertion checks actual client construction, not an AST/string match; it does not claim a separate real-proxy network penetration test. The same test retains its real POSIX deadline/unknown-receipt assertions.

Exact red/green source and test snapshots, command exit status, SHA256 and logs are saved in separate directories. Green source/test bytes remained unchanged during the run. Ruff passed. All earlier 9f31cc2 preparation evidence is preserved unchanged. No production, browser or model execution occurred.
