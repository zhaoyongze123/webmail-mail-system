import { expect, test, type Page } from '@playwright/test';

async function mockMailApi(page: Page) {
  let draftRequests = 0;
  let draftPatchRequests = 0;
  let sendRequests = 0;
  let settingsSaveCount = 0;
  let operationRequests = 0;
  let attachmentChunkRequests = 0;
  let lastSendPayload: Record<string, unknown> | null = null;
  let draftState: { draft_id: string; payload: Record<string, unknown> } | null = null;
  let releaseSend: (() => void) | null = null;
  let inboxMessageRead = false;
  let inboxUnreadCount = 1;

  await page.route('**/api/signatures/default', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: {
          signature: null,
        },
        error: null,
      }),
    });
  });

  await page.route('**/api/signatures?**', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: {
          signatures: [],
        },
        error: null,
      }),
    });
  });

  await page.route('**/api/folders', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: {
          folders: [
            { name: 'INBOX', display_name: '收件箱', type: 'inbox', unread_count: inboxUnreadCount, total_count: 1 },
            { name: '.Sent', display_name: '已发送', type: 'sent', unread_count: 0, total_count: 1 },
          ],
        },
        error: null,
      }),
    });
  });

  await page.route('**/api/folders/INBOX/messages?**', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: {
          folder: 'INBOX',
          page: 1,
          page_size: 30,
          total: 1,
          messages: [
            {
              uid: '101',
              subject: '客户报价确认',
              sender: { name: 'Alice', email: 'alice@example.com' },
              date: '2026-05-07T09:00:00+08:00',
              read: inboxMessageRead,
              has_attachments: true,
              snippet: '请查看附件里的报价。',
            },
          ],
        },
        error: null,
      }),
    });
  });

  await page.route('**/api/folders/INBOX/messages/search?**', async (route) => {
    const url = new URL(route.request().url());
    const query = url.searchParams.get('q') ?? '';
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: {
          folder: 'INBOX',
          page: 1,
          page_size: 30,
          total: query ? 1 : 0,
          messages: query
            ? [
                {
                  uid: '101',
                  subject: '客户报价确认',
                  sender: { name: 'Alice', email: 'alice@example.com' },
                  date: '2026-05-07T09:00:00+08:00',
                  read: false,
                  has_attachments: true,
                  snippet: '请查看附件里的报价。',
                },
              ]
            : [],
          query,
        },
        error: null,
      }),
    });
  });

  await page.route('**/api/folders/.Sent/messages?**', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: {
          folder: '.Sent',
          page: 1,
          page_size: 30,
          total: 1,
          messages: [
            {
              uid: '201',
              subject: '已发送回执',
              sender: { name: 'User', email: 'user@example.com' },
              date: '2026-05-07T08:30:00+08:00',
              read: true,
              has_attachments: false,
              snippet: '这是已发送文件夹中的邮件。',
            },
          ],
        },
        error: null,
      }),
    });
  });

  await page.route('**/api/folders/INBOX/messages/101', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: {
          uid: '101',
          folder: 'INBOX',
          subject: '客户报价确认',
          from: { name: 'Alice', email: 'alice@example.com' },
          to: [{ name: 'User', email: 'user@example.com' }],
          date: '2026-05-07T09:00:00+08:00',
          html_body:
            '<style>p.notice{color:#e74c3c;} table td{border:1px solid #d8dee9;}</style><p class="notice">报价见附件</p><table><tbody><tr><td>单元格</td></tr></tbody></table><img src="data:image/png;base64,aGVsbG8=" alt="内联图片"><script>window.__xss = true</script><a href="javascript:window.__xss = true">危险链接</a>',
          text_body: '报价见附件',
          attachments: [{ attachment_id: 'att-1', filename: 'quote.txt', content_type: 'text/plain', size_bytes: 12 }],
        },
        error: null,
      }),
    });
  });

  await page.context().route('**/api/folders/INBOX/messages/101/attachments/**', async (route) => {
    await route.fulfill({
      contentType: 'text/plain',
      headers: { 'Content-Disposition': 'attachment; filename="quote.txt"' },
      body: 'quote bytes',
    });
  });

  await page.route('**/api/contacts?**', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: { contacts: [{ email: 'receiver@example.com', last_used_at: '2026-05-07T10:00:00+08:00' }] },
        error: null,
      }),
    });
  });

  await page.route('**/api/attachments/chunks', async (route) => {
    attachmentChunkRequests += 1;
    const body = route.request().postData() ?? '';
    const readField = (field: string) => {
      const match = body.match(new RegExp(`name="${field}"\\r?\\n\\r?\\n([^\\r\\n]+)`));
      return match?.[1] ?? '';
    };
    const filename = readField('filename') || 'plan.txt';
    const contentType = readField('content_type') || 'text/plain';
    const fileSizeBytes = Number(readField('file_size_bytes') || '0');
    const totalChunks = Number(readField('total_chunks') || '1');
    const chunkIndex = Number(readField('chunk_index') || '0');
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: {
          attachment: {
            attachment_id: `upload-${attachmentChunkRequests}`,
            filename,
            content_type: contentType,
            size_bytes: fileSizeBytes,
            expires_at: '2026-05-07T10:00:00+08:00',
            complete: chunkIndex + 1 >= totalChunks,
            uploaded_chunks: chunkIndex + 1,
            total_chunks: totalChunks,
          },
        },
        error: null,
      }),
    });
  });

  await page.route('**/api/drafts/**', async (route) => {
    const url = new URL(route.request().url());
    const draftId = decodeURIComponent(url.pathname.split('/').pop() ?? '');
    if (route.request().method() === 'GET') {
      if (!draftState || draftState.draft_id !== draftId) {
        await route.fulfill({
          contentType: 'application/json',
          status: 404,
          body: JSON.stringify({
            success: false,
            data: null,
            error: { code: 'DRAFT_NOT_FOUND', message: '草稿不存在' },
          }),
        });
        return;
      }
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: {
            ...draftState.payload,
            draft_id: draftState.draft_id,
          },
          error: null,
        }),
      });
      return;
    }
    if (route.request().method() === 'PATCH') {
      draftPatchRequests += 1;
      draftState = { draft_id: draftId, payload: route.request().postDataJSON() as Record<string, unknown> };
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: { ...draftState.payload, draft_id: draftId, status: 'saved', saved_at: '2026-05-07T10:00:00+08:00' },
          error: null,
        }),
      });
    }
  });

  await page.route('**/api/drafts', async (route) => {
    draftRequests += 1;
    const payload = route.request().postDataJSON() as Record<string, unknown>;
    const draftId = `draft-${draftRequests}`;
    draftState = { draft_id: draftId, payload: { ...payload } };
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: { draft_id: draftId, status: 'saved', saved_at: '2026-05-07T10:00:00+08:00' },
        error: null,
      }),
    });
  });

  await page.route('**/api/messages/send', async (route) => {
    sendRequests += 1;
    lastSendPayload = route.request().postDataJSON() as Record<string, unknown>;
    await new Promise<void>((resolve) => {
      releaseSend = resolve;
    });
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: { message_id: '<msg-1@example.com>', sent: true, archived_folder: '.Sent' },
        error: null,
      }),
    });
  });

  await page.route('**/api/folders/INBOX/messages/operations', async (route) => {
    operationRequests += 1;
    const payload = route.request().postDataJSON() as { action: string; uids: string[] };
    if (payload.action === 'mark_read') {
      inboxMessageRead = true;
      inboxUnreadCount = 0;
    }
    if (payload.action === 'mark_unread') {
      inboxMessageRead = false;
      inboxUnreadCount = 1;
    }
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: { action: payload.action, folder: 'INBOX', uids: payload.uids },
        error: null,
      }),
    });
  });

  await page.route('**/api/messages/move?**', async (route) => {
    const payload = route.request().postDataJSON() as { folder: string; uids: string[]; target_folder: string };
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: { action: 'move', folder: payload.folder, target_folder: payload.target_folder, uids: payload.uids },
        error: null,
      }),
    });
  });

  await page.route('**/api/messages/delete?**', async (route) => {
    const payload = route.request().postDataJSON() as { folder: string; uids: string[] };
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: { action: 'delete', folder: payload.folder, uids: payload.uids },
        error: null,
      }),
    });
  });

  await page.route('**/api/settings', async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: {
            account: { email: 'user@example.com' },
            preferences: { page_size: 30, mark_read_on_open: true },
          },
          error: null,
        }),
      });
      return;
    }
    if (route.request().method() === 'PUT') {
      settingsSaveCount += 1;
      const payload = route.request().postDataJSON() as { page_size: number; mark_read_on_open: boolean };
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          data: {
            account: { email: 'user@example.com' },
            preferences: payload,
          },
          error: null,
        }),
      });
    }
  });

  return {
    getDraftRequests: () => draftRequests,
    getDraftPatchRequests: () => draftPatchRequests,
    getSettingsSaveCount: () => settingsSaveCount,
    getSendRequests: () => sendRequests,
    getLastSendPayload: () => lastSendPayload,
    getOperationRequests: () => operationRequests,
    releaseSend: () => releaseSend?.(),
  };
}

