import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { fetchAdminHealth } from '../api';
import { SectionCard, StatusPill, formatAdminDateTime, formatAdminTokenizedText, translateSystemText } from '../components/AdminHelpers';
import { AdminListTable } from '../components/AdminListTable';
import type { AdminDiskUsageItem, AdminHealthItem, AdminLogSnapshot } from '../types';

function formatCheckedAt(value?: string) {
  return formatAdminDateTime(value);
}

function healthStatusLabel(status: string) {
  if (status === 'ok' || status === 'healthy') return '正常';
  if (status === 'warning') return '告警';
  if (status === 'critical') return '严重';
  if (status === 'unavailable') return '不可用';
  if (status === 'error') return '异常';
  return status;
}

function serviceDisplayName(value?: string | null) {
  return formatAdminTokenizedText(value, '未命名服务');
}

export function AdminSystemHealthPage() {
  const queryClient = useQueryClient();
  const [activeLogKey, setActiveLogKey] = useState<'postfix' | 'dovecot'>('postfix');
  const { data, isLoading } = useQuery({ queryKey: ['admin-system-health'], queryFn: fetchAdminHealth });

  const serviceColumns = useMemo<ColumnDef<AdminHealthItem>[]>(() => [
    { accessorKey: 'name', header: '服务', cell: (info) => serviceDisplayName(String(info.getValue() || '')) },
    { accessorKey: 'status', header: '状态', cell: (info) => <StatusPill status={String(info.getValue())} label={healthStatusLabel(String(info.getValue()))} /> },
    { accessorKey: 'detail', header: '详情', cell: (info) => translateSystemText(String(info.getValue() || '—')) || '—' },
  ], []);

  const diskColumns = useMemo<ColumnDef<AdminDiskUsageItem>[]>(() => [
    { accessorKey: 'mount_point', header: '挂载点' },
    { accessorKey: 'filesystem', header: '文件系统' },
    { accessorKey: 'used_gb', header: '已用(GB)' },
    { accessorKey: 'free_gb', header: '可用(GB)' },
    { accessorKey: 'usage_percent', header: '使用率', cell: (info) => `${info.getValue<number>() ?? 0}%` },
    { accessorKey: 'status', header: '状态', cell: (info) => <StatusPill status={String(info.getValue())} label={healthStatusLabel(String(info.getValue()))} /> },
  ], []);

  const activeLog = useMemo<AdminLogSnapshot | null>(() => {
    return data?.logs.find((item) => item.key === activeLogKey) ?? data?.logs[0] ?? null;
  }, [activeLogKey, data?.logs]);

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['admin-system-health'] });
  };

  return (
    <div className="admin-section-stack">
      <SectionCard
        title="运行概览"
        description="聚合基础应用健康、邮件服务状态、磁盘用量与最近错误日志。所有系统状态优先读取真实命令或日志文件。"
        actions={(
          <div className="admin-inline-actions">
            <span className="admin-page-meta">最近刷新：{formatCheckedAt(data?.checked_at)}</span>
            <button type="button" className="admin-button admin-button-secondary" onClick={() => void refresh()}>
              刷新
            </button>
          </div>
        )}
      >
        <div className="admin-info-grid">
          {(data?.items ?? []).map((item) => (
            <div key={item.name} className="admin-info-card">
              <strong>{serviceDisplayName(item.name)}</strong>
              <StatusPill status={item.status} label={healthStatusLabel(item.status)} />
              <p>{translateSystemText(item.detail)}</p>
            </div>
          ))}
        </div>
      </SectionCard>

      <SectionCard title="邮件服务状态" description="重点关注投递服务、收信服务、反垃圾服务三个后台依赖。">
        <AdminListTable
          data={data?.services ?? []}
          emptyMessage={isLoading ? '加载中...' : '暂无服务状态'}
          columns={serviceColumns}
        />
      </SectionCard>

      <SectionCard title="磁盘用量" description="默认读取 `/` 与 `/var` 的磁盘使用情况，优先走 `df`，失败时回退 Python 标准库。">
        <AdminListTable
          data={data?.disks ?? []}
          emptyMessage={isLoading ? '加载中...' : '暂无磁盘数据'}
          columns={diskColumns}
        />
      </SectionCard>

      <SectionCard title="错误日志" description="读取投递服务与收信服务最近若干行错误日志，用于后台一期最小排障闭环。">
        <div className="admin-inline-actions">
          <button
            type="button"
            className={`admin-button ${activeLogKey === 'postfix' ? 'admin-button-primary' : 'admin-button-secondary'}`}
            onClick={() => setActiveLogKey('postfix')}
          >
            投递服务
          </button>
          <button
            type="button"
            className={`admin-button ${activeLogKey === 'dovecot' ? 'admin-button-primary' : 'admin-button-secondary'}`}
            onClick={() => setActiveLogKey('dovecot')}
          >
            收信服务
          </button>
        </div>
        {activeLog ? (
          <div className="admin-log-panel">
            <div className="admin-log-meta">
              <StatusPill status={activeLog.status} label={healthStatusLabel(activeLog.status)} />
              <span>{translateSystemText(activeLog.label)}</span>
              <span>来源：{activeLog.source || '未知'}</span>
              <span>行数：{activeLog.line_count}</span>
            </div>
            <p>{translateSystemText(activeLog.detail)}</p>
            <pre className="admin-log-output">{activeLog.lines.length ? activeLog.lines.join('\n') : '暂无日志内容'}</pre>
          </div>
        ) : (
          <p className="admin-empty-text">{isLoading ? '加载中...' : '暂无日志内容'}</p>
        )}
      </SectionCard>
    </div>
  );
}
