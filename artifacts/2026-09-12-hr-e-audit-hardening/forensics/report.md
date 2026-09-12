# Snapshot forensics

Observed at HEAD `5978eaa0bf657721d1ef7ab64d5b4a7ada5ca43a`. This directory is new; no older artifact was modified.

## Recovered material

The two pre-cosmetic test snapshots were absent from reachable and unreachable Git blobs, so the files under `reconstructed-pre-cosmetic/` are explicitly **deterministic reconstructions, not originally saved copies**. Inverting the recorded lint-only edits produced the exact `before_sha256` values in `final/cosmetic-delta.json`. For attachment binding, the inverse restores the module-level Ruff declaration and the two line-level `F401, F811` selectors. For material transport, it restores 12-space indentation on historical lines 91–92. Both match their recorded hashes byte for byte.

The old closure body under `recovered-old-closure/` is likewise a **deterministic reconstruction, not an originally saved copy**. Removing the later-added blank line and final bounded-review paragraph (current lines 69–70) from the current closure yields exactly `00c73705918af67f3f8e4f13a2127e0eb918ed8967bd44debedb9bb53bdac632`, the fingerprint recorded by the independent closure review. The reconstructed snapshot still ends with the original current-judgment paragraph.

Both files under `observed-tmp/` are **observed byte-for-byte copies** of surviving `/tmp` patch files. Their copied hashes exactly equal the hashes recorded by their respective independent reviewers.

## `aeff5307…` correction

`artifacts/2026-09-11-hr-e-review/final/runbook-independent-review.md` records `aeff5307660b06c1446ff8e91234e23d6f9040d36cbd614c104ac9381ae400c7` as the closure-record snapshot it reviewed. The referenced bytes are **unavailable**: the hash matches none of the four committed versions of `docs/reviews/2026-09-11-hr-e-review-followup.md`, none of the surviving `/tmp/d1mut`, `/tmp/d1rev`, or `/tmp/d1probe` copies, and no searched Git object. The current document is `a3af799891dbdee171d6d4293c8ea057fe9de23bd75b15d727592e9c6f11f5c5`.

Therefore the old independent review remains evidence about the unavailable `aeff5307…` snapshot identity only. It cannot be cited as byte-level review of the current closure document. The old artifact is intentionally unchanged.

## Verification commands

```text
shasum -a 256 artifacts/2026-09-12-hr-e-audit-hardening/forensics/reconstructed-pre-cosmetic/* artifacts/2026-09-12-hr-e-audit-hardening/forensics/recovered-old-closure/* artifacts/2026-09-12-hr-e-audit-hardening/forensics/observed-tmp/*
for rev in $(git log --format=%H -- docs/reviews/2026-09-11-hr-e-review-followup.md); do git show "$rev:docs/reviews/2026-09-11-hr-e-review-followup.md" | shasum -a 256; done
find /tmp/d1mut /tmp/d1rev /tmp/d1probe -type f -path '*/docs/reviews/2026-09-11-hr-e-review-followup.md' -exec shasum -a 256 {} \;
```

`manifest.json` records every saved item’s provenance category, expected source, observed hash, and verification result.
