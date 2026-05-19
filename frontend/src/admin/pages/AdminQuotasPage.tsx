import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { bulkUpdateQuotas, fetchAdminDomains, fetchAdminQuotas, recalcAdminUserQuota, updateAdminUserQuota, updateQuotaPolicy } from '../api';
import { AdminDialog, ResultMessage, SectionCard, StatusPill, useAdminListSearchParams } from '../components/AdminHelpers';
import { AdminListTable } from '../components/AdminListTable';
import type { AdminMailboxUser, AdminQuotaItem, QuotaPolicyFormInput } from '../types';

const defaultPolicyForm: QuotaPolicyFormInput = {
  domain_id: null,
  default_quota_mb: 500,
  warn_80_enabled: true,
  warn_90_enabled: true,
  warn_95_enabled: true,
};

export function AdminQuotasPage() {
  const queryClient = useQueryClient();
  const params = useAdminListSearchParams({ q: '', domain_id: '' });
  const [selectedUserIds, setSelectedUserIds] = useState<string[]>([]);
  const [policyForm, setPolicyForm] = useState<QuotaPolicyFormInput>(defaultPolicyForm);
  const [quotaTarget, setQuotaTarget] = useState<AdminMailboxUser | null>(null);
  const [recalcTarget, setRecalcTarget] = useState<AdminMailboxUser | null>(null);
  const [quotaValue, setQuotaValue] = useState('');
  const [bulkQuotaValue, setBulkQuotaValue] = useState('');
  const [bulkQuotaDialogOpen, setBulkQuotaDialogOpen] = useState(false);
  const [success, setSuccess] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data: domainData } = useQuery({
    queryKey: ['admin-domains', 'quota-options'],
    queryFn: () => fetchAdminDomains({ page: 1, page_size: 100 }),
  });

  const { data, isLoading } = useQuery({
    queryKey: ['admin-quotas', params.domain_id, params.q],
    queryFn: () => fetchAdminQuotas({ q: params.q, domain_id: params.domain_id || undefined }),
  });

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['admin-quotas'] });
    await queryClient.invalidateQueries({ queryKey: ['admin-users'] });
  };

  const policyMutation = useMutation({
    mutationFn: updateQuotaPolicy,
    onSuccess: async () => {
      setSuccess('配额策略已更新。');
      setError(null);
      await refresh();
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const quotaMutation = useMutation({
    mutationFn: ({ id, quota_mb }: { id: string; quota_mb: number }) => updateAdminUserQuota(id, quota_mb),
    onSuccess: async () => {
      setQuotaTarget(null);
      setQuotaValue('');
      setSuccess('用户配额已更新。');
      setError(null);
      await refresh();
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const bulkMutation = useMutation({
    mutationFn: bulkUpdateQuotas,
    onSuccess: async (_, payload) => {
      setSelectedUserIds([]);
      setBulkQuotaDialogOpen(false);
      setBulkQuotaValue('');
      setSuccess(`已批量更新 ${payload.ids.length} 个用户配额。`);
      setError(null);
      await refresh();
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const recalcMutation = useMutation({
    mutationFn: recalcAdminUserQuota,
    onSuccess: async (payload) => {
      setRecalcTarget(null);
      setSuccess(payload.result.detail);
      setError(null);
      await refresh();
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const domainColumns = useMemo<ColumnDef<AdminQuotaItem>[]>(() => [
    { accessorKey: 'name', header: '域名' },
    { accessorKey: 'default_quota_mb', header: '默认配额(MB)' },
    { accessorKey: 'quota_limit_mb', header: '总上限(MB)' },
    { accessorKey: 'used_quota_mb', header: '已用(MB)' },
    { accessorKey: 'usage_percent', header: '使用率', cell: (info) => `${info.getValue<number>() ?? 0}%` },
    { accessorKey: 'usage_source', header: '来源', cell: (info) => info.getValue<string>() || 'cached' },
    { accessorKey: 'status', header: '阈值状态', cell: (info) => <StatusPill status={String(info.getValue())} /> },
  ], []);

  const userColumns = useMemo<ColumnDef<AdminMailboxUser>[]>(() => [
    {
      id: 'select',
      header: '选择',
      cell: ({ row }) => (
        <input
          type="checkbox"
          checked={selectedUserIds.includes(row.original.id)}
          onChange={(event) => {
            setSelectedUserIds((current) => event.target.checked
              ? [...current, row.original.id]
              : current.filter((item) => item !== row.original.id));
          }}
        />
      ),
    },
    { accessorKey: 'email', header: '邮箱' },
    { accessorKey: 'quota_mb', header: '上限(MB)' },
    { accessorKey: 'used_quota_mb', header: '已用(MB)', cell: (info) => info.getValue<number>() ?? 0 },
    { accessorKey: 'usage_percent', header: '使用率', cell: (info) => `${info.getValue<number>() ?? 0}%` },
    { accessorKey: 'usage_source', header: '来源', cell: (info) => info.getValue<string>() || 'cached' },
    { accessorKey: 'quota_status', header: '阈值状态', cell: (info) => <StatusPill status={String(info.getValue())} /> },
    {
      id: 'actions',
      header: '操作',
      cell: ({ row }) => (
        <div className="admin-inline-actions">
          <button
            type="button"
            className="admin-button admin-button-secondary"
            onClick={() => {
              setQuotaTarget(row.original);
              setQuotaValue(String(row.original.quota_mb));
            }}
          >
            修改配额
          </button>
          <button
            type="button"
            className="admin-button admin-button-secondary"
            onClick={() => setRecalcTarget(row.original)}
          >
            重算使用量
          </button>
        </div>
      ),
    },
  ], [selectedUserIds]);

  return (
    <div className="admin-section-stack">
      <SectionCard title="域级配额策略" description="优先读取 Dovecot `doveadm quota get`，不可用时自动回退到本地缓存聚合。">
        <form
          className="admin-form-grid admin-form-grid--two"
          onSubmit={(event) => {
            event.preventDefault();
            policyMutation.mutate(policyForm);
          }}
        >
          <label>
            <span>作用域域名</span>
            <select value={policyForm.domain_id || ''} onChange={(event) => setPolicyForm((current) => ({ ...current, domain_id: event.target.value || null }))}>
              <option value="">全局默认</option>
              {(domainData?.items || []).map((domain) => (
                <option key={domain.id} value={domain.id}>{domain.name}</option>
              ))}
            </select>
          </label>
          <label>
            <span>默认配额(MB)</span>
            <input inputMode="numeric" value={String(policyForm.default_quota_mb)} onChange={(event) => setPolicyForm((current) => ({ ...current, default_quota_mb: Number(event.target.value || 0) }))} />
          </label>
          <label className="admin-check-field">
            <input type="checkbox" checked={policyForm.warn_80_enabled} onChange={(event) => setPolicyForm((current) => ({ ...current, warn_80_enabled: event.target.checked }))} />
            <span>启用 80% 预警</span>
          </label>
          <label className="admin-check-field">
            <input type="checkbox" checked={policyForm.warn_90_enabled} onChange={(event) => setPolicyForm((current) => ({ ...current, warn_90_enabled: event.target.checked }))} />
            <span>启用 90% 预警</span>
          </label>
          <label className="admin-check-field">
            <input type="checkbox" checked={policyForm.warn_95_enabled} onChange={(event) => setPolicyForm((current) => ({ ...current, warn_95_enabled: event.target.checked }))} />
            <span>启用 95% 预警</span>
          </label>
          <div className="admin-inline-actions">
            <button type="submit" className="admin-button admin-button-primary" disabled={policyMutation.isPending}>保存策略</button>
          </div>
        </form>
        <ResultMessage error={error} success={success} />
      </SectionCard>

      <AdminListTable
        data={data?.items ?? []}
        emptyMessage={isLoading ? '加载中...' : '暂无域配额数据'}
        columns={domainColumns}
        toolbar={(
          <div className="admin-toolbar-grid">
            <input placeholder="搜索用户邮箱" value={params.q} onChange={(event) => params.setQ(event.target.value)} />
            <select value={params.domain_id} onChange={(event) => params.setDomainId(event.target.value)}>
              <option value="">全部域名</option>
              {(domainData?.items || []).map((domain) => (
                <option key={domain.id} value={domain.id}>{domain.name}</option>
              ))}
            </select>
          </div>
        )}
      />

      <AdminListTable
        data={data?.user_items ?? []}
        emptyMessage={isLoading ? '加载中...' : '暂无用户配额数据'}
        columns={userColumns}
        toolbar={(
          <div className="admin-toolbar-grid">
            <div className="admin-inline-actions">
              <button
                type="button"
                className="admin-button admin-button-secondary"
                disabled={selectedUserIds.length === 0}
                onClick={() => {
                  setBulkQuotaValue('');
                  setBulkQuotaDialogOpen(true);
                }}
              >
                批量更新配额
              </button>
            </div>
          </div>
        )}
      />

      <AdminDialog
        open={Boolean(quotaTarget)}
        title="修改用户配额"
        description={quotaTarget ? `修改 ${quotaTarget.email} 的邮箱配额` : undefined}
        onClose={() => {
          setQuotaTarget(null);
          setQuotaValue('');
        }}
        actions={(
          <button
            type="button"
            className="admin-button admin-button-primary"
            disabled={!quotaTarget || !quotaValue || quotaMutation.isPending}
            onClick={() => {
              if (!quotaTarget) return;
              const parsed = Number(quotaValue);
              if (!Number.isFinite(parsed) || parsed <= 0) return;
              quotaMutation.mutate({ id: quotaTarget.id, quota_mb: parsed });
            }}
          >
            保存配额
          </button>
        )}
      >
        <label className="admin-dialog-field">
          <span>配额(MB)</span>
          <input inputMode="numeric" value={quotaValue} onChange={(event) => setQuotaValue(event.target.value)} />
        </label>
      </AdminDialog>

      <AdminDialog
        open={bulkQuotaDialogOpen}
        title="批量更新配额"
        description={`当前选中 ${selectedUserIds.length} 个用户`}
        onClose={() => {
          setBulkQuotaDialogOpen(false);
          setBulkQuotaValue('');
        }}
        actions={(
          <button
            type="button"
            className="admin-button admin-button-primary"
            disabled={!bulkQuotaValue || bulkMutation.isPending}
            onClick={() => {
              const parsed = Number(bulkQuotaValue);
              if (!Number.isFinite(parsed) || parsed <= 0) return;
              bulkMutation.mutate({ ids: selectedUserIds, quota_mb: parsed });
            }}
          >
            批量保存
          </button>
        )}
      >
        <label className="admin-dialog-field">
          <span>统一配额(MB)</span>
          <input inputMode="numeric" value={bulkQuotaValue} onChange={(event) => setBulkQuotaValue(event.target.value)} />
        </label>
      </AdminDialog>

      <AdminDialog
        open={Boolean(recalcTarget)}
        title="确认重算配额"
        description={recalcTarget ? `将调用 doveadm 重新计算 ${recalcTarget.email} 的使用量。若环境未安装 doveadm，会返回明确降级提示。` : undefined}
        onClose={() => setRecalcTarget(null)}
        actions={(
          <>
            <button type="button" className="admin-button admin-button-secondary" onClick={() => setRecalcTarget(null)}>
              取消
            </button>
            <button
              type="button"
              className="admin-button admin-button-primary"
              disabled={!recalcTarget || recalcMutation.isPending}
              onClick={() => {
                if (!recalcTarget) return;
                recalcMutation.mutate(recalcTarget.id);
              }}
            >
              {recalcMutation.isPending ? '重算中...' : '确认重算'}
            </button>
          </>
        )}
      />
    </div>
  );
}
