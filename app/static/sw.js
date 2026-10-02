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
