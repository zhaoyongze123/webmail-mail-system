import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AdminAuthProvider, RequireAdminAuth } from './auth';
import { AdminLoginPage } from './pages/AdminLoginPage';

function renderWithProviders(initialEntries: string[], element: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <AdminAuthProvider>
        <MemoryRouter initialEntries={initialEntries}>
          <Routes>
            <Route path="/admin/login" element={<AdminLoginPage />} />
            <Route path="/admin" element={<RequireAdminAuth />}>
              <Route index element={element} />
            </Route>
          </Routes>
        </MemoryRouter>
      </AdminAuthProvider>
    </QueryClientProvider>,
  );
}

describe('admin auth router', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });

  it('未登录访问 /admin 时会跳转到登录页', async () => {
    renderWithProviders(['/admin'], <div>后台首页</div>);

    expect(await screen.findByRole('heading', { name: '管理员登录' })).toBeInTheDocument();
  });

  it('登录页会渲染账号和密码输入框', async () => {
    renderWithProviders(['/admin/login'], <div>后台首页</div>);

    expect(await screen.findByLabelText('管理员账号')).toHaveValue('admin');
    expect(screen.getByLabelText('密码')).toBeInTheDocument();
  });
});