test('进入邮件工作台后加载账号、文件夹和邮件列表', async ({ page }) => {
  await mockMailApi(page);

  await page.goto('/');

  await expect(page.getByText('user@example.com')).toBeVisible();
  await expect(page.locator('.nav-item-left').filter({ hasText: '收件箱' })).toBeVisible();
  await expect(page.getByText('客户报价确认')).toBeVisible();
});

test('工作台可切换文件夹并在桌面和移动尺寸渲染', async ({ page }) => {
  await mockMailApi(page);

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  await expect(page.getByText('客户报价确认')).toBeVisible();
  await page.screenshot({ path: 'test-results/t20-desktop-workspace.png', fullPage: true });

  await page.getByText('已发送').click();
  await expect(page.getByText('已发送回执')).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: 'test-results/t20-mobile-workspace.png', fullPage: true });
  await expect(page.getByText('文件夹', { exact: true })).toBeVisible();
});

test('工作台支持搜索、读信标记和设置保存', async ({ page }) => {
  const mailApi = await mockMailApi(page);

  await page.goto('/');
  await expect(page.getByText('客户报价确认')).toBeVisible();

  await page.getByPlaceholder('搜索邮件...').fill('客户');
  await page.keyboard.press('Enter');
  await expect(page.getByText('搜索: 客户')).toBeVisible();

  await page.getByText('客户报价确认').click();
  await expect(page.getByText('报价见附件')).toBeVisible();
  await expect.poll(() => mailApi.getOperationRequests()).toBeGreaterThan(0);

  await page.getByRole('button', { name: '打开设置' }).click();
  await expect(page.getByRole('dialog', { name: '设置' })).toBeVisible();
  await expect(page.getByRole('heading', { name: '系统设置' })).toBeVisible();
  await page.getByLabel('每页显示邮件数').selectOption('50');
  await page.getByRole('button', { name: '保存' }).click();
  await expect.poll(() => mailApi.getSettingsSaveCount()).toBe(1);
});

