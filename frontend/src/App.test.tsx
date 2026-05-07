import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
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
    if (url.startsWith('/api/folders/INBOX/messages/search?')) {
      return Promise.resolve(mockApiResponse(success({ ...inboxMessages, total: 0, messages: [] })));
    }
    if (url.startsWith('/api/folders/INBOX/messages?')) {
      return Promise.resolve(mockApiResponse(success(inboxMessages)));
    }
    if (url === '/api/folders/INBOX/messages/101') {
      return Promise.resolve(
        mockApiResponse(
          success({
            uid: '101',
            subject: '客户报价确认',
            text_body: '报价正文内容',
            html_body: null,
          }),
        ),
      );
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
    setupApi();
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
    expect(mockFetch).toHaveBeenCalledWith('/api/folders/INBOX/messages/101');
    expect(mockFetch).toHaveBeenCalledWith('/api/folders/INBOX/messages/operations', expect.objectContaining({ method: 'POST' }));
  });

  it('打开写信面板并显示当前账号发件人', async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole('button', { name: '写邮件' }));
    const panel = await screen.findByRole('complementary', { name: '写信面板' });

    expect(within(panel).getByText('sam.samlee.mobbin@gmail.com')).not.toBeNull();
    expect(within(panel).getByRole('textbox', { name: '收件人' })).not.toBeNull();
  });
});
