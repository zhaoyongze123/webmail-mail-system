import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { fetchSettings, saveSettings } from './mail/api';
import ComposePanel, { type ComposeValues } from './mail/ComposePanel';
import MailWorkspace from './mail/MailWorkspace';
import MessageReader, { type MessageDetail } from './mail/MessageReader';
import type { UserSettingsPreferences } from './mail/types';

type SessionUser = {
  email: string;
};

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

type View = 'login' | 'mail' | 'settings' | 'error';

type SessionState =
  | {
      status: 'loading';
      user: null;
      error: string | null;
    }
  | {
      status: 'anonymous';
      user: null;
      error: string | null;
    }
  | {
      status: 'authenticated';
      user: SessionUser;
      error: string | null;
    };

type LoginFormState = {
  email: string;
  password: string;
  remember: boolean;
};

type LoginFormErrors = {
  email?: string;
  password?: string;
  form?: string;
};

type SettingsFormState = {
  page_size: string;
  mark_read_on_open: boolean;
};

type SettingsFormErrors = {
  page_size?: string;
};

const SESSION_KEY = 'webmail.session';
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS', 'TRACE']);
const CSRF_COOKIE_NAME = 'webmail_csrf';

function readSessionStorage(): SessionUser | null {
  try {
    const raw = window.sessionStorage.getItem(SESSION_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as Partial<SessionUser>;
    if (typeof parsed.email === 'string' && parsed.email) {
      return { email: parsed.email };
    }
  } catch {
    return null;
  }
  return null;
}

function persistSession(user: SessionUser | null) {
  if (!user) {
    window.sessionStorage.removeItem(SESSION_KEY);
    return;
  }
  window.sessionStorage.setItem(SESSION_KEY, JSON.stringify(user));
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
  const method = (init?.method || 'GET').toUpperCase();
  const headers = {
    'Content-Type': 'application/json',
    ...(init?.headers ?? {}),
  } as Record<string, string>;
  if (!SAFE_METHODS.has(method)) {
    const csrfToken = readCookie(CSRF_COOKIE_NAME);
    if (csrfToken) {
      headers['X-CSRF-Token'] = csrfToken;
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
    const error = new Error(message) as Error & { code?: string };
    error.code = payload.error?.code;
    throw error;
  }
  return payload.data as T;
}

async function getCurrentUser(): Promise<SessionUser> {
  return requestApi<SessionUser>('/api/auth/me', { method: 'GET' });
}

async function loginApi(payload: LoginFormState): Promise<SessionUser> {
  return requestApi<SessionUser>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

async function logoutApi(): Promise<void> {
  await requestApi<{ logged_out: boolean }>('/api/auth/logout', { method: 'POST' });
}

function useSession() {
  const [session, setSession] = useState<SessionState>(() => ({
    status: 'loading',
    user: null,
    error: null,
  }));

  useEffect(() => {
    let cancelled = false;

    async function syncSession() {
      try {
        const user = await getCurrentUser();
        if (cancelled) {
          return;
        }
        persistSession(user);
        setSession({ status: 'authenticated', user, error: null });
      } catch (error) {
        if (cancelled) {
          return;
        }
        const message = error instanceof Error ? error.message : '会话失效，请重新登录';
        persistSession(null);
        setSession({ status: 'anonymous', user: null, error: message });
      }
    }

    syncSession();

    return () => {
      cancelled = true;
    };
  }, []);

  const actions = useMemo(
    () => ({
      async signIn(form: LoginFormState) {
        const user = await loginApi(form);
        persistSession(user);
        setSession({ status: 'authenticated', user, error: null });
        return user;
      },
      async signOut() {
        try {
          await logoutApi();
        } finally {
          persistSession(null);
          setSession({ status: 'anonymous', user: null, error: '已退出登录，请重新登录' });
        }
      },
      markExpired(message: string) {
        persistSession(null);
        setSession({ status: 'anonymous', user: null, error: message });
      },
    }),
    [],
  );

  return { session, actions };
}

function parseView(pathname: string): View {
  if (pathname.startsWith('/settings')) {
    return 'settings';
  }
  if (pathname.startsWith('/error')) {
    return 'error';
  }
  if (pathname.startsWith('/mail')) {
    return 'mail';
  }
  return 'login';
}

function useLocationState() {
  const [path, setPath] = useState(() => window.location.pathname || '/login');

  useEffect(() => {
    const onPopState = () => {
      setPath(window.location.pathname || '/login');
    };
    window.addEventListener('popstate', onPopState);
    return () => {
      window.removeEventListener('popstate', onPopState);
    };
  }, []);

  const navigate = (nextPath: string) => {
    if (nextPath === window.location.pathname) {
      setPath(nextPath);
      return;
    }
    window.history.pushState({}, '', nextPath);
    setPath(nextPath);
  };

  return { path, navigate };
}

function AppHeader({
  email,
  onNavigate,
  onLogout,
}: {
  email: string;
  onNavigate: (path: string) => void;
  onLogout: () => void;
}) {
  return (
    <header className="app-header">
      <div>
        <p className="eyebrow">Webmail MVP</p>
        <h1>邮件工作台</h1>
      </div>
      <div className="header-actions">
        <button type="button" className="ghost-button" onClick={() => onNavigate('/mail')}>
          邮件
        </button>
        <button type="button" className="ghost-button" onClick={() => onNavigate('/settings')}>
          设置
        </button>
        <div className="account-badge" aria-label="当前账号">
          {email}
        </div>
        <button type="button" className="primary-button" onClick={onLogout}>
          退出登录
        </button>
      </div>
    </header>
  );
}

function LoginPage({
  initialError,
  onLogin,
}: {
  initialError: string | null;
  onLogin: (form: LoginFormState) => Promise<void>;
}) {
  const [form, setForm] = useState<LoginFormState>({
    email: '',
    password: '',
    remember: false,
  });
  const [errors, setErrors] = useState<LoginFormErrors>({});
  const [status, setStatus] = useState<'idle' | 'submitting' | 'error'>('idle');
  const [message, setMessage] = useState<string | null>(initialError);

  useEffect(() => {
    if (initialError) {
      setMessage(initialError);
    }
  }, [initialError]);

  const validate = () => {
    const nextErrors: LoginFormErrors = {};
    if (!form.email.trim()) {
      nextErrors.email = '请输入邮箱地址';
    } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email.trim())) {
      nextErrors.email = '邮箱格式不正确';
    }
    if (!form.password) {
      nextErrors.password = '请输入密码';
    }
    setErrors(nextErrors);
    return Object.keys(nextErrors).length === 0;
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setMessage(null);
    if (!validate()) {
      return;
    }
    setStatus('submitting');
    try {
      await onLogin({
        email: form.email.trim().toLowerCase(),
        password: form.password,
        remember: form.remember,
      });
    } catch (error) {
      const text = error instanceof Error ? error.message : '登录失败';
      setStatus('error');
      setMessage(text);
    } finally {
      setStatus((current) => (current === 'submitting' ? 'idle' : current));
    }
  };

  return (
    <main className="auth-shell">
      <section className="auth-card">
        <div className="auth-copy">
          <p className="eyebrow">Webmail MVP</p>
          <h1>登录邮箱</h1>
          <p className="subtitle">使用现有邮箱账号进入邮件工作台。</p>
        </div>
        <form className="auth-form" onSubmit={submit} noValidate>
          <label className="field">
            <span>邮箱地址</span>
            <input
              name="email"
              type="email"
              autoComplete="username"
              value={form.email}
              onChange={(event) => setForm((current) => ({ ...current, email: event.target.value }))}
              aria-invalid={Boolean(errors.email)}
              aria-describedby={errors.email ? 'email-error' : undefined}
              placeholder="user@example.com"
            />
            {errors.email ? (
              <small id="email-error" className="field-error">
                {errors.email}
              </small>
            ) : null}
          </label>
          <label className="field">
            <span>密码</span>
            <input
              name="password"
              type="password"
              autoComplete="current-password"
              value={form.password}
              onChange={(event) => setForm((current) => ({ ...current, password: event.target.value }))}
              aria-invalid={Boolean(errors.password)}
              aria-describedby={errors.password ? 'password-error' : undefined}
              placeholder="请输入密码"
            />
            {errors.password ? (
              <small id="password-error" className="field-error">
                {errors.password}
              </small>
            ) : null}
          </label>
          <label className="checkbox-row">
            <input
              name="remember"
              type="checkbox"
              checked={form.remember}
              onChange={(event) => setForm((current) => ({ ...current, remember: event.target.checked }))}
            />
            <span>记住登录</span>
          </label>
          {message ? (
            <div className={`notice ${status === 'error' ? 'notice-error' : ''}`} role="alert">
              {message}
            </div>
          ) : null}
          <button type="submit" className="primary-button submit-button" disabled={status === 'submitting'}>
            {status === 'submitting' ? '登录中...' : '登录'}
          </button>
        </form>
      </section>
    </main>
  );
}

function messageKey(folder: string, uid: string) {
  return `${folder}:${uid}`;
}

function replyValues(message: MessageDetail): ComposeValues {
  const from = Array.isArray(message.from) ? message.from[0] : message.from;
  const email = typeof from === 'string' ? from : from?.email;
  return {
    to: email ? [email] : [],
    subject: message.subject?.startsWith('Re:') ? message.subject : `Re: ${message.subject || '无主题'}`,
    text_body: `\n\n---- 原始邮件 ----\n${message.text_body || ''}`,
  };
}

function forwardValues(message: MessageDetail): ComposeValues {
  return {
    subject: message.subject?.startsWith('Fwd:') ? message.subject : `Fwd: ${message.subject || '无主题'}`,
    text_body: `\n\n---- 转发邮件 ----\n${message.text_body || ''}`,
  };
}

function MailView({
  onSessionExpired,
  onOpenSettings,
}: {
  onSessionExpired: () => void;
  onOpenSettings: () => void;
}) {
  const [selectedFolder, setSelectedFolder] = useState<string | null>(null);
  const [selectedUid, setSelectedUid] = useState<string | null>(null);
  const [composeOpen, setComposeOpen] = useState(false);
  const [composeInitialValues, setComposeInitialValues] = useState<ComposeValues | null>(null);

  const selectedMessageKey = selectedFolder && selectedUid ? messageKey(selectedFolder, selectedUid) : null;

  const openCompose = (values: ComposeValues | null = null) => {
    setComposeInitialValues(values);
    setComposeOpen(true);
  };

  return (
    <>
      <section className="content-panel mail-panel">
        <MailWorkspace
          selectedMessageKey={selectedMessageKey}
          onOpenMessage={(uid, folder) => {
            setSelectedFolder(folder);
            setSelectedUid(uid);
          }}
          onCompose={() => openCompose()}
          onOpenSettings={onOpenSettings}
          renderReader={(context) => (
            <MessageReader
              folder={context?.folder ?? selectedFolder}
              uid={context?.uid ?? selectedUid}
              onSessionExpired={onSessionExpired}
              onReply={(message) => openCompose(replyValues(message))}
              onForward={(message) => openCompose(forwardValues(message))}
            />
          )}
        />
      </section>
      <ComposePanel
        open={composeOpen}
        initialValues={composeInitialValues}
        onClose={() => setComposeOpen(false)}
        onSent={() => {
          setComposeOpen(false);
          setComposeInitialValues(null);
        }}
        onSessionExpired={onSessionExpired}
      />
    </>
  );
}

function SettingsView({ email, onBack, onLogout }: { email: string; onBack: () => void; onLogout: () => void }) {
  const [form, setForm] = useState<SettingsFormState>({
    page_size: '30',
    mark_read_on_open: true,
  });
  const [errors, setErrors] = useState<SettingsFormErrors>({});
  const [message, setMessage] = useState<string | null>(null);
  const [status, setStatus] = useState<'loading' | 'idle' | 'saving' | 'error'>('loading');

  useEffect(() => {
    let cancelled = false;
    fetchSettings()
      .then((payload) => {
        if (cancelled) {
          return;
        }
        setForm({
          page_size: String(payload.preferences.page_size ?? 30),
          mark_read_on_open: Boolean(payload.preferences.mark_read_on_open),
        });
        setStatus('idle');
      })
      .catch((error) => {
        if (cancelled) {
          return;
        }
        setMessage(error instanceof Error ? error.message : '设置加载失败');
        setStatus('error');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const nextErrors: SettingsFormErrors = {};
    const pageSize = Number(form.page_size);
    if (!Number.isInteger(pageSize) || pageSize < 1 || pageSize > 100) {
      nextErrors.page_size = '每页邮件数必须在 1 到 100 之间';
    }
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) {
      return;
    }
    setStatus('saving');
    setMessage(null);
    try {
      const payload = await saveSettings({
        page_size: pageSize,
        mark_read_on_open: form.mark_read_on_open,
      } satisfies Partial<UserSettingsPreferences>);
      setForm({
        page_size: String(payload.preferences.page_size ?? pageSize),
        mark_read_on_open: Boolean(payload.preferences.mark_read_on_open),
      });
      setMessage('设置已保存');
      setStatus('idle');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '设置保存失败');
      setStatus('error');
    }
  };

  return (
    <section className="content-panel">
      <div className="panel-title-row">
        <div>
          <p className="eyebrow">设置</p>
          <h2>账号偏好</h2>
        </div>
        <div className="settings-actions">
          <button type="button" className="secondary-button" onClick={onBack}>
            返回邮件
          </button>
          <button type="button" className="ghost-button" onClick={onLogout}>
            退出登录
          </button>
        </div>
      </div>
      <dl className="settings-list">
        <div>
          <dt>当前账号</dt>
          <dd>{email}</dd>
        </div>
      </dl>
      <form className="settings-form" onSubmit={submit}>
        <label className="field">
          <span>每页邮件数</span>
          <input
            type="number"
            min={1}
            max={100}
            value={form.page_size}
            onChange={(event) => setForm((current) => ({ ...current, page_size: event.target.value }))}
          />
          {errors.page_size ? <small className="field-error">{errors.page_size}</small> : null}
        </label>
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={form.mark_read_on_open}
            onChange={(event) => setForm((current) => ({ ...current, mark_read_on_open: event.target.checked }))}
          />
          <span>打开邮件时自动标记为已读</span>
        </label>
        {message ? (
          <div className={`notice ${status === 'error' ? 'notice-error' : ''}`} role="status">
            {message}
          </div>
        ) : null}
        <div className="settings-actions">
          <button type="submit" className="primary-button" disabled={status === 'loading' || status === 'saving'}>
            {status === 'saving' ? '保存中...' : '保存设置'}
          </button>
        </div>
      </form>
    </section>
  );
}

function ErrorView({ message, onNavigate }: { message: string; onNavigate: (path: string) => void }) {
  return (
    <main className="auth-shell">
      <section className="auth-card">
        <div className="auth-copy">
          <p className="eyebrow">Webmail MVP</p>
          <h1>服务异常</h1>
          <p className="subtitle">{message}</p>
        </div>
        <div className="error-actions">
          <button type="button" className="primary-button" onClick={() => onNavigate('/login')}>
            返回登录
          </button>
          <button type="button" className="secondary-button" onClick={() => onNavigate('/mail')}>
            重试进入邮箱
          </button>
        </div>
      </section>
    </main>
  );
}

export default function App() {
  const { path, navigate } = useLocationState();
  const { session, actions } = useSession();
  const view = parseView(path);

  useEffect(() => {
    if (session.status === 'anonymous' && view !== 'login') {
      navigate('/login');
    }
  }, [navigate, session.status, view]);

  useEffect(() => {
    if (session.status === 'authenticated' && view === 'login') {
      navigate('/mail');
    }
  }, [navigate, session.status, view]);

  if (session.status === 'loading') {
    return (
      <main className="auth-shell">
        <section className="auth-card">
          <p className="eyebrow">Webmail MVP</p>
          <h1>正在检查会话</h1>
          <p className="subtitle">请稍候，正在同步登录状态。</p>
        </section>
      </main>
    );
  }

  if (view === 'error') {
    return <ErrorView message={session.error || '服务暂时不可用'} onNavigate={navigate} />;
  }

  if (session.status === 'anonymous') {
    return (
      <LoginPage
        initialError={session.error}
        onLogin={async (form) => {
          await actions.signIn(form);
          navigate('/mail');
        }}
      />
    );
  }

  return (
    <main className="app-shell">
      <AppHeader email={session.user.email} onNavigate={navigate} onLogout={() => actions.signOut().then(() => navigate('/login'))} />
      {view === 'settings' ? (
        <SettingsView
          email={session.user.email}
          onBack={() => navigate('/mail')}
          onLogout={() => actions.signOut().then(() => navigate('/login'))}
        />
      ) : (
        <MailView
          onSessionExpired={() => {
            actions.markExpired('登录已过期，请重新登录');
            navigate('/login');
          }}
          onOpenSettings={() => navigate('/settings')}
        />
      )}
    </main>
  );
}
