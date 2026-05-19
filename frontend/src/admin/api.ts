import type {
  AdminAlias,
  AdminAuditLogItem,
  AdminAuthPayload,
  AdminDomainDnsCheck,
  AdminDomain,
  AdminSystemHealthSnapshot,
  AdminTlsSnapshot,
  AdminMailboxUser,
  AdminOverviewStats,
  AdminQueueSnapshot,
  AdminQuotaItem,
  AdminRspamdSnapshot,
  AliasFormInput,
  AliasUpdateInput,
  DomainFormInput,
  ListQuery,
  PaginatedResult,
  QuotaPolicyFormInput,
  UserFormInput,
  UserUpdateInput,
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

type PaginatedPayload<T> = {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
  items: T[];
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
  if (!response.ok || !payload.success || payload.data == null) {
    const error = new Error(payload.error?.message || '请求失败，请稍后重试') as Error & { code?: string };
    error.code = payload.error?.code;
    throw error;
  }
  return payload.data;
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

function buildQuery(params?: Record<string, string | number | undefined | null>) {
  const search = new URLSearchParams();
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      search.set(key, String(value));
    }
  });
  const text = search.toString();
  return text ? `?${text}` : '';
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

function normalizePaginated<T>(payload: PaginatedPayload<T>): PaginatedResult<T> {
  return {
    page: payload.page,
    page_size: payload.page_size,
    total: payload.total,
    total_pages: payload.total_pages,
    items: payload.items,
  };
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

export async function fetchAdminDomains(query: ListQuery = {}): Promise<PaginatedResult<AdminDomain>> {
  const data = await requestAdminApi<PaginatedPayload<AdminDomain>>(`/api/admin/domains${buildQuery(query)}`, { method: 'GET' });
  return normalizePaginated(data);
}

export async function fetchAdminDomain(domainId: string) {
  return requestAdminApi<{ domain: AdminDomain }>(`/api/admin/domains/${encodeURIComponent(domainId)}`, { method: 'GET' });
}

export async function fetchAdminDomainDnsCheck(domainId: string) {
  return requestAdminApi<AdminDomainDnsCheck>(`/api/admin/domains/${encodeURIComponent(domainId)}/dns-check`, { method: 'GET' });
}

export async function createAdminDomain(payload: DomainFormInput) {
  return requestAdminApi<{ domain: AdminDomain }>('/api/admin/domains', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function updateAdminDomain(domainId: string, payload: Partial<DomainFormInput>) {
  return requestAdminApi<{ domain: AdminDomain }>(`/api/admin/domains/${encodeURIComponent(domainId)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function deleteAdminDomain(domainId: string) {
  return requestAdminApi<{ deleted: boolean; impact: { user_count: number; alias_count: number } }>(`/api/admin/domains/${encodeURIComponent(domainId)}`, {
    method: 'DELETE',
  });
}

export async function bulkAdminDomainStatus(payload: { ids: string[]; status: 'active' | 'disabled' }) {
  return requestAdminApi<{ updated: number }>('/api/admin/domains/bulk-status', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function fetchAdminUsers(query: ListQuery = {}): Promise<PaginatedResult<AdminMailboxUser>> {
  const data = await requestAdminApi<PaginatedPayload<AdminMailboxUser>>(`/api/admin/users${buildQuery(query)}`, { method: 'GET' });
  return normalizePaginated(data);
}

export async function createAdminUser(payload: UserFormInput) {
  return requestAdminApi<{ user: AdminMailboxUser }>('/api/admin/users', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function updateAdminUser(userId: string, payload: UserUpdateInput) {
  return requestAdminApi<{ user: AdminMailboxUser }>(`/api/admin/users/${encodeURIComponent(userId)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function deleteAdminUser(userId: string) {
  return requestAdminApi<{ deleted: boolean }>(`/api/admin/users/${encodeURIComponent(userId)}`, {
    method: 'DELETE',
  });
}

export async function resetAdminUserPassword(userId: string, password: string) {
  return requestAdminApi<{ password_reset: boolean }>(`/api/admin/users/${encodeURIComponent(userId)}/reset-password`, {
    method: 'POST',
    body: JSON.stringify({ password }),
  });
}

export async function bulkAdminUsers(payload: { ids: string[]; action: 'activate' | 'disable' | 'delete' }) {
  return requestAdminApi<{ updated: number }>('/api/admin/users/bulk-action', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function updateAdminUserQuota(userId: string, quota_mb: number) {
  return requestAdminApi<{ user: AdminMailboxUser }>(`/api/admin/users/${encodeURIComponent(userId)}/quota`, {
    method: 'PATCH',
    body: JSON.stringify({ quota_mb }),
  });
}

export async function recalcAdminUserQuota(userId: string) {
  return requestAdminApi<{ result: { status: string; detail: string }; user: AdminMailboxUser }>(`/api/admin/users/${encodeURIComponent(userId)}/quota/recalc`, {
    method: 'POST',
  });
}

export async function fetchAdminAliases(query: ListQuery = {}): Promise<PaginatedResult<AdminAlias>> {
  const data = await requestAdminApi<PaginatedPayload<AdminAlias>>(`/api/admin/aliases${buildQuery(query)}`, { method: 'GET' });
  return normalizePaginated(data);
}

export async function createAdminAlias(payload: AliasFormInput) {
  return requestAdminApi<{ alias: AdminAlias }>('/api/admin/aliases', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function updateAdminAlias(aliasId: string, payload: AliasUpdateInput) {
  return requestAdminApi<{ alias: AdminAlias }>(`/api/admin/aliases/${encodeURIComponent(aliasId)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function deleteAdminAlias(aliasId: string) {
  return requestAdminApi<{ deleted: boolean }>(`/api/admin/aliases/${encodeURIComponent(aliasId)}`, {
    method: 'DELETE',
  });
}

export async function toggleAdminAlias(aliasId: string) {
  return requestAdminApi<{ alias: AdminAlias }>(`/api/admin/aliases/${encodeURIComponent(aliasId)}/toggle`, {
    method: 'POST',
  });
}

export async function fetchAdminQuotas(query: ListQuery = {}): Promise<{ items: AdminQuotaItem[]; user_items: AdminMailboxUser[] }> {
  return requestAdminApi<{ items: AdminQuotaItem[]; user_items: AdminMailboxUser[] }>(`/api/admin/quotas${buildQuery(query)}`, { method: 'GET' });
}

export async function updateQuotaPolicy(payload: QuotaPolicyFormInput) {
  return requestAdminApi<{ policy: AdminQuotaItem }>('/api/admin/quotas/policy', {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function bulkUpdateQuotas(payload: { ids: string[]; quota_mb: number }) {
  return requestAdminApi<{ updated: number }>('/api/admin/quotas/bulk-update', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function fetchAdminAuditLogs(): Promise<{ items: AdminAuditLogItem[] }> {
  return requestAdminApi<{ items: AdminAuditLogItem[] }>('/api/admin/audit-logs', { method: 'GET' });
}

export async function fetchAdminHealth(): Promise<AdminSystemHealthSnapshot> {
  return requestAdminApi<AdminSystemHealthSnapshot>('/api/admin/system-health', { method: 'GET' });
}

export async function fetchAdminQueue(): Promise<AdminQueueSnapshot> {
  return requestAdminApi<AdminQueueSnapshot>('/api/admin/queue', { method: 'GET' });
}

export async function fetchAdminRspamd(): Promise<AdminRspamdSnapshot> {
  return requestAdminApi<AdminRspamdSnapshot>('/api/admin/rspamd', { method: 'GET' });
}

export async function updateAdminRspamdThresholds(payload: { reject: number; add_header: number; greylist: number }) {
  return requestAdminApi<{ status: string; detail: string; thresholds: { reject: number; add_header: number; greylist: number } }>('/api/admin/rspamd/thresholds', {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function rotateAdminDomainDkim(domainId: string, payload?: { selector?: string | null }) {
  return requestAdminApi<{ domain: string; status: string; detail: string; selector?: string; path?: string; public_key?: string | null }>(`/api/admin/domains/${encodeURIComponent(domainId)}/dkim/rotate`, {
    method: 'POST',
    body: JSON.stringify(payload ?? {}),
  });
}

export async function fetchAdminTls(): Promise<AdminTlsSnapshot> {
  return requestAdminApi<AdminTlsSnapshot>('/api/admin/tls', { method: 'GET' });
}

export async function renewAdminTls() {
  return requestAdminApi<{ status: string; detail: string }>('/api/admin/tls/renew', {
    method: 'POST',
    body: JSON.stringify({ confirm: true }),
  });
}

export async function flushAdminQueue() {
  return requestAdminApi<{ status: string; detail: string }>('/api/admin/queue/flush', {
    method: 'POST',
  });
}

export async function deleteAdminQueueItem(queueId: string) {
  return requestAdminApi<{ queue_id: string; status: string; detail: string }>('/api/admin/queue/delete', {
    method: 'POST',
    body: JSON.stringify({ queue_id: queueId }),
  });
}

export async function getAdminSession(): Promise<{ hasToken: boolean }> {
  return { hasToken: Boolean(getAdminAccessToken()) };
}
