import { useEffect, useRef, useState, type FormEvent } from "react";

import type { Account } from "../auth";
import { createMissionSubmission, fetchAgentCatalog, missionInputTooLarge, type MissionSubmission } from "../brainApi";
import type { AgentCapabilityCard } from "../brainTypes";
import { ErrorState, LoadingState } from "../components/DataState";
import { PlatformLink } from "../components/PlatformLink";
import { navigate } from "../router";


export function AgentUsePage({
  account,
  agentId,
  loadCatalog = fetchAgentCatalog,
  createSubmission = createMissionSubmission,
  onOpenMission = (path) => navigate(path),
}: {
  account: Account;
  agentId: string;
  loadCatalog?: (signal?: AbortSignal) => Promise<AgentCapabilityCard[]>;
  createSubmission?: (text: string, csrfToken: string, agentId?: string) => MissionSubmission;
  onOpenMission?: (path: string) => void;
}) {
  const [card, setCard] = useState<AgentCapabilityCard | null>(null);
  const [loadFailure, setLoadFailure] = useState(false);
  const [text, setText] = useState("");
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState(false);
  const retained = useRef<{ text: string; submission: MissionSubmission } | null>(null);
  const inFlight = useRef(false);
  const controllerRef = useRef<AbortController | null>(null);
  const inputTooLarge = missionInputTooLarge(text.trim());

  useEffect(() => {
    const controller = new AbortController();
    setCard(null);
    setLoadFailure(false);
    setText("");
    setPending(false);
    setFailure(false);
    retained.current = null;
    inFlight.current = false;
    loadCatalog(controller.signal).then((cards) => {
      if (controller.signal.aborted) return;
      const selected = cards.find((item) => item.agent_id === agentId);
      if (selected) setCard(selected); else setLoadFailure(true);
    }).catch(() => { if (!controller.signal.aborted) setLoadFailure(true); });
    return () => {
      controller.abort();
      controllerRef.current?.abort();
    };
  }, [agentId, loadCatalog]);

  const send = async () => {
    const normalized = text.trim();
    if (!card || !normalized || inputTooLarge || inFlight.current || account.hard_stale_read_only) return;
    let selected = retained.current;
    if (!selected || selected.text !== normalized) {
      selected = { text: normalized, submission: createSubmission(normalized, account.csrf_token, card.agent_id) };
      retained.current = selected;
    }
    const controller = new AbortController();
    controllerRef.current?.abort();
    controllerRef.current = controller;
    inFlight.current = true;
    setPending(true);
    setFailure(false);
    try {
      const mission = await selected.submission.send(controller.signal);
      retained.current = null;
      onOpenMission(`/missions/${encodeURIComponent(mission.mission_id)}`);
    } catch {
      if (!controller.signal.aborted) setFailure(true);
    } finally {
      if (controllerRef.current === controller) {
        inFlight.current = false;
        if (!controller.signal.aborted) setPending(false);
      }
    }
  };

  const submit = (event: FormEvent) => { event.preventDefault(); void send(); };

  if (loadFailure) return <><PlatformLink className="back-link" href="/agents">← 返回专业 Agent</PlatformLink><ErrorState /></>;
  if (!card) return <LoadingState label="正在打开专业 Agent" />;
  return <div className="agent-use-page">
    <PlatformLink className="back-link" href="/agents">← 返回专业 Agent</PlatformLink>
    <section className="agent-use-profile">
      <span>{card.domain_group}</span><h1>{card.display_name}</h1><p>{card.mission}</p>
      <div><section><h2>可以完成</h2><ul>{card.capabilities.map((item) => <li key={item}>{item}</li>)}</ul></section>
        <section><h2>能力边界</h2><ul>{card.exclusions.map((item) => <li key={item}>{item}</li>)}</ul></section></div>
    </section>
    <form className="agent-direct-composer" onSubmit={submit}>
      <label htmlFor="direct-agent-request">直接交给 {card.display_name}</label>
      <textarea id="direct-agent-request" rows={5} maxLength={32 * 1024} value={text}
        disabled={account.hard_stale_read_only}
        placeholder={card.example_tasks[0] ?? "描述任务目标和背景…"}
        onChange={(event) => {
          const next = event.target.value;
          setText(next);
          if (retained.current?.text !== next.trim()) retained.current = null;
          setFailure(false);
        }} />
      <div><span>仅支持纯文本；任务会保存在你的历史记录中。</span><button disabled={!text.trim() || inputTooLarge || pending || account.hard_stale_read_only} type="submit">{pending ? "正在提交…" : "开始任务"}</button></div>
    </form>
    {inputTooLarge && <p className="mission-input-error" role="alert">输入超过 32 KiB，请精简后再提交。</p>}
    {failure && <div className="brain-submit-error" role="alert"><span>任务暂未提交成功，可安全重试。</span><button onClick={() => void send()} type="button">重新提交</button></div>}
  </div>;
}
