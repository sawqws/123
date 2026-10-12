// Авито — мини-приложение: объявления (фото на белом, название, описание, цена) и средняя цена.
const tg = window.Telegram && window.Telegram.WebApp;
if (tg) { tg.ready(); tg.expand(); }
const $ = (id) => document.getElementById(id);
const haptic = (kind) => {
  const h = tg && tg.HapticFeedback;
  if (!h) return;
  if (kind === 'tap') h.impactOccurred('light'); else h.notificationOccurred(kind);
};
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const rub = (x) => (x == null ? '' : Math.round(x).toLocaleString('ru-RU') + ' ₽');
const auth = () => ({ 'X-Init-Data': (tg && tg.initData) || '' });

async function api(path, body) {
  const headers = auth();
  if (body) headers['Content-Type'] = 'application/json';
  const r = await fetch(path, { method: body ? 'POST' : 'GET', headers, body: body ? JSON.stringify(body) : undefined });
  let data = {};
  try { data = await r.json(); } catch (e) { /* пустой ответ */ }
  if (!r.ok) { const err = new Error(data.error || ('Ошибка ' + r.status)); err.data = data; throw err; }
  return data;
}

let toastTimer;
function toast(text, err) {
  const t = $('toast');
  t.textContent = text;
  t.className = 'toast show' + (err ? ' err' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = 'toast' + (err ? ' err' : ''); }, 2200);
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
  }
  haptic('success');
  toast('Скопировано');
}

let ads = [];
const byId = (id) => ads.find((a) => a.id === id);

function statsHtml(m, url, adId) {
  if (!m || !m.n) {
    return `<div class="stats"><div class="line">Похожих не нашлось${m ? ` (просмотрел ${m.seen})` : ''}. Уточни запрос.</div>` +
      (url ? `<a href="${esc(url)}" data-link>Открыть поиск на Авито</a>` : '') + '</div>';
  }
  const pick = (cls, label, v) => adId
    ? `<button class="${cls}" data-price="${v}" data-ad="${esc(adId)}"><b>${rub(v)}</b><span>${label}</span></button>`
    : `<button class="${cls}" disabled><b>${rub(v)}</b><span>${label}</span></button>`;
  return `<div class="stats"><div class="label">Медиана</div><div class="median">${rub(m.median)}</div>` +
    `<div class="line">${m.n} похожих из ${m.seen} · ${rub(m.min).replace(' ₽', '')} – ${rub(m.max)}</div>` +
    `<div class="prices">${pick('', 'быстро', m.fast)}${pick('mid', 'медиана', m.median)}${pick('', 'дороже', m.high)}</div>` +
    `<div class="line label">«${esc(m.query)}»${m.when ? ' · ' + esc(m.when) : ''}${adId ? ' · нажми цену — поставлю' : ''}</div>` +
    (url ? `<a href="${esc(url)}" data-link>Открыть поиск на Авито</a>` : '') + '</div>';
}

function adHtml(a) {
  const n = a.photos.length;
  const shown = a.photos.slice(0, n === 1 ? 1 : 4);
  const cols = n === 1 ? ' one' : n < 4 ? ` c${n}` : '';
  const photos = n ? `<div class="photos${cols}">` + shown.map((src, i) =>
    `<div class="ph"><img data-src="${esc(src)}" alt="">${i === 3 && n > 4 ? `<div class="more">+${n - 4}</div>` : ''}</div>`).join('') + '</div>' : '';
  const price = a.price ? `<div class="price">${esc(a.price_text)}</div>` : '<div class="price none">Цена не указана</div>';
  const status = a.status === 'live' ? '🟢 на Авито' : a.status === 'sold'
    ? '✅ продано' + (a.sold_price ? ' за ' + a.sold_price.toLocaleString('ru') + ' ₽' : '') : '';
  const meta = [status, a.category, n ? `на белом ${a.clean} из ${n}` : '', a.floor ? `торг до ${a.floor.toLocaleString('ru')} ₽` : '']
    .filter(Boolean).map(esc).join(' · ');
  const desc = a.description
    ? `<div class="desc"><div class="txt" id="d-${esc(a.id)}">${esc(a.description)}</div></div>` +
      (a.description.length > 260 || a.description.split('\n').length > 6 ? `<button class="more-link" data-more="${esc(a.id)}">Показать полностью</button>` : '')
    : '<div class="meta" style="margin-top:8px">Описания пока нет — расскажи о вещи в чате.</div>';
  return `<article class="card" data-id="${esc(a.id)}">${photos}` +
    `<div class="ad-head"><h2 class="ad-title">${esc(a.title || 'Без названия')}</h2>` +
    (a.current ? '<span class="badge now">сейчас</span>' : '') + '</div>' +
    price + (meta ? `<div class="meta">${meta}</div>` : '') + desc +
    '<div class="actions">' +
    (a.description ? `<button class="btn primary" data-copy="desc" data-ad="${esc(a.id)}">Копировать</button>` : '') +
    (a.title ? `<button class="btn" data-copy="title" data-ad="${esc(a.id)}">Название</button>` : '') +
    `<button class="btn wide" data-market="${esc(a.id)}">Средняя цена</button>` +
    (a.url ? `<a class="btn wide" href="${esc(a.url)}" target="_blank" rel="noopener">Открыть на Авито</a>` : '') +
    '</div>' +
    `<div class="market" id="m-${esc(a.id)}">${a.market ? statsHtml(a.market, '', a.id) : ''}</div></article>`;
}

