// Offline informational page only. Never cache API data, authenticated HTML, or credentials.
const CACHE='reconcile-offline-v3';
self.addEventListener('install',e=>{e.waitUntil(caches.open(CACHE).then(c=>c.add('/offline.html')));self.skipWaiting();});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k.startsWith('reconcile-offline-')&&k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim()));});
self.addEventListener('fetch',e=>{if(e.request.mode==='navigate'&&!new URL(e.request.url).pathname.startsWith('/api/'))e.respondWith(fetch(e.request).catch(()=>caches.match('/offline.html')));});
