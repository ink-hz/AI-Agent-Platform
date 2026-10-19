import { useEffect, useRef, useState, type FormEvent } from "react";

import type { Account } from "../auth";
import { RequestIdentity } from "./requestIdentity";
import { SdkApiError, type BuildInput, type CandidateDetail, type CandidateSummary, type Gate, type Manifest, type SdkApi, type SdkRole, type Target, type TestInput } from "./sdkApi";

const MODULE_PATHS = ["FrameProcessor", "OBEnhancedDepthFilter", "OBFilterLib", "OBSDKPrivFilterLib", "OpenOrbbecSDK", "OpenOrbbecSDKExt", "OpenOrbbecViewer", "OpenOrbbecViewer/third_party/OpenOrbbecSDK"];
const REASONS: Record<string, string> = {
  build_missing: "缺少构建", build_not_successful: "最近构建未成功", unit_not_passed: "单元测试未通过",
  hardware_not_passed: "硬件测试未通过", requirement_review_outdated: "需求版本或评审已变化",
};

function describe(reason: string): string {
  const [target, code] = reason.split(":");
  return code ? `${target}：${REASONS[code] ?? code}` : (REASONS[reason] ?? reason);
}

function errorText(error: unknown): string {
  return error instanceof SdkApiError ? `操作未完成：${error.detail}` : "SDK 证据暂时无法读取，请稍后重试。";
}

type Props = { account: Account; role: SdkRole; readOnly: boolean; client: SdkApi; storyId?: number; taskIds?: number[] };

