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
};

type SaveState = 'idle' | 'dirty' | 'saving' | 'saved' | 'error';
type SendState = 'idle' | 'sending' | 'sent' | 'error';
type RichMode = 'plain' | 'rich';

const AUTOSAVE_DELAY_MS = 5000;
const CONTACT_MIN_QUERY_LENGTH = 1;
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS', 'TRACE']);
const CSRF_COOKIE_NAME = 'webmail_csrf';

function createEmptyForm(values?: ComposeValues | null): ComposeForm {
  return {
    to: (values?.to ?? []).join(', '),
    cc: (values?.cc ?? []).join(', '),
    bcc: (values?.bcc ?? []).join(', '),
    subject: values?.subject ?? '',
    textBody: values?.text_body ?? '',
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
  return {
    draft_id: draftId || null,
    to: parseAddresses(form.to),
    cc: parseAddresses(form.cc),
    bcc: parseAddresses(form.bcc),
    subject: form.subject,
    text_body: form.textBody,
    html_body: null,
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
  const [richMode, setRichMode] = useState<RichMode>('plain');
  const [activeAddressField, setActiveAddressField] = useState<keyof Pick<ComposeForm, 'to' | 'cc' | 'bcc'> | null>(null);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [loadedDraftId, setLoadedDraftId] = useState<string | null>(null);
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
  }, [open, draftId, initialValues]);

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

  function updateField(field: keyof ComposeForm, value: string) {
    hasUserEditedRef.current = true;
    setSaveState('dirty');
    setErrorMessage(null);
    setForm((current) => ({ ...current, [field]: value }));
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
    hasUserEditedRef.current = true;
    setSaveState('dirty');
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
            <button type="button" className="toolbar-btn toolbar-ai-btn" title="优化文字">
              优化文字
            </button>
            <button type="button" className="toolbar-btn" title="Ask AI">
              AI
            </button>
            <span className="toolbar-divider" aria-hidden="true" />
            <button type="button" className="toolbar-btn" aria-pressed={richMode === 'rich'} onClick={() => setRichMode('rich')}>
              富文本
            </button>
            <button type="button" className="toolbar-btn" aria-pressed={richMode === 'plain'} onClick={() => setRichMode('plain')}>
              纯文本
            </button>
            <span className="toolbar-divider" aria-hidden="true" />
            <button type="button" className="toolbar-icon-btn" disabled title="富文本工具将在后续编辑器接入后启用">
              B
            </button>
            <button type="button" className="toolbar-icon-btn italic" disabled title="富文本工具将在后续编辑器接入后启用">
              I
            </button>
            <button type="button" className="toolbar-icon-btn underline" disabled title="富文本工具将在后续编辑器接入后启用">
              U
            </button>
            <button type="button" className="toolbar-icon-btn strike" disabled title="富文本工具将在后续编辑器接入后启用">
              S
            </button>
            <button type="button" className="toolbar-icon-btn" disabled title="富文本工具将在后续编辑器接入后启用">
              link
            </button>
            <span className="toolbar-divider" aria-hidden="true" />
            <button type="button" className="toolbar-icon-btn text-style-btn" disabled title="富文本工具将在后续编辑器接入后启用">
              A
            </button>
            <button type="button" className="toolbar-icon-btn" disabled title="富文本工具将在后续编辑器接入后启用">
              Tx
            </button>
          </div>

          <label className="compose-body-label">
            <span className="visually-hidden">正文</span>
            <textarea
              className="body-input"
              value={form.textBody}
              onChange={(event) => updateField('textBody', event.target.value)}
              rows={10}
              placeholder="Write your email..."
              aria-label="正文"
            />
          </label>
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
            <button type="button" className="icon-btn" title="AI 写作">
              AI
            </button>
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
