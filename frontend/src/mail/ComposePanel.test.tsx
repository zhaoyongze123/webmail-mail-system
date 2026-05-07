import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ComposePanel from './ComposePanel';

const mockFetch = vi.fn();

function mockApiResponse(body: unknown, ok = true, status = ok ? 200 : 400) {
  return {
    ok,
    status,
    json: async () => body,
  } as Response;
}

function success(data: unknown) {
  return { success: true, data, error: null };
}

function renderCompose(overrides: Partial<React.ComponentProps<typeof ComposePanel>> = {}) {
  const props = {
    open: true,
    onClose: vi.fn(),
    onSent: vi.fn(),
    onSessionExpired: vi.fn(),
    ...overrides,
  };
  render(<ComposePanel {...props} />);
  return props;
}

describe('ComposePanel 写信发信草稿', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', mockFetch);
    mockFetch.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('打开写信面板并展示基础字段', () => {
    renderCompose();

    expect(screen.getByRole('heading', { name: '写信' })).not.toBeNull();
    expect(screen.getByLabelText('收件人')).not.toBeNull();
    expect(screen.getByLabelText('抄送')).not.toBeNull();
    expect(screen.getByLabelText('密送')).not.toBeNull();
    expect(screen.getByLabelText('主题')).not.toBeNull();
    expect(screen.getByLabelText('正文')).not.toBeNull();
    expect(screen.getByRole('button', { name: '发送' })).not.toBeNull();
  });

  it('按收件人输入查询联系人并可补全', async () => {
    const user = userEvent.setup();
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith('/api/contacts?')) {
        return Promise.resolve(mockApiResponse(success({ contacts: [{ email: 'alice@example.com' }] })));
      }
      throw new Error(`unexpected request: ${url}`);
    });
    renderCompose();

    await user.type(screen.getByLabelText('收件人'), 'ali');
    const suggestions = await screen.findByLabelText('联系人建议');
    await user.click(within(suggestions).getByRole('button', { name: 'alice@example.com' }));

    expect((screen.getByLabelText('收件人') as HTMLInputElement).value).toBe('alice@example.com ');
    expect(mockFetch).toHaveBeenCalledWith(expect.stringContaining('/api/contacts?query=ali'), expect.any(Object));
  });

  it('上传附件后展示进度和列表', async () => {
    const user = userEvent.setup();
    mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/attachments') {
        expect(init?.body).toBeInstanceOf(FormData);
        return Promise.resolve(
          mockApiResponse(
            success({
              attachments: [
                {
                  attachment_id: 'att-1',
                  filename: 'report.txt',
                  content_type: 'text/plain',
                  size_bytes: 5,
                  expires_at: '2026-05-07T10:00:00+00:00',
                },
              ],
            }),
          ),
        );
      }
      throw new Error(`unexpected request: ${url}`);
    });
    renderCompose();

    await user.upload(screen.getByLabelText('添加附件'), new File(['hello'], 'report.txt', { type: 'text/plain' }));

    const list = await screen.findByLabelText('附件列表');
    expect(within(list).getByText('report.txt')).not.toBeNull();
    expect(within(list).getByText('5 B')).not.toBeNull();
    expect(within(list).getByText('已上传')).not.toBeNull();
    expect((screen.getByLabelText('report.txt 上传进度') as HTMLProgressElement).value).toBe(100);
  });

  it('手动保存草稿并携带表单内容', async () => {
    const user = userEvent.setup();
    mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/drafts') {
        expect(JSON.parse(String(init?.body))).toMatchObject({
          draft_id: null,
          to: ['receiver@example.com'],
          cc: [],
          bcc: [],
          subject: '会议纪要',
          text_body: '正文内容',
          attachment_ids: [],
        });
        return Promise.resolve(mockApiResponse(success({ draft_id: 'draft-1', status: 'saved', saved_at: '2026-05-07T10:00:00+00:00' })));
      }
      throw new Error(`unexpected request: ${url}`);
    });
    renderCompose();

    await user.type(screen.getByLabelText('收件人'), 'receiver@example.com');
    await user.type(screen.getByLabelText('主题'), '会议纪要');
    await user.type(screen.getByLabelText('正文'), '正文内容');
    await user.click(screen.getByRole('button', { name: '保存草稿' }));

    await waitFor(() => {
      expect(screen.getByText('草稿状态：已保存')).not.toBeNull();
    });
  });

  it('发送成功后关闭并触发回调', async () => {
    const user = userEvent.setup();
    const props = renderCompose();
    mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/messages/send') {
        expect(JSON.parse(String(init?.body))).toMatchObject({
          to: ['receiver@example.com'],
          subject: '发送测试',
          text_body: '发送正文',
        });
        return Promise.resolve(mockApiResponse(success({ message_id: '<msg-1@example.com>', sent: true, archived_folder: '.Sent' })));
      }
      throw new Error(`unexpected request: ${url}`);
    });

    await user.type(screen.getByLabelText('收件人'), 'receiver@example.com');
    await user.type(screen.getByLabelText('主题'), '发送测试');
    await user.type(screen.getByLabelText('正文'), '发送正文');
    await user.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(props.onSent).toHaveBeenCalledWith({ message_id: '<msg-1@example.com>', sent: true, archived_folder: '.Sent' });
      expect(props.onClose).toHaveBeenCalledTimes(1);
    });
  });

  it('发送中禁用按钮防止重复发送', async () => {
    const user = userEvent.setup();
    let resolveSend: (value: Response) => void = () => undefined;
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/messages/send') {
        return new Promise<Response>((resolve) => {
          resolveSend = resolve;
        });
      }
      throw new Error(`unexpected request: ${url}`);
    });
    renderCompose();

    await user.type(screen.getByLabelText('收件人'), 'receiver@example.com');
    await user.click(screen.getByRole('button', { name: '发送' }));
    await waitFor(() => {
      expect((screen.getByRole('button', { name: '发送中' }) as HTMLButtonElement).disabled).toBe(true);
    });
    await user.click(screen.getByRole('button', { name: '发送中' }));
    expect(mockFetch).toHaveBeenCalledTimes(1);

    resolveSend(mockApiResponse(success({ message_id: '<msg-1@example.com>', sent: true })));
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(1);
    });
  });

  it('输入后 5 秒内自动保存草稿', async () => {
    vi.useFakeTimers();
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/drafts') {
        return Promise.resolve(mockApiResponse(success({ draft_id: 'draft-auto', status: 'saved', saved_at: '2026-05-07T10:00:00+00:00' })));
      }
      throw new Error(`unexpected request: ${url}`);
    });
    renderCompose();

    fireEvent.change(screen.getByLabelText('主题'), { target: { value: '自动保存' } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4999);
    });
    expect(mockFetch).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });

    expect(mockFetch).toHaveBeenCalledWith('/api/drafts', expect.any(Object));
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByText('草稿状态：已保存')).not.toBeNull();
  });
});
