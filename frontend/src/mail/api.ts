import type { ApiResponse, FolderListPayload, MessageListPayload } from './types';

async function requestApi<T>(input: string, init?: RequestInit): Promise<T> {
  const response = await fetch(input, {
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
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
