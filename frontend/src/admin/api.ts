import type {
  AdminAlias,
  AdminAuditLogItem,
  AdminAuthPayload,
  AdminDomain,
  AdminHealthItem,
  AdminMailboxUser,
  AdminOverviewStats,
  AdminQuotaItem,
} from './types';
import { clearAdminTokens, getAdminAccessToken, getAdminRefreshToken, setAdminTokens } from './token';

type ApiError = {
  code: string;
  message: string;
  details?: Record<string, unknown>;
};

type ApiResponse<T> = {
  success: boolean;
  data: T | null;
  error: ApiError | null;
};

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS', 'TRACE']);

function readCookie(name: string): string | null {
  const prefix = `${encodeURIComponent(name)}=`;
  for (const item of window.document.cookie.split(';')) {
    const trimmed = item.trim();
    if (trimmed.startsWith(prefix)) {
      return decodeURIComponent(trimmed.slice(prefix.length));
    }
  }
  return null;
}

async function parseResponse<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as ApiResponse<T>;
  if (!response.ok || !payload.success) {
    const error = new Error(payload.error?.message || '请求失败，请稍后重试') as Error & { code?: string };
    error.code = payload.error?.code;
    throw error;
  }
  return payload.data as T;
}

async function refreshAdminToken() {
  const refreshToken = getAdminRefreshToken();
  if (!refreshToken) {
    throw new Error('未登录');
  }
  const response = await fetch('/api/admin/auth/refresh', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  const payload = (await response.json()) as ApiResponse<AdminAuthPayload>;
  if (!response.ok || !payload.success || !payload.data) {
    clearAdminTokens();
    const error = new Error(payload.error?.message || '管理员登录已过期') as Error & { code?: string };
    error.code = payload.error?.code || 'ADMIN_AUTH_EXPIRED';
    throw error;
  }
  setAdminTokens(payload.data.access_token, payload.data.refresh_token ?? null);
  return payload.data;
}

async function requestAdminApi<T>(input: string, init?: RequestInit, retryOnAuthFailure = true): Promise<T> {
  const method = (init?.method || 'GET').toUpperCase();
  const headers = {
    'Content-Type': 'application/json',
    ...(init?.headers ?? {}),
  } as Record<string, string>;
  const accessToken = getAdminAccessToken();
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }
  if (!SAFE_METHODS.has(method)) {
    const csrfToken = readCookie('webmail_csrf');
    if (csrfToken) {
      headers['X-CSRF-Token'] = csrfToken;
    }
  }
  const response = await fetch(input, {
    credentials: 'include',
    headers,
    ...init,
  });
  if (response.status === 401 && retryOnAuthFailure) {
    await refreshAdminToken();
    return requestAdminApi<T>(input, init, false);
  }
  return parseResponse<T>(response);
}

export async function adminLogin(payload: { username: string; password: string }): Promise<AdminAuthPayload> {
  const data = await requestAdminApi<AdminAuthPayload>('/api/admin/auth/login', {
    method: 'POST',
    body: JSON.stringify(payload),
  }, false);
  setAdminTokens(data.access_token, data.refresh_token ?? null);
  return data;
}

export async function adminLogout(): Promise<{ logged_out: boolean }> {
  try {
    return await requestAdminApi<{ logged_out: boolean }>('/api/admin/auth/logout', {
      method: 'POST',
    }, false);
  } finally {
    clearAdminTokens();
  }
}

export async function adminChangePassword(payload: { current_password: string; new_password: string }) {
  return requestAdminApi<{ password_updated: boolean }>('/api/admin/auth/change-password', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function adminTotpSetup() {
  return requestAdminApi<{ secret: string; provisioning_uri: string; enabled: boolean }>('/api/admin/auth/totp/setup', {
    method: 'POST',
  });
}

export async function adminTotpEnable(payload: { code: string }) {
  return requestAdminApi<{ enabled: boolean }>('/api/admin/auth/totp/enable', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function adminTotpDisable(payload: { code: string }) {
  return requestAdminApi<{ enabled: boolean }>('/api/admin/auth/totp/disable', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function fetchAdminOverview(): Promise<AdminOverviewStats> {
  return requestAdminApi<AdminOverviewStats>('/api/admin/overview', { method: 'GET' });
}

export async function fetchAdminDomains(): Promise<{ items: AdminDomain[] }> {
  const data = await requestAdminApi<{ items: AdminDomain[]; page?: number; total?: number } | { page: number; page_size: number; total: number; items: AdminDomain[] }>('/api/admin/domains', { method: 'GET' });
  return { items: data.items };
}

export async function createAdminDomain(payload: { name: string; quota_limit_mb: number; status: 'active' | 'disabled' }) {
  return requestAdminApi<{ domain: AdminDomain }>('/api/admin/domains', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function updateAdminDomain(domainId: string, payload: { quota_limit_mb?: number; status?: 'active' | 'disabled' }) {
  return requestAdminApi<{ domain: AdminDomain }>(`/api/admin/domains/${encodeURIComponent(domainId)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function deleteAdminDomain(domainId: string) {
  return requestAdminApi<{ deleted: boolean }>(`/api/admin/domains/${encodeURIComponent(domainId)}`, {
    method: 'DELETE',
  });
}

export async function fetchAdminUsers(): Promise<{ items: AdminMailboxUser[] }> {
  const data = await requestAdminApi<{ items: AdminMailboxUser[]; page?: number; total?: number } | { page: number; page_size: number; total: number; items: AdminMailboxUser[] }>('/api/admin/users', { method: 'GET' });
  return { items: data.items };
}

export async function updateAdminUserQuota(userId: string, quota_mb: number) {
  return requestAdminApi<{ user: AdminMailboxUser }>(`/api/admin/users/${encodeURIComponent(userId)}/quota`, {
    method: 'PATCH',
    body: JSON.stringify({ quota_mb }),
  });
}

export async function fetchAdminAliases(): Promise<{ items: AdminAlias[] }> {
  const data = await requestAdminApi<{ items: AdminAlias[]; page?: number; total?: number } | { page: number; page_size: number; total: number; items: AdminAlias[] }>('/api/admin/aliases', { method: 'GET' });
  return { items: data.items };
}

export async function toggleAdminAlias(aliasId: string) {
  return requestAdminApi<{ alias: AdminAlias }>(`/api/admin/aliases/${encodeURIComponent(aliasId)}/toggle`, {
    method: 'POST',
  });
}

export async function fetchAdminQuotas(): Promise<{ items: AdminQuotaItem[] }> {
  return requestAdminApi<{ items: AdminQuotaItem[] }>('/api/admin/quotas', { method: 'GET' });
}

export async function fetchAdminAuditLogs(): Promise<{ items: AdminAuditLogItem[] }> {
  return requestAdminApi<{ items: AdminAuditLogItem[] }>('/api/admin/audit-logs', { method: 'GET' });
}

export async function fetchAdminHealth(): Promise<{ items: AdminHealthItem[] }> {
  return requestAdminApi<{ items: AdminHealthItem[] }>('/api/admin/system-health', { method: 'GET' });
}

export async function getAdminSession(): Promise<{ hasToken: boolean }> {
  return { hasToken: Boolean(getAdminAccessToken()) };
}
