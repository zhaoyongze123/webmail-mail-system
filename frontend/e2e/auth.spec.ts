import { expect, test, type Page } from '@playwright/test';

async function mockMailApi(page: Page) {
  let draftRequests = 0;
  let sendRequests = 0;
  let settingsSaveCount = 0;
  let operationRequests = 0;
  let releaseSend: (() => void) | null = null;

  await page.route('**/api/folders', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: {
          folders: [
            { name: 'INBOX', display_name: '收件箱', type: 'inbox', unread_count: 1, total_count: 1 },
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
              read: false,
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
          html_body: '<p>报价见附件</p><script>window.__xss = true</script><a href="javascript:window.__xss = true">危险链接</a>',
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

  await page.route('**/api/attachments', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: {
          attachments: [{ attachment_id: 'upload-1', filename: 'plan.txt', content_type: 'text/plain', size_bytes: 11 }],
        },
        error: null,
      }),
    });
  });

  await page.route('**/api/drafts', async (route) => {
    draftRequests += 1;
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: { draft_id: `draft-${draftRequests}`, status: 'saved', saved_at: '2026-05-07T10:00:00+08:00' },
        error: null,
      }),
    });
  });

  await page.route('**/api/messages/send', async (route) => {
    sendRequests += 1;
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
    getSettingsSaveCount: () => settingsSaveCount,
    getSendRequests: () => sendRequests,
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

  await page.getByText('系统设置').click();
  await expect(page.getByRole('heading', { name: '系统设置' })).toBeVisible();
  await page.locator('.settings-field select').selectOption('50');
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

  await page.getByRole('button', { name: '标为已读' }).click();
  await expect.poll(() => mailApi.getOperationRequests()).toBeGreaterThan(1);
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
