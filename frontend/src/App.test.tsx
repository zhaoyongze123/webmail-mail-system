import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';

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

const folderPayload = {
  folders: [
    { name: 'INBOX', display_name: '收件箱', unread_count: 2 },
    { name: '.Sent', display_name: '已发送', unread_count: 0 },
  ],
};

const settingsPayload = {
  account: { email: 'sam.samlee.mobbin@gmail.com' },
  preferences: { page_size: 30, mark_read_on_open: true },
};

const inboxMessages = {
  folder: 'INBOX',
  page: 1,
  page_size: 30,
  total: 1,
  messages: [
    {
      uid: '101',
      subject: '客户报价确认',
      sender: { name: 'Alice', email: 'alice@example.com' },
      to: [{ name: 'Sam', email: 'sam.samlee.mobbin@gmail.com' }],
      date: '2026-05-07T09:30:00+00:00',
      snippet: '请确认最新报价。',
      read: false,
      has_attachments: false,
    },
  ],
};

let messageDetailPayload = {
  uid: '101',
  subject: '客户报价确认',
  text_body: '报价正文内容',
  html_body: null as string | null,
};

function setupApi() {
  mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === '/api/folders') {
      return Promise.resolve(mockApiResponse(success(folderPayload)));
    }
    if (url === '/api/settings' && (!init || init.method === 'GET')) {
      return Promise.resolve(mockApiResponse(success(settingsPayload)));
    }
    if (url === '/api/settings' && init?.method === 'PUT') {
      return Promise.resolve(mockApiResponse(success(settingsPayload)));
    }
    if (url.startsWith('/api/contacts?')) {
      return Promise.resolve(mockApiResponse(success({ query: '', contacts: [{ email: 'alice@example.com', last_used_at: '2026-05-07T09:00:00+00:00' }] })));
    }
    if (url.startsWith('/api/folders/INBOX/messages/search?')) {
      return Promise.resolve(mockApiResponse(success({ ...inboxMessages, total: 0, messages: [] })));
    }
    if (url.startsWith('/api/folders/INBOX/messages?')) {
      return Promise.resolve(mockApiResponse(success(inboxMessages)));
    }
    if (url === '/api/folders/INBOX/messages/101') {
      return Promise.resolve(mockApiResponse(success(messageDetailPayload)));
    }
    if (url === '/api/folders/INBOX/messages/operations') {
      return Promise.resolve(mockApiResponse(success({ updated: 1, action: 'mark_read' })));
    }
    throw new Error(`unexpected request: ${url}`);
  });
}

