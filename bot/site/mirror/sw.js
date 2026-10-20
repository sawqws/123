/* Service worker зеркала (Глеб 20.10: «сделай сайт плавнее сильно»).
   Страница задания спрашивает платформу по цепочке: задание → попытки → попытка… — каждый запрос ждёт предыдущий,
   и каждый — круг iPad ↔ сервер. Здесь: как только страница спросила задания темы (первое звено) или палец коснулся
   карточки, берём у сервера всю цепочку ОДНИМ ответом (/_hdp/bundle) и следующие запросы страницы отдаём из памяти.
   Всё остальное не трогаем (respondWith не зовём) — браузер идёт в сеть как обычно. Любой не-GET — память сбрасывается. */
const TTL = 20000;
const FIRST = /^\/api\/education\/v1\/students\/current\/topics\/([\w-]+)\/tasks\?taskUuid=([\w-]+)$/;
let packs = [];  // [{at, task, ready: Promise<{путь: [статус, тип, текст]}>}]

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

function start(topic, task) {
  const now = Date.now();
  packs = packs.filter(p => now - p.at < TTL);
  if (packs.some(p => p.task === task)) return;
  const ready = fetch(`/_hdp/bundle?t=${topic}&x=${task}`, { credentials: 'same-origin' })
    .then(r => (r.ok ? r.json() : {})).catch(() => ({}));
  packs.push({ at: now, task, ready });
}

self.addEventListener('message', e => {
  const d = e.data || {};
  if (d.hdpx === 'task' && /^[\w-]{8,64}$/.test(d.t || '') && /^[\w-]{8,64}$/.test(d.x || '')) start(d.t, d.x);
});

self.addEventListener('fetch', e => {
  const req = e.request;
  let url;
  try { url = new URL(req.url); } catch (err) { return; }
  if (url.origin !== location.origin || !/^\/api\/(education|profile)\//.test(url.pathname)) return;  // кнопки бота (/api/site…) не трогаем
  if (req.method !== 'GET') { packs = []; return; }  // действие на платформе — старое не отдаём
  const path = url.pathname + url.search;
  const m = FIRST.exec(path);
  if (m) { start(m[1], m[2]); return; }  // первое звено браузер берёт сам, цепочка уже едет следом
  const now = Date.now();
  const pack = packs.find(p => now - p.at < TTL && path.includes(p.task)) ||
    (path === '/api/profile/v1/users/preferences' ? packs.find(p => now - p.at < TTL) : null);
  if (!pack) return;
  e.respondWith(pack.ready.then(all => {
    const hit = all[path];
    if (!hit) return fetch(req);
    delete all[path];  // один раз: следующий запрос — уже свежий с платформы
    return new Response(hit[2], { status: hit[0], headers: { 'Content-Type': hit[1] } });
  }).catch(() => fetch(req)));
});
