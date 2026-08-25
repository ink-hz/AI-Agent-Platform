# Professional Agent User Experience Design

Date: 2026-08-25

Status: approved direction, implementation pending

Scope: Agent Platform professional-Agent chat surfaces and their public projections

## 1. Objective

Make the professional-Agent workspace feel like a finished employee product rather
than an engineering console. A member should see the conversation, useful progress,
and a clear next action. Operational diagnostics remain available to authorized
operators in Management Center, but never leak into the member chat surface.

This design covers HR and the five Marketing Agents. It also defines the shared
public-display rules that future direct-chat Agents must follow. FAE and AI ADMIN
remain external workspaces and are not reimplemented here.

## 2. Product boundary

The Platform has two deliberately different projections of the same execution:

```text
Member workspace       Conversation, business-level progress, answer, feedback
Management Center      Events, status codes, Mission/Run/Trace/Evidence, diagnostics
```

The member workspace must not expose:

- Mission, Run, Task, Trace, Evidence, Adapter, relay, or worker identifiers;
- raw Agent IDs such as `hr-bot`;
- transport states such as `accepted`, `completed`, or `interrupted`;
- links labelled “诊断详情” or equivalent engineering actions;
- model deliberation, tool-selection notes, system prompts, or internal logs;
- raw exception text, database details, provider responses, or stack traces.

An error shown to a member may contain a stable support reference. The reference is
plain text, is not a link to protected diagnostics, and can be searched by an owner
in Management Center.

## 3. Information architecture

### 3.1 Global navigation

Keep the top-level navigation focused on products:

- Agent 大脑
- 专业 Agent
- 管理中心, only for users authorized to enter it
- the signed-in person at the right; selecting the person opens the account page

“企业账号” is not a separate primary tab. The account display name is not an
Agent label and must remain visually separated from the product navigation.

### 3.2 Agent-local navigation

Agent switching is domain-scoped:

- HR pages show only HR identity and HR conversation history.
- Marketing pages show the five Marketing choices: Prospecting, Inbound, Voice,
  Intelligence, and GTM.
- The Marketing switcher is rendered only when the current Agent belongs to the
  Marketing domain.
- FAE and AI ADMIN never appear in the Marketing switcher.

The canonical Catalog remains the source of display names, domain, persona subtitle,
and interaction modes. `persona_subtitle` is optional; a missing value omits the
subtitle instead of exposing implementation metadata. Browser code must not expose
an Agent ID as a display fallback.
If a required display label is missing, the affected surface fails closed with a
generic “专业 Agent” label and records a Catalog-governance warning for operators.

## 4. Conversation workspace

### 4.1 Header

The conversation header identifies the Agent, not the first user prompt. For HR it
has the following shape:

```text
HR Agent
Hannah · 技术人才搜寻与招聘协作
```

The user prompt appears once, inside the user message. The conversation title remains
available in the history list and browser document title, but is not repeated as a
large page heading above the same message.

### 4.2 Continuous conversation

The selected conversation remains on the same page and the composer remains at the
bottom after every completed turn. Sending a follow-up creates a new Turn inside the
same Conversation. Starting a new conversation creates a new history item and does
not replace or archive the previous one.

### 4.3 History sidebar

The sidebar is already scoped to the current Agent, so each row must not repeat the
Agent name. A row contains:

- a concise conversation title;
- relative or compact date and time;
- user-facing state only when it adds value: `处理中`, `需要补充`, or `未完成`.

Completed is the default and need not be repeated on every row. The current row has a
clear selected state. History remains newest-first and paginated.

Members may rename and archive their own conversations. A renamed title is trimmed
and contains 1–160 characters. Archive is recoverable and does not delete underlying
audit or retention data. Rename, archive, and restore all enforce Conversation
ownership and bound Agent identity on the backend. Hard deletion is outside this
change.

## 5. Public answer contract

### 5.1 Final-answer-only rule

Professional Agents must return only user-facing answer content. The following
example is a contract violation and must never appear in a saved assistant message:

```text
Using jd-registry? No — this is a self-introduction request.
```

The fix must not be a broad regular expression that guesses which prose is internal.
Instead, the MetaBot Core Chat completion contract is versioned and split explicitly:

```json
{
  "type": "complete",
  "payload": {
    "final": true,
    "result": {
      "success": true,
      "publicAnswerMarkdown": "...",
      "responseText": "...protected compatibility payload..."
    }
  }
}
```

Only `result.publicAnswerMarkdown` may become a member-visible assistant message.
`result.responseText`, intermediate `state.responseText`, `log`, tool activity, and
bridge metadata are protected execution data. During a coordinated rollout, an old
worker that omits `publicAnswerMarkdown` fails closed with a user-safe compatibility
error; Platform must not fall back to displaying `responseText`.

The producing Agent must construct `publicAnswerMarkdown` from its terminal final
answer channel, not by copying an accumulated transcript or the latest progress
state. In addition:

1. the direct-Agent system instruction explicitly requires a final answer without
   tool-selection narration, planning notes, or self-commentary;
2. MetaBot callback types remain separated: only the terminal
   `publicAnswerMarkdown` field can become an assistant message; state and log
   payloads remain events;
3. Platform validates that completion has the expected public-answer shape before
   saving it;
4. a malformed completion fails with a user-safe message and a support reference,
   while the original protected payload remains operator-only.

No internal reasoning is silently copied into another public field. Validation checks
the typed envelope, size, encoding, terminal success state, and explicit forbidden
protocol markers; it is not a general prose classifier. No model output is sent to a
second model merely to decide whether it is safe to display.

### 5.2 Answer presentation

Markdown remains supported. Self-introduction and broad capability answers should be
concise and end with useful next actions. The workspace can render up to four task
starters from the Agent Catalog's `example_tasks`; selecting one fills the composer
and never sends without a user action.

