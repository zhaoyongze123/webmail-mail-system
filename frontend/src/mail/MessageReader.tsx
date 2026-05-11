import DOMPurify from 'dompurify';
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

const ALLOWED_HTML_TAGS = [
  'a',
  'abbr',
  'b',
  'blockquote',
  'br',
  'caption',
  'code',
  'col',
  'colgroup',
  'div',
  'em',
  'figcaption',
  'figure',
  'font',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
  'hr',
  'i',
  'img',
  'li',
  'ol',
  'p',
  'pre',
  's',
  'small',
  'span',
  'strong',
  'strike',
  'sub',
  'sup',
  'del',
  'ins',
  'style',
  'table',
  'tbody',
  'td',
  'tfoot',
  'th',
  'thead',
  'tr',
  'u',
  'ul',
];

const ALLOWED_HTML_ATTR = [
  'align',
  'alt',
  'bgcolor',
  'border',
  'cellpadding',
  'cellspacing',
  'class',
  'colspan',
  'height',
  'href',
  'id',
  'lang',
  'name',
  'rel',
  'rowspan',
  'src',
  'style',
  'target',
  'title',
  'width',
  'valign',
  'color',
  'face',
  'size',
];

const SANITIZE_CONFIG = {
  ALLOWED_TAGS: ALLOWED_HTML_TAGS,
  ALLOWED_ATTR: ALLOWED_HTML_ATTR,
  ALLOW_DATA_ATTR: false,
  ADD_DATA_URI_TAGS: ['img'],
  FORBID_TAGS: ['script', 'iframe', 'object', 'embed', 'form', 'input', 'button', 'base'],
};

function buildMessageUrl(folder: string, uid: number | string) {
  return `/api/folders/${encodeURIComponent(folder)}/messages/${encodeURIComponent(String(uid))}`;
}

function buildAttachmentUrl(folder: string, uid: number | string, attachmentId: string) {
  return `${buildMessageUrl(folder, uid)}/attachments/${encodeURIComponent(attachmentId)}`;
}

function escapeHtml(value: string) {
  return value.replace(/[&<>"']/g, (char) => {
    switch (char) {
      case '&':
        return '&amp;';
      case '<':
        return '&lt;';
      case '>':
        return '&gt;';
      case '"':
        return '&quot;';
      case "'":
        return '&#39;';
      default:
        return char;
    }
  });
}

function renderPlainTextHtml(value: string) {
  return escapeHtml(value).split(/\r\n|\r|\n/).join('<br>');
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
  return DOMPurify.sanitize(html, SANITIZE_CONFIG);
}

function isPlainTextFallback(html: string, text: string) {
  return html.trim() === renderPlainTextHtml(text).trim();
}

type MessageBodyViewProps = {
  html?: string | null;
  text?: string | null;
  htmlTestId?: string;
  textTestId?: string;
  htmlClassName?: string;
  textClassName?: string;
};

export function MessageBodyView({ html, text, htmlTestId, textTestId, htmlClassName, textClassName }: MessageBodyViewProps) {
  const safeHtml = useMemo(() => {
    if (!html) {
      return null;
    }
    const sanitized = sanitizeMessageHtml(html);
    if (text && isPlainTextFallback(sanitized, text)) {
      return null;
    }
    return sanitized;
  }, [html, text]);

  if (safeHtml) {
    return (
      <div
        className={htmlClassName ?? 'message-html-body'}
        data-testid={htmlTestId}
        dangerouslySetInnerHTML={{ __html: safeHtml }}
      />
    );
  }

  return (
    <pre className={textClassName ?? 'message-text-body'} data-testid={textTestId}>
      {text || '这封邮件没有正文内容。'}
    </pre>
  );
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
        <MessageBodyView
          html={message.html_body}
          htmlTestId="message-html-body"
          htmlClassName="message-html-body"
          text={message.text_body}
          textTestId="message-text-body"
          textClassName="message-text-body"
        />
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
