import { useEffect, useMemo } from 'react';
import type { ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import { formatLocaleDateTime, getRuntimeLocale, translateText } from '../../i18n/runtime';

const STATUS_LABELS: Record<string, { zh: string; en: string }> = {
  healthy: { zh: '正常', en: 'Healthy' },
  ok: { zh: '正常', en: 'Healthy' },
  warning: { zh: '告警', en: 'Warning' },
  unavailable: { zh: '不可用', en: 'Unavailable' },
  error: { zh: '异常', en: 'Error' },
  critical: { zh: '严重', en: 'Critical' },
  missing: { zh: '缺失', en: 'Missing' },
  info: { zh: '信息', en: 'Info' },
};

const SYSTEM_TEXT_REPLACEMENTS: Array<[RegExp, string]> = [
  [/SPF 记录/g, '发件人授权记录'],
  [/DMARC 记录/g, '域名策略记录'],
  [/DKIM 私钥文件/g, '域名签名私钥文件'],
  [/DKIM 私钥/g, '域名签名私钥'],
  [/DKIM 公钥/g, '域名签名公钥'],
  [/DKIM DNS/g, '域名签名解析'],
  [/Rspamd 阈值/g, '反垃圾评分阈值'],
  [/Postfix 错误日志/g, '投递服务错误日志'],
  [/Dovecot 错误日志/g, '收信服务错误日志'],
  [/\bWebmail\b/g, '邮件系统'],
  [/\bIMAP\b/g, '收信服务'],
  [/\bTOTP\b/g, '动态口令'],
  [/\bTLS\b/g, '传输加密'],
  [/\bRspamd\b/g, '反垃圾服务'],
  [/\bPostfix\b/g, '投递服务'],
  [/\bDovecot\b/g, '收信服务'],
  [/\bSPF\b/g, '发件人授权'],
  [/\bDMARC\b/g, '域名策略'],
  [/\bDKIM\b/g, '域名签名'],
  [/\bDNS\b/g, '域名解析'],
  [/\bSecret\b/g, '密钥'],
  [/\bURI\b/g, '配置链接'],
  [/\bcertbot\b/g, '证书续签命令'],
  [/\bopenssl\b/g, '证书解析工具'],
];

const ADMIN_TOKEN_LABELS: Record<string, string> = {
  admin: '后台',
  actor: '操作者',
  action: '动作',
  active: '启用',
  alias: '别名',
  aliases: '别名',
  application: '应用服务',
  audit: '审计',
  auth: '认证',
  backup: '备份',
  bulk: '批量',
  change: '修改',
  config: '配置',
  config_file: '配置文件',
  create: '创建',
  dashboard: '控制台',
  database: '数据库',
  delete: '删除',
  disable: '停用',
  dovecot: '收信服务',
  domain: '域名',
  domains: '域名',
  enable: '启用',
  event: '事件',
  export: '导出',
  history: '历史',
  import: '导入',
  list: '列表',
  log: '日志',
  logs: '日志',
  login: '登录',
  logout: '退出',
  mail: '邮件',
  mail_q: '邮件队列',
  mail_system: '邮件系统',
  me: '当前账号',
  overview: '概览',
  password: '密码',
  postfix: '投递服务',
  quota: '配额',
  quotas: '配额',
  queue: '队列',
  redis: '缓存服务',
  refresh: '刷新',
  reload: '重载',
  reset: '重置',
  restart: '重启',
  restore: '恢复',
  rspamd: '反垃圾服务',
  security: '安全',
  service: '服务',
  session: '会话',
  start: '启动',
  stop: '停止',
  system: '系统',
  system_config: '系统配置',
  target: '目标',
  tls: '证书',
  toggle: '切换',
  update: '更新',
  user: '用户',
  users: '用户',
};

export function translateSystemText(value?: string | null) {
  if (!value) {
    return '';
  }
  const normalized = SYSTEM_TEXT_REPLACEMENTS.reduce(
    (current, [pattern, replacement]) => current.replace(pattern, replacement),
    value,
  );
  return translateText(normalized);
}

export function formatAdminTokenizedText(value?: string | null, fallback = '—') {
  if (!value) {
    return translateText(fallback);
  }
  const normalized = value.trim();
  if (!normalized) {
    return translateText(fallback);
  }
  return normalized
    .split(/[._\-\s]+/)
    .filter(Boolean)
    .map((token) => ADMIN_TOKEN_LABELS[token] ?? translateSystemText(token))
    .join(' · ');
}

export function formatAdminActorText(value?: string | null, fallback = '—') {
  if (!value) {
    return translateText(fallback);
  }
  if (value === 'admin') {
    return translateText('管理员');
  }
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)) {
    return translateText('管理员');
  }
  return formatAdminTokenizedText(value, fallback);
}

