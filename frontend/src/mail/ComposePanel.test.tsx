import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ComposePanel from './ComposePanel';

const mockFetch = vi.fn();
const defaultSignaturePayload = success({
  text_body: '默认签名',
  html_body: '<p>默认签名</p>',
});

let requestHandler: (input: RequestInfo | URL, init?: RequestInit) => unknown = () => {
  throw new Error('unexpected request');
};

function setFetchMockImplementation(handler: (input: RequestInfo | URL, init?: RequestInit) => unknown) {
  requestHandler = handler;
}

function installFetchMock() {
  mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input) === '/api/signatures/default') {
      return Promise.resolve(mockApiResponse(defaultSignaturePayload));
    }
    if (String(input).startsWith('/api/contacts?')) {
      try {
        return requestHandler(input, init);
      } catch {
        return Promise.resolve(mockApiResponse(success({ contacts: [] })));
      }
    }
    return requestHandler(input, init);
  });
}

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

class MockFileReader {
  result: string | ArrayBuffer | null = null;
  onload: ((this: FileReader, ev: ProgressEvent<FileReader>) => unknown) | null = null;

  readAsDataURL(file: File) {
    this.result = `data:${file.type};base64,aW5saW5lLWltYWdl`;
    this.onload?.call(this as unknown as FileReader, {} as ProgressEvent<FileReader>);
  }
}

