const CACHE='posting-navigator-v1.9.26';
const APP=['style.css?v=1.9.26','app.js?v=1.9.26','config.js?v=1.9.26','manifest.webmanifest','offline.html','icons/icon-192.png','icons/icon-512.png'];
self.addEventListener('install',event=>event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(APP)).then(()=>self.skipWaiting())));
self.addEventListener('activate',event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(key=>key.startsWith('posting-navigator-')&&key!==CACHE).map(key=>caches.delete(key)))).then(()=>self.clients.claim())));
self.addEventListener('fetch',event=>{
  const request=event.request;
  if(request.method!=='GET')return;
  const url=new URL(request.url);
  if(url.origin!==self.location.origin)return;
  if(url.pathname.includes('/api/')||url.pathname.includes('/download/'))return;
  // HTML and version checks must always hit the network; never serve stale UI.
  if(request.mode==='navigate'||url.pathname.endsWith('/index.html')||url.searchParams.has('_version_check')){
    event.respondWith(fetch(new Request(request,{cache:'no-store'})).catch(()=>caches.match('offline.html')));
    return;
  }
  event.respondWith(fetch(request).then(response=>{
    if(response.ok){const copy=response.clone();event.waitUntil(caches.open(CACHE).then(cache=>cache.put(request,copy)));}
    return response;
  }).catch(()=>caches.match(request).then(cached=>cached||caches.match('offline.html'))));
});