The Platform does not rewrite substantive Agent answers for style. Concision and
scope discipline belong to the Agent instruction and are verified with scenario
tests.

## 6. Execution presentation

### 6.1 Direct professional-Agent conversation

For a successful direct-Agent Turn:

- while active, show `HR Agent 正在处理…` and the Stop control;
- after completion, remove the active banner;
- do not render a seven-event execution card;
- optionally show one quiet summary below the answer, such as
  `HR Agent 已完成 · 12 秒`, without raw status codes.

For a failed, timed-out, or interrupted Turn, show one user-facing state, a safe retry
action, and an optional support reference. Do not expose diagnostics.

### 6.2 Agent Brain conversation

The Brain workspace may show the collaboration process because delegation is part of
the product value. Its public timeline contains business events only:

- Agent 大脑正在分析需求;
- 已交给 HR Agent / Marketing Agent;
- a named professional Agent is processing or has returned a result;
- Agent 大脑正在整合结果;
- completed, partially completed, waiting for input, or unavailable.

Raw event names and transport statuses remain hidden. The control is labelled
`查看协作过程`, not `执行过程` or `诊断详情`. Detailed records remain in Management
Center.

### 6.3 Role enforcement

This is not only a CSS rule. Member-facing API projections must omit protected fields
and diagnostic URLs. Management APIs continue to enforce owner/viewer authorization
on the backend. A member who constructs a Management URL manually receives 403.

## 7. Feedback

Completed assistant answers expose a visually quiet feedback control:

- `有帮助`
- `需改进`

`有帮助` records immediately. `需改进` opens an optional reason selector with:

- 结论不准确
- 信息不完整
- 表达不清
- 没有解决问题
- 其他

An optional short comment of at most 1,000 UTF-8 bytes may accompany an improvement
reason. Feedback remains
bound to the authenticated internal user, message, conversation, and Agent. Members
see only their submission state; Management Center receives the full review input.

## 8. Errors and recovery

- A temporarily disconnected event stream says that the Platform is reconnecting and
  will not duplicate the task.
- A direct Agent that is locally offline is shown as unavailable before a new task is
  accepted when possible; it is never silently replaced by another Agent.
- A retry of an interrupted Turn creates an explicit retry Turn and preserves the
  prior failed Turn for audit.
- Failed feedback never changes the answer state and offers a feedback-only retry.
- Missing Catalog display metadata does not expose the raw Agent ID.
- The composer retains unsent input across recoverable connection and authentication
  failures.

## 9. Component boundaries

Implementation should preserve these boundaries:

- `AgentUsePage`: domain-aware Agent navigation and workspace selection;
- `ConversationSidebar`: Agent-scoped history, rename, and archive controls;
- `ConversationPage`: continuous Turn lifecycle and reconnection;
- `ConversationMessages`: public message rendering and feedback entry;
- `PublicProgress`: direct-Agent or Brain collaboration projection;
- `Management Center`: the only UI that links to Mission/Run/Trace/Evidence details;
- MetaBot Core Chat contract: typed public completion separated from logs and states;
- Catalog: canonical public labels, persona subtitle, examples, and domain membership.

The existing `ExecutionCard` may be retained for Management Center or replaced by two
explicit projections. It must not remain a shared component that can accidentally
render diagnostic links in a member workspace.

## 10. Testing and acceptance

### 10.1 Automated tests

Tests must prove:

1. HR pages never render Marketing switching controls.
2. Marketing pages render exactly the five authorized Marketing choices.
3. Member pages contain no raw `hr-bot`, transport status, `诊断详情`, Mission link,
   Run link, Trace link, or Evidence link.
4. Conversation headings do not duplicate the first user message.
5. Direct-Agent success does not render the raw event list.
6. Brain collaboration renders public delegation states without diagnostic fields.
7. a completion without `publicAnswerMarkdown`, or with a known explicit internal
   protocol marker in that field, is rejected by the public-answer contract and is
   not saved as a public assistant message; `responseText` is never used as fallback.
8. state/log callback payloads cannot become assistant messages.
9. feedback reason and comment are identity-bound and owner-only outside the member's
   own submission state.
10. history remains Agent-scoped, newest-first, paginated, renameable, and
    recoverably archivable.
11. mobile navigation, composer, feedback, and collaboration disclosure remain usable.
12. direct Agent offline, timeout, retry, stream reconnect, and feedback failure each
    use the defined member-safe language.

### 10.2 Production acceptance

Use a real member account to verify one HR conversation, one Marketing conversation,
one Brain multi-Agent conversation, and one controlled failure. Verify the same runs
in Management Center and prove that the operator sees full diagnostics while the
member sees only the public projection.

The acceptance report records release identifiers and screenshots but contains no
Cookie, token, raw private prompt, candidate data, or provider payload.

## 11. Delivery order

1. Remove cross-domain switcher and raw-ID display defects.
2. Separate public progress from diagnostic execution records.
3. Deploy a backward-compatible MetaBot producer that emits the typed public answer,
   then enforce the fail-closed final-answer-only contract in Platform; do not enable
   strict consumption before the local worker reports the new contract version.
4. Remove duplicate conversation heading and simplify direct-Agent progress.
5. Improve history rename/archive and public states.
6. Add Catalog-backed task starters.
7. Add structured improvement feedback.
8. Run member/operator and mobile production acceptance.

## 12. Non-goals

- changing the Agent Brain orchestration architecture;
- moving MetaBot execution from the local Mac;
- embedding FAE or AI ADMIN inside the chat renderer;
- exposing chain-of-thought or internal reasoning;
- giving members Management Center access;
- hard-deleting retained conversations;
- using a model to cosmetically rewrite every professional-Agent answer.
