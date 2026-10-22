import { useEffect, useRef, useState, type FormEvent } from "react";
import DOMPurify from "dompurify";
import { ArrowLeft, ClipboardList, ListTodo, Plus, Search } from "lucide-react";

import { platformPath, type Account } from "../auth";
import { navigate } from "../router";
import { nativeSdkApi, NativeSdkApiError, type NativeMember, type NativeRequirementDetail,
  type NativeRequirementSummary, type NativeSdkApi, type NativeTask } from "./nativeSdkApi";
import { RequestIdentity } from "./requestIdentity";
import "./nativeSdkWorkspace.css";


function statusLabel(status: string): string {
  return ({ draft: "草稿", reviewing: "待评审", active: "已通过", changing: "变更中",
    closed: "已关闭", doing: "进行中", wait: "未开始", done: "已完成" } as Record<string, string>)[status] || status || "未标注";
}

function priorityLabel(priority: number): string {
  return ({ 1: "高", 2: "中", 3: "低" } as Record<number, string>)[priority] || "未设置";
}

function ZenRichText({ html }: { html: string }) {
  if (!html) return <p className="sdk-native-empty-copy">暂未填写</p>;
  return <div className="sdk-native-rich-text" dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(html) }} />;
}

function plainText(html: string): string {
  const node = document.createElement("div");
  node.innerHTML = DOMPurify.sanitize(html || "");
  return node.textContent?.trim() || "";
}

type Section = "requirements" | "tasks";
type Editor = "create" | "edit" | "assign" | "review" | null;
type RequirementForm = { title: string; scenario: string; device_platform: string; acceptance: string;
  priority: number; business_proposer: string; assigned_to: string; reviewer: string };
const emptyForm: RequirementForm = { title: "", scenario: "", device_platform: "", acceptance: "",
  priority: 2, business_proposer: "", assigned_to: "", reviewer: "" };

function RequirementFields({ form, onChange, members, membersLoading, includePeople }: {
  form: RequirementForm; onChange: (patch: Partial<RequirementForm>) => void;
  members: NativeMember[]; membersLoading: boolean; includePeople: boolean;
}) {
  return <div className="sdk-native-field-grid">
    <label className="sdk-native-field-wide">需求标题<input name="title" required maxLength={200} value={form.title}
      onChange={event => onChange({ title: event.target.value })} /></label>
    <label>业务提出人<input name="business_proposer" maxLength={100} value={form.business_proposer}
      onChange={event => onChange({ business_proposer: event.target.value })} placeholder="原需求未提供可留空" /></label>
    <label>优先级<select name="priority" value={form.priority}
      onChange={event => onChange({ priority: Number(event.target.value) })}>
      <option value="1">高</option><option value="2">中</option><option value="3">低</option><option value="4">最低</option>
    </select></label>
    <label className="sdk-native-field-wide">使用场景<textarea name="scenario" required rows={4} maxLength={10000}
      value={form.scenario} onChange={event => onChange({ scenario: event.target.value })} /></label>
    <label className="sdk-native-field-wide">设备与平台<input name="device_platform" required maxLength={500}
      value={form.device_platform} onChange={event => onChange({ device_platform: event.target.value })} /></label>
    <label className="sdk-native-field-wide">验收标准<textarea name="acceptance" required rows={4} maxLength={10000}
      value={form.acceptance} onChange={event => onChange({ acceptance: event.target.value })} /></label>
    {includePeople && <>
      <label>当前负责人<select name="assigned_to" value={form.assigned_to}
        onChange={event => onChange({ assigned_to: event.target.value })} disabled={membersLoading}>
        <option value="">待指派</option>{members.map(member => <option key={member.account} value={member.account}>{member.name} · {member.account}</option>)}
      </select></label>
      <label>评审人<select name="reviewer" value={form.reviewer}
        onChange={event => onChange({ reviewer: event.target.value })} disabled={membersLoading}>
        <option value="">暂不提交评审</option>{members.map(member => <option key={member.account} value={member.account}>{member.name} · {member.account}</option>)}
      </select></label>
    </>}
  </div>;
}

function readableError(error: unknown): string {
  if (error instanceof NativeSdkApiError) {
    if (error.detail === "sdk_requirement_version_conflict") return "需求已被别人更新，请刷新详情后重试。";
    if (error.detail === "sdk_member_not_bound") return "所选成员尚未开通个人禅道账号。";
    if (error.detail === "sdk_provision_in_progress" || error.detail === "sdk_provision_pending_retry") return "个人禅道账号开通中，请稍后重试。";
    if (error.status === 403) return "当前禅道账号没有执行此操作的权限。";
    if (error.status === 422) return "请检查必填内容、人员和格式。";
  }
  return "操作暂时没有完成，请稍后重试。";
}

