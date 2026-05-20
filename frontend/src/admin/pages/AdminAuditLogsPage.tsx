import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { exportAdminAuditLogs, fetchAdminAuditLogsPage } from '../api';
import { AdminListTable } from '../components/AdminListTable';
import { ResultMessage, SectionCard, StatusPill } from '../components/AdminHelpers';
import type { AdminAuditLogItem } from '../types';

function formatDate(value: string) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—';
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

export function AdminAuditLogsPage() {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState('');
  const [actor, setActor] = useState('');
  const [action, setAction] = useState('');
  const [target, setTarget] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [successOnly, setSuccessOnly] = useState('');
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [success, setSuccess] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const queryParams = {
    q: query || undefined,
    actor_id: actor || undefined,
    action: action || undefined,
    target: target || undefined,
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    success_only: successOnly === '' ? undefined : successOnly === 'true',
  };

  const { data } = useQuery({
    queryKey: ['admin-audit-logs', queryParams],
    queryFn: () => fetchAdminAuditLogsPage(queryParams),
    refetchInterval: autoRefresh ? 20000 : false,
  });

  const exportMutation = useMutation({
    mutationFn: () => exportAdminAuditLogs({ q: query || undefined, status: successOnly === '' ? undefined : successOnly, format: 'csv' }),
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

  const items = data?.items ?? [];
  const summary = useMemo(() => ({
    total: data?.total ?? 0,
    updatedAt: items.length ? items[0].created_at : '—',
  }), [data?.total, items]);

  const columns = useMemo<ColumnDef<AdminAuditLogItem>[]>(() => [
    { accessorKey: 'actor', header: '操作者' },
    { accessorKey: 'action', header: '动作' },
    { accessorKey: 'target', header: '目标' },
    {
      accessorKey: 'event_type',
      header: '事件类型',
      cell: (info) => <StatusPill status={String(info.getValue() || '—')} />,
    },
    { accessorKey: 'created_at', header: '时间', cell: (info) => formatDate(String(info.getValue())) },
  ], []);

  return (
    <div className="admin-section-stack">
      <SectionCard
        title="审计筛选"
        description="后端筛选与导出都基于真实接口，支持时间、操作者、动作、目标和成功状态过滤。"
        actions={(
          <div className="admin-inline-actions">
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
              onClick={() => queryClient.invalidateQueries({ queryKey: ['admin-audit-logs'] })}
            >
              刷新
            </button>
            <button
              type="button"
              className="admin-button admin-button-primary"
              disabled={exportMutation.isPending}
              onClick={() => exportMutation.mutate()}
            >
              {exportMutation.isPending ? '导出中...' : '导出 CSV'}
            </button>
          </div>
        )}
      >
        <div className="admin-info-grid">
          <div className="admin-info-card">
            <strong>审计总数</strong>
            <p>{summary.total}</p>
          </div>
          <div className="admin-info-card">
            <strong>最新时间</strong>
            <p>{summary.updatedAt}</p>
          </div>
        </div>
        <ResultMessage error={error} success={success} />
        <div className="admin-toolbar-grid admin-toolbar-grid--audit">
          <label>
            <span>关键字</span>
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="操作者 / 动作 / 目标" />
          </label>
          <label>
            <span>操作者</span>
            <input value={actor} onChange={(event) => setActor(event.target.value)} placeholder="例如 admin" />
          </label>
          <label>
            <span>动作</span>
            <input value={action} onChange={(event) => setAction(event.target.value)} placeholder="例如 login" />
          </label>
          <label>
            <span>目标</span>
            <input value={target} onChange={(event) => setTarget(event.target.value)} placeholder="例如 user-1" />
          </label>
          <label>
            <span>开始日期</span>
            <input type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} />
          </label>
          <label>
            <span>结束日期</span>
            <input type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} />
          </label>
          <label>
            <span>成功状态</span>
            <select value={successOnly} onChange={(event) => setSuccessOnly(event.target.value)}>
              <option value="">全部</option>
              <option value="true">成功</option>
              <option value="false">失败</option>
            </select>
          </label>
        </div>
      </SectionCard>

      <AdminListTable
        data={items}
        emptyMessage="暂无符合条件的审计日志"
        columns={columns}
      />
    </div>
  );
}
