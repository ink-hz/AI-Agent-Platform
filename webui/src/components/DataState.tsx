export function LoadingState({ label = "正在加载数据" }: { label?: string }) {
  return <section className="data-state" aria-live="polite"><span className="empty-pulse" /><h2>{label}</h2></section>;
}


export function ErrorState({ onRetry }: { onRetry?: () => void }) {
  return <section className="data-state data-error" role="alert"><strong>数据暂不可用</strong><p>Platform 暂时无法读取当前页面，Agent 服务不受影响。</p>{onRetry && <button onClick={onRetry}>重试</button>}</section>;
}


export function EmptyState({ title, description }: { title: string; description: string }) {
  return <section className="data-state"><span className="empty-symbol">00</span><h2>{title}</h2><p>{description}</p></section>;
}
