import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { fetchAdminRspamd, rotateAdminDomainDkim, updateAdminRspamdThresholds } from '../api';
import { AdminDialog, ResultMessage, SectionCard, StatusPill, translateSystemText } from '../components/AdminHelpers';
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
    { accessorKey: 'spf_status', header: '发件人授权', cell: (info) => <StatusPill status={String(info.getValue())} /> },
    { accessorKey: 'dmarc_status', header: '域名策略', cell: (info) => <StatusPill status={String(info.getValue())} /> },
    { accessorKey: 'dkim_dns_status', header: '签名公钥解析', cell: (info) => <StatusPill status={String(info.getValue())} /> },
    { accessorKey: 'dkim_local_status', header: '签名私钥', cell: (info) => <StatusPill status={String(info.getValue())} /> },
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
            轮换签名私钥
          </button>
        </div>
      ),
    },
  ], []);

  return (
    <div className="admin-section-stack">
      <SectionCard title="反垃圾评分阈值" description="优先读取并更新垃圾评分阈值配置，开发环境缺失时返回明确降级状态。">
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
            <span>拒收阈值</span>
            <input inputMode="decimal" value={form.reject} onChange={(event) => setForm((current) => ({ ...current, reject: event.target.value }))} />
          </label>
          <label>
            <span>加头阈值</span>
            <input inputMode="decimal" value={form.add_header} onChange={(event) => setForm((current) => ({ ...current, add_header: event.target.value }))} />
          </label>
          <label>
            <span>灰名单阈值</span>
            <input inputMode="decimal" value={form.greylist} onChange={(event) => setForm((current) => ({ ...current, greylist: event.target.value }))} />
          </label>
          <div className="admin-inline-actions">
            <button type="submit" className="admin-button admin-button-primary" disabled={thresholdMutation.isPending}>
              保存阈值
            </button>
            <StatusPill status={data?.thresholds.status || 'unavailable'} />
          </div>
        </form>
        <p className="admin-mono">{translateSystemText(data?.thresholds.detail)}</p>
        <ResultMessage error={error} success={success} />
      </SectionCard>

      <SectionCard
        title="域级发信认证状态"
        description="复用域名解析检测结果，并补本地签名私钥读取状态。"
        actions={(
          <button type="button" className="admin-button admin-button-secondary" onClick={() => void refresh()}>
            刷新
          </button>
        )}
      >
        <AdminListTable
          data={data?.domains ?? []}
          emptyMessage={isLoading ? '加载中...' : '暂无反垃圾域名数据'}
          columns={columns}
        />
      </SectionCard>

      {data?.domains?.length ? (
        <SectionCard title="域级策略详情" description="展示域名的发件人授权、域名策略记录，以及当前签名公钥与私钥路径。">
          <div className="admin-info-grid">
            {data.domains.map((domain) => (
              <div key={domain.id} className="admin-info-card">
                <strong>{domain.name}</strong>
                <p>发件人授权：{translateSystemText(domain.spf_detail)}</p>
                <p>域名策略：{translateSystemText(domain.dmarc_detail)}</p>
                <p>签名公钥解析：{translateSystemText(domain.dkim_dns_detail)}</p>
                <p>签名私钥：{translateSystemText(domain.dkim_local_detail)}</p>
                {domain.dkim_key_path ? <p className="admin-mono">{domain.dkim_key_path}</p> : null}
                {domain.dkim_public_key ? <p className="admin-mono">{domain.dkim_public_key}</p> : null}
              </div>
            ))}
          </div>
        </SectionCard>
      ) : null}

      <AdminDialog
        open={Boolean(rotateTarget)}
        title="确认轮换签名私钥"
        description={rotateTarget ? `将为 ${rotateTarget.name} 重新生成域名签名私钥。该操作会影响域名解析联动，请确认。` : undefined}
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
