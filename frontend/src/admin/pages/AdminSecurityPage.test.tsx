import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
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
  it('会渲染修改密码和 TOTP 管理入口', () => {
    renderPage();

    expect(screen.getByRole('heading', { name: '修改密码' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'TOTP 管理' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '更新密码' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '初始化 TOTP' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '启用 TOTP' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '停用 TOTP' })).toBeInTheDocument();
  });
});
