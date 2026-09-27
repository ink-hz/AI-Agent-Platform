import type { ReactNode } from 'react';
export function PlatformPageHeader({title, description, actions, className = ''}: {
  title: ReactNode; description?: ReactNode; actions?: ReactNode; className?: string;
}) {
  return <header className={`platform-page-header ${className}`}>
    <div className="platform-page-header-copy"><h1>{title}</h1>{description && <p>{description}</p>}</div>
    {actions && <div className="platform-page-header-actions">{actions}</div>}
  </header>;
}
