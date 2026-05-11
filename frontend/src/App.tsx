import React, { useState, useEffect, useMemo, useRef, FormEvent } from 'react';
import './styles.css';
import {
  fetchContacts,
  createContact,
  updateContact,
  deleteContact,
  fetchFolders,
  createFolder,
  renameFolder,
  deleteFolder,
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
  saveSettings,
  uploadSettingsAvatar,
  changePassword,
  formatDateByTimezone,
} from './mail/api';
import ComposePanel, { type ComposeValues } from './mail/ComposePanel';
import { readComposeDraftCache } from './mail/composeDraftCache';
import SignatureSettings from './mail/SignatureSettings';
import { MessageBodyView, sanitizeMessageHtml } from './mail/MessageReader';
import type {
  AuthCredentials,
  ContactItem,
  ContactUpsertPayload,
  MailFolder,
  MailMessageSummary,
  MessageOperationAction,
  SystemSettingsPreferences,
  UserSettingsPreferences,
  MessageAttachment,
} from './mail/types';

export default function App() {
  const [folders, setFolders] = useState<MailFolder[]>([]);
  const [currentFolder, setCurrentFolder] = useState<string>('INBOX');
  const [messages, setMessages] = useState<MailMessageSummary[]>([]);
  const [messagePage, setMessagePage] = useState(1);
  const [messageTotal, setMessageTotal] = useState(0);
  const [searchDraft, setSearchDraft] = useState({
    query: '',
    sender: '',
    dateFrom: '',
    dateTo: '',
    hasAttachments: false,
  });
  const [activeSearch, setActiveSearch] = useState({
    query: '',
    sender: '',
    dateFrom: '',
    dateTo: '',
    hasAttachments: false,
  });
  const [selectedMessage, setSelectedMessage] = useState<MailMessageSummary | null>(null);
  const [selectedMessageUids, setSelectedMessageUids] = useState<string[]>([]);
  const [showSearchFilters, setShowSearchFilters] = useState(false);
  const [messageBody, setMessageBody] = useState<{
    html: string | null;
    text: string;
    attachments: MessageAttachment[];
  }>({ html: null, text: '', attachments: [] });
  const [isLoadingMessages, setIsLoadingMessages] = useState(false);
  const [isAuthenticated, setIsAuthenticated] = useState(true);
  const [authMode, setAuthMode] = useState<'login' | 'register'>('login');
  const [authForm, setAuthForm] = useState<AuthCredentials>({ email: '', password: '', remember: false, display_name: '' });
  const [authError, setAuthError] = useState<string | null>(null);
  const [isSubmittingAuth, setIsSubmittingAuth] = useState(false);

  // Settings State
  const [showSettings, setShowSettings] = useState(false);
  const [showFolderManager, setShowFolderManager] = useState(false);
  const [showSignatures, setShowSignatures] = useState(false);
  const [showContacts, setShowContacts] = useState(false);
  const [contacts, setContacts] = useState<ContactItem[]>([]);
  const [contactQuery, setContactQuery] = useState('');
  const [contactPage, setContactPage] = useState(1);
  const [contactPageSize] = useState(8);
  const [contactGroupFilter, setContactGroupFilter] = useState('');
  const [contactTagFilter, setContactTagFilter] = useState('');
  const [contactsError, setContactsError] = useState<string | null>(null);
  const [contactsTotal, setContactsTotal] = useState(0);
  const [contactDraft, setContactDraft] = useState<ContactUpsertPayload>({ name: '', email: '', phone: '', note: '', groups: [], tags: [] });
  const [contactEditorMode, setContactEditorMode] = useState<'create' | 'edit'>('create');
  const [editingContactId, setEditingContactId] = useState<string | null>(null);
  const [contactSaving, setContactSaving] = useState(false);
  const [contactDeletingId, setContactDeletingId] = useState<string | null>(null);
  const [folderForm, setFolderForm] = useState({ createName: '', renameTarget: '', renameName: '' });
  const [folderActionError, setFolderActionError] = useState<string | null>(null);
  const [folderActionSuccess, setFolderActionSuccess] = useState<string | null>(null);
  const [folderActionLoading, setFolderActionLoading] = useState(false);
  const [preferences, setPreferences] = useState<UserSettingsPreferences>({
    system: {
      page_size: 30,
      mark_read_on_open: true,
      language: 'zh-CN',
      timezone: 'Asia/Shanghai',
      reply_quote_position: 'bottom',
    },
    user: {
      display_name: '',
      profile_title: '',
      avatar_url: '',
      bio: '',
    },
    theme: {
      mode: 'light',
    },
  });
  const [isSavingSettings, setIsSavingSettings] = useState(false);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [isUploadingAvatar, setIsUploadingAvatar] = useState(false);
  const [passwordForm, setPasswordForm] = useState({ current_password: '', new_password: '', confirm_password: '' });
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordSuccess, setPasswordSuccess] = useState<string | null>(null);
  const [isChangingPassword, setIsChangingPassword] = useState(false);
  const [accountEmail, setAccountEmail] = useState('user@localhost');
  const [hasAccountContext, setHasAccountContext] = useState(false);
  const [isInitialDataReady, setIsInitialDataReady] = useState(false);

  // Compose State
  const [isComposing, setIsComposing] = useState(false);
  const [composeInitialValues, setComposeInitialValues] = useState<ComposeValues | null>(null);
  const [composeDraftId, setComposeDraftId] = useState<string | null>(null);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; message: MailMessageSummary } | null>(null);
  const suppressAutoMarkReadRef = useRef<string | null>(null);

  const normalizePreferences = (value: Partial<UserSettingsPreferences> | null | undefined): UserSettingsPreferences => ({
    system: {
      page_size: typeof value?.system?.page_size === 'number' ? value.system.page_size : 30,
      mark_read_on_open: typeof value?.system?.mark_read_on_open === 'boolean' ? value.system.mark_read_on_open : true,
      language: value?.system?.language || 'zh-CN',
      timezone: value?.system?.timezone || 'Asia/Shanghai',
      reply_quote_position: value?.system?.reply_quote_position === 'top' ? 'top' : 'bottom',
    },
    user: {
      display_name: value?.user?.display_name || '',
      profile_title: value?.user?.profile_title || '',
      avatar_url: value?.user?.avatar_url || '',
      bio: value?.user?.bio || '',
    },
    theme: {
      mode: value?.theme?.mode === 'dark' ? 'dark' : 'light',
    },
  });

  const updateSystemPreferences = (nextSystem: Partial<SystemSettingsPreferences>) => {
    setPreferences((current) => ({
      ...current,
      system: {
        ...current.system,
        ...nextSystem,
      },
    }));
  };

  const applyFolders = (nextFolders: MailFolder[]) => {
    setFolders(nextFolders);
    setCurrentFolder((currentFolderName) => {
      if (nextFolders.some((folder) => folder.name === currentFolderName)) {
        return currentFolderName;
      }
      const inbox = nextFolders.find((folder) => folder.name.toUpperCase() === 'INBOX');
      return inbox?.name || nextFolders[0]?.name || currentFolderName;
    });
  };

  const escapeHtml = (value: string) => value.replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char] || char));
  const escapeTextForHtml = (value: string) => escapeHtml(value).replace(/\r\n/g, '\n').replace(/\n/g, '<br>');

  const buildReplyQuoteMeta = (message: MailMessageSummary, body: { html: string | null; text: string }) => {
    const sentAt = formatDateByTimezone(message.date, {
      locale: 'zh-CN',
      timezone: preferences.system.timezone,
      dateStyle: 'medium',
      timeStyle: 'short',
    });
    const sender = message.sender?.name?.trim() ? `${message.sender.name.trim()} <${message.sender.email}>` : (message.sender?.email || '未提供');
    const recipients = (message.to || []).map((item) => (item.name?.trim() ? `${item.name.trim()} <${item.email}>` : item.email)).filter(Boolean).join('，') || '未提供';
    const subject = message.subject || '(无主题)';
    const metaText = [
      `发件人：${sender}`,
      `发送时间：${sentAt}`,
      `收件人：${recipients}`,
      `主题：${subject}`,
    ].join('\n');
    const metaHtml = [
      `<p><strong>发件人：</strong>${escapeHtml(sender)}</p>`,
      `<p><strong>发送时间：</strong>${escapeHtml(sentAt)}</p>`,
      `<p><strong>收件人：</strong>${escapeHtml(recipients)}</p>`,
      `<p><strong>主题：</strong>${escapeHtml(subject)}</p>`,
    ].join('');
    const quoteBodyHtml = body.html || `<p>${escapeTextForHtml(body.text || '')}</p>`;
    return { metaText, metaHtml, quoteBodyHtml };
  };

  const handleApiError = (error: unknown) => {
    const typedError = error as Error & { code?: string };
    if (typedError.code === 'AUTH_SESSION_EXPIRED') {
      setIsAuthenticated(false);
      setAuthError('请先登录邮箱账号。');
    }
    return typedError.message || '请求失败';
  };

  const buildAttachmentUrl = (folder: string, uid: string, attachmentId: string) => {
    return `/api/folders/${encodeURIComponent(folder)}/messages/${encodeURIComponent(uid)}/attachments/${encodeURIComponent(attachmentId)}`;
  };

  const attachmentId = (attachment: MessageAttachment) => attachment.attachment_id || attachment.id || '';

  const formatSize = (value?: number | null) => {
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
  };

  const contactStorageKey = (scope: string) => `webmail-contacts:${scope.trim().toLowerCase() || 'default'}`;
  const protectedFolderTypes = new Set(['inbox', 'sent', 'drafts', 'spam', 'trash', 'archive']);

  const splitContactValues = (value?: string | null) => {
    if (!value) return [];
    return value
      .split(/[,;，；\n]+/)
      .map((item) => item.trim())
      .filter(Boolean);
  };

  const joinContactValues = (values?: string[] | null) => {
    return (values || []).map((item) => item.trim()).filter(Boolean).join(', ');
  };

  const normalizeContactItem = (contact: Partial<ContactItem> & { email: string }): ContactItem => {
    const normalizedEmail = contact.email.trim().toLowerCase();
    return {
      id: contact.id ?? normalizedEmail,
      name: contact.name?.trim() || '',
      email: normalizedEmail,
      phone: contact.phone?.trim() || '',
      note: contact.note?.trim() || '',
      groups: (contact.groups || []).map((item) => item.trim()).filter(Boolean),
      tags: (contact.tags || []).map((item) => item.trim()).filter(Boolean),
      last_used_at: contact.last_used_at || null,
      created_at: contact.created_at || null,
      updated_at: contact.updated_at || contact.last_used_at || null,
      source: contact.source || 'manual',
    };
  };

  const serializeContactDraft = (contact?: ContactItem | null): ContactUpsertPayload => ({
    name: contact?.name || '',
    email: contact?.email || '',
    phone: contact?.phone || '',
    note: contact?.note || '',
    groups: contact?.groups || [],
    tags: contact?.tags || [],
  });

  const readContactCache = (scope: string): ContactItem[] => {
    if (typeof window === 'undefined') {
      return [];
    }
    try {
      const raw = window.localStorage.getItem(contactStorageKey(scope));
      if (!raw) return [];
      const parsed = JSON.parse(raw) as { contacts?: ContactItem[] };
      return (parsed.contacts || []).map((contact) => normalizeContactItem(contact));
    } catch {
      return [];
    }
  };

  const writeContactCache = (scope: string, nextContacts: ContactItem[]) => {
    if (typeof window === 'undefined') {
      return;
    }
    window.localStorage.setItem(contactStorageKey(scope), JSON.stringify({ contacts: nextContacts }));
  };

  const mergeContacts = (baseContacts: ContactItem[], nextContacts: ContactItem[]) => {
    const byEmail = new Map<string, ContactItem>();
    [...baseContacts, ...nextContacts].forEach((contact) => {
      const normalized = normalizeContactItem(contact);
      const existing = byEmail.get(normalized.email);
      if (!existing) {
        byEmail.set(normalized.email, normalized);
        return;
      }
      byEmail.set(normalized.email, {
        ...existing,
        ...normalized,
        name: normalized.name || existing.name,
        phone: normalized.phone || existing.phone,
        note: normalized.note || existing.note,
        groups: Array.from(new Set([...(existing.groups || []), ...(normalized.groups || [])])),
        tags: Array.from(new Set([...(existing.tags || []), ...(normalized.tags || [])])),
        last_used_at: normalized.last_used_at || existing.last_used_at,
        updated_at: normalized.updated_at || existing.updated_at,
        source: normalized.source || existing.source,
      });
    });
    return Array.from(byEmail.values()).sort((left, right) => {
      const leftTime = left.updated_at || left.last_used_at || '';
      const rightTime = right.updated_at || right.last_used_at || '';
      return rightTime.localeCompare(leftTime) || left.email.localeCompare(right.email);
    });
  };

  const buildContactFormPayload = (): ContactUpsertPayload => ({
    name: contactDraft.name.trim(),
    email: contactDraft.email.trim().toLowerCase(),
    phone: contactDraft.phone?.trim() || '',
    note: contactDraft.note?.trim() || '',
    groups: (contactDraft.groups || []).map((item) => item.trim()).filter(Boolean),
    tags: (contactDraft.tags || []).map((item) => item.trim()).filter(Boolean),
  });

  useEffect(() => {
    if (!hasAccountContext || isComposing) {
      return;
    }
    const cachedDraft = readComposeDraftCache(accountEmail);
    if (!cachedDraft) {
      return;
    }
    setComposeInitialValues(cachedDraft.values);
    setComposeDraftId(cachedDraft.draft_id);
    setIsComposing(true);
  }, [accountEmail, hasAccountContext, isComposing]);

  useEffect(() => {
    document.documentElement.dataset.theme = preferences.theme.mode;
    return () => {
      delete document.documentElement.dataset.theme;
    };
  }, [preferences.theme.mode]);

  // Load Folders & Settings
  useEffect(() => {
    let cancelled = false;
    setIsInitialDataReady(false);
    fetchFolders().then((res) => {
      if (cancelled) return;
      setIsAuthenticated(true);
      applyFolders(res.folders || []);
    }).catch((error) => {
      const message = handleApiError(error);
      const typedError = error as Error & { code?: string };
      if (typedError.code === 'AUTH_SESSION_EXPIRED') {
        setIsInitialDataReady(false);
        return;
      }
      console.error(message);
    });

    fetchSettings().then((res) => {
      if (cancelled) return;
      if (res.account?.email) setAccountEmail(res.account.email);
      if (res.preferences) setPreferences(normalizePreferences(res.preferences));
      setHasAccountContext(true);
      setIsInitialDataReady(true);
    }).catch((error) => {
      if (cancelled) return;
      const message = handleApiError(error);
      const typedError = error as Error & { code?: string };
      if (typedError.code === 'AUTH_SESSION_EXPIRED') {
        setIsInitialDataReady(false);
        return;
      }
      console.error(message);
      setIsInitialDataReady(true);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Load Messages when folder, query or preferences changes
  const loadMessages = async (options: { resetSelection?: boolean; refresh?: boolean } = {}) => {
    if (!currentFolder) return;
    setIsLoadingMessages(true);
    const activeQuery = activeSearch.query.trim();
    const loadOpts = {
      page: messagePage,
      refresh: options.refresh ?? false,
      pageSize: preferences.system.page_size,
      sender: activeSearch.sender.trim() || undefined,
      dateFrom: activeSearch.dateFrom || undefined,
      dateTo: activeSearch.dateTo || undefined,
      hasAttachments: activeSearch.hasAttachments,
    };

    const request = activeQuery
      ? searchFolderMessages(currentFolder, activeQuery, loadOpts)
      : fetchFolderMessages(currentFolder, loadOpts);

    try {
      const res = await request;
      setMessages(res.messages || []);
      setMessageTotal(res.total || 0);
      setSelectedMessageUids((current) => current.filter((uid) => (res.messages || []).some((item) => item.uid === uid)));
      if (options.resetSelection !== false) {
        setSelectedMessage(null);
      } else if (selectedMessage) {
        const latest = (res.messages || []).find((item) => item.uid === selectedMessage.uid);
        if (latest) {
          setSelectedMessage(latest);
        } else {
          setSelectedMessage(null);
        }
      }
      setIsAuthenticated(true);
    } catch (error) {
      const message = handleApiError(error);
      console.error(message);
    } finally {
      setIsLoadingMessages(false);
    }
  };

  useEffect(() => {
    if (!isInitialDataReady) {
      return;
    }
    loadMessages();
  }, [
    isInitialDataReady,
    currentFolder,
    messagePage,
    activeSearch.query,
    preferences.system.page_size,
    activeSearch.sender,
    activeSearch.dateFrom,
    activeSearch.dateTo,
    activeSearch.hasAttachments,
  ]);

  // Load specific message details when selected
  useEffect(() => {
    if (!selectedMessage) {
      setMessageBody({ html: null, text: '', attachments: [] });
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
            attachments: res.attachments || [],
          });
          // Optionally mark as read automatically per settings
          if (preferences.system.mark_read_on_open && !selectedMessage.read && suppressAutoMarkReadRef.current !== selectedMessage.uid) {
             updateMessageOperation(currentFolder, { action: 'mark_read', uids: [selectedMessage.uid] }).then(() => {
                if (cancelled) {
                  return;
                }
                setMessages((msgs) => msgs.map((m) => m.uid === selectedMessage.uid ? { ...m, read: true } : m));
                setSelectedMessage((current) => current?.uid === selectedMessage.uid ? { ...current, read: true } : current);
                refreshFolders();
             });
          } else if (suppressAutoMarkReadRef.current === selectedMessage.uid) {
             suppressAutoMarkReadRef.current = null;
          }
        }
      })
      .catch((e) => {
        if (!cancelled) setMessageBody({ html: null, text: '加载出错: ' + handleApiError(e), attachments: [] });
      });

    return () => {
      cancelled = true;
    };
  }, [selectedMessage, currentFolder, preferences.system.mark_read_on_open]);

  useEffect(() => {
    if (!showContacts || !hasAccountContext) {
      return;
    }
    let cancelled = false;
    setContactsError(null);
    setContactPage(1);
    const cachedContacts = readContactCache(accountEmail);
    setContacts(cachedContacts);
    setContactsTotal(cachedContacts.length);
    if (cachedContacts.length) {
      const selected = cachedContacts[0];
      setEditingContactId(selected.id || selected.email);
      setContactEditorMode('edit');
      setContactDraft(serializeContactDraft(selected));
    } else {
      setEditingContactId(null);
      setContactEditorMode('create');
      setContactDraft({ name: '', email: '', phone: '', note: '', groups: [], tags: [] });
    }
    setContactSaving(false);
    setContactDeletingId(null);

    fetchContacts({ page: 1, pageSize: 50 })
      .then((res) => {
        if (cancelled) return;
        const remoteContacts = (res.contacts || []).map((item) =>
          normalizeContactItem({
            id: item.id || item.email,
            name: item.name || item.email.split('@')[0] || '',
            email: item.email,
            phone: item.phone || '',
            note: item.note || '',
            groups: item.groups || [],
            tags: item.tags || [],
            last_used_at: item.last_used_at || null,
            created_at: item.created_at || null,
            updated_at: item.updated_at || item.last_used_at || null,
            source: item.source || 'recent',
          }),
        );
        const mergedContacts = mergeContacts(cachedContacts, remoteContacts);
        setContacts(mergedContacts);
        setContactsTotal(mergedContacts.length);
        writeContactCache(accountEmail, mergedContacts);
        if (!cachedContacts.length && mergedContacts.length) {
          const selected = mergedContacts[0];
          setEditingContactId(selected.id || selected.email);
          setContactEditorMode('edit');
          setContactDraft(serializeContactDraft(selected));
        }
      })
      .catch((error) => {
        if (!cancelled && !cachedContacts.length) {
          setContactsError(handleApiError(error));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [showContacts, hasAccountContext, accountEmail]);

  useEffect(() => {
    if (!hasAccountContext || !accountEmail) {
      return;
    }
    let cancelled = false;
    const cachedContacts = readContactCache(accountEmail);
    if (cachedContacts.length) {
      setContacts((current) => mergeContacts(current, cachedContacts));
      setContactsTotal((current) => Math.max(current, cachedContacts.length));
    }
    fetchContacts({ page: 1, pageSize: 200 })
      .then((res) => {
        if (cancelled) return;
        const remoteContacts = (res.contacts || []).map((item) =>
          normalizeContactItem({
            id: item.id || item.email,
            name: item.name || item.email.split('@')[0] || '',
            email: item.email,
            phone: item.phone || '',
            note: item.note || '',
            groups: item.groups || [],
            tags: item.tags || [],
            last_used_at: item.last_used_at || null,
            created_at: item.created_at || null,
            updated_at: item.updated_at || item.last_used_at || null,
            source: item.source || 'recent',
          }),
        );
        setContacts((current) => {
          const mergedContacts = mergeContacts(current, remoteContacts);
          writeContactCache(accountEmail, mergedContacts);
          setContactsTotal(Math.max(mergedContacts.length, res.total || mergedContacts.length));
          return mergedContacts;
        });
      })
      .catch(() => {
        // 联系人预加载失败不阻断主流程，打开联系人面板时仍会重试。
      });
    return () => {
      cancelled = true;
    };
  }, [hasAccountContext, accountEmail]);

  const filteredContacts = useMemo(() => {
    const queryText = contactQuery.trim().toLowerCase();
    const groupFilter = contactGroupFilter.trim().toLowerCase();
    const tagFilter = contactTagFilter.trim().toLowerCase();
    return contacts.filter((contact) => {
      const haystack = [
        contact.name,
        contact.email,
        contact.phone,
        contact.note,
        ...(contact.groups || []),
        ...(contact.tags || []),
      ]
        .join(' ')
        .toLowerCase();
      const matchesQuery = !queryText || haystack.includes(queryText);
      const matchesGroup = !groupFilter || (contact.groups || []).some((group) => group.toLowerCase() === groupFilter);
      const matchesTag = !tagFilter || (contact.tags || []).some((tag) => tag.toLowerCase() === tagFilter);
      return matchesQuery && matchesGroup && matchesTag;
    });
  }, [contacts, contactQuery, contactGroupFilter, contactTagFilter]);

  const contactGroups = useMemo(() => {
    return Array.from(new Set(contacts.flatMap((contact) => contact.groups || []))).sort((left, right) => left.localeCompare(right));
  }, [contacts]);

  const contactTags = useMemo(() => {
    return Array.from(new Set(contacts.flatMap((contact) => contact.tags || []))).sort((left, right) => left.localeCompare(right));
  }, [contacts]);

  const totalContactPages = Math.max(1, Math.ceil(filteredContacts.length / contactPageSize));
  const safeContactPage = Math.min(contactPage, totalContactPages);
  const pagedContacts = filteredContacts.slice((safeContactPage - 1) * contactPageSize, safeContactPage * contactPageSize);
  const selectedContact = contacts.find((contact) => (contact.id || contact.email) === editingContactId) || null;
  const activeContact = contactEditorMode === 'edit' ? selectedContact : null;
  const contactByEmail = useMemo(() => {
    const map = new Map<string, ContactItem>();
    contacts.forEach((contact) => {
      map.set(contact.email.trim().toLowerCase(), contact);
    });
    return map;
  }, [contacts]);
  const activeSearchSummary = useMemo(() => {
    const parts: string[] = [];
    const queryText = activeSearch.query.trim();
    const senderText = activeSearch.sender.trim();
    if (queryText) parts.push(`关键词：${queryText}`);
    if (senderText) parts.push(`发件人：${senderText}`);
    if (activeSearch.dateFrom) parts.push(`开始：${activeSearch.dateFrom}`);
    if (activeSearch.dateTo) parts.push(`结束：${activeSearch.dateTo}`);
    if (activeSearch.hasAttachments) {
      parts.push('有附件');
    }
    return parts.join(' · ');
  }, [activeSearch]);

  const resolveSenderLabel = (message: MailMessageSummary) => {
    const senderEmail = message.sender?.email?.trim() || '';
    const normalizedEmail = senderEmail.toLowerCase();
    const matchedContact = normalizedEmail ? contactByEmail.get(normalizedEmail) : null;
    return matchedContact?.note?.trim()
      || message.sender?.name?.trim()
      || preferences.user.display_name.trim()
      || senderEmail
      || '未提供';
  };

  const doSearch = (e: FormEvent) => {
    e.preventDefault();
    setMessagePage(1);
    setActiveSearch({ ...searchDraft });
  };

  const handleClearSearch = () => {
    const emptySearch = {
      query: '',
      sender: '',
      dateFrom: '',
      dateTo: '',
      hasAttachments: false,
    };
    setMessagePage(1);
    setSearchDraft(emptySearch);
    setActiveSearch(emptySearch);
  };

  const totalMessagePages = Math.max(1, Math.ceil(messageTotal / Math.max(preferences.system.page_size, 1)));
  const allVisibleSelected = messages.length > 0 && messages.every((message) => selectedMessageUids.includes(message.uid));

  const toggleMessageSelection = (uid: string) => {
    setSelectedMessageUids((current) => (
      current.includes(uid) ? current.filter((item) => item !== uid) : [...current, uid]
    ));
  };

  const toggleSelectAllVisibleMessages = () => {
    setSelectedMessageUids((current) => {
      if (messages.length === 0) {
        return current;
      }
      if (allVisibleSelected) {
        return current.filter((uid) => !messages.some((message) => message.uid === uid));
      }
      const next = new Set(current);
      messages.forEach((message) => next.add(message.uid));
      return Array.from(next);
    });
  };

  const handleBatchAction = async (action: MessageOperationAction | 'hard_delete', targetFolder?: string) => {
    if (!selectedMessageUids.length) return;
    try {
      if (targetFolder) {
        await moveMessages(currentFolder, selectedMessageUids, targetFolder);
      } else if (action === 'hard_delete') {
        await deleteMessages(currentFolder, selectedMessageUids);
      } else {
        await updateMessageOperation(currentFolder, { action, uids: selectedMessageUids });
      }
      if (action === 'mark_read' || action === 'mark_unread') {
        const nextRead = action === 'mark_read';
        setMessages((current) => current.map((item) => (
          selectedMessageUids.includes(item.uid) ? { ...item, read: nextRead } : item
        )));
        setSelectedMessage((current) => (
          current && selectedMessageUids.includes(current.uid) ? { ...current, read: nextRead } : current
        ));
      } else {
        if (selectedMessage && selectedMessageUids.includes(selectedMessage.uid)) {
          setSelectedMessage(null);
        }
        setSelectedMessageUids([]);
        await loadMessages({ refresh: true });
      }
      await refreshFolders();
    } catch (error) {
      console.error(error);
    }
  };

  const handleMsgAction = async (action: MessageOperationAction | 'hard_delete') => {
    if (!selectedMessage) return;
    try {
      if (action === 'hard_delete') {
         await deleteMessages(currentFolder, [selectedMessage.uid]);
      } else {
         await updateMessageOperation(currentFolder, { action, uids: [selectedMessage.uid] });
      }
      if (action === 'mark_read' || action === 'mark_unread') {
        const nextRead = action === 'mark_read';
        suppressAutoMarkReadRef.current = action === 'mark_unread' ? selectedMessage.uid : null;
        setMessages((msgs) => msgs.map((item) => item.uid === selectedMessage.uid ? { ...item, read: nextRead } : item));
        setSelectedMessage((current) => current?.uid === selectedMessage.uid ? { ...current, read: nextRead } : current);
      } else {
        await loadMessages({ refresh: true });
        setSelectedMessage(null);
      }
      await refreshFolders();
    } catch (e) {
      console.error(e);
    }
  };

  const handleMove = async (targetFolder: string) => {
    if (!selectedMessage || !targetFolder) return;
    try {
      await moveMessages(currentFolder, [selectedMessage.uid], targetFolder);
      await loadMessages({ refresh: true });
      await refreshFolders();
      setSelectedMessage(null);
    } catch(e) {
      console.error(e);
    }
  };

  const handleSaveSettings = async () => {
    setIsSavingSettings(true);
    setSettingsError(null);
    try {
      const res = await saveSettings({ ...preferences });
      if (res.preferences) setPreferences(normalizePreferences(res.preferences));
      setShowSettings(false);
    } catch (e) {
      setSettingsError((e as Error).message || '保存设置失败');
    } finally {
      setIsSavingSettings(false);
    }
  };

  const handleAvatarUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }
    setIsUploadingAvatar(true);
    setSettingsError(null);
    try {
      const res = await uploadSettingsAvatar(file);
      if (res.preferences) {
        setPreferences(normalizePreferences(res.preferences));
      }
    } catch (error) {
      setSettingsError((error as Error).message || '上传头像失败');
    } finally {
      event.target.value = '';
      setIsUploadingAvatar(false);
    }
  };

  const refreshFolders = async () => {
    const res = await fetchFolders();
    applyFolders(res.folders || []);
  };

  const openFolderManager = () => {
    setFolderActionError(null);
    setFolderActionSuccess(null);
    setFolderForm({ createName: '', renameTarget: '', renameName: '' });
    setShowFolderManager(true);
  };

  const handleCreateFolder = async () => {
    const name = folderForm.createName.trim();
    if (!name) {
      setFolderActionError('请填写文件夹名称。');
      return;
    }
    setFolderActionLoading(true);
    setFolderActionError(null);
    try {
      await createFolder(name);
      await refreshFolders();
      setFolderActionSuccess(`已创建文件夹：${name}`);
      setFolderForm((current) => ({ ...current, createName: '' }));
    } catch (error) {
      setFolderActionError((error as Error).message || '创建文件夹失败');
    } finally {
      setFolderActionLoading(false);
    }
  };

  const handleRenameFolder = async () => {
    const target = folderForm.renameTarget.trim();
    const nextName = folderForm.renameName.trim();
    if (!target || !nextName) {
      setFolderActionError('请先选择文件夹并填写新名称。');
      return;
    }
    setFolderActionLoading(true);
    setFolderActionError(null);
    try {
      await renameFolder(target, nextName);
      await refreshFolders();
      setFolderActionSuccess(`已重命名文件夹：${target} → ${nextName}`);
      setFolderForm({ createName: '', renameTarget: '', renameName: '' });
    } catch (error) {
      setFolderActionError((error as Error).message || '重命名文件夹失败');
    } finally {
      setFolderActionLoading(false);
    }
  };

  const handleDeleteFolder = async () => {
    const target = folderForm.renameTarget.trim();
    if (!target) {
      setFolderActionError('请先选择要删除的文件夹。');
      return;
    }
    const folder = folders.find((item) => item.name === target);
    if (folder && protectedFolderTypes.has(folder.type)) {
      setFolderActionError('系统文件夹不可删除。');
      return;
    }
    setFolderActionLoading(true);
    setFolderActionError(null);
    try {
      await deleteFolder(target);
      await refreshFolders();
      setFolderActionSuccess(`已删除文件夹：${target}`);
      setFolderForm((current) => ({ ...current, renameTarget: '', renameName: '' }));
    } catch (error) {
      setFolderActionError((error as Error).message || '删除文件夹失败');
    } finally {
      setFolderActionLoading(false);
    }
  };

  const handleChangePassword = async (event: FormEvent) => {
    event.preventDefault();
    setPasswordError(null);
    setPasswordSuccess(null);
    if (!passwordForm.current_password.trim()) {
      setPasswordError('请填写旧密码。');
      return;
    }
    if (!passwordForm.new_password.trim()) {
      setPasswordError('请填写新密码。');
      return;
    }
    if (passwordForm.new_password !== passwordForm.confirm_password) {
      setPasswordError('两次输入的新密码不一致。');
      return;
    }
    if (passwordForm.current_password === passwordForm.new_password) {
      setPasswordError('新密码不能与旧密码相同。');
      return;
    }

    setIsChangingPassword(true);
    try {
      await changePassword({
        current_password: passwordForm.current_password,
        new_password: passwordForm.new_password,
      });
      setPasswordSuccess('密码已更新，并已通过新密码完成 IMAP 验证。');
      setPasswordForm({ current_password: '', new_password: '', confirm_password: '' });
    } catch (error) {
      setPasswordError((error as Error).message || '修改密码失败');
    } finally {
      setIsChangingPassword(false);
    }
  };

  const handleAuthSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setIsSubmittingAuth(true);
    setAuthError(null);
    try {
      const result = authMode === 'login' ? await login(authForm) : await register(authForm);
      setAccountEmail(result.email);
      setHasAccountContext(true);
      setIsAuthenticated(true);
      setAuthForm({ email: '', password: '', remember: false, display_name: '' });
      await Promise.all([fetchFolders(), fetchSettings()]).then(([folderResult, settingsResult]) => {
        applyFolders(folderResult.folders || []);
        if (settingsResult.account?.email) setAccountEmail(settingsResult.account.email);
        if (settingsResult.preferences) setPreferences(normalizePreferences(settingsResult.preferences));
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
    setHasAccountContext(false);
    setIsComposing(false);
    setComposeInitialValues(null);
    setComposeDraftId(null);
    setShowSettings(false);
    setShowSignatures(false);
    setShowContacts(false);
    setMessages([]);
    setSelectedMessage(null);
    setContacts([]);
    setContactQuery('');
    setContactGroupFilter('');
    setContactTagFilter('');
    setContactPage(1);
    setEditingContactId(null);
    setContactEditorMode('create');
    setContactDraft({ name: '', email: '', phone: '', note: '', groups: [], tags: [] });
    setSearchDraft({
      query: '',
      sender: '',
      dateFrom: '',
      dateTo: '',
      hasAttachments: false,
    });
    setActiveSearch({
      query: '',
      sender: '',
      dateFrom: '',
      dateTo: '',
      hasAttachments: false,
    });
  };

  const buildReplyQuote = (message: MailMessageSummary, body: { html: string | null; text: string }): ComposeValues => {
    const subject = message.subject?.startsWith('Re:') ? message.subject : `Re: ${message.subject || '(无主题)'}`;
    const meta = buildReplyQuoteMeta(message, body);
    const quoteBlockText = `---- 原始邮件 ----\n${meta.metaText}\n\n${body.text}`;
    const quoteBlockHtml = `<blockquote>${meta.metaHtml}<div>${meta.quoteBodyHtml}</div></blockquote>`;
    const quotePosition = preferences.system.reply_quote_position || 'bottom';
    return {
      to: message.sender?.email ? [message.sender.email] : [],
      subject,
      text_body: quotePosition === 'top' ? `${quoteBlockText}\n\n` : `\n\n${quoteBlockText}`,
      html_body: quotePosition === 'top'
        ? `${quoteBlockHtml}<p><br></p>`
        : `<p><br></p>${quoteBlockHtml}`,
    };
  };

  const openCompose = (initialValues: ComposeValues | null = null, draftId: string | null = null) => {
    setComposeInitialValues(initialValues);
    setComposeDraftId(draftId);
    setIsComposing(true);
  };

  const openContacts = () => {
    setShowContacts(true);
    setContactPage(1);
  };

  const selectContact = (contact: ContactItem) => {
    setEditingContactId(contact.id || contact.email);
    setContactEditorMode('edit');
    setContactDraft(serializeContactDraft(contact));
  };

  const startCreateContact = () => {
    setEditingContactId(null);
    setContactEditorMode('create');
    setContactDraft({ name: '', email: '', phone: '', note: '', groups: [], tags: [] });
  };

  const handleContactSave = async () => {
    setContactsError(null);
    const payload = buildContactFormPayload();
    if (!payload.email) {
      setContactsError('请填写邮箱。');
      return;
    }
    if (!payload.name) {
      setContactsError('请填写姓名。');
      return;
    }
    setContactSaving(true);
    try {
      const result = contactEditorMode === 'edit' && editingContactId
        ? await updateContact(editingContactId, payload)
        : await createContact(payload);
      const nextContact = normalizeContactItem({
        ...result.contact,
        source: result.contact.source || 'manual',
      });
      const nextContacts = mergeContacts(
        contacts.filter((item) => (item.id || item.email) !== (editingContactId || nextContact.email)),
        [nextContact],
      );
      setContacts(nextContacts);
      setContactsTotal(nextContacts.length);
      writeContactCache(accountEmail, nextContacts);
      setEditingContactId(nextContact.id || nextContact.email);
      setContactEditorMode('edit');
      setContactDraft(serializeContactDraft(nextContact));
    } catch (error) {
      setContactsError(handleApiError(error));
    } finally {
      setContactSaving(false);
    }
  };

  const handleContactDelete = async (contact: ContactItem) => {
    const contactId = contact.id || contact.email;
    setContactDeletingId(contactId);
    try {
      await deleteContact(contactId);
      const nextContacts = contacts.filter((item) => (item.id || item.email) !== contactId);
      setContacts(nextContacts);
      setContactsTotal(nextContacts.length);
      writeContactCache(accountEmail, nextContacts);
      if (editingContactId === contactId) {
        startCreateContact();
      }
    } catch (error) {
      setContactsError(handleApiError(error));
    } finally {
      setContactDeletingId(null);
    }
  };

  const handleContactCompose = (contact: ContactItem) => {
    setShowContacts(false);
    openCompose({ to: [contact.email] });
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
        attachments: detail.attachments || [],
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

  const formatDate = (dateStr: string | null) => formatDateByTimezone(dateStr, {
    locale: preferences.system.language || 'zh-CN',
    timezone: preferences.system.timezone || 'Asia/Shanghai',
    dateStyle: 'short',
    timeStyle: 'short',
  });
  const formatContactDate = (dateStr: string | null | undefined) => formatDateByTimezone(dateStr, {
    locale: preferences.system.language || 'zh-CN',
    timezone: preferences.system.timezone || 'Asia/Shanghai',
  });

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
    <div className="app-container" data-theme={preferences.theme.mode}>
      {/* Sidebar */}
      <aside className="sidebar">
        <div className="sidebar-header">
          <div className="account-info">
            <div className="account-avatar">
              {preferences.user.avatar_url ? (
                <img src={preferences.user.avatar_url} alt="用户头像" className="account-avatar-image" />
              ) : (
                (preferences.user.display_name || accountEmail).charAt(0).toUpperCase()
              )}
            </div>
            <div className="account-text">
              <span className="account-name">{preferences.user.display_name || 'User'}</span>
              {preferences.user.profile_title ? <span className="account-title">{preferences.user.profile_title}</span> : null}
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

        <form className="search-box search-panel" onSubmit={doSearch}>
          <label className="search-field">
            <span className="search-field__label">关键词</span>
            <div className="search-input-wrapper">
              <svg className="search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="11" cy="11" r="8"></circle>
                <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
              </svg>
              <input
                type="search"
                className="search-input"
                placeholder="搜索邮件..."
                value={searchDraft.query}
                onChange={(event) => setSearchDraft((current) => ({ ...current, query: event.target.value }))}
              />
              {searchDraft.query || searchDraft.sender || searchDraft.dateFrom || searchDraft.dateTo || searchDraft.hasAttachments ? (
                <button
                  type="button"
                  onClick={handleClearSearch}
                  className="search-input__clear"
                  aria-label="清除搜索条件"
                >
                  ×
                </button>
              ) : null}
            </div>
          </label>
          <div className="search-actions">
            <button type="submit" className="search-action search-action--primary">搜索</button>
            <button
              type="button"
              className="search-action"
              onClick={() => setShowSearchFilters((current) => !current)}
              aria-expanded={showSearchFilters}
            >
              {showSearchFilters ? '收起筛选' : '展开筛选'}
            </button>
            <button type="button" className="search-action" onClick={handleClearSearch}>清除</button>
          </div>
          {showSearchFilters ? (
            <div className="search-filters-panel">
              <label className="search-field">
                <span className="search-field__label">发件人</span>
                <input
                  type="text"
                  className="search-field__input"
                  value={searchDraft.sender}
                  onChange={(event) => setSearchDraft((current) => ({ ...current, sender: event.target.value }))}
                  placeholder="姓名或邮箱"
                />
              </label>
              <div className="search-grid">
                <label className="search-field">
                  <span className="search-field__label">开始日期</span>
                  <input
                    type="date"
                    className="search-field__input"
                    value={searchDraft.dateFrom}
                    onChange={(event) => setSearchDraft((current) => ({ ...current, dateFrom: event.target.value }))}
                  />
                </label>
                <label className="search-field">
                  <span className="search-field__label">结束日期</span>
                  <input
                    type="date"
                    className="search-field__input"
                    value={searchDraft.dateTo}
                    onChange={(event) => setSearchDraft((current) => ({ ...current, dateTo: event.target.value }))}
                  />
                </label>
              </div>
              <label className="search-toggle">
                <input
                  type="checkbox"
                  checked={searchDraft.hasAttachments}
                  onChange={(event) => setSearchDraft((current) => ({ ...current, hasAttachments: event.target.checked }))}
                />
                <span>仅看有附件</span>
              </label>
            </div>
          ) : null}
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
                    setSelectedMessage(null);
                    setSelectedMessageUids([]);
                    setMessageBody({ html: null, text: '', attachments: [] });
                    setMessagePage(1);
                    setCurrentFolder(folder.name);
                    setSearchDraft({
                      query: '',
                      sender: '',
                      dateFrom: '',
                      dateTo: '',
                      hasAttachments: false,
                    });
                    setActiveSearch({
                      query: '',
                      sender: '',
                      dateFrom: '',
                      dateTo: '',
                      hasAttachments: false,
                    });
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
          <button type="button" className="footer-item footer-button" onClick={openContacts} aria-label="打开联系人">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"></path>
              <circle cx="9" cy="7" r="4"></circle>
              <path d="M22 21v-2a4 4 0 0 0-3-3.87"></path>
              <path d="M16 3.13a4 4 0 0 1 0 7.75"></path>
            </svg>
            联系人
          </button>
          <button type="button" className="footer-item footer-button" onClick={openFolderManager} aria-label="管理文件夹">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M3 7h6l2 2h10v10H3z"></path>
            </svg>
            文件夹管理
          </button>
          <button
            type="button"
            className="footer-item footer-button"
            onClick={() => {
              setPasswordError(null);
              setPasswordSuccess(null);
              setSettingsError(null);
              setPasswordForm({ current_password: '', new_password: '', confirm_password: '' });
              setShowSettings(true);
            }}
            aria-label="打开设置"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="3"></circle>
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
            </svg>
            设置
          </button>
          <button type="button" className="footer-item footer-button" onClick={() => setShowSignatures(true)}>
            签名设置
          </button>
          <div className="footer-item" onClick={handleLogout}>退出登录</div>
        </div>
      </aside>

      {/* Main Content */}
      <main className="main-content">
        <header className="topbar">
          <div className="topbar-left">
            <div className="topbar-title">
              {folders.find(f => f.name === currentFolder)?.display_name || currentFolder}
              {activeSearch.query.trim() && (
                <span style={{fontSize: '14px', color: '#666', fontWeight: 400, marginLeft: '12px'}}>
                  搜索: {activeSearch.query.trim()}
                  {activeSearchSummary ? ` · ${activeSearchSummary}` : ''}
                </span>
              )}
            </div>
          </div>
          <div className="topbar-right">
             <button className="action-btn" onClick={() => loadMessages({ refresh: true })}>刷新列表</button>
          </div>
        </header>

        <div className="content-row">
          {/* Message List */}
          <div className="message-list-container">
            <div className="message-list-toolbar">
              <div className="message-list-toolbar__selection">
                <label className="message-select-all">
                  <input
                    type="checkbox"
                    checked={allVisibleSelected}
                    onChange={toggleSelectAllVisibleMessages}
                    aria-label="全选当前页邮件"
                  />
                  <span>全选本页</span>
                </label>
                <span className="message-list-toolbar__meta">共 {messageTotal} 封</span>
                <span className="message-list-toolbar__meta">已选 {selectedMessageUids.length} 封</span>
              </div>
              <div className="message-pagination">
                <button
                  type="button"
                  className="message-pagination__button"
                  onClick={() => setMessagePage((current) => Math.max(1, current - 1))}
                  disabled={messagePage <= 1 || isLoadingMessages}
                >
                  上一页
                </button>
                <span className="message-pagination__status">
                  第 {messagePage} / {totalMessagePages} 页
                </span>
                <button
                  type="button"
                  className="message-pagination__button"
                  onClick={() => setMessagePage((current) => Math.min(totalMessagePages, current + 1))}
                  disabled={messagePage >= totalMessagePages || isLoadingMessages}
                >
                  下一页
                </button>
              </div>
            </div>
            {selectedMessageUids.length ? (
              <div className="message-batch-toolbar">
                <button type="button" className="action-btn" onClick={() => handleBatchAction('mark_read')}>批量标已读</button>
                <button type="button" className="action-btn" onClick={() => handleBatchAction('mark_unread')}>批量标未读</button>
                <button type="button" className="action-btn" onClick={() => handleBatchAction('delete')}>批量删除</button>
                <select
                  className="message-batch-move"
                  title="批量移动到"
                  value=""
                  onChange={(event) => {
                    if (!event.target.value) return;
                    handleBatchAction('delete', event.target.value);
                    event.target.value = '';
                  }}
                >
                  <option value="" disabled>批量移动到...</option>
                  {folders.filter((folder) => folder.name !== currentFolder).map((folder) => (
                    <option key={folder.name} value={folder.name}>{folder.display_name}</option>
                  ))}
                </select>
              </div>
            ) : null}
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
                  <label
                    className="message-row-checkbox"
                    onClick={(event) => event.stopPropagation()}
                  >
                    <input
                      type="checkbox"
                      checked={selectedMessageUids.includes(msg.uid)}
                      onChange={() => toggleMessageSelection(msg.uid)}
                      aria-label={`选择邮件 ${msg.subject || msg.sender?.email || msg.uid}`}
                    />
                  </label>
                  {!msg.read ? <div className="unread-dot"></div> : <div className="read-dot-placeholder"></div>}
                  <div className="sender-name">{resolveSenderLabel(msg)}</div>
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
                  <div className="field-value">
                    {resolveSenderLabel(selectedMessage)} <span>&lt;{selectedMessage.sender?.email || '未提供'}&gt;</span>
                  </div>
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
                <MessageBodyView
                  html={messageBody.html}
                  htmlTestId="app-message-html-body"
                  htmlClassName="reading-html-body"
                  text={messageBody.text || '正在加载正文...'}
                  textTestId="app-message-text-body"
                  textClassName="reading-text-body"
                />
                <section className="reading-attachments" aria-label="附件">
                  <h3>附件</h3>
                  {messageBody.attachments.length > 0 ? (
                    <ul>
                      {messageBody.attachments.map((attachment) => {
                        const id = attachmentId(attachment);
                        const href = id ? buildAttachmentUrl(currentFolder, selectedMessage.uid, id) : '#';
                        const attachmentType = attachment.content_type || '未知类型';
                        const size = formatSize(attachment.size_bytes ?? attachment.size ?? null);
                        return (
                          <li key={id || attachment.filename}>
                            <div>
                              <strong>{attachment.filename || '未命名附件'}</strong>
                              <span>{attachmentType} · {size}</span>
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
              </div>
            </div>
          )}
        </div>
      </main>

      {/* Settings Modal */}
      {showSettings && (
        <div className="settings-modal-overlay">
          <div className="settings-modal" role="dialog" aria-modal="true" aria-label="设置">
            <div className="settings-header">
              <div>
                <h2>设置</h2>
                <p>统一管理当前登录用户的系统偏好、资料信息和主题显示。</p>
              </div>
            </div>
            <section className="settings-panel">
              <div className="settings-panel__head">
                <h3>系统设置</h3>
                <p>控制分页、自动已读、回复引用和本地化显示。</p>
              </div>
              <div className="settings-grid">
                <div className="settings-field">
                  <label htmlFor="pageSize">每页显示邮件数</label>
                  <select
                    id="pageSize"
                    value={preferences.system.page_size}
                    onChange={e => updateSystemPreferences({ page_size: Number(e.target.value) })}
                  >
                    <option value={10}>10</option>
                    <option value={30}>30</option>
                    <option value={50}>50</option>
                    <option value={100}>100</option>
                  </select>
                </div>
                <div className="settings-field">
                  <label htmlFor="replyQuotePosition">回复引用位置</label>
                  <select
                    id="replyQuotePosition"
                    value={preferences.system.reply_quote_position}
                    onChange={(event) => updateSystemPreferences({ reply_quote_position: event.target.value as 'top' | 'bottom' })}
                  >
                    <option value="bottom">底部引用</option>
                    <option value="top">顶部引用</option>
                  </select>
                </div>
                <div className="settings-field">
                  <label htmlFor="language">界面语言</label>
                  <select
                    id="language"
                    value={preferences.system.language}
                    onChange={e => updateSystemPreferences({ language: e.target.value })}
                  >
                    <option value="zh-CN">简体中文</option>
                    <option value="en-US">English</option>
                  </select>
                </div>
                <div className="settings-field">
                  <label htmlFor="timezone">时区</label>
                  <select
                    id="timezone"
                    value={preferences.system.timezone}
                    onChange={e => updateSystemPreferences({ timezone: e.target.value })}
                  >
                    <option value="Asia/Shanghai">Asia/Shanghai</option>
                    <option value="UTC">UTC</option>
                    <option value="America/Los_Angeles">America/Los_Angeles</option>
                    <option value="Europe/London">Europe/London</option>
                  </select>
                </div>
                <div className="settings-field settings-check-row settings-field-full">
                  <input
                    type="checkbox"
                    id="markReadOnOpen"
                    checked={preferences.system.mark_read_on_open}
                    onChange={e => updateSystemPreferences({ mark_read_on_open: e.target.checked })}
                  />
                  <label htmlFor="markReadOnOpen">自动标记为已读（打开邮件时）</label>
                </div>
              </div>
            </section>
            <section className="settings-panel">
              <div className="settings-panel__head">
                <h3>用户设置</h3>
                <p>这些资料将和当前登录账号绑定保存。</p>
              </div>
              <div className="settings-grid">
                <div className="settings-field">
                  <label htmlFor="displayName">显示名称</label>
                  <input
                    id="displayName"
                    value={preferences.user.display_name}
                    onChange={(event) => setPreferences((current) => ({ ...current, user: { ...current.user, display_name: event.target.value } }))}
                  />
                </div>
                <div className="settings-field">
                  <label htmlFor="profileTitle">职位/头衔</label>
                  <input
                    id="profileTitle"
                    value={preferences.user.profile_title}
                    onChange={(event) => setPreferences((current) => ({ ...current, user: { ...current.user, profile_title: event.target.value } }))}
                  />
                </div>
                <div className="settings-field settings-field-full">
                  <label htmlFor="avatarUpload">上传头像</label>
                  <input
                    id="avatarUpload"
                    type="file"
                    accept="image/*"
                    onChange={handleAvatarUpload}
                  />
                  <span className="settings-help-text">
                    {isUploadingAvatar ? '头像上传中...' : '支持本地上传图片，保存后自动绑定到当前登录用户。'}
                  </span>
                </div>
                <div className="settings-field settings-field-full">
                  <label htmlFor="avatarUrl">头像地址</label>
                  <input
                    id="avatarUrl"
                    type="url"
                    placeholder="https://example.com/avatar.png"
                    value={preferences.user.avatar_url}
                    onChange={(event) => setPreferences((current) => ({ ...current, user: { ...current.user, avatar_url: event.target.value } }))}
                  />
                </div>
                <div className="settings-field settings-field-full">
                  <label htmlFor="bio">个人简介</label>
                  <input
                    id="bio"
                    value={preferences.user.bio}
                    onChange={(event) => setPreferences((current) => ({ ...current, user: { ...current.user, bio: event.target.value } }))}
                  />
                </div>
              </div>
            </section>
            {settingsError ? <div className="settings-message settings-message-error" role="alert">{settingsError}</div> : null}
            <section className="settings-panel">
              <div className="settings-panel__head">
                <h3>主题设置</h3>
                <p>即时切换浅色与深色主题，保存后下次登录继续生效。</p>
              </div>
              <div className="theme-mode-group" role="radiogroup" aria-label="主题模式">
                <button
                  type="button"
                  className={`theme-mode-card ${preferences.theme.mode === 'light' ? 'active' : ''}`}
                  onClick={() => setPreferences((current) => ({ ...current, theme: { mode: 'light' } }))}
                  aria-pressed={preferences.theme.mode === 'light'}
                >
                  <strong>浅色主题</strong>
                  <span>适合明亮环境和日间浏览</span>
                </button>
                <button
                  type="button"
                  className={`theme-mode-card ${preferences.theme.mode === 'dark' ? 'active' : ''}`}
                  onClick={() => setPreferences((current) => ({ ...current, theme: { mode: 'dark' } }))}
                  aria-pressed={preferences.theme.mode === 'dark'}
                >
                  <strong>深色主题</strong>
                  <span>降低夜间使用时的屏幕刺激</span>
                </button>
              </div>
            </section>
            <form className="settings-password-panel" onSubmit={handleChangePassword}>
              <h3>修改密码</h3>
              <p>仅更新 Webmail 会话保存的密码，并用新密码重新验证 IMAP 登录。</p>
              <div className="settings-field">
                <label htmlFor="currentPassword">旧密码</label>
                <input
                  id="currentPassword"
                  type="password"
                  value={passwordForm.current_password}
                  onChange={(event) => {
                    setPasswordForm((current) => ({ ...current, current_password: event.target.value }));
                    setPasswordError(null);
                    setPasswordSuccess(null);
                  }}
                />
              </div>
              <div className="settings-field">
                <label htmlFor="newPassword">新密码</label>
                <input
                  id="newPassword"
                  type="password"
                  value={passwordForm.new_password}
                  onChange={(event) => {
                    setPasswordForm((current) => ({ ...current, new_password: event.target.value }));
                    setPasswordError(null);
                    setPasswordSuccess(null);
                  }}
                />
              </div>
              <div className="settings-field">
                <label htmlFor="confirmPassword">确认新密码</label>
                <input
                  id="confirmPassword"
                  type="password"
                  value={passwordForm.confirm_password}
                  onChange={(event) => {
                    setPasswordForm((current) => ({ ...current, confirm_password: event.target.value }));
                    setPasswordError(null);
                    setPasswordSuccess(null);
                  }}
                />
              </div>
              {passwordError ? <div className="settings-message settings-message-error" role="alert">{passwordError}</div> : null}
              {passwordSuccess ? <div className="settings-message settings-message-success" role="status">{passwordSuccess}</div> : null}
              <div className="settings-actions">
                <button
                  type="button"
                  onClick={() => {
                    setShowSettings(false);
                    setPasswordError(null);
                    setPasswordSuccess(null);
                  }}
                >
                  取消
                </button>
                <button className="primary" type="submit" disabled={isChangingPassword}>
                  {isChangingPassword ? '验证中...' : '更新密码'}
                </button>
              </div>
            </form>
            <div className="settings-actions settings-actions-bottom">
              <button type="button" onClick={() => setShowSettings(false)}>关闭设置</button>
              <button className="primary" onClick={handleSaveSettings} disabled={isSavingSettings}>
                {isSavingSettings ? '保存中...' : '保存设置'}
              </button>
            </div>
          </div>
        </div>
      )}

      <SignatureSettings
        open={showSignatures}
        onClose={() => setShowSignatures(false)}
      />

      {showFolderManager && (
        <div className="settings-modal-overlay">
          <div className="folder-modal" role="dialog" aria-modal="true" aria-label="文件夹管理">
            <header className="folder-modal-header">
              <div>
                <h2>文件夹管理</h2>
                <p>创建、重命名和删除自定义文件夹。系统文件夹不允许删除。</p>
              </div>
              <button type="button" className="contacts-close-button" onClick={() => setShowFolderManager(false)} aria-label="关闭文件夹管理">
                ×
              </button>
            </header>
            <div className="folder-modal-body">
              <section className="folder-panel">
                <div className="folder-panel__head">
                  <h3>新建文件夹</h3>
                  <p>输入名称后立即创建一个新的自定义文件夹。</p>
                </div>
                <div className="folder-form-row">
                  <label className="contacts-field folder-field folder-field--grow" htmlFor="folderCreateName">
                    <span>文件夹名称</span>
                    <input
                      id="folderCreateName"
                      aria-label="新建文件夹"
                      value={folderForm.createName}
                      onChange={(event) => setFolderForm((current) => ({ ...current, createName: event.target.value }))}
                      placeholder="例如：客户跟进"
                    />
                  </label>
                  <button type="button" className="contacts-primary-button folder-action-button" onClick={handleCreateFolder} disabled={folderActionLoading}>
                    {folderActionLoading ? '处理中...' : '创建'}
                  </button>
                </div>
              </section>
              <section className="folder-panel">
                <div className="folder-panel__head">
                  <h3>重命名或删除</h3>
                  <p>系统文件夹保留，只能操作自定义文件夹。</p>
                </div>
                <div className="folder-form-grid">
                  <label className="contacts-field folder-field" htmlFor="folderRenameTarget">
                    <span>选择文件夹</span>
                    <select
                      id="folderRenameTarget"
                      aria-label="选择文件夹"
                      value={folderForm.renameTarget}
                      onChange={(event) => setFolderForm((current) => ({ ...current, renameTarget: event.target.value }))}
                    >
                      <option value="">请选择</option>
                      {folders.map((folder) => (
                        <option key={folder.name} value={folder.name}>
                          {folder.display_name || folder.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="contacts-field folder-field" htmlFor="folderRenameName">
                    <span>新名称</span>
                    <input
                      id="folderRenameName"
                      aria-label="重命名文件夹"
                      value={folderForm.renameName}
                      onChange={(event) => setFolderForm((current) => ({ ...current, renameName: event.target.value }))}
                      placeholder="输入新的文件夹名称"
                    />
                  </label>
                </div>
                <div className="folder-actions">
                  <button type="button" className="contacts-secondary-button" onClick={handleRenameFolder} disabled={folderActionLoading}>
                    重命名
                  </button>
                  <button type="button" className="contacts-secondary-button danger" onClick={handleDeleteFolder} disabled={folderActionLoading}>
                    删除
                  </button>
                </div>
                <div className="folder-list-preview">
                  <h4>当前文件夹</h4>
                  <ul>
                    {folders.map((folder) => (
                      <li key={folder.name}>
                        <span>{folder.display_name || folder.name}</span>
                        <em>{folder.type}</em>
                      </li>
                    ))}
                  </ul>
                </div>
              </section>
              {folderActionError ? <div className="settings-message settings-message-error" role="alert">{folderActionError}</div> : null}
              {folderActionSuccess ? <div className="settings-message settings-message-success" role="status">{folderActionSuccess}</div> : null}
            </div>
          </div>
        </div>
      )}

      {showContacts && (
        <div className="settings-modal-overlay">
          <div className="contacts-modal" role="dialog" aria-modal="true" aria-label="联系人管理">
            <header className="contacts-modal-header">
              <div>
                <h2>联系人管理</h2>
                <p>支持分页搜索、分组标签筛选和联系人资料维护。</p>
              </div>
              <button type="button" className="contacts-close-button" onClick={() => setShowContacts(false)} aria-label="关闭联系人管理">
                ×
              </button>
            </header>
            <div className="contacts-modal-toolbar">
              <div className="contacts-search-row">
                <label className="contacts-field contacts-search-field">
                  <span>搜索</span>
                  <input
                    className="contacts-search"
                    placeholder="姓名、邮箱、手机或备注"
                    value={contactQuery}
                    onChange={(event) => {
                      setContactQuery(event.target.value);
                      setContactPage(1);
                    }}
                  />
                </label>
                <label className="contacts-field">
                  <span>分组</span>
                  <select
                    value={contactGroupFilter}
                    onChange={(event) => {
                      setContactGroupFilter(event.target.value);
                      setContactPage(1);
                    }}
                  >
                    <option value="">全部分组</option>
                    {contactGroups.map((group) => (
                      <option key={group} value={group}>{group}</option>
                    ))}
                  </select>
                </label>
                <label className="contacts-field">
                  <span>标签</span>
                  <select
                    value={contactTagFilter}
                    onChange={(event) => {
                      setContactTagFilter(event.target.value);
                      setContactPage(1);
                    }}
                  >
                    <option value="">全部标签</option>
                    {contactTags.map((tag) => (
                      <option key={tag} value={tag}>{tag}</option>
                    ))}
                  </select>
                </label>
                <div className="contacts-toolbar-actions">
                  <button type="button" className="contacts-primary-button" onClick={startCreateContact}>新建联系人</button>
                  <button type="button" className="contacts-secondary-button" onClick={() => selectedContact && handleContactCompose(selectedContact)} disabled={!selectedContact}>写信</button>
                </div>
              </div>
              <div className="contacts-summary">
                <span>共 {filteredContacts.length} 条</span>
                <span>第 {safeContactPage} / {totalContactPages} 页</span>
              </div>
            </div>
            {contactsError ? <div className="auth-error" role="alert">{contactsError}</div> : null}
            <div className="contacts-modal-body">
              <section className="contacts-list-pane">
                <div className="contacts-list-head">
                  <h3>联系人列表</h3>
                  <span>{pagedContacts.length} / {filteredContacts.length}</span>
                </div>
                <ul className="contacts-list" aria-label="联系人列表">
                  {pagedContacts.map((contact) => {
                    const isSelected = (contact.id || contact.email) === (activeContact?.id || activeContact?.email);
                    return (
                      <li key={contact.id || contact.email} data-selected={isSelected ? 'true' : 'false'}>
                        <div className="contacts-list-item">
                          <button type="button" className="contacts-list-item-main" onClick={() => handleContactCompose(contact)} aria-label={contact.email}>
                            <div className="contacts-list-item-title">
                              <strong>{contact.name || contact.email}</strong>
                              {contact.source === 'recent' ? <span className="contacts-source-badge">最近</span> : null}
                            </div>
                            <span className="contacts-list-item-email">{contact.email}</span>
                            <span className="contacts-list-item-meta">
                              {contact.phone || '未填写手机'} · {(contact.groups || []).slice(0, 2).join('、') || '未分组'}
                            </span>
                          </button>
                          <button type="button" className="contacts-row-action" onClick={() => selectContact(contact)} aria-label={`编辑 ${contact.email}`}>
                            编辑
                          </button>
                        </div>
                      </li>
                    );
                  })}
                  {!pagedContacts.length ? <li className="contacts-empty">暂无联系人，先新建一条吧。</li> : null}
                </ul>
                <div className="contacts-pagination">
                  <button type="button" className="contacts-secondary-button" onClick={() => setContactPage((value) => Math.max(1, value - 1))} disabled={safeContactPage <= 1}>上一页</button>
                  <span>第 {safeContactPage} 页 / 共 {totalContactPages} 页</span>
                  <button type="button" className="contacts-secondary-button" onClick={() => setContactPage((value) => Math.min(totalContactPages, value + 1))} disabled={safeContactPage >= totalContactPages}>下一页</button>
                </div>
              </section>
              <section className="contacts-editor-pane">
                <div className="contacts-editor-head">
                  <div>
                    <h3>{contactEditorMode === 'edit' ? '编辑联系人' : '新建联系人'}</h3>
                    <p>{contactEditorMode === 'edit' ? '修改姓名、邮箱、手机、备注、分组和标签。' : '补全联系人基本信息后保存。'}</p>
                  </div>
                  {activeContact ? (
                    <button
                      type="button"
                      className="contacts-secondary-button"
                      onClick={() => handleContactCompose(activeContact)}
                    >
                      从此联系人写信
                    </button>
                  ) : null}
                </div>
                <div className="contacts-form-grid">
                  <label className="contacts-field">
                    <span>姓名</span>
                    <input value={contactDraft.name} onChange={(event) => setContactDraft((current) => ({ ...current, name: event.target.value }))} />
                  </label>
                  <label className="contacts-field">
                    <span>邮箱</span>
                    <input type="email" value={contactDraft.email} onChange={(event) => setContactDraft((current) => ({ ...current, email: event.target.value }))} />
                  </label>
                  <label className="contacts-field">
                    <span>手机</span>
                    <input value={contactDraft.phone || ''} onChange={(event) => setContactDraft((current) => ({ ...current, phone: event.target.value }))} />
                  </label>
                  <label className="contacts-field contacts-field-full">
                    <span>备注</span>
                    <textarea value={contactDraft.note || ''} onChange={(event) => setContactDraft((current) => ({ ...current, note: event.target.value }))} rows={5} />
                  </label>
                  <label className="contacts-field">
                    <span>分组</span>
                    <input
                      value={joinContactValues(contactDraft.groups)}
                      onChange={(event) => setContactDraft((current) => ({ ...current, groups: splitContactValues(event.target.value) }))}
                      placeholder="例如：客户、同事"
                    />
                  </label>
                  <label className="contacts-field">
                    <span>标签</span>
                    <input
                      value={joinContactValues(contactDraft.tags)}
                      onChange={(event) => setContactDraft((current) => ({ ...current, tags: splitContactValues(event.target.value) }))}
                      placeholder="例如：重点、上海"
                    />
                  </label>
                </div>
                <div className="contacts-editor-meta">
                  <span>最近联系：{activeContact?.last_used_at ? formatContactDate(activeContact.last_used_at) : '暂无'}</span>
                  <span>来源：{activeContact?.source === 'recent' ? '最近联系人' : activeContact ? '手动维护' : '未选择联系人'}</span>
                </div>
                <div className="contacts-editor-actions">
                  <button type="button" className="contacts-secondary-button" onClick={startCreateContact}>清空表单</button>
                  {activeContact ? (
                    <button
                      type="button"
                      className="contacts-secondary-button danger"
                      onClick={() => handleContactDelete(activeContact!)}
                      disabled={contactDeletingId === (activeContact!.id || activeContact!.email)}
                    >
                      {contactDeletingId === (activeContact!.id || activeContact!.email) ? '删除中...' : '删除'}
                    </button>
                  ) : null}
                  <button type="button" className="contacts-primary-button" onClick={handleContactSave} disabled={contactSaving}>
                    {contactSaving ? '保存中...' : '保存'}
                  </button>
                </div>
              </section>
            </div>
            <footer className="contacts-modal-footer">
              <button type="button" className="contacts-secondary-button" onClick={() => setShowContacts(false)}>关闭</button>
            </footer>
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
        draftId={composeDraftId}
        initialValues={composeInitialValues}
        from={accountEmail}
        onClose={() => { setIsComposing(false); setComposeInitialValues(null); setComposeDraftId(null); }}
        onSent={() => { loadMessages(); setIsComposing(false); setComposeInitialValues(null); setComposeDraftId(null); }}
      />
    </div>
  );
}
