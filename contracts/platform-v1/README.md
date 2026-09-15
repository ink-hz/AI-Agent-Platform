# Shared content format fixtures

`content-crypto-vectors.json` and its SHA256 file must be byte-identical in AI-Agent-Platform and AI-HR-Agent. Both repositories run `test_hr_content_crypto_vectors.py` in CI against their own ContentCodec/Keyring implementation. Fixtures contain synthetic keys and plaintext only.

A format change requires a new version and coordinated fixture changes in both repositories. Keep old-version vectors so existing ciphertext remains readable. The gate checks exact sealing, decryption, AAD, key versions and rejection of changed owner/key/ciphertext. No shared runtime package or new API protocol is introduced.
