import { useEffect, useMemo } from 'react';
import type { ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';

export function StatusPill({ status }: { status: string }) {
  const className =
    status === 'active' || status === 'healthy' || status === 'ok'
      ? 'is-success'
      : status === 'warning'
        ? 'is-warning'
        : 'is-working';
  return <span className={`admin-status-pill ${className}`}>{status}</span>;
}

export function ResultMessage({ error, success }: { error?: string | null; success?: string | null }) {
  if (error) {
    return <p className="admin-error-text">{error}</p>;
  }
  if (success) {
    return <p className="admin-success-text">{success}</p>;
  }
  return null;
}

export function SectionCard({ title, description, children, actions }: { title: string; description?: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="admin-form-card">
      <div className="admin-form-card__header">
        <div>
          <h2>{title}</h2>
          {description ? <p>{description}</p> : null}
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}

export function AdminDialog({
  open,
  title,
  description,
  children,
  actions,
  onClose,
}: {
  open: boolean;
  title: string;
  description?: string;
  children?: ReactNode;
  actions?: ReactNode;
  onClose: () => void;
}) {
  if (!open) {
    return null;
  }

  return (
    <div className="settings-modal-overlay" onClick={onClose}>
      <div className="settings-modal admin-dialog" role="dialog" aria-modal="true" aria-label={title} onClick={(event) => event.stopPropagation()}>
        <div className="admin-form-card__header">
          <div>
            <h2>{title}</h2>
            {description ? <p>{description}</p> : null}
          </div>
          <button type="button" className="admin-button admin-button-secondary" onClick={onClose}>
            关闭
          </button>
        </div>
        {children}
        {actions ? <div className="admin-inline-actions">{actions}</div> : null}
      </div>
    </div>
  );
}

export function useAdminListSearchParams(defaults: {
  q?: string;
  status?: string;
  domain_id?: string;
  page?: number;
}) {
  const [searchParams, setSearchParams] = useSearchParams();

  const state = useMemo(() => ({
    q: searchParams.get('q') ?? defaults.q ?? '',
    status: searchParams.get('status') ?? defaults.status ?? '',
    domain_id: searchParams.get('domain_id') ?? defaults.domain_id ?? '',
    page: Math.max(Number(searchParams.get('page') ?? defaults.page ?? 1), 1),
  }), [defaults.domain_id, defaults.page, defaults.q, defaults.status, searchParams]);

  useEffect(() => {
    const next = new URLSearchParams(searchParams);
    let changed = false;
    ([
      ['q', state.q],
      ['status', state.status],
      ['domain_id', state.domain_id],
      ['page', String(state.page)],
    ] as const).forEach(([key, value]) => {
      const normalizedValue = value || '';
      if ((next.get(key) ?? '') !== normalizedValue) {
        if (normalizedValue) {
          next.set(key, normalizedValue);
        } else {
          next.delete(key);
        }
        changed = true;
      }
    });
    if (changed) {
      setSearchParams(next, { replace: true });
    }
  }, [searchParams, setSearchParams, state.domain_id, state.page, state.q, state.status]);

  return {
    ...state,
    setQ(value: string) {
      const next = new URLSearchParams(searchParams);
      if (value) next.set('q', value);
      else next.delete('q');
      next.set('page', '1');
      setSearchParams(next, { replace: true });
    },
    setStatus(value: string) {
      const next = new URLSearchParams(searchParams);
      if (value) next.set('status', value);
      else next.delete('status');
      next.set('page', '1');
      setSearchParams(next, { replace: true });
    },
    setDomainId(value: string) {
      const next = new URLSearchParams(searchParams);
      if (value) next.set('domain_id', value);
      else next.delete('domain_id');
      next.set('page', '1');
      setSearchParams(next, { replace: true });
    },
    setPage(value: number) {
      const next = new URLSearchParams(searchParams);
      next.set('page', String(Math.max(value, 1)));
      setSearchParams(next, { replace: true });
    },
  };
}