export function CandidateWorkspace({ account, role, readOnly, client, storyId, taskIds = [] }: Props) {
  const [items, setItems] = useState<CandidateSummary[]>([]);
  const [candidatePage, setCandidatePage] = useState(1);
  const [candidateTotal, setCandidateTotal] = useState(0);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<CandidateDetail | null>(null);
  const [gate, setGate] = useState<Gate | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [packagingSha, setPackagingSha] = useState("");
  const [sdkVersion, setSdkVersion] = useState("");
  const [scriptPath, setScriptPath] = useState("");
  const [moduleShas, setModuleShas] = useState<Record<string, string>>({});
  const [targetsText, setTargetsText] = useState("linux/x86_64\nwindows/x86_64");
  const [buildTarget, setBuildTarget] = useState("");
  const [buildStatus, setBuildStatus] = useState<BuildInput["status"]>("success");
  const [command, setCommand] = useState("");
  const [logUri, setLogUri] = useState("");
  const [artifactName, setArtifactName] = useState("");
  const [artifactSha, setArtifactSha] = useState("");
  const [artifactSize, setArtifactSize] = useState("");
  const [artifactUri, setArtifactUri] = useState("");
  const [testBuild, setTestBuild] = useState("");
  const [suite, setSuite] = useState<TestInput["suite"]>("unit");
  const [testStatus, setTestStatus] = useState<TestInput["status"]>("passed");
  const [passed, setPassed] = useState("0");
  const [failed, setFailed] = useState("0");
  const [skipped, setSkipped] = useState("0");
  const [reportUri, setReportUri] = useState("");
  const [device, setDevice] = useState("");
  const [firmware, setFirmware] = useState("");
  const [approvalNote, setApprovalNote] = useState("");
  const [channel, setChannel] = useState("");
  const [rollbackUri, setRollbackUri] = useState("");
  const [releasedAt, setReleasedAt] = useState("");
  const createRequest = useRef(new RequestIdentity()).current;
  const buildRequest = useRef(new RequestIdentity()).current;
  const testRequest = useRef(new RequestIdentity()).current;
  const approvalRequest = useRef(new RequestIdentity()).current;
  const releaseRequest = useRef(new RequestIdentity()).current;

  useEffect(() => {
    let live = true;
    void client.candidates(candidatePage).then(list => {
      if (!live) return;
      setItems(list.items);
      setCandidateTotal(list.total);
      setSelectedId(current => current != null && list.items.some(item => item.id === current) ? current : (list.items[0]?.id ?? null));
    }).catch(error => { if (live) setError(errorText(error)); });
    return () => { live = false; };
  }, [client, candidatePage]);

  useEffect(() => {
    if (!selectedId) { setDetail(null); setGate(null); return; }
    let live = true;
    setGate(null);
    void client.candidate(selectedId).then(candidate => { if (live) setDetail(candidate); })
      .catch(error => { if (live) setError(errorText(error)); });
    void client.gate(selectedId).then(status => { if (live) setGate(status); })
      .catch(error => { if (live) setError(errorText(error)); });
    return () => { live = false; };
  }, [client, selectedId]);

  async function refresh(id: number, page = candidatePage) {
    const [list, candidate] = await Promise.all([client.candidates(page), client.candidate(id)]);
    setCandidatePage(page); setItems(list.items); setCandidateTotal(list.total); setSelectedId(id); setDetail(candidate);
    try { setGate(await client.gate(id)); }
    catch (error) { setGate(null); setError(errorText(error)); }
  }

  async function submit(action: () => Promise<{ id: number }>, success: (id: number) => string, refreshId: number) {
    setError(""); setNotice("");
    try { const result = await action(); await refresh(refreshId); setNotice(success(result.id)); }
    catch (error) { setError(errorText(error)); }
  }

  async function createCandidate(event: FormEvent) {
    event.preventDefault(); setError(""); setNotice("");
    if (!storyId) return;
    const targets: Target[] = targetsText.trim().split(/\n+/).map(line => {
      const [os, arch] = line.trim().split("/"); return { os, arch };
    });
    const manifest: Manifest = { packaging_sha: packagingSha, sdk_version: sdkVersion, script_path: scriptPath, modules: moduleShas };
    const input = { story_id: storyId, task_ids: taskIds, manifest, targets };
    try {
      const result = await client.createCandidate({ request_id: createRequest.forPayload(input), ...input }, account.csrf_token);
      createRequest.clear(); await refresh(result.id, 1); setNotice(`候选 #${result.id} 已冻结；源码清单由人工登记，待核对。`);
    } catch (error) { setError(errorText(error)); }
  }

  const successfulBuilds = detail?.builds.filter(build => build.status === "success") ?? [];
  return <section className="sdk-panel sdk-members sdk-candidates">
    <h2>候选、构建与发布</h2>
    <p>源码提交、构建产物及测试结果由人工登记，待核对；平台不读取 GitLab。</p>
    {error && <p className="sdk-error" role="alert">{error}</p>}
    {notice && <p className="sdk-notice" role="status">{notice}</p>}
    <div className="sdk-candidate-layout">
      <div><h3>候选列表</h3><ul className="sdk-list">{items.map(item => <li key={item.id}><button type="button" className={selectedId === item.id ? "selected" : ""} onClick={() => setSelectedId(item.id)}>候选 #{item.id} · 需求 #{item.story_id}</button></li>)}</ul>{items.length === 0 && <p>还没有候选版本。</p>}<div className="sdk-pagination"><button type="button" disabled={candidatePage <= 1} onClick={() => setCandidatePage(candidatePage - 1)}>上一页</button><span>第 {candidatePage} 页</span><button type="button" disabled={candidatePage * 20 >= candidateTotal} onClick={() => setCandidatePage(candidatePage + 1)}>下一页</button></div></div>
      <div>{detail && <>
        <h3>候选 #{detail.id} · SDK {detail.manifest.sdk_version}</h3>
        <p>需求 #{detail.story_id} v{detail.story_version} · 任务 {detail.task_ids.map(id => `#${id}`).join("、")} · 证据版本 {detail.evidence_version}</p>
        <p className="sdk-pre">打包仓库：{detail.manifest.packaging_sha}<br />构建脚本：{detail.manifest.script_path}</p>
        <details><summary>冻结的子模块清单</summary><ul>{Object.entries(detail.manifest.modules).map(([path, sha]) => <li key={path}>{path}: <code>{sha}</code></li>)}</ul></details>
        <h4>发布门槛</h4>
        {!gate && <p>正在计算…</p>}
        {gate && <><p className={gate.ready ? "sdk-notice" : "sdk-error"}>{gate.ready ? (gate.approved ? "可发布 · 已审批" : "证据齐全 · 待审批") : "暂不可发布"}</p>{gate.reasons.length > 0 && <ul>{gate.reasons.map(reason => <li key={reason}>{describe(reason)}</li>)}</ul>}</>}
        <h4>构建与产物</h4><ul>{detail.builds.map(build => <li key={build.id}>#{build.id} {build.os}/{build.arch} · {build.status} · {build.artifact_name || "无产物"}{build.artifact_sha256 && <> · <code>{build.artifact_sha256}</code></>}<br />执行者：{build.actor_id} · {build.created_at}<br />命令：<code>{build.command}</code> · 日志：<code>{build.log_uri}</code>{build.artifact_uri && <> · 产物：<code>{build.artifact_uri}</code></>}</li>)}</ul>
        <h4>测试记录</h4><ul>{detail.tests.map(test => <li key={test.id}>#{test.id} · 产物 #{test.build_id} · {test.suite} · {test.status} · 通过 {test.passed} / 失败 {test.failed} / 跳过 {test.skipped}{test.device && ` · ${test.device} / ${test.firmware}`}<br />执行者：{test.actor_id} · {test.created_at} · 报告：<code>{test.report_uri}</code></li>)}</ul>
        <h4>审批与发布追溯</h4><ul>{detail.approvals.map(approval => <li key={approval.id}>审批 #{approval.id} · 证据 v{approval.evidence_version} · {approval.note} · 审批人 {approval.actor_id} · {approval.created_at}</li>)}{detail.releases.map(release => <li key={`release-${release.id}`}>发布 #{release.id} · {release.channel} · {release.released_at} · 发布登记人 {release.actor_id} · 审批 #{release.approval_id} · 回退：{release.rollback_uri}<br />产物摘要：<code>{release.artifact_sha256s.join(", ")}</code></li>)}</ul>
      </>}</div>
    </div>
    {role === "contributor" && !readOnly && storyId && taskIds.length > 0 && <form className="sdk-form" onSubmit={event => { void createCandidate(event); }}><h3>冻结候选基线 · 需求 #{storyId}</h3>
      <label>打包仓库 40 位提交 SHA<input required pattern="[0-9a-f]{40}" value={packagingSha} onChange={event => setPackagingSha(event.target.value)} /></label>
      <label>SDK 版本<input required maxLength={80} value={sdkVersion} onChange={event => setSdkVersion(event.target.value)} /></label>
      <label>构建脚本路径<input required maxLength={300} value={scriptPath} onChange={event => setScriptPath(event.target.value)} /></label>
      <div className="sdk-module-grid">{MODULE_PATHS.map(path => <label key={path}>{path}<input required pattern="[0-9a-f]{40}" value={moduleShas[path] ?? ""} onChange={event => setModuleShas(old => ({ ...old, [path]: event.target.value }))} /></label>)}</div>
      <label>目标平台，每行 os/arch<textarea required value={targetsText} onChange={event => setTargetsText(event.target.value)} /></label><button type="submit">冻结候选</button>
    </form>}
    {detail && role === "contributor" && !readOnly && <form className="sdk-form" onSubmit={event => { event.preventDefault(); const [os, arch] = (buildTarget || `${detail.targets[0]?.os}/${detail.targets[0]?.arch}`).split("/"); const input: Omit<BuildInput, "request_id"> = { os, arch, status: buildStatus, command, log_uri: logUri, artifact_name: buildStatus === "success" ? artifactName : null, artifact_sha256: buildStatus === "success" ? artifactSha : null, artifact_size: buildStatus === "success" ? Number(artifactSize) : null, artifact_uri: buildStatus === "success" ? artifactUri : null }; void submit(async () => { const result = await client.addBuild(detail.id, { request_id: buildRequest.forPayload(input), ...input }, account.csrf_token); buildRequest.clear(); return result; }, id => `构建 #${id} 已记录`, detail.id); }}><h3>登记构建</h3>
      <label>目标<select value={buildTarget} onChange={event => setBuildTarget(event.target.value)}>{detail.targets.map(target => <option key={`${target.os}/${target.arch}`} value={`${target.os}/${target.arch}`}>{target.os}/{target.arch}</option>)}</select></label>
      <label>状态<select value={buildStatus} onChange={event => setBuildStatus(event.target.value as BuildInput["status"])}><option value="success">成功</option><option value="failed">失败</option><option value="cancelled">取消</option><option value="timeout">超时</option></select></label>
      <label>构建命令<input required value={command} onChange={event => setCommand(event.target.value)} /></label><label>日志 URI<input required value={logUri} onChange={event => setLogUri(event.target.value)} /></label>
      {buildStatus === "success" && <><label>产物名称<input required value={artifactName} onChange={event => setArtifactName(event.target.value)} /></label><label>产物 SHA-256<input required pattern="[0-9a-f]{64}" value={artifactSha} onChange={event => setArtifactSha(event.target.value)} /></label><label>产物大小（字节）<input required type="number" min="1" value={artifactSize} onChange={event => setArtifactSize(event.target.value)} /></label><label>产物 URI<input required value={artifactUri} onChange={event => setArtifactUri(event.target.value)} /></label></>}
      <button type="submit">登记构建</button>
    </form>}
    {detail && role === "reviewer" && !readOnly && successfulBuilds.length > 0 && <form className="sdk-form" onSubmit={event => { event.preventDefault(); const input: Omit<TestInput, "request_id"> = { suite, status: testStatus, passed: Number(passed), failed: Number(failed), skipped: Number(skipped), report_uri: reportUri, device, firmware }; const buildId = Number(testBuild || successfulBuilds[0].id); void submit(async () => { const result = await client.addTest(buildId, { request_id: testRequest.forPayload({ buildId, ...input }), ...input }, account.csrf_token); testRequest.clear(); return result; }, id => `测试 #${id} 已记录`, detail.id); }}><h3>登记测试</h3>
      <label>产物<select value={testBuild} onChange={event => setTestBuild(event.target.value)}>{successfulBuilds.map(build => <option key={build.id} value={build.id}>#{build.id} · {build.os}/{build.arch} · {build.artifact_name}</option>)}</select></label>
      <label>类别<select value={suite} onChange={event => setSuite(event.target.value as TestInput["suite"])}><option value="unit">单元测试</option><option value="hardware">硬件测试</option></select></label>
      <label>状态<select value={testStatus} onChange={event => setTestStatus(event.target.value as TestInput["status"])}><option value="passed">通过</option><option value="failed">失败</option><option value="blocked">阻塞</option><option value="skipped">跳过</option></select></label>
      <label>通过数<input required type="number" min="0" value={passed} onChange={event => setPassed(event.target.value)} /></label><label>失败数<input required type="number" min="0" value={failed} onChange={event => setFailed(event.target.value)} /></label><label>跳过数<input required type="number" min="0" value={skipped} onChange={event => setSkipped(event.target.value)} /></label>
      <label>报告 URI<input required value={reportUri} onChange={event => setReportUri(event.target.value)} /></label>{suite === "hardware" && <><label>设备<input required value={device} onChange={event => setDevice(event.target.value)} /></label><label>固件<input required value={firmware} onChange={event => setFirmware(event.target.value)} /></label></>}
      <button type="submit">登记测试</button>
    </form>}
    {detail && gate?.ready && !gate.approved && role === "release_manager" && !readOnly && detail.creator_id !== account.internal_user_id && <form className="sdk-form" onSubmit={event => { event.preventDefault(); const input = { note: approvalNote }; void submit(async () => { const result = await client.approve(detail.id, { request_id: approvalRequest.forPayload(input), ...input }, account.csrf_token); approvalRequest.clear(); return result; }, id => `审批 #${id} 已记录`, detail.id); }}><h3>发布审批</h3><label>审批说明<textarea required value={approvalNote} onChange={event => setApprovalNote(event.target.value)} /></label><button type="submit">批准当前证据版本</button></form>}
    {detail && gate?.approved && role === "release_manager" && !readOnly && <form className="sdk-form" onSubmit={event => { event.preventDefault(); const input = { channel, rollback_uri: rollbackUri, released_at: new Date(releasedAt).toISOString() }; void submit(async () => { const result = await client.release(detail.id, { request_id: releaseRequest.forPayload(input), ...input }, account.csrf_token); releaseRequest.clear(); return result; }, id => `发布 #${id} 已记录`, detail.id); }}><h3>登记实际发布</h3><label>渠道<input required value={channel} onChange={event => setChannel(event.target.value)} /></label><label>发布时间<input required type="datetime-local" value={releasedAt} onChange={event => setReleasedAt(event.target.value)} /></label><label>回退 URI<input required value={rollbackUri} onChange={event => setRollbackUri(event.target.value)} /></label><button type="submit">登记发布</button></form>}
  </section>;
}
