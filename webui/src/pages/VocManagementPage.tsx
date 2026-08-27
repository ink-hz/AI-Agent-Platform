import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";

import {
  vocAdminApi,
  type VocAdminApi,
  type VocAdminDetail,
  type VocAdminFilters,
  type VocAdminSummary,
  type VocSubmitterOption,
} from "../vocAdminApi";

const ANALYSIS_LABEL = {
  pending: "待分析",
  claimed: "分析中",
  succeeded: "已分析",
  failed: "分析失败",
  not_requested: "未请求分析",
} as const;

const SOURCE_LABEL = {
  platform: "Agent Platform",
  dingtalk: "钉钉历史",
} as const;

const ENTRY_LABEL = {
  original: "原始反馈",
  supplement: "补充信息",
  correction: "修正记录",
} as const;

const ATTACHMENT_ONLY_CONTENT = "仅包含附件，暂无文字内容";

const EMPTY_FILTERS: VocAdminFilters = {
  query: null,
  submitterInternalUserId: null,
  legacySubmitterName: null,
  createdFrom: null,
  createdTo: null,
  cursor: null,
  limit: 50,
};

function localDayIso(value: string, exclusiveEnd = false): string | null {
  if (!value) return null;
  const date = new Date(`${value}T00:00:00`);
  if (exclusiveEnd) date.setDate(date.getDate() + 1);
  return date.toISOString();
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export function VocManagementPage({ api = vocAdminApi }: { api?: VocAdminApi }) {
  const [items, setItems] = useState<VocAdminSummary[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [paginationError, setPaginationError] = useState(false);
  const [hasAppliedFilters, setHasAppliedFilters] = useState(false);
  const [appliedFilters, setAppliedFilters] = useState<VocAdminFilters>(EMPTY_FILTERS);
  const [query, setQuery] = useState("");
  const [submitterId, setSubmitterId] = useState("");
  const [legacyName, setLegacyName] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [submitters, setSubmitters] = useState<VocSubmitterOption[]>([]);
  const [selected, setSelected] = useState<VocAdminSummary | null>(null);
  const [detail, setDetail] = useState<VocAdminDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState(false);
  const listController = useRef<AbortController | null>(null);
  const detailController = useRef<AbortController | null>(null);
  const listRequestId = useRef(0);
  const detailRequestId = useRef(0);

  const requestList = useCallback((filters: VocAdminFilters, append: boolean) => {
    listController.current?.abort();
    const controller = new AbortController();
    listController.current = controller;
    const requestId = ++listRequestId.current;
    if (append) {
      setLoadingMore(true);
      setPaginationError(false);
    } else {
      setLoading(true);
      setError(null);
      setPaginationError(false);
    }
    void api.list(filters, controller.signal).then((page) => {
      if (requestId !== listRequestId.current) return;
      setItems((current) => append ? [...current, ...page.items] : page.items);
      setNextCursor(page.next_cursor);
    }).catch((failure: unknown) => {
      if (requestId !== listRequestId.current || isAbort(failure)) return;
      if (append) setPaginationError(true);
      else setError("暂时无法读取 VOC，请稍后重试。");
    }).finally(() => {
      if (requestId !== listRequestId.current) return;
      setLoading(false);
      setLoadingMore(false);
    });
  }, [api]);

  useEffect(() => {
    requestList(EMPTY_FILTERS, false);
    return () => {
      listRequestId.current += 1;
      listController.current?.abort();
    };
  }, [requestList]);

  useEffect(() => {
    const controller = new AbortController();
    void api.submitters(controller.signal).then(setSubmitters).catch((failure: unknown) => {
      if (!isAbort(failure)) setSubmitters([]);
    });
    return () => controller.abort();
  }, [api]);

  useEffect(() => () => {
    detailRequestId.current += 1;
    detailController.current?.abort();
  }, []);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const filters: VocAdminFilters = {
      query: query.trim() || null,
      submitterInternalUserId: submitterId || null,
      legacySubmitterName: legacyName.trim() || null,
      createdFrom: localDayIso(startDate),
      createdTo: localDayIso(endDate, true),
      cursor: null,
      limit: 50,
    };
    setAppliedFilters(filters);
    setHasAppliedFilters(Boolean(
      filters.query || filters.submitterInternalUserId || filters.legacySubmitterName
      || filters.createdFrom || filters.createdTo
    ));
    requestList(filters, false);
  };

  const clear = () => {
    setQuery("");
    setSubmitterId("");
    setLegacyName("");
    setStartDate("");
    setEndDate("");
    setAppliedFilters(EMPTY_FILTERS);
    setHasAppliedFilters(false);
    requestList(EMPTY_FILTERS, false);
  };

  const loadMore = () => {
    if (!nextCursor || loadingMore) return;
    requestList({ ...appliedFilters, cursor: nextCursor }, true);
  };

  const openDetail = (item: VocAdminSummary) => {
    setSelected(item);
    setDetail(null);
    setDetailError(false);
    setDetailLoading(true);
    detailController.current?.abort();
    const controller = new AbortController();
    detailController.current = controller;
    const requestId = ++detailRequestId.current;
    void api.detail(item.voc_no, controller.signal).then((value) => {
      if (requestId === detailRequestId.current) setDetail(value);
    }).catch((failure: unknown) => {
      if (requestId === detailRequestId.current && !isAbort(failure)) setDetailError(true);
    }).finally(() => {
      if (requestId === detailRequestId.current) setDetailLoading(false);
    });
  };

  const closeDetail = () => {
    detailRequestId.current += 1;
    detailController.current?.abort();
    setSelected(null);
    setDetail(null);
  };

  return (
    <section className="voc-management" aria-labelledby="voc-management-title">
      <header className="voc-management__header">
        <div>
          <p className="voc-management__kicker">VOICE OF CUSTOMER</p>
          <h1 id="voc-management-title">VOC 管理</h1>
          <p>集中查看所有员工提交的客户反馈和补充记录。</p>
        </div>
      </header>

      <form className="voc-management__filters" onSubmit={submit}>
        <label>关键词<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="反馈内容或 VOC 编号" /></label>
        <label>平台提交人<select value={submitterId} onChange={(event) => {
          setSubmitterId(event.target.value);
          if (event.target.value) setLegacyName("");
        }}><option value="">全部</option>{submitters.map((item) => <option value={item.internal_user_id} key={item.internal_user_id}>{item.display_name}</option>)}</select></label>
        <label>历史钉钉提交人<input value={legacyName} onChange={(event) => {
          setLegacyName(event.target.value);
          if (event.target.value) setSubmitterId("");
        }} placeholder="历史姓名" /></label>
        <label>开始日期<input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label>
        <label>结束日期<input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} /></label>
        <div className="voc-management__filter-actions">
          <button type="submit">查询</button>
          <button type="button" className="secondary" onClick={clear}>清空</button>
        </div>
      </form>

      <div className="voc-management__content">
        {loading && <div className="voc-management__loading" role="status">正在加载 VOC…</div>}
        {!loading && error && <div className="voc-management__error" role="alert"><p>{error}</p><button type="button" onClick={() => requestList(appliedFilters, false)}>重新加载</button></div>}
        {!loading && !error && items.length === 0 && <div className="voc-management__empty"><h2>{hasAppliedFilters ? "没有符合条件的 VOC" : "还没有 VOC 记录"}</h2><p>{hasAppliedFilters ? "可以调整筛选条件后重新查询。" : "员工提交后会在这里集中展示。"}</p></div>}
        {!loading && !error && items.length > 0 && <div className="voc-management__table-wrap"><table>
          <thead><tr><th>VOC 编号</th><th>提交人</th><th>内容摘要</th><th>来源</th><th>提交时间</th><th>分析状态</th></tr></thead>
          <tbody>{items.map((item) => <tr key={item.voc_no}>
            <td data-label="VOC 编号"><button type="button" className="voc-management__voc-link" onClick={() => openDetail(item)}>{item.voc_no}</button></td>
            <td data-label="提交人">{item.submitter_name}</td>
            <td data-label="内容摘要" className="voc-management__summary">{item.latest_content || ATTACHMENT_ONLY_CONTENT}</td>
            <td data-label="来源"><span className={`voc-management__source is-${item.source}`}>{SOURCE_LABEL[item.source]}</span></td>
            <td data-label="提交时间"><time dateTime={item.created_at}>{formatTime(item.created_at)}</time></td>
            <td data-label="分析状态"><span className={`voc-management__status is-${item.analysis_status}`}>{ANALYSIS_LABEL[item.analysis_status]}</span></td>
          </tr>)}</tbody>
        </table></div>}
        {!loading && items.length > 0 && (nextCursor || paginationError) && <div className="voc-management__pagination">
          {paginationError && <span role="alert">加载更多失败，已有记录已保留。</span>}
          <button type="button" onClick={loadMore} disabled={loadingMore}>{loadingMore ? "正在加载…" : paginationError ? "重试加载更多" : "加载更多"}</button>
        </div>}
      </div>

      {selected && <aside className="voc-management__drawer" aria-label="VOC 详情">
        <div className="voc-management__drawer-head"><div><p className="voc-management__kicker">VOC DETAIL</p><h2>{selected.voc_no}</h2></div><button type="button" className="secondary" onClick={closeDetail}>关闭</button></div>
        {detailLoading && <p role="status">正在加载详情…</p>}
        {detailError && <div role="alert"><p>暂时无法读取详情。</p><button type="button" onClick={() => openDetail(selected)}>重新加载</button></div>}
        {detail && <>
          <dl className="voc-management__metadata">
            <div><dt>提交人</dt><dd>{detail.submitter_name}</dd></div>
            <div><dt>来源</dt><dd>{SOURCE_LABEL[detail.source]}</dd></div>
            <div><dt>分析状态</dt><dd>{ANALYSIS_LABEL[detail.analysis_status]}</dd></div>
            <div><dt>创建时间</dt><dd>{formatTime(detail.created_at)}</dd></div>
            <div><dt>当前版本</dt><dd>第 {detail.revision} 版</dd></div>
          </dl>
          <div className="voc-management__entries">{detail.entries.map((entry) => <article key={`${entry.revision}-${entry.created_at}`}>
            <header><strong>{ENTRY_LABEL[entry.entry_type]}</strong><span>第 {entry.revision} 版 · {formatTime(entry.created_at)}</span></header>
            <p>{entry.content || ATTACHMENT_ONLY_CONTENT}</p>
          </article>)}</div>
        </>}
      </aside>}
    </section>
  );
}
