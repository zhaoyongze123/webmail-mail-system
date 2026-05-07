import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import MailWorkspace from './MailWorkspace';

const mockFetch = vi.fn();

const folders = [
  {
    name: 'INBOX',
    canonical_name: 'INBOX',
    display_name: '收件箱',
    type: 'inbox',
    unread_count: 2,
    total_count: 3,
  },
  {
    name: '.Sent',
    canonical_name: '.Sent',
    display_name: '已发送',
    type: 'sent',
    unread_count: 0,
    total_count: 1,
  },
  {
    name: '.Archive',
    canonical_name: '.Archive',
    display_name: '归档',
    type: 'archive',
    unread_count: 0,
    total_count: 0,
  },
];

const inboxMessages = [
  {
    uid: '103',
    message_id: '<103@example.com>',
    subject: '最新客户邮件',
    sender: { name: 'Carol', email: 'carol@example.com' },
    date: '2026-05-07T09:00:00+08:00',
    read: false,
    has_attachments: true,
    snippet: '请查看附件里的报价。',
  },
];

const sentMessages = [
  {
    uid: '201',
    message_id: '<201@example.com>',
    subject: '已发送邮件',
    sender: { name: 'User', email: 'user@example.com' },
    date: '2026-05-07T08:00:00+08:00',
    read: true,
    has_attachments: false,
    snippet: '这是已发送文件夹中的邮件。',
  },
];

function apiResponse(data: unknown, ok = true) {
  return {
    ok,
    json: async () => ({ success: ok, data: ok ? data : null, error: ok ? null : data }),
  } as Response;
}

function requestUrl(input: RequestInfo | URL) {
  return String(input);
}

function parseUrl(input: RequestInfo | URL) {
  return new URL(String(input), 'http://localhost');
}

