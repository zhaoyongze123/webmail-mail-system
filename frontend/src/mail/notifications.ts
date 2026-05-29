import {
  fetchNotificationStatus,
  deletePushSubscriptionRecord,
  fetchPushSubscriptionStatus,
  saveNotificationPreferences,
  savePushSubscriptionRecord,
} from './api';
import type {
  NotificationSubscriptionStatusPayload,
  PushSubscriptionPayload,
} from './types';

export const SERVICE_WORKER_PATH = '/sw.js';

export type NotificationCapability = 'supported' | 'unsupported';
export type NotificationActivationStatus =
  | 'idle'
  | 'checking'
  | 'enabled'
  | 'disabled'
  | 'denied'
  | 'unsupported'
  | 'error';

export type NotificationState = {
  capability: NotificationCapability;
  permission: NotificationPermission | 'unsupported';
  status: NotificationActivationStatus;
  message: string | null;
  subscriptionEndpoint: string | null;
  vapidPublicKey: string | null;
};

export const DEFAULT_NOTIFICATION_STATE: NotificationState = {
  capability: 'unsupported',
  permission: 'unsupported',
  status: 'unsupported',
  message: '当前浏览器不支持系统通知或后台推送。',
  subscriptionEndpoint: null,
  vapidPublicKey: null,
};

export function isNotificationSupported() {
  return typeof window !== 'undefined'
    && 'Notification' in window
    && 'serviceWorker' in navigator
    && 'PushManager' in window;
}

export async function loadNotificationStatus(): Promise<NotificationState> {
  if (!isNotificationSupported()) {
    return DEFAULT_NOTIFICATION_STATE;
  }
  const statusPayload = await fetchNotificationStatus().catch(() => null);
  const remote = await fetchPushSubscriptionStatus();
  const registration = await navigator.serviceWorker.getRegistration();
  const subscription = await registration?.pushManager.getSubscription();
  const permission = Notification.permission;
  const endpoint = subscription?.endpoint
    ?? remote.subscription?.endpoint
    ?? (statusPayload?.has_subscription ? remote.subscription?.endpoint ?? null : null);
  const status = deriveStatus(permission, endpoint);

  return {
    capability: 'supported',
    permission,
    status,
    message: buildStatusMessage(status),
    subscriptionEndpoint: endpoint,
    vapidPublicKey: remote.vapid_public_key,
  };
}

export async function enableSystemNotifications(): Promise<NotificationState> {
  if (!isNotificationSupported()) {
    return DEFAULT_NOTIFICATION_STATE;
  }
  const remote = await fetchPushSubscriptionStatus();
  if (!remote.vapid_public_key) {
    return {
      capability: 'supported',
      permission: Notification.permission,
      status: 'error',
      message: '通知公钥未配置，暂时无法启用系统通知。',
      subscriptionEndpoint: null,
      vapidPublicKey: null,
    };
  }

  const permission = await Notification.requestPermission();
  if (permission !== 'granted') {
    return {
      capability: 'supported',
      permission,
      status: permission === 'denied' ? 'denied' : 'disabled',
      message: permission === 'denied' ? '浏览器已拒绝通知权限，请在浏览器设置中手动开启。' : '通知权限未授予，系统提醒未启用。',
      subscriptionEndpoint: null,
      vapidPublicKey: remote.vapid_public_key,
    };
  }

  const registration = await navigator.serviceWorker.register(SERVICE_WORKER_PATH);
  const subscription = await ensurePushSubscription(registration, remote.vapid_public_key);
  await savePushSubscriptionRecord(serializePushSubscription(subscription));
  await saveNotificationPreferences({ enabled: true, permission_state: permission });

  return {
    capability: 'supported',
    permission,
    status: 'enabled',
    message: '已启用新邮件系统通知，浏览器关闭标签页后仍可接收提醒。',
    subscriptionEndpoint: subscription.endpoint,
    vapidPublicKey: remote.vapid_public_key,
  };
}

export async function disableSystemNotifications(): Promise<NotificationState> {
  if (!isNotificationSupported()) {
    return DEFAULT_NOTIFICATION_STATE;
  }
  const registration = await navigator.serviceWorker.getRegistration();
  const subscription = await registration?.pushManager.getSubscription();
  if (subscription) {
    await subscription.unsubscribe();
  }
  await deletePushSubscriptionRecord();
  await saveNotificationPreferences({ enabled: false, permission_state: Notification.permission });
  return {
    capability: 'supported',
    permission: Notification.permission,
    status: Notification.permission === 'denied' ? 'denied' : 'disabled',
    message: '已关闭新邮件系统通知。',
    subscriptionEndpoint: null,
    vapidPublicKey: null,
  };
}

export async function registerNotificationServiceWorker() {
  if (!isNotificationSupported()) {
    return null;
  }
  return navigator.serviceWorker.register(SERVICE_WORKER_PATH);
}

function deriveStatus(permission: NotificationPermission, endpoint: string | null): NotificationActivationStatus {
  if (permission === 'denied') {
    return 'denied';
  }
  if (permission === 'granted' && endpoint) {
    return 'enabled';
  }
  return 'disabled';
}

function buildStatusMessage(status: NotificationActivationStatus) {
  switch (status) {
    case 'enabled':
      return '已启用新邮件系统通知，浏览器关闭标签页后仍可接收提醒。';
    case 'denied':
      return '浏览器已拒绝通知权限，请在浏览器设置中手动开启。';
    case 'disabled':
      return '系统通知未启用，新邮件仅在页面内更新。';
    case 'unsupported':
      return '当前浏览器不支持系统通知或后台推送。';
    case 'error':
      return '系统通知初始化失败，请稍后重试。';
    default:
      return null;
  }
}

async function ensurePushSubscription(
  registration: ServiceWorkerRegistration,
  vapidPublicKey: string,
) {
  const existing = await registration.pushManager.getSubscription();
  if (existing) {
    return existing;
  }
  return registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: base64UrlToArrayBuffer(vapidPublicKey),
  });
}

function serializePushSubscription(subscription: PushSubscription): PushSubscriptionPayload {
  const json = subscription.toJSON();
  return {
    endpoint: subscription.endpoint,
    expiration_time: subscription.expirationTime ?? null,
    keys: {
      p256dh: json.keys?.p256dh || '',
      auth: json.keys?.auth || '',
    },
  };
}

function base64UrlToArrayBuffer(input: string): ArrayBuffer {
  const padding = '='.repeat((4 - (input.length % 4)) % 4);
  const normalized = (input + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = window.atob(normalized);
  const output = new Uint8Array(raw.length);
  for (let index = 0; index < raw.length; index += 1) {
    output[index] = raw.charCodeAt(index);
  }
  return output.buffer;
}

export function buildNotificationTargetUrl(data?: {
  folder?: string | null;
  uid?: string | null;
  message_id?: string | null;
}) {
  const params = new URLSearchParams();
  if (data?.folder) {
    params.set('folder', data.folder);
  }
  if (data?.uid) {
    params.set('uid', data.uid);
  }
  if (data?.message_id) {
    params.set('messageId', data.message_id);
  }
  const query = params.toString();
  return query ? `/?${query}` : '/';
}

export function parseNotificationTargetFromUrl(locationLike: Pick<Location, 'search'>) {
  const params = new URLSearchParams(locationLike.search);
  return {
    folder: params.get('folder'),
    uid: params.get('uid'),
    messageId: params.get('messageId'),
  };
}

export type NotificationStatusResponse = NotificationSubscriptionStatusPayload;
