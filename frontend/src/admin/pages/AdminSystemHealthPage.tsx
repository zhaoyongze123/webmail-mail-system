import { useQuery } from '@tanstack/react-query';
import { fetchAdminHealth } from '../api';
import { AdminListTable } from '../components/AdminListTable';

export function AdminSystemHealthPage() {
  const { data } = useQuery({ queryKey: ['admin-system-health'], queryFn: fetchAdminHealth });

  return <AdminListTable data={data?.items ?? []} emptyMessage="暂无健康状态" columns={[
    { accessorKey: 'name', header: '服务' },
    { accessorKey: 'status', header: '状态' },
    { accessorKey: 'detail', header: '详情' },
  ]} />;
}
