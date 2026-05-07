import { expect, test, type Page } from '@playwright/test';

const authExpired = {
  success: false,
  data: null,
  error: {
    code: 'AUTH_SESSION_EXPIRED',
    message: '登录已过期，请重新登录',
    details: {},
  },
  request_id: 'req_e2e',
};

const invalidCredentials = {
  success: false,
  data: null,
  error: {
    code: 'AUTH_INVALID_CREDENTIALS',
    message: '邮箱或密码不正确',
    details: {},
  },
  request_id: 'req_e2e',
};

const currentUser = {
  success: true,
  data: { email: 'user@example.com' },
  error: null,
  request_id: 'req_e2e',
};

async function mockAuth(page: Page, loginResponse: typeof invalidCredentials | typeof currentUser) {
  await page.route('**/api/auth/me', async (route) => {
    await route.fulfill({
      status: 401,
      contentType: 'application/json',
      body: JSON.stringify(authExpired),
    });
  });
  await page.route('**/api/auth/login', async (route) => {
    await route.fulfill({
      status: loginResponse === currentUser ? 200 : 401,
      contentType: 'application/json',
      body: JSON.stringify(loginResponse),
    });
  });
}

async function mockMailApi(page: Page) {
  let draftRequests = 0;
  let sendRequests = 0;
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

  return {
    getDraftRequests: () => draftRequests,
    getSendRequests: () => sendRequests,
    releaseSend: () => releaseSend?.(),
  };
}

test('未登录访问邮箱会跳转登录并展示错误密码提示', async ({ page }) => {
  await mockAuth(page, invalidCredentials);

  await page.goto('/mail');
  await page.waitForURL('**/login');
  await expect(page.getByRole('heading', { name: '登录邮箱' })).toBeVisible();

  await page.getByLabel('邮箱地址').fill('user@example.com');
  await page.getByLabel('密码').fill('wrong-password');
  await page.getByRole('button', { name: '登录' }).click();

  await expect(page.getByRole('alert')).toContainText('邮箱或密码不正确');
});

test('登录成功后进入邮箱工作台', async ({ page }) => {
  await mockAuth(page, currentUser);
  await mockMailApi(page);

  await page.goto('/login');
  await page.getByLabel('邮箱地址').fill('user@example.com');
  await page.getByLabel('密码').fill('correct-password');
  await page.getByRole('button', { name: '登录' }).click();

  await page.waitForURL('**/mail');
  await expect(page.getByRole('heading', { name: '邮件工作台' })).toBeVisible();
  await expect(page.getByLabel('当前账号')).toContainText('user@example.com');
  await expect(page.getByRole('navigation', { name: '文件夹' })).toContainText('收件箱');
  await expect(page.getByRole('region', { name: '邮件列表' })).toContainText('客户报价确认');
});

test('工作台可切换文件夹并在桌面和移动尺寸渲染', async ({ page }) => {
  await mockAuth(page, currentUser);
  await mockMailApi(page);

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/login');
  await page.getByLabel('邮箱地址').fill('user@example.com');
  await page.getByLabel('密码').fill('correct-password');
  await page.getByRole('button', { name: '登录' }).click();
  await page.waitForURL('**/mail');
  await expect(page.getByRole('region', { name: '邮件列表' })).toContainText('客户报价确认');
  await page.screenshot({ path: 'test-results/t20-desktop-workspace.png', fullPage: true });

  await page.getByRole('button', { name: /已发送/ }).click();
  await expect(page.getByRole('region', { name: '邮件列表' })).toContainText('已发送回执');

  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: 'test-results/t20-mobile-workspace.png', fullPage: true });
  await expect(page.getByRole('navigation', { name: '文件夹' })).toBeVisible();
});

test('可打开邮件详情、净化危险 HTML 并下载附件', async ({ page }) => {
  await mockAuth(page, currentUser);
  await mockMailApi(page);

  await page.goto('/login');
  await page.getByLabel('邮箱地址').fill('user@example.com');
  await page.getByLabel('密码').fill('correct-password');
  await page.getByRole('button', { name: '登录' }).click();
  await page.waitForURL('**/mail');

  await page.getByRole('button', { name: /客户报价确认/ }).click();
  const reader = page.getByRole('article', { name: '邮件阅读区' });
  await expect(reader).toContainText('报价见附件');
  await expect(reader.locator('script')).toHaveCount(0);
  await expect(reader.locator('a[href^="javascript:"]')).toHaveCount(0);
  await expect(reader).toContainText('quote.txt');
  await expect.poll(() => page.evaluate(() => (window as Window & { __xss?: boolean }).__xss)).toBeUndefined();

  const download = page.waitForEvent('download');
  await page.getByRole('link', { name: '下载' }).click();
  expect((await download).suggestedFilename()).toBe('quote.txt');
});

test('写信支持上传附件、保存草稿、发送并防重复发送', async ({ page }) => {
  await mockAuth(page, currentUser);
  const mailApi = await mockMailApi(page);

  await page.goto('/login');
  await page.getByLabel('邮箱地址').fill('user@example.com');
  await page.getByLabel('密码').fill('correct-password');
  await page.getByRole('button', { name: '登录' }).click();
  await page.waitForURL('**/mail');

  await page.getByRole('button', { name: '新信' }).click();
  await expect(page.getByRole('complementary', { name: '写信面板' })).toBeVisible();
  await page.getByLabel('收件人').fill('receiver@example.com');
  await page.getByLabel('主题').fill('Playwright 发信');
  await page.getByLabel('正文').fill('端到端正文');
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