test('可打开邮件详情并执行邮件操作', async ({ page }) => {
  const mailApi = await mockMailApi(page);

  await page.goto('/');
  await page.getByText('客户报价确认').click();

  await expect(page.getByText('报价见附件')).toBeVisible();
  await expect(page.locator('.reading-pane .field-value').filter({ hasText: 'Alice' })).toBeVisible();
  await expect(page.getByText('<alice@example.com>')).toBeVisible();

  await page.getByRole('button', { name: '标为未读' }).click();
  await expect.poll(() => mailApi.getOperationRequests()).toBeGreaterThan(1);
});

test('未读角标会随读信和重新标记未读同步变化', async ({ page }) => {
  await mockMailApi(page);

  await page.goto('/');
  const inboxBadge = page.locator('.nav-item').filter({ hasText: '收件箱' }).locator('.badge');
  await expect(inboxBadge).toHaveText('1');

  await page.getByText('客户报价确认').click();
  await expect(inboxBadge).toHaveCount(0);

  await page.getByRole('button', { name: '标为未读' }).click();
  await expect(inboxBadge).toHaveText('1');
});

test('HTML 邮件在阅读区直接渲染且脚本不会执行', async ({ page }) => {
  await mockMailApi(page);

  await page.goto('/');
  await page.getByText('客户报价确认').click();

  const htmlBody = page.locator('[data-testid="app-message-html-body"]');
  await expect(htmlBody).toHaveJSProperty('tagName', 'DIV');
  await expect(htmlBody.getByText('报价见附件')).toBeVisible();
  await expect(htmlBody.locator('table td')).toHaveText('单元格');
  await expect(htmlBody.locator('img')).toHaveAttribute('src', /data:image\/png/);
  await expect(htmlBody.locator('script')).toHaveCount(0);
  await expect.poll(async () => page.evaluate(() => (window as Window & { __xss?: boolean }).__xss)).toBeUndefined();
});

