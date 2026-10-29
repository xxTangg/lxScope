import type { ReactNode } from 'react';
import { AlertTriangle, ChevronRight } from 'lucide-react';

export function PageHeading({
  eyebrow,
  title,
  subtitle,
  children,
}: {
  eyebrow: string;
  title: string;
  subtitle: string;
  children?: ReactNode;
}) {
  return (
    <header className="page-heading">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        <p>{subtitle}</p>
      </div>
      <div className="heading-actions">{children}</div>
    </header>
  );
}

export function PageTabs<T extends string>({
  label,
  tabs,
  value,
  onChange,
}: {
  label: string;
  tabs: Array<{ id: T; label: string; count?: number }>;
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div className="page-tabs" role="tablist" aria-label={label}>
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          aria-selected={value === tab.id}
          className={`page-tab ${value === tab.id ? 'active' : ''}`}
          onClick={() => onChange(tab.id)}
        >
          {tab.label}
          {tab.count !== undefined && <span className="tab-count">{tab.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function Panel({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
}) {
  return (
    <section className="panel">
      <header>
        <div>
          <h2>{title}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
      </header>
      {children}
    </section>
  );
}

export function ActionRow({
  icon,
  title,
  detail,
  onClick,
}: {
  icon: ReactNode;
  title: string;
  detail: string;
  onClick: () => void;
}) {
  return (
    <button className="action-row" onClick={onClick}>
      <span className="action-icon">{icon}</span>
      <span>
        <b>{title}</b>
        <small>{detail}</small>
      </span>
      <ChevronRight size={16} />
    </button>
  );
}

export function Tag({ text, good = false }: { text: string; good?: boolean }) {
  return (
    <span className={`tag ${good ? 'tag-good' : ''}`}>
      {good && <i />}
      {text}
    </span>
  );
}

export function Loading() {
  return (
    <div className="loading-state">
      <div className="spinner" />
      正在读取运营数据…
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  detail,
}: {
  icon: ReactNode;
  title: string;
  detail: string;
}) {
  return (
    <div className="empty-state">
      <span>{icon}</span>
      <b>{title}</b>
      <p>{detail}</p>
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="empty-state">
      <span>
        <AlertTriangle size={20} />
      </span>
      <b>读取失败</b>
      <p>{message}</p>
      <button className="secondary" onClick={onRetry}>
        重新加载
      </button>
    </div>
  );
}
