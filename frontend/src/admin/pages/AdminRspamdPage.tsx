import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { fetchAdminRspamd, rotateAdminDomainDkim, updateAdminRspamdThresholds } from '../api';
import { AdminDialog, ResultMessage, SectionCard, StatusPill } from '../components/AdminHelpers';
import { AdminListTable } from '../components/AdminListTable';
import type { AdminRspamdDomainItem } from '../types';

type ThresholdForm = {
  reject: string;
  add_header: string;
  greylist: string;
};

const emptyThresholdForm: ThresholdForm = {
  reject: '15',
  add_header: '6',
  greylist: '4',
};

export function AdminRspamdPage() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<ThresholdForm>(emptyThresholdForm);
  const [rotateTarget, setRotateTarget] = useState<AdminRspamdDomainItem | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['admin-rspamd'],
    queryFn: fetchAdminRspamd,
  });

  useEffect(() => {
    if (!data?.thresholds?.thresholds) return;
    setForm({
      reject: String(data.thresholds.thresholds.reject),
      add_header: String(data.thresholds.thresholds.add_header),
      greylist: String(data.thresholds.thresholds.greylist),
    });
  }, [data?.thresholds]);

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['admin-rspamd'] });
  };

  const thresholdMutation = useMutation({
    mutationFn: updateAdminRspamdThresholds,
    onSuccess: async (payload) => {
      setSuccess(payload.detail);
      setError(null);
      await refresh();
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const rotateMutation = useMutation({
    mutationFn: ({ domainId, selector }: { domainId: string; selector?: string | null }) => rotateAdminDomainDkim(domainId, { selector }),
    onSuccess: async (payload) => {
      setRotateTarget(null);
      setSuccess(payload.detail);
      setError(null);
      await refresh();
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const columns = useMemo<ColumnDef<AdminRspamdDomainItem>[]>(() => [
    { accessorKey: 'name', header: '域名' },
    { accessorKey: 'spf_status', header: 'SPF', cell: (info) => <StatusPill status={String(info.getValue())} /> },
    { accessorKey: 'dmarc_status', header: 'DMARC', cell: (info) => <StatusPill status={String(info.getValue())} /> },
    { accessorKey: 'dkim_dns_status', header: 'DKIM DNS', cell: (info) => <StatusPill status={String(info.getValue())} /> },
    { accessorKey: 'dkim_local_status', header: 'DKIM Key', cell: (info) => <StatusPill status={String(info.getValue())} /> },
    {
      id: 'details',
      header: '详情',
      cell: ({ row }) => (
        <div className="admin-inline-actions">
          <button
            type="button"
            className="admin-button admin-button-secondary"
            onClick={() => setRotateTarget(row.original)}
          >
            轮换 DKIM
          </button>
        </div>
      ),
    },
  ], []);

  return (
    <div className="admin-section-stack">
      <SectionCard title="Rspamd 全局阈值" description="优先读取并更新 `actions.conf` 中的垃圾分阈值，开发环境缺失时返回明确降级状态。">
        <form
          className="admin-form-grid admin-form-grid--two"
          onSubmit={(event) => {
            event.preventDefault();
            thresholdMutation.mutate({
              reject: Number(form.reject || 0),
              add_header: Number(form.add_header || 0),
              greylist: Number(form.greylist || 0),
            });
          }}
        >
          <label>
            <span>reject</span>
            <input inputMode="decimal" value={form.reject} onChange={(event) => setForm((current) => ({ ...current, reject: event.target.value }))} />
          </label>
          <label>
            <span>add_header</span>
            <input inputMode="decimal" value={form.add_header} onChange={(event) => setForm((current) => ({ ...current, add_header: event.target.value }))} />
          </label>
          <label>
            <span>greylist</span>
            <input inputMode="decimal" value={form.greylist} onChange={(event) => setForm((current) => ({ ...current, greylist: event.target.value }))} />
          </label>
          <div className="admin-inline-actions">
            <button type="submit" className="admin-button admin-button-primary" disabled={thresholdMutation.isPending}>
              保存阈值
            </button>
            <StatusPill status={data?.thresholds.status || 'unavailable'} />
          </div>
        </form>
        <p className="admin-mono">{data?.thresholds.detail}</p>
        <ResultMessage error={error} success={success} />
      </SectionCard>

      <SectionCard
        title="域级 SPF / DMARC / DKIM"
        description="复用域名 DNS 检测结果，并补本地 DKIM 私钥读取状态。"
        actions={(
          <button type="button" className="admin-button admin-button-secondary" onClick={() => void refresh()}>
            刷新
          </button>
        )}
      >
        <AdminListTable
          data={data?.domains ?? []}
          emptyMessage={isLoading ? '加载中...' : '暂无 Rspamd 域名数据'}
          columns={columns}
        />
      </SectionCard>

      {data?.domains?.length ? (
        <SectionCard title="域级策略详情" description="展示域名的 SPF / DMARC 记录，以及当前 DKIM 公钥与私钥路径。">
          <div className="admin-info-grid">
            {data.domains.map((domain) => (
              <div key={domain.id} className="admin-info-card">
                <strong>{domain.name}</strong>
                <p>SPF：{domain.spf_detail}</p>
                <p>DMARC：{domain.dmarc_detail}</p>
                <p>DKIM DNS：{domain.dkim_dns_detail}</p>
                <p>DKIM Key：{domain.dkim_local_detail}</p>
                {domain.dkim_key_path ? <p className="admin-mono">{domain.dkim_key_path}</p> : null}
                {domain.dkim_public_key ? <p className="admin-mono">{domain.dkim_public_key}</p> : null}
              </div>
            ))}
          </div>
        </SectionCard>
      ) : null}

      <AdminDialog
        open={Boolean(rotateTarget)}
        title="确认轮换 DKIM 私钥"
        description={rotateTarget ? `将为 ${rotateTarget.name} 重新生成 DKIM 私钥。该操作会影响 DNS 配置联动，请确认。` : undefined}
        onClose={() => setRotateTarget(null)}
        actions={(
          <>
            <button type="button" className="admin-button admin-button-secondary" onClick={() => setRotateTarget(null)}>
              取消
            </button>
            <button
              type="button"
              className="admin-button admin-button-danger"
              disabled={!rotateTarget || rotateMutation.isPending}
              onClick={() => {
                if (!rotateTarget) return;
                rotateMutation.mutate({ domainId: rotateTarget.id, selector: rotateTarget.dkim_selector || undefined });
              }}
            >
              {rotateMutation.isPending ? '轮换中...' : '确认轮换'}
            </button>
          </>
        )}
      />
    </div>
  );
}
