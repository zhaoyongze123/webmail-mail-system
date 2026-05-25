import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider } from 'react-router-dom';
import './styles.css';
import { AdminAuthProvider } from './admin/auth';
import { createAppRouter } from './router';
import { RuntimeI18nBridge, setRuntimeLocale, DEFAULT_LOCALE } from './i18n/runtime';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
    mutations: {
      retry: 0,
    },
  },
});

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <QueryClientProvider client={queryClient}>
    <AdminAuthProvider>
      <RuntimeI18nBridge />
      <RouterProvider router={createAppRouter()} />
    </AdminAuthProvider>
  </QueryClientProvider>,
);

setRuntimeLocale(window.localStorage.getItem('webmail-admin-locale') || window.localStorage.getItem('webmail-user-locale') || DEFAULT_LOCALE);
