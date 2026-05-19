import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { fetchAdminTls, renewAdminTls } from '../api';
import { AdminDialog, ResultMessage, SectionCard, StatusPill } from '../components/AdminHelpers';
import { AdminListTable } from '../components/AdminListTable';
import type { AdminTlsItem } from '../types';

export function AdminTlsPage() {
  const queryClient = useQueryClient();
  const [renewOpen, setRenewOpen] = useState(false);
  const [success, setSuccess] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['admin-tls'],
    queryFn: fetchAdminTls,
  });

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['admin-tls'] });
  };

  const renewMutation = useMutation({
    mutationFn: renewAdminTls,
    onSuccess: async (payload) => {
      setRenewOpen(false);
      setSuccess(payload.detail);
      setError(null);
      await refresh();
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const columns = useMemo<ColumnDef<AdminTlsItem>[]>(() => [
    { accessorKey: 'name', header: '证书目录' },
    { accessorKey: 'status', header: '状态', cell: (info) => <StatusPill status={String(info.getValue())} /> },
    { accessorKey: 'expires_at', header: '到期时间', cell: (info) => info.getValue<string>() || '—' },
    { accessorKey: 'domains', header: '覆盖域名', cell: ({ row }) => row.original.domains.length ? row.original.domains.join(', ') : '—' },
    { accessorKey: 'certificate_path', header: '证书路径', cell: (info) => info.getValue<string>() || '—' },
  ], []);

  return (
    <div className="admin-section-stack">
      <SectionCard
        title="TLS 证书状态"
        description="优先读取 Let’s Encrypt live 目录中的真实证书，并通过 openssl 提取到期时间和覆盖域名。"
        actions={(
          <div className="admin-inline-actions">
            <button type="button" className="admin-button admin-button-secondary" onClick={() => void refresh()}>
              刷新
            </button>
            <button type="button" className="admin-button admin-button-primary" onClick={() => setRenewOpen(true)}>
              触发续签
            </button>
          </div>
        )}
      >
        <p>{data?.detail || '暂无证书状态摘要'}</p>
        <ResultMessage error={error} success={success} />
      </SectionCard>

      <AdminListTable
        data={data?.items ?? []}
        emptyMessage={isLoading ? '加载中...' : '暂无证书数据'}
        columns={columns}
      />

      <AdminDialog
        open={renewOpen}
        title="确认触发证书续签"
        description="将执行 certbot renew。开发环境未安装 certbot 时会返回明确降级提示。"
        onClose={() => setRenewOpen(false)}
        actions={(
          <>
            <button type="button" className="admin-button admin-button-secondary" onClick={() => setRenewOpen(false)}>
              取消
            </button>
            <button
              type="button"
              className="admin-button admin-button-danger"
              disabled={renewMutation.isPending}
              onClick={() => renewMutation.mutate()}
            >
              {renewMutation.isPending ? '执行中...' : '确认续签'}
            </button>
          </>
        )}
      />
    </div>
  );
}
