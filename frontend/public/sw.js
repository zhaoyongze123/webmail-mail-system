self.addEventListener('push', (event) => {
  if (!event.data) {
    return;
  }

  let payload = {};
  try {
    payload = event.data.json();
  } catch {
    payload = { title: '新邮件到达', body: event.data.text() };
  }

  const title = payload.title || '新邮件到达';
  const body = payload.body || '你有一封新的邮件，请点击查看。';
  const tag = payload.tag || `mail-${payload.uid || Date.now()}`;
  const data = {
    folder: payload.folder || null,
    uid: payload.uid || null,
    messageId: payload.message_id || null,
    message_id: payload.message_id || null,
    subject: payload.subject || null,
    url: payload.url || '/',
  };

  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      icon: '/favicon.ico',
      badge: '/favicon.ico',
      tag,
      renotify: true,
      data,
    }),
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  const url = data.url || '/';

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      const existing = clients.find((client) => 'focus' in client);
      if (existing) {
        if ('navigate' in existing) {
          return existing.navigate(url).then(() => existing.focus());
        }
        return existing.focus();
      }
      return self.clients.openWindow(url);
    }),
  );
});