describe('MailWorkspace 三栏工作台', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', mockFetch);
    mockFetch.mockReset();
  });

  it('加载并展示文件夹与默认收件箱列表', async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === '/api/folders') {
        return Promise.resolve(apiResponse({ folders }));
      }
      if (url.startsWith('/api/folders/INBOX/messages')) {
        return Promise.resolve(apiResponse({ folder: 'INBOX', page: 1, page_size: 30, total: 1, messages: inboxMessages }));
      }
      throw new Error(`unexpected request: ${url}`);
    });

    render(<MailWorkspace onOpenMessage={vi.fn()} />);

    expect(await screen.findByRole('button', { name: /收件箱/ })).not.toBeNull();
    expect(screen.getByRole('button', { name: /已发送/ })).not.toBeNull();
    expect(await screen.findByText(/最新客户邮件/)).not.toBeNull();
  });

  it('切换文件夹后加载对应邮件列表', async () => {
    const user = userEvent.setup();
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === '/api/folders') {
        return Promise.resolve(apiResponse({ folders }));
      }
      if (url.startsWith('/api/folders/INBOX/messages')) {
        return Promise.resolve(apiResponse({ folder: 'INBOX', page: 1, page_size: 30, total: 1, messages: inboxMessages }));
      }
      if (url.startsWith('/api/folders/.Sent/messages')) {
        return Promise.resolve(apiResponse({ folder: '.Sent', page: 1, page_size: 30, total: 1, messages: sentMessages }));
      }
      throw new Error(`unexpected request: ${url}`);
    });

    render(<MailWorkspace onOpenMessage={vi.fn()} />);

    await screen.findByText(/最新客户邮件/);
    await user.click(screen.getByRole('button', { name: /已发送/ }));

    expect(await screen.findByText('已发送邮件')).not.toBeNull();
    expect(mockFetch).toHaveBeenCalledWith(expect.stringContaining('/api/folders/.Sent/messages'), expect.any(Object));
  });

  it('刷新保留当前文件夹并携带 refresh 参数', async () => {
    const user = userEvent.setup();
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === '/api/folders') {
        return Promise.resolve(apiResponse({ folders }));
      }
      if (url.startsWith('/api/folders/INBOX/messages')) {
        return Promise.resolve(apiResponse({ folder: 'INBOX', page: 1, page_size: 30, total: 1, messages: inboxMessages }));
      }
      if (url.startsWith('/api/folders/.Sent/messages')) {
        return Promise.resolve(apiResponse({ folder: '.Sent', page: 1, page_size: 30, total: 1, messages: sentMessages }));
      }
      throw new Error(`unexpected request: ${url}`);
    });

    render(<MailWorkspace onOpenMessage={vi.fn()} />);

    await screen.findByText(/最新客户邮件/);
    await user.click(screen.getByRole('button', { name: /已发送/ }));
    await screen.findByText('已发送邮件');
    await user.click(screen.getByRole('button', { name: '刷新' }));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(expect.stringContaining('/api/folders/.Sent/messages?page=1&page_size=30&refresh=true'), expect.any(Object));
    });
    expect(screen.getByText('当前文件夹：已发送')).not.toBeNull();
  });

  it('支持当前文件夹搜索和清空搜索', async () => {
    const user = userEvent.setup();
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = parseUrl(input);
      if (url.pathname === '/api/folders') {
        return Promise.resolve(apiResponse({ folders }));
      }
      if (url.pathname === '/api/folders/INBOX/messages' && !url.searchParams.get('refresh')) {
        return Promise.resolve(apiResponse({ folder: 'INBOX', page: 1, page_size: 30, total: 1, messages: inboxMessages }));
      }
      if (url.pathname === '/api/folders/INBOX/messages/search') {
        expect(url.searchParams.get('q')).toBe('客户');
        return Promise.resolve(apiResponse({ folder: 'INBOX', page: 1, page_size: 30, total: 1, messages: inboxMessages, query: '客户' }));
      }
      if (url.pathname === '/api/folders/INBOX/messages' && url.searchParams.get('refresh') === 'true') {
        return Promise.resolve(apiResponse({ folder: 'INBOX', page: 1, page_size: 30, total: 1, messages: inboxMessages }));
      }
      throw new Error(`unexpected request: ${url.toString()}`);
    });

    render(<MailWorkspace onOpenMessage={vi.fn()} />);

    await screen.findByText(/最新客户邮件/);
    await user.type(screen.getByLabelText('搜索当前文件夹'), '客户');
    await user.click(screen.getByRole('button', { name: '搜索' }));
    await screen.findByText('搜索：客户');
    await user.click(screen.getByRole('button', { name: '清空搜索' }));

    expect(await screen.findByText('当前显示全部邮件')).not.toBeNull();
    expect(mockFetch).toHaveBeenCalledWith(expect.stringContaining('/api/folders/INBOX/messages/search?q=%E5%AE%A2%E6%88%B7'), expect.any(Object));
  });

  it('支持批量标记、移动和删除', async () => {
    const user = userEvent.setup();
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = parseUrl(input);
      if (url.pathname === '/api/folders') {
        return Promise.resolve(apiResponse({ folders }));
      }
      if (url.pathname === '/api/folders/INBOX/messages') {
        return Promise.resolve(apiResponse({ folder: 'INBOX', page: 1, page_size: 30, total: 2, messages: inboxMessages }));
      }
      if (url.pathname === '/api/folders/INBOX/messages/operations') {
        return Promise.resolve(apiResponse({ action: 'mark_read', folder: 'INBOX', uids: ['103'] }));
      }
      if (url.pathname === '/api/messages/move') {
        expect(url.searchParams.get('folder')).toBe('INBOX');
        return Promise.resolve(apiResponse({ action: 'move', folder: 'INBOX', target_folder: '.Sent', uids: ['103'] }));
      }
      if (url.pathname === '/api/messages/delete') {
        expect(url.searchParams.get('folder')).toBe('INBOX');
        return Promise.resolve(apiResponse({ action: 'delete', folder: 'INBOX', uids: ['103'] }));
      }
      throw new Error(`unexpected request: ${url.toString()}`);
    });

    render(<MailWorkspace onOpenMessage={vi.fn()} />);

    await screen.findByText(/最新客户邮件/);
    const selectMessage = async () => {
      await user.click(screen.getByLabelText('选择邮件 最新客户邮件'));
      await screen.findByText('已选 1 封');
    };

    await selectMessage();
    await user.click(screen.getByRole('button', { name: '执行' }));
    await screen.findByText('已更新 1 封邮件');

    await user.selectOptions(screen.getByLabelText('批量操作类型'), 'move');
    await selectMessage();
    await user.selectOptions(screen.getByLabelText('目标文件夹'), '.Sent');
    await user.click(screen.getByRole('button', { name: '执行' }));
    await screen.findByText('已移动 1 封邮件');

    await user.selectOptions(screen.getByLabelText('批量操作类型'), 'delete');
    await selectMessage();
    await user.click(screen.getByRole('button', { name: '执行' }));
    await screen.findByText('已删除 1 封邮件');
  });

  it('空邮件列表展示空状态', async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === '/api/folders') {
        return Promise.resolve(apiResponse({ folders }));
      }
      if (url.startsWith('/api/folders/INBOX/messages')) {
        return Promise.resolve(apiResponse({ folder: 'INBOX', page: 1, page_size: 30, total: 0, messages: [] }));
      }
      throw new Error(`unexpected request: ${url}`);
    });

    render(<MailWorkspace onOpenMessage={vi.fn()} />);

    const list = await screen.findByRole('region', { name: '邮件列表' });
    expect(within(list).getByText('当前文件夹暂无邮件')).not.toBeNull();
  });

  it('顶部可进入设置页入口', async () => {
    const user = userEvent.setup();
    const onOpenSettings = vi.fn();
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url === '/api/folders') {
        return Promise.resolve(apiResponse({ folders }));
      }
      if (url.startsWith('/api/folders/INBOX/messages')) {
        return Promise.resolve(apiResponse({ folder: 'INBOX', page: 1, page_size: 30, total: 0, messages: [] }));
      }
      throw new Error(`unexpected request: ${url}`);
    });

    render(<MailWorkspace onOpenMessage={vi.fn()} onOpenSettings={onOpenSettings} />);

    await screen.findByRole('button', { name: /收件箱/ });
    await user.click(screen.getByRole('button', { name: '设置偏好' }));
    expect(onOpenSettings).toHaveBeenCalledTimes(1);
  });
});
