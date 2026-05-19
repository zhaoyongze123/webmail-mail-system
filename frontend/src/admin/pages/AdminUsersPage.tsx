import { useMemo } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { fetchAdminUsers, updateAdminUserQuota } from '../api';
import { AdminListTable } from '../components/AdminListTable';
import type { AdminMailboxUser } from '../types';

export function AdminUsersPage() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ['admin-users'], queryFn: fetchAdminUsers });
  const quotaMutation = useMutation({
    mutationFn: ({ id, quota_mb }: { id: string; quota_mb: number }) => updateAdminUserQuota(id, quota_mb),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['admin-users'] });
      await queryClient.invalidateQueries({ queryKey: ['admin-quotas'] });
    },
  });

  const columns = useMemo<ColumnDef<AdminMailboxUser>[]>(() => [
    { accessorKey: 'name', header: '名称' },
    { accessorKey: 'email', header: '邮箱' },
    { accessorKey: 'status', header: '状态' },
    { accessorKey: 'quota_mb', header: '配额(MB)' },
    { accessorKey: 'description', header: '角色', cell: (info) => info.getValue<string>() || '—' },
    {
      id: 'actions',
      header: '操作',
      cell: ({ row }) => (
        <button
          type="button"
          className="admin-button admin-button-secondary"
          onClick={() => {
            const nextQuota = window.prompt('请输入新的配额(MB)', String(row.original.quota_mb));
            if (!nextQuota) return;
            const parsed = Number(nextQuota);
            if (!Number.isFinite(parsed) || parsed <= 0) return;
            quotaMutation.mutate({ id: row.original.id, quota_mb: parsed });
          }}
        >
          修改配额
        </button>
      ),
    },
  ], [quotaMutation]);

  return <AdminListTable data={data?.items ?? []} emptyMessage={isLoading ? '加载中...' : '暂无用户数据'} columns={columns} />;
}
