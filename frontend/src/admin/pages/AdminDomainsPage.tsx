import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { createAdminDomain, deleteAdminDomain, fetchAdminDomains, updateAdminDomain } from '../api';
import { AdminListTable } from '../components/AdminListTable';
import type { AdminDomain } from '../types';

export function AdminDomainsPage() {
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [quota, setQuota] = useState('10240');
  const [error, setError] = useState<string | null>(null);
  const { data, isLoading } = useQuery({ queryKey: ['admin-domains'], queryFn: fetchAdminDomains });

  const createMutation = useMutation({
    mutationFn: createAdminDomain,
    onSuccess: async () => {
      setName('');
      setQuota('10240');
      setError(null);
      await queryClient.invalidateQueries({ queryKey: ['admin-domains'] });
      await queryClient.invalidateQueries({ queryKey: ['admin-overview'] });
    },
    onError: (err) => setError((err as Error).message),
  });

  const toggleMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: 'active' | 'disabled' }) => updateAdminDomain(id, { status }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['admin-domains'] });
      await queryClient.invalidateQueries({ queryKey: ['admin-overview'] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: deleteAdminDomain,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['admin-domains'] });
      await queryClient.invalidateQueries({ queryKey: ['admin-overview'] });
    },
  });

  const columns = useMemo<ColumnDef<AdminDomain>[]>(() => [
    { accessorKey: 'name', header: '名称' },
    { accessorKey: 'status', header: '状态' },
    { accessorKey: 'quota_limit_mb', header: '配额上限(MB)' },
    { accessorKey: 'description', header: '说明', cell: (info) => info.getValue<string>() || '—' },
    {
      id: 'actions',
      header: '操作',
      cell: ({ row }) => (
        <div className="admin-inline-actions">
          <button
            type="button"
            className="admin-button admin-button-secondary"
            onClick={() => toggleMutation.mutate({ id: row.original.id, status: row.original.status === 'active' ? 'disabled' : 'active' })}
          >
            {row.original.status === 'active' ? '停用' : '启用'}
          </button>
          <button
            type="button"
            className="admin-button admin-button-danger"
            onClick={() => {
              if (window.confirm(`确认删除域 ${row.original.name} 吗？`)) {
                deleteMutation.mutate(row.original.id);
              }
            }}
          >
            删除
          </button>
        </div>
      ),
    },
  ], [deleteMutation, toggleMutation]);

  return (
    <div className="admin-section-stack">
      <form
        className="admin-form-card"
        onSubmit={(event) => {
          event.preventDefault();
          createMutation.mutate({
            name,
            quota_limit_mb: Number(quota) || 10240,
            status: 'active',
          });
        }}
      >
        <div className="admin-form-grid">
          <label>
            <span>新增域名</span>
            <input value={name} onChange={(event) => setName(event.target.value)} placeholder="example.com" />
          </label>
          <label>
            <span>配额上限(MB)</span>
            <input value={quota} onChange={(event) => setQuota(event.target.value)} inputMode="numeric" />
          </label>
        </div>
        <div className="admin-inline-actions">
          <button type="submit" className="admin-button admin-button-primary" disabled={createMutation.isPending}>
            {createMutation.isPending ? '提交中...' : '创建域名'}
          </button>
          {error ? <p className="admin-error-text">{error}</p> : null}
        </div>
      </form>
      <AdminListTable data={data?.items ?? []} emptyMessage={isLoading ? '加载中...' : '暂无域名数据'} columns={columns} />
    </div>
  );
}
