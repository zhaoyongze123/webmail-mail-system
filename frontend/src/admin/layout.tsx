import { useEffect, useMemo, useState } from 'react';
import { Link, NavLink, Outlet } from 'react-router-dom';
import type { ReactNode } from 'react';
import { useAdminAuth, useAdminSignOut } from './auth';

const navItems = [
  { to: '/admin/dashboard', label: '控制台' },
  { to: '/admin/action-history', label: '历史' },
  { to: '/admin/logs', label: '日志' },
  { to: '/admin/system-config', label: '系统配置' },
  { to: '/admin/domains', label: '域名' },
  { to: '/admin/users', label: '用户' },
  { to: '/admin/aliases', label: '别名' },
  { to: '/admin/quotas', label: '配额' },
  { to: '/admin/rspamd', label: '反垃圾' },
  { to: '/admin/tls', label: 'TLS' },
  { to: '/admin/queue', label: '队列' },
  { to: '/admin/audit-logs', label: '审计' },
  { to: '/admin/system-health', label: '监控' },
  { to: '/admin/security', label: '安全' },
];

const THEME_STORAGE_KEY = 'webmail-admin-theme';
const LOCALE_STORAGE_KEY = 'webmail-admin-locale';

const themeOptions = [
  { value: 'system', label: '跟随系统' },
  { value: 'light', label: '浅色' },
  { value: 'dark', label: '深色' },
] as const;

const localeOptions = [
  { value: 'zh-CN', label: '中文' },
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
  const [locale, setLocale] = useState(() => window.localStorage.getItem(LOCALE_STORAGE_KEY) ?? 'zh-CN');

  useEffect(() => {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
    document.documentElement.dataset.theme = resolveTheme(theme);
  }, [theme]);

  useEffect(() => {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, locale);
    document.documentElement.lang = locale;
  }, [locale]);

  const themeLabel = useMemo(() => themeOptions.find((item) => item.value === theme)?.label ?? '跟随系统', [theme]);

  return (
    <div className="admin-shell">
      <aside className="admin-sidebar">
        <div className="admin-brand">
          <Link to="/admin/dashboard">Webmail Admin</Link>
          <p>同站运营后台一期</p>
        </div>
        <nav className="admin-nav" aria-label="管理菜单">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => `admin-nav-link${isActive ? ' is-active' : ''}`}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="admin-sidebar-footer">
          <div>
            <strong>{user?.name || '管理员'}</strong>
            <p>{user?.email || 'admin'} · {user?.totp_enabled ? 'TOTP 已启用' : 'TOTP 未启用'}</p>
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
