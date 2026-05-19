import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider } from 'react-router-dom';
import './styles.css';
import { AdminAuthProvider } from './admin/auth';
import { createAppRouter } from './router';

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
      <RouterProvider router={createAppRouter()} />
    </AdminAuthProvider>
  </QueryClientProvider>,
);
