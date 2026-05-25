import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import type { MessageDetailPayload } from './mail/types';

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

let folderPayload = {
  folders: [
    { name: 'INBOX', display_name: '收件箱', unread_count: 1, type: 'inbox' },
    { name: '.Sent', display_name: '已发送', unread_count: 0, type: 'sent' },
  ],
};

const baseSettingsPayload = {
  account: { email: 'sam.samlee.mobbin@gmail.com' },
  preferences: {
    system: { page_size: 30, mark_read_on_open: true, language: 'zh-CN', timezone: 'Asia/Shanghai', reply_quote_position: 'bottom' },
    user: { display_name: '', profile_title: '', avatar_url: '', bio: '' },
    theme: { mode: 'light' },
  },
};

let settingsPayload = structuredClone(baseSettingsPayload);

let signatureState = [
  {
    id: 'sig-1',
    name: '默认签名',
    html_body: '<p>您好，<a href="https://example.com">官网</a></p><img src="https://cdn.example.com/logo.png" alt="Logo"><script>alert(1)</script>',
    is_default: true,
  },
  {
    id: 'sig-2',
    name: '销售签名',
    html_body: '<p>联系 <a href="mailto:sales@example.com">sales@example.com</a></p>',
    is_default: false,
  },
];

let defaultSignatureId = 'sig-1';

let inboxMessageRead = false;

const buildInboxMessages = () => ({
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
      read: inboxMessageRead,
      has_attachments: false,
    },
  ],
});

let messageDetailPayload: MessageDetailPayload = {
  uid: '101',
  subject: '客户报价确认',
  text_body: '报价正文内容',
  html_body: null as string | null,
};

