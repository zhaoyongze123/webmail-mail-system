import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { createAdminAlias, deleteAdminAlias, fetchAdminAliases, fetchAdminDomains, toggleAdminAlias, updateAdminAlias } from '../api';
import { AdminDialog, ResultMessage, SectionCard, StatusPill, useAdminListSearchParams } from '../components/AdminHelpers';
import { AdminListTable } from '../components/AdminListTable';
import type { AdminAlias, AliasFormInput } from '../types';

const emptyAliasForm: AliasFormInput = {
  domain_id: '',
  source_address: '',
  target_addresses: [''],
};

export function AdminAliasesPage() {
  const queryClient = useQueryClient();
  const params = useAdminListSearchParams({ q: '', domain_id: '', page: 1 });
  const [editingAlias, setEditingAlias] = useState<AdminAlias | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<AdminAlias | null>(null);
  const [form, setForm] = useState<AliasFormInput>(emptyAliasForm);
  const [success, setSuccess] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data: domainData } = useQuery({
    queryKey: ['admin-domains', 'alias-options'],
    queryFn: () => fetchAdminDomains({ page: 1, page_size: 100 }),
  });

  const { data, isLoading } = useQuery({
    queryKey: ['admin-aliases', params.page, params.q, params.domain_id],
    queryFn: () => fetchAdminAliases({ page: params.page, page_size: 10, q: params.q, domain_id: params.domain_id || undefined }),
  });

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['admin-aliases'] });
    await queryClient.invalidateQueries({ queryKey: ['admin-overview'] });
  };

  const createMutation = useMutation({
    mutationFn: createAdminAlias,
    onSuccess: async () => {
      setForm(emptyAliasForm);
      setSuccess('别名已创建。');
      setError(null);
      await refresh();
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: { target_addresses: string[] } }) => updateAdminAlias(id, payload),
    onSuccess: async () => {
      setEditingAlias(null);
      setSuccess('别名已更新。');
      setError(null);
      await refresh();
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const toggleMutation = useMutation({
    mutationFn: toggleAdminAlias,
    onSuccess: async () => {
      setSuccess('别名状态已切换。');
      setError(null);
      await refresh();
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: deleteAdminAlias,
    onSuccess: async () => {
      setDeleteTarget(null);
      setSuccess('别名已删除。');
      setError(null);
      await refresh();
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const columns = useMemo<ColumnDef<AdminAlias>[]>(() => [
    { accessorKey: 'source_address', header: '源地址' },
    { accessorKey: 'domain_name', header: '域名', cell: (info) => info.getValue<string>() || '—' },
    { accessorKey: 'description', header: '转发目标', cell: (info) => info.getValue<string>() || '—' },
    { accessorKey: 'status', header: '状态', cell: (info) => <StatusPill status={String(info.getValue())} /> },
    {
      id: 'actions',
      header: '操作',
      cell: ({ row }) => (
        <div className="admin-inline-actions">
          <button
            type="button"
            className="admin-button admin-button-secondary"
            onClick={() => {
              setEditingAlias(row.original);
              setForm({
                domain_id: row.original.domain_id,
                source_address: row.original.source_address,
                target_addresses: row.original.target_addresses.length > 0 ? row.original.target_addresses : [''],
              });
            }}
          >
            编辑
          </button>
          <button
            type="button"
            className="admin-button admin-button-secondary"
            onClick={() => toggleMutation.mutate(row.original.id)}
          >
            {row.original.is_active ? '停用' : '启用'}
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
  ], [deleteMutation, toggleMutation]);

  return (
    <div className="admin-section-stack">
      <SectionCard title={editingAlias ? '编辑别名' : '新增别名'} description="支持多目标地址、冲突提示和启停切换。">
        <form
          className="admin-form-grid admin-form-grid--two"
          onSubmit={(event) => {
            event.preventDefault();
            const payload = {
              ...form,
              target_addresses: form.target_addresses.map((item) => item.trim()).filter(Boolean),
            };
            if (editingAlias) {
              updateMutation.mutate({ id: editingAlias.id, payload: { target_addresses: payload.target_addresses } });
              return;
            }
            createMutation.mutate(payload);
          }}
        >
          <label>
            <span>所属域</span>
            <select value={form.domain_id} onChange={(event) => setForm((current) => ({ ...current, domain_id: event.target.value }))}>
              <option value="">请选择域名</option>
              {(domainData?.items || []).map((domain) => (
                <option key={domain.id} value={domain.id}>{domain.name}</option>
              ))}
            </select>
          </label>
          <label>
            <span>源地址</span>
            <input value={form.source_address} disabled={Boolean(editingAlias)} onChange={(event) => setForm((current) => ({ ...current, source_address: event.target.value }))} />
          </label>
          <div className="admin-multi-value">
            <span>目标地址</span>
            {form.target_addresses.map((target, index) => (
              <div key={`${index}-${target}`} className="admin-inline-actions">
                <input
                  value={target}
                  onChange={(event) => setForm((current) => ({
                    ...current,
                    target_addresses: current.target_addresses.map((item, itemIndex) => (itemIndex === index ? event.target.value : item)),
                  }))}
                />
                <button
                  type="button"
                  className="admin-button admin-button-secondary"
                  onClick={() => setForm((current) => ({
                    ...current,
                    target_addresses: current.target_addresses.length === 1 ? [''] : current.target_addresses.filter((_, itemIndex) => itemIndex !== index),
                  }))}
                >
                  删除
                </button>
              </div>
            ))}
            <button
              type="button"
              className="admin-button admin-button-secondary"
              onClick={() => setForm((current) => ({ ...current, target_addresses: [...current.target_addresses, ''] }))}
            >
              添加目标地址
            </button>
          </div>
          <div className="admin-inline-actions">
            <button type="submit" className="admin-button admin-button-primary" disabled={createMutation.isPending || updateMutation.isPending}>
              {editingAlias ? '保存修改' : '创建别名'}
            </button>
            {editingAlias ? (
              <button type="button" className="admin-button admin-button-secondary" onClick={() => { setEditingAlias(null); setForm(emptyAliasForm); }}>
                取消编辑
              </button>
            ) : null}
          </div>
        </form>
        <ResultMessage error={error} success={success} />
      </SectionCard>

      <AdminListTable
        data={data?.items ?? []}
        emptyMessage={isLoading ? '加载中...' : '暂无别名数据'}
        columns={columns}
        toolbar={(
          <div className="admin-toolbar-grid">
            <input placeholder="搜索别名地址" value={params.q} onChange={(event) => params.setQ(event.target.value)} />
            <select value={params.domain_id} onChange={(event) => params.setDomainId(event.target.value)}>
              <option value="">全部域名</option>
              {(domainData?.items || []).map((domain) => (
                <option key={domain.id} value={domain.id}>{domain.name}</option>
              ))}
            </select>
          </div>
        )}
        pagination={data ? { ...data, onPageChange: params.setPage } : undefined}
      />

      <AdminDialog
        open={Boolean(deleteTarget)}
        title="确认删除别名"
        description={deleteTarget ? `删除 ${deleteTarget.source_address} 后，该转发规则将立即失效。` : undefined}
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
