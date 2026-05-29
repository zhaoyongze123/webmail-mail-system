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

class MockNotification {
  static permission: NotificationPermission = 'default';

  static requestPermission = vi.fn(async () => MockNotification.permission);
}

const pushSubscription = {
  endpoint: 'https://push.example.test/subscriptions/default',
  expirationTime: null,
  unsubscribe: vi.fn(async () => true),
  toJSON() {
    return {
      endpoint: this.endpoint,
      expirationTime: this.expirationTime,
      keys: {
        p256dh: 'p256dh-key',
        auth: 'auth-key',
      },
    };
  },
};

const pushManager = {
  getSubscription: vi.fn(async () => null),
  subscribe: vi.fn(async () => pushSubscription),
};

const serviceWorkerRegistration = {
  pushManager,
};

const serviceWorkerController = {
  register: vi.fn(async () => serviceWorkerRegistration),
  getRegistration: vi.fn(async () => serviceWorkerRegistration),
};

Object.defineProperty(window, 'Notification', {
  configurable: true,
  value: MockNotification,
});

Object.defineProperty(window, 'PushManager', {
  configurable: true,
  value: function PushManager() {},
});

Object.defineProperty(window.navigator, 'serviceWorker', {
  configurable: true,
  value: serviceWorkerController,
});

afterEach(() => {
  vi.restoreAllMocks();
  window.history.replaceState({}, '', '/');
  window.sessionStorage.clear();
  window.localStorage.clear();
  MockNotification.permission = 'default';
  MockNotification.requestPermission.mockClear();
  pushManager.getSubscription.mockClear();
  pushManager.subscribe.mockClear();
  pushSubscription.unsubscribe.mockClear();
  serviceWorkerController.register.mockClear();
  serviceWorkerController.getRegistration.mockClear();
});