function setupApi() {
  mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === '/api/folders' && init?.method === 'POST') {
      const payload = JSON.parse(String(init.body || '{}')) as { name?: string };
      folderPayload.folders = [
        ...folderPayload.folders,
        { name: payload.name || '', display_name: payload.name || '', unread_count: 0, type: 'custom' },
      ];
      return Promise.resolve(mockApiResponse(success({ folder: payload.name || '', new_name: null, deleted: false })));
    }
    if (url.startsWith('/api/folders/') && init?.method === 'PATCH') {
      const payload = JSON.parse(String(init.body || '{}')) as { name?: string; new_name?: string };
      folderPayload.folders = folderPayload.folders.map((item) =>
        item.name === payload.name
          ? { ...item, name: payload.new_name || item.name, display_name: payload.new_name || item.display_name }
          : item,
      );
      return Promise.resolve(
        mockApiResponse(success({ folder: payload.name || '', new_name: payload.new_name || '', deleted: false })),
      );
    }
    if (url.startsWith('/api/folders/') && init?.method === 'DELETE') {
      const name = decodeURIComponent(url.split('/').pop() || '');
      folderPayload.folders = folderPayload.folders.filter((item) => item.name !== name);
      return Promise.resolve(mockApiResponse(success({ folder: name, new_name: null, deleted: true })));
    }
    if (url === '/api/folders') {
      return Promise.resolve(mockApiResponse(success(folderPayload)));
    }
    if (url === '/api/settings' && (!init || init.method === 'GET')) {
      return Promise.resolve(mockApiResponse(success(settingsPayload)));
    }
    if (url === '/api/settings' && init?.method === 'PUT') {
      const payload = JSON.parse(String(init.body || '{}')) as Record<string, unknown>;
      settingsPayload = {
        ...settingsPayload,
        preferences: {
          ...settingsPayload.preferences,
          system: {
            ...settingsPayload.preferences.system,
            ...((payload.system as Record<string, unknown> | undefined) || {}),
          },
          user: {
            ...settingsPayload.preferences.user,
            ...((payload.user as Record<string, unknown> | undefined) || {}),
          },
          theme: {
            ...settingsPayload.preferences.theme,
            ...((payload.theme as Record<string, unknown> | undefined) || {}),
          },
        },
      };
      return Promise.resolve(mockApiResponse(success(settingsPayload)));
    }
    if (url === '/api/settings/avatar' && init?.method === 'POST') {
      settingsPayload = {
        ...settingsPayload,
        preferences: {
          ...settingsPayload.preferences,
          user: {
            ...settingsPayload.preferences.user,
            avatar_url: 'data:image/png;base64,aGVsbG8=',
          },
        },
      };
      return Promise.resolve(mockApiResponse(success(settingsPayload)));
    }
    if (url === '/api/settings/password' && init?.method === 'POST') {
      const payload = JSON.parse(String(init.body || '{}')) as { current_password?: string; new_password?: string };
      if (payload.current_password !== 'correct-password') {
        return Promise.resolve(
          mockApiResponse(
            { success: false, data: null, error: { code: 'AUTH_INVALID_CREDENTIALS', message: '邮箱或密码不正确' } },
            false,
            401,
          ),
        );
      }
      if (payload.new_password === 'wrong-password') {
        return Promise.resolve(
          mockApiResponse(
            { success: false, data: null, error: { code: 'AUTH_INVALID_CREDENTIALS', message: '邮箱或密码不正确' } },
            false,
            401,
          ),
        );
      }
      return Promise.resolve(mockApiResponse(success({ password_updated: true })));
    }
    if (url === '/api/signatures' && (!init || init.method === 'GET')) {
      return Promise.resolve(mockApiResponse(success({ signatures: signatureState })));
    }
    if (url === '/api/signatures/default' && (!init || init.method === 'GET')) {
      return Promise.resolve(
        mockApiResponse(success({ signature: signatureState.find((item) => item.id === defaultSignatureId) || null })),
      );
    }
    if (url === '/api/signatures' && init?.method === 'POST') {
      const payload = JSON.parse(String(init.body || '{}')) as { name?: string; content?: string; text_body?: string };
      const created = {
        id: `sig-${signatureState.length + 1}`,
        name: payload.name || '未命名签名',
        html_body: payload.content || '',
        is_default: false,
      };
      signatureState = [...signatureState, created];
      return Promise.resolve(mockApiResponse(success({ signature: created })));
    }
    if (url.startsWith('/api/signatures/') && url.endsWith('/default') && init?.method === 'POST') {
      const id = url.split('/')[3];
      defaultSignatureId = id;
      signatureState = signatureState.map((item) => ({ ...item, is_default: item.id === id }));
      return Promise.resolve(mockApiResponse(success({ signature: signatureState.find((item) => item.id === id) || null })));
    }
    if (url.startsWith('/api/signatures/') && init?.method === 'PATCH') {
      const id = url.split('/')[3];
      const payload = JSON.parse(String(init.body || '{}')) as { name?: string; content?: string; text_body?: string };
      signatureState = signatureState.map((item) =>
        item.id === id
          ? {
              ...item,
              name: payload.name ?? item.name,
              html_body: payload.content ?? item.html_body,
            }
          : item,
      );
      return Promise.resolve(
        mockApiResponse(success({ signature: signatureState.find((item) => item.id === id) || null })),
      );
    }
    if (url.startsWith('/api/signatures/') && init?.method === 'DELETE') {
      const id = url.split('/')[3];
      signatureState = signatureState.filter((item) => item.id !== id);
      if (defaultSignatureId === id) {
        defaultSignatureId = signatureState[0]?.id || '';
        signatureState = signatureState.map((item) => ({ ...item, is_default: item.id === defaultSignatureId }));
      }
      return Promise.resolve(mockApiResponse(success({ deleted: true })));
    }
    if (url.startsWith('/api/contacts?')) {
      return Promise.resolve(
        mockApiResponse(
          success({
            query: '',
            contacts: [
              { email: 'alice@example.com', name: 'Alice', note: '重点客户', last_used_at: '2026-05-07T09:00:00+00:00' },
            ],
          }),
        ),
      );
    }
    if (url.startsWith('/api/folders/INBOX/messages/search?')) {
      const inboxMessages = buildInboxMessages();
      return Promise.resolve(mockApiResponse(success({ ...inboxMessages, total: 0, messages: [] })));
    }
    if (url.startsWith('/api/folders/INBOX/messages?')) {
      return Promise.resolve(mockApiResponse(success(buildInboxMessages())));
    }
    if (url === '/api/folders/INBOX/messages/101') {
      return Promise.resolve(mockApiResponse(success(messageDetailPayload)));
    }
    if (url === '/api/folders/INBOX/messages/operations') {
      const payload = JSON.parse(String(init?.body || '{}')) as { action?: string };
      if (payload.action === 'mark_read') {
        inboxMessageRead = true;
        folderPayload.folders = folderPayload.folders.map((folder) =>
          folder.name === 'INBOX' ? { ...folder, unread_count: 0 } : folder,
        );
      }
      if (payload.action === 'mark_unread') {
        inboxMessageRead = false;
        folderPayload.folders = folderPayload.folders.map((folder) =>
          folder.name === 'INBOX' ? { ...folder, unread_count: 1 } : folder,
        );
      }
      return Promise.resolve(mockApiResponse(success({ updated: 1, action: payload.action || 'mark_read' })));
    }
    throw new Error(`unexpected request: ${url}`);
  });
}

