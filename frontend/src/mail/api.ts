import type {
  ApiResponse,
  MessageAttachment,
  AuthCredentials,
  AuthPayload,
  AttachmentPreviewStatusPayload,
  ContactListQuery,
  ContactListPayload,
  ContactPayload,
  ContactUpsertPayload,
  FolderListPayload,
  FolderOperationResult,
  MessageDetailPayload,
  MessageListPayload,
  MessageOperationAction,
  MessageOperationPayload,
  MessageOperationResult,
  MessageSearchOptions,
  ChangePasswordPayload,
  ChangePasswordResult,
  MailSignature,
  NotificationSubscriptionStatusPayload,
  NotificationStatusPayload,
  SignatureDefaultPayload,
  SignatureListPayload,
  SignatureUpdatePayload,
  SignatureUpsertPayload,
  PushSubscriptionPayload,
  PushSubscriptionRecord,
  SettingsPayload,
  UserSettingsPreferences,
} from './types';
import { translateText } from '../i18n/runtime';

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS', 'TRACE']);
const CSRF_COOKIE_NAME = 'webmail_csrf';
const messageDetailCache = new Map<string, MessageDetailPayload>();
const messageDetailPending = new Map<string, Promise<MessageDetailPayload>>();

function messageDetailKey(folder: string, uid: string) {
  return `${folder}::${uid}`;
}

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
    const message = payload.error?.message || translateText('请求失败，请稍后重试');
    const error = new Error(message) as Error & { code?: string };
    error.code = payload.error?.code;
    throw error;
  }
  return payload.data as T;
}

export async function fetchFolders(): Promise<FolderListPayload> {
  return requestApi<FolderListPayload>('/api/folders', { method: 'GET' });
}

export async function createFolder(name: string): Promise<FolderOperationResult> {
  return requestApi<FolderOperationResult>('/api/folders', {
    method: 'POST',
    body: JSON.stringify({ name }),
  });
}

export async function renameFolder(name: string, newName: string): Promise<FolderOperationResult> {
  return requestApi<FolderOperationResult>(`/api/folders/${encodeURIComponent(name)}`, {
    method: 'PATCH',
    body: JSON.stringify({ name, new_name: newName }),
  });
}