describe('App 邮件工作台', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', mockFetch);
    mockFetch.mockReset();
    messageDetailPayload = {
      uid: '101',
      subject: '客户报价确认',
      text_body: '报价正文内容',
      html_body: null,
    };
    setupApi();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('加载文件夹、设置和邮件列表', async () => {
    render(<App />);

    await waitFor(() => {
      expect(screen.getAllByText('收件箱').length).toBeGreaterThanOrEqual(1);
    });
    expect(screen.getByText('sam.samlee.mobbin@gmail.com')).not.toBeNull();
    expect(await screen.findByText('客户报价确认')).not.toBeNull();
    expect(mockFetch).toHaveBeenCalledWith('/api/folders', expect.any(Object));
    expect(mockFetch).toHaveBeenCalledWith('/api/settings', expect.any(Object));
  });

  it('搜索邮件后展示空结果', async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByText('客户报价确认');
    await user.type(screen.getByPlaceholderText('搜索邮件...'), 'release');
    await user.keyboard('{Enter}');

    await waitFor(() => {
      expect(screen.getByText('搜索: release')).not.toBeNull();
      expect(screen.getByText('当前文件夹暂无邮件。')).not.toBeNull();
    });
    expect(mockFetch).toHaveBeenCalledWith(expect.stringContaining('/api/folders/INBOX/messages/search?'), expect.any(Object));
  });

  it('打开邮件详情并按偏好标记已读', async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByText('客户报价确认'));

    expect(await screen.findByText('报价正文内容')).not.toBeNull();
    expect(mockFetch).toHaveBeenCalledWith('/api/folders/INBOX/messages/101', expect.any(Object));
    expect(mockFetch).toHaveBeenCalledWith('/api/folders/INBOX/messages/operations', expect.objectContaining({ method: 'POST' }));
  });

  it('收件阅读区优先展示富文本 HTML 并保留颜色表格图片', async () => {
    const user = userEvent.setup();
    messageDetailPayload = {
      uid: '101',
      subject: '富文本邮件',
      text_body: '纯文本备用正文',
      html_body:
        '<p><span style="color:#e74c3c;background-color:#fff3cd;font-family:Arial;font-size:18px;">红色正文</span></p><table><tbody><tr><td>单元格</td></tr></tbody></table><img src="data:image/png;base64,aGVsbG8=" alt="内联图片"><script>alert(1)</script>',
    };
    render(<App />);

    await user.click(await screen.findByText('客户报价确认'));

    const htmlBody = await screen.findByTestId('app-message-html-body');
    expect(htmlBody.textContent).toContain('红色正文');
    expect(htmlBody.querySelector('span')?.getAttribute('style')).toContain('color:#e74c3c');
    expect(htmlBody.querySelector('table td')?.textContent).toBe('单元格');
    expect(htmlBody.querySelector('img')?.getAttribute('src')).toContain('data:image/png');
    expect(htmlBody.innerHTML.toLowerCase()).not.toContain('<script');
    expect(screen.queryByTestId('app-message-text-body')).toBeNull();
  });

  it('打开写信面板并显示当前账号发件人', async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole('button', { name: '写邮件' }));
    const panel = await screen.findByRole('complementary', { name: '写信面板' });

    expect(within(panel).getByText('sam.samlee.mobbin@gmail.com')).not.toBeNull();
    expect(within(panel).getByRole('textbox', { name: '收件人' })).not.toBeNull();
  });

  it('打开联系人面板并可从联系人发起写信', async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByText('联系人'));
    const list = await screen.findByLabelText('联系人列表');
    await user.click(within(list).getByRole('button', { name: 'alice@example.com' }));

    const panel = await screen.findByRole('complementary', { name: '写信面板' });
    expect((within(panel).getByRole('textbox', { name: '收件人' }) as HTMLInputElement).value).toBe('alice@example.com');
  });

  it('每 30 秒自动刷新当前收件箱', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<App />);

    await screen.findByText('客户报价确认');
    const callsBeforeTimer = mockFetch.mock.calls.filter(([url]) => String(url).startsWith('/api/folders/INBOX/messages?')).length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });

    await waitFor(() => {
      const callsAfterTimer = mockFetch.mock.calls.filter(([url]) => String(url).startsWith('/api/folders/INBOX/messages?')).length;
      expect(callsAfterTimer).toBeGreaterThan(callsBeforeTimer);
    });
  });

  it('收件箱右键邮件可以回复引用', async () => {
    const user = userEvent.setup();
    render(<App />);

    const messageRow = await screen.findByText('客户报价确认');
    fireEvent.contextMenu(messageRow);
    await user.click(await screen.findByRole('button', { name: '回复并引用' }));

    const panel = await screen.findByRole('complementary', { name: '写信面板' });
    expect((within(panel).getByRole('textbox', { name: '收件人' }) as HTMLInputElement).value).toBe('alice@example.com');
    expect((within(panel).getByLabelText('主题') as HTMLInputElement).value).toBe('Re: 客户报价确认');
    expect(within(panel).getByLabelText('正文').innerHTML).toContain('报价正文内容');
  });

  it('会话过期时展示登录页并可登录', async () => {
    mockFetch.mockReset();
    mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/folders') {
        return Promise.resolve(mockApiResponse({ success: false, data: null, error: { code: 'AUTH_SESSION_EXPIRED', message: '登录已过期' } }, false, 401));
      }
      if (url === '/api/settings') {
        return Promise.resolve(mockApiResponse({ success: false, data: null, error: { code: 'AUTH_SESSION_EXPIRED', message: '登录已过期' } }, false, 401));
      }
      if (url === '/api/auth/login') {
        return Promise.resolve(mockApiResponse(success({ email: 'sam.samlee.mobbin@gmail.com' })));
      }
      throw new Error(`unexpected request: ${url}`);
    });
    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole('heading', { name: '登录邮箱' });
    await user.type(screen.getByLabelText('邮箱'), 'sam.samlee.mobbin@gmail.com');
    await user.type(screen.getByLabelText('密码'), 'correct-password');
    await user.click(screen.getByRole('button', { name: '登录' }));

    expect(mockFetch).toHaveBeenCalledWith('/api/auth/login', expect.objectContaining({ method: 'POST' }));
  });
});
