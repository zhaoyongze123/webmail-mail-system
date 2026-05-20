import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchAdminDashboardTrends, fetchAdminOverview } from '../api';

function MiniSparkline({ values }: { values: number[] }) {
  const width = 240;
  const height = 72;
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const points = values.map((value, index) => {
    const x = values.length <= 1 ? 0 : (width / Math.max(values.length - 1, 1)) * index;
    const y = height - ((value - min) / (max - min || 1)) * height;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="admin-sparkline" role="img" aria-label="趋势图">
      <polyline
        fill="none"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
        strokeLinejoin="round"
        points={points}
      />
    </svg>
  );
}

function formatNumber(value?: number | null) {
  if (value === undefined || value === null) return '—';
  return new Intl.NumberFormat('zh-CN').format(value);
}

export function AdminDashboardPage() {
  const overviewQuery = useQuery({
    queryKey: ['admin-overview'],
    queryFn: fetchAdminOverview,
  });
  const trendsQuery = useQuery({
    queryKey: ['admin-dashboard-trends', '7d'],
    queryFn: () => fetchAdminDashboardTrends('7d'),
  });

  const overview = overviewQuery.data;
  const trends = trendsQuery.data;
  const trendValues = useMemo(() => trends?.points.map((point) => point.audit_count) ?? [], [trends?.points]);
  const onlineCount = overview?.online_users?.online_user_count ?? overview?.online_users?.count ?? 0;
  const queueSummary = overview?.queue_summary ?? overview?.summary ?? {};
  const recentAudits = overview?.recent_audits ?? [];

  return (
    <div className="admin-section-stack">
      <section className="admin-stats-grid">
        <article className="admin-stat-card">
          <span>活跃用户</span>
          <strong>{formatNumber(overview?.active_users)}</strong>
        </article>
        <article className="admin-stat-card">
          <span>在线用户</span>
          <strong>{formatNumber(onlineCount)}</strong>
        </article>
        <article className="admin-stat-card">
          <span>邮件域名</span>
          <strong>{formatNumber(overview?.mail_domains)}</strong>
        </article>
        <article className="admin-stat-card">
          <span>队列总数</span>
          <strong>{formatNumber(queueSummary.total ?? overview?.queued_jobs)}</strong>
        </article>
      </section>

      <section className="admin-dashboard-grid">
        <div className="admin-form-card">
          <div className="admin-form-card__header">
            <div>
              <h2>最近审计</h2>
              <p>展示最近的后台操作快照。</p>
            </div>
          </div>
          <div className="admin-timeline-list">
            {recentAudits.length ? recentAudits.slice(0, 5).map((item) => (
              <article key={item.id} className="admin-timeline-item">
                <strong>{item.action}</strong>
                <p>{item.actor} · {item.target}</p>
                <span>{new Date(item.created_at).toLocaleString('zh-CN', { hour12: false })}</span>
              </article>
            )) : <p className="admin-empty-text">暂无最近审计</p>}
          </div>
        </div>

        <div className="admin-form-card">
          <div className="admin-form-card__header">
            <div>
              <h2>队列摘要</h2>
              <p>展示当前关键队列状态。</p>
            </div>
          </div>
          <div className="admin-info-grid">
            <div className="admin-info-card">
              <strong>deferred</strong>
              <p>{formatNumber(queueSummary.deferred ?? 0)}</p>
            </div>
            <div className="admin-info-card">
              <strong>active</strong>
              <p>{formatNumber(queueSummary.active ?? 0)}</p>
            </div>
            <div className="admin-info-card">
              <strong>hold</strong>
              <p>{formatNumber(queueSummary.hold ?? 0)}</p>
            </div>
            <div className="admin-info-card">
              <strong>queued</strong>
              <p>{formatNumber(queueSummary.queued ?? 0)}</p>
            </div>
          </div>
          <p className="admin-page-meta">{overview?.online_users?.detail || '等待在线用户数据'}</p>
        </div>
      </section>

      <section className="admin-form-card">
        <div className="admin-form-card__header">
          <div>
            <h2>趋势</h2>
            <p>按天查看审计、发送和后台动作变化。</p>
          </div>
        </div>
        <div className="admin-trend-panel">
          <MiniSparkline values={trendValues} />
          <div className="admin-trend-legend">
            {trends?.points.slice(-5).map((point) => (
              <div key={point.date} className="admin-trend-item">
                <strong>{point.date}</strong>
                <span>审计 {point.audit_count} · 发送 {point.sent_count} · 动作 {point.admin_action_count}</span>
              </div>
            )) ?? <p className="admin-empty-text">暂无趋势数据</p>}
          </div>
        </div>
      </section>
    </div>
  );
}
