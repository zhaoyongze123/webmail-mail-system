import { Link, NavLink, Outlet } from 'react-router-dom';
import type { ReactNode } from 'react';
import { useAdminAuth, useAdminSignOut } from './auth';

const navItems = [
  { to: '/admin/dashboard', label: '控制台' },
  { to: '/admin/domains', label: '域名' },
  { to: '/admin/users', label: '用户' },
  { to: '/admin/aliases', label: '别名' },
  { to: '/admin/quotas', label: '配额' },
  { to: '/admin/audit-logs', label: '审计' },
  { to: '/admin/system-health', label: '健康' },
  { to: '/admin/security', label: '安全' },
];

export function AdminLayout() {
  const { user } = useAdminAuth();
  const signOut = useAdminSignOut();

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
