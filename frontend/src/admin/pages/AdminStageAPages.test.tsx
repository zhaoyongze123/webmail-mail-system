import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { AdminAliasesPage } from './AdminAliasesPage';
import { AdminAuditLogsPage } from './AdminAuditLogsPage';
import { AdminDomainsPage } from './AdminDomainsPage';
import { AdminQueuePage } from './AdminQueuePage';
import { AdminQuotasPage } from './AdminQuotasPage';
import { AdminRspamdPage } from './AdminRspamdPage';
import { AdminLogsPage } from './AdminLogsPage';
import { AdminSystemConfigPage } from './AdminSystemConfigPage';
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
        data: {
          page: 1,
          page_size: 10,
          total: 0,
          total_pages: 0,
          items: [],
          capability: {
            status: 'ok',
            detail: '当前 vmail.db 已启用 aliases 表',
            writable: true,
            backend: 'sqlite_vmail',
          },
        },
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
          capability: {
            status: 'unavailable',
            detail: '当前 Dovecot 未启用 quota 命令',
            writable: true,
            usage_source: 'unavailable',
          },
        },
        error: null,
      }));
    }
    if (url.includes('/api/admin/queue/Q1')) {
      return new Response(JSON.stringify({
        success: true,
        data: {
          status: 'ok',
          detail: '已读取队列邮件 Q1 的正文',
          queue_id: 'Q1',
          content: 'From: sender@example.com\nTo: target@example.com\n\nHello queue body',
          command_result: { ok: true },
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
            recipient_details: [{
              address: 'target@example.com',
              delay_reason: 'host mx.example.com refused to talk to me: 421 temporary failure',
              delay_reason_display: '对方服务器临时拒绝建立连接，稍后会继续重试',
            }],
            recipient_count: 1,
            message_size: 2048,
            arrival_time: 1747641600,
            created_at: 1747641600,
            name: 'Q1',
            failure_reason: '对方服务器临时拒绝建立连接，稍后会继续重试',
            description: 'sender@example.com -> target@example.com',
          }],
          summary: { total: 1, deferred: 1 },
          command_result: { ok: true },
        },
        error: null,
      }));
    }
    if (url.includes('/api/admin/logs/export')) {
      return new Response(JSON.stringify({
        success: true,
        data: {
          format: 'csv',
          content: 'queue_id,created_at,updated_at,sender,recipients,message_size,status,status_detail,event_count\n"ABC123","2026-05-19T00:00:00Z","2026-05-19T00:01:00Z","sender@example.com","target@example.com","2048","sent","status=sent (250 2.0.0 Ok)","2"',
          media_type: 'text/csv',
          filename: 'mail-logs.csv',
        },
        error: null,
      }));
    }
    if (url.includes('/api/admin/logs')) {
      const parsedUrl = new URL(url, 'http://localhost');
      const protocol = parsedUrl.searchParams.get('domain_id') || 'smtp';
      if (protocol === 'imap') {
        return new Response(JSON.stringify({
          success: true,
          data: {
            summary: { total: 1, completed: 1, failed: 0, login: 0, warning: 0 },
            items: [
              {
                id: 'IMAPSESS1',
                queue_id: 'IMAPSESS1',
                source: 'imap',
                sender: 'alice@example.com',
                recipient: 'alice@example.com',
                recipients: ['alice@example.com'],
                message_size: 0,
                status: 'completed',
                status_detail: 'Disconnected: Logged out in=245 out=1834',
                created_at: '2026-05-19T09:00:00Z',
                updated_at: '2026-05-19T09:05:00Z',
                event_count: 2,
                protocol: 'imap',
                user: 'alice@example.com',
                auth_method: 'PLAIN',
                client_ip: '10.0.0.8',
                server_ip: '10.0.0.2',
                operation_summary: { in: 245, out: 1834 },
                events: [
                  {
                    time: '2026-05-19T09:00:00Z',
                    status: 'login',
                    summary: '账号 alice@example.com，状态 login，登录成功，客户端 10.0.0.8',
                    recipient: 'alice@example.com',
                    relay: '10.0.0.8',
                    delay: null,
                    dsn: 'PLAIN',
                    raw: 'May 19 09:00:00 mail dovecot: imap-login: Login: user=<alice@example.com>, method=PLAIN, rip=10.0.0.8, lip=10.0.0.2, mpid=201, TLS, session=<IMAPSESS1>',
                  },
                  {
                    time: '2026-05-19T09:05:00Z',
                    status: 'completed',
                    summary: '账号 alice@example.com，状态 completed，会话断开，客户端 10.0.0.8',
                    recipient: 'alice@example.com',
                    relay: '10.0.0.8',
                    delay: null,
                    dsn: 'PLAIN',
                    raw: 'May 19 09:05:00 mail dovecot: imap(alice@example.com)<201><IMAPSESS1>: Disconnected: Logged out in=245 out=1834',
                  },
                ],
                raw_lines: [],
              },
            ],
            total: 1,
            page: 1,
            page_size: 10,
            total_pages: 1,
            updated_at: '2026-05-19T09:05:00Z',
            detail: '已返回 1 条 IMAP 会话追踪',
          },
          error: null,
        }));
      }
      return new Response(JSON.stringify({
        success: true,
        data: {
          summary: { total: 1, sent: 1, deferred: 0, bounced: 0, rejected: 0 },
          items: [
            {
              id: 'ABC123',
              queue_id: 'ABC123',
              source: 'postfix',
              sender: 'sender@example.com',
              recipient: 'target@example.com',
              recipients: ['target@example.com'],
              message_size: 2048,
              status: 'sent',
              status_detail: 'status=sent (250 2.0.0 Ok)',
              created_at: '2026-05-19T00:00:00Z',
              updated_at: '2026-05-19T00:01:00Z',
              event_count: 2,
              events: [
                {
                  time: '2026-05-19T00:00:00Z',
                  status: 'queued',
                  summary: '收件人 target@example.com，状态 queued，cleanup 入队',
                  recipient: 'target@example.com',
                  relay: null,
                  delay: null,
                  dsn: null,
                  raw: 'May 19 00:00:00 mail postfix/cleanup[111]: ABC123: message-id=<demo@example.com>',
                },
                {
                  time: '2026-05-19T00:01:00Z',
                  status: 'sent',
                  summary: '收件人 target@example.com，状态 sent，投递点 mx.example.com[1.1.1.1]:25',
                  recipient: 'target@example.com',
                  relay: 'mx.example.com[1.1.1.1]:25',
                  delay: '1.2',
                  dsn: '2.0.0',
                  raw: 'May 19 00:01:00 mail postfix/smtp[222]: ABC123: to=<target@example.com>, relay=mx.example.com[1.1.1.1]:25, delay=1.2, dsn=2.0.0, status=sent (250 2.0.0 Ok)',
                },
              ],
              raw_lines: [],
            },
          ],
          total: 1,
          page: 1,
          page_size: 10,
          total_pages: 1,
          updated_at: '2026-05-19T01:00:00Z',
          detail: '已返回 1 条投递追踪',
        },
        error: null,
      }));
    }
    if (url.includes('/api/admin/audit-logs/export')) {
      return new Response(JSON.stringify({
        success: true,
        data: {
          format: 'csv',
          content: 'id,log_key,label,status,source,line_number,summary,raw\n"1","audit","audit","success","admin","0","admin.login","{}"',
          media_type: 'text/csv',
          filename: 'mail-logs.csv',
        },
        error: null,
      }));
    }
    if (url.includes('/api/admin/audit-logs')) {
      return new Response(JSON.stringify({
        success: true,
        data: {
          page: 1,
          page_size: 20,
          total: 2,
          total_pages: 1,
          items: [
            { id: 'audit-1', actor: 'admin', action: 'admin.login', target: 'session', event_type: 'auth.login', created_at: '2026-05-19T00:00:00Z' },
            { id: 'audit-2', actor: 'admin', action: 'admin.queue.delete', target: 'Q1', event_type: 'admin.queue.delete', created_at: '2026-05-19T01:00:00Z' },
          ],
        },
        error: null,
      }));
    }
    if (url.includes('/api/admin/system-config')) {
      return new Response(JSON.stringify({
        success: true,
        data: {
          theme: 'system',
          language: 'zh-CN',
          queue_auto_refresh_seconds: 15,
          queue_max_items: 100,
          audit_default_days: 30,
          log_retention_days: 14,
          updated_at: '2026-05-19T01:00:00Z',
          detail: '当前配置已加载',
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
    expect(await screen.findByText('DNS 总状态：告警')).toBeInTheDocument();
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

  it('队列页渲染刷新与投递入口', async () => {
    renderPage(<AdminQueuePage />);
    expect(await screen.findByRole('heading', { name: '队列摘要' })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: '状态' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '刷新' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '立即刷新投递' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '批量删除' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '按状态清空' })).toBeInTheDocument();
    expect(await screen.findByText('当前检测到 1 条队列邮件')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看' }));
    expect(await screen.findByText((_, element) => element?.tagName === 'PRE' && element.textContent?.includes('Hello queue body') === true)).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: '重投' }).length).toBeGreaterThanOrEqual(1);
  });

  it('日志页渲染投递追踪列表与详情展开', async () => {
    renderPage(<AdminLogsPage />);
    expect(await screen.findByRole('heading', { name: 'SMTP 投递追踪' })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: '关键字' })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: '状态' })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: '发件人' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '导出投递追踪' })).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'ABC123' })).toBeInTheDocument();
    expect(screen.getByText('sender@example.com')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'ABC123' }));
    expect(await screen.findByText(/投递点：mx.example.com/)).toBeInTheDocument();
  });

  it('日志页支持切换到 IMAP 会话追踪', async () => {
    renderPage(<AdminLogsPage />);
    expect(await screen.findByRole('heading', { name: 'SMTP 投递追踪' })).toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', { name: '协议' }), { target: { value: 'imap' } });
    expect(await screen.findByRole('heading', { name: 'IMAP 会话追踪' })).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'IMAPSESS1' })).toBeInTheDocument();
    expect(screen.getByText('alice@example.com')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'IMAPSESS1' }));
    expect(await screen.findByText(/认证方式：PLAIN/)).toBeInTheDocument();
  });

  it('审计页接入后端筛选与导出', async () => {
    renderPage(<AdminAuditLogsPage />);
    expect(await screen.findByRole('heading', { name: '审计筛选' })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: '关键字' })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: '成功状态' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '导出审计文件' })).toBeInTheDocument();
    expect(await screen.findByText('后台 · 登录')).toBeInTheDocument();
  });

  it('系统配置页渲染主题、语言与保存入口', async () => {
    renderPage(<AdminSystemConfigPage />);
    expect(await screen.findByRole('heading', { name: '主题与语言' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '保存配置' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '恢复默认' })).toBeInTheDocument();
  });

  it('日志与监控页渲染服务、磁盘和日志区域', async () => {
    renderPage(<AdminSystemHealthPage />);
    expect(await screen.findByRole('heading', { name: '运行概览' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '邮件服务状态' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '磁盘用量' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '错误日志' })).toBeInTheDocument();
    expect(await screen.findByText(/postfix error line 1/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '收信服务' }));
    expect(await screen.findByText(/dovecot warning/)).toBeInTheDocument();
  });

  it('Rspamd 页渲染阈值表单与 DKIM 轮换入口', async () => {
    renderPage(<AdminRspamdPage />);
    expect(await screen.findByRole('heading', { name: '反垃圾评分阈值' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '保存阈值' })).toBeInTheDocument();
    expect(await screen.findByText((content) => content.includes('发件人授权：') && content.includes('发件人授权记录'))).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: '轮换签名私钥' })).toBeInTheDocument();
  });

  it('TLS 页渲染证书列表与续签入口', async () => {
    renderPage(<AdminTlsPage />);
    expect(await screen.findByRole('heading', { name: '传输加密证书状态' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '触发续签' })).toBeInTheDocument();
    expect(await screen.findByText('/etc/letsencrypt/live/mail.example.com/fullchain.pem')).toBeInTheDocument();
    expect(screen.getByText((content) => content.includes('2026') && (content.includes('6/30') || content.includes('7/1')))).toBeInTheDocument();
  });
});
