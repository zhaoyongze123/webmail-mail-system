import { useCallback, useEffect, useMemo, useState } from 'react';
import { fetchFolderMessages, fetchFolders } from './api';
import type { MailFolder, MailMessageSummary, MailWorkspaceProps } from './types';

type LoadState = 'idle' | 'loading' | 'ready' | 'error';

const DEFAULT_FOLDER = 'INBOX';

const workspaceStyles = `
.mail-workspace {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: 0;
}

.mail-workspace__toolbar {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}

.mail-workspace__current-folder {
  color: #4b5563;
  font-size: 0.95rem;
}

.mail-workspace__layout {
  display: grid;
  gap: 16px;
  grid-template-columns: minmax(220px, 260px) minmax(420px, 520px) minmax(0, 1fr);
  min-height: 560px;
}

.mail-workspace__folders,
.mail-workspace__list,
.mail-workspace__reader {
  border: 1px solid #d9dee8;
  border-radius: 8px;
  min-width: 0;
  overflow: hidden;
}

.mail-workspace__folders,
.mail-workspace__list {
  background: #ffffff;
}

.mail-workspace__reader {
  background: #f8fafc;
}

.mail-workspace h2 {
  font-size: 1rem;
  margin: 0;
}

.mail-workspace__folders h2,
.mail-workspace__list-header,
.mail-workspace__reader-placeholder {
  padding: 16px;
}

.mail-workspace__folders ul,
.mail-workspace__messages {
  list-style: none;
  margin: 0;
  padding: 0;
}

.mail-workspace__folder,
.mail-workspace__message {
  background: transparent;
  border: 0;
  color: inherit;
  cursor: pointer;
  text-align: left;
  width: 100%;
}

.mail-workspace__folder {
  align-items: center;
  display: flex;
  justify-content: space-between;
  padding: 10px 16px;
}

.mail-workspace__folder.is-active {
  background: #e8f0fe;
  color: #1d4ed8;
  font-weight: 700;
}

.mail-workspace__list-header,
.mail-workspace__message-meta {
  align-items: center;
  display: flex;
  justify-content: space-between;
}

.mail-workspace__list-header {
  border-bottom: 1px solid #e5e7eb;
}

.mail-workspace__message {
  border-bottom: 1px solid #edf0f5;
  display: grid;
  gap: 6px;
  min-height: 92px;
  padding: 12px 16px;
}

.mail-workspace__message.is-active {
  background: #f0f7ff;
}

.mail-workspace__message-meta,
.mail-workspace__snippet {
  color: #64748b;
  font-size: 0.85rem;
}

.mail-workspace__subject,
.mail-workspace__snippet,
.mail-workspace__message-meta strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mail-workspace__subject.is-unread {
  font-weight: 700;
}

.mail-workspace__skeleton {
  display: grid;
  gap: 12px;
  padding: 16px;
}

.mail-workspace__skeleton span {
  background: linear-gradient(90deg, #eef2f7, #f8fafc, #eef2f7);
  border-radius: 6px;
  height: 72px;
}

.mail-workspace__empty {
  color: #64748b;
  padding: 32px 16px;
  text-align: center;
}

@media (max-width: 900px) {
  .mail-workspace__layout {
    grid-template-columns: 1fr;
    min-height: auto;
  }

  .mail-workspace__folders,
  .mail-workspace__list,
  .mail-workspace__reader {
    overflow: visible;
  }
}
`;

function messageKey(folder: string, uid: string) {
  return `${folder}:${uid}`;
}