export function formatAdminDateTime(value?: string | null, fallback = '—') {
  if (!value) {
    return translateText(fallback);
  }
  return formatLocaleDateTime(value, { hour12: false });
}

export function StatusPill({
  status,
  label,
}: {
  status: string;
  label?: string;
}) {
  const locale = getRuntimeLocale();
  const labelText = label ?? STATUS_LABELS[status]?.[locale === 'en-US' ? 'en' : 'zh'] ?? translateText(status);
  const className =
    status === 'active' || status === 'healthy' || status === 'ok'
      ? 'is-success'
      : status === 'warning'
        ? 'is-warning'
        : 'is-working';
  return <span className={`admin-status-pill ${className}`}>{labelText}</span>;
}

export function ResultMessage({ error, success }: { error?: string | null; success?: string | null }) {
  if (error) {
    return <p className="admin-error-text">{translateText(error)}</p>;
  }
  if (success) {
    return <p className="admin-success-text">{translateText(success)}</p>;
  }
  return null;
}

export function SectionCard({ title, description, children, actions }: { title: string; description?: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="admin-form-card">
      <div className="admin-form-card__header">
        <div>
          <h2>{title}</h2>
          {description ? <p>{description}</p> : null}
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}

export function AdminDialog({
  open,
  title,
  description,
  children,
  actions,
  onClose,
}: {
  open: boolean;
  title: string;
  description?: string;
  children?: ReactNode;
  actions?: ReactNode;
  onClose: () => void;
}) {
  if (!open) {
    return null;
  }

  return (
    <div className="settings-modal-overlay" onClick={onClose}>
      <div className="settings-modal admin-dialog" role="dialog" aria-modal="true" aria-label={title} onClick={(event) => event.stopPropagation()}>
        <div className="admin-form-card__header">
          <div>
            <h2>{title}</h2>
            {description ? <p>{description}</p> : null}
          </div>
          <button type="button" className="admin-button admin-button-secondary" onClick={onClose}>
            {translateText('关闭')}
          </button>
        </div>
        {children}
        {actions ? <div className="admin-inline-actions">{actions}</div> : null}
      </div>
    </div>
  );
}

export function useAdminListSearchParams(defaults: {
  q?: string;
  status?: string;
  domain_id?: string;
  page?: number;
}) {
  const [searchParams, setSearchParams] = useSearchParams();

  const state = useMemo(() => ({
    q: searchParams.get('q') ?? defaults.q ?? '',
    status: searchParams.get('status') ?? defaults.status ?? '',
    domain_id: searchParams.get('domain_id') ?? defaults.domain_id ?? '',
    page: Math.max(Number(searchParams.get('page') ?? defaults.page ?? 1), 1),
  }), [defaults.domain_id, defaults.page, defaults.q, defaults.status, searchParams]);

  useEffect(() => {
    const next = new URLSearchParams(searchParams);
    let changed = false;
    ([
      ['q', state.q],
      ['status', state.status],
      ['domain_id', state.domain_id],
      ['page', String(state.page)],
    ] as const).forEach(([key, value]) => {
      const normalizedValue = value || '';
      if ((next.get(key) ?? '') !== normalizedValue) {
        if (normalizedValue) {
          next.set(key, normalizedValue);
        } else {
          next.delete(key);
        }
        changed = true;
      }
    });
    if (changed) {
      setSearchParams(next, { replace: true });
    }
  }, [searchParams, setSearchParams, state.domain_id, state.page, state.q, state.status]);

  return {
    ...state,
    setQ(value: string) {
      const next = new URLSearchParams(searchParams);
      if (value) next.set('q', value);
      else next.delete('q');
      next.set('page', '1');
      setSearchParams(next, { replace: true });
    },
    setStatus(value: string) {
      const next = new URLSearchParams(searchParams);
      if (value) next.set('status', value);
      else next.delete('status');
      next.set('page', '1');
      setSearchParams(next, { replace: true });
    },
    setDomainId(value: string) {
      const next = new URLSearchParams(searchParams);
      if (value) next.set('domain_id', value);
      else next.delete('domain_id');
      next.set('page', '1');
      setSearchParams(next, { replace: true });
    },
    setPage(value: number) {
      const next = new URLSearchParams(searchParams);
      next.set('page', String(Math.max(value, 1)));
      setSearchParams(next, { replace: true });
    },
  };
}
