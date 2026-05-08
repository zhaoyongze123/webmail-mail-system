import React, { useState, useEffect, FormEvent } from 'react';
import './styles.css';
import {
  fetchContacts,
  fetchFolders,
  fetchFolderMessages,
  fetchMessageDetail,
  searchFolderMessages,
  updateMessageOperation,
  moveMessages,
  deleteMessages,
  fetchSettings,
  login,
  logout,
  register,
  saveSettings
} from './mail/api';
import ComposePanel, { type ComposeValues } from './mail/ComposePanel';
import { sanitizeMessageHtml } from './mail/MessageReader';
import type { AuthCredentials, ContactItem, MailFolder, MailMessageSummary, MessageOperationAction, UserSettingsPreferences } from './mail/types';

const AUTO_REFRESH_MS = 30000;

export default function App() {
  const [folders, setFolders] = useState<MailFolder[]>([]);
  const [currentFolder, setCurrentFolder] = useState<string>('INBOX');
  const [messages, setMessages] = useState<MailMessageSummary[]>([]);
  const [query, setQuery] = useState('');
  const [activeQuery, setActiveQuery] = useState('');
  const [selectedMessage, setSelectedMessage] = useState<MailMessageSummary | null>(null);
  const [messageBody, setMessageBody] = useState<{ html: string | null; text: string }>({ html: null, text: '' });
  const [isLoadingMessages, setIsLoadingMessages] = useState(false);
  const [isAuthenticated, setIsAuthenticated] = useState(true);
  const [authMode, setAuthMode] = useState<'login' | 'register'>('login');
  const [authForm, setAuthForm] = useState<AuthCredentials>({ email: '', password: '', remember: false, display_name: '' });
  const [authError, setAuthError] = useState<string | null>(null);
  const [isSubmittingAuth, setIsSubmittingAuth] = useState(false);

  // Settings State
  const [showSettings, setShowSettings] = useState(false);
  const [showContacts, setShowContacts] = useState(false);
  const [contacts, setContacts] = useState<ContactItem[]>([]);
  const [contactQuery, setContactQuery] = useState('');
  const [contactsError, setContactsError] = useState<string | null>(null);
  const [preferences, setPreferences] = useState<UserSettingsPreferences>({ page_size: 30, mark_read_on_open: true });
  const [isSavingSettings, setIsSavingSettings] = useState(false);
  const [accountEmail, setAccountEmail] = useState('user@localhost');

  // Compose State
  const [isComposing, setIsComposing] = useState(false);
  const [composeInitialValues, setComposeInitialValues] = useState<ComposeValues | null>(null);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; message: MailMessageSummary } | null>(null);

  const handleApiError = (error: unknown) => {
    const typedError = error as Error & { code?: string };
    if (typedError.code === 'AUTH_SESSION_EXPIRED') {
      setIsAuthenticated(false);
      setAuthError('请先登录邮箱账号。');
    }
    return typedError.message || '请求失败';
  };

  // Load Folders & Settings
  useEffect(() => {
    fetchFolders().then((res) => {
      setIsAuthenticated(true);
      setFolders(res.folders || []);
      if (res.folders?.length) {
        const inbox = res.folders.find(f => f.name.toUpperCase() === 'INBOX');
        setCurrentFolder(inbox?.name || res.folders[0].name);
      }
    }).catch((error) => {
      const message = handleApiError(error);
      console.error(message);
    });

    fetchSettings().then((res) => {
      if (res.account?.email) setAccountEmail(res.account.email);
      if (res.preferences) setPreferences(res.preferences);
    }).catch((error) => {
      const message = handleApiError(error);
      console.error(message);
    });
  }, []);

  // Load Messages when folder, query or preferences changes
  const loadMessages = (options: { resetSelection?: boolean; refresh?: boolean } = {}) => {
    if (!currentFolder) return;
    setIsLoadingMessages(true);
    const loadOpts = { refresh: options.refresh ?? true, query: activeQuery, pageSize: preferences.page_size };

    const request = activeQuery.trim()
      ? searchFolderMessages(currentFolder, activeQuery.trim(), loadOpts)
      : fetchFolderMessages(currentFolder, loadOpts);

    request.then(res => {
      setMessages(res.messages || []);
      if (options.resetSelection !== false) {
        setSelectedMessage(null);
      } else if (selectedMessage) {
        const latest = (res.messages || []).find((item) => item.uid === selectedMessage.uid);
        if (latest) {
          setSelectedMessage(latest);
        }
      }
      setIsAuthenticated(true);
    }).catch((error) => {
      const message = handleApiError(error);
      console.error(message);
    }).finally(() => setIsLoadingMessages(false));
  };

  useEffect(() => {
    loadMessages();
  }, [currentFolder, activeQuery, preferences.page_size]);

  useEffect(() => {
    if (!isAuthenticated || !currentFolder) {
      return;
    }
    const timer = window.setInterval(() => {
      loadMessages({ resetSelection: false, refresh: true });
    }, AUTO_REFRESH_MS);
    return () => {
      window.clearInterval(timer);
    };
  }, [isAuthenticated, currentFolder, activeQuery, preferences.page_size, selectedMessage?.uid]);

  // Load specific message details when selected
  useEffect(() => {
    if (!selectedMessage) {
      setMessageBody({ html: null, text: '' });
      return;
    }

    let cancelled = false;
    fetchMessageDetail(currentFolder, selectedMessage.uid)
      .then(res => {
        if (cancelled) return;
        if (res) {
          setMessageBody({
            html: res.html_body ? sanitizeMessageHtml(res.html_body) : null,
            text: res.text_body || '此邮件暂无正文内容。',
          });
          // Optionally mark as read automatically per settings
          if (preferences.mark_read_on_open && !selectedMessage.read) {
             updateMessageOperation(currentFolder, { action: 'mark_read', uids: [selectedMessage.uid] }).then(() => {
                setMessages(msgs => msgs.map(m => m.uid === selectedMessage.uid ? { ...m, read: true } : m));
             });
          }
        }
      })
      .catch((e) => {
        if (!cancelled) setMessageBody({ html: null, text: '加载出错: ' + handleApiError(e) });
      });

    return () => {
      cancelled = true;
    };
  }, [selectedMessage, currentFolder, preferences.mark_read_on_open]);

  useEffect(() => {
    if (!showContacts) return;
    let cancelled = false;
    fetchContacts(contactQuery, 10)
      .then((res) => {
        if (!cancelled) {
          setContacts(res.contacts || []);
          setContactsError(null);
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setContactsError(handleApiError(error));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [showContacts, contactQuery]);

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

  const handleAuthSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setIsSubmittingAuth(true);
    setAuthError(null);
    try {
      const result = authMode === 'login' ? await login(authForm) : await register(authForm);
      setAccountEmail(result.email);
      setIsAuthenticated(true);
      setAuthForm({ email: '', password: '', remember: false, display_name: '' });
      await Promise.all([fetchFolders(), fetchSettings()]).then(([folderResult, settingsResult]) => {
        setFolders(folderResult.folders || []);
        const inbox = folderResult.folders?.find(f => f.name.toUpperCase() === 'INBOX');
        if (folderResult.folders?.length) setCurrentFolder(inbox?.name || folderResult.folders[0].name);
        if (settingsResult.account?.email) setAccountEmail(settingsResult.account.email);
        if (settingsResult.preferences) setPreferences(settingsResult.preferences);
      });
    } catch (error) {
      setAuthError((error as Error).message || '认证失败');
    } finally {
      setIsSubmittingAuth(false);
    }
  };

  const handleLogout = async () => {
    try {
      await logout();
    } catch (error) {
      console.error(error);
    }
    setIsAuthenticated(false);
    setMessages([]);
    setSelectedMessage(null);
  };

  const buildReplyQuote = (message: MailMessageSummary, body: { html: string | null; text: string }): ComposeValues => {
    const subject = message.subject?.startsWith('Re:') ? message.subject : `Re: ${message.subject || '(无主题)'}`;
    const escapedText = body.text.replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char] || char));
    const quoteHeader = `${message.sender?.email || ''} 写道：`;
    return {
      to: message.sender?.email ? [message.sender.email] : [],
      subject,
      text_body: `\n\n---- 原始邮件 ----\n发件人：${message.sender?.email || ''}\n主题：${message.subject || '(无主题)'}\n\n${body.text}`,
      html_body: `<p><br></p><blockquote><p>${quoteHeader}</p>${body.html || `<p>${escapedText.replace(/\n/g, '<br>')}</p>`}</blockquote>`,
    };
  };

  const openCompose = (initialValues: ComposeValues | null = null) => {
    setComposeInitialValues(initialValues);
    setIsComposing(true);
  };

  const replyWithQuote = async (message: MailMessageSummary) => {
    setContextMenu(null);
    setSelectedMessage(message);
    if (selectedMessage?.uid === message.uid && (messageBody.html || messageBody.text)) {
      setComposeInitialValues(buildReplyQuote(message, messageBody));
      setIsComposing(true);
      return;
    }
    try {
      const detail = await fetchMessageDetail(currentFolder, message.uid);
      const body = {
        html: detail.html_body ? sanitizeMessageHtml(detail.html_body) : null,
        text: detail.text_body || '此邮件暂无正文内容。',
      };
      setMessageBody(body);
      setComposeInitialValues(buildReplyQuote(message, body));
      setIsComposing(true);
    } catch (error) {
      setComposeInitialValues(buildReplyQuote(message, { html: null, text: '' }));
      setIsComposing(true);
      handleApiError(error);
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

  if (!isAuthenticated) {
    return (
      <div className="auth-page">
        <form className="auth-card" onSubmit={handleAuthSubmit}>
          <h1>{authMode === 'login' ? '登录邮箱' : '注册邮箱'}</h1>
          <label>
            邮箱
            <input
              type="email"
              value={authForm.email}
              onChange={(event) => setAuthForm((current) => ({ ...current, email: event.target.value }))}
              required
            />
          </label>
          {authMode === 'register' ? (
            <label>
              显示名
              <input
                value={authForm.display_name || ''}
                onChange={(event) => setAuthForm((current) => ({ ...current, display_name: event.target.value }))}
              />
            </label>
          ) : null}
          <label>
            密码
            <input
              type="password"
              value={authForm.password}
              onChange={(event) => setAuthForm((current) => ({ ...current, password: event.target.value }))}
              required
            />
          </label>
          <label className="auth-check">
            <input
              type="checkbox"
              checked={Boolean(authForm.remember)}
              onChange={(event) => setAuthForm((current) => ({ ...current, remember: event.target.checked }))}
            />
            记住登录
          </label>
          {authError ? <div className="auth-error" role="alert">{authError}</div> : null}
          <button className="auth-submit" disabled={isSubmittingAuth}>{isSubmittingAuth ? '处理中...' : authMode === 'login' ? '登录' : '注册并登录'}</button>
          <button
            type="button"
            className="auth-switch"
            onClick={() => {
              setAuthMode(authMode === 'login' ? 'register' : 'login');
              setAuthError(null);
            }}
          >
            {authMode === 'login' ? '创建新账号' : '已有账号，去登录'}
          </button>
        </form>
      </div>
    );
  }

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
          <button className="compose-btn" onClick={() => openCompose()}>
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
          <div className="footer-item" onClick={() => setShowContacts(true)}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"></path>
              <circle cx="9" cy="7" r="4"></circle>
              <path d="M22 21v-2a4 4 0 0 0-3-3.87"></path>
              <path d="M16 3.13a4 4 0 0 1 0 7.75"></path>
            </svg>
            联系人
          </div>
          <div className="footer-item" onClick={() => setShowSettings(true)}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="3"></circle>
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
            </svg>
            系统设置
          </div>
          <div className="footer-item" onClick={handleLogout}>退出登录</div>
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
             <button className="action-btn" onClick={() => loadMessages()}>刷新列表</button>
             <span className="auto-refresh-label">每 30 秒自动刷新</span>
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
                  onContextMenu={(event) => {
                    event.preventDefault();
                    setContextMenu({ x: event.clientX, y: event.clientY, message: msg });
                  }}
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
                {messageBody.html ? (
                  <div
                    className="reading-html-body"
                    data-testid="app-message-html-body"
                    dangerouslySetInnerHTML={{ __html: messageBody.html }}
                  />
                ) : (
                  <pre className="reading-text-body" data-testid="app-message-text-body">
                    {messageBody.text || '正在加载正文...'}
                  </pre>
                )}
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

      {showContacts && (
        <div className="settings-modal-overlay">
          <div className="settings-modal contacts-modal">
             <h2>联系人</h2>
             <input
               className="contacts-search"
               placeholder="搜索联系人"
               value={contactQuery}
               onChange={(event) => setContactQuery(event.target.value)}
             />
             {contactsError ? <div className="auth-error" role="alert">{contactsError}</div> : null}
             <ul className="contacts-list" aria-label="联系人列表">
               {contacts.map((contact) => (
                 <li key={contact.email}>
                   <button type="button" onClick={() => { setShowContacts(false); openCompose({ to: [contact.email] }); }}>{contact.email}</button>
                 </li>
               ))}
               {!contacts.length ? <li className="contacts-empty">暂无联系人</li> : null}
             </ul>
             <div className="settings-actions">
                <button onClick={() => setShowContacts(false)}>关闭</button>
             </div>
          </div>
        </div>
      )}

      {contextMenu ? (
        <div className="message-context-menu" style={{ left: contextMenu.x, top: contextMenu.y }} onMouseLeave={() => setContextMenu(null)}>
          <button type="button" onClick={() => replyWithQuote(contextMenu.message)}>回复并引用</button>
        </div>
      ) : null}

      {/* Compose Component (Native fixed positioning) */}
      <ComposePanel
        open={isComposing}
        initialValues={composeInitialValues}
        from={accountEmail}
        onClose={() => { setIsComposing(false); setComposeInitialValues(null); }}
        onSent={() => { loadMessages(); setIsComposing(false); }}
      />
    </div>
  );
}
