import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { fetchAdminQuotas } from '../api';
import { AdminListTable } from '../components/AdminListTable';
import type { AdminQuotaItem } from '../types';

export function AdminQuotasPage() {
  const { data, isLoading } = useQuery({ queryKey: ['admin-quotas'], queryFn: fetchAdminQuotas });
  const columns = useMemo<ColumnDef<AdminQuotaItem>[]>(() => [
    { accessorKey: 'name', header: '名称' },
    { accessorKey: 'status', header: '状态' },
    { accessorKey: 'default_quota_mb', header: '默认配额(MB)' },
    {
      accessorKey: 'usage_percent',
      header: '使用率',
      cell: (info) => (typeof info.getValue<number | undefined>() === 'number' ? `${info.getValue<number>()}%` : '—'),
    },
    {
      accessorKey: 'used_quota_mb',
      header: '已用(MB)',
      cell: (info) => (typeof info.getValue<number | undefined>() === 'number' ? String(info.getValue<number>()) : '—'),
    },
  ], []);

  return <AdminListTable data={data?.items ?? []} emptyMessage={isLoading ? '加载中...' : '暂无配额数据'} columns={columns} />;
}
