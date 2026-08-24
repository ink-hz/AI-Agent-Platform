import { useState } from "react";


export function UserInputRequest({
  question,
  disabled,
  pending,
  onSubmit,
}: {
  question: string;
  disabled: boolean;
  pending: boolean;
  onSubmit: (answer: string) => void;
}) {
  const [answer, setAnswer] = useState("");
  const ready = Boolean(answer.trim()) && !disabled && !pending;
  return <section className="user-input-request" aria-live="polite">
    <header><strong>Agent 大脑需要你补充信息</strong><p>{question}</p></header>
    <textarea
      aria-label="回答 Agent 大脑"
      disabled={disabled || pending}
      onChange={(event) => setAnswer(event.target.value)}
      placeholder="输入补充信息，继续当前这一轮"
      rows={3}
      value={answer}
    />
    <button
      disabled={!ready}
      onClick={() => onSubmit(answer.trim())}
      type="button"
    >{pending ? "正在继续…" : "继续执行"}</button>
  </section>;
}
