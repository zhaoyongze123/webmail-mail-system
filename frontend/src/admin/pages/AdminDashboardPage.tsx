import { useId, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchAdminDashboardTrends, fetchAdminOverview } from '../api';

function MiniSparkline({ values }: { values: number[] }) {
  const gradientId = useId().replace(/:/g, '');
  const width = 360;
  const height = 168;
  const paddingX = 10;
  const paddingY = 18;
  const normalizedValues = values.length ? values : [0, 0, 0, 0, 0, 0, 0];
  const max = Math.max(...normalizedValues);
  const min = Math.min(...normalizedValues);
  const isFlat = max === min;
  const innerHeight = height - paddingY * 2;
  const baseline = height - paddingY;
  const points = normalizedValues.map((value, index) => {
    const x = normalizedValues.length <= 1
      ? width / 2
      : paddingX + ((width - paddingX * 2) / Math.max(normalizedValues.length - 1, 1)) * index;
    const y = isFlat
      ? paddingY + innerHeight * 0.58
      : paddingY + ((max - value) / Math.max(max - min, 1)) * innerHeight;
    return { x, y, value };
  });
  const linePath = points.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(1)} ${point.y.toFixed(1)}`).join(' ');
  const areaPath = `${linePath} L ${points[points.length - 1]?.x.toFixed(1) ?? width - paddingX} ${baseline} L ${points[0]?.x.toFixed(1) ?? paddingX} ${baseline} Z`;
  const lastPoint = points[points.length - 1];

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="admin-sparkline" role="img" aria-label="趋势图">
      <defs>
        <linearGradient id={`${gradientId}-fill`} x1="0%" y1="0%" x2="0%" y2="100%">
          <stop offset="0%" stopColor="currentColor" stopOpacity="0.28" />
          <stop offset="100%" stopColor="currentColor" stopOpacity="0.02" />
        </linearGradient>
      </defs>
      {[0, 1, 2, 3].map((step) => {
        const y = paddingY + (innerHeight / 3) * step;
        return <line key={step} x1={paddingX} x2={width - paddingX} y1={y} y2={y} className="admin-sparkline__grid" />;
      })}
      <path d={areaPath} fill={`url(#${gradientId}-fill)`} />
      <path d={linePath} className="admin-sparkline__line" />
      {lastPoint ? (
        <>
          <circle cx={lastPoint.x} cy={lastPoint.y} r="7" className="admin-sparkline__halo" />
          <circle cx={lastPoint.x} cy={lastPoint.y} r="4.5" className="admin-sparkline__dot" />
        </>
      ) : null}
    </svg>
  );
}

function formatNumber(value?: number | null) {
  if (value === undefined || value === null) return '—';
  return new Intl.NumberFormat('zh-CN').format(value);
}

const auditTokenLabels: Record<string, string> = {
  admin: '后台',
  queue: '队列',
  tls: 'TLS',
  rspamd: '反垃圾',
  users: '用户',
  user: '用户',
  domains: '域名',
  aliases: '别名',
  quotas: '配额',
  audit: '审计',
  logs: '日志',
  mail: '邮件',
  mail_system: '系统',
  list: '列表',
  overview: '概览',
  create: '创建',
  update: '更新',
  delete: '删除',
  configs: '配置',
  config: '配置',
  history: '历史',
  reset_password: '重置密码',
};

function formatAuditAction(action?: string | null) {
  if (!action) return '未命名操作';
  return action
    .split('.')
    .filter(Boolean)
    .map((token) => auditTokenLabels[token] || token.replace(/_/g, ' '))
    .join(' · ');
}

function compactValue(value?: string | null, leading = 12, trailing = 6) {
  if (!value) return '未指定';
  if (value.length <= leading + trailing + 3) return value;
  return `${value.slice(0, leading)}...${value.slice(-trailing)}`;
}

function formatAuditActor(actor?: string | null) {
  if (!actor) return '未知操作者';
  const uuidLike = /^[0-9a-f]{8}-[0-9a-f-]{27}$/i.test(actor);
  return uuidLike ? '管理员' : actor;
}

function formatAuditTarget(target?: string | null) {
  if (!target) return '未关联目标';
  return compactValue(target.replace(/_/g, ' '), 18, 8);
}