export function NativeSdkWorkspace({ account, storyId, readOnly = false, client = nativeSdkApi }:
  { account: Account; storyId?: number; readOnly?: boolean; client?: NativeSdkApi }) {
  const [section, setSection] = useState<Section>("requirements");
  const [view, setView] = useState<"q4" | "all">("q4");
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [requirements, setRequirements] = useState<NativeRequirementSummary[]>([]);
  const [requirementTotal, setRequirementTotal] = useState<number | null>(null);
  const [tasks, setTasks] = useState<NativeTask[]>([]);
  const [taskTotal, setTaskTotal] = useState<number | null>(null);
  const [detail, setDetail] = useState<NativeRequirementDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editor, setEditor] = useState<Editor>(null);
  const [form, setForm] = useState<RequirementForm>(emptyForm);
  const [members, setMembers] = useState<NativeMember[]>([]);
  const [membersLoading, setMembersLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [reviewDecision, setReviewDecision] = useState<"pass" | "reject">("pass");
  const [reviewComment, setReviewComment] = useState("");
  const [closedReason, setClosedReason] = useState("");
  const requestIdentity = useRef(new RequestIdentity()).current;

  useEffect(() => {
    let live = true;
    setLoading(true); setError("");
    if (section === "tasks") {
      void client.myTasks(page).then(result => {
        if (live) { setTasks(result.items); setTaskTotal(result.total); }
      }).catch(() => { if (live) setError("暂时无法读取我的任务，请稍后重试。"); })
        .finally(() => { if (live) setLoading(false); });
    } else {
      void client.requirements({ view, page, query, status }).then(result => {
        if (live) { setRequirements(result.items); setRequirementTotal(result.total); }
      }).catch(() => { if (live) setError("暂时无法读取需求，请稍后重试。"); })
        .finally(() => { if (live) setLoading(false); });
    }
    return () => { live = false; };
  }, [client, section, view, page, query, status, refresh]);

  useEffect(() => {
    if (!storyId) return;
    let live = true;
    setDetailLoading(true);
    void client.requirement(storyId).then(value => { if (live) setDetail(value); })
      .catch(() => { if (live) setError("无法读取这条需求，请返回列表重试。"); })
      .finally(() => { if (live) setDetailLoading(false); });
    return () => { live = false; };
  }, [client, storyId]);

  async function openRequirement(id: number) {
    setError(""); setDetailLoading(true);
    try {
      const selected = await client.requirement(id);
      setDetail(selected);
      navigate(`/sdk/requirements/${id}`);
    } catch {
      setError("无法读取这条需求，请返回列表重试。");
    } finally {
      setDetailLoading(false);
    }
  }

  function chooseSection(next: Section) {
    setSection(next); setPage(1); setError(""); setDetail(null); setEditor(null);
    if (next === "requirements") navigate("/sdk/");
  }

  async function showEditor(next: Exclude<Editor, null>) {
    if (readOnly) return;
    setError(""); setNotice(""); setEditor(next);
    if (next === "create") { setForm(emptyForm); requestIdentity.clear(); }
    if (next === "edit" && detail) setForm({ title: detail.title, scenario: plainText(detail.spec),
      device_platform: detail.device_platform || detail.source?.device_platform || "", acceptance: plainText(detail.verify),
      priority: detail.priority || 2, business_proposer: detail.business_proposer || "",
      assigned_to: detail.assigned_to || "", reviewer: detail.reviewer[0] || "" });
    if (next === "assign" && detail) setForm(current => ({ ...current, assigned_to: detail.assigned_to || "" }));
    if (next === "review") { setReviewDecision("pass"); setReviewComment(""); setClosedReason(""); }
    if ((next === "create" || next === "assign") && !members.length) {
      setMembersLoading(true);
      try { setMembers((await client.members()).items); }
      catch (error) { setError(readableError(error)); }
      finally { setMembersLoading(false); }
    }
  }

  async function submitCreate(event: FormEvent) {
    event.preventDefault(); if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const input = { ...form, assigned_to: form.assigned_to || null, reviewer: form.reviewer || null,
        business_proposer: form.business_proposer.trim() || null };
      const result = await client.createRequirement({ ...input, request_id: requestIdentity.forPayload(input) }, account.csrf_token);
      if (result.status === "reconciliation_required") {
        setNotice("提交结果待核对。请保留当前页面，系统不会重复创建同一需求。");
        return;
      }
      requestIdentity.clear(); setEditor(null); setDetail(result.requirement);
      setView("all"); setPage(1); setRefresh(value => value + 1);
      setNotice(`需求 #${result.story_id} 已创建。`);
      navigate(`/sdk/requirements/${result.story_id}`);
    } catch (error) { setError(readableError(error)); }
    finally { setBusy(false); }
  }

  async function submitEdit(event: FormEvent) {
    event.preventDefault(); if (!detail || busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const updated = await client.editRequirement(detail.id, { expected_version: detail.version,
        title: form.title, scenario: form.scenario, device_platform: form.device_platform,
        acceptance: form.acceptance, priority: form.priority,
        business_proposer: form.business_proposer.trim() || null }, account.csrf_token);
      setDetail(updated); setEditor(null); setRefresh(value => value + 1); setNotice("需求已更新。");
    } catch (error) { setError(readableError(error)); }
    finally { setBusy(false); }
  }

  async function submitAssign(event: FormEvent) {
    event.preventDefault(); if (!detail || !form.assigned_to || busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const updated = await client.assign(detail.id, { expected_version: detail.version,
        assigned_to: form.assigned_to }, account.csrf_token);
      setDetail(updated); setEditor(null); setRefresh(value => value + 1); setNotice("负责人已更新。");
    } catch (error) { setError(readableError(error)); }
    finally { setBusy(false); }
  }

  async function submitReview(event: FormEvent) {
    event.preventDefault(); if (!detail || busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const updated = await client.review(detail.id, { expected_version: detail.version,
        decision: reviewDecision, comment: reviewComment.trim(),
        ...(reviewDecision === "reject" ? { closed_reason: closedReason } : {}) }, account.csrf_token);
      setDetail(updated); setEditor(null); setRefresh(value => value + 1);
      setNotice(reviewDecision === "pass" ? "评审已通过。" : "评审已驳回。");
    } catch (error) { setError(readableError(error)); }
    finally { setBusy(false); }
  }

  return <section className="sdk-native-page" aria-label="SDK 开发管理工作台">
    <header className="sdk-native-header">
      <div>
        <p className="sdk-native-eyebrow">Orbbec SDK · 研发协作</p>
        <h1>SDK 开发管理</h1>
        <p>需求和开发任务直接同步禅道，操作记录归属当前员工。</p>
      </div>
      <div className="sdk-native-header-tools">
        <span>{account.display_name}</span>
        <a href={platformPath("/")}><ArrowLeft size={16} aria-hidden="true" /> 返回平台</a>
      </div>
    </header>

    <nav className="sdk-native-tabs" aria-label="SDK 工作区">
      <button type="button" aria-current={section === "requirements" ? "page" : undefined}
        onClick={() => chooseSection("requirements")}><ClipboardList size={18} aria-hidden="true" />需求管理</button>
      <button type="button" aria-current={section === "tasks" ? "page" : undefined}
        onClick={() => chooseSection("tasks")}><ListTodo size={18} aria-hidden="true" />我的任务</button>
    </nav>

    {error && <p className="sdk-native-error" role="alert">{error}</p>}
    {notice && <p className="sdk-native-notice" role="status">{notice}</p>}

    {section === "requirements" ? <>
      <div className="sdk-native-toolbar">
        <div>
          <h2>需求管理</h2>
          <p>{requirementTotal === null ? "正在读取需求" : `${requirementTotal} 条需求`} · 来源于禅道</p>
        </div>
        <button type="button" className="sdk-native-primary" disabled={readOnly || busy}
          title={readOnly ? "当前身份为只读状态" : undefined} onClick={() => { void showEditor("create"); }}>
          <Plus size={16} aria-hidden="true" /> 新建需求
        </button>
      </div>
      {editor === "create" && <form className="sdk-native-editor" aria-label="新建需求" onSubmit={submitCreate}>
        <div className="sdk-native-editor-head"><div><h3>新建需求</h3><p>需求由当前个人禅道账号录入；业务提出人另行记录。</p></div>
          <button type="button" onClick={() => setEditor(null)}>取消</button></div>
        <RequirementFields form={form} onChange={patch => setForm(current => ({ ...current, ...patch }))}
          members={members} membersLoading={membersLoading} includePeople />
        <div className="sdk-native-form-actions"><button type="submit" className="sdk-native-primary" disabled={busy || membersLoading}>
          {busy ? "正在提交…" : "保存需求"}</button></div>
      </form>}
      <div className="sdk-native-filters">
        <div className="sdk-native-view-switch" role="group" aria-label="需求范围">
          <button type="button" aria-pressed={view === "q4"} onClick={() => { setView("q4"); setPage(1); }}>2026 Q4 计划</button>
          <button type="button" aria-pressed={view === "all"} onClick={() => { setView("all"); setPage(1); }}>全部需求</button>
        </div>
        <form onSubmit={event => { event.preventDefault(); setPage(1); setQuery(queryInput.trim()); }} role="search">
          <Search size={16} aria-hidden="true" /><input value={queryInput} onChange={event => setQueryInput(event.target.value)}
            placeholder="搜索需求标题" aria-label="搜索需求标题" />
          <button type="submit">搜索</button>
        </form>
        <label>状态<select value={status} onChange={event => { setStatus(event.target.value); setPage(1); }}>
          <option value="">全部状态</option><option value="draft">草稿</option><option value="reviewing">待评审</option>
          <option value="active">已通过</option><option value="changing">变更中</option><option value="closed">已关闭</option>
        </select></label>
      </div>

      <div className="sdk-native-main">
        <div className="sdk-native-list" aria-label="需求列表">
          {loading ? <p className="sdk-native-placeholder">正在读取禅道需求…</p>
            : requirements.length === 0 ? <p className="sdk-native-placeholder">当前条件下没有需求。</p>
              : requirements.map(item => <button key={item.id} type="button" className={detail?.id === item.id ? "is-selected" : ""}
                onClick={() => { void openRequirement(item.id); }}>
                <span className="sdk-native-list-title"><small>#{item.id}</small>{item.title}</span>
                <span className="sdk-native-list-meta"><span className={`sdk-native-status status-${item.status}`}>{statusLabel(item.status)}</span>
                  <span>{item.source_module || "SDK"}</span><span>{item.assigned_to_name || "待指派"}</span></span>
              </button>)}
          <div className="sdk-native-pager"><button type="button" disabled={page <= 1} onClick={() => setPage(value => value - 1)}>上一页</button>
            <span>第 {page} 页</span><button type="button" disabled={requirementTotal === null || page * 50 >= requirementTotal}
              onClick={() => setPage(value => value + 1)}>下一页</button></div>
        </div>

        <div className="sdk-native-detail" aria-label="需求详情">
          {detailLoading ? <p className="sdk-native-placeholder">正在读取需求详情…</p>
            : !detail ? <div className="sdk-native-detail-empty"><ClipboardList size={34} aria-hidden="true" /><h3>选择一条需求</h3><p>查看场景、验收标准、责任人与评审记录。</p></div>
              : <>
                <div className="sdk-native-detail-head"><div><span className="sdk-native-id">需求 #{detail.id}</span><h2>{detail.title}</h2></div>
                  <span className={`sdk-native-status status-${detail.status}`}>{statusLabel(detail.status)}</span></div>
                {!readOnly && <div className="sdk-native-detail-tools">
                  <button type="button" onClick={() => { void showEditor("edit"); }}>编辑需求</button>
                  <button type="button" onClick={() => { void showEditor("assign"); }}>指派负责人</button>
                  {detail.status === "reviewing" && <button type="button" onClick={() => { void showEditor("review"); }}>评审需求</button>}
                </div>}
                {editor === "edit" && <form className="sdk-native-detail-form" aria-label="编辑需求" onSubmit={submitEdit}>
                  <h3>编辑需求 · 当前版本 {detail.version}</h3>
                  <p>保存后，需求描述与验收标准的原有富文本排版将转换为纯文本。</p>
                  <RequirementFields form={form} onChange={patch => setForm(current => ({ ...current, ...patch }))}
                    members={members} membersLoading={membersLoading} includePeople={false} />
                  <div className="sdk-native-form-actions"><button type="submit" className="sdk-native-primary" disabled={busy}>保存修改</button>
                    <button type="button" onClick={() => setEditor(null)}>取消</button></div>
                </form>}
                {editor === "assign" && <form className="sdk-native-detail-form" aria-label="指派负责人" onSubmit={submitAssign}>
                  <h3>指派负责人 · 当前版本 {detail.version}</h3>
                  <label>当前负责人<select name="assigned_to" required value={form.assigned_to}
                    onChange={event => setForm(current => ({ ...current, assigned_to: event.target.value }))} disabled={membersLoading}>
                    <option value="">选择负责人</option>{members.map(member => <option key={member.account} value={member.account}>{member.name} · {member.account}</option>)}
                  </select></label>
                  <div className="sdk-native-form-actions"><button type="submit" className="sdk-native-primary" disabled={busy || membersLoading}>确认指派</button>
                    <button type="button" onClick={() => setEditor(null)}>取消</button></div>
                </form>}
                {editor === "review" && <form className="sdk-native-detail-form" aria-label="评审需求" onSubmit={submitReview}>
                  <h3>评审需求 · 当前版本 {detail.version}</h3>
                  <label>结论<select value={reviewDecision} onChange={event => setReviewDecision(event.target.value as "pass" | "reject")}>
                    <option value="pass">通过</option><option value="reject">驳回</option></select></label>
                  {reviewDecision === "reject" && <label>驳回原因<select required value={closedReason} onChange={event => setClosedReason(event.target.value)}>
                    <option value="">请选择</option><option value="willnotdo">不做</option><option value="postponed">延期</option>
                    <option value="cancel">已取消</option><option value="bydesign">设计如此</option></select></label>}
                  <label>评审意见<textarea rows={3} value={reviewComment} onChange={event => setReviewComment(event.target.value)} /></label>
                  <div className="sdk-native-form-actions"><button type="submit" className="sdk-native-primary" disabled={busy}>提交评审</button>
                    <button type="button" onClick={() => setEditor(null)}>取消</button></div>
                </form>}
                <dl className="sdk-native-facts">
                  <div><dt>业务提出人</dt><dd>{detail.business_proposer || "原计划未提供"}</dd></div>
                  <div><dt>当前负责人</dt><dd>{detail.assigned_to_name || "待指派"}</dd></div>
                  <div><dt>禅道录入人</dt><dd>{detail.opened_by_name || detail.opened_by || "未记录"}</dd></div>
                  <div><dt>优先级</dt><dd>{priorityLabel(detail.priority)}</dd></div>
                  <div><dt>创建时间</dt><dd>{detail.opened_date || "未记录"}</dd></div>
                  <div><dt>设备与平台</dt><dd>{detail.device_platform || detail.source?.device_platform || "未提供"}</dd></div>
                  <div><dt>来源栏目</dt><dd>{detail.source_module || "未分类"}</dd></div>
                </dl>
                {detail.source && <div className="sdk-native-source"><h3>原计划信息</h3><dl>
                  <div><dt>原责任人</dt><dd>{detail.source.responsible_raw || "未提供"}</dd></div>
                  <div><dt>计划版本</dt><dd>{detail.source.source_version || "未提供"}</dd></div>
                  <div><dt>计划开始</dt><dd>{detail.source.planned_start || "未提供"}</dd></div>
                  <div><dt>计划结束</dt><dd>{detail.source.planned_end || "未提供"}</dd></div>
                </dl></div>}
                <div className="sdk-native-detail-section"><h3>需求描述</h3><ZenRichText html={detail.spec} /></div>
                <div className="sdk-native-detail-section"><h3>验收标准</h3><ZenRichText html={detail.verify} /></div>
                <div className="sdk-native-detail-section"><h3>操作记录</h3>
                  {detail.actions.length ? <ol className="sdk-native-history">{detail.actions.map(action => <li key={action.id}>
                    <strong>{action.actor_name || action.actor}</strong> · {action.action} · <time>{action.date}</time>
                    {action.comment && <p>{action.comment}</p>}
                  </li>)}</ol> : <p className="sdk-native-empty-copy">暂无操作记录</p>}
                </div>
              </>}
        </div>
      </div>
    </> : <div className="sdk-native-task-view"><div className="sdk-native-toolbar"><div><h2>我的任务</h2>
      <p>{taskTotal === null ? "正在读取任务" : `${taskTotal} 条任务`} · 禅道按当前员工返回</p></div></div>
      {loading ? <p className="sdk-native-placeholder">正在读取我的任务…</p>
        : tasks.length === 0 ? <p className="sdk-native-placeholder">当前没有分配给你的任务。</p>
          : <div className="sdk-native-task-list">{tasks.map(task => <article key={task.id}>
              <div><span className="sdk-native-id">任务 #{task.id}</span><h3>{task.name}</h3>
                <p>关联需求 #{task.story_id} · 负责人 {task.assignee_name || task.assignee}</p></div>
              <div><span className={`sdk-native-status status-${task.status}`}>{statusLabel(task.status)}</span>
                <p>计划 {task.planned_start || "未填写"} → {task.deadline || "未填写"}</p></div>
            </article>)}</div>}
      <div className="sdk-native-pager"><button type="button" disabled={page <= 1} onClick={() => setPage(value => value - 1)}>上一页</button>
        <span>第 {page} 页</span><button type="button" disabled={taskTotal === null || page * 50 >= taskTotal}
          onClick={() => setPage(value => value + 1)}>下一页</button></div>
    </div>}
  </section>;
}
