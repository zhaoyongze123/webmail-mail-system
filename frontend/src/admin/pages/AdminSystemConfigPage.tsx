import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { backupAdminMailSystemConfig, fetchAdminMailSystemConfigPreview, fetchAdminSystemConfig, postaliasAdminMailSystemConfig, postmapAdminMailSystemConfig, reloadAdminDovecotService, reloadAdminPostfixService, restoreAdminMailSystemConfig, runAdminServiceAction, updateAdminSystemConfig } from '../api';
import { ResultMessage, SectionCard, StatusPill } from '../components/AdminHelpers';
import type { AdminMailSystemCommandResult, AdminMailSystemConfigPreview, AdminSystemConfigPayload } from '../types';

const defaultConfig: AdminSystemConfigPayload = {
  theme: 'system',
  language: 'zh-CN',
  queue_auto_refresh_seconds: 15,
  queue_max_items: 100,
  audit_default_days: 30,
  log_retention_days: 14,
};

function CommandResultCard({ title, result }: { title: string; result?: AdminMailSystemCommandResult | null }) {
  if (!result) return null;
  return (
    <div className="admin-info-card">
      <strong>{title}</strong>
      <StatusPill status={result.status} />
      <p>{result.detail}</p>
      {result.backup_path ? <p>{result.backup_path}</p> : null}
      {result.path ? <p>{result.path}</p> : null}
    </div>
  );
}

function ConfigPreviewCard({
  title,
  preview,
}: {
  title: string;
  preview?: AdminMailSystemConfigPreview[keyof Pick<AdminMailSystemConfigPreview, 'postfix' | 'dovecot'>] | null;
}) {
  const resolvedPreview = preview ?? { status: 'unavailable', detail: '暂无配置预览', content: '' };
  return (
    <div className="admin-info-card">
      <strong>{title}</strong>
      <StatusPill status={resolvedPreview.status} />
      <p>{resolvedPreview.detail}</p>
      <pre className="admin-log-output">{resolvedPreview.content || '暂无配置预览'}</pre>
    </div>
  );
}

