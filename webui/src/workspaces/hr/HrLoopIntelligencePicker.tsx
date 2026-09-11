import { useEffect, useRef, useState } from "react";
import {
  HrLoopError,
  type ExactRef,
  type HrLoopApi,
  type ResourceItem,
} from "../../hrLoopApi";
import { HrLoopMethodPreview } from "./HrLoopMethodPreview";

type Props = {
  api: HrLoopApi;
  initialRef?: ExactRef;
  disabled: boolean;
  onSelect: (ref: ExactRef) => void;
  onClose: () => void;
  onAccessError: (error: unknown) => void;
  onAvailability?: (ref: ExactRef, available: boolean) => void;
};
const identity = (ref: ExactRef) =>
  JSON.stringify([ref.kind, ref.id, ref.revision, ref.sha256]);
const scopes: Record<string, string> = {
  company: "公司研究",
  topic: "专题研究",
  direction: "方向研究",
  "secondary-direction": "细分方向研究",
  task: "招聘任务研究",
  executive: "研究总览",
  index: "资料范围与阅读说明",
};
const coverage: Record<string, string> = {
  succeeded: "所查来源读取成功",
  partial: "部分覆盖",
  failed: "来源读取失败",
  not_observed: "尚未观测",
  empty_confirmed: "所查来源确认为空",
};
function metadata(text: string) {
  const front =
    text.match(/^\uFEFF?---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/)?.[1] ?? "";
  // Display scalar fields from the existing publication format, never execute YAML.
  const field = (name: string) =>
    front
      .match(new RegExp(`^${name}:\\s*([^\\r\\n]+)$`, "m"))?.[1]
      .trim()
      .replace(/^['"]|['"]$/g, "") ?? "";
  const observed = field("observed_at");
  return {
    title: text.match(/^# (.+)$/m)?.[1] ?? "情报正文",
    scope: scopes[field("scope")] ?? "研究范围见正文",
    observed:
      /^\d{4}-\d{2}-\d{2}T/.test(observed) &&
      Number.isFinite(Date.parse(observed))
        ? observed
        : null,
    coverage: coverage[field("coverage")] ?? "覆盖情况见正文",
  };
}

export function HrLoopIntelligencePicker({
  api,
  initialRef,
  disabled,
  onSelect,
  onClose,
  onAccessError,
  onAvailability,
}: Props) {
  const [items, setItems] = useState<ResourceItem[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [catalogError, setCatalogError] = useState("");
  const [preview, setPreview] = useState<{
    ref: ExactRef;
    text: string | null;
    error: string;
  } | null>(null);
  const live = useRef(true);
  const listRequest = useRef(0);
  const bodyRequest = useRef(0);
  const selectedIdentity = initialRef ? identity(initialRef) : "";
  function accessError(error: unknown) {
    if (error instanceof HrLoopError && [401, 403].includes(error.status))
      onAccessError(error);
  }
  async function refresh() {
    const request = ++listRequest.current;
    setLoading(true);
    setCatalogError("");
    try {
      const value = await api.knowledge("intelligence");
      if (live.current && request === listRequest.current)
        setItems(value.items);
    } catch (error) {
      if (live.current && request === listRequest.current) {
        setItems([]);
        setCatalogError("情报目录暂时不可用，请重试。");
        accessError(error);
      }
    } finally {
      if (live.current && request === listRequest.current) setLoading(false);
    }
  }
  async function read(ref: ExactRef) {
    const request = ++bodyRequest.current;
    setPreview({ ref, text: null, error: "" });
    try {
      const value = await api.intelligence(ref);
      if (identity(value.ref) !== identity(ref))
        throw new HrLoopError(410, "reference_unavailable", {});
      if (live.current && request === bodyRequest.current) {
        setPreview({ ref, text: value.text, error: "" });
        onAvailability?.(ref, true);
      }
    } catch (error) {
      if (live.current && request === bodyRequest.current) {
        onAvailability?.(ref, false);
        setPreview({
          ref,
          text: null,
          error:
            error instanceof HrLoopError && [404, 410].includes(error.status)
              ? "这份情报已不可用。已选引用仍保留，请移除或明确另选报告。"
              : "这份情报暂不可读。可重试读取，或移除本次参考。",
        });
        accessError(error);
      }
    }
  }
  useEffect(() => {
    live.current = true;
    void refresh();
    return () => {
      live.current = false;
      listRequest.current++;
      bodyRequest.current++;
    };
  }, [api]);
  useEffect(() => {
    if (initialRef) void read(initialRef);
  }, [api, selectedIdentity]);
  const details =
    preview?.text !== null && preview?.text !== undefined
      ? metadata(preview.text)
      : null;
  const filtered = items.filter((item) =>
    `${item.title} ${item.description}`
      .toLocaleLowerCase()
      .includes(query.trim().toLocaleLowerCase()),
  );
  return (
    <section
      className="hr-loop-method hr-loop-intelligence"
      aria-label="公司与专题情报"
    >
      <header>
        <h2>公司与专题情报</h2>
        <button type="button" onClick={onClose}>
          关闭情报
        </button>
      </header>
      <p>阅读已发布的研究、证据与限制，再选入本次讨论。</p>
      <div className="hr-loop-intelligence-search">
        <label>
          查找情报
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <button type="button" disabled={loading} onClick={() => void refresh()}>
          重新加载目录
        </button>
      </div>
      {catalogError && <p role="alert">{catalogError}</p>}
      {loading ? (
        <p role="status">正在读取情报目录…</p>
      ) : (
        !catalogError &&
        filtered.length === 0 && (
          <p>{query ? "没有匹配的情报。" : "暂无已发布情报。"}</p>
        )
      )}
      <ul className="hr-loop-intelligence-list" aria-label="已发布情报目录">
        {filtered.map((item) => (
          <li key={identity(item.ref)}>
            <button type="button" onClick={() => void read(item.ref)}>
              阅读：{item.title}
            </button>
            <small>
              {item.objects?.some((object) => object.kind === "company")
                ? "公司研究"
                : item.objects?.some((object) => object.kind === "topic")
                  ? "专题研究"
                  : "研究总览"}{" "}
              · {item.description}
            </small>
          </li>
        ))}
      </ul>
      {preview && (
        <article aria-label="情报正文预览">
          {preview.error ? (
            <>
              <p role="alert">{preview.error}</p>
              <button type="button" onClick={() => void read(preview.ref)}>
                重试读取
              </button>
            </>
          ) : details ? (
            <>
              <h3>{details.title}</h3>
              <p className="hr-loop-intelligence-meta">
                {details.scope} · {details.coverage}
                <br />
                资料时间：
                {details.observed ? (
                  <time dateTime={details.observed}>
                    {new Date(details.observed).toLocaleString("zh-CN")}
                  </time>
                ) : (
                  "未提供"
                )}
              </p>
              <HrLoopMethodPreview content={preview.text!} />
              <button
                type="button"
                disabled={disabled}
                onClick={() => onSelect(preview.ref)}
              >
                带此情报讨论
              </button>
            </>
          ) : (
            <p role="status">正在读取这份情报…</p>
          )}
        </article>
      )}
    </section>
  );
}
