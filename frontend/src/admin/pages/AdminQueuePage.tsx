import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import {
  bulkDeleteAdminQueueItems,
  clearAdminQueueByStatuses,
  deleteAdminQueueItem,
  fetchAdminQueue,
  fetchAdminQueueItem,
  flushAdminQueue,
  requeueAdminQueueItem,
} from '../api';
import { AdminDialog, ResultMessage, SectionCard, StatusPill } from '../components/AdminHelpers';
import { AdminListTable } from '../components/AdminListTable';
import { AdminTextPreview } from '../components/AdminTextPreview';
import type { AdminQueueItem } from '../types';
import { formatLocaleDateTime } from '../../i18n/runtime';

const QUEUE_STATUS_LABELS: Record<string, string> = {
  queued: '排队中',
  active: '投递中',
  deferred: '延迟重试',
  hold: '人工挂起',
  incoming: '待入队',
  maildrop: '本地投递',
};

function formatBytes(bytes: number) {
  if (!bytes) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function formatTimestamp(timestamp: number) {
  if (!timestamp) return '—';
  return formatLocaleDateTime(new Date(timestamp * 1000), { hour12: false });
}

const DEFAULT_CLEAR_STATUS = ['deferred', 'hold', 'queued'];

function queueStatusLabel(status: string) {
  return QUEUE_STATUS_LABELS[status] ?? status;
}

function renderRecipientSummary(item: AdminQueueItem) {
  const details = item.recipient_details ?? [];
  if (details.length > 0) {
    return (
      <div className="admin-queue-recipient-list">
        {details.slice(0, 3).map((recipient) => (
          <div key={`${item.queue_id}-${recipient.address}`} className="admin-queue-recipient-item">
            <span>{recipient.address}</span>
            {recipient.delay_reason_display ? <span className="admin-queue-reason-text">{recipient.delay_reason_display}</span> : null}
          </div>
        ))}
        {details.length > 3 ? <span className="admin-queue-reason-text">其余 {details.length - 3} 个收件人已省略</span> : null}
      </div>
    );
  }
  return item.recipients.length ? item.recipients.join(', ') : '—';
}

export function AdminQueuePage() {
  const queryClient = useQueryClient();
  const [deleteTarget, setDeleteTarget] = useState<AdminQueueItem | null>(null);
  const [bulkDeleteTargets, setBulkDeleteTargets] = useState<AdminQueueItem[]>([]);
  const [activeQueueItem, setActiveQueueItem] = useState<AdminQueueItem | null>(null);
  const [statusFilter, setStatusFilter] = useState('');
  const [query, setQuery] = useState('');
  const [clearStatuses, setClearStatuses] = useState(DEFAULT_CLEAR_STATUS.join(','));
  const [success, setSuccess] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['admin-queue', statusFilter, query],
    queryFn: () => fetchAdminQueue({ status: statusFilter || undefined, q: query || undefined }),
  });

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['admin-queue'] });
    await queryClient.invalidateQueries({ queryKey: ['admin-overview'] });
  };

  const queueItemDetailQuery = useQuery({
    queryKey: ['admin-queue-item', activeQueueItem?.queue_id],
    queryFn: () => fetchAdminQueueItem(activeQueueItem?.queue_id ?? ''),
    enabled: Boolean(activeQueueItem?.queue_id),
  });

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

  const requeueMutation = useMutation({
    mutationFn: requeueAdminQueueItem,
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

  const bulkDeleteMutation = useMutation({
    mutationFn: bulkDeleteAdminQueueItems,
    onSuccess: async (payload) => {
      setBulkDeleteTargets([]);
      setSuccess(payload.detail);
      setError(null);
      await refresh();
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const clearMutation = useMutation({
    mutationFn: clearAdminQueueByStatuses,
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

  const columns = useMemo<ColumnDef<AdminQueueItem>[]>(() => [
    {
      id: 'select',
      header: '选择',
      cell: ({ row }) => (
        <input
          type="checkbox"
          checked={bulkDeleteTargets.some((item) => item.queue_id === row.original.queue_id)}
          onChange={(event) => {
            setBulkDeleteTargets((current) => (
              event.target.checked
                ? [...current, row.original]
                : current.filter((item) => item.queue_id !== row.original.queue_id)
            ));
          }}
        />
      ),
    },
    { accessorKey: 'queue_id', header: '队列编号' },
    { accessorKey: 'status', header: '状态', cell: (info) => <StatusPill status={String(info.getValue())} label={queueStatusLabel(String(info.getValue()))} /> },
    { accessorKey: 'sender', header: '发件人' },
    {
      accessorKey: 'recipients',
      header: '收件人',
      cell: ({ row }) => renderRecipientSummary(row.original),
    },
    {
      accessorKey: 'failure_reason',
      header: '失败原因',
      cell: ({ row }) => row.original.failure_reason ? <span className="admin-queue-reason-text">{row.original.failure_reason}</span> : '—',
    },
    { accessorKey: 'message_size', header: '大小', cell: (info) => formatBytes(Number(info.getValue()) || 0) },
    { accessorKey: 'arrival_time', header: '入队时间', cell: (info) => formatTimestamp(Number(info.getValue()) || 0) },
    {
      id: 'actions',
      header: '操作',
      cell: ({ row }) => (
        <div className="admin-inline-actions">
          <button
            type="button"
            className="admin-button admin-button-secondary"
            onClick={() => setActiveQueueItem(row.original)}
          >
            查看
          </button>
          <button
            type="button"
            className="admin-button admin-button-secondary"
            onClick={() => requeueMutation.mutate(row.original.queue_id)}
          >
            重投
          </button>
          <button
            type="button"
            className="admin-button admin-button-danger"
            onClick={() => setDeleteTarget(row.original)}
          >
            删除
          </button>
        </div>
      ),
    },
  ], [bulkDeleteTargets, requeueMutation]);

  const filteredItems = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return (data?.items ?? []).filter((item) => {
      const matchesStatus = !statusFilter || item.status === statusFilter;
      const recipientText = item.recipient_details?.map((recipient) => [recipient.address, recipient.delay_reason_display, recipient.delay_reason].filter(Boolean).join(' ')).join(' ') || item.recipients.join(', ');
      const haystack = [item.queue_id, item.status, item.queue_name, item.sender, recipientText, item.failure_reason || ''].join(' ').toLowerCase();
      const matchesQuery = !normalizedQuery || haystack.includes(normalizedQuery);
      return matchesStatus && matchesQuery;
    });
  }, [data?.items, query, statusFilter]);

  const summaryItems = useMemo(() => {
    const summary = data?.summary || {};
    return [
      { key: 'total', label: '总队列数', value: summary.total ?? 0 },
      { key: 'visible', label: '当前筛选', value: summary.visible_total ?? filteredItems.length },
      { key: 'active', label: '投递中', value: summary.active ?? 0 },
      { key: 'deferred', label: '延迟重试', value: summary.deferred ?? 0 },
      { key: 'hold', label: '人工挂起', value: summary.hold ?? 0 },
    ];
  }, [data?.summary, filteredItems.length]);

  const activeDetail = queueItemDetailQuery.data;

  return (
    <div className="admin-section-stack">
      <SectionCard
        title="队列摘要"
        description="基于真实邮件队列接口，支持查看队列正文、单项重投、批量删除和按状态清空。"
        actions={(
          <div className="admin-inline-actions">
            <label className="admin-filter-field">
              <span>状态</span>
              <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
                <option value="">全部</option>
                <option value="queued">排队中</option>
                <option value="active">投递中</option>
                <option value="deferred">延迟重试</option>
                <option value="hold">人工挂起</option>
              </select>
            </label>
            <label className="admin-filter-field">
              <span>搜索</span>
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="队列编号 / 发件人 / 收件人" />
            </label>
            <fieldset className="admin-filter-field">
              <span>清空状态</span>
              <div className="admin-inline-actions">
                {DEFAULT_CLEAR_STATUS.map((status) => {
                  const selected = clearStatuses.split(',').map((value) => value.trim()).filter(Boolean).includes(status);
                  return (
                    <label key={status} className="admin-check-field">
                      <input
                        type="checkbox"
                        checked={selected}
                        onChange={(event) => {
                          const current = new Set(clearStatuses.split(',').map((value) => value.trim()).filter(Boolean));
                          if (event.target.checked) current.add(status);
                          else current.delete(status);
                          setClearStatuses(Array.from(current).join(','));
                        }}
                      />
                      <span>{queueStatusLabel(status)}</span>
                    </label>
                  );
                })}
              </div>
            </fieldset>
            <button type="button" className="admin-button admin-button-secondary" onClick={() => void refresh()}>
              刷新
            </button>
            <button
              type="button"
              className="admin-button admin-button-primary"
              disabled={flushMutation.isPending}
              onClick={() => flushMutation.mutate()}
            >
              {flushMutation.isPending ? '执行中...' : '立即刷新投递'}
            </button>
            <button
              type="button"
              className="admin-button admin-button-secondary"
              disabled={bulkDeleteTargets.length === 0 || bulkDeleteMutation.isPending}
              onClick={() => bulkDeleteMutation.mutate(bulkDeleteTargets.map((item) => item.queue_id))}
            >
              批量删除
            </button>
            <button
              type="button"
              className="admin-button admin-button-danger"
              disabled={clearMutation.isPending}
              onClick={() => clearMutation.mutate(clearStatuses.split(',').map((value) => value.trim()).filter(Boolean))}
            >
              按状态清空
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
        {activeQueueItem ? (
          <div className="admin-info-card admin-queue-detail">
            <strong>当前选中队列</strong>
            <p>{activeQueueItem.queue_id} · {queueStatusLabel(activeQueueItem.status)} · {queueStatusLabel(activeQueueItem.queue_name)}</p>
            <p>发件人：{activeQueueItem.sender}</p>
            <div>
              <p>收件人：</p>
              {renderRecipientSummary(activeQueueItem)}
            </div>
            {activeQueueItem.failure_reason ? <p>失败原因：{activeQueueItem.failure_reason}</p> : null}
            <p>消息大小：{formatBytes(activeQueueItem.message_size)}</p>
            <p>入队时间：{formatTimestamp(activeQueueItem.arrival_time)}</p>
            <div className="admin-inline-actions">
              <button
                type="button"
                className="admin-button admin-button-secondary"
                onClick={() => void navigator.clipboard?.writeText(activeQueueItem.queue_id)}
              >
                复制编号
              </button>
              <button
                type="button"
                className="admin-button admin-button-secondary"
                onClick={() => requeueMutation.mutate(activeQueueItem.queue_id)}
              >
                重投
              </button>
              <button
                type="button"
                className="admin-button admin-button-danger"
                onClick={() => setDeleteTarget(activeQueueItem)}
              >
                删除此项
              </button>
            </div>
            {activeDetail?.content ? (
              <AdminTextPreview title="队列正文" text={activeDetail.content} />
            ) : activeDetail?.detail ? (
              <p className="admin-mono">{activeDetail.detail}</p>
            ) : null}
            {activeDetail?.command_result?.stderr ? <p className="admin-mono">{activeDetail.command_result.stderr}</p> : null}
          </div>
        ) : null}
      </SectionCard>

      <AdminListTable
        data={filteredItems}
        emptyMessage={isLoading ? '加载中...' : '暂无符合条件的队列数据'}
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