// Фото отдаются только с подписью Telegram — грузим через fetch и показываем blob
const blobCache = {};
async function loadImages() {
  for (const img of document.querySelectorAll('img[data-src]')) {
    const src = img.dataset.src;
    img.removeAttribute('data-src');
    try {
      if (!blobCache[src]) {
        const r = await fetch(src, { headers: auth() });
        if (!r.ok) continue;
        blobCache[src] = URL.createObjectURL(await r.blob());
      }
      img.src = blobCache[src];
      img.onload = () => img.classList.add('on');
    } catch (e) { /* без фото */ }
  }
}

function render(state) {
  ads = state.ads || [];
  $('region').textContent = 'Регион: ' + (state.region || 'вся Россия');
  $('ads').innerHTML = ads.length ? ads.map(adHtml).join('')
    : '<div class="card empty">Объявлений пока нет. Пришли фото вещи в чат — уберу фон и напишу описание.</div>';
  loadImages();
}

async function load() {
  try { render(await api('/api/state')); } catch (e) {
    $('ads').innerHTML = `<div class="card empty">${esc(e.message)}</div>`;
  }
}

async function market(btn, body, box, adId) {
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Ищу…';
  box.innerHTML = '<div class="stats"><div class="line label">Смотрю до 100 объявлений — это до минуты.</div></div>';
  try {
    const res = await api('/api/market', body);
    box.innerHTML = statsHtml(res.market, res.url, adId);
    if (res.ad) { const i = ads.findIndex((a) => a.id === res.ad.id); if (i >= 0) ads[i] = res.ad; }
    haptic('success');
  } catch (e) {
    box.innerHTML = `<div class="stats"><div class="line err">${esc(e.message)}</div>` +
      (e.data && e.data.url ? `<a href="${esc(e.data.url)}" data-link>Открыть поиск на Авито</a>` : '') + '</div>';
    haptic('error');
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}

$('search').addEventListener('submit', (ev) => {
  ev.preventDefault();
  const query = $('query').value.trim();
  if (!query) { haptic('error'); toast('Напиши, что ищем', true); return; }
  haptic('tap');
  $('query').blur();
  market($('search-go'), { query }, $('search-result'), '');
});

document.addEventListener('click', async (ev) => {
  const t = ev.target.closest('[data-copy],[data-market],[data-price],[data-more],[data-link]');
  if (!t) return;
  if (t.dataset.link !== undefined) {
    ev.preventDefault();
    if (tg && tg.openLink) tg.openLink(t.href); else window.open(t.href, '_blank');
    return;
  }
  if (t.dataset.more) {
    const d = $('d-' + t.dataset.more);
    d.classList.toggle('open');
    t.textContent = d.classList.contains('open') ? 'Свернуть' : 'Показать полностью';
    haptic('tap');
    return;
  }
  if (t.dataset.copy) {
    const a = byId(t.dataset.ad);
    if (a) copy(t.dataset.copy === 'title' ? a.title : a.description);
    return;
  }
  if (t.dataset.market) {
    haptic('tap');
    market(t, { id: t.dataset.market, force: !!(byId(t.dataset.market) || {}).market }, $('m-' + t.dataset.market), t.dataset.market);
    return;
  }
  if (t.dataset.price) {
    haptic('tap');
    try {
      const res = await api('/api/price', { id: t.dataset.ad, price: Number(t.dataset.price) });
      const i = ads.findIndex((a) => a.id === res.ad.id);
      if (i >= 0) ads[i] = res.ad;
      const card = document.querySelector(`article[data-id="${CSS.escape(res.ad.id)}"] .price`);
      if (card) { card.className = 'price'; card.textContent = res.ad.price_text; }
      haptic('success');
      toast('Цена ' + res.ad.price_text);
    } catch (e) { haptic('error'); toast(e.message, true); }
  }
});

load();
