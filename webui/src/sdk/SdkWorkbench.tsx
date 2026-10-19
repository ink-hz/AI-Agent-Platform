import { useEffect, useRef, useState, type FormEvent } from "react";

import type { Account } from "../auth";
import { navigate } from "../router";
import { sdkApi, SdkApiError, type PlanRow, type ReconciliationIntent, type RequirementDetail, type RequirementList, type SdkApi, type SdkMe, type SdkRole } from "./sdkApi";
import { RequestIdentity } from "./requestIdentity";
import { CandidateWorkspace } from "./CandidateWorkspace";
import { PlanIntake } from "./PlanIntake";
import "./sdkWorkbench.css";

type Props = { account: Account; storyId?: number; client?: SdkApi };

function message(error: unknown): string {
  if (error instanceof SdkApiError) {
    if (error.detail === "sdk_reconcile_required") return "提交结果需要管理员核对，请勿重复创建。";
    if (error.detail === "sdk_requirement_review_required") return "当前需求版本尚未评审通过，不能创建任务。";
    if (error.detail === "sdk_zentao_auth_required") return "禅道服务账号认证失败，请联系平台管理员；可在恢复后重试同一提交。";
    if (error.detail === "sdk_external_mismatch") return "禅道记录与原提交内容不一致，不能绑定。";
    if (error.status === 403) return "当前账号没有执行此操作的 SDK 权限。";
    if (error.status === 422) return "输入不符合要求，请检查必填项与格式。";
    if (error.status === 409) return `状态冲突：${error.detail}`;
  }
  if (error instanceof Error && error.message === "sdk_reconcile_required") return "提交结果需要管理员核对，请勿重复创建。";
  return "暂时无法连接 SDK 工作台，请稍后重试。";
}

