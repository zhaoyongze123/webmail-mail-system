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

  await page.goto('/login');
  await page.getByLabel('邮箱地址').fill('user@example.com');
  await page.getByLabel('密码').fill('correct-password');
  await page.getByRole('button', { name: '登录' }).click();

  await page.waitForURL('**/mail');
  await expect(page.getByRole('heading', { name: '邮件工作台' })).toBeVisible();
  await expect(page.getByLabel('当前账号')).toContainText('user@example.com');
});
