import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { exportAdminLogs, fetchAdminLogs } from '../api';
import { ResultMessage, SectionCard, StatusPill } from '../components/AdminHelpers';
import { AdminListTable } from '../components/AdminListTable';
import type { AdminLogEntry } from '../types';

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

export function AdminLogsPage() {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState('');
  const [source, setSource] = useState('');
  const [level, setLevel] = useState('');
  const [sender, setSender] = useState('');
  const [recipient, setRecipient] = useState('');
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [success, setSuccess] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['admin-logs', query, source, level, sender, recipient],
    queryFn: () => fetchAdminLogs({
      q: query,
      status: level,
      domain_id: source,
      sender: sender || undefined,
      recipient: recipient || undefined,
    }),
    refetchInterval: autoRefresh ? 15000 : false,
  });

  const exportMutation = useMutation({
    mutationFn: () => exportAdminLogs({ log_key: source || undefined, q: query || undefined, status: level || undefined, sender: sender || undefined, recipient: recipient || undefined, format: 'csv' }),
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

  const columns = useMemo<ColumnDef<AdminLogEntry>[]>(() => [
    { accessorKey: 'created_at', header: '时间', cell: (info) => formatDate(String(info.getValue())) },
    { accessorKey: 'source', header: '来源' },
    { accessorKey: 'level', header: '级别', cell: (info) => <StatusPill status={String(info.getValue())} /> },
    { accessorKey: 'message', header: '消息' },
    { accessorKey: 'actor', header: '操作者', cell: (info) => info.getValue<string>() || '—' },
    { accessorKey: 'target', header: '目标', cell: (info) => info.getValue<string>() || '—' },
  ], []);

  const summary = useMemo(() => ({
    total: data?.total ?? 0,
    updatedAt: data?.updated_at ?? '—',
  }), [data?.total, data?.updated_at]);

  return (
    <div className="admin-section-stack">
      <SectionCard
        title="日志筛选"
        description="直接调用后端日志搜索与导出接口，支持来源、状态、发件人、收件人和自动刷新。"
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
              {exportMutation.isPending ? '导出中...' : '导出 CSV'}
            </button>
          </div>
        )}
      >
        <div className="admin-info-grid">
          <div className="admin-info-card">
            <strong>日志总数</strong>
            <p>{summary.total}</p>
          </div>
          <div className="admin-info-card">
            <strong>更新时间</strong>
            <p>{summary.updatedAt}</p>
          </div>
        </div>
        <ResultMessage error={error} success={success} />
        <div className="admin-toolbar-grid admin-toolbar-grid--logs">
          <label>
            <span>关键字</span>
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索消息内容" />
          </label>
          <label>
            <span>来源</span>
            <input value={source} onChange={(event) => setSource(event.target.value)} placeholder="例如 postfix / audit" />
          </label>
          <label>
            <span>状态</span>
            <select value={level} onChange={(event) => setLevel(event.target.value)}>
              <option value="">全部</option>
              <option value="info">info</option>
              <option value="warning">warning</option>
              <option value="error">error</option>
            </select>
          </label>
          <label>
            <span>发件人</span>
            <input value={sender} onChange={(event) => setSender(event.target.value)} placeholder="sender@example.com" />
          </label>
          <label>
            <span>收件人</span>
            <input value={recipient} onChange={(event) => setRecipient(event.target.value)} placeholder="target@example.com" />
          </label>
        </div>
      </SectionCard>

      <AdminListTable
        data={data?.items ?? []}
        emptyMessage={isLoading ? '加载中...' : '暂无日志'}
        columns={columns}
      />
    </div>
  );
}
