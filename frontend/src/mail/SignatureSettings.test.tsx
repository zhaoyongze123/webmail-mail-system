import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import SignatureSettings from './SignatureSettings';

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

function setupApi() {
  mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
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
      return Promise.resolve(mockApiResponse(success({ signature: signatureState.find((item) => item.id === id) || null })));
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
    throw new Error(`unexpected request: ${url}`);
  });
}

describe('SignatureSettings 签名面板', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', mockFetch);
    vi.stubGlobal('confirm', vi.fn(() => true));
    mockFetch.mockReset();
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
    setupApi();
  });

  afterEach(() => {
    Reflect.deleteProperty(window, 'confirm');
    Reflect.deleteProperty(window, 'prompt');
  });

  it('加载时会净化脚本并保留图片和链接', async () => {
    render(<SignatureSettings open onClose={vi.fn()} />);

    const dialog = await screen.findByRole('dialog', { name: '签名设置' });
    await waitFor(() => {
      expect(within(dialog).getByRole('button', { name: '编辑 默认签名' })).not.toBeNull();
    });
    const defaultItem = within(dialog).getByRole('button', { name: '编辑 默认签名' });

    expect(defaultItem.innerHTML).toContain('官网');
    expect(defaultItem.innerHTML).toContain('<img');
    expect(defaultItem.innerHTML).not.toContain('<script');
  });

  it('可以新建签名并插入链接和图片后保存', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('prompt', vi.fn(() => 'https://example.com'));
    render(<SignatureSettings open onClose={vi.fn()} />);

    const dialog = await screen.findByRole('dialog', { name: '签名设置' });
    await waitFor(() => {
      expect(within(dialog).getByRole('button', { name: '新建签名' })).not.toBeNull();
    });
    await user.click(within(dialog).getByRole('button', { name: '新建签名' }));
    const nameInput = screen.getByLabelText('签名名称');
    await user.clear(nameInput);
    await user.type(nameInput, '新签名');
    await user.click(screen.getByRole('button', { name: '富文本' }));

    const editor = screen.getByRole('textbox', { name: '签名正文' }) as HTMLDivElement;
    editor.innerHTML = '<p>欢迎<script>alert(1)</script></p>';
    fireEvent.input(editor);

    await user.click(screen.getByRole('button', { name: '添加链接' }));
    editor.innerHTML = `${editor.innerHTML}<img src="https://cdn.example.com/logo.png" alt="品牌 Logo">`;
    fireEvent.input(editor);

    await user.click(screen.getByRole('button', { name: '保存签名' }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '编辑 新签名' })).not.toBeNull();
    });
    const createdItem = screen.getByRole('button', { name: '编辑 新签名' });
    expect(createdItem.innerHTML).toContain('新签名');
    expect(createdItem.innerHTML).toContain('<img src="https://cdn.example.com/logo.png"');
    expect(createdItem.innerHTML).not.toContain('<script');
  });

  it('连续新建多个签名时不会跳回默认签名，并且可以编辑已有签名', async () => {
    const user = userEvent.setup();
    render(<SignatureSettings open onClose={vi.fn()} />);

    const dialog = await screen.findByRole('dialog', { name: '签名设置' });
    await waitFor(() => {
      expect(within(dialog).getByRole('button', { name: '新建签名' })).not.toBeNull();
    });

    await user.click(within(dialog).getByRole('button', { name: '新建签名' }));
    const nameInput = screen.getByLabelText('签名名称');
    await user.type(nameInput, '第二个签名');
    await user.click(screen.getByRole('button', { name: '保存签名' }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '编辑 第二个签名' })).not.toBeNull();
    });
    expect((screen.getByLabelText('签名名称') as HTMLInputElement).value).toBe('第二个签名');

    await user.click(within(dialog).getByRole('button', { name: '新建签名' }));
    await user.clear(screen.getByLabelText('签名名称'));
    await user.type(screen.getByLabelText('签名名称'), '第三个签名');
    await user.click(screen.getByRole('button', { name: '保存签名' }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '编辑 第三个签名' })).not.toBeNull();
    });

    await user.click(screen.getByRole('button', { name: '编辑 第二个签名' }));
    await user.clear(screen.getByLabelText('签名名称'));
    await user.type(screen.getByLabelText('签名名称'), '第二个签名-已更新');
    await user.click(screen.getByRole('button', { name: '保存签名' }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '编辑 第二个签名-已更新' })).not.toBeNull();
    });
  });

  it('可以在富文本和纯文本之间切换并保留签名内容', async () => {
    const user = userEvent.setup();
    render(<SignatureSettings open onClose={vi.fn()} />);

    const dialog = await screen.findByRole('dialog', { name: '签名设置' });
    await waitFor(() => {
      expect(within(dialog).getByRole('button', { name: '新建签名' })).not.toBeNull();
    });
    await user.click(within(dialog).getByRole('button', { name: '新建签名' }));
    await user.type(screen.getByLabelText('签名名称'), '模式切换签名');
    await user.click(screen.getByRole('button', { name: '纯文本' }));

    const plainEditor = screen.getByRole('textbox', { name: '签名正文' }) as HTMLTextAreaElement;
    await user.type(plainEditor, '第一行\\n第二行');

    await user.click(screen.getByRole('button', { name: '富文本' }));
    const richEditor = screen.getByRole('textbox', { name: '签名正文' }) as HTMLDivElement;
    expect(richEditor.innerHTML).toContain('第一行');
    expect(richEditor.textContent || '').toContain('第二行');
  });

  it('可以设为默认并删除签名', async () => {
    const user = userEvent.setup();
    render(<SignatureSettings open onClose={vi.fn()} />);

    const dialog = await screen.findByRole('dialog', { name: '签名设置' });
    await waitFor(() => {
      expect(within(dialog).getByRole('button', { name: '设为默认 销售签名' })).not.toBeNull();
    });
    await user.click(within(dialog).getByRole('button', { name: '设为默认 销售签名' }));
    expect(mockFetch).toHaveBeenCalledWith('/api/signatures/sig-2/default', expect.objectContaining({ method: 'POST' }));
    expect(within(dialog).getByText('销售签名').closest('li')?.textContent).toContain('默认');

    await user.click(within(dialog).getByRole('button', { name: '删除 销售签名' }));
    expect(mockFetch).toHaveBeenCalledWith('/api/signatures/sig-2', expect.objectContaining({ method: 'DELETE' }));
  });

  it('可以选择签名图片并调整大小与位置后保存', async () => {
    const user = userEvent.setup();
    render(<SignatureSettings open onClose={vi.fn()} />);

    await screen.findByRole('dialog', { name: '签名设置' });
    await user.click(screen.getByRole('button', { name: '新建签名' }));
    await user.type(screen.getByLabelText('签名名称'), '图片签名');
    await user.click(screen.getByRole('button', { name: '富文本' }));

    const editor = screen.getByRole('textbox', { name: '签名正文' }) as HTMLDivElement;
    editor.innerHTML =
      '<div class="inline-image" data-inline-image-id="img-1" data-inline-image-position="center" style="text-align: center;"><img src="https://cdn.example.com/logo.png" alt="Logo" style="width: 320px; max-width: 100%; height: auto;"></div><p><br></p>';
    fireEvent.mouseDown(editor.querySelector('.inline-image') as HTMLElement);
    fireEvent.input(editor);

    const slider = await screen.findByRole('slider');
    fireEvent.change(slider, { target: { value: '480' } });
    await user.click(screen.getByRole('button', { name: '图片右对齐' }));
    await user.click(screen.getByRole('button', { name: '保存签名' }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '编辑 图片签名' })).not.toBeNull();
    });
    const createdItem = screen.getByRole('button', { name: '编辑 图片签名' });
    expect(createdItem.innerHTML).toContain('width: 480px');
    expect(createdItem.innerHTML).toContain('text-align: right');
    expect(createdItem.innerHTML).not.toContain('data-selected');
    expect(createdItem.innerHTML).not.toContain('data-inline-image-id');
  });
});
