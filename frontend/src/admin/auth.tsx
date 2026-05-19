import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { adminLogout, getAdminSession } from './api';
import { clearAdminTokens, hasAdminToken, setAdminTokens } from './token';
import type { AdminUser } from './types';

type AdminAuthContextValue = {
  hasToken: boolean;
  user: AdminUser | null;
  syncFromLogin: (payload: { access_token: string; refresh_token?: string | null; user: AdminUser }) => void;
  signOut: () => Promise<void>;
  refreshSession: () => Promise<void>;
};

const AdminAuthContext = createContext<AdminAuthContextValue | null>(null);

export function AdminAuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AdminUser | null>(null);
  const [hasToken, setHasToken] = useState(hasAdminToken());

  useEffect(() => {
    void getAdminSession().then((session) => setHasToken(session.hasToken));
  }, []);

  const value = useMemo<AdminAuthContextValue>(() => ({
    hasToken,
    user,
    syncFromLogin(payload) {
      setAdminTokens(payload.access_token, payload.refresh_token ?? null);
      setUser(payload.user);
      setHasToken(true);
    },
    async signOut() {
      try {
        await adminLogout();
      } finally {
        clearAdminTokens();
        setUser(null);
        setHasToken(false);
      }
    },
    async refreshSession() {
      setHasToken(hasAdminToken());
    },
  }), [hasToken, user]);

  return <AdminAuthContext.Provider value={value}>{children}</AdminAuthContext.Provider>;
}

export function useAdminAuth() {
  const context = useContext(AdminAuthContext);
  if (!context) {
    throw new Error('useAdminAuth must be used within AdminAuthProvider');
  }
  return context;
}

export function RequireAdminAuth() {
  const auth = useAdminAuth();
  const location = useLocation();

  if (!auth.hasToken) {
    return <Navigate to="/admin/login" replace state={{ from: `${location.pathname}${location.search}` }} />;
  }

  return <Outlet />;
}

export function useAdminSignOut() {
  const auth = useAdminAuth();
  const navigate = useNavigate();

  return async () => {
    await auth.signOut();
    navigate('/admin/login', { replace: true });
  };
}
