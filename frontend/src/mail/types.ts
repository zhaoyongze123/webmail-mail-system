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

export type MessageAttachment = {
  id?: string;
  attachment_id?: string;
  filename: string;
  content_type?: string | null;
  size?: number | null;
  size_bytes?: number | null;
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
  attachment_types?: string[];
  snippet: string;
};

export type FolderListPayload = {
  folders: MailFolder[];
};

export type FolderOperationResult = {
  folder: string;
  new_name?: string | null;
  deleted?: boolean | null;
};

export type MessageListPayload = {
  folder: string;
  page: number;
  page_size: number;
  total: number;
  messages: MailMessageSummary[];
  cached?: boolean;
  query?: string;
  sender?: string | null;
  date_from?: string | null;
  date_to?: string | null;
  has_attachments?: boolean | null;
};

export type MessageSearchOptions = {
  refresh?: boolean;
  page?: number;
  pageSize?: number;
  sender?: string;
  dateFrom?: string;
  dateTo?: string;
  hasAttachments?: boolean;
};

export type MessageOperationAction = 'mark_read' | 'mark_unread' | 'delete' | 'move';

export type MessageOperationPayload = {
  action: MessageOperationAction;
  uids: string[];
  target_folder?: string | null;
};

export type MessageOperationResult = {
  action: MessageOperationAction;
  folder: string;
  target_folder?: string | null;
  uids: string[];
};

export type SystemSettingsPreferences = {
  page_size: number;
  mark_read_on_open: boolean;
  language: string;
  timezone: string;
  reply_quote_position: 'top' | 'bottom';
};

export type UserProfileSettings = {
  display_name: string;
  profile_title: string;
  avatar_url: string;
  bio: string;
};

export type ThemeSettings = {
  mode: 'light' | 'dark';
};

export type UserSettingsPreferences = {
  system: SystemSettingsPreferences;
  user: UserProfileSettings;
  theme: ThemeSettings;
};

export type SettingsPayload = {
  account: {
    email: string;
  };
  preferences: UserSettingsPreferences;
};

export type ChangePasswordPayload = {
  current_password: string;
  new_password: string;
};

export type ChangePasswordResult = {
  password_updated: boolean;
};

export type MailSignature = {
  id: string;
  name: string;
  content?: string;
  html_body: string;
  text_body?: string | null;
  is_default?: boolean;
  created_at?: string | null;
  updated_at?: string | null;
};

export type SignatureListPayload = {
  signatures: MailSignature[];
};

export type SignatureDefaultPayload = {
  signature: MailSignature | null;
};

export type SignatureUpsertPayload = {
  name: string;
  html_body: string;
  text_body?: string | null;
  is_default?: boolean;
};

export type SignatureUpdatePayload = Partial<SignatureUpsertPayload>;

export type AuthPayload = {
  email: string;
};

export type AuthCredentials = {
  email: string;
  password: string;
  remember?: boolean;
  display_name?: string;
};

export type ContactItem = {
  id?: string | null;
  name?: string;
  email: string;
  phone?: string | null;
  note?: string | null;
  groups?: string[];
  tags?: string[];
  last_used_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  source?: 'recent' | 'manual';
};

export type ContactListPayload = {
  query: string;
  page?: number;
  page_size?: number;
  total?: number;
  group?: string | null;
  tag?: string | null;
  contacts: ContactItem[];
};

export type ContactListQuery = {
  query?: string;
  page?: number;
  pageSize?: number;
  group?: string | null;
  tag?: string | null;
  limit?: number;
};

export type ContactUpsertPayload = {
  name: string;
  email: string;
  phone?: string | null;
  note?: string | null;
  groups?: string[];
  tags?: string[];
};

export type ContactPayload = {
  contact: ContactItem;
};

export type MessageDetailPayload = {
  uid: string;
  subject: string;
  from?: MailAddress[];
  to?: MailAddress[];
  cc?: MailAddress[];
  date?: string | null;
  html_body?: string | null;
  text_body?: string | null;
  attachments?: MessageAttachment[] | null;
  read?: boolean;
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
  onOpenSettings?: () => void;
};