function mockRichEditorExecCommand() {
  const execCommand = vi.fn((command: string, _showUi?: boolean, value?: string) => {
    const editor = screen.getByLabelText('正文') as HTMLDivElement;
    if (command === 'insertHTML' && value) {
      editor.innerHTML += value;
      editor.dispatchEvent(new InputEvent('input', { bubbles: true }));
      return true;
    }
    return true;
  });
  Object.defineProperty(document, 'execCommand', {
    configurable: true,
    value: execCommand,
  });
  Object.defineProperty(document, 'queryCommandState', {
    configurable: true,
    value: vi.fn(() => false),
  });
  return execCommand;
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
    requestHandler = () => {
      throw new Error('unexpected request');
    };
    installFetchMock();
  });

  afterEach(() => {
    vi.useRealTimers();
    Reflect.deleteProperty(document, 'execCommand');
    Reflect.deleteProperty(document, 'queryCommandState');
    Reflect.deleteProperty(window, 'FileReader');
    window.localStorage.clear();
  });

  it('打开写信面板并展示基础字段', () => {
    const props = renderCompose();

    expect(screen.getByRole('heading', { name: '写信' })).not.toBeNull();
    expect(screen.getByLabelText('收件人')).not.toBeNull();
    expect(screen.getByLabelText('抄送')).not.toBeNull();
    expect(screen.getByLabelText('密送')).not.toBeNull();
    expect(screen.getByLabelText('主题')).not.toBeNull();
    expect(screen.getByLabelText('正文')).not.toBeNull();
    expect(screen.getByRole('button', { name: '发送' })).not.toBeNull();
    expect(screen.queryByRole('button', { name: 'AI' })).toBeNull();
    expect(screen.queryByRole('button', { name: '优化文字' })).toBeNull();
    expect(screen.getByLabelText('字体')).not.toBeNull();
    expect(screen.getByLabelText('字号')).not.toBeNull();
  });

  it('新建邮件时会自动插入默认签名', async () => {
    renderCompose();

    await waitFor(() => {
      expect((screen.getByLabelText('正文') as HTMLDivElement).innerHTML).toContain('默认签名');
    });
  });

  it('回复内容时会在引用后自动追加默认签名', async () => {
    renderCompose({
      initialValues: {
        subject: 'Re: 需求确认',
        text_body: '引用内容',
        html_body: '<blockquote>引用内容</blockquote>',
      },
    });

    await waitFor(() => {
      const editor = screen.getByLabelText('正文') as HTMLDivElement;
      expect(editor.innerHTML).toContain('引用内容');
      expect(editor.innerHTML).toContain('默认签名');
    });
  });

  it('按收件人输入查询联系人并可补全', async () => {
    const user = userEvent.setup();
    setFetchMockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith('/api/contacts?')) {
        return Promise.resolve(mockApiResponse(success({ contacts: [{ email: 'alice@example.com' }] })));
      }
      throw new Error(`unexpected request: ${url}`);
    });
    const props = renderCompose();

    await user.type(screen.getByLabelText('收件人'), 'ali');
    const suggestions = await screen.findByRole('list', { name: '联系人建议' });
    await user.click(within(suggestions).getByRole('button', { name: 'alice@example.com' }));

    expect((screen.getByLabelText('收件人') as HTMLInputElement).value).toBe('');
    expect(screen.getByRole('button', { name: '删除 alice@example.com' })).not.toBeNull();
    expect(mockFetch).toHaveBeenCalledWith(expect.stringContaining('/api/contacts?query=ali'), expect.any(Object));
  });

  it('收件人 Tag 支持回车添加、方向键导航和键盘删除', async () => {
    const user = userEvent.setup();
    const props = renderCompose();

    await user.type(screen.getByLabelText('收件人'), 'alice@example.com{enter}bob@example.com{enter}');

    expect(screen.getByRole('button', { name: '删除 alice@example.com' })).not.toBeNull();
    expect(screen.getByRole('button', { name: '删除 bob@example.com' })).not.toBeNull();

    const recipientInput = screen.getByLabelText('收件人');
    await user.click(recipientInput);
    await user.keyboard('{ArrowLeft}');

    const chipsAfterFirstArrow = Array.from(document.querySelectorAll('.compose-recipient-row .recipient-chip'));
    expect(chipsAfterFirstArrow[1]?.getAttribute('data-selected')).toBe('true');

    await user.keyboard('{ArrowLeft}');
    const chipsAfterSecondArrow = Array.from(document.querySelectorAll('.compose-recipient-row .recipient-chip'));
    expect(chipsAfterSecondArrow[0]?.getAttribute('data-selected')).toBe('true');

    await user.keyboard('{ArrowRight}');
    const chipsAfterRight = Array.from(document.querySelectorAll('.compose-recipient-row .recipient-chip'));
    expect(chipsAfterRight[1]?.getAttribute('data-selected')).toBe('true');

    await user.keyboard('{Delete}');
    expect(screen.queryByRole('button', { name: '删除 bob@example.com' })).toBeNull();
    expect(screen.getByRole('button', { name: '删除 alice@example.com' })).not.toBeNull();
  });

  it('跨收件人字段重复时会阻止新增并提示错误', async () => {
    const user = userEvent.setup();
    const props = renderCompose();

    await user.type(screen.getByLabelText('收件人'), 'alice@example.com{enter}');
    await user.type(screen.getByLabelText('抄送'), 'alice@example.com{enter}');

    expect(screen.getByRole('alert').textContent).toContain('收件人不能重复');
    expect(screen.getAllByRole('button', { name: '删除 alice@example.com' })).toHaveLength(1);
  });

  it('可在富文本和纯文本之间切换并保留正文内容', async () => {
    const user = userEvent.setup();
    renderCompose({
      initialValues: {
        subject: '模式切换',
        text_body: '第一行\n第二行',
        html_body: '<p>第一行</p><p><strong>第二行</strong></p>',
      },
    });

    await user.click(screen.getByRole('button', { name: '纯文本' }));
    const plainEditor = screen.getByLabelText('正文') as HTMLTextAreaElement;
    expect(plainEditor.tagName).toBe('TEXTAREA');
    expect(plainEditor.value).toContain('第一行');
    expect(plainEditor.value).toContain('第二行');

    await user.type(plainEditor, '\n第三行');
    await user.click(screen.getByRole('button', { name: '富文本' }));

    const richEditor = screen.getByLabelText('正文') as HTMLDivElement;
    expect(richEditor.tagName).toBe('DIV');
    expect(richEditor.innerHTML).toContain('第一行');
    expect(richEditor.innerHTML).toContain('第二行');
    expect(richEditor.innerHTML).toContain('第三行');
  });

  it('默认签名不会重复插入到已有正文末尾', async () => {
    const user = userEvent.setup();
    const props = renderCompose({
      initialValues: {
        subject: '重复签名',
        text_body: '正文内容\n\n默认签名',
        html_body: '<p>正文内容</p><p><br></p><p>默认签名</p>',
      },
    });

    setFetchMockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/messages/send') {
        const payload = JSON.parse(String(init?.body));
        expect(payload.text_body).toBe('正文内容\n\n默认签名');
        expect(String(payload.html_body)).toContain('正文内容');
        expect(String(payload.html_body)).toContain('默认签名');
        expect(String(payload.html_body).match(/默认签名/g)?.length).toBe(1);
        return Promise.resolve(mockApiResponse(success({ message_id: '<msg-dup@example.com>', sent: true, archived_folder: '.Sent' })));
      }
      throw new Error(`unexpected request: ${url}`);
    });

    await user.type(screen.getByLabelText('收件人'), 'receiver@example.com');
    await user.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(props.onSent).toHaveBeenCalledTimes(1);
    });
  });

  it('上传附件后展示进度和列表', async () => {
    const user = userEvent.setup();
    setFetchMockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/attachments/chunks') {
        expect(init?.body).toBeInstanceOf(FormData);
        const formData = init?.body as FormData;
        expect(formData.get('chunk_index')).toBe('0');
        expect(formData.get('total_chunks')).toBe('1');
        return Promise.resolve(
          mockApiResponse(
            success({
              attachment: {
                attachment_id: 'att-1',
                filename: 'report.txt',
                content_type: 'text/plain',
                size_bytes: 5,
                expires_at: '2026-05-07T10:00:00+00:00',
                complete: true,
                uploaded_chunks: 1,
                total_chunks: 1,
              },
            }),
          ),
        );
      }
      throw new Error(`unexpected request: ${url}`);
    });
    const props = renderCompose();

    await user.upload(screen.getByLabelText('添加附件'), new File(['hello'], 'report.txt', { type: 'text/plain' }));

    const list = await screen.findByRole('list', { name: '附件列表' });
    expect(within(list).getByText('report.txt')).not.toBeNull();
    expect(within(list).getByText('5 B')).not.toBeNull();
    expect(within(list).getByText('已上传')).not.toBeNull();
    expect((screen.getByLabelText('report.txt 上传进度') as HTMLProgressElement).value).toBe(100);
  });

  it('拖拽上传大文件时按分块推进进度', async () => {
    const bigFile = new File([new Uint8Array(1024 * 1024 + 32)], 'big.bin', { type: 'application/octet-stream' });
    setFetchMockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/attachments/chunks') {
        const formData = init?.body as FormData;
        const chunkIndex = Number(formData.get('chunk_index'));
        const totalChunks = Number(formData.get('total_chunks'));
        if (chunkIndex === 0) {
          expect(totalChunks).toBe(2);
          return Promise.resolve(
            mockApiResponse(
              success({
                attachment: {
                  attachment_id: 'att-big',
                  filename: 'big.bin',
                  content_type: 'application/octet-stream',
                  size_bytes: bigFile.size,
                  expires_at: '2026-05-07T10:00:00+00:00',
                  complete: false,
                  uploaded_chunks: 1,
                  total_chunks: 2,
                },
              }),
            ),
          );
        }
        return new Promise<Response>((resolve) => {
          window.setTimeout(() => {
            resolve(
              mockApiResponse(
                success({
                  attachment: {
                    attachment_id: 'att-big',
                    filename: 'big.bin',
                    content_type: 'application/octet-stream',
                    size_bytes: bigFile.size,
                    expires_at: '2026-05-07T10:00:00+00:00',
                    complete: true,
                    uploaded_chunks: 2,
                    total_chunks: 2,
                  },
                }),
              ),
            );
          }, 500);
        });
      }
      throw new Error(`unexpected request: ${url}`);
    });
    const props = renderCompose();

    const dropzone = screen.getByLabelText('附件拖拽上传区');
    fireEvent.dragEnter(dropzone);
    fireEvent.dragOver(dropzone);
    fireEvent.drop(dropzone, { dataTransfer: { files: [bigFile] } });

    const list = await screen.findByRole('list', { name: '附件列表' });
    const progress = screen.getByLabelText('big.bin 上传进度') as HTMLProgressElement;
    await waitFor(() => {
      expect(progress.value).toBe(50);
    });
    expect(within(list).getByText('上传中')).not.toBeNull();

    await waitFor(() => {
      expect(progress.value).toBe(100);
      expect(within(list).getByText('已上传')).not.toBeNull();
    });
  });

  it('分块上传失败时显示失败状态', async () => {
    const user = userEvent.setup();
    const bigFile = new File([new Uint8Array(1024 * 1024 + 16)], 'broken.bin', { type: 'application/octet-stream' });
    setFetchMockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/attachments/chunks') {
        const formData = init?.body as FormData;
        const chunkIndex = Number(formData.get('chunk_index'));
        if (chunkIndex === 0) {
          return Promise.resolve(
            mockApiResponse(
              success({
                attachment: {
                  attachment_id: 'att-broken',
                  filename: 'broken.bin',
                  content_type: 'application/octet-stream',
                  size_bytes: bigFile.size,
                  expires_at: '2026-05-07T10:00:00+00:00',
                  complete: false,
                  uploaded_chunks: 1,
                  total_chunks: 2,
                },
              }),
            ),
          );
        }
        return Promise.resolve(
          mockApiResponse(
            { success: false, data: null, error: { code: 'ATTACHMENT_TOO_LARGE', message: '附件总大小不能超过 9 MB' } },
            false,
            413,
          ),
        );
      }
      throw new Error(`unexpected request: ${url}`);
    });
    const props = renderCompose();

    await user.upload(screen.getByLabelText('添加附件'), bigFile);

    const list = await screen.findByRole('list', { name: '附件列表' });
    const progress = screen.getByLabelText('broken.bin 上传进度') as HTMLProgressElement;
    await waitFor(() => {
      expect(progress.value).toBe(100);
      expect(within(list).getByText('附件总大小不能超过 9 MB')).not.toBeNull();
    });
  });

  it('可插入内嵌图片并调整位置和大小后发送', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('FileReader', MockFileReader);
    const execCommand = mockRichEditorExecCommand();
    setFetchMockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/messages/send') {
        const body = JSON.parse(String(init?.body));
        expect(body.html_body).toContain('class="inline-image"');
        expect(body.html_body).toContain('text-align: right');
        expect(body.html_body).toContain('width: 420px');
        expect(body.html_body).toContain('data:image/png;base64,aW5saW5lLWltYWdl');
        expect(body.html_body).toContain('alt="inline.png"');
        expect(body.html_body).not.toContain('data-inline-image-id');
        expect(body.html_body).not.toContain('data-selected');
        return Promise.resolve(mockApiResponse(success({ message_id: '<msg-inline@example.com>', sent: true, archived_folder: '.Sent' })));
      }
      throw new Error(`unexpected request: ${url}`);
    });
    const props = renderCompose();

    const inlineImageInput = screen.getAllByLabelText('插入图片').find((element) => element.tagName === 'INPUT') as HTMLInputElement;
    await user.upload(inlineImageInput, new File(['inline'], 'inline.png', { type: 'image/png' }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: '图片右对齐' })).not.toBeNull();
    });

    await user.click(screen.getByRole('button', { name: '图片右对齐' }));
    const sizeSlider = screen.getAllByLabelText('图片大小').find((element) => element.tagName === 'INPUT') as HTMLInputElement;
    fireEvent.change(sizeSlider, { target: { value: '420' } });
    await waitFor(() => {
      const currentSizeSlider = screen.getAllByLabelText('图片大小').find((element) => element.tagName === 'INPUT') as HTMLInputElement;
      expect(currentSizeSlider.value).toBe('420');
    });
    await user.type(screen.getByLabelText('收件人'), 'receiver@example.com');
    await user.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(props.onSent).toHaveBeenCalledWith({ message_id: '<msg-inline@example.com>', sent: true, archived_folder: '.Sent' });
      expect(props.onClose).toHaveBeenCalledTimes(1);
    });
  });

  it('手动保存草稿并携带表单内容', async () => {
    const user = userEvent.setup();
    let savedDraftPayload: Record<string, unknown> | null = null;
    setFetchMockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/drafts') {
        savedDraftPayload = JSON.parse(String(init?.body)) as Record<string, unknown>;
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
    expect(savedDraftPayload).not.toBeNull();
    expect(savedDraftPayload).toMatchObject({
      draft_id: null,
      to: ['receiver@example.com'],
      cc: [],
      bcc: [],
      subject: '会议纪要',
      text_body: '正文内容默认签名',
      attachment_ids: [],
    });
    expect(String((savedDraftPayload as any)?.html_body)).toContain('正文内容');
    expect(String((savedDraftPayload as any)?.html_body)).toContain('默认签名');
  });

  it('发送成功后关闭并触发回调', async () => {
    const user = userEvent.setup();
    const props = renderCompose();
    let sendPayload: Record<string, unknown> | null = null;
    setFetchMockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/messages/send') {
        sendPayload = JSON.parse(String(init?.body)) as Record<string, unknown>;
        return Promise.resolve(mockApiResponse(success({ message_id: '<msg-1@example.com>', sent: true, archived_folder: '.Sent' })));
      }
      throw new Error(`unexpected request: ${url}`);
    });

    await waitFor(() => {
      expect((screen.getByLabelText('正文') as HTMLDivElement).innerHTML).toContain('默认签名');
    });
    await user.type(screen.getByLabelText('收件人'), 'receiver@example.com');
    await user.type(screen.getByLabelText('主题'), '发送测试');
    await user.type(screen.getByLabelText('正文'), '发送正文');
    await user.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(props.onSent).toHaveBeenCalledWith({ message_id: '<msg-1@example.com>', sent: true, archived_folder: '.Sent' });
      expect(props.onClose).toHaveBeenCalledTimes(1);
    });
    expect(sendPayload).not.toBeNull();
    expect(sendPayload).toMatchObject({
      to: ['receiver@example.com'],
      subject: '发送测试',
      text_body: '发送正文默认签名',
    });
    expect(String((sendPayload as any)?.html_body)).toContain('发送正文');
    expect(String((sendPayload as any)?.html_body)).toContain('默认签名');
  });

  it('发送时会校验重复收件人并阻止请求', async () => {
    const user = userEvent.setup();
    renderCompose({
      initialValues: {
        to: ['dup@example.com', 'dup@example.com'],
        subject: '重复收件人',
        text_body: '正文',
      },
    });

    await user.click(screen.getByRole('button', { name: '发送' }));

    expect(screen.getByRole('alert').textContent).toContain('收件人不能重复');
    expect(mockFetch.mock.calls.filter(([url]) => String(url) === '/api/messages/send')).toHaveLength(0);
  });

  it('纯文本模式发送时不携带 HTML 正文', async () => {
    const user = userEvent.setup();
    const props = renderCompose();
    let sendPayload: Record<string, unknown> | null = null;
    setFetchMockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/messages/send') {
        sendPayload = JSON.parse(String(init?.body)) as Record<string, unknown>;
        return Promise.resolve(mockApiResponse(success({ message_id: '<msg-plain@example.com>', sent: true, archived_folder: '.Sent' })));
      }
      throw new Error(`unexpected request: ${url}`);
    });

    await waitFor(() => {
      expect((screen.getByLabelText('正文') as HTMLDivElement).innerHTML).toContain('默认签名');
    });
    await user.click(screen.getByRole('button', { name: '纯文本' }));
    await waitFor(() => {
      expect((screen.getByLabelText('正文') as HTMLTextAreaElement).value).toContain('默认签名');
    });
    await user.type(screen.getByLabelText('收件人'), 'receiver@example.com');
    await user.type(screen.getByLabelText('主题'), '纯文本发送');
    await user.type(screen.getByLabelText('正文'), '纯文本正文');
    await user.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(props.onSent).toHaveBeenCalledWith({ message_id: '<msg-plain@example.com>', sent: true, archived_folder: '.Sent' });
      expect(props.onClose).toHaveBeenCalledTimes(1);
    });
    expect(sendPayload).not.toBeNull();
    expect(sendPayload).toMatchObject({
      to: ['receiver@example.com'],
      subject: '纯文本发送',
      text_body: '默认签名纯文本正文',
      html_body: null,
    });
  });

  it('发送中禁用按钮防止重复发送', async () => {
    const user = userEvent.setup();
    let resolveSend: (value: Response) => void = () => undefined;
    setFetchMockImplementation((input: RequestInfo | URL) => {
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
    expect(mockFetch.mock.calls.filter(([url]) => String(url) === '/api/messages/send')).toHaveLength(1);

    resolveSend(mockApiResponse(success({ message_id: '<msg-1@example.com>', sent: true })));
    await waitFor(() => {
      expect(mockFetch.mock.calls.filter(([url]) => String(url) === '/api/messages/send')).toHaveLength(1);
    });
  });

  it('输入后 30 秒内自动保存草稿', async () => {
    vi.useFakeTimers();
    let draftSaved = false;
    setFetchMockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/drafts') {
        draftSaved = true;
        return Promise.resolve(mockApiResponse(success({ draft_id: 'draft-auto', status: 'saved', saved_at: '2026-05-07T10:00:00+00:00' })));
      }
      throw new Error(`unexpected request: ${url}`);
    });
    renderCompose();

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    fireEvent.change(screen.getByLabelText('主题'), { target: { value: '自动保存' } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(29999);
    });
    expect(mockFetch.mock.calls.filter(([url]) => String(url) === '/api/drafts')).toHaveLength(0);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(draftSaved).toBe(true);
    expect(screen.getByText('草稿状态：已保存')).not.toBeNull();
  });

  it('已有草稿在 30 秒自动保存时使用 PATCH 更新', async () => {
    vi.useFakeTimers();
    setFetchMockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/drafts/draft-1') {
        expect(init?.method).toBe('PATCH');
        expect(JSON.parse(String(init?.body))).toMatchObject({
          draft_id: 'draft-1',
          to: ['receiver@example.com'],
          subject: '已有草稿更新',
        });
        return Promise.resolve(mockApiResponse(success({ draft_id: 'draft-1', status: 'saved', saved_at: '2026-05-07T10:00:00+00:00' })));
      }
      throw new Error(`unexpected request: ${url}`);
    });
    renderCompose({
      draftId: 'draft-1',
      initialValues: {
        to: ['receiver@example.com'],
        subject: '已有草稿',
        text_body: '原始正文',
        html_body: '原始正文',
      },
    });

    fireEvent.change(screen.getByLabelText('主题'), { target: { value: '已有草稿更新' } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });

    expect(mockFetch.mock.calls.some(([url]) => String(url) === '/api/drafts/draft-1')).toBe(true);
    expect(screen.getByText('草稿状态：已保存')).not.toBeNull();
  });

  it('富文本工具会写入可发送的 HTML 正文', async () => {
    const user = userEvent.setup();
    const commandState: Record<string, boolean> = { bold: false };
    const execCommand = vi.fn((command: string, _showUi?: boolean, value?: string) => {
      const editor = screen.getByLabelText('正文') as HTMLDivElement;
      if (command === 'bold') {
        commandState.bold = !commandState.bold;
        editor.innerHTML += '<b>重点内容</b>';
        editor.dispatchEvent(new InputEvent('input', { bubbles: true }));
        return true;
      }
      if (command === 'insertHTML' && value) {
        editor.innerHTML += value;
        editor.dispatchEvent(new InputEvent('input', { bubbles: true }));
        return true;
      }
      if (command === 'foreColor' && value) {
        editor.innerHTML = `<font color="${value}">${editor.innerHTML}</font>`;
        editor.dispatchEvent(new InputEvent('input', { bubbles: true }));
        return true;
      }
      return true;
    });
    Object.defineProperty(document, 'queryCommandState', {
      configurable: true,
      value: vi.fn((command: string) => commandState[command] ?? false),
    });
    Object.defineProperty(document, 'execCommand', {
      configurable: true,
      value: execCommand,
    });
    setFetchMockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/drafts') {
        const body = JSON.parse(String(init?.body));
        expect(body.text_body).toContain('重点内容');
        expect(body.html_body).toContain('#e74c3c');
        expect(body.html_body).toContain('<table>');
        return Promise.resolve(mockApiResponse(success({ draft_id: 'draft-rich', status: 'saved' })));
      }
      throw new Error(`unexpected request: ${url}`);
    });
    renderCompose();

    const editor = screen.getByLabelText('正文');
    await user.click(editor);
    await user.click(screen.getByRole('button', { name: '加粗' }));
    await user.click(screen.getByRole('button', { name: '插入表格' }));
    await user.click(screen.getByRole('button', { name: '2 × 2 表格' }));
    await user.click(screen.getByRole('button', { name: '文字颜色' }));
    await user.click(screen.getByRole('button', { name: '文字颜色 #e74c3c' }));
    await user.click(screen.getByRole('button', { name: '保存草稿' }));

    await waitFor(() => {
      expect(screen.getByText('草稿状态：已保存')).not.toBeNull();
    });
    expect(screen.getByRole('button', { name: '加粗' }).getAttribute('aria-pressed')).toBe('true');
    expect(execCommand).toHaveBeenCalledWith('foreColor', false, '#e74c3c');
  });

  it('未选中文字时选择颜色会切换后续输入颜色', async () => {
    const user = userEvent.setup();
    let activeColor = '';
    const execCommand = vi.fn((command: string, _showUi?: boolean, value?: string) => {
      const editor = screen.getByLabelText('正文') as HTMLDivElement;
      if (command === 'foreColor' && value) {
        activeColor = value;
        return true;
      }
      return true;
    });
    Object.defineProperty(document, 'execCommand', {
      configurable: true,
      value: execCommand,
    });
    renderCompose();

    const editor = screen.getByLabelText('正文') as HTMLDivElement;
    await user.click(editor);
    await user.click(screen.getByRole('button', { name: '文字颜色' }));
    await user.click(screen.getByRole('button', { name: '文字颜色 #e74c3c' }));
    editor.innerHTML = `<font color="${activeColor}">未选中文字也变色</font>`;
    fireEvent.input(editor);

    expect(activeColor).toBe('#e74c3c');
    expect(editor.innerHTML).toContain('<font color="#e74c3c">未选中文字也变色</font>');
  });

  it('加粗斜体下划线删除线可多选并可取消选中', async () => {
    const user = userEvent.setup();
    const commandState: Record<string, boolean> = {
      bold: false,
      italic: false,
      underline: false,
      strikeThrough: false,
    };
    const execCommand = vi.fn((command: string) => {
      commandState[command] = !commandState[command];
      return true;
    });
    Object.defineProperty(document, 'execCommand', {
      configurable: true,
      value: execCommand,
    });
    Object.defineProperty(document, 'queryCommandState', {
      configurable: true,
      value: vi.fn((command: string) => commandState[command] ?? false),
    });
    renderCompose();

    const boldButton = screen.getByRole('button', { name: '加粗' });
    const italicButton = screen.getByRole('button', { name: '斜体' });
    const underlineButton = screen.getByRole('button', { name: '下划线' });
    const strikeButton = screen.getByRole('button', { name: '删除线' });

    await user.click(screen.getByLabelText('正文'));
    await user.click(boldButton);
    await user.click(italicButton);
    await user.click(underlineButton);
    await user.click(strikeButton);

    expect(boldButton.getAttribute('aria-pressed')).toBe('true');
    expect(italicButton.getAttribute('aria-pressed')).toBe('true');
    expect(underlineButton.getAttribute('aria-pressed')).toBe('true');
    expect(strikeButton.getAttribute('aria-pressed')).toBe('true');

    await user.click(italicButton);
    await user.click(strikeButton);

    expect(boldButton.getAttribute('aria-pressed')).toBe('true');
    expect(italicButton.getAttribute('aria-pressed')).toBe('false');
    expect(underlineButton.getAttribute('aria-pressed')).toBe('true');
    expect(strikeButton.getAttribute('aria-pressed')).toBe('false');
    expect(execCommand).toHaveBeenCalledWith('bold', false);
    expect(execCommand).toHaveBeenCalledWith('italic', false);
    expect(execCommand).toHaveBeenCalledWith('underline', false);
    expect(execCommand).toHaveBeenCalledWith('strikeThrough', false);
  });

  it('取消全部文字样式后后续输入不继承旧样式', async () => {
    const user = userEvent.setup();
    const commandState: Record<string, boolean> = {
      bold: false,
      underline: false,
      italic: false,
      strikeThrough: false,
    };
    Object.defineProperty(document, 'execCommand', {
      configurable: true,
      value: vi.fn((command: string) => {
        commandState[command] = !commandState[command];
        return true;
      }),
    });
    Object.defineProperty(document, 'queryCommandState', {
      configurable: true,
      value: vi.fn((command: string) => commandState[command] ?? false),
    });
    renderCompose();

    await user.click(screen.getByLabelText('正文'));
    await user.click(screen.getByRole('button', { name: '加粗' }));
    await user.click(screen.getByRole('button', { name: '下划线' }));
    expect(screen.getByRole('button', { name: '加粗' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('button', { name: '下划线' }).getAttribute('aria-pressed')).toBe('true');

    await user.click(screen.getByRole('button', { name: '加粗' }));
    await user.click(screen.getByRole('button', { name: '下划线' }));

    expect(screen.getByRole('button', { name: '加粗' }).getAttribute('aria-pressed')).toBe('false');
    expect(screen.getByRole('button', { name: '下划线' }).getAttribute('aria-pressed')).toBe('false');
    expect((screen.getByLabelText('正文') as HTMLDivElement).innerHTML).toContain('data-style-reset="true"');
  });

  it('开启文字样式后只影响当前输入文本，不回改已有正文', async () => {
    const user = userEvent.setup();
    const current = { bold: true, italic: false, underline: false, strikeThrough: false };
    Object.defineProperty(document, 'queryCommandState', {
      configurable: true,
      value: vi.fn((command: string) => current[command as keyof typeof current] ?? false),
    });
    Object.defineProperty(document, 'execCommand', {
      configurable: true,
      value: vi.fn(() => true),
    });
    renderCompose();

    const editor = screen.getByLabelText('正文') as HTMLDivElement;
    await user.click(editor);
    await user.click(screen.getByRole('button', { name: '加粗' }));
    editor.innerHTML = '已有文本<span>新输入</span>';
    const textNode = editor.querySelector('span')?.firstChild as Text;
    const range = document.createRange();
    range.setStart(textNode, textNode.textContent?.length ?? 0);
    range.collapse(true);
    window.getSelection()?.removeAllRanges();
    window.getSelection()?.addRange(range);

    fireEvent.input(editor);

    expect(editor.innerHTML).toBe('已有文本<span><strong>新输入</strong></span>');
  });
});
