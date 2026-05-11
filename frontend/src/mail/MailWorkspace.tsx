import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import {
  deleteMessages,
  fetchFolderMessages,
  fetchFolders,
  moveMessages,
  searchFolderMessages,
  updateMessageOperation,
} from './api';
import type { MailFolder, MailMessageSummary, MailWorkspaceProps, MessageOperationAction } from './types';

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
  align-items: stretch;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.mail-workspace__toolbar-row,
.mail-workspace__search,
.mail-workspace__bulk-actions {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.mail-workspace__toolbar-row {
  justify-content: space-between;
}

.mail-workspace__search {
  width: 100%;
}

.mail-workspace__search-field {
  flex: 1 1 320px;
  min-width: 0;
}

.mail-workspace__search-field input,
.mail-workspace__bulk-actions select {
  width: 100%;
  min-width: 0;
  border: 1px solid #c7d2e3;
  border-radius: 8px;
  color: #17304d;
  padding: 10px 12px;
}

.mail-workspace__current-folder {
  color: #4b5563;
  font-size: 0.95rem;
}

.mail-workspace__search-state {
  color: #52657d;
  font-size: 0.92rem;
}

.mail-workspace__bulk-actions {
  border: 1px solid #d9dee8;
  border-radius: 8px;
  background: #fbfcfe;
  padding: 10px 12px;
}

.mail-workspace__bulk-actions strong {
  white-space: nowrap;
}

.mail-workspace__bulk-actions label {
  align-items: center;
  display: flex;
  gap: 8px;
}

.mail-workspace__bulk-actions select {
  min-width: 160px;
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

.mail-workspace__message-row {
  align-items: stretch;
  border-bottom: 1px solid #edf0f5;
  display: grid;
  gap: 8px;
  grid-template-columns: auto minmax(0, 1fr);
  padding: 12px 12px 12px 16px;
}

.mail-workspace__message-checkbox {
  align-self: start;
  margin-top: 11px;
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
  display: grid;
  gap: 6px;
  min-height: 92px;
  padding: 0;
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

.mail-workspace__message-row.is-selected {
  background: #f0f7ff;
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
  .mail-workspace__toolbar-row {
    align-items: flex-start;
    flex-direction: column;
  }

  .mail-workspace__search-field,
  .mail-workspace__search button {
    width: 100%;
  }

  .mail-workspace__bulk-actions {
    align-items: flex-start;
    width: 100%;
  }

  .mail-workspace__bulk-actions select {
    min-width: 0;
    width: 100%;
  }

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

function pickDefaultFolder(folders: MailFolder[], currentFolder: string) {
  return (
    folders.find((folder) => folder.name !== currentFolder && folder.name !== '.Trash')?.name ??
    folders.find((folder) => folder.name !== currentFolder)?.name ??
    folders[0]?.name ??
    DEFAULT_FOLDER
  );
}

export default function MailWorkspace({
  onOpenMessage,
  selectedMessageKey,
  renderReader,
  onCompose,
  onOpenSettings,
}: MailWorkspaceProps) {
  const [folders, setFolders] = useState<MailFolder[]>([]);
  const [currentFolder, setCurrentFolder] = useState(DEFAULT_FOLDER);
  const [messages, setMessages] = useState<MailMessageSummary[]>([]);
  const [query, setQuery] = useState('');
  const [activeQuery, setActiveQuery] = useState('');
  const [selectedUids, setSelectedUids] = useState<string[]>([]);
  const [bulkAction, setBulkAction] = useState<MessageOperationAction>('mark_read');
  const [targetFolder, setTargetFolder] = useState('');
  const [folderState, setFolderState] = useState<LoadState>('idle');
  const [messageState, setMessageState] = useState<LoadState>('idle');
  const [actionState, setActionState] = useState<'idle' | 'loading' | 'error'>('idle');
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const requestIdRef = useRef(0);

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
        setTargetFolder((current) => current || pickDefaultFolder(payload.folders, currentFolder));
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

  const loadMessages = useCallback(async (folder: string, options: { refresh?: boolean; query?: string } = {}) => {
    const requestId = ++requestIdRef.current;
    setMessageState('loading');
    setError(null);
    setInfo(null);
    try {
      const payload = options.query?.trim()
        ? await searchFolderMessages(folder, options.query.trim(), { refresh: options.refresh })
        : await fetchFolderMessages(folder, { refresh: options.refresh });
      if (requestId !== requestIdRef.current) {
        return;
      }
      setMessages(payload.messages);
      setMessageState('ready');
      setActiveQuery(options.query?.trim() ?? '');
      setSelectedUids([]);
    } catch (loadError) {
      if (requestId !== requestIdRef.current) {
        return;
      }
      setMessageState('error');
      setError(loadError instanceof Error ? loadError.message : '加载邮件列表失败');
    }
  }, []);

  useEffect(() => {
    loadMessages(currentFolder, { query: activeQuery });
  }, [currentFolder, loadMessages]);

  useEffect(() => {
    if (!folders.length) {
      return;
    }
    setTargetFolder((current) => current || pickDefaultFolder(folders, currentFolder));
  }, [currentFolder, folders]);

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

  const allVisibleSelected = messages.length > 0 && selectedUids.length === messages.length;

  const toggleSelectAll = (checked: boolean) => {
    if (checked) {
      setSelectedUids(messages.map((message) => message.uid));
      return;
    }
    setSelectedUids([]);
  };

  const submitSearch = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    await loadMessages(currentFolder, { query });
  };

  const clearSearch = async () => {
    setQuery('');
    setActiveQuery('');
    await loadMessages(currentFolder, { refresh: true });
  };

  const runOperation = async (action: MessageOperationAction, uids: string[], nextTargetFolder?: string) => {
    setActionState('loading');
    setError(null);
    setInfo(null);
    try {
      if (action === 'move') {
        if (!nextTargetFolder) {
          throw new Error('请选择目标文件夹');
        }
        await moveMessages(currentFolder, uids, nextTargetFolder);
      } else if (action === 'delete') {
        await deleteMessages(currentFolder, uids);
      } else {
        await updateMessageOperation(currentFolder, { action, uids });
      }
      setSelectedUids([]);
      await loadMessages(currentFolder, { refresh: true, query: activeQuery });
      setInfo(
        action === 'delete'
          ? `已删除 ${uids.length} 封邮件`
          : action === 'move'
            ? `已移动 ${uids.length} 封邮件`
            : `已更新 ${uids.length} 封邮件`,
      );
    } catch (operationError) {
      setActionState('error');
      setError(operationError instanceof Error ? operationError.message : '邮件操作失败');
    } finally {
      setActionState((current) => (current === 'loading' ? 'idle' : current));
    }
  };

  const submitBulkAction = async () => {
    if (!selectedUids.length) {
      setError('请先选择邮件');
      return;
    }
    await runOperation(bulkAction, selectedUids, targetFolder);
  };

  return (
    <section className="mail-workspace" aria-label="邮件工作台">
      <style>{workspaceStyles}</style>
      <div className="mail-workspace__toolbar">
        <div className="mail-workspace__toolbar-row" role="toolbar" aria-label="邮件工具栏">
          <div className="mail-workspace__current-folder" aria-live="polite">
            当前文件夹：{folders.find((folder) => folder.name === currentFolder)?.display_name ?? currentFolder}
          </div>
          <div className="mail-workspace__search-state">{activeQuery ? `搜索：${activeQuery}` : '当前显示全部邮件'}</div>
          <div className="mail-workspace__toolbar-row">
            <button type="button" className="primary-button" onClick={onCompose}>
              新信
            </button>
            <button
              type="button"
              className="secondary-button"
              onClick={() => loadMessages(currentFolder, { refresh: true, query: activeQuery })}
              disabled={messageState === 'loading'}
            >
              {messageState === 'loading' ? '刷新中...' : '刷新'}
            </button>
            <button type="button" className="secondary-button" onClick={onOpenSettings}>
              设置偏好
            </button>
          </div>
        </div>
        <form className="mail-workspace__search" onSubmit={submitSearch}>
          <label className="mail-workspace__search-field">
            <span className="muted">搜索当前文件夹</span>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="按主题、发件人或正文搜索"
              aria-label="搜索当前文件夹"
            />
          </label>
          <button type="submit" className="primary-button" disabled={messageState === 'loading'}>
            搜索
          </button>
          <button type="button" className="secondary-button" onClick={clearSearch} disabled={!query && !activeQuery}>
            清空搜索
          </button>
        </form>
        <div className="mail-workspace__bulk-actions" aria-label="批量操作">
          <strong>批量操作</strong>
          <label>
            <input type="checkbox" checked={allVisibleSelected} onChange={(event) => toggleSelectAll(event.target.checked)} />
            <span>全选当前页</span>
          </label>
          <span className="muted">已选 {selectedUids.length} 封</span>
          <select value={bulkAction} onChange={(event) => setBulkAction(event.target.value as MessageOperationAction)} aria-label="批量操作类型">
            <option value="mark_read">标记已读</option>
            <option value="mark_unread">标记未读</option>
            <option value="delete">删除</option>
            <option value="move">移动</option>
          </select>
          {bulkAction === 'move' ? (
            <select value={targetFolder} onChange={(event) => setTargetFolder(event.target.value)} aria-label="目标文件夹">
              {folders
                .filter((folder) => folder.name !== currentFolder)
                .map((folder) => (
                  <option key={folder.name} value={folder.name}>
                    {folder.display_name}
                  </option>
                ))}
            </select>
          ) : null}
          <button
            type="button"
            className="primary-button"
            onClick={submitBulkAction}
            disabled={!selectedUids.length || actionState === 'loading' || (bulkAction === 'move' && !targetFolder)}
          >
            执行
          </button>
        </div>
      </div>

      {error ? (
        <div className="notice notice-error" role="alert">
          {error}
        </div>
      ) : null}
      {info ? (
        <div className="notice" role="status">
          {info}
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
                const isSelected = selectedUids.includes(message.uid);
                return (
                  <li key={key}>
                    <div className={isSelected ? 'mail-workspace__message-row is-selected' : 'mail-workspace__message-row'}>
                      <input
                        className="mail-workspace__message-checkbox"
                        type="checkbox"
                        aria-label={`选择邮件 ${message.subject}`}
                        checked={isSelected}
                        onChange={(event) => {
                          setSelectedUids((current) =>
                            event.target.checked ? [...new Set([...current, message.uid])] : current.filter((uid) => uid !== message.uid),
                          );
                        }}
                      />
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
                    </div>
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
