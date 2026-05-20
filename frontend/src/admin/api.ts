import type {
  AdminAlias,
  AdminActionHistoryItem,
  AdminAuditLogItem,
  AdminAuthPayload,
  AdminDomainDnsCheck,
  AdminDomain,
  AdminDashboardTrendsSnapshot,
  AdminLogSnapshotPage,
  AdminMailSystemCommandResult,
  AdminMailSystemConfigPreview,
  AdminSystemHealthSnapshot,
  AdminSystemConfigPayload,
  AdminSystemConfigSnapshot,
  AdminTlsSnapshot,
  AdminMailboxUser,
  AdminOverviewStats,
  AdminQueueSnapshot,
  AdminQuotaItem,
  AdminRspamdSnapshot,
  AdminCatchAllAliasResult,
  AliasFormInput,
  AliasUpdateInput,
  DomainFormInput,
  ListQuery,
  PaginatedResult,
  AdminUserImportResult,
  AdminUserResetPasswordResult,
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

export async function fetchAdminDashboardTrends(period: '24h' | '7d' | '30d' = '7d'): Promise<AdminDashboardTrendsSnapshot> {
  return requestAdminApi<AdminDashboardTrendsSnapshot>(`/api/admin/dashboard/trends${buildQuery({ period })}`, { method: 'GET' });
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
  return requestAdminApi<AdminUserResetPasswordResult>(`/api/admin/users/${encodeURIComponent(userId)}/reset-password`, {
    method: 'POST',
    body: JSON.stringify({ password }),
  });
}

export async function resetAdminUserPasswordRandomly(userId: string) {
  return requestAdminApi<AdminUserResetPasswordResult>(`/api/admin/users/${encodeURIComponent(userId)}/reset-password`, {
    method: 'POST',
    body: JSON.stringify({ generate_random: true }),
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

export async function importAdminUsersCsv(payload: { csv_content: string; domain_id?: string | null }) {
  return requestAdminApi<AdminUserImportResult>('/api/admin/users/import-csv', {
    method: 'POST',
    body: JSON.stringify(payload),
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

export async function createAdminCatchAllAlias(payload: { domain_id: string; target_address: string }) {
  return requestAdminApi<AdminCatchAllAliasResult>('/api/admin/aliases/catch-all', {
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

export async function fetchAdminActionHistory(query: ListQuery & {
  action_type?: string;
  target_type?: string;
} = {}): Promise<PaginatedResult<AdminActionHistoryItem>> {
  const data = await requestAdminApi<PaginatedPayload<AdminActionHistoryItem>>(`/api/admin/action-history${buildQuery(query)}`, { method: 'GET' });
  return normalizePaginated(data);
}

export async function fetchAdminAuditLogsPage(query: ListQuery & {
  event_type?: string;
  actor_id?: string;
  success_only?: boolean;
  action?: string;
  target?: string;
  date_from?: string;
  date_to?: string;
} = {}): Promise<PaginatedResult<AdminAuditLogItem>> {
  const data = await requestAdminApi<PaginatedPayload<AdminAuditLogItem>>(
    `/api/admin/audit-logs${buildQuery({
      ...query,
      success_only: query.success_only === undefined ? undefined : Number(query.success_only),
    })}`,
    { method: 'GET' },
  );
  return normalizePaginated(data);
}

export async function exportAdminAuditLogs(payload: {
  q?: string;
  status?: string;
  format?: 'csv' | 'json';
}) {
  return requestAdminApi<{ format: string; content: string; media_type: string; filename: string }>('/api/admin/audit-logs/export', {
    method: 'POST',
    body: JSON.stringify({ format: 'csv', ...payload }),
  });
}

export async function fetchAdminLogs(query: ListQuery & {
  sender?: string;
  recipient?: string;
} = {}): Promise<AdminLogSnapshotPage> {
  return requestAdminApi<AdminLogSnapshotPage>(`/api/admin/logs${buildQuery(query)}`, { method: 'GET' });
}

export async function exportAdminLogs(payload: {
  log_key?: string;
  q?: string;
  status?: string;
  sender?: string;
  recipient?: string;
  format?: 'csv' | 'json';
}) {
  return requestAdminApi<{ format: string; content: string; media_type: string; filename: string }>('/api/admin/logs/export', {
    method: 'POST',
    body: JSON.stringify({ format: 'csv', ...payload }),
  });
}

export async function fetchAdminHealth(): Promise<AdminSystemHealthSnapshot> {
  return requestAdminApi<AdminSystemHealthSnapshot>('/api/admin/system-health', { method: 'GET' });
}

export async function fetchAdminSystemConfig(): Promise<AdminSystemConfigSnapshot> {
  return requestAdminApi<AdminSystemConfigSnapshot>('/api/admin/system-config', { method: 'GET' });
}

export async function updateAdminSystemConfig(payload: AdminSystemConfigPayload) {
  return requestAdminApi<{ config: AdminSystemConfigSnapshot; detail: string }>('/api/admin/system-config', {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function fetchAdminMailSystemConfigPreview(): Promise<AdminMailSystemConfigPreview> {
  return requestAdminApi<AdminMailSystemConfigPreview>('/api/admin/mail-system/configs', { method: 'GET' });
}

export async function backupAdminMailSystemConfig(targetPath: string): Promise<AdminMailSystemCommandResult> {
  return requestAdminApi<AdminMailSystemCommandResult>(`/api/admin/mail-system/backup${buildQuery({ target_path: targetPath })}`, {
    method: 'POST',
  });
}

export async function restoreAdminMailSystemConfig(payload: { backup_path: string; target_path: string }): Promise<AdminMailSystemCommandResult> {
  return requestAdminApi<AdminMailSystemCommandResult>('/api/admin/mail-system/restore', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function postmapAdminMailSystemConfig(): Promise<AdminMailSystemCommandResult> {
  return requestAdminApi<AdminMailSystemCommandResult>('/api/admin/mail-system/postmap', {
    method: 'POST',
  });
}

export async function postaliasAdminMailSystemConfig(): Promise<AdminMailSystemCommandResult> {
  return requestAdminApi<AdminMailSystemCommandResult>('/api/admin/mail-system/postalias', {
    method: 'POST',
  });
}

export async function reloadAdminPostfixService(): Promise<AdminMailSystemCommandResult> {
  return requestAdminApi<AdminMailSystemCommandResult>('/api/admin/mail-system/postfix/reload', {
    method: 'POST',
  });
}

export async function reloadAdminDovecotService(): Promise<AdminMailSystemCommandResult> {
  return requestAdminApi<AdminMailSystemCommandResult>('/api/admin/mail-system/dovecot/reload', {
    method: 'POST',
  });
}

export async function runAdminServiceAction(payload: { service: string; action: 'start' | 'stop' | 'restart' }): Promise<AdminMailSystemCommandResult> {
  return requestAdminApi<AdminMailSystemCommandResult>('/api/admin/system/service-action', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function fetchAdminQueue(query: ListQuery = {}): Promise<AdminQueueSnapshot> {
  return requestAdminApi<AdminQueueSnapshot>(`/api/admin/queue${buildQuery(query)}`, { method: 'GET' });
}

export async function fetchAdminQueueItem(queueId: string) {
  return requestAdminApi<{ status: string; detail: string; queue_id: string; content?: string; command_result?: { command?: string[]; stdout?: string; stderr?: string; exit_code?: number; duration_ms?: number; ok?: boolean } }>(`/api/admin/queue/${encodeURIComponent(queueId)}`, { method: 'GET' });
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

export async function requeueAdminQueueItem(queueId: string) {
  return requestAdminApi<{ status: string; detail: string }>('/api/admin/queue/requeue', {
    method: 'POST',
    body: JSON.stringify({ queue_id: queueId }),
  });
}

export async function bulkDeleteAdminQueueItems(queueIds: string[]) {
  return requestAdminApi<{ status: string; detail: string; deleted_count: number; deleted_ids: string[]; errors: { queue_id: string; detail: string }[] }>('/api/admin/queue/bulk-delete', {
    method: 'POST',
    body: JSON.stringify({ queue_ids: queueIds }),
  });
}

export async function clearAdminQueueByStatuses(statuses: string[]) {
  return requestAdminApi<{ status: string; detail: string; deleted_count: number; deleted_ids: string[]; errors: { queue_id: string; detail: string }[] }>('/api/admin/queue/clear', {
    method: 'POST',
    body: JSON.stringify({ statuses }),
  });
}

export async function getAdminSession(): Promise<{ hasToken: boolean }> {
  return { hasToken: Boolean(getAdminAccessToken()) };
}