export function SdkWorkbench({ account, storyId, client = sdkApi }: Props) {
  const [me, setMe] = useState<SdkMe | null>(null);
  const [list, setList] = useState<RequirementList | null>(null);
  const [detail, setDetail] = useState<RequirementDetail | null>(null);
  const [reviewPage, setReviewPage] = useState(1);
  const [hasOlderReviews, setHasOlderReviews] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [title, setTitle] = useState("");
  const [selectedPlanRow, setSelectedPlanRow] = useState<PlanRow | null>(null);
  const [scenario, setScenario] = useState("");
  const [devicePlatform, setDevicePlatform] = useState("");
  const [acceptance, setAcceptance] = useState("");
  const [taskName, setTaskName] = useState("");
  const [taskDescription, setTaskDescription] = useState("");
  const [reviewDecision, setReviewDecision] = useState<"approved" | "rejected" | "needs_changes">("approved");
  const [reviewReason, setReviewReason] = useState("");
  const [memberId, setMemberId] = useState("");
  const [memberRole, setMemberRole] = useState<SdkRole>("contributor");
  const [members, setMembers] = useState<Array<{ internal_user_id: string; role: SdkRole }>>([]);
  const [reconciliation, setReconciliation] = useState<ReconciliationIntent[]>([]);
  const [selectedIntent, setSelectedIntent] = useState<ReconciliationIntent | null>(null);
  const [externalId, setExternalId] = useState("");
  const requirementRequest = useRef(new RequestIdentity()).current;
  const taskRequest = useRef(new RequestIdentity()).current;
  const reviewRequest = useRef(new RequestIdentity()).current;
  const memberRequest = useRef(new RequestIdentity()).current;
  const reconciliationRequest = useRef(new RequestIdentity()).current;

  useEffect(() => {
    let live = true;
    setError("");
    void client.me().then(async identity => {
      if (!live) return;
      setMe(identity);
      if (identity.role && identity.requirements_enabled !== false) {
        const requirements = await client.requirements();
        if (live) setList(requirements);
      }
      else setList({ items: [], page: 1, total: 0 });
      if (identity.can_manage) {
        void client.members().then(value => { if (live) setMembers(value.items); }).catch(error => { if (live) setError(message(error)); });
        if (identity.requirements_enabled !== false) void client.reconciliation().then(value => { if (live) setReconciliation(value.items); }).catch(error => { if (live) setError(message(error)); });
      }
    }).catch(error => { if (live) setError(message(error)); });
    return () => { live = false; };
  }, [client]);

  useEffect(() => {
    if (!storyId) { setDetail(null); return; }
    let live = true;
    setError("");
    setReviewPage(1); setHasOlderReviews(false);
    void client.requirement(storyId).then(value => { if (live) { setDetail(value); setHasOlderReviews(value.reviews.length === 50); } })
      .catch(error => { if (live) setError(message(error)); });
    return () => { live = false; };
  }, [client, storyId]);

  async function submit<T>(action: () => Promise<T>, success: (result: T) => string) {
    setError(""); setNotice("");
    try { const result = await action(); setNotice(success(result)); }
    catch (error) { setError(message(error)); }
  }

  async function createRequirement(event: FormEvent) {
    event.preventDefault();
    setError(""); setNotice("");
    try {
      const input = { title, scenario, device_platform: devicePlatform, acceptance };
      const request_id = requirementRequest.forPayload({ ...input, plan_row_id: selectedPlanRow?.id });
      const created = selectedPlanRow
        ? await client.createPlanRequirement(selectedPlanRow.id, { request_id, ...input }, account.csrf_token)
        : await client.createRequirement({ request_id, ...input }, account.csrf_token);
      requirementRequest.clear();
      setList(await client.requirements());
      navigate(`/sdk/requirements/${created.id}`);
    } catch (error) { setError(message(error)); }
  }

  async function requirementPage(page: number) {
    try { setError(""); setList(await client.requirements(page)); }
    catch (error) { setError(message(error)); }
  }

  async function olderReviews() {
    if (!storyId || !detail) return;
    try {
      const page = reviewPage + 1;
      const older = await client.reviews(storyId, page);
      setDetail({ ...detail, reviews: [...older.items, ...detail.reviews] });
      setReviewPage(page); setHasOlderReviews(older.items.length === 50);
    } catch (error) { setError(message(error)); }
  }

  if (error && !me) return <section className="sdk-workbench"><h1>SDK 开发管理</h1><p role="alert">{error}</p></section>;
  if (!me || !list) return <section className="sdk-workbench"><h1>SDK 开发管理</h1><p>正在读取需求…</p></section>;
  if (!me.role && !me.can_manage) return <section className="sdk-workbench"><h1>SDK 开发管理</h1><p>当前账号没有 SDK 工作台权限。</p></section>;

  return <section className="sdk-workbench">
    <header><p className="sdk-eyebrow">开发流程 · 需求与任务</p><h1>SDK 开发管理</h1><p>需求、验收标准和任务保存在禅道；评审结论由平台留痕。</p></header>
    {me.read_only && <p className="sdk-notice">当前身份为只读状态。</p>}
    {error && <p className="sdk-error" role="alert">{error}</p>}
    {notice && <p className="sdk-notice" role="status">{notice}</p>}
    {me.role && typeof client.planImports === "function" && <PlanIntake client={client} csrfToken={account.csrf_token} role={me.role} readOnly={me.read_only} onSelect={row => {
      if (row.kind !== "task") return;
      setSelectedPlanRow(row); setTitle(row.cells[1]); setScenario(""); setDevicePlatform(""); setAcceptance(""); requirementRequest.clear();
    }} />}
    {me.requirements_enabled === false && <p className="sdk-notice">年度计划已可阅读；正式需求功能等待禅道服务配置。</p>}
    <div className="sdk-columns">
      <div className="sdk-panel">
        <h2>需求</h2>
        {selectedPlanRow && <p className="sdk-notice">正在整理来源：{selectedPlanRow.module} · 原文件第 {selectedPlanRow.physical_line} 行。请人工补全场景、设备与验收标准。</p>}
        <ul className="sdk-list">{list.items.map(item => <li key={item.id}><button type="button" onClick={() => navigate(`/sdk/requirements/${item.id}`)} className={storyId === item.id ? "selected" : ""}>#{item.id} {item.title}</button><span>{item.status || "待定"}</span></li>)}</ul>
        {list.items.length === 0 && <p>还没有需求。</p>}
        <div className="sdk-pagination"><button type="button" disabled={list.page <= 1} onClick={() => { void requirementPage(list.page - 1); }}>上一页</button><span>第 {list.page} 页</span><button type="button" disabled={list.total == null ? list.items.length < 50 : list.page * 50 >= Number(list.total)} onClick={() => { void requirementPage(list.page + 1); }}>下一页</button></div>
        {me.role === "contributor" && !me.read_only && <form onSubmit={createRequirement} className="sdk-form"><h3>创建需求</h3>
          <label>标题<input required maxLength={200} value={title} onChange={event => setTitle(event.target.value)} /></label>
          <label>场景<textarea required maxLength={4000} value={scenario} onChange={event => setScenario(event.target.value)} /></label>
          <label>设备与平台<input required maxLength={500} value={devicePlatform} onChange={event => setDevicePlatform(event.target.value)} /></label>
          <label>验收标准<textarea required maxLength={4000} value={acceptance} onChange={event => setAcceptance(event.target.value)} /></label>
          <button type="submit" disabled={me.requirements_enabled === false || !!selectedPlanRow?.story_id || (selectedPlanRow?.conversion_state != null && selectedPlanRow.conversion_state !== "rejected")}>创建需求</button>
        </form>}
      </div>
      <div className="sdk-panel">
        <h2>需求详情</h2>
        {storyId && !detail && <p>正在读取详情…</p>}
        {!storyId && <p>从左侧选择一条需求。</p>}
        {detail && <>
          <p className="sdk-id">禅道需求 #{detail.story.id} · 原状态：{detail.story.status || "未知"}</p>
          <h3>{detail.story.title}</h3>
          <h4>描述</h4><p className="sdk-pre">{detail.story.spec || "未填写"}</p>
          <h4>验收标准</h4><p className="sdk-pre">{detail.story.verify || "未填写"}</p>
          <h4>SDK 评审记录</h4>
          {hasOlderReviews && <button type="button" onClick={() => { void olderReviews(); }}>查看更早评审</button>}
          <ul>{detail.reviews.map((review, index) => <li key={index}>{review.decision} · {review.reason} · {review.created_at || ""}</li>)}</ul>
          <h4>开发任务</h4>
          <ul>{detail.tasks.map(task => <li key={task.task_id}>开发任务 #{task.task_id}</li>)}</ul>
          {me.role === "reviewer" && !me.read_only && <form className="sdk-form" onSubmit={event => { event.preventDefault(); void submit(async () => {
            const input = { decision: reviewDecision, reason: reviewReason };
            await client.review(storyId!, { request_id: reviewRequest.forPayload(input), ...input }, account.csrf_token);
            reviewRequest.clear();
            setDetail(await client.requirement(storyId!)); setReviewReason("");
          }, () => "评审已记录"); }}><h4>记录评审</h4>
            <label>结论<select value={reviewDecision} onChange={event => setReviewDecision(event.target.value as typeof reviewDecision)}><option value="approved">通过</option><option value="needs_changes">需修改</option><option value="rejected">不通过</option></select></label>
            <label>理由<textarea required maxLength={2000} value={reviewReason} onChange={event => setReviewReason(event.target.value)} /></label><button type="submit">提交评审</button>
          </form>}
          {me.role === "contributor" && !me.read_only && <form className="sdk-form" onSubmit={event => { event.preventDefault(); void submit(async () => {
            const input = { name: taskName, description: taskDescription };
            const task = await client.createTask(storyId!, { request_id: taskRequest.forPayload(input), ...input }, account.csrf_token);
            taskRequest.clear(); setTaskName(""); setTaskDescription("");
            setDetail(await client.requirement(storyId!));
            return task;
          }, task => `开发任务 #${task.id} 已创建`); }}><h4>拆解开发任务</h4>
            <label>任务名称<input required maxLength={200} value={taskName} onChange={event => setTaskName(event.target.value)} /></label>
            <label>任务说明<textarea required maxLength={4000} value={taskDescription} onChange={event => setTaskDescription(event.target.value)} /></label><button type="submit">创建任务</button>
          </form>}
        </>}
      </div>
    </div>
    {me.role && me.requirements_enabled !== false && <CandidateWorkspace account={account} role={me.role} readOnly={me.read_only} client={client} storyId={storyId} taskIds={detail?.tasks.map(task => task.task_id)} />}
    {me.can_manage && <section className="sdk-panel sdk-members"><h2>成员权限</h2><ul>{members.map(member => <li key={member.internal_user_id}>{member.internal_user_id} · {member.role}</li>)}</ul>
      {!me.read_only && <form className="sdk-form" onSubmit={event => { event.preventDefault(); void submit(async () => {
        const input = { internal_user_id: memberId, role: memberRole };
        await client.grantMember({ request_id: memberRequest.forPayload(input), ...input }, account.csrf_token);
        memberRequest.clear();
        setMembers((await client.members()).items); setMemberId("");
      }, () => "成员权限已保存"); }}>
        <label>内部用户 ID<input required value={memberId} onChange={event => setMemberId(event.target.value)} /></label>
        <label>角色<select value={memberRole} onChange={event => setMemberRole(event.target.value as SdkRole)}><option value="viewer">只读</option><option value="contributor">开发</option><option value="reviewer">评审</option><option value="release_manager">发布</option></select></label>
        <button type="submit">保存成员权限</button>
      </form>}
    </section>}
    {me.can_manage && me.requirements_enabled !== false && <section className="sdk-panel sdk-members"><h2>待核对写入</h2>
      <p>先在禅道核对实际记录，再绑定 ID。未确认前不得重新创建。</p>
      <ul>{reconciliation.map(intent => <li key={`${intent.actor_id}:${intent.request_id}`}>
        {intent.kind} · {intent.request_payload?.title || intent.request_payload?.name || "无摘要"} · {intent.request_id} · 操作者 {intent.actor_id}
        {intent.story_id && <> · 需求 #{intent.story_id}</>}
        <button type="button" onClick={() => { setSelectedIntent(intent); setExternalId(""); }}>选择</button>
      </li>)}</ul>
      {reconciliation.length === 0 && <p>没有待核对写入。</p>}
      {selectedIntent && !me.read_only && <form className="sdk-form" onSubmit={event => { event.preventDefault(); void submit(async () => {
        const input = { external_id: Number(externalId) };
        const bound = await client.bindReconciliation(selectedIntent, { request_id: reconciliationRequest.forPayload({ ...input, actor_id: selectedIntent.actor_id, target_request_id: selectedIntent.request_id }), ...input }, account.csrf_token);
        reconciliationRequest.clear();
        setReconciliation((await client.reconciliation()).items); setSelectedIntent(null); setExternalId("");
        return bound;
      }, result => `已绑定禅道记录 #${result.id}`); }}>
        <label>核对后的禅道 ID<input required inputMode="numeric" pattern="[1-9][0-9]*" value={externalId} onChange={event => setExternalId(event.target.value)} /></label>
        <button type="submit">确认绑定</button>
      </form>}
    </section>}
  </section>;
}