export function AdminSystemConfigPage() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<AdminSystemConfigPayload>(defaultConfig);
  const [restoreBackupPath, setRestoreBackupPath] = useState('');
  const [restoreTargetPath, setRestoreTargetPath] = useState('/etc/postfix/main.cf');
  const [serviceName, setServiceName] = useState('postfix');
  const [serviceAction, setServiceAction] = useState<'start' | 'stop' | 'restart'>('restart');
  const [success, setSuccess] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [commandResult, setCommandResult] = useState<AdminMailSystemCommandResult | null>(null);

  const systemConfigQuery = useQuery({
    queryKey: ['admin-system-config'],
    queryFn: fetchAdminSystemConfig,
  });
  const mailSystemConfigQuery = useQuery({
    queryKey: ['admin-mail-system-config-preview'],
    queryFn: fetchAdminMailSystemConfigPreview,
  });

  useEffect(() => {
    if (!systemConfigQuery.data) return;
    setForm({
      theme: systemConfigQuery.data.theme,
      language: systemConfigQuery.data.language,
      queue_auto_refresh_seconds: systemConfigQuery.data.queue_auto_refresh_seconds,
      queue_max_items: systemConfigQuery.data.queue_max_items,
      audit_default_days: systemConfigQuery.data.audit_default_days,
      log_retention_days: systemConfigQuery.data.log_retention_days,
    });
  }, [systemConfigQuery.data]);

  const configMutation = useMutation({
    mutationFn: updateAdminSystemConfig,
    onSuccess: async (payload) => {
      setSuccess(payload.detail);
      setError(null);
      await queryClient.invalidateQueries({ queryKey: ['admin-system-config'] });
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const backupMutation = useMutation({
    mutationFn: backupAdminMailSystemConfig,
    onSuccess: async (payload) => {
      setCommandResult(payload);
      setSuccess(payload.detail);
      setError(null);
      await queryClient.invalidateQueries({ queryKey: ['admin-mail-system-config-preview'] });
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const restoreMutation = useMutation({
    mutationFn: restoreAdminMailSystemConfig,
    onSuccess: async (payload) => {
      setCommandResult(payload);
      setSuccess(payload.detail);
      setError(null);
      await queryClient.invalidateQueries({ queryKey: ['admin-mail-system-config-preview'] });
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const postmapMutation = useMutation({
    mutationFn: postmapAdminMailSystemConfig,
    onSuccess: (payload) => {
      setCommandResult(payload);
      setSuccess(payload.detail);
      setError(null);
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const postaliasMutation = useMutation({
    mutationFn: postaliasAdminMailSystemConfig,
    onSuccess: (payload) => {
      setCommandResult(payload);
      setSuccess(payload.detail);
      setError(null);
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const postfixReloadMutation = useMutation({
    mutationFn: reloadAdminPostfixService,
    onSuccess: (payload) => {
      setCommandResult(payload);
      setSuccess(payload.detail);
      setError(null);
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const dovecotReloadMutation = useMutation({
    mutationFn: reloadAdminDovecotService,
    onSuccess: (payload) => {
      setCommandResult(payload);
      setSuccess(payload.detail);
      setError(null);
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const serviceActionMutation = useMutation({
    mutationFn: runAdminServiceAction,
    onSuccess: (payload) => {
      setCommandResult(payload);
      setSuccess(payload.detail);
      setError(null);
    },
    onError: (err) => {
      setSuccess(null);
      setError((err as Error).message);
    },
  });

  const mailSystemPreview = mailSystemConfigQuery.data && 'postfix' in mailSystemConfigQuery.data && 'dovecot' in mailSystemConfigQuery.data
    ? mailSystemConfigQuery.data
    : null;

  const previewCards = useMemo(() => mailSystemPreview ? [
    <ConfigPreviewCard key="postfix" title="Postfix 配置预览" preview={mailSystemPreview.postfix} />,
    <ConfigPreviewCard key="dovecot" title="Dovecot 配置预览" preview={mailSystemPreview.dovecot} />,
  ] : [
    <ConfigPreviewCard key="postfix" title="Postfix 配置预览" />,
    <ConfigPreviewCard key="dovecot" title="Dovecot 配置预览" />,
  ], [mailSystemPreview]);

  return (
    <div className="admin-section-stack">
      <SectionCard
        title="主题与语言"
        description="保留后台主题、语言与队列配置。"
        actions={(
          <div className="admin-inline-actions">
            <button type="button" className="admin-button admin-button-secondary" onClick={() => setForm(defaultConfig)}>
              恢复默认
            </button>
            <button
              type="button"
              className="admin-button admin-button-primary"
              disabled={configMutation.isPending}
              onClick={() => configMutation.mutate(form)}
            >
              {configMutation.isPending ? '保存中...' : '保存配置'}
            </button>
          </div>
        )}
      >
        <div className="admin-form-grid admin-form-grid--two">
          <label>
            <span>主题</span>
            <select value={form.theme} onChange={(event) => setForm((current) => ({ ...current, theme: event.target.value as AdminSystemConfigPayload['theme'] }))}>
              <option value="system">跟随系统</option>
              <option value="light">浅色</option>
              <option value="dark">深色</option>
            </select>
          </label>
          <label>
            <span>语言</span>
            <select value={form.language} onChange={(event) => setForm((current) => ({ ...current, language: event.target.value as AdminSystemConfigPayload['language'] }))}>
              <option value="zh-CN">中文</option>
              <option value="en-US">English</option>
            </select>
          </label>
          <label>
            <span>队列自动刷新（秒）</span>
            <input type="number" min={5} value={form.queue_auto_refresh_seconds} onChange={(event) => setForm((current) => ({ ...current, queue_auto_refresh_seconds: Number(event.target.value || 0) }))} />
          </label>
          <label>
            <span>队列展示上限</span>
            <input type="number" min={10} value={form.queue_max_items} onChange={(event) => setForm((current) => ({ ...current, queue_max_items: Number(event.target.value || 0) }))} />
          </label>
          <label>
            <span>审计默认天数</span>
            <input type="number" min={1} value={form.audit_default_days} onChange={(event) => setForm((current) => ({ ...current, audit_default_days: Number(event.target.value || 0) }))} />
          </label>
          <label>
            <span>日志保留天数</span>
            <input type="number" min={1} value={form.log_retention_days} onChange={(event) => setForm((current) => ({ ...current, log_retention_days: Number(event.target.value || 0) }))} />
          </label>
        </div>
        <ResultMessage error={error} success={success} />
        <p className="admin-page-meta">当前配置更新时间：{systemConfigQuery.data?.updated_at || '—'} · {systemConfigQuery.data?.detail || '等待后端配置接口接入'}</p>
      </SectionCard>

      <SectionCard
        title="系统配置"
        description="联通邮件系统配置预览、备份、恢复和服务动作。"
        actions={(
          <div className="admin-inline-actions">
            <button type="button" className="admin-button admin-button-secondary" onClick={() => void queryClient.invalidateQueries({ queryKey: ['admin-mail-system-config-preview'] })}>
              刷新预览
            </button>
            <button type="button" className="admin-button admin-button-secondary" onClick={() => backupMutation.mutate('/etc/postfix/main.cf')}>
              备份 Postfix
            </button>
          </div>
        )}
      >
        <div className="admin-info-grid">
          {previewCards.length ? previewCards : <div className="admin-info-card"><strong>邮件系统配置预览</strong><p>暂无可用预览数据</p></div>}
        </div>
        <div className="admin-form-grid admin-form-grid--two">
          <label>
            <span>恢复备份路径</span>
            <input value={restoreBackupPath} onChange={(event) => setRestoreBackupPath(event.target.value)} placeholder="/var/backups/webmail-admin/main.cf.20260101010101.bak" />
          </label>
          <label>
            <span>恢复目标路径</span>
            <input value={restoreTargetPath} onChange={(event) => setRestoreTargetPath(event.target.value)} placeholder="/etc/postfix/main.cf" />
          </label>
        </div>
        <div className="admin-inline-actions">
          <button type="button" className="admin-button admin-button-secondary" disabled={!restoreBackupPath} onClick={() => restoreMutation.mutate({ backup_path: restoreBackupPath, target_path: restoreTargetPath })}>
            恢复配置
          </button>
          <button type="button" className="admin-button admin-button-secondary" onClick={() => postmapMutation.mutate()}>
            postmap
          </button>
          <button type="button" className="admin-button admin-button-secondary" onClick={() => postaliasMutation.mutate()}>
            postalias
          </button>
          <button type="button" className="admin-button admin-button-primary" onClick={() => postfixReloadMutation.mutate()}>
            postfix reload
          </button>
          <button type="button" className="admin-button admin-button-primary" onClick={() => dovecotReloadMutation.mutate()}>
            dovecot reload
          </button>
        </div>
      </SectionCard>

      <SectionCard
        title="服务动作"
        description="最小闭环支持 service action，直接对接后端 /api/admin/system/service-action。"
      >
        <div className="admin-form-grid admin-form-grid--two">
          <label>
            <span>服务名</span>
            <input value={serviceName} onChange={(event) => setServiceName(event.target.value)} placeholder="postfix / dovecot / rspamd" />
          </label>
          <label>
            <span>动作</span>
            <select value={serviceAction} onChange={(event) => setServiceAction(event.target.value as 'start' | 'stop' | 'restart')}>
              <option value="start">start</option>
              <option value="stop">stop</option>
              <option value="restart">restart</option>
            </select>
          </label>
        </div>
        <div className="admin-inline-actions">
          <button
            type="button"
            className="admin-button admin-button-primary"
            onClick={() => serviceActionMutation.mutate({ service: serviceName, action: serviceAction })}
          >
            执行服务动作
          </button>
        </div>
        <div className="admin-info-grid">
          <CommandResultCard title="最近备份" result={commandResult?.backup_path ? commandResult : null} />
          <CommandResultCard title="最近命令结果" result={commandResult} />
        </div>
      </SectionCard>
    </div>
  );
}
