import { Navigate, Outlet, createBrowserRouter, type RouteObject } from 'react-router-dom';
import App from './App';
import { AdminLayout, AdminPageShell } from './admin/layout';
import { AdminAuthProvider, RequireAdminAuth, useAdminAuth } from './admin/auth';
import { AdminLoginPage } from './admin/pages/AdminLoginPage';
import { AdminDashboardPage } from './admin/pages/AdminDashboardPage';
import { AdminDomainsPage } from './admin/pages/AdminDomainsPage';
import { AdminUsersPage } from './admin/pages/AdminUsersPage';
import { AdminAliasesPage } from './admin/pages/AdminAliasesPage';
import { AdminQuotasPage } from './admin/pages/AdminQuotasPage';
import { AdminAuditLogsPage } from './admin/pages/AdminAuditLogsPage';
import { AdminSystemHealthPage } from './admin/pages/AdminSystemHealthPage';
import { AdminSecurityPage } from './admin/pages/AdminSecurityPage';

function AdminIndexRedirect() {
  const { hasToken } = useAdminAuth();
  return <Navigate to={hasToken ? '/admin/dashboard' : '/admin/login'} replace />;
}

function AdminRoot() {
  return <Outlet />;
}

export function buildRoutes(): RouteObject[] {
  return [
    {
      path: '/',
      element: <App />,
    },
    {
      path: '/admin',
      element: <AdminRoot />,
      children: [
        { index: true, element: <AdminIndexRedirect /> },
        { path: 'login', element: <AdminLoginPage /> },
        {
          element: <RequireAdminAuth />,
          children: [
            {
              element: <AdminLayout />,
              children: [
                {
                  path: 'dashboard',
                  element: (
                    <AdminPageShell title="控制台" description="查看管理后台关键概览与最近运行状态。">
                      <AdminDashboardPage />
                    </AdminPageShell>
                  ),
                },
                {
                  path: 'domains',
                  element: (
                    <AdminPageShell title="域名管理" description="维护收发信域名与基础路由信息。">
                      <AdminDomainsPage />
                    </AdminPageShell>
                  ),
                },
                {
                  path: 'users',
                  element: (
                    <AdminPageShell title="用户管理" description="查看管理员和邮箱用户的基础信息。">
                      <AdminUsersPage />
                    </AdminPageShell>
                  ),
                },
                {
                  path: 'aliases',
                  element: (
                    <AdminPageShell title="别名管理" description="配置邮箱别名与转发关系。">
                      <AdminAliasesPage />
                    </AdminPageShell>
                  ),
                },
                {
                  path: 'quotas',
                  element: (
                    <AdminPageShell title="配额管理" description="展示账号容量与资源阈值。">
                      <AdminQuotasPage />
                    </AdminPageShell>
                  ),
                },
                {
                  path: 'audit-logs',
                  element: (
                    <AdminPageShell title="审计日志" description="浏览后台关键操作记录。">
                      <AdminAuditLogsPage />
                    </AdminPageShell>
                  ),
                },
                {
                  path: 'system-health',
                  element: (
                    <AdminPageShell title="系统健康" description="查看服务可用性与连接状态。">
                      <AdminSystemHealthPage />
                    </AdminPageShell>
                  ),
                },
                {
                  path: 'security',
                  element: (
                    <AdminPageShell title="安全设置" description="修改管理员密码，并管理 TOTP 二次验证入口。">
                      <AdminSecurityPage />
                    </AdminPageShell>
                  ),
                },
              ],
            },
          ],
        },
      ],
    },
  ];
}

export function createAppRouter() {
  return createBrowserRouter(buildRoutes());
}

export { AdminAuthProvider };