test('写信支持上传附件、保存草稿、发送并防重复发送', async ({ page }) => {
  const mailApi = await mockMailApi(page);

  await page.goto('/');
  await page.getByRole('button', { name: '写邮件' }).click();
  await expect(page.getByRole('complementary', { name: '写信面板' })).toBeVisible();
  await expect(page.getByLabel('地址栏').getByText('user@example.com')).toBeVisible();
  await page.getByLabel('收件人').fill('receiver@example.com');
  await page.getByLabel('主题').fill('Playwright 发信');
  await page.getByRole('textbox', { name: '正文' }).fill('端到端正文');
  await page.getByLabel('添加附件').setInputFiles({
    name: 'plan.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('hello world'),
  });
  await expect(page.getByLabel('附件列表')).toContainText('plan.txt');

  await page.getByRole('button', { name: '保存草稿' }).click();
  await expect(page.getByText('草稿状态：已保存')).toBeVisible();
  expect(mailApi.getDraftRequests()).toBe(1);

  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(page.getByRole('button', { name: '发送中' })).toBeDisabled();
  expect(mailApi.getSendRequests()).toBe(1);
  mailApi.releaseSend();
  await expect(page.getByRole('complementary', { name: '写信面板' })).toBeHidden();
  expect(mailApi.getSendRequests()).toBe(1);
});

test('写信可上传多个附件并看到分块进度', async ({ page }) => {
  await mockMailApi(page);

  let resolveSecondChunk: (() => void) | null = null;
  let chunkCalls = 0;
  await page.route('**/api/attachments/chunks', async (route) => {
    chunkCalls += 1;
    const body = route.request().postData() ?? '';
    const readField = (field: string) => {
      const match = body.match(new RegExp(`name="${field}"\\r?\\n\\r?\\n([^\\r\\n]+)`));
      return match?.[1] ?? '';
    };
    const filename = readField('filename');
    const fileSizeBytes = Number(readField('file_size_bytes') || '0');
    const totalChunks = Number(readField('total_chunks') || '1');
    const chunkIndex = Number(readField('chunk_index') || '0');
    if (filename === 'large.bin' && chunkIndex === 1) {
      await new Promise<void>((resolve) => {
        resolveSecondChunk = resolve;
      });
    }
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: {
          attachment: {
            attachment_id: `upload-${chunkCalls}`,
            filename,
            content_type: readField('content_type') || 'application/octet-stream',
            size_bytes: fileSizeBytes,
            expires_at: '2026-05-07T10:00:00+08:00',
            complete: chunkIndex + 1 >= totalChunks,
            uploaded_chunks: chunkIndex + 1,
            total_chunks: totalChunks,
          },
        },
        error: null,
      }),
    });
  });

  await page.goto('/');
  await page.getByRole('button', { name: '写邮件' }).click();
  await expect(page.getByRole('complementary', { name: '写信面板' })).toBeVisible();

  await page.setInputFiles('input[aria-label="添加附件"]', [
    {
      name: 'small.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('small'),
    },
    {
      name: 'large.bin',
      mimeType: 'application/octet-stream',
      buffer: Buffer.alloc(1024 * 1024 + 64, 1),
    },
  ]);

  const attachmentList = page.getByRole('list', { name: '附件列表' });
  await expect(attachmentList).toContainText('small.txt');
  await expect(attachmentList).toContainText('large.bin');
  await expect(page.getByRole('progressbar', { name: 'small.txt 上传进度' })).toHaveJSProperty('value', 100);
  await expect.poll(async () =>
    page.getByRole('progressbar', { name: 'large.bin 上传进度' }).evaluate((node) => (node as HTMLProgressElement).value),
  ).toBe(50);
  resolveSecondChunk?.();
  await expect(page.getByRole('progressbar', { name: 'large.bin 上传进度' })).toHaveJSProperty('value', 100);
  await expect(attachmentList).toContainText('已上传');
});

