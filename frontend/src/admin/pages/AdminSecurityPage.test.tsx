import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi, afterEach } from 'vitest';
import { AdminAuthProvider } from '../auth';
import { AdminSecurityPage } from './AdminSecurityPage';

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <AdminAuthProvider>
        <AdminSecurityPage />
      </AdminAuthProvider>
    </QueryClientProvider>,
  );
}

describe('AdminSecurityPage', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('会渲染修改密码和动态口令管理入口', () => {
    renderPage();

    expect(screen.getByRole('heading', { name: '修改密码' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '动态口令管理' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '更新密码' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '初始化动态口令' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '启用动态口令' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '停用动态口令' })).toBeInTheDocument();
  });

  it('会通过统一弹层提交动态口令停用验证码', async () => {
    const fetchMock = vi.fn(async (input: string | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/api/admin/auth/totp/disable')) {
        return new Response(JSON.stringify({
          success: true,
          data: { enabled: false },
          error: null,
        }));
      }
      return new Response(JSON.stringify({
        success: true,
        data: { hasToken: false },
        error: null,
      }));
    });
    vi.stubGlobal('fetch', fetchMock as typeof fetch);

    renderPage();

    fireEvent.click(screen.getByRole('button', { name: '停用动态口令' }));

    const dialog = screen.getByRole('dialog', { name: '确认停用动态口令' });
    expect(dialog).toBeInTheDocument();
    fireEvent.change(within(dialog).getByPlaceholderText('123456'), { target: { value: '654321' } });
    fireEvent.click(within(dialog).getByRole('button', { name: '确认停用' }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/admin/auth/totp/disable',
        expect.objectContaining({
          method: 'POST',
          credentials: 'include',
          body: JSON.stringify({ code: '654321' }),
        }),
      );
    });
    expect(await screen.findByText('动态口令已停用。')).toBeInTheDocument();
  });
});
