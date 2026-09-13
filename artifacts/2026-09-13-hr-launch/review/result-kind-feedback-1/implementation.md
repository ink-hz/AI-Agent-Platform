# Result kind feedback repair

H03 real run 3 reached waiting_budget after repeated wrong-kind result updates and one debug placeholder write. Existing immutable-kind enforcement was correct; its generic invalid_input feedback omitted the correct field/type. The change returns field=kind and expected_kind only after owner, expected revision and same-work authorization. It preserves rejection and result identity. Role guidance requires reading the existing result and prohibits debug placeholder saves. It cannot mechanically prove model compliance or professional quality.

RED 1 failed at fixture creation because JD requires a nonempty basis; that failed attempt is retained. RED 2 supplied the required user_temporary basis and failed at the missing error details. GREEN 1: 16 passed/1.67s; GREEN 2 after adding other-work and foreign-owner no-leak checks: 16 passed/1.69s. All raw runs are in runs/result-kind-feedback-*. Real local PostgreSQL, model/provider and scope boundary substitutes; no HTTP/production/real-model acceptance is claimed. Lint comparison preserves the existing repository I001 import finding and shows no new findings; no unrelated import rewrite.

H03 historical output and RED receipts remain unchanged. New role bytes require a new knowledge package; installed production fc339 knowledge is not described as updated.