export async function deleteFolder(name: string): Promise<FolderOperationResult> {
  return requestApi<FolderOperationResult>(`/api/folders/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  });
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
  options: MessageSearchOptions = {},
): Promise<MessageListPayload> {
  const params = new URLSearchParams({
    q: query,
    page: String(options.page ?? 1),
    page_size: String(options.pageSize ?? 30),
  });
  if (options.sender?.trim()) {
    params.set('sender', options.sender.trim());
  }
  if (options.dateFrom) {
    params.set('date_from', options.dateFrom);
  }
  if (options.dateTo) {
    params.set('date_to', options.dateTo);
  }
  if (typeof options.hasAttachments === 'boolean') {
    params.set('has_attachments', String(options.hasAttachments));
  }
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

export async function fetchMessageDetail(
  folder: string,
  uid: string,
  options: { force?: boolean } = {},
): Promise<MessageDetailPayload> {
  const key = messageDetailKey(folder, uid);
  if (!options.force) {
    const cached = messageDetailCache.get(key);
    if (cached) {
      return cached;
    }
    const pending = messageDetailPending.get(key);
    if (pending) {
      return pending;
    }
  }
  const request = requestApi<MessageDetailPayload>(
    `/api/folders/${encodeURIComponent(folder)}/messages/${encodeURIComponent(uid)}`,
    { method: 'GET' },
  ).then((payload) => {
    messageDetailCache.set(key, payload);
    messageDetailPending.delete(key);
    return payload;
  }).catch((error) => {
    messageDetailPending.delete(key);
    throw error;
  });
  messageDetailPending.set(key, request);
  return request;
}

export async function fetchAttachmentPreviewStatus(
  folder: string,
  uid: string,
  attachmentId: string,
): Promise<AttachmentPreviewStatusPayload> {
  return requestApi<AttachmentPreviewStatusPayload>(
    `/api/folders/${encodeURIComponent(folder)}/messages/${encodeURIComponent(uid)}/attachments/${encodeURIComponent(attachmentId)}/preview/status`,
    { method: 'GET' },
  );
}

export function primeMessageDetailCache(folder: string, uid: string): Promise<MessageDetailPayload> {
  return fetchMessageDetail(folder, uid);
}

function canPreviewAttachmentForPrefetch(attachment: MessageAttachment) {
  const lowerType = String(attachment.content_type || '').toLowerCase();
  const lowerName = String(attachment.filename || '').toLowerCase();
  return (
    lowerType.startsWith('image/') ||
    lowerType === 'application/pdf' ||
    lowerType.startsWith('text/') ||
    lowerType === 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' ||
    /\.(png|jpe?g|gif|webp|bmp|svg|pdf|txt|md|json|docx)$/i.test(lowerName)
  );
}

function resolveAttachmentId(attachment: MessageAttachment) {
  return String(attachment.attachment_id || attachment.id || '');
}

function buildAttachmentPreviewThumbnailUrl(folder: string, uid: string, attachmentId: string) {
  return `/api/folders/${encodeURIComponent(folder)}/messages/${encodeURIComponent(uid)}/attachments/${encodeURIComponent(attachmentId)}/preview-thumbnail`;
}

export async function primeMessageAttachmentPreviewCache(folder: string, uid: string): Promise<void> {
  const detail = await fetchMessageDetail(folder, uid);
  const attachments = Array.isArray(detail.attachments) ? detail.attachments : [];
  const previewableAttachments = attachments
    .filter((attachment) => canPreviewAttachmentForPrefetch(attachment))
    .slice(0, 3);
  await Promise.all(previewableAttachments.map(async (attachment) => {
    const attachmentId = resolveAttachmentId(attachment);
    if (!attachmentId) {
      return;
    }
    const status = await fetchAttachmentPreviewStatus(folder, uid, attachmentId);
    if (status.thumbnail_ready) {
      const image = new window.Image();
      image.decoding = 'async';
      image.src = buildAttachmentPreviewThumbnailUrl(folder, uid, attachmentId);
    }
  }));
}

export function clearMessageDetailCache(folder?: string, uid?: string): void {
  if (folder && uid) {
    const key = messageDetailKey(folder, uid);
    messageDetailCache.delete(key);
    messageDetailPending.delete(key);
    return;
  }
  if (folder) {
    for (const key of [...messageDetailCache.keys()]) {
      if (key.startsWith(`${folder}::`)) {
        messageDetailCache.delete(key);
      }
    }
    for (const key of [...messageDetailPending.keys()]) {
      if (key.startsWith(`${folder}::`)) {
        messageDetailPending.delete(key);
      }
    }
    return;
  }
  messageDetailCache.clear();
  messageDetailPending.clear();
}

export async function fetchContacts(queryOrOptions: string | ContactListQuery = '', limit = 10): Promise<ContactListPayload> {
  const options = typeof queryOrOptions === 'string'
    ? { query: queryOrOptions, limit }
    : queryOrOptions;
  const params = new URLSearchParams();
  const query = options.query ?? '';
  params.set('query', query);
  if (typeof options.page === 'number') {
    params.set('page', String(options.page));
  }
  if (typeof options.pageSize === 'number') {
    params.set('page_size', String(options.pageSize));
  } else if (typeof options.limit === 'number') {
    params.set('limit', String(options.limit));
  }
  if (options.group) {
    params.set('group', options.group);
  }
  if (options.tag) {
    params.set('tag', options.tag);
  }
  return requestApi<ContactListPayload>(`/api/contacts?${params.toString()}`, { method: 'GET' });
}

export async function createContact(payload: ContactUpsertPayload): Promise<ContactPayload> {
  return requestApi<ContactPayload>('/api/contacts', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function updateContact(contactId: string, payload: Partial<ContactUpsertPayload>): Promise<ContactPayload> {
  return requestApi<ContactPayload>(`/api/contacts/${encodeURIComponent(contactId)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function deleteContact(contactId: string): Promise<{ deleted: boolean }> {
  return requestApi<{ deleted: boolean }>(`/api/contacts/${encodeURIComponent(contactId)}`, {
    method: 'DELETE',
  });
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

export async function uploadSettingsAvatar(file: File): Promise<SettingsPayload> {
  const formData = new FormData();
  formData.append('file', file);
  const csrfToken = readCookie(CSRF_COOKIE_NAME);
  const headers: Record<string, string> = {};
  if (csrfToken) {
    headers['X-CSRF-Token'] = csrfToken;
  }
  const response = await fetch('/api/settings/avatar', {
    method: 'POST',
    credentials: 'include',
    headers,
    body: formData,
  });
  const payload = (await response.json()) as ApiResponse<SettingsPayload>;
  if (!response.ok || !payload.success) {
    const message = payload.error?.message || translateText('请求失败，请稍后重试');
    const error = new Error(message) as Error & { code?: string };
    error.code = payload.error?.code;
    throw error;
  }
  return payload.data as SettingsPayload;
}

export function formatDateByTimezone(
  value: string | null | undefined,
  options?: { locale?: string; timezone?: string; dateStyle?: Intl.DateTimeFormatOptions['dateStyle']; timeStyle?: Intl.DateTimeFormatOptions['timeStyle'] },
): string {
  if (!value) {
    return translateText('未提供');
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  try {
    return new Intl.DateTimeFormat(options?.locale ?? 'zh-CN', {
      dateStyle: options?.dateStyle ?? 'medium',
      timeStyle: options?.timeStyle ?? 'short',
      timeZone: options?.timezone,
    }).format(date);
  } catch {
    return new Intl.DateTimeFormat(options?.locale ?? 'zh-CN', {
      dateStyle: options?.dateStyle ?? 'medium',
      timeStyle: options?.timeStyle ?? 'short',
    }).format(date);
  }
}

export async function changePassword(payload: ChangePasswordPayload): Promise<ChangePasswordResult> {
  return requestApi<ChangePasswordResult>('/api/settings/password', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function fetchPushSubscriptionStatus(): Promise<NotificationSubscriptionStatusPayload> {
  return requestApi<NotificationSubscriptionStatusPayload>('/api/notifications/push-subscription', {
    method: 'GET',
  });
}

export async function savePushSubscriptionRecord(
  payload: PushSubscriptionPayload,
): Promise<{ subscription: PushSubscriptionRecord | null }> {
  return requestApi<{ subscription: PushSubscriptionRecord | null }>('/api/notifications/push-subscription', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function deletePushSubscriptionRecord(): Promise<{ deleted: boolean }> {
  return requestApi<{ deleted: boolean }>('/api/notifications/push-subscription', {
    method: 'DELETE',
  });
}

export async function fetchNotificationStatus(): Promise<NotificationStatusPayload> {
  return requestApi<NotificationStatusPayload>('/api/notifications/status', {
    method: 'GET',
  });
}

export async function saveNotificationPreferences(payload: {
  enabled: boolean;
  permission_state?: string;
}): Promise<NotificationStatusPayload> {
  return requestApi<NotificationStatusPayload>('/api/notifications/preferences', {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

export async function fetchSignatures(): Promise<SignatureListPayload> {
  const payload = await requestApi<SignatureListPayload>('/api/signatures', { method: 'GET' });
  return {
    signatures: payload.signatures.map(normalizeSignature),
  };
}

export async function fetchDefaultSignature(): Promise<SignatureDefaultPayload> {
  const payload = await requestApi<SignatureDefaultPayload>('/api/signatures/default', { method: 'GET' });
  return {
    signature: payload.signature ? normalizeSignature(payload.signature) : null,
  };
}

export async function createSignature(payload: SignatureUpsertPayload): Promise<{ signature: MailSignature }> {
  const result = await requestApi<{ signature: MailSignature }>('/api/signatures', {
    method: 'POST',
    body: JSON.stringify(signatureRequestBody(payload)),
  });
  return { signature: normalizeSignature(result.signature) };
}

export async function updateSignature(id: string, payload: SignatureUpdatePayload): Promise<{ signature: MailSignature }> {
  const result = await requestApi<{ signature: MailSignature }>(`/api/signatures/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(signatureRequestBody(payload)),
  });
  return { signature: normalizeSignature(result.signature) };
}

export async function deleteSignature(id: string): Promise<{ deleted: boolean }> {
  return requestApi<{ deleted: boolean }>(`/api/signatures/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
}

export async function setDefaultSignature(id: string): Promise<SignatureDefaultPayload> {
  const payload = await requestApi<SignatureDefaultPayload>(`/api/signatures/${encodeURIComponent(id)}/default`, {
    method: 'POST',
  });
  return {
    signature: payload.signature ? normalizeSignature(payload.signature) : null,
  };
}

function normalizeSignature(signature: MailSignature): MailSignature {
  return {
    ...signature,
    html_body: signature.html_body ?? signature.content ?? '',
  };
}

function signatureRequestBody(payload: SignatureUpdatePayload) {
  const body: Record<string, unknown> = {};
  if (payload.name !== undefined) {
    body.name = payload.name;
  }
  if (payload.html_body !== undefined) {
    body.content = payload.html_body;
  }
  if (payload.is_default !== undefined) {
    body.is_default = payload.is_default;
  }
  return body;
}
