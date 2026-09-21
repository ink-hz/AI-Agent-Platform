import { useEffect, useRef, useState } from "react";
import { searchAdministratorCandidates, type AdministratorSearchResult, type AdministratorUser } from "../administratorDirectory";

export function AdministratorSearch({ disabled, excludedIds, onSelect, onClose }: {
  disabled: boolean;
  excludedIds: string[];
  onSelect: (user: AdministratorUser) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<AdministratorSearchResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  const sequence = useRef(0);
  useEffect(() => () => { sequence.current++; request.current?.abort(); }, []);
  function changeQuery(value: string) {
    sequence.current++;
    request.current?.abort();
    setQuery(value); setResult(null); setError(""); setLoading(false);
  }
  async function search() {
    if (!query.trim() || disabled) return;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    const selected = ++sequence.current;
    setLoading(true); setResult(null); setError("");
    try {
      const found = await searchAdministratorCandidates(query, controller.signal);
      if (selected === sequence.current) setResult(found);
    } catch {
      if (selected === sequence.current) setError("搜索失败，请重试。");
    } finally {
      if (selected === sequence.current) setLoading(false);
    }
  }
  const candidates = result?.users.filter(user => !excludedIds.includes(user.internal_user_id)) ?? [];
  function ambiguous(user: AdministratorUser) {
    return candidates.some(other => other.internal_user_id !== user.internal_user_id
      && other.display_name === user.display_name
      && [...other.departments].sort().join("\0") === [...user.departments].sort().join("\0"));
  }
  return <section className="administrator-search" aria-label="添加管理员">
    <form onSubmit={event => { event.preventDefault(); void search(); }}>
      <label htmlFor="administrator-query">搜索花名或姓名</label>
      <div className="administrator-search-controls">
        <input id="administrator-query" autoFocus maxLength={256} value={query}
          placeholder="输入通讯录中的花名或姓名" onInput={event => changeQuery(event.currentTarget.value)} />
        <button type="submit" disabled={disabled || loading || !query.trim()}>搜索</button>
        <button type="button" className="is-secondary" onClick={onClose}>取消</button>
      </div>
    </form>
    {loading && <p role="status">正在搜索…</p>}
    {error && <p role="alert">{error}</p>}
    {result && !candidates.length && <p role="status">未找到可添加的成员</p>}
    {result?.truncated && <p role="status">匹配较多，请输入更完整的花名或姓名。</p>}
    <div className="administrator-results">
      {candidates.map(user => <article key={user.internal_user_id}>
        <div><strong>{user.display_name}</strong>{user.departments.length > 0 && <p>{user.departments.join("、")}</p>}
          {ambiguous(user) && <small>同名且部门相同，请先核实通讯录身份。</small>}
        </div>
        <button type="button" disabled={disabled || !!result?.truncated || ambiguous(user)} onClick={() => onSelect(user)}>设为平台管理员</button>
      </article>)}
    </div>
  </section>;
}
