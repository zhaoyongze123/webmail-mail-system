import { useEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent } from 'react';

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

type SaveState = 'idle' | 'dirty' | 'saving' | 'saved' | 'error';
type SendState = 'idle' | 'sending' | 'sent' | 'error';
type ColorMenu = 'text' | 'highlight' | null;
type ActiveStyles = {
  bold: boolean;
  italic: boolean;
  underline: boolean;
  strikeThrough: boolean;
  insertOrderedList: boolean;
  insertUnorderedList: boolean;
};
type ToggleStyleCommand = 'bold' | 'italic' | 'underline' | 'strikeThrough';

const AUTOSAVE_DELAY_MS = 5000;
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

function normalizeAttachments(values?: ComposeValues | null): AttachmentState[] {
  return (values?.attachments ?? []).map((attachment) => ({
    ...attachment,
    local_id: attachment.attachment_id,
    progress: 100,
    status: 'uploaded',
    error: null,
  }));
}

function parseAddresses(value: string) {
  return value
    .split(/[,;\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
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

function buildPayload(form: ComposeForm, attachmentIds: string[], draftId?: string | null) {
  const htmlBody = form.htmlBody.replace(/\u200b/g, '').replace(/<span data-style-reset="true"><\/span>/g, '');
  return {
    draft_id: draftId || null,
    to: parseAddresses(form.to),
    cc: parseAddresses(form.cc),
    bcc: parseAddresses(form.bcc),
    subject: form.subject,
    text_body: form.textBody,
    html_body: htmlBody.trim() ? htmlBody : null,
    attachment_ids: attachmentIds,
  };
}

function saveDraft(payload: ReturnType<typeof buildPayload>) {
  return requestApi<DraftSaveResult>('/api/drafts', {
    method: 'POST',
    body: JSON.stringify(payload),
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

function searchContacts(query: string) {
  return requestApi<{ contacts: Contact[] }>(`/api/contacts?query=${encodeURIComponent(query)}&limit=10`, {
    method: 'GET',
  });
}

async function uploadAttachments(files: File[]) {
  const formData = new FormData();
  files.forEach((file) => formData.append('files', file));
  return requestApi<{ attachments: AttachmentItem[] }>('/api/attachments', {
    method: 'POST',
    body: formData,
  });
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
  const [currentDraftId, setCurrentDraftId] = useState<string | null>(draftId ?? null);
  const [attachments, setAttachments] = useState<AttachmentState[]>(() => normalizeAttachments(initialValues));
  const [saveState, setSaveState] = useState<SaveState>('idle');
  const [sendState, setSendState] = useState<SendState>('idle');
  const [activeAddressField, setActiveAddressField] = useState<keyof Pick<ComposeForm, 'to' | 'cc' | 'bcc'> | null>(null);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [loadedDraftId, setLoadedDraftId] = useState<string | null>(null);
  const [openColorMenu, setOpenColorMenu] = useState<ColorMenu>(null);
  const [tablePickerOpen, setTablePickerOpen] = useState(false);
  const [tablePickerSize, setTablePickerSize] = useState({ rows: 1, columns: 1 });
  const [activeStyles, setActiveStyles] = useState<ActiveStyles>(DEFAULT_ACTIVE_STYLES);
  const bodyEditorRef = useRef<HTMLDivElement | null>(null);
  const imageInputRef = useRef<HTMLInputElement | null>(null);
  const savedSelectionRef = useRef<Range | null>(null);
  const hasUserEditedRef = useRef(false);

  const uploadedAttachmentIds = useMemo(
    () => attachments.filter((item) => item.status === 'uploaded').map((item) => item.attachment_id),
    [attachments],
  );

  useEffect(() => {
    if (!open) {
      return;
    }
    setForm(createEmptyForm(initialValues));
    setCurrentDraftId(draftId ?? null);
    setAttachments(normalizeAttachments(initialValues));
    setSaveState('idle');
    setSendState('idle');
    setErrorMessage(null);
    hasUserEditedRef.current = false;
    setLoadedDraftId(null);
    setOpenColorMenu(null);
    setTablePickerOpen(false);
    setActiveStyles(DEFAULT_ACTIVE_STYLES);
  }, [open, draftId, initialValues]);

  useEffect(() => {
    if (bodyEditorRef.current && bodyEditorRef.current.innerHTML !== form.htmlBody) {
      bodyEditorRef.current.innerHTML = form.htmlBody;
    }
  }, [form.htmlBody, open]);

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
        setAttachments(normalizeAttachments(draft));
        setCurrentDraftId(draft.draft_id ?? draftId);
        setLoadedDraftId(draftId);
        hasUserEditedRef.current = false;
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
    if (!open || !activeAddressField) {
      setContacts([]);
      return;
    }
    const input = form[activeAddressField];
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
  }, [activeAddressField, form, onSessionExpired, open]);

  useEffect(() => {
    if (!open || !hasUserEditedRef.current || sendState === 'sending') {
      return;
    }
    const timer = window.setTimeout(() => {
      void handleSaveDraft('auto');
    }, AUTOSAVE_DELAY_MS);
    return () => {
      window.clearTimeout(timer);
    };
  }, [form, uploadedAttachmentIds, open, sendState]);

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
    return Boolean(editor?.textContent?.trim());
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
  }

  function focusBodyEditor() {
    bodyEditorRef.current?.focus();
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
    runEditorCommand('insertHTML', html);
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
      const reader = new FileReader();
      reader.onload = () => {
        const src = String(reader.result ?? '');
        insertHtmlAtCursor(`<img src="${src}" alt="${file.name}" />`);
      };
      reader.readAsDataURL(file);
    });
  }

  function handleInlineImageUpload(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = '';
    handleInlineImageFiles(files);
  }

  async function handleSaveDraft(mode: 'manual' | 'auto') {
    setSaveState('saving');
    setErrorMessage(null);
    try {
      const result = await saveDraft(buildPayload(form, uploadedAttachmentIds, currentDraftId));
      setCurrentDraftId(result.draft_id);
      setSaveState('saved');
      hasUserEditedRef.current = false;
    } catch (error) {
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

  async function handleUpload(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = '';
    if (files.length === 0) {
      return;
    }
    const localItems: AttachmentState[] = files.map((file) => ({
      local_id: `${file.name}-${file.size}-${Date.now()}-${Math.random()}`,
      attachment_id: '',
      filename: file.name,
      content_type: file.type || 'application/octet-stream',
      size_bytes: file.size,
      progress: 25,
      status: 'uploading',
      error: null,
    }));
    markEdited();
    setAttachments((current) => [...current, ...localItems]);
    try {
      const result = await uploadAttachments(files);
      setAttachments((current) =>
        current.map((item) => {
          const index = localItems.findIndex((local) => local.local_id === item.local_id);
          if (index === -1) {
            return item;
          }
          const uploaded = result.attachments[index];
          if (!uploaded) {
            return { ...item, progress: 100, status: 'failed', error: '附件上传结果缺失' };
          }
          return { ...uploaded, local_id: item.local_id, progress: 100, status: 'uploaded', error: null };
        }),
      );
    } catch (error) {
      const typedError = error as Error & { code?: string; status?: number };
      if (isSessionExpired(typedError)) {
        onSessionExpired?.();
      }
      setAttachments((current) =>
        current.map((item) =>
          localItems.some((local) => local.local_id === item.local_id)
            ? { ...item, progress: 100, status: 'failed', error: typedError.message || '附件上传失败' }
            : item,
        ),
      );
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (sendState === 'sending') {
      return;
    }
    setSendState('sending');
    setErrorMessage(null);
    try {
      const result = await sendMessage(buildPayload(form, uploadedAttachmentIds, currentDraftId));
      setSendState('sent');
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

  function chooseContact(email: string) {
    if (!activeAddressField) {
      return;
    }
    const value = form[activeAddressField];
    const lastSeparatorIndex = Math.max(value.lastIndexOf(','), value.lastIndexOf(';'), value.lastIndexOf(' '));
    const prefix = lastSeparatorIndex >= 0 ? value.slice(0, lastSeparatorIndex + 1) : '';
    const next = `${prefix}${email}`;
    updateField(activeAddressField, next.endsWith(' ') ? next : `${next} `);
    setContacts([]);
  }

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
          <button type="button" className="compose-window-button" onClick={onClose} aria-label="关闭写信">
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
          <label className="compose-field-row">
            <span className="field-label">To</span>
            <input
              className="address-input"
              value={form.to}
              onChange={(event) => updateField('to', event.target.value)}
              onFocus={() => setActiveAddressField('to')}
              placeholder="Add recipient"
              autoComplete="off"
              aria-label="收件人"
            />
          </label>
          <label className="compose-field-row">
            <span className="field-label">Cc</span>
            <input
              className="address-input"
              value={form.cc}
              onChange={(event) => updateField('cc', event.target.value)}
              onFocus={() => setActiveAddressField('cc')}
              placeholder="Add recipient"
              autoComplete="off"
              aria-label="抄送"
            />
          </label>
          <label className="compose-field-row">
            <span className="field-label">Bcc</span>
            <input
              className="address-input"
              value={form.bcc}
              onChange={(event) => updateField('bcc', event.target.value)}
              onFocus={() => setActiveAddressField('bcc')}
              placeholder="Add recipient"
              autoComplete="off"
              aria-label="密送"
            />
          </label>
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
          </div>

          <div
            ref={bodyEditorRef}
            className="body-input rich-body-input"
            contentEditable
            role="textbox"
            aria-label="正文"
            aria-multiline="true"
            data-placeholder="Write your email..."
            data-empty={form.textBody.trim() ? 'false' : 'true'}
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
        </section>

        <section className="compose-attachments" aria-label="附件上传">
          <label className="compose-upload-control">
            <span>添加附件</span>
            <input type="file" multiple onChange={handleUpload} aria-label="添加附件" />
          </label>
          {attachments.length > 0 ? (
            <ul aria-label="附件列表">
              {attachments.map((attachment) => (
                <li key={attachment.local_id}>
                  <span>{attachment.filename}</span>
                  <span>{formatFileSize(attachment.size_bytes)}</span>
                  <progress value={attachment.progress} max={100} aria-label={`${attachment.filename} 上传进度`} />
                  <span>
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
            <button type="button" className="icon-btn danger" title="丢弃草稿" onClick={onClose}>
              del
            </button>
          </div>
        </footer>
      </form>
    </aside>
  );
}
