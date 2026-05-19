import { useQuery } from '@tanstack/react-query';
import { fetchAdminOverview } from '../api';

const stats = [
  { key: 'active_users', label: '活跃用户' },
  { key: 'mail_domains', label: '域名' },
  { key: 'aliases', label: '别名' },
  { key: 'queued_jobs', label: '待处理任务' },
] as const;

export function AdminDashboardPage() {
  const { data } = useQuery({
    queryKey: ['admin-overview'],
    queryFn: fetchAdminOverview,
  });

  return (
    <div className="admin-stats-grid">
      {stats.map((item) => (
        <article key={item.key} className="admin-stat-card">
          <span>{item.label}</span>
          <strong>{data?.[item.key] ?? '—'}</strong>
        </article>
      ))}
    </div>
  );
}
