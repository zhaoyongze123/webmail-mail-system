import type { ReactNode } from 'react';

export type ApiError = {
  code: string;
  message: string;
  details?: Record<string, unknown>;
};

export type ApiResponse<T> = {
  success: boolean;
  data: T | null;
  error: ApiError | null;
};

export type MailFolderType = 'inbox' | 'sent' | 'drafts' | 'spam' | 'trash' | 'archive' | string;

export type MailFolder = {
  name: string;
  canonical_name?: string;
  display_name: string;
  type: MailFolderType;
  delimiter?: string;
  unread_count: number;
  total_count: number;
  uid_validity?: number | string | null;
};

export type MailAddress = {
  name: string;
  email: string;
};

export type MailMessageSummary = {
  uid: string;
  message_id?: string | null;
  subject: string;
  sender: MailAddress;
  to?: MailAddress[];
  date: string | null;
  read: boolean;
  has_attachments: boolean;
  snippet: string;
};

export type FolderListPayload = {
  folders: MailFolder[];
};

export type MessageListPayload = {
  folder: string;
  page: number;
  page_size: number;
  total: number;
  messages: MailMessageSummary[];
  cached?: boolean;
};

export type ReaderRenderContext = {
  folder: string;
  uid: string;
  message: MailMessageSummary | null;
};

export type MailWorkspaceProps = {
  onOpenMessage: (uid: string, folder: string) => void;
  selectedMessageKey?: string | null;
  renderReader?: (context: ReaderRenderContext | null) => ReactNode;
  onCompose?: () => void;
};
