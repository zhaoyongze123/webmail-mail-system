import { Navigate, Outlet, createBrowserRouter, type RouteObject } from 'react-router-dom';
import App from './App';
import { AdminLayout, AdminPageShell } from './admin/layout';
import { AdminAuthProvider, RequireAdminAuth, useAdminAuth } from './admin/auth';
import { AdminLoginPage } from './admin/pages/AdminLoginPage';
import { AdminDashboardPage } from './admin/pages/AdminDashboardPage';
import { AdminActionHistoryPage } from './admin/pages/AdminActionHistoryPage';
import { AdminDomainsPage } from './admin/pages/AdminDomainsPage';
import { AdminUsersPage } from './admin/pages/AdminUsersPage';
import { AdminAliasesPage } from './admin/pages/AdminAliasesPage';
import { AdminQuotasPage } from './admin/pages/AdminQuotasPage';
import { AdminRspamdPage } from './admin/pages/AdminRspamdPage';
import { AdminTlsPage } from './admin/pages/AdminTlsPage';
import { AdminQueuePage } from './admin/pages/AdminQueuePage';
import { AdminAuditLogsPage } from './admin/pages/AdminAuditLogsPage';
import { AdminSystemHealthPage } from './admin/pages/AdminSystemHealthPage';
import { AdminSecurityPage } from './admin/pages/AdminSecurityPage';
import { AdminLogsPage } from './admin/pages/AdminLogsPage';
import { AdminSystemConfigPage } from './admin/pages/AdminSystemConfigPage';

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
                  path: 'action-history',
                  element: (
                    <AdminPageShell title="操作历史" description="查看后台关键操作历史、状态与执行细节。">
                      <AdminActionHistoryPage />
                    </AdminPageShell>
                  ),
                },
                {
                  path: 'logs',
                  element: (
                    <AdminPageShell title="日志中心" description="按来源、级别和关键字查看后台日志，便于快速排障与追踪。">
                      <AdminLogsPage />
                    </AdminPageShell>
                  ),
                },
                {
                  path: 'system-config',
                  element: (
                    <AdminPageShell title="系统配置" description="预留主题、语言、队列和审计相关的统一配置入口。">
                      <AdminSystemConfigPage />
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
                  path: 'rspamd',
                  element: (
                    <AdminPageShell title="Rspamd 反垃圾" description="查看全局垃圾分阈值，并聚合域级 SPF / DMARC / DKIM 状态。">
                      <AdminRspamdPage />
                    </AdminPageShell>
                  ),
                },
                {
                  path: 'tls',
                  element: (
                    <AdminPageShell title="TLS 与证书" description="查看当前证书状态，并触发 Let’s Encrypt 续签。">
                      <AdminTlsPage />
                    </AdminPageShell>
                  ),
                },
                {
                  path: 'queue',
                  element: (
                    <AdminPageShell title="邮件队列" description="查看 Postfix 队列并执行 flush / 删除操作。">
                      <AdminQueuePage />
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
                    <AdminPageShell title="日志与监控" description="查看邮件服务状态、磁盘用量与最近错误日志。">
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
