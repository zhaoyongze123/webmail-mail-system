import type {
  ApiResponse,
  AuthCredentials,
  AuthPayload,
  ContactListPayload,
  FolderListPayload,
  MessageDetailPayload,
  MessageListPayload,
  MessageOperationAction,
  MessageOperationPayload,
  MessageOperationResult,
  SettingsPayload,
  UserSettingsPreferences,
} from './types';

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS', 'TRACE']);
const CSRF_COOKIE_NAME = 'webmail_csrf';

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

async function requestApi<T>(input: string, init?: RequestInit): Promise<T> {
  const method = (init?.method || 'GET').toUpperCase();
  const headers = {
    'Content-Type': 'application/json',
    ...(init?.headers ?? {}),
  } as Record<string, string>;
  if (!SAFE_METHODS.has(method)) {
    const csrfToken = readCookie(CSRF_COOKIE_NAME);
    if (csrfToken) {
      headers['X-CSRF-Token'] = csrfToken;
    }
  }
  const response = await fetch(input, {
    credentials: 'include',
    headers,
    ...init,
  });
  const payload = (await response.json()) as ApiResponse<T>;
  if (!response.ok || !payload.success) {
    const message = payload.error?.message || '请求失败，请稍后重试';
    const error = new Error(message) as Error & { code?: string };
    error.code = payload.error?.code;
    throw error;
  }
  return payload.data as T;
}

export async function fetchFolders(): Promise<FolderListPayload> {
  return requestApi<FolderListPayload>('/api/folders', { method: 'GET' });
}

export async function login(credentials: AuthCredentials): Promise<AuthPayload> {
  return requestApi<AuthPayload>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({
      email: credentials.email,
      password: credentials.password,
      remember: Boolean(credentials.remember),
    }),
  });
}

export async function register(credentials: AuthCredentials): Promise<AuthPayload> {
  return requestApi<AuthPayload>('/api/auth/register', {
    method: 'POST',
    body: JSON.stringify({
      email: credentials.email,
      password: credentials.password,
      remember: Boolean(credentials.remember),
      display_name: credentials.display_name || null,
    }),
  });
}

export async function logout(): Promise<{ logged_out: boolean }> {
  return requestApi<{ logged_out: boolean }>('/api/auth/logout', { method: 'POST' });
}

export async function fetchFolderMessages(
  folder: string,
  options: { page?: number; pageSize?: number; refresh?: boolean } = {},
): Promise<MessageListPayload> {
  const params = new URLSearchParams({
    page: String(options.page ?? 1),
    page_size: String(options.pageSize ?? 30),
  });
  if (options.refresh) {
    params.set('refresh', 'true');
  }
  return requestApi<MessageListPayload>(`/api/folders/${encodeURIComponent(folder)}/messages?${params.toString()}`, {
    method: 'GET',
  });
}

export async function searchFolderMessages(
  folder: string,
  query: string,
  options: { page?: number; pageSize?: number; refresh?: boolean } = {},
): Promise<MessageListPayload> {
  const params = new URLSearchParams({
    q: query,
    page: String(options.page ?? 1),
    page_size: String(options.pageSize ?? 30),
  });
  if (options.refresh) {
    params.set('refresh', 'true');
  }
  return requestApi<MessageListPayload>(`/api/folders/${encodeURIComponent(folder)}/messages/search?${params.toString()}`, {
    method: 'GET',
  });
}

export async function updateMessageOperation(
  folder: string,
  payload: MessageOperationPayload,
): Promise<MessageOperationResult> {
  return requestApi<MessageOperationResult>(`/api/folders/${encodeURIComponent(folder)}/messages/operations`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function fetchMessageDetail(folder: string, uid: string): Promise<MessageDetailPayload> {
  return requestApi<MessageDetailPayload>(`/api/folders/${encodeURIComponent(folder)}/messages/${encodeURIComponent(uid)}`, {
    method: 'GET',
  });
}

export async function fetchContacts(query = '', limit = 10): Promise<ContactListPayload> {
  const params = new URLSearchParams({
    query,
    limit: String(limit),
  });
  return requestApi<ContactListPayload>(`/api/contacts?${params.toString()}`, { method: 'GET' });
}

export async function moveMessages(
  folder: string,
  uids: string[],
  targetFolder: string,
): Promise<MessageOperationResult> {
  return requestApi<MessageOperationResult>(`/api/messages/move?folder=${encodeURIComponent(folder)}`, {
    method: 'POST',
    body: JSON.stringify({
      folder,
      uids,
      target_folder: targetFolder,
    }),
  });
}

export async function deleteMessages(folder: string, uids: string[]): Promise<MessageOperationResult> {
  return requestApi<MessageOperationResult>(`/api/messages/delete?folder=${encodeURIComponent(folder)}`, {
    method: 'POST',
    body: JSON.stringify({
      folder,
      uids,
    }),
  });
}

export async function fetchSettings(): Promise<SettingsPayload> {
  return requestApi<SettingsPayload>('/api/settings', { method: 'GET' });
}

export async function saveSettings(preferences: Partial<UserSettingsPreferences>): Promise<SettingsPayload> {
  return requestApi<SettingsPayload>('/api/settings', {
    method: 'PUT',
    body: JSON.stringify(preferences),
  });
}
