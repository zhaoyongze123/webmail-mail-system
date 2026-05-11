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

  it('HTML 正文会保留富文本样式并净化危险内容', async () => {
    mockFetch.mockResolvedValue(
      mockApiResponse({
        success: true,
        data: {
          uid: 7,
          folder: 'INBOX',
          subject: '富文本样例',
          from: 'security@example.com',
          to: ['user@example.com'],
          html_body:
            '<div align="center" style="color:#e74c3c;text-align:center;">'
            + '<p><i>斜体</i> <u>下划线</u> <s>删除线</s></p>'
            + '<p>H<sub>2</sub>O 与 x<sup>2</sup></p>'
            + '<hr>'
            + '<ol><li>第一项</li><li>第二项</li></ol>'
            + '<ul><li>A</li><li>B</li></ul>'
            + '<p onclick="window.__xss=1">安全内容</p>'
            + '<table><tbody><tr><td>单元格</td></tr></tbody></table>'
            + '<img src="data:image/png;base64,aGVsbG8=" alt="内联图片">'
            + '<script>window.__xss=1</script>'
            + '<a href="javascript:alert(1)">危险链接</a>'
            + '<img src="javascript:alert(2)" onerror="window.__xss=1">'
            + '</div>',
          text_body: '安全内容',
          attachments: [],
        },
        error: null,
      }),
    );

    render(<MessageReader folder="INBOX" uid={7} />);

    const body = await screen.findByTestId('message-html-body');
    expect(body.tagName).toBe('DIV');
    const centeredBlock = body.querySelector('div[align="center"]') as HTMLElement | null;
    expect(centeredBlock).not.toBeNull();
    expect(centeredBlock?.getAttribute('style')).toContain('color:#e74c3c');
    expect(centeredBlock?.getAttribute('style')).toContain('text-align:center');
    expect(body.querySelector('i')?.textContent).toBe('斜体');
    expect(body.querySelector('u')?.textContent).toBe('下划线');
    expect(body.querySelector('s')?.textContent).toBe('删除线');
    expect(body.querySelector('sub')?.textContent).toBe('2');
    expect(body.querySelector('sup')?.textContent).toBe('2');
    expect(body.querySelector('hr')).not.toBeNull();
    expect(body.querySelectorAll('ol li')).toHaveLength(2);
    expect(body.querySelectorAll('ul li')).toHaveLength(2);
    expect(body.textContent).toContain('安全内容');
    expect(body.textContent).toContain('单元格');
    expect(body.querySelector('img')?.getAttribute('src')).toContain('data:image/png');
    expect(body.innerHTML).not.toContain('<script');
    expect(body.innerHTML).not.toContain('onclick');
    expect(body.innerHTML).not.toContain('onerror');
    expect(body.innerHTML).not.toContain('javascript:');
    expect((window as Window & { __xss?: number }).__xss).toBeUndefined();
  });

  it('纯文本邮件即使带有文本转 HTML 结果也会回退为纯文本展示', async () => {
    mockFetch.mockResolvedValue(
      mockApiResponse({
        success: true,
        data: {
          uid: 8,
          folder: 'INBOX',
          subject: '纯文本样例',
          from: 'plain@example.com',
          to: ['user@example.com'],
          html_body: '第一行<br>第二行',
          text_body: '第一行\n第二行',
          attachments: [],
        },
        error: null,
      }),
    );

    render(<MessageReader folder="INBOX" uid={8} />);

    const textBody = await screen.findByTestId('message-text-body');
    expect(textBody.textContent).toBe('第一行\n第二行');
    expect(screen.queryByTestId('message-html-body')).toBeNull();
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
