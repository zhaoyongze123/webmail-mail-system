import { useEffect, useMemo, useState } from 'react';
import type { ApiResponse } from './types';

export type MailAddress = {
  name?: string | null;
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

export type MessageDetail = {
  uid: number | string;
  folder: string;
  subject?: string | null;
  from?: MailAddress | MailAddress[] | string | null;
  to?: MailAddress[] | string[] | string | null;
  cc?: MailAddress[] | string[] | string | null;
  date?: string | null;
  html_body?: string | null;
  text_body?: string | null;
  attachments?: MessageAttachment[] | null;
};

type MessageReaderProps = {
  folder?: string | null;
  uid?: number | string | null;
  onSessionExpired?: () => void;
  onReply?: (message: MessageDetail) => void;
  onForward?: (message: MessageDetail) => void;
};

type LoadState =
  | { status: 'idle'; message: null; error: null }
  | { status: 'loading'; message: null; error: null }
  | { status: 'loaded'; message: MessageDetail; error: null }
  | { status: 'error'; message: null; error: string };

function buildMessageUrl(folder: string, uid: number | string) {
  return `/api/folders/${encodeURIComponent(folder)}/messages/${encodeURIComponent(String(uid))}`;
}

function buildAttachmentUrl(folder: string, uid: number | string, attachmentId: string) {
  return `${buildMessageUrl(folder, uid)}/attachments/${encodeURIComponent(attachmentId)}`;
}

async function requestMessage(folder: string, uid: number | string): Promise<MessageDetail> {
  const response = await fetch(buildMessageUrl(folder, uid), {
    credentials: 'include',
    headers: { Accept: 'application/json' },
  });
  const payload = (await response.json()) as ApiResponse<MessageDetail>;
  if (!response.ok || !payload.success || !payload.data) {
    const message = payload.error?.message || '邮件详情加载失败';
    const error = new Error(message) as Error & { code?: string; status?: number };
    error.code = payload.error?.code;
    error.status = response.status;
    throw error;
  }
  return payload.data;
}

export function sanitizeMessageHtml(html: string) {
  const parser = new DOMParser();
  const document = parser.parseFromString(html, 'text/html');

  document.querySelectorAll('script, iframe, object, embed, base, form, input, button').forEach((node) => node.remove());
  document.body.querySelectorAll<HTMLElement>('*').forEach((element) => {
    for (const attribute of Array.from(element.attributes)) {
      const name = attribute.name.toLowerCase();
      const value = attribute.value.trim().toLowerCase();
      const isDangerousHandler = name.startsWith('on');
      const isDangerousUrl = ['href', 'src', 'xlink:href', 'formaction'].includes(name) && value.startsWith('javascript:');
      if (isDangerousHandler || isDangerousUrl) {
        element.removeAttribute(attribute.name);
      }
    }
  });

  return document.body.innerHTML;
}

function formatAddress(address: MailAddress | string) {
  if (typeof address === 'string') {
    return address;
  }
  if (address.name) {
    return `${address.name} <${address.email}>`;
  }
  return address.email;
}

function formatAddressList(value: MessageDetail['to'] | MessageDetail['from']) {
  if (!value) {
    return '未提供';
  }
  if (Array.isArray(value)) {
    return value.map(formatAddress).join(', ') || '未提供';
  }
  return formatAddress(value);
}

function formatDate(value?: string | null) {
  if (!value) {
    return '未提供';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date);
}

function formatSize(value?: number | null) {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '未知大小';
  }
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function attachmentId(attachment: MessageAttachment) {
  return attachment.attachment_id || attachment.id || '';
}

function attachmentSize(attachment: MessageAttachment) {
  return attachment.size_bytes ?? attachment.size ?? null;
}

export default function MessageReader({ folder, uid, onSessionExpired, onReply, onForward }: MessageReaderProps) {
  const [state, setState] = useState<LoadState>({ status: 'idle', message: null, error: null });

  useEffect(() => {
    if (!folder || uid === null || uid === undefined || uid === '') {
      setState({ status: 'idle', message: null, error: null });
      return;
    }

    const controller = new AbortController();
    setState({ status: 'loading', message: null, error: null });

    requestMessage(folder, uid)
      .then((message) => {
        if (!controller.signal.aborted) {
          setState({ status: 'loaded', message, error: null });
        }
      })
      .catch((error: Error & { code?: string; status?: number }) => {
        if (controller.signal.aborted) {
          return;
        }
        if (error.status === 401 || error.code === 'AUTH_SESSION_EXPIRED') {
          onSessionExpired?.();
        }
        setState({ status: 'error', message: null, error: error.message || '邮件详情加载失败' });
      });

    return () => {
      controller.abort();
    };
  }, [folder, uid, onSessionExpired]);

  const safeHtml = useMemo(() => {
    if (state.status !== 'loaded' || !state.message.html_body) {
      return null;
    }
    return sanitizeMessageHtml(state.message.html_body);
  }, [state]);

  if (!folder || uid === null || uid === undefined || uid === '') {
    return (
      <section className="message-reader message-reader-empty" aria-label="邮件阅读区">
        <h2>选择一封邮件</h2>
        <p>从左侧列表选择邮件后，正文和附件会显示在这里。</p>
      </section>
    );
  }

  if (state.status === 'loading') {
    return (
      <section className="message-reader" aria-label="邮件阅读区" aria-busy="true">
        <p>正在加载邮件详情...</p>
      </section>
    );
  }

  if (state.status === 'error') {
    return (
      <section className="message-reader" aria-label="邮件阅读区">
        <div role="alert">{state.error}</div>
      </section>
    );
  }

  if (state.status !== 'loaded') {
    return null;
  }

  const { message } = state;
  const attachments = message.attachments ?? [];

  return (
    <article className="message-reader" aria-label="邮件阅读区">
      <header className="message-reader-header">
        <p className="eyebrow">邮件详情</p>
        <h2>{message.subject || '无主题'}</h2>
        <dl>
          <div>
            <dt>发件人</dt>
            <dd>{formatAddressList(message.from)}</dd>
          </div>
          <div>
            <dt>收件人</dt>
            <dd>{formatAddressList(message.to)}</dd>
          </div>
          {message.cc ? (
            <div>
              <dt>抄送</dt>
              <dd>{formatAddressList(message.cc)}</dd>
            </div>
          ) : null}
          <div>
            <dt>时间</dt>
            <dd>{formatDate(message.date)}</dd>
          </div>
        </dl>
        <div className="message-reader-actions">
          <button type="button" onClick={() => onReply?.(message)}>
            回复
          </button>
          <button type="button" onClick={() => onForward?.(message)}>
            转发
          </button>
        </div>
      </header>

      <section className="message-reader-body" aria-label="邮件正文">
        {safeHtml ? (
          <div data-testid="message-html-body" dangerouslySetInnerHTML={{ __html: safeHtml }} />
        ) : (
          <pre data-testid="message-text-body">{message.text_body || '这封邮件没有正文内容。'}</pre>
        )}
      </section>

      <section className="message-reader-attachments" aria-label="附件">
        <h3>附件</h3>
        {attachments.length > 0 ? (
          <ul>
            {attachments.map((attachment) => {
              const id = attachmentId(attachment);
              const href = id ? buildAttachmentUrl(folder, uid, id) : '#';
              return (
                <li key={id || attachment.filename}>
                  <div>
                    <strong>{attachment.filename || '未命名附件'}</strong>
                    <span>
                      {attachment.content_type || '未知类型'} · {formatSize(attachmentSize(attachment))}
                    </span>
                  </div>
                  <a href={href} download={attachment.filename || undefined} aria-disabled={!id}>
                    下载
                  </a>
                </li>
              );
            })}
          </ul>
        ) : (
          <p>没有附件</p>
        )}
      </section>
    </article>
  );
}