function formatDate(value: string | null) {
  if (!value) {
    return '';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

function senderLabel(message: MailMessageSummary) {
  return message.sender.name || message.sender.email || '未知发件人';
}

export default function MailWorkspace({
  onOpenMessage,
  selectedMessageKey,
  renderReader,
  onCompose,
}: MailWorkspaceProps) {
  const [folders, setFolders] = useState<MailFolder[]>([]);
  const [currentFolder, setCurrentFolder] = useState(DEFAULT_FOLDER);
  const [messages, setMessages] = useState<MailMessageSummary[]>([]);
  const [folderState, setFolderState] = useState<LoadState>('idle');
  const [messageState, setMessageState] = useState<LoadState>('idle');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadFolders() {
      setFolderState('loading');
      setError(null);
      try {
        const payload = await fetchFolders();
        if (cancelled) {
          return;
        }
        setFolders(payload.folders);
        setFolderState('ready');
        setCurrentFolder((current) => {
          if (payload.folders.some((folder) => folder.name === current)) {
            return current;
          }
          return payload.folders[0]?.name ?? DEFAULT_FOLDER;
        });
      } catch (loadError) {
        if (cancelled) {
          return;
        }
        setFolderState('error');
        setError(loadError instanceof Error ? loadError.message : '加载文件夹失败');
      }
    }

    loadFolders();

    return () => {
      cancelled = true;
    };
  }, []);

  const loadMessages = useCallback(async (folder: string, refresh = false) => {
    setMessageState('loading');
    setError(null);
    try {
      const payload = await fetchFolderMessages(folder, { refresh });
      setMessages(payload.messages);
      setMessageState('ready');
    } catch (loadError) {
      setMessageState('error');
      setError(loadError instanceof Error ? loadError.message : '加载邮件列表失败');
    }
  }, []);

  useEffect(() => {
    loadMessages(currentFolder);
  }, [currentFolder, loadMessages]);

  const selectedMessage = useMemo(() => {
    if (!selectedMessageKey) {
      return null;
    }
    return messages.find((message) => messageKey(currentFolder, message.uid) === selectedMessageKey) ?? null;
  }, [currentFolder, messages, selectedMessageKey]);

  const readerContext =
    selectedMessageKey && selectedMessage
      ? {
          folder: currentFolder,
          uid: selectedMessage.uid,
          message: selectedMessage,
        }
      : null;

  return (
    <section className="mail-workspace" aria-label="邮件工作台">
      <style>{workspaceStyles}</style>
      <div className="mail-workspace__toolbar" role="toolbar" aria-label="邮件工具栏">
        <button type="button" className="primary-button" onClick={onCompose}>
          新信
        </button>
        <button
          type="button"
          className="secondary-button"
          onClick={() => loadMessages(currentFolder, true)}
          disabled={messageState === 'loading'}
        >
          {messageState === 'loading' ? '刷新中...' : '刷新'}
        </button>
        <span className="mail-workspace__current-folder" aria-live="polite">
          当前文件夹：{folders.find((folder) => folder.name === currentFolder)?.display_name ?? currentFolder}
        </span>
      </div>

      {error ? (
        <div className="notice notice-error" role="alert">
          {error}
        </div>
      ) : null}

      <div className="mail-workspace__layout">
        <nav className="mail-workspace__folders" aria-label="文件夹">
          <h2>文件夹</h2>
          {folderState === 'loading' ? <p className="muted">正在加载文件夹...</p> : null}
          <ul>
            {folders.map((folder) => (
              <li key={folder.name}>
                <button
                  type="button"
                  className={folder.name === currentFolder ? 'mail-workspace__folder is-active' : 'mail-workspace__folder'}
                  aria-current={folder.name === currentFolder ? 'page' : undefined}
                  onClick={() => setCurrentFolder(folder.name)}
                >
                  <span>{folder.display_name}</span>
                  <span aria-label={`${folder.display_name} 未读 ${folder.unread_count} 封`}>
                    {folder.unread_count > 0 ? folder.unread_count : folder.total_count}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </nav>

        <section className="mail-workspace__list" aria-label="邮件列表">
          <div className="mail-workspace__list-header">
            <h2>邮件列表</h2>
            <span>{messages.length} 封</span>
          </div>
          {messageState === 'loading' ? (
            <div className="mail-workspace__skeleton" role="status" aria-label="邮件列表加载中">
              <span />
              <span />
              <span />
            </div>
          ) : null}
          {messageState === 'ready' && messages.length === 0 ? (
            <div className="mail-workspace__empty" role="status">
              当前文件夹暂无邮件
            </div>
          ) : null}
          {messageState === 'ready' && messages.length > 0 ? (
            <ul className="mail-workspace__messages">
              {messages.map((message) => {
                const key = messageKey(currentFolder, message.uid);
                return (
                  <li key={key}>
                    <button
                      type="button"
                      className={key === selectedMessageKey ? 'mail-workspace__message is-active' : 'mail-workspace__message'}
                      aria-pressed={key === selectedMessageKey}
                      onClick={() => onOpenMessage(message.uid, currentFolder)}
                    >
                      <span className="mail-workspace__message-meta">
                        <strong>{senderLabel(message)}</strong>
                        <time dateTime={message.date ?? undefined}>{formatDate(message.date)}</time>
                      </span>
                      <span className={message.read ? 'mail-workspace__subject' : 'mail-workspace__subject is-unread'}>
                        {message.has_attachments ? '附件 ' : ''}
                        {message.subject}
                      </span>
                      <span className="mail-workspace__snippet">{message.snippet}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : null}
        </section>

        <aside className="mail-workspace__reader" aria-label="阅读区">
          {renderReader ? (
            renderReader(readerContext)
          ) : (
            <div className="mail-workspace__reader-placeholder">
              <h2>阅读区</h2>
              <p>选择一封邮件后在这里阅读正文。</p>
            </div>
          )}
        </aside>
      </div>
    </section>
  );
}
