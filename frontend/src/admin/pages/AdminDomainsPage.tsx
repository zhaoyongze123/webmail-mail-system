import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { bulkAdminDomainStatus, createAdminDomain, deleteAdminDomain, fetchAdminDomain, fetchAdminDomainDnsCheck, fetchAdminDomains, updateAdminDomain } from '../api';
import { AdminDialog, ResultMessage, SectionCard, StatusPill, useAdminListSearchParams } from '../components/AdminHelpers';
import { AdminListTable } from '../components/AdminListTable';
import type { AdminDomain, AdminDomainDnsCheck, DomainFormInput } from '../types';

function domainStatusLabel(status: string) {
  if (status === 'active') return '启用';
  if (status === 'disabled') return '停用';
  return status;
}

const emptyDomainForm: DomainFormInput = {
  name: '',
  quota_limit_mb: 10240,
  status: 'active',
};

export function AdminDomainsPage() {
  const queryClient = useQueryClient();
  const params = useAdminListSearchParams({ q: '', status: '', page: 1 });
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [editingDomain, setEditingDomain] = useState<AdminDomain | null>(null);
  const [detailDomainId, setDetailDomainId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<AdminDomain | null>(null);
  const [form, setForm] = useState<DomainFormInput>(emptyDomainForm);
  const [dnsCheckResult, setDnsCheckResult] = useState<AdminDomainDnsCheck | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['admin-domains', params.page, params.q, params.status],
    queryFn: () => fetchAdminDomains({ page: params.page, page_size: 10, q: params.q, status: params.status || undefined }),
  });

  const detailQuery = useQuery({
    queryKey: ['admin-domain-detail', detailDomainId],
    queryFn: () => fetchAdminDomain(detailDomainId!),
    enabled: Boolean(detailDomainId),
  });

  const dnsCheckMutation = useMutation({
    mutationFn: fetchAdminDomainDnsCheck,
    onSuccess: (payload) => {
      setDnsCheckResult(payload);
      setSuccess(`域名 ${payload.domain} DNS 检测已完成。`);
      setError(null);
    },
    onError: (err) => {
      setDnsCheckResult(null);
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const refreshLists = async () => {
    await queryClient.invalidateQueries({ queryKey: ['admin-domains'] });
    await queryClient.invalidateQueries({ queryKey: ['admin-overview'] });
  };

  const createMutation = useMutation({
    mutationFn: createAdminDomain,
    onSuccess: async () => {
      setForm(emptyDomainForm);
      setSuccess('域名已创建。');
      setError(null);
      await refreshLists();
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: Partial<DomainFormInput> }) => updateAdminDomain(id, payload),
    onSuccess: async () => {
      setEditingDomain(null);
      setSuccess('域名已更新。');
      setError(null);
      await refreshLists();
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: deleteAdminDomain,
    onSuccess: async (payload) => {
      setDeleteTarget(null);
      setSuccess(`域名已删除，影响用户 ${payload.impact.user_count} 个、别名 ${payload.impact.alias_count} 个。`);
      setError(null);
      await refreshLists();
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const bulkMutation = useMutation({
    mutationFn: bulkAdminDomainStatus,
    onSuccess: async (_, payload) => {
      setSelectedIds([]);
      setSuccess(`批量状态更新完成：${payload.status}`);
      setError(null);
      await refreshLists();
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const columns = useMemo<ColumnDef<AdminDomain>[]>(() => [
    {
      id: 'select',
      header: '选择',
      cell: ({ row }) => (
        <input
          type="checkbox"
          checked={selectedIds.includes(row.original.id)}
          onChange={(event) => {
            setSelectedIds((current) => event.target.checked
              ? [...current, row.original.id]
              : current.filter((item) => item !== row.original.id));
          }}
        />
      ),
    },
    { accessorKey: 'name', header: '域名' },
    { accessorKey: 'quota_limit_mb', header: '配额上限(MB)' },
    { accessorKey: 'user_count', header: '用户数' },
    { accessorKey: 'alias_count', header: '别名数' },
    { accessorKey: 'used_quota_mb', header: '已用(MB)' },
    { accessorKey: 'status', header: '状态', cell: (info) => <StatusPill status={String(info.getValue())} label={domainStatusLabel(String(info.getValue()))} /> },
    {
      id: 'actions',
      header: '操作',
      cell: ({ row }) => (
        <div className="admin-inline-actions">
          <button
            type="button"
            className="admin-button admin-button-secondary"
            onClick={() => {
              setEditingDomain(row.original);
              setForm({
                name: row.original.name,
                quota_limit_mb: row.original.quota_limit_mb,
                status: row.original.status as 'active' | 'disabled',
              });
            }}
          >
            编辑
          </button>
          <button
            type="button"
            className="admin-button admin-button-secondary"
            onClick={() => setDetailDomainId(row.original.id)}
          >
            详情
          </button>
          <button
            type="button"
            className="admin-button admin-button-secondary"
            onClick={() => updateMutation.mutate({ id: row.original.id, payload: { status: row.original.status === 'active' ? 'disabled' : 'active' } })}
          >
            {row.original.status === 'active' ? '停用' : '启用'}
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
  ], [deleteMutation, selectedIds, updateMutation]);

  return (
    <div className="admin-section-stack">
      <SectionCard title={editingDomain ? '编辑域名' : '新增域名'} description="支持搜索、分页、详情和批量启停。">
        <form
          className="admin-form-grid admin-form-grid--two"
          onSubmit={(event) => {
            event.preventDefault();
            if (editingDomain) {
              updateMutation.mutate({ id: editingDomain.id, payload: form });
              return;
            }
            createMutation.mutate(form);
          }}
        >
          <label>
            <span>域名</span>
            <input value={form.name} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} />
          </label>
          <label>
            <span>配额上限(MB)</span>
            <input inputMode="numeric" value={String(form.quota_limit_mb)} onChange={(event) => setForm((current) => ({ ...current, quota_limit_mb: Number(event.target.value || 0) }))} />
          </label>
          <label>
            <span>状态</span>
            <select value={form.status} onChange={(event) => setForm((current) => ({ ...current, status: event.target.value as 'active' | 'disabled' }))}>
              <option value="active">启用</option>
              <option value="disabled">停用</option>
            </select>
          </label>
          <div className="admin-inline-actions">
            <button type="submit" className="admin-button admin-button-primary" disabled={createMutation.isPending || updateMutation.isPending}>
              {editingDomain ? '保存修改' : '创建域名'}
            </button>
            {editingDomain ? (
              <button type="button" className="admin-button admin-button-secondary" onClick={() => { setEditingDomain(null); setForm(emptyDomainForm); }}>
                取消编辑
              </button>
            ) : null}
          </div>
        </form>
        <ResultMessage error={error} success={success} />
      </SectionCard>

      {detailQuery.data?.domain ? (
        <SectionCard
          title={`域详情：${detailQuery.data.domain.name}`}
          description={detailQuery.data.domain.description}
          actions={(
            <div className="admin-inline-actions">
              <button
                type="button"
                className="admin-button admin-button-secondary"
                disabled={dnsCheckMutation.isPending}
                onClick={() => {
                  setDnsCheckResult(null);
                  dnsCheckMutation.mutate(detailQuery.data.domain.id);
                }}
              >
                {dnsCheckMutation.isPending ? '检测中...' : 'DNS 检测'}
              </button>
              <button type="button" className="admin-button admin-button-secondary" onClick={() => {
                setDetailDomainId(null);
                setDnsCheckResult(null);
              }}
              >
                关闭详情
              </button>
            </div>
          )}
        >
          <div className="admin-info-grid">
            <div className="admin-info-card"><strong>用户数</strong><p>{detailQuery.data.domain.user_count}</p></div>
            <div className="admin-info-card"><strong>别名数</strong><p>{detailQuery.data.domain.alias_count}</p></div>
            <div className="admin-info-card"><strong>配额上限</strong><p>{detailQuery.data.domain.quota_limit_mb} MB</p></div>
            <div className="admin-info-card"><strong>已用</strong><p>{detailQuery.data.domain.used_quota_mb} MB</p></div>
          </div>
          {dnsCheckResult ? (
            <div className="admin-section-stack">
              <div className="admin-inline-actions">
                <span className={`admin-status-pill ${dnsCheckResult.status === 'ok' ? 'is-success' : dnsCheckResult.status === 'warning' ? 'is-warning' : 'is-working'}`}>
                  DNS 总状态：{dnsCheckResult.status === 'ok' ? '正常' : dnsCheckResult.status === 'warning' ? '告警' : dnsCheckResult.status}
                </span>
              </div>
              <div className="admin-info-grid">
                {dnsCheckResult.checks.map((check) => (
                  <div key={check.key} className="admin-info-card">
                    <strong>{check.label}</strong>
                    <p><StatusPill status={check.status} /></p>
                    <p>{check.detail}</p>
                    <p>探测后端：{check.backend}</p>
                    {check.records.length > 0 ? <p className="admin-mono">{check.records.join('\n')}</p> : null}
                    {check.command_result?.stderr ? <p className="admin-mono">{check.command_result.stderr}</p> : null}
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </SectionCard>
      ) : null}

      <AdminListTable
        data={data?.items ?? []}
        emptyMessage={isLoading ? '加载中...' : '暂无域名数据'}
        columns={columns}
        toolbar={(
          <div className="admin-toolbar-grid">
            <input placeholder="搜索域名" value={params.q} onChange={(event) => params.setQ(event.target.value)} />
            <select value={params.status} onChange={(event) => params.setStatus(event.target.value)}>
              <option value="">全部状态</option>
              <option value="active">启用</option>
              <option value="disabled">停用</option>
            </select>
            <div className="admin-inline-actions">
              <button type="button" className="admin-button admin-button-secondary" disabled={selectedIds.length === 0} onClick={() => bulkMutation.mutate({ ids: selectedIds, status: 'active' })}>批量启用</button>
              <button type="button" className="admin-button admin-button-secondary" disabled={selectedIds.length === 0} onClick={() => bulkMutation.mutate({ ids: selectedIds, status: 'disabled' })}>批量停用</button>
            </div>
          </div>
        )}
        pagination={data ? { ...data, onPageChange: params.setPage } : undefined}
      />

      <AdminDialog
        open={Boolean(deleteTarget)}
        title="确认删除域名"
        description={deleteTarget ? `删除 ${deleteTarget.name} 会同时移除关联元数据，请确认。` : undefined}
        onClose={() => setDeleteTarget(null)}
        actions={(
          <button
            type="button"
            className="admin-button admin-button-danger"
            disabled={!deleteTarget || deleteMutation.isPending}
            onClick={() => {
              if (!deleteTarget) return;
              deleteMutation.mutate(deleteTarget.id);
            }}
          >
            确认删除
          </button>
        )}
      />
    </div>
  );
}
