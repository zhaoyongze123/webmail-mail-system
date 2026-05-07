import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';

const mockFetch = vi.fn();

function mockApiResponse(body: unknown, ok = true) {
  return {
    ok,
    json: async () => body,
  } as Response;
}

function setPath(pathname: string) {
  window.history.pushState({}, '', pathname);
}

describe('App 登录与路由', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', mockFetch);
    mockFetch.mockReset();
    setPath('/');
  });

  it('登录成功后进入 /mail', async () => {
    const user = userEvent.setup();
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/api/auth/me')) {
        return Promise.resolve(
          mockApiResponse(
            { success: false, data: null, error: { code: 'AUTH_SESSION_EXPIRED', message: '登录已过期，请重新登录' } },
            false,
          ),
        );
      }
      if (url.endsWith('/api/auth/login')) {
        return Promise.resolve(mockApiResponse({ success: true, data: { email: 'user@example.com' }, error: null }));
      }
      throw new Error(`unexpected request: ${url}`);
    });

    render(<App />);

    await screen.findByRole('heading', { name: '登录邮箱' });
    await user.type(screen.getByLabelText('邮箱地址'), 'user@example.com');
    await user.type(screen.getByLabelText('密码'), 'correct-password');
    await user.click(screen.getByRole('button', { name: '登录' }));

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: '邮件工作台' })).not.toBeNull();
    });
    expect(window.location.pathname).toBe('/mail');
    expect(screen.getByText('user@example.com')).not.toBeNull();
  });

  it('错误密码时展示错误信息', async () => {
    const user = userEvent.setup();
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/api/auth/me')) {
        return Promise.resolve(
          mockApiResponse(
            { success: false, data: null, error: { code: 'AUTH_SESSION_EXPIRED', message: '登录已过期，请重新登录' } },
            false,
          ),
        );
      }
      if (url.endsWith('/api/auth/login')) {
        return Promise.resolve(mockApiResponse({ success: false, data: null, error: { code: 'AUTH_INVALID_CREDENTIALS', message: '邮箱或密码不正确' } }, false));
      }
      throw new Error(`unexpected request: ${url}`);
    });

    render(<App />);

    await screen.findByRole('heading', { name: '登录邮箱' });
    await user.type(screen.getByLabelText('邮箱地址'), 'user@example.com');
    await user.type(screen.getByLabelText('密码'), 'wrong-password');
    await user.click(screen.getByRole('button', { name: '登录' }));

    await waitFor(() => {
      const alert = screen.getByRole('alert');
      expect(alert.textContent).toContain('邮箱或密码不正确');
    });
    expect(screen.getByRole('heading', { name: '登录邮箱' })).not.toBeNull();
  });

  it('/mail 未登录时跳转 /login', async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/api/auth/me')) {
        return Promise.resolve(
          mockApiResponse(
            { success: false, data: null, error: { code: 'AUTH_SESSION_EXPIRED', message: '登录已过期，请重新登录' } },
            false,
          ),
        );
      }
      throw new Error(`unexpected request: ${url}`);
    });

    setPath('/mail');
    render(<App />);

    await waitFor(() => {
      expect(window.location.pathname).toBe('/login');
    });
    expect(screen.getByRole('heading', { name: '登录邮箱' })).not.toBeNull();
  });

  it('已登录进入 /mail', async () => {
    mockFetch.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/api/auth/me')) {
        return Promise.resolve(mockApiResponse({ success: true, data: { email: 'user@example.com' }, error: null }));
      }
      throw new Error(`unexpected request: ${url}`);
    });

    window.sessionStorage.setItem('webmail.session', JSON.stringify({ email: 'user@example.com' }));
    setPath('/mail');
    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: '邮件工作台' })).not.toBeNull();
    });
    expect(window.location.pathname).toBe('/mail');
  });
});