function formatTrendDateLabel(value: string) {
  const segments = value.split('-');
  if (segments.length !== 3) return value;
  return `${Number(segments[1])}/${Number(segments[2])}`;
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
  const trendPoints = trends?.points ?? [];
  const trendValues = useMemo(() => trendPoints.map((point) => point.audit_count), [trendPoints]);
  const trendSummary = useMemo(() => {
    const totalAudit = trendPoints.reduce((sum, point) => sum + point.audit_count, 0);
    const totalSent = trendPoints.reduce((sum, point) => sum + point.sent_count, 0);
    const totalActions = trendPoints.reduce((sum, point) => sum + point.admin_action_count, 0);
    const peakAudit = trendPoints.reduce((peak, point) => Math.max(peak, point.audit_count), 0);
    const latest = trendPoints[trendPoints.length - 1];
    return {
      totalAudit,
      totalSent,
      totalActions,
      peakAudit,
      latestLabel: latest ? formatTrendDateLabel(latest.date) : '最近 7 天',
    };
  }, [trendPoints]);
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
        <article className="admin-dashboard-card admin-dashboard-card--audit">
          <div className="admin-dashboard-card__header">
            <div>
              <h2>最近审计</h2>
              <p>保留最近的后台动作脉络，便于快速追踪。</p>
            </div>
            <span className="admin-dashboard-card__eyebrow">最近 5 条</span>
          </div>
          <div className="admin-audit-stream">
            {recentAudits.length ? recentAudits.slice(0, 5).map((item) => (
              <article key={item.id} className="admin-audit-stream__item">
                <div className="admin-audit-stream__headline">
                  <strong className="admin-audit-stream__action">{formatAuditAction(item.action)}</strong>
                  <time className="admin-audit-stream__time">
                    {new Date(item.created_at).toLocaleString('zh-CN', {
                      hour12: false,
                      month: 'numeric',
                      day: 'numeric',
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </time>
                </div>
                <p className="admin-audit-stream__target" title={item.target}>{formatAuditTarget(item.target)}</p>
                <div className="admin-audit-stream__meta">
                  <span>{formatAuditActor(item.actor)}</span>
                  <span className="admin-audit-stream__id">#{compactValue(item.id, 8, 4)}</span>
                </div>
              </article>
            )) : <p className="admin-empty-text">暂无最近审计</p>}
          </div>
        </article>

        <article className="admin-dashboard-card admin-dashboard-card--trend">
          <div className="admin-dashboard-card__header">
            <div>
              <h2>趋势</h2>
              <p>只保留关键波动，避免把列表说明再次堆满。</p>
            </div>
            <span className="admin-dashboard-card__eyebrow">{trendSummary.latestLabel}</span>
          </div>
          <div className="admin-trend-panel">
            <div className="admin-trend-summary admin-trend-summary--row">
              <div className="admin-trend-metric">
                <span>审计总量</span>
                <strong>{formatNumber(trendSummary.totalAudit)}</strong>
              </div>
              <div className="admin-trend-metric">
                <span>发送总量</span>
                <strong>{formatNumber(trendSummary.totalSent)}</strong>
              </div>
              <div className="admin-trend-metric">
                <span>峰值</span>
                <strong>{formatNumber(trendSummary.peakAudit)}</strong>
              </div>
            </div>
            <div className="admin-trend-chart-card">
              <MiniSparkline values={trendValues} />
              <div className="admin-trend-axis">
                {trendPoints.length ? trendPoints.map((point) => (
                  <span key={point.date}>{formatTrendDateLabel(point.date)}</span>
                )) : <span>暂无趋势数据</span>}
              </div>
              <div className="admin-trend-footnote">
                <span>后台动作 {formatNumber(trendSummary.totalActions)}</span>
                <span>最近 7 天审计波动</span>
              </div>
            </div>
          </div>
        </article>
      </section>

      <section className="admin-dashboard-card admin-dashboard-card--queue">
        <div className="admin-dashboard-card__header">
          <div>
            <h2>队列摘要</h2>
            <p>保留必要状态，不再把大段说明塞进主视区。</p>
          </div>
          <span className="admin-dashboard-card__eyebrow">mail queue</span>
        </div>
        <div className="admin-queue-summary">
          <div className="admin-queue-summary__item">
            <span>deferred</span>
            <strong>{formatNumber(queueSummary.deferred ?? 0)}</strong>
          </div>
          <div className="admin-queue-summary__item">
            <span>active</span>
            <strong>{formatNumber(queueSummary.active ?? 0)}</strong>
          </div>
          <div className="admin-queue-summary__item">
            <span>hold</span>
            <strong>{formatNumber(queueSummary.hold ?? 0)}</strong>
          </div>
          <div className="admin-queue-summary__item">
            <span>queued</span>
            <strong>{formatNumber(queueSummary.queued ?? 0)}</strong>
          </div>
        </div>
        <p className="admin-page-meta">{overview?.online_users?.detail || '等待在线用户数据'}</p>
      </section>

      <section className="admin-dashboard-card admin-dashboard-card--trend-detail">
        <div className="admin-dashboard-card__header">
          <div>
            <h2>最近 5 天明细</h2>
            <p>把日粒度变化压成简洁列表，替代原来那种又挤又散的趋势文本。</p>
          </div>
        </div>
        <div className="admin-trend-list">
          {trendPoints.length ? trendPoints.slice(-5).map((point) => (
            <div key={point.date} className="admin-trend-list__item">
              <strong>{formatTrendDateLabel(point.date)}</strong>
              <span>审计 {formatNumber(point.audit_count)}</span>
              <span>发送 {formatNumber(point.sent_count)}</span>
              <span>动作 {formatNumber(point.admin_action_count)}</span>
            </div>
          )) : <p className="admin-empty-text">暂无趋势数据</p>}
        </div>
      </section>
    </div>
  );
}