test('写信可插入内嵌图片并调整位置和大小后发送草稿', async ({ page }) => {
  const mailApi = await mockMailApi(page);
  await page.addInitScript(() => {
    class MockFileReader {
      result: string | ArrayBuffer | null = null;
      onload: ((this: FileReader, ev: ProgressEvent<FileReader>) => unknown) | null = null;

      readAsDataURL(file: File) {
        this.result = `data:${file.type};base64,aW5saW5lLWltYWdl`;
        this.onload?.call(this as unknown as FileReader, {} as ProgressEvent<FileReader>);
      }
    }
    Object.defineProperty(window, 'FileReader', {
      configurable: true,
      value: MockFileReader,
    });
  });

  await page.goto('/');
  await page.getByRole('button', { name: '写邮件' }).click();
  await expect(page.getByRole('complementary', { name: '写信面板' })).toBeVisible();
  await page.locator('input[aria-label="插入图片"]').setInputFiles({
    name: 'inline.png',
    mimeType: 'image/png',
    buffer: Buffer.from('inline image'),
  });

  await expect(page.locator('.inline-image img')).toBeVisible();
  await page.locator('.inline-image img').click();
  await expect(page.getByRole('button', { name: '图片居中' })).toBeVisible();
  await page.getByRole('button', { name: '图片右对齐' }).click();
  const sizeSlider = page.getByRole('slider', { name: '图片大小' });
  await sizeSlider.evaluate((element) => {
    const input = element as HTMLInputElement;
    const descriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
    descriptor?.set?.call(input, '420');
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await page.locator('.body-input.rich-body-input').click({ position: { x: 380, y: 80 } });
  const recipientInput = page.getByLabel('收件人');
  await recipientInput.fill('receiver@example.com');

  await page.getByRole('button', { name: '保存草稿' }).click();
  await expect(page.getByText('草稿状态：已保存')).toBeVisible();
  expect(mailApi.getDraftRequests()).toBe(1);

  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(page.getByRole('button', { name: '发送中' })).toBeDisabled();
  await expect.poll(() => mailApi.getSendRequests()).toBe(1);
  await expect.poll(() => mailApi.getLastSendPayload()).not.toBeNull();
  expect(mailApi.getLastSendPayload()).toMatchObject({
    to: ['receiver@example.com'],
    subject: '',
  });
  expect(String(mailApi.getLastSendPayload()?.html_body)).toContain('class="inline-image"');
  expect(String(mailApi.getLastSendPayload()?.html_body)).toContain('text-align: right');
  expect(String(mailApi.getLastSendPayload()?.html_body)).toContain('width: 420px');
  expect(String(mailApi.getLastSendPayload()?.html_body)).toContain('data:image/png;base64,aW5saW5lLWltYWdl');
  expect(String(mailApi.getLastSendPayload()?.html_body)).not.toContain('data-inline-image-id');
  expect(String(mailApi.getLastSendPayload()?.html_body)).not.toContain('data-selected');
  mailApi.releaseSend();
  await expect(page.getByRole('complementary', { name: '写信面板' })).toBeHidden();
});

test('写信保存草稿后刷新可恢复内容', async ({ page }) => {
  const mailApi = await mockMailApi(page);

  await page.goto('/');
  await page.getByRole('button', { name: '写邮件' }).click();
  await expect(page.getByRole('complementary', { name: '写信面板' })).toBeVisible();
  await page.getByLabel('收件人').fill('receiver@example.com');
  await page.getByLabel('收件人').press('Enter');
  await page.getByLabel('主题').fill('刷新恢复草稿');
  await page.getByRole('textbox', { name: '正文' }).fill('刷新前正文');
  await page.getByLabel('添加附件').setInputFiles({
    name: 'plan.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('restore'),
  });
  await expect(page.getByLabel('附件列表')).toContainText('plan.txt');

  await page.getByRole('button', { name: '保存草稿' }).click();
  await expect(page.getByText('草稿状态：已保存')).toBeVisible();
  expect(mailApi.getDraftRequests()).toBe(1);

  await page.reload();
  await expect(page.getByRole('complementary', { name: '写信面板' })).toBeVisible();
  await expect(page.getByRole('button', { name: '选择 receiver@example.com' })).toBeVisible();
  await expect(page.getByLabel('收件人')).toHaveValue('');
  await expect(page.getByLabel('主题')).toHaveValue('刷新恢复草稿');
  await expect(page.getByRole('textbox', { name: '正文' })).toContainText('刷新前正文');
  await expect(page.getByLabel('附件列表')).toContainText('plan.txt');
});

test('写信可切换到纯文本模式并发送不含 HTML 的邮件', async ({ page }) => {
  const mailApi = await mockMailApi(page);

  await page.goto('/');
  await page.getByRole('button', { name: '写邮件' }).click();
  await expect(page.getByRole('complementary', { name: '写信面板' })).toBeVisible();
  await page.getByRole('button', { name: '纯文本' }).click();
  await page.getByLabel('收件人').fill('receiver@example.com');
  await page.getByLabel('收件人').press('Enter');
  await page.getByLabel('主题').fill('纯文本模式邮件');
  await page.getByRole('textbox', { name: '正文' }).fill('第一行\n第二行');

  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(page.getByRole('button', { name: '发送中' })).toBeDisabled();
  expect(mailApi.getSendRequests()).toBe(1);
  expect(mailApi.getLastSendPayload()).toMatchObject({
    to: ['receiver@example.com'],
    subject: '纯文本模式邮件',
    text_body: '第一行\n第二行',
    html_body: null,
  });
  mailApi.releaseSend();
  await expect(page.getByRole('complementary', { name: '写信面板' })).toBeHidden();
});
