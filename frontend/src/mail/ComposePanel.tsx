import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ChangeEvent, type DragEvent, type FormEvent, type KeyboardEvent } from 'react';
import { clearComposeDraftCache, writeComposeDraftCache } from './composeDraftCache';
import { fetchSignatures } from './api';
import type { MailSignature } from './types';

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

type Contact = {
  email: string;
  last_used_at?: string;
};

type AttachmentItem = {
  attachment_id: string;
  filename: string;
  content_type?: string | null;
  size_bytes: number;
  expires_at?: string | null;
};

type AttachmentState = AttachmentItem & {
  local_id: string;
  progress: number;
  status: 'uploading' | 'uploaded' | 'failed';
  error: string | null;
};

type ChunkUploadResponse = AttachmentItem & {
  complete: boolean;
  uploaded_chunks: number;
  total_chunks: number;
};

type DefaultSignaturePayload = {
  text_body?: string | null;
  html_body?: string | null;
  text?: string | null;
  html?: string | null;
  content?: string | null;
  signature?: string | null;
};

type DefaultSignature = {
  text: string;
  html: string;
};

export type ComposeValues = {
  to?: string[];
  cc?: string[];
  bcc?: string[];
  subject?: string;
  text_body?: string;
  html_body?: string | null;
  attachment_ids?: string[];
  attachments?: AttachmentItem[];
};

export type ComposePanelProps = {
  open: boolean;
  draftId?: string | null;
  initialValues?: ComposeValues | null;
  from?: string;
  onClose: () => void;
  onSent?: (result: SendResult) => void;
  onSessionExpired?: () => void;
};

type SignatureOption = {
  id: string;
  name: string;
  text: string;
  html: string;
  isDefault: boolean;
};

type DraftSaveResult = {
  draft_id: string;
  status: string;
  saved_at?: string;
};

export type SendResult = {
  message_id?: string;
  sent: boolean;
  archived_folder?: string;
};

type ComposeForm = {
  to: string;
  cc: string;
  bcc: string;
  subject: string;
  textBody: string;
  htmlBody: string;
};

type RecipientField = 'to' | 'cc' | 'bcc';

type RecipientFieldState = {
  tags: string[];
  draft: string;
};

type RecipientState = Record<RecipientField, RecipientFieldState>;

type RecipientSelection = {
  field: RecipientField;
  index: number;
} | null;

type SaveState = 'idle' | 'dirty' | 'saving' | 'saved' | 'error';
type SendState = 'idle' | 'sending' | 'sent' | 'error';
type EditorMode = 'rich' | 'plain';
type ColorMenu = 'text' | 'highlight' | null;
type InlineImagePosition = 'left' | 'center' | 'right';
type ActiveStyles = {
  bold: boolean;
  italic: boolean;
  underline: boolean;
  strikeThrough: boolean;
  insertOrderedList: boolean;
  insertUnorderedList: boolean;
};
type ToggleStyleCommand = 'bold' | 'italic' | 'underline' | 'strikeThrough';

const AUTOSAVE_DELAY_MS = 30000;
const CONTACT_MIN_QUERY_LENGTH = 1;
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS', 'TRACE']);
const CSRF_COOKIE_NAME = 'webmail_csrf';
const FONT_FAMILIES = ['宋体', '微软雅黑', 'Arial', 'Helvetica', 'Times New Roman', 'Courier New'];
const FONT_SIZES = [
  { label: '12', value: '12px' },
  { label: '14', value: '14px' },
  { label: '16', value: '16px' },
  { label: '18', value: '18px' },
  { label: '24', value: '24px' },
  { label: '32', value: '32px' },
];
const COLOR_SWATCHES = [
  '#1abc9c',
  '#2ecc71',
  '#3498db',
  '#9b59b6',
  '#536779',
  '#f1c40f',
  '#16a085',
  '#27ae60',
  '#2980b9',
  '#8e44ad',
  '#2c3e50',
  '#f39c12',
  '#e67e22',
  '#e74c3c',
  '#ecf0f1',
  '#95a5a6',
  '#d9d9d9',
  '#ffffff',
  '#d35400',
  '#c0392b',
  '#bdc3c7',
  '#7f8c8d',
  '#999999',
  '#000000',
];
const TABLE_PICKER_ROWS = 8;
const TABLE_PICKER_COLUMNS = 10;
const CHUNK_UPLOAD_SIZE_BYTES = 1024 * 1024;
const INLINE_IMAGE_DEFAULT_WIDTH = 320;
const INLINE_IMAGE_MIN_WIDTH = 120;
const INLINE_IMAGE_MAX_WIDTH = 900;
const INLINE_IMAGE_STEP = 10;
const DEFAULT_ACTIVE_STYLES: ActiveStyles = {
  bold: false,
  italic: false,
  underline: false,
  strikeThrough: false,
  insertOrderedList: false,
  insertUnorderedList: false,
};

function createEmptyForm(values?: ComposeValues | null): ComposeForm {
  const textBody = values?.text_body ?? '';
  return {
    to: (values?.to ?? []).join(', '),
    cc: (values?.cc ?? []).join(', '),
    bcc: (values?.bcc ?? []).join(', '),
    subject: values?.subject ?? '',
    textBody,
    htmlBody: values?.html_body ?? textBody,
  };
}

function createRecipientState(values?: ComposeValues | null): RecipientState {
  return {
    to: { tags: (values?.to ?? []).map((item) => item.trim()).filter(Boolean), draft: '' },
    cc: { tags: (values?.cc ?? []).map((item) => item.trim()).filter(Boolean), draft: '' },
    bcc: { tags: (values?.bcc ?? []).map((item) => item.trim()).filter(Boolean), draft: '' },
  };
}

function normalizeAttachments(values?: ComposeValues | null): AttachmentState[] {
  return (values?.attachments ?? []).map((attachment) => ({
    ...attachment,
    local_id: attachment.attachment_id,
    progress: 100,
    status: 'uploaded',
    error: null,
  }));
}

