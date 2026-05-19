import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { AdminAliasesPage } from './AdminAliasesPage';
import { AdminDomainsPage } from './AdminDomainsPage';
import { AdminQueuePage } from './AdminQueuePage';
import { AdminQuotasPage } from './AdminQuotasPage';
import { AdminRspamdPage } from './AdminRspamdPage';
import { AdminSystemHealthPage } from './AdminSystemHealthPage';
import { AdminTlsPage } from './AdminTlsPage';
import { AdminUsersPage } from './AdminUsersPage';

function renderPage(element: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  vi.stubGlobal('fetch', vi.fn(async (input: string | URL) => {
    const url = String(input);
    if (url.includes('/api/admin/domains/') && url.includes('/dns-check')) {
      return new Response(JSON.stringify({
        success: true,
        data: {
          domain: 'example.com',
          checked_at: 1747641600,
          status: 'warning',
          checks: [
            { key: 'mx', label: 'MX', status: 'ok', detail: '检测到 1 条 MX 记录', records: ['10 mail.example.com.'], backend: 'dig', command_result: { ok: true } },
            { key: 'spf', label: 'SPF', status: 'missing', detail: '未检测到 SPF 记录', records: [], backend: 'dig', command_result: { ok: true } },
          ],
        },
        error: null,
      }));
    }
    if (url.includes('/api/admin/domains/') && !url.includes('/dns-check')) {
      return new Response(JSON.stringify({
        success: true,
        data: {
          domain: {
            id: 'domain-1',
            name: 'example.com',
            quota_limit_mb: 1024,
            user_count: 2,
            alias_count: 1,
            used_quota_mb: 10,
            status: 'active',
            description: '用户 2 / 别名 1 / 已用 10 MB',
            created_at: '2026-05-19T00:00:00Z',
            updated_at: '2026-05-19T00:00:00Z',
          },
        },
        error: null,
      }));
    }
    if (url.includes('/api/admin/domains')) {
      return new Response(JSON.stringify({
        success: true,
        data: {
          page: 1,
          page_size: 10,
          total: 1,
          total_pages: 1,
          items: [{
            id: 'domain-1',
            name: 'example.com',
            quota_limit_mb: 1024,
            user_count: 2,
            alias_count: 1,
            used_quota_mb: 10,
            status: 'active',
            description: '用户 2 / 别名 1 / 已用 10 MB',
            created_at: '2026-05-19T00:00:00Z',
            updated_at: '2026-05-19T00:00:00Z',
          }],
        },
        error: null,
      }));
    }
    if (url.includes('/api/admin/users')) {
      return new Response(JSON.stringify({
        success: true,
        data: { page: 1, page_size: 10, total: 0, total_pages: 0, items: [] },
        error: null,
      }));
    }
    if (url.includes('/api/admin/aliases')) {
      return new Response(JSON.stringify({
        success: true,
        data: { page: 1, page_size: 10, total: 0, total_pages: 0, items: [] },
        error: null,
      }));
    }
    if (url.includes('/api/admin/quotas')) {
      return new Response(JSON.stringify({
        success: true,
        data: {
          items: [{
            id: 'quota-domain-1',
            name: 'example.com',
            status: 'healthy',
            updated_at: '2026-05-19T00:00:00Z',
            domain_id: 'domain-1',
            domain_name: 'example.com',
            default_quota_mb: 500,
            quota_limit_mb: 1024,
            used_quota_mb: 128,
            usage_percent: 12.5,
            usage_source: 'doveadm',
            warn_80_enabled: true,
            warn_90_enabled: true,
            warn_95_enabled: true,
          }],
          user_items: [{
            id: 'user-1',
            name: 'Alice',
            email: 'alice@example.com',
            status: 'active',
            updated_at: '2026-05-19T00:00:00Z',
            quota_mb: 500,
            used_quota_mb: 128,
            usage_percent: 25.6,
            usage_source: 'doveadm',
            quota_status: 'healthy',
            is_admin: false,
          }],
        },
        error: null,
      }));
    }
    if (url.includes('/api/admin/queue')) {
      return new Response(JSON.stringify({
        success: true,
        data: {
          status: 'ok',
          detail: '当前检测到 1 条队列邮件',
          items: [{
            id: 'Q1',
            queue_id: 'Q1',
            status: 'deferred',
            queue_name: 'deferred',
            sender: 'sender@example.com',
            recipients: ['target@example.com'],
            recipient_count: 1,
            message_size: 2048,
            arrival_time: 1747641600,
            created_at: 1747641600,
            name: 'Q1',
            description: 'sender@example.com -> target@example.com',
          }],
          summary: { total: 1, deferred: 1 },
          command_result: { ok: true },
        },
        error: null,
      }));
    }
    if (url.includes('/api/admin/system-health')) {
      return new Response(JSON.stringify({
        success: true,
        data: {
          checked_at: '2026-05-19T00:00:00Z',
          items: [
            { name: 'database', status: 'ok', detail: '数据库连接正常' },
            { name: 'redis', status: 'ok', detail: 'Redis 已配置' },
            { name: 'application', status: 'ok', detail: '应用健康检查时间 2026-05-19T00:00:00Z' },
            { name: 'postfix', status: 'ok', detail: 'systemctl 显示 postfix.service 正在运行' },
          ],
          services: [
            { name: 'postfix', status: 'ok', detail: 'systemctl 显示 postfix.service 正在运行' },
            { name: 'dovecot', status: 'warning', detail: '未检测到 dovecot 相关进程' },
            { name: 'rspamd', status: 'unavailable', detail: '当前环境未安装 pgrep，无法探测服务进程' },
          ],
          disks: [
            { name: '/', mount_point: '/', filesystem: '/dev/root', total_gb: 100, used_gb: 42, free_gb: 58, usage_percent: 42, status: 'ok', detail: '/ 已使用 42%' },
          ],
          logs: [
            { key: 'postfix', label: 'Postfix 错误日志', status: 'ok', detail: '已从 /var/log/mail.log 读取最近 2 行日志', source: 'file:/var/log/mail.log', line_count: 2, lines: ['postfix error line 1', 'postfix error line 2'] },
            { key: 'dovecot', label: 'Dovecot 错误日志', status: 'ok', detail: '已从 /var/log/dovecot.log 读取最近 1 行日志', source: 'file:/var/log/dovecot.log', line_count: 1, lines: ['dovecot warning'] },
          ],
        },
        error: null,
      }));
    }
    if (url.includes('/api/admin/tls')) {
      return new Response(JSON.stringify({
        success: true,
        data: {
          status: 'ok',
          detail: '已读取 1 份证书',
          items: [
            {
              name: 'mail.example.com',
              status: 'ok',
              detail: '证书将于 Jun 30 23:59:59 2026 GMT 到期',
              certificate_path: '/etc/letsencrypt/live/mail.example.com/fullchain.pem',
              expires_at: 'Jun 30 23:59:59 2026 GMT',
              domains: ['mail.example.com', 'imap.example.com'],
            },
          ],
        },
        error: null,
      }));
    }
    if (url.includes('/api/admin/rspamd')) {
      return new Response(JSON.stringify({
        success: true,
        data: {
          thresholds: {
            status: 'ok',
            detail: '已从 /etc/rspamd/local.d/actions.conf 读取 Rspamd 阈值',
            source: 'file:/etc/rspamd/local.d/actions.conf',
            thresholds: {
              reject: 15,
              add_header: 6,
              greylist: 4,
            },
          },
          domains: [
            {
              id: 'domain-1',
              name: 'example.com',
              spf_status: 'ok',
              spf_detail: '检测到 SPF 记录',
              spf_records: ['v=spf1 mx -all'],
              dmarc_status: 'missing',
              dmarc_detail: '未检测到 DMARC 记录',
              dmarc_records: [],
              dkim_dns_status: 'missing',
              dkim_dns_detail: '未检测到 TXT 记录',
              dkim_dns_records: [],
              dkim_selector: 'default',
              dkim_local_status: 'unavailable',
              dkim_local_detail: '未找到 DKIM 私钥文件 /var/lib/rspamd/dkim/example.com.default.key',
              dkim_key_path: '/var/lib/rspamd/dkim/example.com.default.key',
              dkim_key_exists: false,
              dkim_public_key: null,
            },
          ],
        },
        error: null,
      }));
    }
    return new Response(JSON.stringify({ success: true, data: { hasToken: false }, error: null }));
  }) as typeof fetch);

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        {element}
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('admin stage A pages', () => {
  it('用户页渲染新增与批量操作入口', async () => {
    renderPage(<AdminUsersPage />);
    expect(await screen.findByRole('heading', { name: '新增用户' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '创建用户' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '批量启用' })).toBeInTheDocument();
  });

  it('域名页渲染创建与批量状态入口', async () => {
    renderPage(<AdminDomainsPage />);
    expect(await screen.findByRole('heading', { name: '新增域名' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '创建域名' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '批量启用' })).toBeInTheDocument();
  });

  it('域名详情支持触发 DNS 检测', async () => {
    renderPage(<AdminDomainsPage />);
    fireEvent.click(await screen.findByRole('button', { name: '详情' }));
    fireEvent.click(await screen.findByRole('button', { name: 'DNS 检测' }));
    expect(await screen.findByText('DNS 总状态：warning')).toBeInTheDocument();
    expect(screen.getByText('检测到 1 条 MX 记录')).toBeInTheDocument();
  });

  it('别名页渲染多目标地址入口', async () => {
    renderPage(<AdminAliasesPage />);
    expect(await screen.findByRole('heading', { name: '新增别名' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '添加目标地址' })).toBeInTheDocument();
  });

  it('配额页渲染策略与批量更新入口', async () => {
    renderPage(<AdminQuotasPage />);
    expect(await screen.findByRole('heading', { name: '域级配额策略' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '保存策略' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '批量更新配额' })).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: '重算使用量' })).toBeInTheDocument();
  });

  it('队列页渲染刷新与 flush 入口', async () => {
    renderPage(<AdminQueuePage />);
    expect(await screen.findByRole('heading', { name: '队列摘要' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '刷新' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Flush 队列' })).toBeInTheDocument();
    expect(await screen.findByText('当前检测到 1 条队列邮件')).toBeInTheDocument();
  });

  it('日志与监控页渲染服务、磁盘和日志区域', async () => {
    renderPage(<AdminSystemHealthPage />);
    expect(await screen.findByRole('heading', { name: '运行概览' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '邮件服务状态' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '磁盘用量' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '错误日志' })).toBeInTheDocument();
    expect(await screen.findByText(/postfix error line 1/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Dovecot' }));
    expect(await screen.findByText(/dovecot warning/)).toBeInTheDocument();
  });

  it('Rspamd 页渲染阈值表单与 DKIM 轮换入口', async () => {
    renderPage(<AdminRspamdPage />);
    expect(await screen.findByRole('heading', { name: 'Rspamd 全局阈值' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '保存阈值' })).toBeInTheDocument();
    expect(await screen.findByText(/检测到 SPF 记录/)).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: '轮换 DKIM' })).toBeInTheDocument();
  });

  it('TLS 页渲染证书列表与续签入口', async () => {
    renderPage(<AdminTlsPage />);
    expect(await screen.findByRole('heading', { name: 'TLS 证书状态' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '触发续签' })).toBeInTheDocument();
    expect(await screen.findByText('/etc/letsencrypt/live/mail.example.com/fullchain.pem')).toBeInTheDocument();
    expect(screen.getByText(/Jun 30 23:59:59 2026 GMT/)).toBeInTheDocument();
  });
});
