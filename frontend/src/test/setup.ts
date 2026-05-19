import { afterEach, vi } from 'vitest';
import '@testing-library/jest-dom/vitest';

const storageState = new Map<string, string>();

function createStorage() {
  return {
    getItem(key: string) {
      return storageState.has(key) ? storageState.get(key)! : null;
    },
    setItem(key: string, value: string) {
      storageState.set(key, String(value));
    },
    removeItem(key: string) {
      storageState.delete(key);
    },
    clear() {
      storageState.clear();
    },
  };
}

Object.defineProperty(window, 'localStorage', {
  configurable: true,
  value: createStorage(),
});

afterEach(() => {
  vi.restoreAllMocks();
  window.history.replaceState({}, '', '/');
  window.sessionStorage.clear();
  window.localStorage.clear();
});