function createUploadId() {
  if (window.crypto && typeof window.crypto.randomUUID === 'function') {
    return window.crypto.randomUUID();
  }
  return `attachment-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function parseAddresses(value: string) {
  return value
    .split(/[,;\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function serializeRecipientField(tags: string[], draft: string) {
  return [...tags, draft.trim()].filter(Boolean).join(', ');
}

function recipientKey(value: string) {
  return value.trim().toLowerCase();
}

function getRecipientKeys(state: RecipientState, exclude?: { field: RecipientField; index?: number }) {
  const keys = new Set<string>();
  (Object.entries(state) as [RecipientField, RecipientFieldState][]).forEach(([field, item]) => {
    item.tags.forEach((tag, index) => {
      if (exclude && exclude.field === field && exclude.index === index) {
        return;
      }
      keys.add(recipientKey(tag));
    });
    if (!exclude || exclude.field !== field) {
      const draft = item.draft.trim();
      if (draft) {
        keys.add(recipientKey(draft));
      }
    }
  });
  return keys;
}

function validateRecipientDuplicates(form: ComposeForm) {
  const recipients = [...parseAddresses(form.to), ...parseAddresses(form.cc), ...parseAddresses(form.bcc)];
  const seen = new Set<string>();
  for (const recipient of recipients) {
    const key = recipientKey(recipient);
    if (seen.has(key)) {
      return '收件人不能重复';
    }
    seen.add(key);
  }
  return null;
}

function formatFileSize(size: number) {
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function escapeHtml(value: string) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function plainTextToHtml(value: string) {
  const normalized = value.replace(/\r\n/g, '\n');
  return escapeHtml(normalized).replace(/\n/g, '<br>');
}

function htmlToPlainText(value: string) {
  const container = document.createElement('div');
  container.innerHTML = value;
  return (container.innerText || container.textContent || '').replace(/\u200b/g, '');
}

function normalizeLineBreaks(value: string) {
  return value.replace(/\r\n/g, '\n');
}

function trimComposeWhitespace(value: string) {
  return normalizeLineBreaks(value).replace(/^[\s\u200b]+|[\s\u200b]+$/g, '');
}

function normalizeSignaturePayload(data: DefaultSignaturePayload | string | null | undefined): DefaultSignature | null {
  if (typeof data === 'string') {
    const text = trimComposeWhitespace(data);
    if (!text) {
      return null;
    }
    return { text, html: plainTextToHtml(text) };
  }
  if (!data) {
    return null;
  }
  const textSource = data.text_body ?? data.text ?? data.content ?? data.signature ?? '';
  const htmlSource = data.html_body ?? data.html ?? '';
  const text = trimComposeWhitespace(textSource || htmlToPlainText(htmlSource));
  const html = trimComposeWhitespace(htmlSource) || plainTextToHtml(text);
  if (!text && !html) {
    return null;
  }
  return {
    text,
    html,
  };
}

function getComposeBodyText(form: Pick<ComposeForm, 'textBody' | 'htmlBody'>) {
  const textFromPlain = trimComposeWhitespace(form.textBody);
  if (textFromPlain) {
    return textFromPlain;
  }
  return trimComposeWhitespace(htmlToPlainText(form.htmlBody));
}

function appendComposeSignature(form: ComposeForm, signature: DefaultSignature) {
  const bodyText = getComposeBodyText(form);
  const signatureText = trimComposeWhitespace(signature.text);
  if (!signatureText) {
    return null;
  }
  if (bodyText.endsWith(signatureText)) {
    return null;
  }
  const separator = '\n\n';
  const nextTextBody = bodyText ? `${bodyText}${separator}${signatureText}` : `${separator}${signatureText}`;
  const nextHtmlBody = trimComposeWhitespace(form.htmlBody)
    ? `${trimComposeWhitespace(form.htmlBody)}<p><br></p>${signature.html}`
    : `<p><br></p>${signature.html}`;
  return {
    textBody: nextTextBody,
    htmlBody: nextHtmlBody,
  };
}

function signatureToOption(signature: MailSignature): SignatureOption | null {
  const normalized = normalizeSignaturePayload({
    text_body: signature.text_body,
    html_body: signature.html_body,
    content: signature.content,
  });
  if (!normalized) {
    return null;
  }
  return {
    id: signature.id,
    name: signature.name,
    text: normalized.text,
    html: normalized.html,
    isDefault: Boolean(signature.is_default),
  };
}

function createInlineImageId() {
  return `inline-image-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function parseInlineImageWidth(image: HTMLImageElement) {
  const styleWidth = image.style.width.trim();
  if (styleWidth.endsWith('px')) {
    const parsed = Number.parseInt(styleWidth, 10);
    if (Number.isFinite(parsed) && parsed > 0) {
      return parsed;
    }
  }
  const attrWidth = Number.parseInt(image.getAttribute('width') || '', 10);
  if (Number.isFinite(attrWidth) && attrWidth > 0) {
    return attrWidth;
  }
  return INLINE_IMAGE_DEFAULT_WIDTH;
}

function clampInlineImageWidth(value: number) {
  return Math.min(INLINE_IMAGE_MAX_WIDTH, Math.max(INLINE_IMAGE_MIN_WIDTH, value));
}

function getInitialEditorMode(values?: ComposeValues | null): EditorMode {
  if (!values) {
    return 'rich';
  }
  return values.html_body && values.html_body.trim() ? 'rich' : 'plain';
}

function isSessionExpired(error: Error & { code?: string; status?: number }) {
  return error.status === 401 || error.code === 'AUTH_SESSION_EXPIRED';
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
  const headers = new Headers(init?.body instanceof FormData ? init?.headers : { 'Content-Type': 'application/json', ...(init?.headers ?? {}) });
  const method = (init?.method || 'GET').toUpperCase();
  if (!SAFE_METHODS.has(method)) {
    const csrfToken = readCookie(CSRF_COOKIE_NAME);
    if (csrfToken) {
      headers.set('X-CSRF-Token', csrfToken);
    }
  }
  const response = await fetch(input, {
    credentials: 'include',
    headers,
    ...init,
  });
  const payload = (await response.json()) as ApiResponse<T>;
  if (!response.ok || !payload.success || payload.data === null) {
    const message = payload.error?.message || '请求失败，请稍后重试';
    const error = new Error(message) as Error & { code?: string; status?: number };
    error.code = payload.error?.code;
    error.status = response.status;
    throw error;
  }
  return payload.data;
}

async function requestOptionalApi<T>(input: string, init?: RequestInit): Promise<T | null> {
  const headers = new Headers(init?.body instanceof FormData ? init?.headers : { 'Content-Type': 'application/json', ...(init?.headers ?? {}) });
  const method = (init?.method || 'GET').toUpperCase();
  if (!SAFE_METHODS.has(method)) {
    const csrfToken = readCookie(CSRF_COOKIE_NAME);
    if (csrfToken) {
      headers.set('X-CSRF-Token', csrfToken);
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
    const error = new Error(message) as Error & { code?: string; status?: number };
    error.code = payload.error?.code;
    error.status = response.status;
    throw error;
  }
  return payload.data;
}

function stripComposeEditorMetadata(value: string) {
  const container = document.createElement('div');
  container.innerHTML = value;
  container.querySelectorAll<HTMLElement>('.inline-image').forEach((wrapper) => {
    wrapper.removeAttribute('data-inline-image-id');
    wrapper.removeAttribute('data-selected');
    wrapper.removeAttribute('data-inline-image-position');
  });
  return container.innerHTML;
}

function buildPayload(
  form: ComposeForm,
  attachmentIds: string[],
  editorMode: EditorMode,
  draftId?: string | null,
  options: { stripEditorMetadata?: boolean } = {},
) {
  const htmlBody = form.htmlBody.replace(/\u200b/g, '').replace(/<span data-style-reset="true"><\/span>/g, '');
  const preparedHtml = options.stripEditorMetadata ? stripComposeEditorMetadata(htmlBody) : htmlBody;
  const normalizedHtmlBody = preparedHtml.trim() ? preparedHtml : null;
  return {
    draft_id: draftId || null,
    to: parseAddresses(form.to),
    cc: parseAddresses(form.cc),
    bcc: parseAddresses(form.bcc),
    subject: form.subject,
    text_body: form.textBody,
    html_body: editorMode === 'plain' ? null : normalizedHtmlBody,
    attachment_ids: attachmentIds,
  };
}

function buildCacheValues(form: ComposeForm, attachmentIds: string[], editorMode: EditorMode, attachments: AttachmentState[]): ComposeValues {
  const payload = buildPayload(form, attachmentIds, editorMode);
  return {
    to: payload.to,
    cc: payload.cc,
    bcc: payload.bcc,
    subject: payload.subject,
    text_body: payload.text_body,
    html_body: payload.html_body,
    attachment_ids: payload.attachment_ids,
    attachments: attachments
      .filter((item) => item.status === 'uploaded')
      .map((item) => ({
        attachment_id: item.attachment_id,
        filename: item.filename,
        content_type: item.content_type,
        size_bytes: item.size_bytes,
        expires_at: item.expires_at,
      })),
  };
}

function saveDraft(payload: ReturnType<typeof buildPayload>, init?: RequestInit) {
  return requestApi<DraftSaveResult>('/api/drafts', {
    method: 'POST',
    body: JSON.stringify(payload),
    ...init,
  });
}

function updateDraft(draftId: string, payload: ReturnType<typeof buildPayload>, init?: RequestInit) {
  return requestApi<DraftSaveResult>(`/api/drafts/${encodeURIComponent(draftId)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
    ...init,
  });
}

function sendMessage(payload: ReturnType<typeof buildPayload>) {
  return requestApi<SendResult>('/api/messages/send', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

function fetchDraft(draftId: string) {
  return requestApi<ComposeValues & { draft_id: string }>(`/api/drafts/${encodeURIComponent(draftId)}`, {
    method: 'GET',
  });
}

function fetchDefaultSignature() {
  return requestOptionalApi<DefaultSignaturePayload>('/api/signatures/default', {
    method: 'GET',
  });
}

function searchContacts(query: string) {
  return requestApi<{ contacts: Contact[] }>(`/api/contacts?query=${encodeURIComponent(query)}&limit=10`, {
    method: 'GET',
  });
}

async function uploadAttachments(files: File[], onProgress?: (progress: number) => void) {
  const attachments: AttachmentItem[] = [];
  for (const file of files) {
    const attachmentId = createUploadId();
    const totalChunks = Math.max(1, Math.ceil(file.size / CHUNK_UPLOAD_SIZE_BYTES));
    let latestAttachment: AttachmentItem | null = null;
    for (let index = 0; index < totalChunks; index += 1) {
      const start = index * CHUNK_UPLOAD_SIZE_BYTES;
      const end = Math.min(file.size, start + CHUNK_UPLOAD_SIZE_BYTES);
      const chunk = file.slice(start, end, file.type || 'application/octet-stream');
      const formData = new FormData();
      formData.append('attachment_id', attachmentId);
      formData.append('chunk_index', String(index));
      formData.append('total_chunks', String(totalChunks));
      formData.append('file_size_bytes', String(file.size));
      formData.append('filename', file.name);
      formData.append('content_type', file.type || 'application/octet-stream');
      formData.append('chunk', chunk, file.name);
      const result = await requestApi<{ attachment: ChunkUploadResponse }>('/api/attachments/chunks', {
        method: 'POST',
        body: formData,
      });
      latestAttachment = result.attachment;
      onProgress?.(Math.round(((index + 1) / totalChunks) * 100));
    }
    if (!latestAttachment) {
      throw new Error('附件上传失败');
    }
    attachments.push(latestAttachment);
  }
  return { attachments };
}

export default function ComposePanel({
  open,
  draftId,
  initialValues,
  from = 'user@localhost',
  onClose,
  onSent,
  onSessionExpired,
}: ComposePanelProps) {
  const [form, setForm] = useState<ComposeForm>(() => createEmptyForm(initialValues));
  const [editorMode, setEditorMode] = useState<EditorMode>(() => getInitialEditorMode(initialValues));
  const [currentDraftId, setCurrentDraftId] = useState<string | null>(draftId ?? null);
  const [attachments, setAttachments] = useState<AttachmentState[]>(() => normalizeAttachments(initialValues));
  const [recipientState, setRecipientState] = useState<RecipientState>(() => createRecipientState(initialValues));
  const [saveState, setSaveState] = useState<SaveState>('idle');
  const [sendState, setSendState] = useState<SendState>('idle');
  const [activeAddressField, setActiveAddressField] = useState<keyof Pick<ComposeForm, 'to' | 'cc' | 'bcc'> | null>(null);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [loadedDraftId, setLoadedDraftId] = useState<string | null>(null);
  const [openColorMenu, setOpenColorMenu] = useState<ColorMenu>(null);
  const [tablePickerOpen, setTablePickerOpen] = useState(false);
  const [tablePickerSize, setTablePickerSize] = useState({ rows: 1, columns: 1 });
  const [selectedInlineImageId, setSelectedInlineImageId] = useState<string | null>(null);
  const [selectedInlineImageWidth, setSelectedInlineImageWidth] = useState(INLINE_IMAGE_DEFAULT_WIDTH);
  const [activeStyles, setActiveStyles] = useState<ActiveStyles>(DEFAULT_ACTIVE_STYLES);
  const [isDraggingAttachments, setIsDraggingAttachments] = useState(false);
  const [recipientSelection, setRecipientSelection] = useState<RecipientSelection>(null);
  const [availableSignatures, setAvailableSignatures] = useState<SignatureOption[]>([]);
  const [selectedSignatureId, setSelectedSignatureId] = useState<string>('');
  const bodyEditorRef = useRef<HTMLDivElement | null>(null);
  const plainBodyRef = useRef<HTMLTextAreaElement | null>(null);
  const imageInputRef = useRef<HTMLInputElement | null>(null);
  const recipientInputRefs = useRef<Record<RecipientField, HTMLInputElement | null>>({ to: null, cc: null, bcc: null });
  const attachmentDragDepthRef = useRef(0);
  const savedSelectionRef = useRef<Range | null>(null);
  const richHtmlCacheRef = useRef<string>(initialValues?.html_body ?? '');
  const plainModeEditedRef = useRef(false);
  const hasUserEditedRef = useRef(false);
  const placeCursorAtStartRef = useRef(false);

  const uploadedAttachmentIds = useMemo(
    () => attachments.filter((item) => item.status === 'uploaded').map((item) => item.attachment_id),
    [attachments],
  );

  useEffect(() => {
    if (!open) {
      return;
    }
    setForm(createEmptyForm(initialValues));
    setEditorMode(getInitialEditorMode(initialValues));
    setCurrentDraftId(draftId ?? null);
    setAttachments(normalizeAttachments(initialValues));
    setRecipientState(createRecipientState(initialValues));
    setSaveState('idle');
    setSendState('idle');
    setErrorMessage(null);
    hasUserEditedRef.current = false;
    setLoadedDraftId(null);
    setOpenColorMenu(null);
    setTablePickerOpen(false);
    setSelectedInlineImageId(null);
    setSelectedInlineImageWidth(INLINE_IMAGE_DEFAULT_WIDTH);
    setActiveStyles(DEFAULT_ACTIVE_STYLES);
    setIsDraggingAttachments(false);
    attachmentDragDepthRef.current = 0;
    setRecipientSelection(null);
    setAvailableSignatures([]);
    setSelectedSignatureId('');
    richHtmlCacheRef.current = initialValues?.html_body ?? '';
    plainModeEditedRef.current = false;
    placeCursorAtStartRef.current = false;
  }, [open, draftId, initialValues]);

  useLayoutEffect(() => {
    if (!open || !placeCursorAtStartRef.current) {
      return;
    }
    if (editorMode === 'plain') {
      const editor = plainBodyRef.current;
      if (editor) {
        editor.focus();
        editor.setSelectionRange(0, 0);
      }
    } else {
      const editor = bodyEditorRef.current;
      if (editor) {
        editor.focus();
        const range = document.createRange();
        range.setStart(editor, 0);
        range.collapse(true);
        const selection = window.getSelection();
        if (selection) {
          selection.removeAllRanges();
          selection.addRange(range);
        }
      }
    }
    placeCursorAtStartRef.current = false;
  }, [editorMode, form.htmlBody, form.textBody, open]);

  useEffect(() => {
    if (editorMode !== 'rich') {
      return;
    }
    if (bodyEditorRef.current && bodyEditorRef.current.innerHTML !== form.htmlBody) {
      bodyEditorRef.current.innerHTML = form.htmlBody;
    }
  }, [editorMode, form.htmlBody, open]);

  useEffect(() => {
    if (editorMode !== 'rich' || !selectedInlineImageId) {
      return;
    }
    const editor = bodyEditorRef.current;
    if (!editor) {
      return;
    }
    let foundImage: HTMLImageElement | null = null;
    editor.querySelectorAll<HTMLElement>('.inline-image').forEach((wrapper) => {
      const isSelected = wrapper.getAttribute('data-inline-image-id') === selectedInlineImageId;
      if (isSelected) {
        wrapper.setAttribute('data-selected', 'true');
        foundImage = wrapper.querySelector('img');
      } else {
        wrapper.removeAttribute('data-selected');
      }
    });
    if (foundImage) {
      setSelectedInlineImageWidth(parseInlineImageWidth(foundImage));
      return;
    }
    setSelectedInlineImageId(null);
  }, [editorMode, form.htmlBody, selectedInlineImageId]);

  useEffect(() => {
    if (!open || !draftId || loadedDraftId === draftId) {
      return;
    }
    let cancelled = false;
    fetchDraft(draftId)
      .then((draft) => {
        if (cancelled) {
          return;
        }
        setForm(createEmptyForm(draft));
        setEditorMode(getInitialEditorMode(draft));
        setAttachments((current) => (draft.attachments?.length ? normalizeAttachments(draft) : current));
        setRecipientState(createRecipientState(draft));
        setRecipientSelection(null);
        setCurrentDraftId(draft.draft_id ?? draftId);
        setLoadedDraftId(draftId);
        hasUserEditedRef.current = false;
        richHtmlCacheRef.current = draft.html_body ?? plainTextToHtml(draft.text_body ?? '');
        plainModeEditedRef.current = false;
      })
      .catch((error: Error & { code?: string; status?: number }) => {
        if (cancelled) {
          return;
        }
        if (isSessionExpired(error)) {
          onSessionExpired?.();
        }
        setErrorMessage(error.message || '草稿加载失败');
      });
    return () => {
      cancelled = true;
    };
  }, [draftId, loadedDraftId, onSessionExpired, open]);

  useEffect(() => {
    if (!open || draftId) {
      return;
    }
    let cancelled = false;
    fetchDefaultSignature()
      .then((result) => {
        if (cancelled) {
          return;
        }
        const signature = normalizeSignaturePayload(result);
        if (!signature) {
          return;
        }
        setForm((current) => {
          const nextValues = appendComposeSignature(current, signature);
          if (!nextValues) {
            return current;
          }
          if (!trimComposeWhitespace(current.textBody) && !trimComposeWhitespace(current.htmlBody)) {
            placeCursorAtStartRef.current = true;
          }
          return { ...current, ...nextValues };
        });
      })
      .catch((error: Error & { code?: string; status?: number }) => {
        if (cancelled) {
          return;
        }
        if (isSessionExpired(error)) {
          onSessionExpired?.();
        }
      });
    return () => {
      cancelled = true;
    };
  }, [draftId, initialValues, onSessionExpired, open]);

  useEffect(() => {
    if (!open) {
      return;
    }
    let cancelled = false;
    Promise.all([fetchSignatures(), fetchDefaultSignature()])
      .then(([listResult, defaultResult]) => {
        if (cancelled) {
          return;
        }
        const items = (listResult.signatures || [])
          .map(signatureToOption)
          .filter((item): item is SignatureOption => Boolean(item));
        setAvailableSignatures(items);
        const normalizedDefault = normalizeSignaturePayload(defaultResult);
        const defaultId =
          (normalizedDefault
            ? items.find((item) => item.text === normalizedDefault.text && item.html === normalizedDefault.html)?.id
            : undefined) ||
          items.find((item) => item.isDefault)?.id ||
          '';
        setSelectedSignatureId(defaultId);
      })
      .catch((error: Error & { code?: string; status?: number }) => {
        if (cancelled) {
          return;
        }
        if (isSessionExpired(error)) {
          onSessionExpired?.();
        }
      });
    return () => {
      cancelled = true;
    };
  }, [onSessionExpired, open]);

  useEffect(() => {
    if (!open) {
      return;
    }
    if (!hasUserEditedRef.current && saveState !== 'saved') {
      return;
    }
    writeComposeDraftCache(from, {
      draft_id: currentDraftId,
      values: buildCacheValues(form, uploadedAttachmentIds, editorMode, attachments),
      updated_at: new Date().toISOString(),
    });
  }, [attachments, currentDraftId, editorMode, form, from, open, saveState, uploadedAttachmentIds]);

  useEffect(() => {
    if (!open || !activeAddressField) {
      setContacts([]);
      return;
    }
    const input = recipientState[activeAddressField].draft;
    const query = input.split(/[,;\s]+/).pop()?.trim() ?? '';
    if (query.length < CONTACT_MIN_QUERY_LENGTH) {
      setContacts([]);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      searchContacts(query)
        .then((result) => {
          if (!cancelled) {
            setContacts(result.contacts);
          }
        })
        .catch((error: Error & { code?: string; status?: number }) => {
          if (cancelled) {
            return;
          }
          if (isSessionExpired(error)) {
            onSessionExpired?.();
          }
          setContacts([]);
        });
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [activeAddressField, onSessionExpired, open, recipientState]);

  useEffect(() => {
    if (!open || saveState !== 'dirty' || sendState === 'sending') {
      return;
    }
    const timer = window.setInterval(() => {
      void handleSaveDraft('auto');
    }, AUTOSAVE_DELAY_MS);
    return () => {
      window.clearInterval(timer);
    };
  }, [open, saveState, sendState, currentDraftId, editorMode, form, uploadedAttachmentIds, attachments]);

  useEffect(() => {
    if (!open) {
      return;
    }
    const handleUnload = () => {
      if (!hasUserEditedRef.current || sendState === 'sending') {
        return;
      }
      void handleSaveDraft('auto', { keepalive: true, silent: true });
    };
    window.addEventListener('beforeunload', handleUnload);
    window.addEventListener('pagehide', handleUnload);
    return () => {
      window.removeEventListener('beforeunload', handleUnload);
      window.removeEventListener('pagehide', handleUnload);
    };
  }, [open, sendState, currentDraftId, editorMode, form, uploadedAttachmentIds, attachments]);

  if (!open) {
    return null;
  }

  function markEdited() {
    hasUserEditedRef.current = true;
    setSaveState('dirty');
    setErrorMessage(null);
  }

  function updateField(field: keyof ComposeForm, value: string) {
    markEdited();
    setForm((current) => ({ ...current, [field]: value }));
  }

  function focusRecipientInput(field: RecipientField) {
    recipientInputRefs.current[field]?.focus();
  }

  function applyRecipientFieldState(field: RecipientField, nextFieldState: RecipientFieldState) {
    markEdited();
    setRecipientState((currentState) => ({ ...currentState, [field]: nextFieldState }));
    setForm((currentForm) => ({ ...currentForm, [field]: serializeRecipientField(nextFieldState.tags, nextFieldState.draft) }));
  }

  function clearRecipientSelectionIf(field: RecipientField) {
    setRecipientSelection((current) => (current && current.field === field ? null : current));
  }

  function addRecipientValues(field: RecipientField, values: string[]) {
    const additions = values.map((item) => item.trim()).filter(Boolean);
    if (additions.length === 0) {
      return;
    }
    let duplicateValue: string | null = null;
    const current = recipientState[field];
    const seen = getRecipientKeys(recipientState, { field });
    const nextTags = [...current.tags];
    for (const value of additions) {
      const key = recipientKey(value);
      if (seen.has(key) || nextTags.some((tag) => recipientKey(tag) === key)) {
        duplicateValue = value;
        continue;
      }
      seen.add(key);
      nextTags.push(value);
    }
    applyRecipientFieldState(field, { tags: nextTags, draft: '' });
    setRecipientSelection(null);
    focusRecipientInput(field);
    if (duplicateValue) {
      setErrorMessage('收件人不能重复');
    }
  }

  function commitRecipientDraft(field: RecipientField) {
    const draft = recipientState[field].draft.trim();
    if (!draft) {
      return;
    }
    const values = parseAddresses(draft);
    if (values.length === 0) {
      return;
    }
    addRecipientValues(field, values);
  }

  function removeRecipientTag(field: RecipientField, index: number) {
    const current = recipientState[field];
    if (index < 0 || index >= current.tags.length) {
      return;
    }
    const nextTags = current.tags.filter((_, tagIndex) => tagIndex !== index);
    applyRecipientFieldState(field, { ...current, tags: nextTags });
    setRecipientSelection((current) => {
      if (!current || current.field !== field) {
        return current;
      }
      if (current.index === index) {
        return null;
      }
      if (current.index > index) {
        return { field, index: current.index - 1 };
      }
      return current;
    });
    focusRecipientInput(field);
  }

  function setRecipientDraft(field: RecipientField, value: string) {
    applyRecipientFieldState(field, { ...recipientState[field], draft: value });
    clearRecipientSelectionIf(field);
  }

  function handleRecipientBlur(field: RecipientField) {
    commitRecipientDraft(field);
    setRecipientSelection(null);
  }

  function handleRecipientKeyDown(field: RecipientField, event: KeyboardEvent<HTMLInputElement>) {
    const current = recipientState[field];
    const selectionStart = event.currentTarget.selectionStart ?? current.draft.length;
    const hasSelection = recipientSelection?.field === field;

    if (event.key === 'Enter' || event.key === ',' || event.key === ';') {
      event.preventDefault();
      if (current.draft.trim()) {
        commitRecipientDraft(field);
      }
      return;
    }

    if (event.key === 'Escape') {
      if (hasSelection) {
        event.preventDefault();
        setRecipientSelection(null);
      }
      return;
    }

    if (event.key === 'Backspace') {
      if (current.draft.length > 0 || selectionStart > 0) {
        return;
      }
      event.preventDefault();
      if (hasSelection && recipientSelection) {
        removeRecipientTag(field, recipientSelection.index);
        return;
      }
      if (current.tags.length > 0) {
        setRecipientSelection({ field, index: current.tags.length - 1 });
      }
      return;
    }

    if (event.key === 'Delete') {
      if (hasSelection && recipientSelection) {
        event.preventDefault();
        removeRecipientTag(field, recipientSelection.index);
      }
      return;
    }

    if (event.key === 'ArrowLeft') {
      if (current.draft.length > 0 && selectionStart > 0) {
        return;
      }
      if (hasSelection && recipientSelection) {
        event.preventDefault();
        if (recipientSelection.index > 0) {
          setRecipientSelection({ field, index: recipientSelection.index - 1 });
        } else {
          setRecipientSelection(null);
        }
        return;
      }
      if (selectionStart === 0 && current.tags.length > 0) {
        event.preventDefault();
        setRecipientSelection({ field, index: current.tags.length - 1 });
      }
      return;
    }

    if (event.key === 'ArrowRight' && hasSelection && recipientSelection) {
      event.preventDefault();
      if (recipientSelection.index < current.tags.length - 1) {
        setRecipientSelection({ field, index: recipientSelection.index + 1 });
      } else {
        setRecipientSelection(null);
      }
    }
  }

  function validateSendRecipients() {
    const error = validateRecipientDuplicates(form);
    if (error) {
      setSendState('error');
      setErrorMessage(error);
      return error;
    }
    return null;
  }

  function editorContainsNode(node: Node | null) {
    return Boolean(node && bodyEditorRef.current?.contains(node));
  }

  function saveEditorSelection() {
    const selection = window.getSelection();
    if (!selection || selection.rangeCount === 0) {
      return;
    }
    const range = selection.getRangeAt(0);
    if (editorContainsNode(range.commonAncestorContainer)) {
      savedSelectionRef.current = range.cloneRange();
    }
  }

  function restoreEditorSelection() {
    const selection = window.getSelection();
    const range = savedSelectionRef.current;
    if (!selection || !range) {
      collapseSelectionToEditorEnd();
      focusBodyEditor();
      return;
    }
    selection.removeAllRanges();
    selection.addRange(range);
    focusBodyEditor();
  }

  function collapseSelectionToEditorEnd() {
    const editor = bodyEditorRef.current;
    const selection = window.getSelection();
    if (!editor || !selection) {
      return;
    }
    const range = document.createRange();
    range.selectNodeContents(editor);
    range.collapse(false);
    selection.removeAllRanges();
    selection.addRange(range);
    savedSelectionRef.current = range.cloneRange();
  }

  function createPlainTypingAnchor() {
    const editor = bodyEditorRef.current;
    const selection = window.getSelection();
    if (!editor || !selection) {
      return;
    }
    const anchor = document.createElement('span');
    anchor.setAttribute('data-style-reset', 'true');
    anchor.appendChild(document.createTextNode('\u200b'));
    editor.appendChild(anchor);
    const range = document.createRange();
    range.setStart(anchor.firstChild ?? anchor, 1);
    range.collapse(true);
    selection.removeAllRanges();
    selection.addRange(range);
    savedSelectionRef.current = range.cloneRange();
  }

  function hasSavedTextSelection() {
    const range = savedSelectionRef.current;
    return Boolean(range && !range.collapsed && editorContainsNode(range.commonAncestorContainer));
  }

  function refreshActiveStyles() {
    if (typeof document.queryCommandState !== 'function') {
      return;
    }
    setActiveStyles((current) => ({
      ...current,
      insertOrderedList: document.queryCommandState('insertOrderedList'),
      insertUnorderedList: document.queryCommandState('insertUnorderedList'),
    }));
  }

  function hasEditorContent() {
    const editor = bodyEditorRef.current;
    if (!editor) {
      return false;
    }
    const visibleText = (editor.textContent || '').replace(/\u200b/g, '').trim();
    return Boolean(visibleText || editor.querySelector('img,table,blockquote,ul,ol,li'));
  }

  function getSelectionElement() {
    const range = savedSelectionRef.current;
    if (!range || !editorContainsNode(range.commonAncestorContainer)) {
      return null;
    }
    const node = range.startContainer;
    return node.nodeType === Node.ELEMENT_NODE ? (node as Element) : node.parentElement;
  }

  function readInlineStylesFromSelection() {
    const element = getSelectionElement();
    if (!element || !hasEditorContent()) {
      return;
    }
    setActiveStyles((current) => ({
      ...current,
      bold: Boolean(element.closest('strong,b')),
      italic: Boolean(element.closest('em,i')),
      underline: Boolean(element.closest('u,[style*="underline"]')),
      strikeThrough: Boolean(element.closest('s,strike,[style*="line-through"]')),
    }));
    refreshActiveStyles();
  }

  function syncBodyFromEditor() {
    const editor = bodyEditorRef.current;
    if (!editor) {
      return;
    }
    saveEditorSelection();
    markEdited();
    setForm((current) => ({
      ...current,
      textBody: (editor.innerText ?? editor.textContent ?? '').replace(/\u200b/g, ''),
      htmlBody: editor.innerHTML,
    }));
    richHtmlCacheRef.current = editor.innerHTML;
    if (selectedInlineImageId && !getSelectedInlineImageElements()) {
      setSelectedInlineImageId(null);
      setSelectedInlineImageWidth(INLINE_IMAGE_DEFAULT_WIDTH);
    }
  }

  function focusBodyEditor() {
    if (editorMode === 'plain') {
      plainBodyRef.current?.focus();
      return;
    }
    bodyEditorRef.current?.focus();
  }

  function switchEditorMode(nextMode: EditorMode) {
    if (nextMode === editorMode) {
      return;
    }
    markEdited();
    setOpenColorMenu(null);
    setTablePickerOpen(false);
    if (nextMode === 'plain') {
      const richHtml = form.htmlBody || richHtmlCacheRef.current || plainTextToHtml(form.textBody);
      richHtmlCacheRef.current = richHtml;
      plainModeEditedRef.current = false;
      setEditorMode('plain');
      setForm((current) => ({
        ...current,
        textBody: htmlToPlainText(richHtml || current.textBody),
        htmlBody: richHtml,
      }));
      window.setTimeout(() => plainBodyRef.current?.focus(), 0);
      return;
    }
    const nextHtml = plainModeEditedRef.current ? plainTextToHtml(form.textBody) : richHtmlCacheRef.current || plainTextToHtml(form.textBody);
    richHtmlCacheRef.current = nextHtml;
    setEditorMode('rich');
    setForm((current) => ({
      ...current,
      htmlBody: nextHtml,
    }));
    window.setTimeout(() => bodyEditorRef.current?.focus(), 0);
  }

  function runEditorCommand(command: string, value?: string) {
    restoreEditorSelection();
    if (typeof document.execCommand === 'function') {
      document.execCommand(command, false, value);
    }
    saveEditorSelection();
    refreshActiveStyles();
    syncBodyFromEditor();
  }

  function toggleInlineStyle(command: ToggleStyleCommand) {
    const nextEnabled = !activeStyles[command];
    const nextStyles = { ...activeStyles, [command]: nextEnabled };
    setActiveStyles((current) => ({ ...current, [command]: nextEnabled }));
    restoreEditorSelection();
    if (typeof document.execCommand === 'function') {
      document.execCommand(command, false);
    }
    saveEditorSelection();
    if (!nextStyles.bold && !nextStyles.italic && !nextStyles.underline && !nextStyles.strikeThrough) {
      createPlainTypingAnchor();
    }
    syncBodyFromEditor();
  }

  function insertHtmlAtCursor(html: string) {
    const editor = bodyEditorRef.current;
    if (!editor) {
      return;
    }
    restoreEditorSelection();
    const selection = window.getSelection();
    if (selection && selection.rangeCount > 0) {
      const range = selection.getRangeAt(0);
      if (editorContainsNode(range.commonAncestorContainer)) {
        const fragment = createInlineFragment(html);
        range.deleteContents();
        const lastNode = fragment.lastChild;
        range.insertNode(fragment);
        if (lastNode) {
          range.setStartAfter(lastNode);
          range.collapse(true);
          selection.removeAllRanges();
          selection.addRange(range);
          savedSelectionRef.current = range.cloneRange();
        }
        syncBodyFromEditor();
        return;
      }
    }
    editor.appendChild(createInlineFragment(html));
    syncBodyFromEditor();
  }

  function hasActiveInlineStyle() {
    return activeStyles.bold || activeStyles.italic || activeStyles.underline || activeStyles.strikeThrough;
  }

  function buildActiveInlineHtml(text: string) {
    let html = escapeHtml(text);
    if (activeStyles.bold) {
      html = `<strong>${html}</strong>`;
    }
    if (activeStyles.italic) {
      html = `<em>${html}</em>`;
    }
    if (activeStyles.underline) {
      html = `<span style="text-decoration: underline;">${html}</span>`;
    }
    if (activeStyles.strikeThrough) {
      html = `<s>${html}</s>`;
    }
    return html;
  }

  function buildMissingInlineHtml(text: string, element: Element | null) {
    let html = escapeHtml(text);
    if (activeStyles.bold && !element?.closest('strong,b')) {
      html = `<strong>${html}</strong>`;
    }
    if (activeStyles.italic && !element?.closest('em,i')) {
      html = `<em>${html}</em>`;
    }
    if (activeStyles.underline && !element?.closest('u,[style*="underline"]')) {
      html = `<span style="text-decoration: underline;">${html}</span>`;
    }
    if (activeStyles.strikeThrough && !element?.closest('s,strike,[style*="line-through"]')) {
      html = `<s>${html}</s>`;
    }
    return html;
  }

  function createInlineFragment(html: string) {
    const template = document.createElement('template');
    template.innerHTML = html;
    return template.content;
  }

  function getSelectedInlineImageElements() {
    const editor = bodyEditorRef.current;
    if (!editor || !selectedInlineImageId) {
      return null;
    }
    const wrapper = Array.from(editor.querySelectorAll<HTMLElement>('.inline-image')).find(
      (item) => item.getAttribute('data-inline-image-id') === selectedInlineImageId,
    );
    if (!wrapper) {
      return null;
    }
    const image = wrapper.querySelector('img') as HTMLImageElement | null;
    if (!image) {
      return null;
    }
    return { wrapper, image };
  }

  function setSelectedInlineImageSize(width: number) {
    const elements = getSelectedInlineImageElements();
    if (!elements) {
      return;
    }
    const nextWidth = clampInlineImageWidth(width);
    elements.image.style.width = `${nextWidth}px`;
    elements.image.style.maxWidth = '100%';
    elements.image.style.height = 'auto';
    elements.image.removeAttribute('width');
    elements.image.removeAttribute('height');
    setSelectedInlineImageWidth(nextWidth);
    syncBodyFromEditor();
  }

  function selectInlineImage(wrapper: HTMLElement) {
    const image = wrapper.querySelector('img') as HTMLImageElement | null;
    if (!image) {
      return;
    }
    const imageId = wrapper.getAttribute('data-inline-image-id') || createInlineImageId();
    wrapper.setAttribute('data-inline-image-id', imageId);
    wrapper.setAttribute('data-selected', 'true');
    setSelectedInlineImageId(imageId);
    setSelectedInlineImageWidth(parseInlineImageWidth(image));
    saveEditorSelection();
    focusBodyEditor();
  }

  function clearInlineImageSelection() {
    setSelectedInlineImageId(null);
    const editor = bodyEditorRef.current;
    if (!editor) {
      return;
    }
    editor.querySelectorAll<HTMLElement>('.inline-image[data-selected="true"]').forEach((wrapper) => {
      wrapper.removeAttribute('data-selected');
    });
  }

  function setInlineImageWrapperPosition(wrapper: HTMLElement, position: InlineImagePosition) {
    wrapper.style.textAlign = position;
    wrapper.setAttribute('data-inline-image-position', position);
  }

  function wrapCurrentTextNodeWithActiveStyles() {
    const editor = bodyEditorRef.current;
    if (!editor || !hasActiveInlineStyle()) {
      return;
    }
    const selection = window.getSelection();
    if (!selection || selection.rangeCount === 0) {
      return;
    }
    const range = selection.getRangeAt(0);
    const node = range.startContainer;
    const textNode =
      node.nodeType === Node.TEXT_NODE
        ? (node as Text)
        : node.childNodes[Math.max(0, range.startOffset - 1)]?.nodeType === Node.TEXT_NODE
          ? (node.childNodes[Math.max(0, range.startOffset - 1)] as Text)
          : null;
    if (!textNode || !textNode.textContent || !editorContainsNode(textNode)) {
      return;
    }
    const parent = textNode.parentElement;
    if (!parent) {
      return;
    }
    const hasAllActiveStyles =
      (!activeStyles.bold || Boolean(parent.closest('strong,b'))) &&
      (!activeStyles.italic || Boolean(parent.closest('em,i'))) &&
      (!activeStyles.underline || Boolean(parent.closest('u,[style*="underline"]'))) &&
      (!activeStyles.strikeThrough || Boolean(parent.closest('s,strike,[style*="line-through"]')));
    if (hasAllActiveStyles) {
      return;
    }
    const replacement = createInlineFragment(buildMissingInlineHtml(textNode.textContent, parent));
    textNode.replaceWith(replacement);
    collapseSelectionToEditorEnd();
  }

  function applyInlineStyle(property: string, value: string) {
    insertHtmlAtCursor(`<span style="${property}: ${value};">${window.getSelection()?.toString() || '文字'}</span>`);
  }

  function applyColor(command: 'foreColor' | 'hiliteColor', color: string) {
    saveEditorSelection();
    const hasTextSelection = hasSavedTextSelection();
    runEditorCommand(command, color);
    if (hasTextSelection) {
      collapseSelectionToEditorEnd();
    }
    syncBodyFromEditor();
    setOpenColorMenu(null);
  }

  function applyCustomColor(command: 'foreColor' | 'hiliteColor') {
    const color = window.prompt('请输入颜色值，例如 #1f2937');
    if (!color) {
      return;
    }
    applyColor(command, color);
  }

  function handleLinkInsert() {
    const url = window.prompt('请输入链接地址');
    if (!url) {
      return;
    }
    runEditorCommand('createLink', url);
  }

  function handleQuoteInsert() {
    insertHtmlAtCursor('<blockquote>引用内容</blockquote>');
  }

  function insertTable(rows: number, columns: number) {
    if (!Number.isInteger(rows) || !Number.isInteger(columns) || rows < 1 || columns < 1 || rows > 20 || columns > 10) {
      setErrorMessage('表格行数需为 1-20，列数需为 1-10');
      return;
    }
    const cells = Array.from({ length: columns }, () => '<td><br></td>').join('');
    const body = Array.from({ length: rows }, () => `<tr>${cells}</tr>`).join('');
    insertHtmlAtCursor(`<table><tbody>${body}</tbody></table><p><br></p>`);
    setTablePickerOpen(false);
  }

  function handleCustomTableInsert() {
    const rows = Number(window.prompt('请输入表格行数', String(tablePickerSize.rows)));
    const columns = Number(window.prompt('请输入表格列数', String(tablePickerSize.columns)));
    insertTable(rows, columns);
  }

  function handleInlineImageFiles(files: File[]) {
    files.forEach((file) => {
      if (!file.type.startsWith('image/')) {
        setErrorMessage('只能插入图片文件');
        return;
      }
      markEdited();
      const reader = new FileReader();
      reader.onload = () => {
        const src = String(reader.result ?? '');
        const inlineImageId = createInlineImageId();
        insertHtmlAtCursor(
          `<div class="inline-image" data-inline-image-id="${inlineImageId}" data-inline-image-position="center" style="text-align: center;">` +
            `<img src="${src}" alt="${escapeHtml(file.name)}" style="width: ${INLINE_IMAGE_DEFAULT_WIDTH}px; max-width: 100%; height: auto;" />` +
            `</div><p><br></p>`,
        );
        setSelectedInlineImageId(inlineImageId);
        setSelectedInlineImageWidth(INLINE_IMAGE_DEFAULT_WIDTH);
      };
      reader.readAsDataURL(file);
    });
  }

  function handleInlineImageUpload(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = '';
    handleInlineImageFiles(files);
  }

  function handlePlainBodyChange(event: ChangeEvent<HTMLTextAreaElement>) {
    const value = event.target.value;
    plainModeEditedRef.current = true;
    markEdited();
    setForm((current) => ({
      ...current,
      textBody: value,
      htmlBody: plainTextToHtml(value),
    }));
  }

  async function handleSaveDraft(
    mode: 'manual' | 'auto',
    options: { keepalive?: boolean; silent?: boolean } = {},
  ) {
    const payload = buildPayload(form, uploadedAttachmentIds, editorMode, currentDraftId);
    const isUpdate = Boolean(currentDraftId);
    const shouldUpdateUi = !options.silent;
    if (shouldUpdateUi) {
      setSaveState('saving');
      setErrorMessage(null);
    }
    try {
      const requestInit = options.keepalive ? { keepalive: true } : undefined;
      const result = isUpdate ? await updateDraft(currentDraftId as string, payload, requestInit) : await saveDraft(payload, requestInit);
      if (shouldUpdateUi) {
        setCurrentDraftId(result.draft_id);
        setSaveState('saved');
      }
      hasUserEditedRef.current = false;
      writeComposeDraftCache(from, {
        draft_id: result.draft_id,
        values: buildCacheValues(form, uploadedAttachmentIds, editorMode, attachments),
        updated_at: new Date().toISOString(),
      });
    } catch (error) {
      if (options.silent) {
        return;
      }
      const typedError = error as Error & { code?: string; status?: number };
      if (isSessionExpired(typedError)) {
        onSessionExpired?.();
      }
      setSaveState('error');
      if (mode === 'manual') {
        setErrorMessage(typedError.message || '草稿保存失败');
      }
    }
  }

  function updateAttachmentProgress(localId: string, progress: number) {
    setAttachments((current) => current.map((item) => (item.local_id === localId ? { ...item, progress } : item)));
  }

  async function handleUploadFiles(files: File[]) {
    if (files.length === 0) {
      return;
    }
    const localItems: AttachmentState[] = files.map((file) => ({
      local_id: `${file.name}-${file.size}-${Date.now()}-${Math.random()}`,
      attachment_id: '',
      filename: file.name,
      content_type: file.type || 'application/octet-stream',
      size_bytes: file.size,
      progress: 0,
      status: 'uploading',
      error: null,
    }));
    markEdited();
    setAttachments((current) => [...current, ...localItems]);
    for (const [index, file] of files.entries()) {
      const localItem = localItems[index];
      try {
        updateAttachmentProgress(localItem.local_id, 5);
        const result = await uploadAttachments([file], (progress) => updateAttachmentProgress(localItem.local_id, progress));
        const uploaded = result.attachments[0];
        if (!uploaded) {
          throw new Error('附件上传结果缺失');
        }
        setAttachments((current) =>
          current.map((item) =>
            item.local_id === localItem.local_id
              ? { ...uploaded, local_id: item.local_id, progress: 100, status: 'uploaded', error: null }
              : item,
          ),
        );
      } catch (error) {
        const typedError = error as Error & { code?: string; status?: number };
        if (isSessionExpired(typedError)) {
          onSessionExpired?.();
        }
        setAttachments((current) =>
          current.map((item) =>
            item.local_id === localItem.local_id
              ? { ...item, progress: 100, status: 'failed', error: typedError.message || '附件上传失败' }
              : item,
          ),
        );
      }
    }
  }

  async function handleUpload(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = '';
    await handleUploadFiles(files);
  }

  function handleAttachmentDragEnter(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    attachmentDragDepthRef.current += 1;
    setIsDraggingAttachments(true);
  }

  function handleAttachmentDragOver(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = 'copy';
    }
  }

  function handleAttachmentDragLeave(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    attachmentDragDepthRef.current = Math.max(0, attachmentDragDepthRef.current - 1);
    if (attachmentDragDepthRef.current === 0) {
      setIsDraggingAttachments(false);
    }
  }

  async function handleAttachmentDrop(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    attachmentDragDepthRef.current = 0;
    setIsDraggingAttachments(false);
    await handleUploadFiles(Array.from(event.dataTransfer.files ?? []));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (sendState === 'sending') {
      return;
    }
    const recipientError = validateSendRecipients();
    if (recipientError) {
      return;
    }
    setSendState('sending');
    setErrorMessage(null);
    try {
      const result = await sendMessage(
        buildPayload(form, uploadedAttachmentIds, editorMode, currentDraftId, { stripEditorMetadata: true }),
      );
      setSendState('sent');
      clearComposeDraftCache(from);
      onSent?.(result);
      onClose();
    } catch (error) {
      const typedError = error as Error & { code?: string; status?: number };
      if (isSessionExpired(typedError)) {
        onSessionExpired?.();
      }
      setSendState('error');
      setErrorMessage(typedError.message || '邮件发送失败');
    }
  }

  function handleDiscard() {
    clearComposeDraftCache(from);
    onClose();
  }

  function chooseContact(email: string) {
    if (!activeAddressField) {
      return;
    }
    addRecipientValues(activeAddressField, [email]);
    setContacts([]);
  }

  function handleApplySignature(signatureId: string) {
    setSelectedSignatureId(signatureId);
    const signature = availableSignatures.find((item) => item.id === signatureId);
    if (!signature) {
      return;
    }
    const nextValues = appendComposeSignature(form, { text: signature.text, html: signature.html });
    if (!nextValues) {
      return;
    }
    markEdited();
    setForm((current) => ({ ...current, ...nextValues }));
    if (editorMode === 'rich') {
      richHtmlCacheRef.current = nextValues.htmlBody;
    }
  }

  const recipientFieldConfigs: Array<{ field: RecipientField; label: string; ariaLabel: string }> = [
    { field: 'to', label: 'To', ariaLabel: '收件人' },
    { field: 'cc', label: 'Cc', ariaLabel: '抄送' },
    { field: 'bcc', label: 'Bcc', ariaLabel: '密送' },
  ];

  const saveLabel =
    saveState === 'saving'
      ? '保存中'
      : saveState === 'saved'
        ? '已保存'
        : saveState === 'error'
          ? '保存失败'
          : saveState === 'dirty'
            ? '有未保存内容'
            : '尚未保存';

  return (
    <aside className="compose-panel" aria-label="写信面板" aria-modal="true">
      <form className="compose-panel-form" onSubmit={handleSubmit}>
        <header className="compose-panel-header">
          <h2 className="visually-hidden">写信</h2>
          <button type="button" className="compose-window-button" aria-label="最小化写信">
            -
          </button>
          <button type="button" className="compose-window-button" onClick={handleDiscard} aria-label="关闭写信">
            x
          </button>
        </header>

        {errorMessage ? (
          <div className="compose-alert" role="alert">
            {errorMessage}
          </div>
        ) : null}

        <section className="compose-address-container" aria-label="地址栏">
          <div className="compose-field-row">
            <span className="field-label">From</span>
            <span className="field-value text-black">{from}</span>
          </div>
          {recipientFieldConfigs.map((config) => {
            const fieldState = recipientState[config.field];
            const selectedIndex = recipientSelection?.field === config.field ? recipientSelection.index : -1;
            return (
              <div className="compose-field-row compose-recipient-row" key={config.field}>
                <span className="field-label">{config.label}</span>
                <div className="recipient-input-shell">
                  {fieldState.tags.map((recipient, index) => (
                    <div
                      key={`${config.field}-${recipient}-${index}`}
                      className={`recipient-chip${selectedIndex === index ? ' selected' : ''}`}
                      data-selected={selectedIndex === index ? 'true' : undefined}
                    >
                      <button
                        type="button"
                        className="recipient-chip-label"
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={() => {
                          setActiveAddressField(config.field);
                          setRecipientSelection({ field: config.field, index });
                          focusRecipientInput(config.field);
                        }}
                        aria-label={`选择 ${recipient}`}
                      >
                        {recipient}
                      </button>
                      <button
                        type="button"
                        className="recipient-chip-remove"
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={() => removeRecipientTag(config.field, index)}
                        aria-label={`删除 ${recipient}`}
                      >
                        ×
                      </button>
                    </div>
                  ))}
                  <input
                    ref={(element) => {
                      recipientInputRefs.current[config.field] = element;
                    }}
                    className="recipient-input"
                    value={fieldState.draft}
                    onChange={(event) => setRecipientDraft(config.field, event.target.value)}
                    onFocus={() => setActiveAddressField(config.field)}
                    onBlur={() => handleRecipientBlur(config.field)}
                    onKeyDown={(event) => handleRecipientKeyDown(config.field, event)}
                    placeholder="Add recipient"
                    autoComplete="off"
                    aria-label={config.ariaLabel}
                  />
                </div>
              </div>
            );
          })}
          {contacts.length > 0 ? (
            <ul className="compose-contact-suggestions" aria-label="联系人建议">
              {contacts.map((contact) => (
                <li key={contact.email}>
                  <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => chooseContact(contact.email)}>
                    {contact.email}
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </section>

        <label className="compose-subject-container">
          <span className="visually-hidden">主题</span>
          <input
            className="subject-input"
            value={form.subject}
            onChange={(event) => updateField('subject', event.target.value)}
            placeholder="Subject"
            aria-label="主题"
          />
        </label>

        <section className="compose-body-container" aria-label="正文编辑区">
          <div className="compose-toolbar-floating" role="toolbar" aria-label="编辑工具栏">
            <div className="toolbar-mode-group" role="group" aria-label="编辑模式">
              <button
                type="button"
                className="toolbar-mode-btn"
                aria-pressed={editorMode === 'rich'}
                onClick={() => switchEditorMode('rich')}
              >
                富文本
              </button>
              <button
                type="button"
                className="toolbar-mode-btn"
                aria-pressed={editorMode === 'plain'}
                onClick={() => switchEditorMode('plain')}
              >
                纯文本
              </button>
            </div>
            {editorMode === 'rich' ? (
              <>
                <span className="toolbar-divider" aria-hidden="true" />
                <select className="toolbar-select" aria-label="字体" defaultValue="" onChange={(event) => applyInlineStyle('font-family', event.target.value)}>
                  <option value="" disabled>
                    字体
                  </option>
                  {FONT_FAMILIES.map((font) => (
                    <option key={font} value={font}>
                      {font}
                    </option>
                  ))}
                </select>
                <select className="toolbar-select toolbar-size-select" aria-label="字号" defaultValue="" onChange={(event) => applyInlineStyle('font-size', event.target.value)}>
                  <option value="" disabled>
                    字号
                  </option>
                  {FONT_SIZES.map((size) => (
                    <option key={size.value} value={size.value}>
                      {size.label}
                    </option>
                  ))}
                </select>
                <span className="toolbar-divider" aria-hidden="true" />
                <button
                  type="button"
                  className="toolbar-icon-btn"
                  aria-label="加粗"
                  aria-pressed={activeStyles.bold}
                  title="加粗"
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => toggleInlineStyle('bold')}
                >
                  B
                </button>
                <button
                  type="button"
                  className="toolbar-icon-btn italic"
                  aria-label="斜体"
                  aria-pressed={activeStyles.italic}
                  title="斜体"
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => toggleInlineStyle('italic')}
                >
                  I
                </button>
                <button
                  type="button"
                  className="toolbar-icon-btn underline"
                  aria-label="下划线"
                  aria-pressed={activeStyles.underline}
                  title="下划线"
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => toggleInlineStyle('underline')}
                >
                  U
                </button>
                <button
                  type="button"
                  className="toolbar-icon-btn strike"
                  aria-label="删除线"
                  aria-pressed={activeStyles.strikeThrough}
                  title="删除线"
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => toggleInlineStyle('strikeThrough')}
                >
                  S
                </button>
                <div className="toolbar-popover-root">
                  <button
                    type="button"
                    className="toolbar-color-button"
                    aria-label="文字颜色"
                    aria-expanded={openColorMenu === 'text'}
                    title="文字颜色"
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => {
                      saveEditorSelection();
                      setTablePickerOpen(false);
                      setOpenColorMenu((current) => (current === 'text' ? null : 'text'));
                    }}
                  >
                    <span className="toolbar-color-letter">A</span>
                    <span className="toolbar-caret">▾</span>
                  </button>
                  {openColorMenu === 'text' ? (
                    <div className="toolbar-popover color-palette" role="menu" aria-label="文字颜色色板">
                      <div className="color-grid">
                        {COLOR_SWATCHES.map((color) => (
                          <button
                            key={`text-${color}`}
                            type="button"
                            className="color-swatch"
                            style={{ backgroundColor: color }}
                            aria-label={`文字颜色 ${color}`}
                            onMouseDown={(event) => {
                              event.preventDefault();
                              applyColor('foreColor', color);
                            }}
                          />
                        ))}
                      </div>
                      <button type="button" className="palette-more-button" onMouseDown={(event) => event.preventDefault()} onClick={() => applyCustomColor('foreColor')}>
                        其它颜色...
                      </button>
                    </div>
                  ) : null}
                </div>
                <div className="toolbar-popover-root">
                  <button
                    type="button"
                    className="toolbar-color-button highlight"
                    aria-label="背景高亮颜色"
                    aria-expanded={openColorMenu === 'highlight'}
                    title="背景高亮颜色"
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => {
                      saveEditorSelection();
                      setTablePickerOpen(false);
                      setOpenColorMenu((current) => (current === 'highlight' ? null : 'highlight'));
                    }}
                  >
                    <span className="toolbar-color-letter">A</span>
                    <span className="toolbar-caret">▾</span>
                  </button>
                  {openColorMenu === 'highlight' ? (
                    <div className="toolbar-popover color-palette" role="menu" aria-label="背景高亮颜色色板">
                      <div className="color-grid">
                        {COLOR_SWATCHES.map((color) => (
                          <button
                            key={`highlight-${color}`}
                            type="button"
                            className="color-swatch"
                            style={{ backgroundColor: color }}
                            aria-label={`背景高亮颜色 ${color}`}
                            onMouseDown={(event) => {
                              event.preventDefault();
                              applyColor('hiliteColor', color);
                            }}
                          />
                        ))}
                      </div>
                      <button type="button" className="palette-more-button" onMouseDown={(event) => event.preventDefault()} onClick={() => applyCustomColor('hiliteColor')}>
                        其它颜色...
                      </button>
                    </div>
                  ) : null}
                </div>
                <span className="toolbar-divider" aria-hidden="true" />
                <button
                  type="button"
                  className="toolbar-icon-btn"
                  aria-label="有序列表"
                  aria-pressed={activeStyles.insertOrderedList}
                  title="有序列表"
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => runEditorCommand('insertOrderedList')}
                >
                  1.
                </button>
                <button
                  type="button"
                  className="toolbar-icon-btn"
                  aria-label="无序列表"
                  aria-pressed={activeStyles.insertUnorderedList}
                  title="无序列表"
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => runEditorCommand('insertUnorderedList')}
                >
                  •
                </button>
                <button type="button" className="toolbar-icon-btn" aria-label="减少缩进" title="减少缩进" onMouseDown={(event) => event.preventDefault()} onClick={() => runEditorCommand('outdent')}>
                  ←
                </button>
                <button type="button" className="toolbar-icon-btn" aria-label="增加缩进" title="增加缩进" onMouseDown={(event) => event.preventDefault()} onClick={() => runEditorCommand('indent')}>
                  →
                </button>
                <button type="button" className="toolbar-icon-btn" aria-label="引用块" title="引用块" onMouseDown={(event) => event.preventDefault()} onClick={handleQuoteInsert}>
                  “”
                </button>
                <span className="toolbar-divider" aria-hidden="true" />
                <button type="button" className="toolbar-icon-btn toolbar-link-btn" aria-label="添加链接" title="添加链接" onMouseDown={(event) => event.preventDefault()} onClick={handleLinkInsert}>
                  link
                </button>
                <button type="button" className="toolbar-icon-btn" aria-label="取消链接" title="取消链接" onMouseDown={(event) => event.preventDefault()} onClick={() => runEditorCommand('unlink')}>
                  Tx
                </button>
                <div className="toolbar-popover-root">
                  <button
                    type="button"
                    className="toolbar-icon-btn"
                    aria-label="插入表格"
                    aria-expanded={tablePickerOpen}
                    title="插入表格"
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => {
                      saveEditorSelection();
                      setOpenColorMenu(null);
                      setTablePickerOpen((current) => !current);
                    }}
                  >
                    表
                  </button>
                  {tablePickerOpen ? (
                    <div className="toolbar-popover table-picker" role="menu" aria-label="表格选择器">
                      <div className="table-picker-size">
                        {tablePickerSize.rows} × {tablePickerSize.columns} 表格
                      </div>
                      <div className="table-picker-grid">
                        {Array.from({ length: TABLE_PICKER_ROWS }).map((_, rowIndex) =>
                          Array.from({ length: TABLE_PICKER_COLUMNS }).map((__, columnIndex) => {
                            const rows = rowIndex + 1;
                            const columns = columnIndex + 1;
                            const selected = rows <= tablePickerSize.rows && columns <= tablePickerSize.columns;
                            return (
                              <button
                                key={`${rows}-${columns}`}
                                type="button"
                                className="table-picker-cell"
                                data-selected={selected ? 'true' : 'false'}
                                aria-label={`${rows} × ${columns} 表格`}
                                onMouseEnter={() => setTablePickerSize({ rows, columns })}
                                onMouseDown={(event) => event.preventDefault()}
                                onClick={() => insertTable(rows, columns)}
                              />
                            );
                          }),
                        )}
                      </div>
                      <button type="button" className="palette-more-button" onMouseDown={(event) => event.preventDefault()} onClick={handleCustomTableInsert}>
                        其它...
                      </button>
                    </div>
                  ) : null}
                </div>
                <button type="button" className="toolbar-icon-btn" aria-label="插入图片" title="插入图片" onClick={() => imageInputRef.current?.click()}>
                  图
                </button>
                <input ref={imageInputRef} className="hidden-file-input" type="file" accept="image/*" onChange={handleInlineImageUpload} aria-label="插入图片" />
                {selectedInlineImageId ? (
                  <>
                    <span className="toolbar-divider" aria-hidden="true" />
                    <div className="toolbar-inline-image-group" role="group" aria-label="内嵌图片调整">
                      <button
                        type="button"
                        className="toolbar-icon-btn"
                        aria-label="图片左对齐"
                        title="图片左对齐"
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={() => {
                          const selected = getSelectedInlineImageElements();
                          if (!selected) {
                            return;
                          }
                          setInlineImageWrapperPosition(selected.wrapper, 'left');
                          syncBodyFromEditor();
                        }}
                      >
                        ←
                      </button>
                      <button
                        type="button"
                        className="toolbar-icon-btn"
                        aria-label="图片居中"
                        title="图片居中"
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={() => {
                          const selected = getSelectedInlineImageElements();
                          if (!selected) {
                            return;
                          }
                          setInlineImageWrapperPosition(selected.wrapper, 'center');
                          syncBodyFromEditor();
                        }}
                      >
                        ↔
                      </button>
                      <button
                        type="button"
                        className="toolbar-icon-btn"
                        aria-label="图片右对齐"
                        title="图片右对齐"
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={() => {
                          const selected = getSelectedInlineImageElements();
                          if (!selected) {
                            return;
                          }
                          setInlineImageWrapperPosition(selected.wrapper, 'right');
                          syncBodyFromEditor();
                        }}
                      >
                        →
                      </button>
                      <label className="toolbar-inline-image-size" aria-label="图片大小">
                        <span className="visually-hidden">图片大小</span>
                        <input
                          type="range"
                          min={INLINE_IMAGE_MIN_WIDTH}
                          max={INLINE_IMAGE_MAX_WIDTH}
                          step={INLINE_IMAGE_STEP}
                          value={selectedInlineImageWidth}
                          onChange={(event) => setSelectedInlineImageSize(Number(event.target.value))}
                        />
                      </label>
                    </div>
                  </>
                ) : null}
              </>
            ) : null}
          </div>

          {editorMode === 'rich' ? (
            <div
              ref={bodyEditorRef}
              className="body-input rich-body-input"
              contentEditable
              role="textbox"
              aria-label="正文"
              aria-multiline="true"
              data-placeholder="Write your email..."
              data-empty={hasEditorContent() ? 'false' : 'true'}
              onMouseDown={(event) => {
                const target = event.target as HTMLElement | null;
                const wrapper = target?.closest('.inline-image') as HTMLElement | null;
                if (wrapper) {
                  event.preventDefault();
                  selectInlineImage(wrapper);
                  return;
                }
                clearInlineImageSelection();
              }}
              onInput={() => {
                wrapCurrentTextNodeWithActiveStyles();
                syncBodyFromEditor();
              }}
              onBlur={syncBodyFromEditor}
              onFocus={() => {
                saveEditorSelection();
                readInlineStylesFromSelection();
              }}
              onMouseUp={() => {
                saveEditorSelection();
                readInlineStylesFromSelection();
              }}
              onKeyUp={() => {
                saveEditorSelection();
                readInlineStylesFromSelection();
              }}
              onPaste={() => window.setTimeout(syncBodyFromEditor, 0)}
            />
          ) : (
            <textarea
              ref={plainBodyRef}
              className="body-input plain-body-input"
              role="textbox"
              aria-label="正文"
              aria-multiline="true"
              placeholder="Write your email..."
              value={form.textBody}
              onChange={handlePlainBodyChange}
            />
          )}
        </section>

        <section className="compose-attachments" aria-label="附件上传">
          <div
            className={`compose-upload-dropzone${isDraggingAttachments ? ' is-dragging' : ''}`}
            aria-label="附件拖拽上传区"
            onDragEnter={handleAttachmentDragEnter}
            onDragOver={handleAttachmentDragOver}
            onDragLeave={handleAttachmentDragLeave}
            onDrop={handleAttachmentDrop}
          >
            <label className="compose-upload-control">
              <span>点击或拖拽添加附件</span>
              <input type="file" multiple onChange={handleUpload} aria-label="添加附件" />
            </label>
            <div className="compose-upload-hint">较大附件会自动分块上传。</div>
          </div>
          {attachments.length > 0 ? (
            <ul aria-label="附件列表">
              {attachments.map((attachment) => (
                <li key={attachment.local_id} data-status={attachment.status}>
                  <span>{attachment.filename}</span>
                  <span>{formatFileSize(attachment.size_bytes)}</span>
                  <progress value={attachment.progress} max={100} aria-label={`${attachment.filename} 上传进度`} />
                  <span className="compose-upload-status">
                    {attachment.status === 'uploading'
                      ? '上传中'
                      : attachment.status === 'uploaded'
                        ? '已上传'
                        : attachment.error || '上传失败'}
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
        </section>

        <footer className="compose-panel-footer">
          <div className="footer-left">
            <div className="send-btn-group">
              <button className="send-main-btn" type="submit" disabled={sendState === 'sending'}>
                {sendState === 'sending' ? '发送中' : '发送'}
              </button>
              <button className="send-dropdown-btn" type="button" aria-label="发送选项">
                v
              </button>
            </div>
            <button
              className="draft-btn"
              type="button"
              onClick={() => void handleSaveDraft('manual')}
              disabled={saveState === 'saving' || sendState === 'sending'}
            >
              保存草稿
            </button>
            {availableSignatures.length > 0 ? (
              <label className="compose-signature-picker">
                <span>签名</span>
                <select value={selectedSignatureId} onChange={(event) => handleApplySignature(event.target.value)}>
                  <option value="">选择签名</option>
                  {availableSignatures.map((signature) => (
                    <option key={signature.id} value={signature.id}>
                      {signature.isDefault ? `默认 · ${signature.name}` : signature.name}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            <span className="draft-status" aria-live="polite">
              草稿状态：{saveLabel}
            </span>
          </div>
          <div className="footer-right" aria-label="更多操作">
            <button type="button" className="icon-btn" title="添加附件" onClick={() => undefined}>
              @
            </button>
            <button type="button" className="icon-btn" title="插入变量">
              {`{}`}
            </button>
            <button type="button" className="icon-btn" title="日历">
              #
            </button>
            <button type="button" className="icon-btn danger" title="丢弃草稿" onClick={handleDiscard}>
              del
            </button>
          </div>
        </footer>
      </form>
    </aside>
  );
}