describe('App 邮件工作台', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', mockFetch);
    mockFetch.mockReset();
    window.localStorage.clear();
    settingsPayload = structuredClone(baseSettingsPayload);
    folderPayload = {
      folders: [
        { name: 'INBOX', display_name: '收件箱', unread_count: 1, type: 'inbox' },
        { name: '.Sent', display_name: '已发送', unread_count: 0, type: 'sent' },
      ],
    };
    inboxMessageRead = false;
    signatureState = [
      {
        id: 'sig-1',
        name: '默认签名',
        html_body: '<p>您好，<a href="https://example.com">官网</a></p><img src="https://cdn.example.com/logo.png" alt="Logo"><script>alert(1)</script>',
        is_default: true,
      },
      {
        id: 'sig-2',
        name: '销售签名',
        html_body: '<p>联系 <a href="mailto:sales@example.com">sales@example.com</a></p>',
        is_default: false,
      },
    ];
    defaultSignatureId = 'sig-1';
    messageDetailPayload = {
      uid: '101',
      subject: '客户报价确认',
      text_body: '报价正文内容',
      html_body: null,
    };
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
    const listCalls = mockFetch.mock.calls.filter(([url]) => String(url).startsWith('/api/folders/INBOX/messages?'));
    expect(listCalls).toHaveLength(1);
    expect(String(listCalls[0][0])).toContain('page_size=30');
    expect(String(listCalls[0][0])).not.toContain('refresh=true');
  });

  it('搜索邮件后展示空结果', async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByText('客户报价确认');
    await user.type(screen.getByPlaceholderText('搜索邮件...'), 'release');
    await user.keyboard('{Enter}');

    await waitFor(() => {
      expect(screen.getByText((content) => content.includes('关键词：release'))).not.toBeNull();
      expect(screen.getByText('当前文件夹暂无邮件。')).not.toBeNull();
    });
    expect(mockFetch).toHaveBeenCalledWith(expect.stringContaining('/api/folders/INBOX/messages/search?'), expect.any(Object));
  });

  it('点击当前文件夹会自动刷新邮件列表', async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByText('客户报价确认');
    mockFetch.mockClear();

    await user.click(screen.getAllByText('收件箱')[0]);

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(expect.stringContaining('/api/folders/INBOX/messages?page=1&page_size=30&refresh=true'), expect.any(Object));
    });
  });

  it('搜索表单会提交发件人、日期范围和有附件筛选', async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByText('客户报价确认');
    await user.type(screen.getByPlaceholderText('搜索邮件...'), '客户');
    await user.click(screen.getByRole('button', { name: '展开筛选' }));
    await user.type(screen.getByLabelText('发件人'), 'Alice');
    fireEvent.change(screen.getByLabelText('开始日期'), { target: { value: '2026-05-07' } });
    fireEvent.change(screen.getByLabelText('结束日期'), { target: { value: '2026-05-08' } });
    await user.click(screen.getByLabelText('仅看有附件'));
    await user.click(screen.getByRole('button', { name: '应用筛选' }));

    await waitFor(() => {
      const call = mockFetch.mock.calls.find(([url]) => String(url).includes('/api/folders/INBOX/messages/search?'));
      expect(call).toBeDefined();
      const url = new URL(String(call?.[0]), 'http://localhost');
      expect(url.searchParams.get('q')).toBe('客户');
      expect(url.searchParams.get('sender')).toBe('Alice');
      expect(url.searchParams.get('date_from')).toBe('2026-05-07');
      expect(url.searchParams.get('date_to')).toBe('2026-05-08');
      expect(url.searchParams.get('has_attachments')).toBe('true');
    });
    const summary = screen.getByText(/关键词：客户/);
    expect(summary.textContent).toContain('有附件');
  });

  it('搜索筛选区域默认折叠并可展开收起', async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByText('客户报价确认');
    expect(screen.queryByLabelText('发件人')).toBeNull();

    await user.click(screen.getByRole('button', { name: '展开筛选' }));
    expect(screen.getByLabelText('发件人')).not.toBeNull();
    expect(screen.getByRole('button', { name: '收起筛选' })).not.toBeNull();

    await user.click(screen.getByRole('button', { name: '收起筛选' }));
    await waitFor(() => {
      expect(screen.queryByLabelText('发件人')).toBeNull();
    });
  });

  it('打开邮件详情并按偏好标记已读', async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByText('客户报价确认'));

    expect(await screen.findByText('报价正文内容')).not.toBeNull();
    expect(mockFetch).toHaveBeenCalledWith('/api/folders/INBOX/messages/101', expect.any(Object));
    expect(mockFetch).toHaveBeenCalledWith('/api/folders/INBOX/messages/operations', expect.objectContaining({ method: 'POST' }));
  });

  it('阅读区发件人优先展示联系人备注并保留邮箱地址', async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByText('客户报价确认'));

    const senderLabel = await screen.findByText('发件人');
    const senderField = senderLabel.parentElement?.querySelector('.field-value');
    expect(senderField?.textContent).toContain('重点客户');
    expect(senderField?.textContent).toContain('<alice@example.com>');
  });

  it('邮件列表支持多选并批量标记已读', async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByText('客户报价确认');
    await user.click(screen.getByLabelText('选择邮件 客户报价确认'));
    expect(screen.getByText('已选 1 封')).not.toBeNull();

    await user.click(screen.getByRole('button', { name: '批量标已读' }));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/folders/INBOX/messages/operations',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ action: 'mark_read', uids: ['101'] }),
        }),
      );
    });
  });

  it('文件夹未读角标会随读信、标记未读和手动刷新同步', async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByText('客户报价确认');
    expect(document.querySelectorAll('.badge')).toHaveLength(1);

    await user.click(screen.getByText('客户报价确认'));

    await waitFor(() => {
      expect(document.querySelectorAll('.badge')).toHaveLength(0);
    });

    await user.click(screen.getByRole('button', { name: '标为未读' }));

    await waitFor(() => {
      expect(document.querySelectorAll('.badge')).toHaveLength(1);
    });

    await user.click(screen.getByRole('button', { name: '刷新列表' }));

    await waitFor(() => {
      expect(document.querySelectorAll('.badge')).toHaveLength(1);
    });
  });

  it('切换文件夹时不会继续请求旧选中邮件详情', async () => {
    const user = userEvent.setup();
    const sentMessages = {
      folder: '.Sent',
      page: 1,
      page_size: 30,
      total: 1,
      messages: [
        {
          uid: '201',
          subject: '已发送回执',
          sender: { name: 'Sam', email: 'sam.samlee.mobbin@gmail.com' },
          to: [{ name: 'Alice', email: 'alice@example.com' }],
          date: '2026-05-07T10:00:00+00:00',
          snippet: '已发送内容',
          read: true,
          has_attachments: false,
        },
      ],
    };

    mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/folders') {
        return Promise.resolve(mockApiResponse(success(folderPayload)));
      }
      if (url === '/api/settings' && (!init || init.method === 'GET')) {
        return Promise.resolve(mockApiResponse(success(settingsPayload)));
      }
      if (url.startsWith('/api/folders/INBOX/messages?')) {
        return Promise.resolve(mockApiResponse(success(buildInboxMessages())));
      }
      if (url.startsWith('/api/folders/.Sent/messages?')) {
        return Promise.resolve(mockApiResponse(success(sentMessages)));
      }
      if (url === '/api/folders/INBOX/messages/101') {
        return Promise.resolve(mockApiResponse(success(messageDetailPayload)));
      }
      if (url === '/api/folders/INBOX/messages/operations') {
        return Promise.resolve(mockApiResponse(success({ action: 'mark_read', folder: 'INBOX', uids: ['101'] })));
      }
      throw new Error(`unexpected request: ${url}`);
    });

    render(<App />);

    await user.click(await screen.findByText('客户报价确认'));
    expect(await screen.findByText('报价正文内容')).not.toBeNull();

    await user.click(screen.getAllByText('已发送')[0]);
    expect(await screen.findByText('已发送回执')).not.toBeNull();

    expect(mockFetch).not.toHaveBeenCalledWith('/api/folders/.Sent/messages/101', expect.any(Object));
  });

  it('收件阅读区优先展示富文本 HTML 并保留样式与结构', async () => {
    const user = userEvent.setup();
    messageDetailPayload = {
      uid: '101',
      subject: '富文本邮件',
      text_body: '纯文本备用正文',
      html_body:
        '<div align="right" style="color:#2ecc71;text-align:right;">'
        + '<p><font color="#2ecc71">绿色正文</font> '
        + '<span class="notice" style="color:#e74c3c;background-color:#fff3cd;font-family:Arial;font-size:18px;">红色正文</span></p>'
        + '<p><i>斜体</i> <u>下划线</u> <s>删除线</s></p>'
        + '<p>H<sub>2</sub>O 与 x<sup>2</sup></p>'
        + '<hr>'
        + '<ol><li>第一项</li><li>第二项</li></ol>'
        + '<ul><li>A</li><li>B</li></ul>'
        + '<table><tbody><tr><td>单元格</td></tr></tbody></table>'
        + '<img src="data:image/png;base64,aGVsbG8=" alt="内联图片">'
        + '<script>alert(1)</script>'
        + '</div>',
    };
    render(<App />);

    await user.click(await screen.findByText('客户报价确认'));

    const htmlBody = await screen.findByTestId('app-message-html-body');
    expect(htmlBody.tagName).toBe('DIV');
    const alignedBlock = htmlBody.querySelector('div[align="right"]') as HTMLElement | null;
    expect(alignedBlock).not.toBeNull();
    expect(alignedBlock?.getAttribute('style')).toContain('color:#2ecc71');
    expect(alignedBlock?.getAttribute('style')).toContain('text-align:right');
    expect(htmlBody.textContent).toContain('绿色正文');
    expect(htmlBody.textContent).toContain('红色正文');
    expect(htmlBody.innerHTML).toContain('绿色正文');
    expect(htmlBody.querySelector('.notice')?.textContent).toBe('红色正文');
    expect(htmlBody.querySelector('.notice')?.getAttribute('style')).toContain('color:#e74c3c');
    expect(htmlBody.querySelector('i')?.textContent).toBe('斜体');
    expect(htmlBody.querySelector('u')?.textContent).toBe('下划线');
    expect(htmlBody.querySelector('s')?.textContent).toBe('删除线');
    expect(htmlBody.querySelector('sub')?.textContent).toBe('2');
    expect(htmlBody.querySelector('sup')?.textContent).toBe('2');
    expect(htmlBody.querySelector('hr')).not.toBeNull();
    expect(htmlBody.querySelectorAll('ol li')).toHaveLength(2);
    expect(htmlBody.querySelectorAll('ul li')).toHaveLength(2);
    expect(htmlBody.querySelector('table td')?.textContent).toBe('单元格');
    expect(htmlBody.querySelector('img')?.getAttribute('src')).toContain('data:image/png');
    expect(htmlBody.innerHTML).not.toContain('<script');
    expect(screen.queryByTestId('app-message-text-body')).toBeNull();
  });

  it('阅读区展示 Word 附件的文件名、类型、大小和下载链接', async () => {
    const user = userEvent.setup();
    messageDetailPayload = {
      uid: '101',
      subject: 'Word 附件邮件',
      text_body: '请查看附件',
      html_body: null,
      attachments: [
        {
          id: 'attachment-1',
          filename: '会议纪要.docx',
          content_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
          size: 3072,
        },
      ],
    };
    render(<App />);

    await user.click(await screen.findByText('客户报价确认'));

    expect(await screen.findByText('会议纪要.docx')).not.toBeNull();
    expect(
      screen.getByText('application/vnd.openxmlformats-officedocument.wordprocessingml.document · 3.0 KB'),
    ).not.toBeNull();
    const previewButton = screen.getByRole('button', { name: '预览 会议纪要.docx' });
    expect(previewButton).not.toBeNull();
    const downloadLink = screen.getByRole('link', { name: '下载' }) as HTMLAnchorElement;
    expect(downloadLink).not.toBeNull();
    expect(downloadLink.getAttribute('href')).toBe('/api/folders/INBOX/messages/101/attachments/attachment-1');
    expect(downloadLink.getAttribute('download')).toBe('会议纪要.docx');

    await user.click(previewButton);
    const previewDialog = await screen.findByRole('dialog', { name: '附件预览' });
    const previewFrame = previewDialog.querySelector('iframe') as HTMLIFrameElement | null;
    expect(previewFrame).not.toBeNull();
    expect(previewFrame?.getAttribute('src')).toBe('/api/folders/INBOX/messages/101/attachments/attachment-1/preview');
  });

  it('纯文本邮件即使后端返回文本转 HTML 也会保持纯文本阅读', async () => {
    const user = userEvent.setup();
    messageDetailPayload = {
      uid: '101',
      subject: '纯文本邮件',
      text_body: '第一行\n第二行',
      html_body: '第一行<br>第二行',
    };
    render(<App />);

    await user.click(await screen.findByText('客户报价确认'));

    const textBody = await screen.findByTestId('app-message-text-body');
    expect(textBody.textContent).toBe('第一行\n第二行');
    expect(screen.queryByTestId('app-message-html-body')).toBeNull();
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

    await user.click(await screen.findByRole('button', { name: '打开联系人' }));
    const list = await screen.findByLabelText('联系人列表');
    await user.click(within(list).getByRole('button', { name: 'alice@example.com' }));

    const panel = await screen.findByRole('complementary', { name: '写信面板' });
    expect(within(panel).getByRole('button', { name: '选择 alice@example.com' })).not.toBeNull();
    expect((within(panel).getByRole('textbox', { name: '收件人' }) as HTMLInputElement).value).toBe('');
  });


  it('可以打开签名设置面板并加载现有签名', async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole('button', { name: '打开设置' }));
    const settingsDialog = await screen.findByRole('dialog', { name: '设置' });
    await user.click(within(settingsDialog).getByRole('button', { name: '安全' }));
    await user.click(within(settingsDialog).getByRole('button', { name: '进入签名设置' }));
    const dialog = await screen.findByRole('dialog', { name: '签名设置' });

    expect(within(dialog).getByText('默认签名')).not.toBeNull();
    expect(within(dialog).getByText('销售签名')).not.toBeNull();
    expect(mockFetch).toHaveBeenCalledWith('/api/signatures', expect.any(Object));
    expect(mockFetch).toHaveBeenCalledWith('/api/signatures/default', expect.any(Object));

    const defaultPreview = within(dialog).getByRole('button', { name: '编辑 默认签名' });
    expect(defaultPreview.innerHTML).toContain('官网');
    expect(defaultPreview.innerHTML).toContain('img');
    expect(defaultPreview.innerHTML).not.toContain('<script');
  });

  it('收件箱右键邮件可以回复引用', async () => {
    const user = userEvent.setup();
    render(<App />);

    const messageRow = await screen.findByText('客户报价确认');
    fireEvent.contextMenu(messageRow.closest('.message-row') as HTMLElement);
    await user.click(await screen.findByRole('button', { name: '回复并引用' }));

    const panel = await screen.findByRole('complementary', { name: '写信面板' });
    expect(within(panel).getByRole('button', { name: '选择 alice@example.com' })).not.toBeNull();
    expect((within(panel).getByRole('textbox', { name: '收件人' }) as HTMLInputElement).value).toBe('');
    expect((within(panel).getByLabelText('主题') as HTMLInputElement).value).toBe('回复：客户报价确认');
    expect(within(panel).getByLabelText('正文').innerHTML).toContain('报价正文内容');
  });

  it('回复引用方式默认使用底部引用，切换后使用顶部引用', async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByText('sam.samlee.mobbin@gmail.com');
    await user.click(screen.getByRole('button', { name: '打开设置' }));
    const dialog = await screen.findByRole('dialog', { name: '设置' });

    expect(within(dialog).getByLabelText('回复引用位置')).not.toBeNull();
    await user.selectOptions(within(dialog).getByLabelText('回复引用位置'), 'top');
    await user.click(within(dialog).getByRole('button', { name: '保存设置' }));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/settings',
        expect.objectContaining({
          method: 'PUT',
          body: JSON.stringify({
            system: {
              page_size: 30,
              mark_read_on_open: true,
              language: 'zh-CN',
              timezone: 'Asia/Shanghai',
              reply_quote_position: 'top',
            },
            user: {
              display_name: '',
              profile_title: '',
              avatar_url: '',
              bio: '',
            },
            theme: {
              mode: 'light',
            },
          }),
        }),
      );
    });

    const messageRow = await screen.findByText('客户报价确认');
    fireEvent.contextMenu(messageRow.closest('.message-row') as HTMLElement);
    await user.click(await screen.findByRole('button', { name: '回复并引用' }));
    const panel = await screen.findByRole('complementary', { name: '写信面板' });
    const body = within(panel).getByLabelText('正文') as HTMLElement;
    expect(body.innerHTML).toContain('发件人：');
    expect(body.innerHTML).toContain('发送时间：');
    expect(body.innerHTML).toContain('收件人：');
    expect(body.innerHTML).toContain('主题：');
    expect(body.innerHTML.trim().startsWith('<blockquote>')).toBe(true);
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

  it('可以在设置中修改密码并提交新密码', async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByText('sam.samlee.mobbin@gmail.com');
    await user.click(screen.getByRole('button', { name: '打开设置' }));
    const dialog = await screen.findByRole('dialog', { name: '设置' });
    await user.click(within(dialog).getByRole('button', { name: '安全' }));

    await user.type(within(dialog).getByLabelText('旧密码'), 'correct-password');
    await user.type(within(dialog).getByLabelText('新密码'), 'updated-password');
    await user.type(within(dialog).getByLabelText('确认新密码'), 'updated-password');
    await user.click(within(dialog).getByRole('button', { name: '更新密码' }));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/settings/password',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            current_password: 'correct-password',
            new_password: 'updated-password',
          }),
        }),
      );
    });
    expect(within(dialog).getByText('密码已更新，并已通过新密码完成收信服务登录验证。')).not.toBeNull();
    expect((within(dialog).getByLabelText('旧密码') as HTMLInputElement).value).toBe('');
  });

  it('可以创建、重命名和删除文件夹，且系统文件夹不可删除', async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByText('sam.samlee.mobbin@gmail.com');
    await user.click(screen.getByRole('button', { name: '管理文件夹' }));
    const dialog = await screen.findByRole('dialog', { name: '文件夹管理' });

    await user.type(within(dialog).getByLabelText('新建文件夹'), 'Projects');
    await user.click(within(dialog).getByRole('button', { name: '创建' }));
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/folders',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ name: 'Projects' }),
        }),
      );
    });
    expect(within(dialog).getByText('已创建文件夹：Projects')).not.toBeNull();

    await user.selectOptions(within(dialog).getByLabelText('选择文件夹'), 'Projects');
    await user.clear(within(dialog).getByLabelText('新名称'));
    await user.type(within(dialog).getByLabelText('新名称'), 'Projects 2026');
    await user.click(within(dialog).getByRole('button', { name: '重命名' }));
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/folders/Projects',
        expect.objectContaining({
          method: 'PATCH',
          body: JSON.stringify({ name: 'Projects', new_name: 'Projects 2026' }),
        }),
      );
    });
    expect(within(dialog).getByText('已重命名文件夹：Projects → Projects 2026')).not.toBeNull();

    await waitFor(() => {
      expect(within(dialog).getByRole('option', { name: 'Projects 2026' })).not.toBeNull();
    });
    await user.selectOptions(within(dialog).getByLabelText('选择文件夹'), 'Projects 2026');
    await user.click(within(dialog).getByRole('button', { name: '删除' }));
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith('/api/folders/Projects%202026', expect.objectContaining({ method: 'DELETE' }));
    });
    expect(within(dialog).getByText('已删除文件夹：Projects 2026')).not.toBeNull();
    expect(folderPayload.folders.some((item) => item.name === 'Projects 2026')).toBe(false);

    await user.selectOptions(within(dialog).getByLabelText('选择文件夹'), 'INBOX');
    await user.click(within(dialog).getByRole('button', { name: '删除' }));
    expect(within(dialog).getByText('系统文件夹不可删除。')).not.toBeNull();
  });

  it('确认新密码不一致时不会提交修改密码请求', async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByText('sam.samlee.mobbin@gmail.com');
    await user.click(screen.getByRole('button', { name: '打开设置' }));
    const dialog = await screen.findByRole('dialog', { name: '设置' });
    await user.click(within(dialog).getByRole('button', { name: '安全' }));

    await user.type(within(dialog).getByLabelText('旧密码'), 'correct-password');
    await user.type(within(dialog).getByLabelText('新密码'), 'updated-password');
    await user.type(within(dialog).getByLabelText('确认新密码'), 'other-password');
    await user.click(within(dialog).getByRole('button', { name: '更新密码' }));

    expect(within(dialog).getByRole('alert').textContent).toContain('两次输入的新密码不一致。');
    expect(mockFetch).not.toHaveBeenCalledWith(
      '/api/settings/password',
      expect.objectContaining({ method: 'POST' }),
    );
  });

  it('设置支持保存语言与时区，并按用户时区格式化时间', async () => {
    const user = userEvent.setup();
    const laSettings = {
      ...settingsPayload,
      preferences: {
        system: { page_size: 30, mark_read_on_open: true, language: 'en-US', timezone: 'America/Los_Angeles', reply_quote_position: 'bottom' },
        user: { display_name: '', profile_title: '', avatar_url: '', bio: '' },
        theme: { mode: 'light' },
      },
    };
    mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/folders') {
        return Promise.resolve(mockApiResponse(success(folderPayload)));
      }
      if (url === '/api/settings' && (!init || init.method === 'GET')) {
        return Promise.resolve(mockApiResponse(success(laSettings)));
      }
      if (url === '/api/settings' && init?.method === 'PUT') {
        const payload = JSON.parse(String(init.body || '{}')) as { system?: { language?: string; timezone?: string } };
        settingsPayload.preferences = {
          ...settingsPayload.preferences,
          system: {
            ...settingsPayload.preferences.system,
            ...(payload.system || {}),
          },
        };
        return Promise.resolve(mockApiResponse(success({
          account: { email: 'sam.samlee.mobbin@gmail.com' },
          preferences: settingsPayload.preferences,
        })));
      }
      if (url === '/api/settings/password') {
        return Promise.resolve(mockApiResponse(success({ password_updated: true })));
      }
      if (url === '/api/signatures' && (!init || init.method === 'GET')) {
        return Promise.resolve(mockApiResponse(success({ signatures: signatureState })));
      }
      if (url === '/api/signatures/default' && (!init || init.method === 'GET')) {
        return Promise.resolve(
          mockApiResponse(success({ signature: signatureState.find((item) => item.id === defaultSignatureId) || null })),
        );
      }
      if (url.startsWith('/api/folders/INBOX/messages?')) {
        return Promise.resolve(mockApiResponse(success(buildInboxMessages())));
      }
      if (url === '/api/folders/INBOX/messages/101') {
        return Promise.resolve(mockApiResponse(success(messageDetailPayload)));
      }
      throw new Error(`unexpected request: ${url}`);
    });

    render(<App />);

    await screen.findByText('sam.samlee.mobbin@gmail.com');
    expect(await screen.findByText('客户报价确认')).not.toBeNull();
    expect(screen.getByText('5/7/26, 2:30 AM')).not.toBeNull();

    await user.click(screen.getByRole('button', { name: '打开设置' }));
    const dialog = await screen.findByRole('dialog', { name: '设置' });
    await user.selectOptions(within(dialog).getByLabelText('界面语言'), 'en-US');
    await user.selectOptions(within(dialog).getByLabelText('时区'), 'America/Los_Angeles');
    await user.click(within(dialog).getByRole('button', { name: '保存设置' }));

    await waitFor(() => {
      const putCall = mockFetch.mock.calls.find((call) => call[0] === '/api/settings' && call[1]?.method === 'PUT');
      expect(putCall).toBeTruthy();
      expect(putCall?.[1]).toEqual(
        expect.objectContaining({
          method: 'PUT',
          body: JSON.stringify({
            system: {
              page_size: 30,
              mark_read_on_open: true,
              language: 'en-US',
              timezone: 'America/Los_Angeles',
              reply_quote_position: 'bottom',
            },
            user: {
              display_name: '',
              profile_title: '',
              avatar_url: '',
              bio: '',
            },
            theme: {
              mode: 'light',
            },
          }),
        }),
      );
    });
  });

  it('用户设置和主题设置可以保存并立即反映到界面', async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByText('sam.samlee.mobbin@gmail.com');
    await user.click(screen.getByRole('button', { name: '打开设置' }));
    const dialog = await screen.findByRole('dialog', { name: '设置' });

    await user.type(within(dialog).getByLabelText('显示名称'), 'Sam Lee');
    await user.type(within(dialog).getByLabelText('职位/头衔'), '设计负责人');
    await user.type(within(dialog).getByLabelText('头像地址'), 'https://cdn.example.com/avatar.png');
    await user.type(within(dialog).getByLabelText('个人简介'), '负责品牌体验');
    await user.click(within(dialog).getByRole('button', { name: '外观' }));
    await user.click(within(dialog).getByRole('button', { name: /深色主题/ }));
    await user.click(within(dialog).getByRole('button', { name: '保存设置' }));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/settings',
        expect.objectContaining({
          method: 'PUT',
          body: JSON.stringify({
            system: {
              page_size: 30,
              mark_read_on_open: true,
              language: 'zh-CN',
              timezone: 'Asia/Shanghai',
              reply_quote_position: 'bottom',
            },
            user: {
              display_name: 'Sam Lee',
              profile_title: '设计负责人',
              avatar_url: 'https://cdn.example.com/avatar.png',
              bio: '负责品牌体验',
            },
            theme: {
              mode: 'dark',
            },
          }),
        }),
      );
    });

    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(screen.getByText('Sam Lee')).not.toBeNull();
    expect(screen.getByText('设计负责人')).not.toBeNull();
    expect(document.querySelector('.account-avatar img')?.getAttribute('src')).toBe('https://cdn.example.com/avatar.png');
  });

  it('可以上传本地头像并立即显示', async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByText('sam.samlee.mobbin@gmail.com');
    await user.click(screen.getByRole('button', { name: '打开设置' }));
    const dialog = await screen.findByRole('dialog', { name: '设置' });

    const file = new File(['hello'], 'avatar.png', { type: 'image/png' });
    await user.upload(within(dialog).getByLabelText('上传头像'), file);

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        '/api/settings/avatar',
        expect.objectContaining({
          method: 'POST',
          body: expect.any(FormData),
        }),
      );
    });

    expect(document.querySelector('.account-avatar img')?.getAttribute('src')).toBe('data:image/png;base64,aGVsbG8=');
  });
});
