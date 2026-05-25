import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { fetchAdminActionHistory } from '../api';
import { AdminListTable } from '../components/AdminListTable';
import { ResultMessage, SectionCard, StatusPill, formatAdminActorText, formatAdminTokenizedText } from '../components/AdminHelpers';
import type { AdminActionHistoryItem } from '../types';

function formatDate(value: string) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—';
}

function actionStatusLabel(status: string) {
  if (status === 'ok') return '成功';
  if (status === 'warning') return '告警';
  if (status === 'error') return '失败';
  if (status === 'unavailable') return '不可用';
  return status;
}

export function AdminActionHistoryPage() {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState('');
  const [actionType, setActionType] = useState('');
  const [targetType, setTargetType] = useState('');
  const [status, setStatus] = useState('');
  const [page, setPage] = useState(1);
  const [detailQuery, setDetailQuery] = useState('');

  const queryParams = useMemo(() => ({
    page,
    page_size: 20,
    action_type: actionType || undefined,
    target_type: targetType || undefined,
    status: status || undefined,
  }), [actionType, page, status, targetType]);

  const { data, isLoading, error } = useQuery({
    queryKey: ['admin-action-history', queryParams],
    queryFn: () => fetchAdminActionHistory(queryParams),
  });

  const items = useMemo(() => {
    const normalized = detailQuery.trim().toLowerCase();
    if (!normalized) return data?.items ?? [];
    return (data?.items ?? []).filter((item) => [item.actor, item.action, item.target, item.detail ?? '', item.status].join(' ').toLowerCase().includes(normalized));
  }, [data?.items, detailQuery]);

  const columns = useMemo<ColumnDef<AdminActionHistoryItem>[]>(() => [
    { accessorKey: 'created_at', header: '时间', cell: (info) => formatDate(String(info.getValue())) },
    { accessorKey: 'actor', header: '操作者', cell: (info) => formatAdminActorText(String(info.getValue() || '')) },
    { accessorKey: 'action', header: '动作', cell: (info) => formatAdminTokenizedText(String(info.getValue() || '')) },
    { accessorKey: 'target', header: '目标', cell: (info) => formatAdminTokenizedText(String(info.getValue() || '')) },
    { accessorKey: 'status', header: '状态', cell: (info) => <StatusPill status={String(info.getValue())} label={actionStatusLabel(String(info.getValue()))} /> },
    { accessorKey: 'detail', header: '详情', cell: (info) => info.getValue<string>() || '—' },
  ], []);

  return (
    <div className="admin-section-stack">
      <SectionCard
        title="操作历史筛选"
        description="展示后台关键动作、状态与执行详情。"
        actions={(
          <div className="admin-inline-actions">
            <button
              type="button"
              className="admin-button admin-button-secondary"
              onClick={() => void queryClient.invalidateQueries({ queryKey: ['admin-action-history'] })}
            >
              刷新
            </button>
          </div>
        )}
      >
        <div className="admin-info-grid">
          <div className="admin-info-card">
            <strong>总记录</strong>
            <p>{data?.total ?? 0}</p>
          </div>
          <div className="admin-info-card">
            <strong>当前页</strong>
            <p>{data?.page ?? page}</p>
          </div>
          <div className="admin-info-card">
            <strong>总页数</strong>
            <p>{data?.total_pages ?? 0}</p>
          </div>
          <div className="admin-info-card">
            <strong>页大小</strong>
            <p>{data?.page_size ?? 20}</p>
          </div>
        </div>
        {error ? <ResultMessage error={(error as Error).message} /> : null}
        <div className="admin-toolbar-grid admin-toolbar-grid--audit">
          <label>
            <span>关键字</span>
            <input value={detailQuery} onChange={(event) => setDetailQuery(event.target.value)} placeholder="动作 / 目标 / 详情" />
          </label>
          <label>
            <span>动作类型</span>
            <input value={actionType} onChange={(event) => { setActionType(event.target.value); setPage(1); }} placeholder="例如：邮件系统备份" />
          </label>
          <label>
            <span>目标类型</span>
            <input value={targetType} onChange={(event) => { setTargetType(event.target.value); setPage(1); }} placeholder="例如：服务 / 配置文件" />
          </label>
          <label>
            <span>状态</span>
            <select value={status} onChange={(event) => { setStatus(event.target.value); setPage(1); }}>
              <option value="">全部</option>
              <option value="ok">成功</option>
              <option value="warning">告警</option>
              <option value="error">失败</option>
              <option value="unavailable">不可用</option>
            </select>
          </label>
        </div>
      </SectionCard>

        <AdminListTable
        data={items}
        emptyMessage={isLoading ? '加载中...' : '暂无操作历史'}
        columns={columns}
        pagination={data ? {
          page: data.page,
          page_size: data.page_size,
          total: data.total,
          total_pages: data.total_pages,
          onPageChange: setPage,
        } : undefined}
      />
    </div>
  );
}
