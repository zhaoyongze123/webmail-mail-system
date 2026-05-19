import { useMemo } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { fetchAdminAliases, toggleAdminAlias } from '../api';
import { AdminListTable } from '../components/AdminListTable';
import type { AdminAlias } from '../types';

export function AdminAliasesPage() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ['admin-aliases'], queryFn: fetchAdminAliases });
  const toggleMutation = useMutation({
    mutationFn: toggleAdminAlias,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['admin-aliases'] });
    },
  });
  const columns = useMemo<ColumnDef<AdminAlias>[]>(() => [
    { accessorKey: 'name', header: '名称' },
    { accessorKey: 'status', header: '状态' },
    { accessorKey: 'description', header: '转发目标', cell: (info) => info.getValue<string>() || '—' },
    { accessorKey: 'updated_at', header: '更新时间' },
    {
      id: 'actions',
      header: '操作',
      cell: ({ row }) => (
        <button
          type="button"
          className="admin-button admin-button-secondary"
          onClick={() => toggleMutation.mutate(row.original.id)}
        >
          {row.original.is_active ? '停用' : '启用'}
        </button>
      ),
    },
  ], [toggleMutation]);

  return <AdminListTable data={data?.items ?? []} emptyMessage={isLoading ? '加载中...' : '暂无别名数据'} columns={columns} />;
}
