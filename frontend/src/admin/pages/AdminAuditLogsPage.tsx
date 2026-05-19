import { useQuery } from '@tanstack/react-query';
import { fetchAdminAuditLogs } from '../api';
import { AdminListTable } from '../components/AdminListTable';

export function AdminAuditLogsPage() {
  const { data } = useQuery({ queryKey: ['admin-audit-logs'], queryFn: fetchAdminAuditLogs });

  return <AdminListTable data={data?.items ?? []} emptyMessage="暂无审计日志" columns={[
    { accessorKey: 'actor', header: '操作者' },
    { accessorKey: 'action', header: '动作' },
    { accessorKey: 'target', header: '目标' },
    { accessorKey: 'created_at', header: '时间' },
  ]} />;
}
