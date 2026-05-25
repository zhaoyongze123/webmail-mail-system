import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import { Link, NavLink, Outlet } from 'react-router-dom';
import type { ReactNode } from 'react';
import { useAdminAuth, useAdminSignOut } from './auth';
import { ADMIN_LOCALE_STORAGE_KEY, setRuntimeLocale } from '../i18n/runtime';

const navGroups = [
  {
    title: '概览',
    items: [{ to: '/admin/dashboard', label: '控制台' }],
  },
  {
    title: '邮件资源',
    items: [
      { to: '/admin/domains', label: '域名' },
      { to: '/admin/users', label: '用户' },
      { to: '/admin/aliases', label: '别名' },
      { to: '/admin/quotas', label: '配额' },
    ],
  },
  {
    title: '邮件安全',
    items: [
      { to: '/admin/rspamd', label: '反垃圾' },
      { to: '/admin/tls', label: '证书' },
      { to: '/admin/queue', label: '队列' },
    ],
  },
  {
    title: '可观测性',
    items: [
      { to: '/admin/audit-logs', label: '审计' },
      { to: '/admin/action-history', label: '历史' },
      { to: '/admin/logs', label: '日志' },
      { to: '/admin/system-health', label: '监控' },
      { to: '/admin/security', label: '安全' },
    ],
  },
  {
    title: '平台设置',
    items: [{ to: '/admin/system-config', label: '系统配置' }],
  },
] as const;

const THEME_STORAGE_KEY = 'webmail-admin-theme';
const themeOptions = [
  { value: 'system', label: '跟随系统' },
  { value: 'light', label: '浅色' },
  { value: 'dark', label: '深色' },
] as const;

const localeOptions = [
  { value: 'zh-CN', label: '简体中文' },
  { value: 'en-US', label: 'English' },
] as const;

function resolveTheme(theme: string) {
  if (theme === 'light' || theme === 'dark') {
    return theme;
  }
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export function AdminLayout() {
  const { user } = useAdminAuth();
  const signOut = useAdminSignOut();
  const [theme, setTheme] = useState(() => window.localStorage.getItem(THEME_STORAGE_KEY) ?? 'system');
  const [locale, setLocale] = useState(() => window.localStorage.getItem(ADMIN_LOCALE_STORAGE_KEY) ?? 'zh-CN');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const settingsRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
    document.documentElement.dataset.theme = resolveTheme(theme);
  }, [theme]);

  useEffect(() => {
    window.localStorage.setItem(ADMIN_LOCALE_STORAGE_KEY, locale);
    setRuntimeLocale(locale);
  }, [locale]);

  useEffect(() => {
    if (!settingsOpen) {
      return undefined;
    }

    const handlePointerDown = (event: PointerEvent) => {
      if (!settingsRef.current?.contains(event.target as Node)) {
        setSettingsOpen(false);
      }
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setSettingsOpen(false);
      }
    };

    window.addEventListener('pointerdown', handlePointerDown);
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('pointerdown', handlePointerDown);
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [settingsOpen]);

  const themeLabel = useMemo(() => themeOptions.find((item) => item.value === theme)?.label ?? '跟随系统', [theme]);

  return (
    <div className="admin-shell">
      <aside className="admin-sidebar">
        <div className="admin-brand">
          <Link to="/admin/dashboard">邮件后台管理</Link>
          <p>同站运营后台一期</p>
        </div>
        <nav className="admin-nav" aria-label="管理菜单">
          {navGroups.map((group) => (
            <Fragment key={group.title}>
              <section className="admin-nav-group" aria-label={group.title}>
                <p className="admin-nav-group__title">{group.title}</p>
                <div className="admin-nav-group__items">
                  {group.items.map((item) => (
                    <NavLink
                      key={item.to}
                      to={item.to}
                      className={({ isActive }) => `admin-nav-link${isActive ? ' is-active' : ''}`}
                    >
                      {item.label}
                    </NavLink>
                  ))}
                </div>
              </section>
            </Fragment>
          ))}
        </nav>
        <div className="admin-sidebar-footer" ref={settingsRef}>
          <div className="admin-sidebar-footer__summary">
            <strong>{user?.name || '管理员'}</strong>
            <p>{user?.email || 'admin'} · {user?.totp_enabled ? '动态口令已启用' : '动态口令未启用'}</p>
          </div>
          <button
            type="button"
            className="admin-sidebar-settings-trigger"
            aria-label={settingsOpen ? '关闭系统设置' : '打开系统设置'}
            data-tooltip="系统设置"
            onClick={() => setSettingsOpen((value) => !value)}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M19.14 12.94c.04-.31.06-.63.06-.94s-.02-.63-.06-.94l2.03-1.58a.5.5 0 0 0 .12-.64l-1.92-3.32a.5.5 0 0 0-.6-.22l-2.39.96a7.24 7.24 0 0 0-1.63-.94l-.36-2.54a.5.5 0 0 0-.5-.42h-3.84a.5.5 0 0 0-.5.42l-.36 2.54c-.58.23-1.13.54-1.63.94l-2.39-.96a.5.5 0 0 0-.6.22L2.71 8.84a.5.5 0 0 0 .12.64l2.03 1.58c-.04.31-.06.63-.06.94s.02.63.06.94l-2.03 1.58a.5.5 0 0 0-.12.64l1.92 3.32a.5.5 0 0 0 .6.22l2.39-.96c.5.4 1.05.71 1.63.94l.36 2.54a.5.5 0 0 0 .5.42h3.84a.5.5 0 0 0 .5-.42l.36-2.54c.58-.23 1.13-.54 1.63-.94l2.39.96a.5.5 0 0 0 .6-.22l1.92-3.32a.5.5 0 0 0-.12-.64zM12 15.5A3.5 3.5 0 1 1 12 8.5a3.5 3.5 0 0 1 0 7z" />
            </svg>
          </button>
          {settingsOpen ? (
            <div className="admin-sidebar-settings-popover" role="dialog" aria-modal="false" aria-label="系统设置">
              <div className="admin-sidebar-settings-header">
                <strong>系统设置</strong>
                <p>统一管理后台主题、语言和会话操作。</p>
              </div>
              <div className="admin-sidebar-controls">
                <label className="admin-sidebar-control">
                  <span>主题</span>
                  <select value={theme} onChange={(event) => setTheme(event.target.value)}>
                    {themeOptions.map((item) => (
                      <option key={item.value} value={item.value}>{item.label}</option>
                    ))}
                  </select>
                </label>
                <label className="admin-sidebar-control">
                  <span>语言</span>
                  <select value={locale} onChange={(event) => setLocale(event.target.value)}>
                    {localeOptions.map((item) => (
                      <option key={item.value} value={item.value}>{item.label}</option>
                    ))}
                  </select>
                </label>
              </div>
              <p className="admin-sidebar-meta">当前主题：{themeLabel}</p>
              <button type="button" className="admin-button admin-button-secondary" onClick={() => void signOut()}>
                退出
              </button>
            </div>
          ) : null}
        </div>
      </aside>
      <main className="admin-main">
        <Outlet />
      </main>
    </div>
  );
}

export function AdminPageShell({ title, description, children }: { title: string; description: string; children: ReactNode }) {
  return (
    <section className="admin-page">
      <header className="admin-page-header">
        <div>
          <h1>{title}</h1>
          <p>{description}</p>
        </div>
      </header>
      <div className="admin-page-body">{children}</div>
    </section>
  );
}
