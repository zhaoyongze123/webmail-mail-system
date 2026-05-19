import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { deleteAdminQueueItem, fetchAdminQueue, flushAdminQueue } from '../api';
import { AdminDialog, ResultMessage, SectionCard, StatusPill } from '../components/AdminHelpers';
import { AdminListTable } from '../components/AdminListTable';
import type { AdminQueueItem } from '../types';

function formatBytes(bytes: number) {
  if (!bytes) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function formatTimestamp(timestamp: number) {
  if (!timestamp) return '—';
  return new Date(timestamp * 1000).toLocaleString('zh-CN', { hour12: false });
}

export function AdminQueuePage() {
  const queryClient = useQueryClient();
  const [deleteTarget, setDeleteTarget] = useState<AdminQueueItem | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['admin-queue'],
    queryFn: fetchAdminQueue,
  });

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['admin-queue'] });
    await queryClient.invalidateQueries({ queryKey: ['admin-overview'] });
  };

  const flushMutation = useMutation({
    mutationFn: flushAdminQueue,
    onSuccess: async (payload) => {
      setSuccess(payload.detail);
      setError(null);
      await refresh();
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: deleteAdminQueueItem,
    onSuccess: async (payload) => {
      setDeleteTarget(null);
      setSuccess(payload.detail);
      setError(null);
      await refresh();
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const columns = useMemo<ColumnDef<AdminQueueItem>[]>(() => [
    { accessorKey: 'queue_id', header: '队列 ID' },
    { accessorKey: 'status', header: '状态', cell: (info) => <StatusPill status={String(info.getValue())} /> },
    { accessorKey: 'sender', header: '发件人' },
    {
      accessorKey: 'recipients',
      header: '收件人',
      cell: ({ row }) => row.original.recipients.length ? row.original.recipients.join(', ') : '—',
    },
    { accessorKey: 'message_size', header: '大小', cell: (info) => formatBytes(Number(info.getValue()) || 0) },
    { accessorKey: 'arrival_time', header: '入队时间', cell: (info) => formatTimestamp(Number(info.getValue()) || 0) },
    {
      id: 'actions',
      header: '操作',
      cell: ({ row }) => (
        <button
          type="button"
          className="admin-button admin-button-danger"
          onClick={() => setDeleteTarget(row.original)}
        >
          删除
        </button>
      ),
    },
  ], []);

  const summaryItems = useMemo(() => {
    const summary = data?.summary || {};
    return [
      { key: 'total', label: '总队列数', value: summary.total ?? 0 },
      { key: 'active', label: 'active', value: summary.active ?? 0 },
      { key: 'deferred', label: 'deferred', value: summary.deferred ?? 0 },
      { key: 'hold', label: 'hold', value: summary.hold ?? 0 },
    ];
  }, [data?.summary]);

  return (
    <div className="admin-section-stack">
      <SectionCard
        title="队列摘要"
        description="基于 `postqueue -j`、`postqueue -f` 和 `postsuper -d` 的最小闭环。开发机缺少命令时会返回明确降级提示。"
        actions={(
          <div className="admin-inline-actions">
            <button type="button" className="admin-button admin-button-secondary" onClick={() => void refresh()}>
              刷新
            </button>
            <button
              type="button"
              className="admin-button admin-button-primary"
              disabled={flushMutation.isPending}
              onClick={() => flushMutation.mutate()}
            >
              {flushMutation.isPending ? '执行中...' : 'Flush 队列'}
            </button>
          </div>
        )}
      >
        <div className="admin-info-grid">
          {summaryItems.map((item) => (
            <div key={item.key} className="admin-info-card">
              <strong>{item.label}</strong>
              <p>{item.value}</p>
            </div>
          ))}
        </div>
        <ResultMessage error={error} success={success} />
        {data?.detail ? <p className="admin-mono">{data.detail}</p> : null}
        {data?.command_result?.stderr ? <p className="admin-mono">{data.command_result.stderr}</p> : null}
      </SectionCard>

      <AdminListTable
        data={data?.items ?? []}
        emptyMessage={isLoading ? '加载中...' : '暂无队列数据'}
        columns={columns}
      />

      <AdminDialog
        open={Boolean(deleteTarget)}
        title="确认删除队列邮件"
        description={deleteTarget ? `将删除队列邮件 ${deleteTarget.queue_id}，该操作不可恢复。` : undefined}
        onClose={() => setDeleteTarget(null)}
        actions={(
          <>
            <button type="button" className="admin-button admin-button-secondary" onClick={() => setDeleteTarget(null)}>
              取消
            </button>
            <button
              type="button"
              className="admin-button admin-button-danger"
              disabled={!deleteTarget || deleteMutation.isPending}
              onClick={() => {
                if (!deleteTarget) return;
                deleteMutation.mutate(deleteTarget.queue_id);
              }}
            >
              {deleteMutation.isPending ? '删除中...' : '确认删除'}
            </button>
          </>
        )}
      />
    </div>
  );
}
