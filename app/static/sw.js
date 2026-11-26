// Network only: diary pages hold health details, so nothing is cached on the device.
const offline = `<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1"><title>Offline · Food diary</title>
<body style="margin:0;font-family:sans-serif;background:#fafbf9;color:#232a26;display:grid;place-items:center;min-height:100vh;text-align:center;padding:24px">
<div><h1 style="font-size:24px">You're offline</h1><p>Connect to the internet to open your food diary.</p></div></body>`;
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", event => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", event => {
  if (event.request.mode !== "navigate") return;
  event.respondWith(fetch(event.request).catch(() => new Response(offline, {headers: {"Content-Type": "text/html; charset=utf-8"}})));
});

// Stress alerts arrive as Web Push messages: {title, body, url, tag}. The badge is the small status-bar icon on
// Android: white on transparent, since only its shape is used (without it Chrome shows a bell).
self.addEventListener("push", event => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch {}
  event.waitUntil(self.registration.showNotification(data.title || "Food diary", {
    body: data.body || "", tag: data.tag, renotify: Boolean(data.tag), icon: "/static/icon-192.png", badge: "/static/badge-96.png", data: {url: data.url || "/"},
  }));
});
self.addEventListener("notificationclick", event => {
  event.notification.close();
  const url = new URL(event.notification.data?.url || "/", self.location.origin).href;
  event.waitUntil(self.clients.matchAll({type: "window", includeUncontrolled: true}).then(async windows => {
    const open = windows.find(w => new URL(w.url).origin === self.location.origin);
    if (open) { await open.focus(); return open.navigate(url).catch(() => null); }
    return self.clients.openWindow(url);
  }));
});
