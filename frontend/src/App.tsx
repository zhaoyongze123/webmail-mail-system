import React, { useState, useEffect, FormEvent } from 'react';
import './styles.css';
import {
  fetchFolders,
  fetchFolderMessages,
  searchFolderMessages,
  updateMessageOperation,
  moveMessages,
  deleteMessages,
  fetchSettings,
  saveSettings
} from './mail/api';
import ComposePanel from './mail/ComposePanel';
import type { MailFolder, MailMessageSummary, MessageOperationAction, UserSettingsPreferences } from './mail/types';

export default function App() {
  const [folders, setFolders] = useState<MailFolder[]>([]);
  const [currentFolder, setCurrentFolder] = useState<string>('INBOX');
  const [messages, setMessages] = useState<MailMessageSummary[]>([]);
  const [query, setQuery] = useState('');
  const [activeQuery, setActiveQuery] = useState('');
  const [selectedMessage, setSelectedMessage] = useState<MailMessageSummary | null>(null);
  const [messageBody, setMessageBody] = useState<string>('');
  const [isLoadingMessages, setIsLoadingMessages] = useState(false);

  // Settings State
  const [showSettings, setShowSettings] = useState(false);
  const [preferences, setPreferences] = useState<UserSettingsPreferences>({ page_size: 30, mark_read_on_open: true });
  const [isSavingSettings, setIsSavingSettings] = useState(false);
  const [accountEmail, setAccountEmail] = useState('user@localhost');

  // Compose State
  const [isComposing, setIsComposing] = useState(false);

  // Load Folders & Settings
  useEffect(() => {
    fetchFolders().then((res) => {
      setFolders(res.folders || []);
      if (res.folders?.length) {
        const inbox = res.folders.find(f => f.name.toUpperCase() === 'INBOX');
        setCurrentFolder(inbox?.name || res.folders[0].name);
      }
    }).catch(console.error);

    fetchSettings().then((res) => {
      if (res.account?.email) setAccountEmail(res.account.email);
      if (res.preferences) setPreferences(res.preferences);
    }).catch(console.error);
  }, []);

  // Load Messages when folder, query or preferences changes
  const loadMessages = () => {
    if (!currentFolder) return;
    setIsLoadingMessages(true);
    const loadOpts = { refresh: true, query: activeQuery, pageSize: preferences.page_size };

    const request = activeQuery.trim()
      ? searchFolderMessages(currentFolder, activeQuery.trim(), loadOpts)
      : fetchFolderMessages(currentFolder, loadOpts);

    request.then(res => {
      setMessages(res.messages || []);
      setSelectedMessage(null); // Reset selection on refresh
    }).catch(console.error).finally(() => setIsLoadingMessages(false));
  };

  useEffect(() => {
    loadMessages();
  }, [currentFolder, activeQuery, preferences.page_size]);

  // Load specific message details when selected
  useEffect(() => {
    if (!selectedMessage) {
      setMessageBody('');
      return;
    }

    let cancelled = false;
    fetch(`/api/folders/${encodeURIComponent(currentFolder)}/messages/${encodeURIComponent(selectedMessage.uid)}`)
      .then(res => res.json())
      .then(res => {
        if (cancelled) return;
        if (res.success && res.data) {
          setMessageBody(res.data.text_body || res.data.html_body || '此邮件暂无正文内容。');
          // Optionally mark as read automatically per settings
          if (preferences.mark_read_on_open && !selectedMessage.read) {
             updateMessageOperation(currentFolder, { action: 'mark_read', uids: [selectedMessage.uid] }).then(() => {
                setMessages(msgs => msgs.map(m => m.uid === selectedMessage.uid ? { ...m, read: true } : m));
             });
          }
        } else {
          setMessageBody('加载邮件正文失败。');
        }
      })
      .catch((e) => {
        if (!cancelled) setMessageBody('加载出错: ' + e.message);
      });

    return () => {
      cancelled = true;
    };
  }, [selectedMessage, currentFolder]);

  const doSearch = (e: FormEvent) => {
    e.preventDefault();
    setActiveQuery(query);
  };

  const handleClearSearch = () => {
    setQuery('');
    setActiveQuery('');
  };

  const handleMsgAction = async (action: MessageOperationAction | 'hard_delete') => {
    if (!selectedMessage) return;
    try {
      if (action === 'hard_delete') {
         await deleteMessages(currentFolder, [selectedMessage.uid]);
      } else {
         await updateMessageOperation(currentFolder, { action, uids: [selectedMessage.uid] });
      }
      loadMessages();
      if (action === 'delete' || action === 'hard_delete') setSelectedMessage(null);
    } catch (e) {
      console.error(e);
    }
  };

  const handleMove = async (targetFolder: string) => {
    if (!selectedMessage || !targetFolder) return;
    try {
      await moveMessages(currentFolder, [selectedMessage.uid], targetFolder);
      loadMessages();
      setSelectedMessage(null);
    } catch(e) {
      console.error(e);
    }
  };

  const handleSaveSettings = async () => {
    setIsSavingSettings(true);
    try {
      const res = await saveSettings({ ...preferences });
      if (res.preferences) setPreferences(res.preferences);
      setShowSettings(false);
    } catch (e) {
      console.error(e);
    } finally {
      setIsSavingSettings(false);
    }
  };

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return '';
    try {
      return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(dateStr));
    } catch {
      return dateStr;
    }
  };

  return (
    <div className="app-container">
      {/* Sidebar */}
      <aside className="sidebar">
        <div className="sidebar-header">
          <div className="account-info">
            <div className="account-avatar">{accountEmail.charAt(0).toUpperCase()}</div>
            <div className="account-text">
              <span className="account-name">User</span>
              <span className="account-email">{accountEmail}</span>
            </div>
          </div>
        </div>

        <div className="compose-btn-container">
          <button className="compose-btn" onClick={() => setIsComposing(true)}>
            <svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor">
              <path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a.996.996 0 0 0 0-1.41l-2.34-2.34a.996.996 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"></path>
            </svg>
            写邮件
          </button>
        </div>

        <form className="search-box" onSubmit={doSearch}>
          <div className="search-input-wrapper">
            <svg className="search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="11" cy="11" r="8"></circle>
              <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
            </svg>
            <input
              type="search"
              className="search-input"
              placeholder="搜索邮件..."
              value={query}
              onChange={e => setQuery(e.target.value)}
            />
            {query && (
               <button
                 type="button"
                 onClick={handleClearSearch}
                 style={{ position: 'absolute', right: 8, border: 'none', background: 'transparent', cursor: 'pointer', color: '#999' }}
               >
                 ×
               </button>
            )}
          </div>
        </form>

        <div className="nav-section">
          <div className="nav-group">
            <div className="nav-title">文件夹</div>
            <ul>
              {folders.map(folder => (
                <li
                  key={folder.name}
                  className={`nav-item ${currentFolder === folder.name ? 'active' : ''}`}
                  onClick={() => {
                    setCurrentFolder(folder.name);
                    setActiveQuery('');
                    setQuery('');
                  }}
                >
                  <div className="nav-item-left">
                    <svg className="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path>
                    </svg>
                    <span>{folder.display_name || folder.name}</span>
                  </div>
                  {folder.unread_count > 0 && <span className="badge">{folder.unread_count}</span>}
                </li>
              ))}
            </ul>
          </div>
        </div>

        <div className="sidebar-footer">
          <div className="footer-item" onClick={() => setShowSettings(true)}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="3"></circle>
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
            </svg>
            系统设置
          </div>
        </div>
      </aside>

      {/* Main Content */}
      <main className="main-content">
        <header className="topbar">
          <div className="topbar-left">
            <div className="topbar-title">
              {folders.find(f => f.name === currentFolder)?.display_name || currentFolder}
              {activeQuery && <span style={{fontSize: '14px', color: '#666', fontWeight: 400, marginLeft: '12px'}}>搜索: {activeQuery}</span>}
            </div>
          </div>
          <div className="topbar-right">
             <button className="action-btn" onClick={loadMessages}>刷新列表</button>
          </div>
        </header>

        <div className="content-row">
          {/* Message List */}
          <div className="message-list-container">
            {isLoadingMessages ? (
               <div style={{ padding: '24px', color: '#666' }}>正在加载邮件...</div>
            ) : messages.length === 0 ? (
               <div style={{ padding: '24px', color: '#666' }}>当前文件夹暂无邮件。</div>
            ) : (
               messages.map(msg => (
                <div
                  key={msg.uid}
                  className="message-row"
                  style={{ background: selectedMessage?.uid === msg.uid ? '#eaf1fb' : '' }}
                  onClick={() => setSelectedMessage(msg)}
                >
                  {!msg.read ? <div className="unread-dot"></div> : <div className="read-dot-placeholder"></div>}
                  <div className="sender-name">{msg.sender?.name || msg.sender?.email || 'Unknown'}</div>
                  <div className="message-subject">{msg.subject || '(无主题)'}</div>
                  <div className="message-preview">{msg.snippet}</div>
                  <div className="message-time">{formatDate(msg.date)}</div>
                </div>
              ))
            )}
          </div>

          {/* Reading Pane */}
          {selectedMessage && (
            <div className="reading-pane">
              <div className="reading-header">
                <div className="reading-header-top">
                  <select
                    title="移动到"
                    className="minmax-btn"
                    value=""
                    onChange={e => handleMove(e.target.value)}
                    style={{ marginRight: '8px' }}
                  >
                    <option value="" disabled>移动到...</option>
                    {folders.filter(f => f.name !== currentFolder).map(f => (
                      <option key={f.name} value={f.name}>{f.display_name}</option>
                    ))}
                  </select>
                  <button className="minmax-btn" onClick={() => handleMsgAction(selectedMessage.read ? 'mark_unread' : 'mark_read')} title="标记已读/未读">
                    {selectedMessage.read ? '标为未读' : '标为已读'}
                  </button>
                  <button className="minmax-btn" onClick={() => handleMsgAction('delete')} title="移到回收站 (操作)" style={{ marginLeft: '8px' }}>
                    <span style={{fontSize: '14px', marginRight: '4px'}}>🗑</span> 删除
                  </button>
                  <button className="minmax-btn" onClick={() => handleMsgAction('hard_delete')} title="彻底删除" style={{ marginLeft: '8px' }}>
                    <span style={{fontSize: '14px', marginRight: '4px'}}>⚠</span> 彻底删除
                  </button>
                  <button className="minmax-btn" onClick={() => setSelectedMessage(null)} title="关闭" style={{ border: 'none', marginLeft: 'auto' }}>
                    <span style={{fontSize: '20px'}}>×</span>
                  </button>
                </div>
                <div className="reading-field">
                  <div className="field-label">发件人</div>
                  <div className="field-value">{selectedMessage.sender?.name} <span>&lt;{selectedMessage.sender?.email}&gt;</span></div>
                </div>
                {selectedMessage.to?.length ? (
                  <div className="reading-field">
                     <div className="field-label">收件人</div>
                     <div className="field-value">{selectedMessage.to.map(t => t.email).join(', ')}</div>
                  </div>
                ) : null}
                <div className="reading-field" style={{marginTop: '12px', paddingBottom: '12px'}}>
                  <div className="field-label" style={{color: '#222', fontSize: '18px', fontWeight: 600, width: '100%'}}>
                    {selectedMessage.subject || '(无主题)'}
                  </div>
                </div>
              </div>

              <div className="reading-body">
                {messageBody || '正在加载正文...'}
              </div>
            </div>
          )}
        </div>
      </main>

      {/* Settings Modal */}
      {showSettings && (
        <div className="settings-modal-overlay">
          <div className="settings-modal">
             <h2>系统设置</h2>
             <div className="settings-field">
                <label>每页显示邮件数</label>
                <select
                  value={preferences.page_size}
                  onChange={e => setPreferences({...preferences, page_size: Number(e.target.value)})}
                >
                  <option value={10}>10</option>
                  <option value={30}>30</option>
                  <option value={50}>50</option>
                  <option value={100}>100</option>
                </select>
             </div>
             <div className="settings-field" style={{ flexDirection: 'row', alignItems: 'center' }}>
                <input
                  type="checkbox"
                  id="markReadOnOpen"
                  checked={preferences.mark_read_on_open}
                  onChange={e => setPreferences({...preferences, mark_read_on_open: e.target.checked})}
                />
                <label htmlFor="markReadOnOpen">自动标记为已读 (打开时)</label>
             </div>
             <div className="settings-actions">
                <button onClick={() => setShowSettings(false)}>取消</button>
                <button className="primary" onClick={handleSaveSettings} disabled={isSavingSettings}>
                  {isSavingSettings ? '保存中...' : '保存'}
                </button>
             </div>
          </div>
        </div>
      )}

      {/* Compose Component (Native fixed positioning) */}
      <ComposePanel
        open={isComposing}
        from={accountEmail}
        onClose={() => setIsComposing(false)}
        onSent={() => { loadMessages(); setIsComposing(false); }}
      />
    </div>
  );
}
