import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { exportAdminLogs, fetchAdminLogs } from '../api';
import { ResultMessage, SectionCard, StatusPill, formatAdminDateTime, useAdminListSearchParams } from '../components/AdminHelpers';
import type { AdminLogEntry, AdminLogEvent } from '../types';

type LogProtocol = 'smtp' | 'imap' | 'pop3';

function formatBytes(value: number) {
  if (!value) {
    return '—';
  }
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function logStatusLabel(status: string) {
  if (status === 'sent') return '已投递';
  if (status === 'deferred') return '延迟';
  if (status === 'bounced') return '退信';
  if (status === 'rejected') return '拒收';
  if (status === 'queued') return '已入队';
  if (status === 'accepted') return '已接收';
  if (status === 'removed') return '已移除';
  if (status === 'warning') return '告警';
  if (status === 'login') return '已登录';
  if (status === 'completed') return '已退出';
  if (status === 'failed') return '失败';
  if (status === 'disconnected') return '已断开';
  return '处理中';
}

function logStatusTone(status: string) {
  if (status === 'sent') return 'ok';
  if (status === 'completed' || status === 'login') return 'ok';
  if (status === 'deferred') return 'warning';
  if (status === 'warning') return 'warning';
  if (status === 'bounced' || status === 'rejected') return 'error';
  if (status === 'failed') return 'error';
  return 'info';
}

function protocolMeta(protocol: LogProtocol) {
  if (protocol === 'smtp') {
    return {
      title: 'SMTP 投递追踪',
      description: '按 Queue ID 聚合 Postfix 多行日志，一行代表一封邮件，便于按发件时间、收发件人和投递状态快速追踪。',
      exportLabel: '导出投递追踪',
      searchPlaceholder: '搜索发件人 / 收件人 / Queue ID',
      identifierLabel: 'Queue ID',
      actorLabel: '发件人',
      targetLabel: '收件人',
      totalLabel: '总邮件数',
      summaryKeys: ['sent', 'deferred', 'bounced'] as const,
      summaryLabels: ['已发送', '延迟', '退信'] as const,
    };
  }
  return {
    title: protocol === 'imap' ? 'IMAP 会话追踪' : 'POP3 会话追踪',
    description: `按 ${protocol.toUpperCase()} session 聚合 Dovecot 多行日志，一行代表一次登录会话，便于排查登录、断开和收信操作。`,
    exportLabel: `导出${protocol.toUpperCase()}会话`,
    searchPlaceholder: '搜索账号 / 会话 ID / 客户端 IP',
    identifierLabel: '会话 ID',
    actorLabel: '账号',
    targetLabel: '客户端',
    totalLabel: '总会话数',
    summaryKeys: ['completed', 'failed', 'login'] as const,
    summaryLabels: ['已退出', '失败', '已登录'] as const,
  };
}

function downloadText(filename: string, content: string, mediaType: string) {
  const blob = new Blob([content], { type: mediaType });
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  window.URL.revokeObjectURL(url);
}

function LogTimeline({ events }: { events: AdminLogEvent[] }) {
  if (!events.length) {
    return <p className="admin-empty-text">暂无追踪事件</p>;
  }
  return (
    <div className="admin-log-timeline">
      {events.map((event, index) => (
        <div key={`${event.time || 'na'}-${index}`} className="admin-log-timeline__item">
          <div className="admin-log-timeline__meta">
            <span>{formatAdminDateTime(event.time)}</span>
            <StatusPill status={logStatusTone(event.status)} label={logStatusLabel(event.status)} />
          </div>
          <strong>{event.summary}</strong>
          <div className="admin-log-timeline__grid">
            <span>收件人：{event.recipient || '—'}</span>
            <span>投递点：{event.relay || '—'}</span>
            <span>延迟：{event.delay || '—'}</span>
            <span>DSN：{event.dsn || '—'}</span>
          </div>
          <pre className="admin-log-output">{event.raw}</pre>
        </div>
      ))}
    </div>
  );
}

function LogTraceRow({
  item,
  expanded,
  onToggle,
  protocol,
}: {
  item: AdminLogEntry;
  expanded: boolean;
  onToggle: () => void;
  protocol: LogProtocol;
}) {
  const meta = protocolMeta(protocol);
  const identifier = protocol === 'smtp' ? item.queue_id : item.queue_id;
  const actor = protocol === 'smtp' ? item.sender || '—' : (item.user || item.sender || '—');
  const target = protocol === 'smtp'
    ? (item.recipient || item.recipients.join(', ') || '—')
    : (item.client_ip || '—');
  return (
    <>
      <tr>
        <td>{formatAdminDateTime(item.created_at || item.updated_at)}</td>
        <td>
          <button type="button" className="admin-link-button" onClick={onToggle}>
            {identifier}
          </button>
        </td>
        <td>{actor}</td>
        <td title={protocol === 'smtp' ? item.recipients.join(', ') : target}>{target}</td>
        <td>{protocol === 'smtp' ? formatBytes(item.message_size) : `${item.event_count} 条事件`}</td>
        <td><StatusPill status={logStatusTone(item.status)} label={logStatusLabel(item.status)} /></td>
      </tr>
      {expanded ? (
        <tr className="admin-log-detail-row">
          <td colSpan={6}>
            <div className="admin-log-detail-card">
              <div className="admin-log-detail-card__meta">
                <span>最新状态：{logStatusLabel(item.status)}</span>
                <span>追踪事件：{item.event_count}</span>
                <span>最后更新时间：{formatAdminDateTime(item.updated_at || item.created_at)}</span>
                {protocol !== 'smtp' ? <span>认证方式：{item.auth_method || '—'}</span> : null}
              </div>
              <p className="admin-log-detail-card__summary">{item.status_detail || '暂无状态说明'}</p>
              <LogTimeline events={item.events} />
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
}

export function AdminLogsPage() {
  const queryClient = useQueryClient();
  const params = useAdminListSearchParams({ q: '', page: 1 });
  const [protocol, setProtocol] = useState<LogProtocol>('smtp');
  const [level, setLevel] = useState('');
  const [sender, setSender] = useState('');
  const [recipient, setRecipient] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const meta = protocolMeta(protocol);
  const queryParams = {
    page: params.page,
    page_size: 10,
    q: params.q || undefined,
    domain_id: protocol,
    status: level || undefined,
    sender: sender || undefined,
    recipient: recipient || undefined,
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
  };

  const { data, isLoading } = useQuery({
    queryKey: ['admin-logs', queryParams],
    queryFn: () => fetchAdminLogs(queryParams),
    refetchInterval: autoRefresh ? 15000 : false,
  });

  const exportMutation = useMutation({
    mutationFn: () => exportAdminLogs({ log_key: protocol, q: params.q || undefined, status: level || undefined, sender: sender || undefined, recipient: recipient || undefined, date_from: dateFrom || undefined, date_to: dateTo || undefined, format: 'csv' }),
    onSuccess: (payload) => {
      setError(null);
      setSuccess(`已导出 ${payload.filename}`);
      downloadText(payload.filename, payload.content, payload.media_type);
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const summary = data?.summary ?? {};
  const items = data?.items ?? [];

  return (
    <div className="admin-section-stack">
      <SectionCard
        title={meta.title}
        description={meta.description}
        actions={(
          <div className="admin-inline-actions">
            <label className="admin-filter-field">
              <span>协议</span>
              <select value={protocol} onChange={(event) => {
                setProtocol(event.target.value as LogProtocol);
                setExpandedId(null);
                setLevel('');
                params.setPage(1);
              }}>
                <option value="smtp">SMTP</option>
                <option value="imap">IMAP</option>
                <option value="pop3">POP3</option>
              </select>
            </label>
            <label className="admin-filter-field">
              <span>自动刷新</span>
              <select value={autoRefresh ? 'on' : 'off'} onChange={(event) => setAutoRefresh(event.target.value === 'on')}>
                <option value="on">开启</option>
                <option value="off">关闭</option>
              </select>
            </label>
            <button
              type="button"
              className="admin-button admin-button-secondary"
              onClick={() => queryClient.invalidateQueries({ queryKey: ['admin-logs'] })}
            >
              刷新
            </button>
            <button
              type="button"
              className="admin-button admin-button-primary"
              disabled={exportMutation.isPending}
              onClick={() => exportMutation.mutate()}
            >
              {exportMutation.isPending ? '导出中...' : meta.exportLabel}
            </button>
          </div>
        )}
      >
        <div className="admin-info-grid">
          <div className="admin-info-card">
            <strong>{meta.totalLabel}</strong>
            <p>{summary.total ?? 0}</p>
          </div>
          <div className="admin-info-card">
            <strong>{meta.summaryLabels[0]}</strong>
            <p>{summary[meta.summaryKeys[0]] ?? 0}</p>
          </div>
          <div className="admin-info-card">
            <strong>{meta.summaryLabels[1]}</strong>
            <p>{summary[meta.summaryKeys[1]] ?? 0}</p>
          </div>
          <div className="admin-info-card">
            <strong>{meta.summaryLabels[2]}</strong>
            <p>{summary[meta.summaryKeys[2]] ?? 0}</p>
          </div>
        </div>
        <ResultMessage error={error} success={success} />
        <div className="admin-toolbar-grid admin-toolbar-grid--logs">
          <label>
            <span>关键字</span>
            <input
              value={params.q}
              onChange={(event) => {
                params.setQ(event.target.value);
                params.setPage(1);
              }}
              placeholder={meta.searchPlaceholder}
            />
          </label>
          <label>
            <span>开始时间</span>
            <input type="datetime-local" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} />
          </label>
          <label>
            <span>结束时间</span>
            <input type="datetime-local" value={dateTo} onChange={(event) => setDateTo(event.target.value)} />
          </label>
          <label>
            <span>状态</span>
            <select value={level} onChange={(event) => setLevel(event.target.value)}>
              <option value="">全部状态</option>
              {protocol === 'smtp' ? (
                <>
                  <option value="sent">已投递</option>
                  <option value="deferred">延迟</option>
                  <option value="bounced">退信</option>
                  <option value="rejected">拒收</option>
                </>
              ) : (
                <>
                  <option value="login">已登录</option>
                  <option value="completed">已退出</option>
                  <option value="failed">失败</option>
                  <option value="warning">告警</option>
                </>
              )}
            </select>
          </label>
          <label>
            <span>{meta.actorLabel}</span>
            <input value={sender} onChange={(event) => setSender(event.target.value)} placeholder={protocol === 'smtp' ? 'sender@example.com' : 'user@example.com'} />
          </label>
          <label>
            <span>{meta.targetLabel}</span>
            <input value={recipient} onChange={(event) => setRecipient(event.target.value)} placeholder={protocol === 'smtp' ? 'target@example.com' : '192.0.2.10'} />
          </label>
        </div>
      </SectionCard>

      <div className="admin-table-panel">
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>时间</th>
                <th>{meta.identifierLabel}</th>
                <th>{meta.actorLabel}</th>
                <th>{meta.targetLabel}</th>
                <th>{protocol === 'smtp' ? '大小' : '事件数'}</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <tr>
                  <td colSpan={6}>{isLoading ? '加载中...' : '暂无数据，请导入日志或放宽筛选条件'}</td>
                </tr>
              ) : (
                items.map((item) => (
                  <LogTraceRow
                    key={item.id}
                    item={item}
                    expanded={expandedId === item.id}
                    protocol={protocol}
                    onToggle={() => setExpandedId((current) => (current === item.id ? null : item.id))}
                  />
                ))
              )}
            </tbody>
          </table>
        </div>
        {data ? (
          <div className="admin-pagination">
            <span>
              第 {data.page} / {Math.max(data.total_pages, 1)} 页，共 {data.total} 条
            </span>
            <div className="admin-inline-actions">
              <button
                type="button"
                className="admin-button admin-button-secondary"
                disabled={data.page <= 1}
                onClick={() => params.setPage(data.page - 1)}
              >
                上一页
              </button>
              <button
                type="button"
                className="admin-button admin-button-secondary"
                disabled={data.page >= data.total_pages}
                onClick={() => params.setPage(data.page + 1)}
              >
                下一页
              </button>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
