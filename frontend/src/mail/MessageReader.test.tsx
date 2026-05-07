import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import MessageReader from './MessageReader';

const mockFetch = vi.fn();

function mockApiResponse(body: unknown, ok = true, status = ok ? 200 : 500) {
  return {
    ok,
    status,
    json: async () => body,
  } as Response;
}

describe('MessageReader', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', mockFetch);
    mockFetch.mockReset();
  });

  it('未选中邮件时展示占位', () => {
    render(<MessageReader folder="INBOX" uid={null} />);

    expect(screen.getByRole('heading', { name: '选择一封邮件' })).not.toBeNull();
    expect(screen.getByText('从左侧列表选择邮件后，正文和附件会显示在这里。')).not.toBeNull();
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('加载并展示邮件详情和纯文本正文', async () => {
    const onReply = vi.fn();
    const onForward = vi.fn();
    const user = userEvent.setup();
    mockFetch.mockResolvedValue(
      mockApiResponse({
        success: true,
        data: {
          uid: 42,
          folder: 'INBOX',
          subject: '项目周报',
          from: { name: '张三', email: 'zhangsan@example.com' },
          to: [{ email: 'lisi@example.com' }],
          cc: [{ email: 'wangwu@example.com' }],
          date: '2026-05-07T02:30:00Z',
          text_body: '第一行\n第二行',
          attachments: [],
        },
        error: null,
      }),
    );

    render(<MessageReader folder="INBOX" uid={42} onReply={onReply} onForward={onForward} />);

    expect(await screen.findByRole('heading', { name: '项目周报' })).not.toBeNull();
    expect(screen.getByText('张三 <zhangsan@example.com>')).not.toBeNull();
    expect(screen.getByText('lisi@example.com')).not.toBeNull();
    expect(screen.getByText('wangwu@example.com')).not.toBeNull();
    expect(screen.getByTestId('message-text-body').textContent).toBe('第一行\n第二行');

    await user.click(screen.getByRole('button', { name: '回复' }));
    await user.click(screen.getByRole('button', { name: '转发' }));
    expect(onReply).toHaveBeenCalledWith(expect.objectContaining({ uid: 42, subject: '项目周报' }));
    expect(onForward).toHaveBeenCalledWith(expect.objectContaining({ uid: 42, subject: '项目周报' }));
  });

  it('HTML 正文会移除脚本、事件处理和 javascript 链接', async () => {
    mockFetch.mockResolvedValue(
      mockApiResponse({
        success: true,
        data: {
          uid: 7,
          folder: 'INBOX',
          subject: '安全样例',
          from: 'security@example.com',
          to: ['user@example.com'],
          html_body:
            '<p onclick="window.__xss=1">安全内容</p><script>window.__xss=1</script><a href="javascript:alert(1)">危险链接</a><img src="javascript:alert(2)" onerror="window.__xss=1">',
          attachments: [],
        },
        error: null,
      }),
    );

    render(<MessageReader folder="INBOX" uid={7} />);

    const body = await screen.findByTestId('message-html-body');
    const html = body.innerHTML.toLowerCase();
    expect(html).toContain('安全内容');
    expect(html).not.toContain('<script');
    expect(html).not.toContain('onclick');
    expect(html).not.toContain('onerror');
    expect(html).not.toContain('javascript:');
    expect((window as Window & { __xss?: number }).__xss).toBeUndefined();
  });

  it('附件下载链接使用后端附件接口', async () => {
    mockFetch.mockResolvedValue(
      mockApiResponse({
        success: true,
        data: {
          uid: 99,
          folder: 'Archive 2026',
          subject: '附件邮件',
          from: 'sender@example.com',
          to: ['user@example.com'],
          text_body: '见附件',
          attachments: [
            {
              id: 'part/1',
              filename: '报价单.pdf',
              content_type: 'application/pdf',
              size: 2048,
            },
          ],
        },
        error: null,
      }),
    );

    render(<MessageReader folder="Archive 2026" uid={99} />);

    expect(await screen.findByText('报价单.pdf')).not.toBeNull();
    expect(screen.getByText('application/pdf · 2.0 KB')).not.toBeNull();

    const link = screen.getByRole('link', { name: '下载' }) as HTMLAnchorElement;
    expect(link.getAttribute('href')).toBe('/api/folders/Archive%202026/messages/99/attachments/part%2F1');
    expect(link.getAttribute('download')).toBe('报价单.pdf');
  });

  it('会话过期时触发回调并展示错误', async () => {
    const onSessionExpired = vi.fn();
    mockFetch.mockResolvedValue(
      mockApiResponse(
        {
          success: false,
          data: null,
          error: { code: 'AUTH_SESSION_EXPIRED', message: '登录已过期，请重新登录' },
        },
        false,
        401,
      ),
    );

    render(<MessageReader folder="INBOX" uid={1} onSessionExpired={onSessionExpired} />);

    await waitFor(() => {
      expect(onSessionExpired).toHaveBeenCalledTimes(1);
    });
    expect(screen.getByRole('alert').textContent).toContain('登录已过期，请重新登录');
  });
});
