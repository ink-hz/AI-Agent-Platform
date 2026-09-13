# H03 real run 3 — professional and execution review

Verdict: **not accepted as a completed four-turn scenario**. Turns 1–3 persist progressively revised JD/JR content; turn 4 does not deliver the requested updated result and leaves an unrelated troubleshooting placeholder. Earlier content also contains unsupported recommendations and one factual inconsistency. This is neither a model-quality pass nor a production acceptance.

## Independence and exact scope

This reviewer authored the synthetic replay-v2 corpus. This review independently examines the runner/model outputs, not independently certifies its own corpus. Full saved bodies, final messages, attempted turn-4 body, read-resource evidence, result references and tool outcomes were examined; old assistant responses are not gold. Original RED runs/reviews remain unchanged. No model, production, browser or test rerun was performed for this review.

`fingerprints.json` binds 15 exact input/output/source snapshots under `frozen/`; `turn-audit.json` extracts requests, material bindings, states, result refs and body hashes. Run report identifies starting HEAD `8c9afd0a7ce61336812dd42014a64b0d18ccb9bf`, runner helper SHA `ebc1562fe491b5e1e0064c03e101927e7c19ce5a27b50e07f594587c5a992b38`, role/knowledge release `hr-07ae50d49fbca7c8e78cdd2b`, role manifest SHA `4a30fa3d2536996d0f3762ec0d69fada0da193a451f582bfd8f4473345f48afe`. It reports unchanged tracked runtime source across the case; this does not claim all repository files stayed unchanged. The frozen repository.py SHA matches both run source records.

This is a four-turn synthetic adaptation of historical intent, not verbatim personal history and not replay of all 242 historical messages. The actual run uses one persistent work with input revisions 1–4 and previous-result revision bindings. Its external login exchange and object storage are substituted, with real local HTTP/PG persistence and configured model calls. Security observations are anonymous 401, CSRF 403, origin 403, wrong-owner 404. Model identity assurance is provider self-report only. The test uses a 1.2M-token budget, not a production-budget acceptance.

## Input and reference boundaries

Turn 1 provides only a synthetic expert-manager engineering director role, NPI/fixtures/engineering software, approximately 30 people and reference profile R with fixture design/team-retrospective clues; other ability is unknown. Turn 2 asks for team delivery plus personal technical contribution; turn 3 asks for concision; turn 4 adds six product categories to manufacturability responsibilities. No supplied material establishes market scarcity, a required subordinate management layer, numeric past team-size thresholds or a first-priority reorganization mandate.

Read receipts include the exact first material, methods job-and-context/competency-and-profile/requirement-calibration, preceding result revisions and the final product-range material. Turn 4 reads the 64-character product material and v3 result. Those valid refs establish availability and lineage, not the truth of added market or organizational propositions. The saved JD keeps one identity `02eeb800-f78c-4143-b726-a8ff6529a7a8` through three revisions, with exact refs in turn-audit.json.

## Professional assessment by turn

1. The combined JD/JR is a legitimate single `jd` result. It covers management outcomes, NPI, fixtures, software and personal technical judgment, preserves substitute capability paths, and treats R's missing evidence as unknown. However, the rationale says engineering software is the scarcest of the three supply areas, while another section/final explanation says there is no market evidence and no scarcity judgment. This is an actual evidence-boundary contradiction, not a keyword issue. A required “10人以上” past team-size anchor has no supplied basis; the general closing qualification that strengths are suggestions mitigates but does not clearly qualify that individual hard threshold. Exempting production-level coding is also a proposed calibration, not a user-confirmed requirement.

2. The revision substantially improves the requested manager/technical-expert balance using personal action, delegation and outcome responsibility. It removes the scarcity assertion and explicitly labels added numerical/time-allocation anchors as suggestions requiring confirmation. Alternatives and R's unknowns remain. But approximately 30 people across three areas does not establish that a subordinate management layer exists or is necessary. “If none exists, building it is the first task” imposes an unsupported organizational priority. The self-description of v1 as lacking both management and technical weight overstates the prior omission: v1 already contained those elements, although less prominently. These are calibration quality issues; numerical suggestions marked pending are not established company facts.

3. The saved revision reduces the body from 4,591 to 3,437 characters (about 25.1%) while retaining the two axes, substitute paths, priority/trainable distinctions, reference-profile limit and eight pending confirmations. Moving software alternatives into their own requirement removes the prior parent/child ambiguity. It fulfills the concision request materially. It retains the unsupported “build the next management layer first” priority; pending confirmation reduces but does not eliminate that overreach.

4. The attempted full draft incorporates all six products and labels product-specific DFM details as assumptions/suggestions rather than requiring experience in all six. That is a reasonable direction, but it is **not saved/delivered**. Its renewed question whether these six products belong within the role's scope asks again about a scope the user just instructed; process maturity, volume and safety details remain legitimately unknown. The old organizational priority remains. None of this attempted content earns a completed-turn quality pass.

## Terminal execution failure and artifact contamination

The driver ends exit 1, status failed, executed_turns 4, generation_calls 20. Turn 4 reports `ValueError: replayed work did not complete`; actual persisted state is `waiting_budget`, phase `finalizing`, answer_state `ended`, and there is no final assistant response. The existing saved JD remains v3.

The sequence explains the concrete failure:

- Attempt 13 reads the new material and preceding JD.
- Attempt 14 tries to update the existing `jd` identity using `kind: role_calibration`; it also supplies an `official_original` basis with a null ref despite synthetic-only material. Validation rejects the null basis ref. This is a structured source-classification error, even though the draft prose calls the source synthetic.
- Attempts 15–17 continue the incompatible kind update and receive invalid_input; shortening the body does not repair the identity/type mismatch.
- Attempt 18 creates a new role_calibration titled “占位测试” with a channel-testing body. It is persisted but is not a user-requested deliverable.
- Attempt 19 lists results; attempt 20 again tries the old JD identity with role_calibration and fails.

Frozen `backend/app/hr_agent/repository.py:1689` explicitly rejects changing an existing result's kind. Its SHA `87febf7d6947813a27aeb6a4fe571e3ea85cbeb389a3af36b1c542040654a488` matches the run metadata. Thus the later generic invalid_input outcomes are consistent with an actual immutable-kind constraint, not the superseded harness requirement to create two result kinds. The platform's rejection is legitimate; generic feedback and repeated model retries warrant a separate repair investigation, not weakening acceptance to ignore the incomplete work.

At the stop, charged tokens are 1,161,589 (usage_quality estimated), charged calls 20, active seconds 1,013.497892; limits are 1,200,000 / 32 / 1,800, with token reserve 32,768. This proves budget waiting, not that the call/time maxima were exhausted. This review does not infer the exact admission reservation calculation solely from these counters. The retry loop consumes budget and introduces an extraneous persisted artifact. Increasing budget alone would not demonstrate repair of the wrong-kind loop.

## Acceptance consequence

Preserve this RED. Do not count an attempted full draft or placeholder as completion. A future rerun must finish the product-range revision against the existing JD type and revision, avoid fabricated source classification and troubleshooting artifacts, and retain the prior corrections without unsupported scarcity or categorical organization mandates. H03 remains professionally and operationally unresolved; the prior dual-kind test issue was real but no longer explains this failure.
