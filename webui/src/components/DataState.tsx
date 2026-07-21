export function LoadingState({ label = "Loading data" }: { label?: string }) {
  return <section className="data-state" aria-live="polite"><span className="empty-pulse" /><h2>{label}</h2></section>;
}


export function ErrorState({ onRetry }: { onRetry?: () => void }) {
  return <section className="data-state data-error" role="alert"><strong>Data unavailable</strong><p>The Platform could not read this view. Existing Agent services are not affected.</p>{onRetry && <button onClick={onRetry}>Retry</button>}</section>;
}


export function EmptyState({ title, description }: { title: string; description: string }) {
  return <section className="data-state"><span className="empty-symbol">00</span><h2>{title}</h2><p>{description}</p></section>;
}
