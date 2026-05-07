import { afterEach, vi } from 'vitest';

afterEach(() => {
  vi.restoreAllMocks();
  window.history.replaceState({}, '', '/');
  window.sessionStorage.clear();
});
