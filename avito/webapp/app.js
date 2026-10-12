// Авито — мини-приложение. Главный экран: загрузить фото → белый фон + описание → средняя цена.
// Вкладки: Объявление, Цены (любой запрос), Все (прошлые объявления). Сервер — avito/avito.py.
const { tg, $, esc, api, toast, haptic, sheet, busy } = Kit;

const rub = (x) => (x == null ? '' : Math.round(x).toLocaleString('ru-RU') + ' ₽');
const auth = () => ({ 'X-Init-Data': (tg && tg.initData) || '' });
const MAX_PHOTOS = 10;

let state = { ads: [], region: 'all', region_name: 'вся Россия' };
let tab = 'ad';
let pollTimer = null;
let marketBusy = '';      // id объявления, для которого сейчас ищем цену
let uploading = null;     // {done, total} — пока грузим фото
const searchResult = { m: null, url: '' };

const current = () => state.ads.find((a) => a.current) || null;
const byId = (id) => state.ads.find((a) => a.id === id);

// ---------- общее ----------

async function copy(text, what) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;opacity:0';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
  }
  haptic('success');
  toast((what || 'Текст') + ' скопирован', 'ok');
}

function openLink(url) {
  if (tg && tg.openLink) tg.openLink(url); else window.open(url, '_blank');
}

// Фото отдаются только с подписью Telegram — грузим через fetch и показываем blob
const blobs = {};
async function loadImages(root) {
  for (const img of (root || document).querySelectorAll('img[data-src]')) {
    const src = img.dataset.src;
    img.removeAttribute('data-src');
    try {
      if (!blobs[src]) {
        const r = await fetch(src, { headers: auth() });
        if (!r.ok) continue;
        blobs[src] = URL.createObjectURL(await r.blob());
      }
      img.onload = () => img.classList.add('on');
      img.src = blobs[src];
    } catch (e) { /* без фото */ }
  }
}

function statusPill(a) {
  if (a.status === 'sold') return `<span class="pill ok">Продано${a.sold_price ? ' · ' + rub(a.sold_price) : ''}</span>`;
  if (a.status === 'live') return '<span class="pill ok dot">На Авито</span>';
  return '';
}

// ---------- средняя цена ----------

function histogram(m) {
  const lo = m.min;
  const hi = m.max;
  const prices = (m.prices || []).filter((p) => p >= lo && p <= hi);
  if (prices.length < 3 || hi <= lo) return '';
  const bins = Math.max(8, Math.min(18, Math.round(Math.sqrt(prices.length) * 2.2)));
  const counts = new Array(bins).fill(0);
  for (const p of prices) counts[Math.min(bins - 1, Math.floor(((p - lo) / (hi - lo)) * bins))] += 1;
  const top = Math.max(...counts);
  const pos = (v) => Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100));
  const bars = counts.map((c, i) => {
    const mid = lo + ((i + 0.5) / bins) * (hi - lo);
    const zone = mid >= m.fast && mid <= m.high ? ' in' : '';
    return `<i class="${zone}" style="height:${c ? Math.max(8, (c / top) * 100) : 3}%"></i>`;
  }).join('');
  return `<div class="hist" aria-hidden="true"><div class="bars">${bars}</div>` +
    `<div class="med" style="left:${pos(m.median)}%"></div></div>` +
    `<div class="axis"><span>${rub(lo)}</span><span>${rub(hi)}</span></div>`;
}

function marketHtml(m, ad, url) {
  if (!m || !m.n) {
    return `<div class="empty"><span class="e">🔍</span><b>Похожих не нашлось</b>` +
      `${m ? `Просмотрел ${m.seen} объявлений. ` : ''}Уточни запрос — поищу снова.` +
      (url ? `<button class="btn ghost" data-link="${esc(url)}">Открыть поиск на Авито</button>` : '') + '</div>';
  }
  const pick = (cls, icon, label, v) => {
    const on = ad && ad.price === v ? ' on' : '';
    return `<button class="pick ${cls}${on}" ${ad ? `data-price="${v}" data-ad="${esc(ad.id)}"` : 'disabled'}>` +
      `<span class="k"><i>${icon} </i>${label}</span><b>${rub(v)}</b></button>`;
  };
  const sample = (m.sample || []).slice().sort((x, y) => Math.abs(x.p - m.median) - Math.abs(y.p - m.median)).slice(0, 6);
  return `<div class="mk">
    <div class="mk-head">
      <div class="main"><div class="k">Медиана на Авито</div><div class="big-num">${rub(m.median)}</div></div>
      <span class="pill accent">${m.n} из ${m.seen}</span>
    </div>
    ${histogram(m)}
    <div class="picks">${pick('', '⚡', 'Быстро', m.fast)}${pick('mid', '🎯', 'Медиана', m.median)}${pick('', '💎', 'Дороже', m.high)}</div>
    ${ad ? '<p class="hint center">Нажми цену — поставлю в объявление</p>' : ''}
    ${sample.length ? `<details class="similar"><summary>Похожие объявления</summary><div class="list plain">` +
      sample.map((s) => `<button class="row" data-link="${esc(s.u)}"><div class="main"><div class="title">${esc(s.t)}</div></div>` +
        `<div class="end">${rub(s.p)}</div></button>`).join('') + '</div></details>' : ''}
    <div class="mk-foot">
      <span class="hint clip">«${esc(m.query)}»${m.when ? ' · ' + esc(m.when) : ''}</span>
      ${url ? `<button class="btn sm ghost" data-link="${esc(url)}">Авито ↗</button>` : ''}
    </div>
  </div>`;
}

function marketLoading() {
  return `<div class="mk"><div class="mk-head"><div class="main"><div class="k">Ищу похожие на Авито…</div>
    <div class="sk" style="height:44px;width:58%;margin-top:6px"></div></div><span class="pill live dot accent">до минуты</span></div>
    <div class="hist sk-hist">${'<i></i>'.repeat(14)}</div>
    <div class="picks"><div class="sk" style="height:58px"></div><div class="sk" style="height:58px"></div><div class="sk" style="height:58px"></div></div></div>`;
}

async function findPrice(body, btn) {
  const ad = body.id ? byId(body.id) : null;
  if (ad) { marketBusy = ad.id; render(); }
  else { $('q-result').innerHTML = `<div class="card">${marketLoading()}</div>`; }
  try {
    const res = await busy(btn, () => api('/api/market', body));
    if (res.ad) replaceAd(res.ad);
    else { searchResult.m = res.market; searchResult.url = res.url; }
    haptic('success');
  } catch (e) {
    haptic('error');
    if (ad) toast(e.message, true);
    else {
      searchResult.m = null;
      $('q-result').innerHTML = `<div class="note err">⚠️ ${esc(e.message)}</div>` +
        (e.data && e.data.url ? `<button class="btn ghost" data-link="${esc(e.data.url)}">Открыть поиск на Авито</button>` : '');
      return;
    }
  } finally {
    marketBusy = '';
  }
  render();
}

// ---------- экран «Объявление» ----------

function steps(a) {
  const s1 = a && a.photos.length ? (a.pending ? 'now' : 'done') : (a && a.title ? 'skip' : 'now');
  const s2 = a && a.title ? 'done' : (a && (a.job === 'write' || a.photos.length || a.info) ? 'now' : '');
  const s3 = a && a.price ? 'done' : (a && a.title ? 'now' : '');
  const item = (st, n, label) => `<div class="step ${st}"><span class="n">${st === 'done' ? '✓' : n}</span>${label}</div>`;
  return `<div class="steps">${item(s1, 1, 'Фото')}<i></i>${item(s2, 2, 'Описание')}<i></i>${item(s3, 3, 'Цена')}</div>`;
}

function startCard() {
  return `<div class="card feature start rise">
    <div class="drop" data-act="upload"><span>📷</span></div>
    <h2 class="title-lg">Новое объявление</h2>
    <p class="hint lead">Сфотографируй вещь — уберу фон, напишу описание и подскажу цену.</p>
    <button class="btn primary" data-act="upload">📷 Загрузить фото</button>
    <button class="btn tonal" id="describe" data-act="describe">✍️ Описать словами</button>
  </div>
  <p class="hint center">В чате тоже можно: фото, видео или голосовое.</p>`;
}

function photosCard(a) {
  const n = a.photos.length;
  if (!n) {
    return `<div class="card rise"><div class="card-row"><div><div class="k-title">Фото</div>
      <div class="hint">Без фото объявление смотрят реже</div></div>
      <button class="btn sm tonal" data-act="upload">📷 Добавить</button></div></div>`;
  }
  const tile = (p, i) => `<div class="ph ${p.state}${i === 0 ? ' big' : ''}" data-view="${i}">` +
    `<img data-src="${esc(p.url)}" alt="">` +
    (p.state === 'wait' ? `<span class="tag live">${i === 0 ? 'Убираю фон…' : 'фон…'}</span>` : p.state === 'err' ? '<span class="tag">оригинал</span>' : '') +
    '</div>';
  // большое первое фото, справа — остальные; «＋» добивает пустые клетки
  const more = n < MAX_PHOTOS ? `<button class="ph add${n === 3 ? ' wide' : ''}" data-act="upload" aria-label="Добавить фото">＋</button>` : '';
  const layout = n === 1 && !more ? 'solo' : n + (more ? 1 : 0) <= 3 ? 'duo' : 'grid';
  const status = uploading ? `Загружаю ${uploading.done} из ${uploading.total}…`
    : a.pending ? `Убираю фон · ${a.pending} фото` : `${a.clean} из ${n} на белом фоне`;
  return `<div class="card rise">
    <div class="card-row"><div class="main"><div class="k-title">Фото</div><div class="hint">${esc(status)}</div></div>
      <button class="btn sm tonal" data-act="send" ${a.pending ? 'disabled' : ''}>📤 В чат</button></div>
    <div class="gallery ${layout}">${a.photos.map(tile).join('')}${more}</div>
  </div>`;
}

function descCard(a) {
  if (a.job === 'write' || (a.job === 'bg' && !a.title)) {
    return `<div class="card rise"><div class="card-row"><div class="k-title">Описание</div>
      <span class="pill accent live dot">${a.job === 'bg' ? 'после фона' : 'пишу…'}</span></div>
      <div class="sk" style="height:22px;width:72%;margin-top:12px"></div>
      <div class="sk" style="height:14px;margin-top:14px"></div><div class="sk" style="height:14px;margin-top:8px"></div>
      <div class="sk" style="height:14px;width:80%;margin-top:8px"></div></div>`;
  }
  const err = a.error ? `<div class="note err">⚠️ ${esc(a.error)}</div>` : '';
  if (!a.title) {
    return `<div class="card rise"><div class="k-title">Описание</div>
      <p class="hint">${a.photos.length ? 'Опишу по фото — или расскажи пару слов: бренд, размер, состояние.' : 'Расскажи, что продаёшь: бренд, размер, состояние.'}</p>
      ${err}
      ${a.photos.length ? '<button class="btn primary" data-act="gen">✨ Описать по фото</button>' : ''}
      <button class="btn tonal" data-act="describe">✍️ Рассказать о вещи</button></div>`;
  }
  const longDesc = a.description.length > 280 || a.description.split('\n').length > 6;
  const qs = (a.questions || []).map((q, i) => `<button class="q" data-q="${i}"><span>❓</span>${esc(q)}<span class="chev"></span></button>`).join('');
  return `<div class="card rise desc-card">
    <div class="card-row top"><h2 class="ad-title">${esc(a.title)}</h2>
      <button class="btn icon" data-copy="title" aria-label="Скопировать название">⧉</button></div>
    <div class="tags">${statusPill(a)}${a.category ? `<span class="pill">${esc(a.category)}</span>` : ''}</div>
    <div class="desc${longDesc ? ' clamp' : ''}" id="desc">${esc(a.description)}</div>
    ${longDesc ? '<button class="more-link" data-act="more">Показать полностью</button>' : ''}
    ${qs ? `<div class="qs"><div class="hint">Ответь — описание станет точнее</div>${qs}</div>` : ''}
    ${err}
    <button class="btn primary" data-copy="desc">📋 Скопировать описание</button>
    <div class="btns two">
      <button class="btn tonal" data-act="fix">✏️ Исправить</button>
      <button class="btn tonal" data-act="again">🔄 Ещё вариант</button>
    </div>
    ${a.url ? `<button class="btn ghost" data-link="${esc(a.url)}">Открыть на Авито ↗</button>` : ''}
  </div>`;
}

function priceCard(a) {
  if (!a.title && !a.info) return '';
  const head = `<div class="card-row"><div class="main"><div class="k-title">Цена</div>
      <div class="price-now${a.price ? '' : ' none'}">${a.price ? esc(a.price_text) : 'не указана'}</div>
      ${a.floor ? `<div class="hint">торг до ${rub(a.floor)} · покупателю не называю</div>` : ''}</div>
    <button class="btn sm tonal" data-act="setprice">${a.price ? 'Изменить' : 'Указать'}</button></div>`;
  let body;
  if (marketBusy === a.id) body = marketLoading();
  else if (a.market) {
    body = marketHtml(a.market, a, '') +
      '<button class="btn tonal" data-market="force">🔄 Обновить цены</button>';
  } else {
    body = '<p class="hint">Посмотрю до 100 похожих объявлений и подскажу цену: быстро продать, по рынку или дороже.</p>' +
      '<button class="btn primary" data-market="go">📊 Средняя цена на Авито</button>';
  }
  return `<div class="card rise">${head}<div class="sep"></div>${body}</div>`;
}

function renderAd() {
  const a = current();
  const box = $('tab-ad');
  if (!a || (!a.photos.length && !a.title && !a.info && !a.job)) {
    box.innerHTML = steps(null) + startCard() + Kit.supportRow('/api/support');
    return;
  }
  box.innerHTML = steps(a) + photosCard(a) + descCard(a) + priceCard(a) +
    '<button class="btn ghost new" data-act="new">🆕 Новое объявление</button>' + Kit.supportRow('/api/support');
  loadImages(box);
}

// ---------- экран «Цены» ----------

function renderPrice() {
  if (searchResult.m !== null) {
    $('q-result').innerHTML = `<div class="card rise">${marketHtml(searchResult.m, null, searchResult.url)}</div>`;
    return;
  }
  // пока ничего не искали — запросы из своих объявлений одной кнопкой
  const qs = [...new Set(state.ads.map((a) => a.query).filter(Boolean))].slice(0, 6);
  $('q-result').innerHTML = qs.length ? `<div class="recent"><div class="label">Из моих объявлений</div>
    <div class="chips">${qs.map((q) => `<button class="chip" data-query="${esc(q)}">${esc(q)}</button>`).join('')}</div></div>`
    : '<div class="empty"><span class="e">📊</span>Напиши, что продаёшь, — покажу медиану, разброс и цену для быстрой продажи.</div>';
}

// ---------- экран «Все» ----------

function renderHist() {
  const box = $('hist');
  if (!state.ads.length) {
    box.className = '';
    box.innerHTML = '<div class="card empty"><span class="e">🗂</span><b>Объявлений пока нет</b>Загрузи фото вещи — первое появится здесь.</div>';
    return;
  }
  box.className = 'list';
  box.innerHTML = state.ads.map((a) => {
    const img = a.photos.length ? `<img data-src="${esc(a.photos[0].url)}" alt="">` : '<span>📝</span>';
    const status = a.status === 'live' ? '🟢 на Авито' : a.status === 'sold' ? '✅ продано' : '';
    const meta = [a.date, a.price ? rub(a.price) : 'без цены', status || a.category].filter(Boolean).map(esc).join(' · ');
    const end = a.current ? '<span class="pill accent">сейчас</span>' : '';
    return `<button class="row" data-open="${esc(a.id)}"><div class="thumb">${img}</div>` +
      `<div class="main"><div class="title">${esc(a.title || 'Без названия')}</div><div class="meta clip">${meta}</div></div>` +
      `<div class="end">${end}<span class="chev"></span></div></button>`;
  }).join('');
  loadImages(box);
}

function openAd(id) {
  const a = byId(id);
  if (!a) return;
  const photos = a.photos.length ? `<div class="strip">${a.photos.map((p) =>
    `<div class="ph"><img data-src="${esc(p.url)}" alt=""></div>`).join('')}</div>` : '';
  const el = sheet(`<h2>${esc(a.title || 'Без названия')}</h2>${photos}
    <div class="tags">${statusPill(a)}${a.price ? `<span class="pill accent">${esc(a.price_text)}</span>` : ''}${a.category ? `<span class="pill">${esc(a.category)}</span>` : ''}</div>
    ${a.description ? `<div class="desc">${esc(a.description)}</div>` : '<p class="hint">Описания нет.</p>'}
    <div class="btns two">
      ${a.description ? `<button class="btn primary" data-copy="desc" data-ad="${esc(a.id)}">📋 Описание</button>` : ''}
      ${a.title ? `<button class="btn tonal" data-copy="title" data-ad="${esc(a.id)}">Название</button>` : ''}
    </div>
    ${a.photos.length ? `<button class="btn tonal" data-act="send" data-ad="${esc(a.id)}">📤 Фото в чат</button>` : ''}
    ${a.url ? `<button class="btn ghost" data-link="${esc(a.url)}">Открыть на Авито ↗</button>` : ''}
    ${a.market ? `<div class="card flat">${marketHtml(a.market, null, '')}</div>` : ''}`);
  loadImages(el);
}

// ---------- отрисовка и опрос ----------

function render() {
  $('region').textContent = '📍 ' + (state.region_name || 'вся Россия');
  if (tab === 'ad') renderAd();
  if (tab === 'hist') renderHist();
  if (tab === 'price') renderPrice();
  const a = current();
  clearTimeout(pollTimer);
  if (a && (a.job || (a.pending && !uploading))) pollTimer = setTimeout(load, 1600);
}

function replaceAd(ad) {
  const i = state.ads.findIndex((a) => a.id === ad.id);
  if (i >= 0) state.ads[i] = ad; else state.ads.unshift(ad);
}

async function load() {
  try {
    state = await api('/api/state');
    render();
  } catch (e) {
    $('tab-ad').innerHTML = `<div class="card empty"><span class="e">🔒</span><b>Не открылось</b>${esc(e.message)}</div>` +
      '<button class="btn tonal" data-act="reload">Попробовать ещё раз</button>';
    clearTimeout(pollTimer);
  }
}

// ---------- загрузка фото ----------

async function shrink(file) {
  // большие фото уменьшаем до 1600 px — быстрее грузится, фону хватает
  try {
    const bmp = await createImageBitmap(file);
    const k = Math.min(1, 1600 / Math.max(bmp.width, bmp.height));
    const c = document.createElement('canvas');
    c.width = Math.round(bmp.width * k);
    c.height = Math.round(bmp.height * k);
    c.getContext('2d').drawImage(bmp, 0, 0, c.width, c.height);
    return await new Promise((ok) => c.toBlob((b) => ok(b || file), 'image/jpeg', 0.9));
  } catch (e) {
    return file;
  }
}

async function upload(files) {
  const a = current();
  const room = MAX_PHOTOS - (a ? a.photos.length : 0);
  files = Array.from(files).filter((f) => /^image\//.test(f.type) || !f.type).slice(0, Math.max(0, room));
  if (!files.length) { toast(room ? 'Это не фото' : 'На Авито хватит 10 фото', true); return; }
  uploading = { done: 0, total: files.length };
  tab === 'ad' ? render() : switchTab('ad');
  try {
    for (const f of files) {
      const blob = await shrink(f);
      const r = await fetch('/api/upload', { method: 'POST', body: blob,
        headers: Object.assign(auth(), { 'Content-Type': blob.type || 'image/jpeg' }) });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.error || 'Ошибка ' + r.status);
      replaceAd(data.ad);
      uploading.done += 1;
      render();
    }
    const res = await api('/api/process', { id: current() && current().id });
    replaceAd(res.ad);
    haptic('success');
  } catch (e) {
    haptic('error');
    toast(e.message, true);
  } finally {
    uploading = null;
    render();
  }
}

// ---------- листы ----------

function textSheet({ title, hint, value, placeholder, chips, button, onSave, numeric, single }) {
  const field = numeric || single
    ? `<input id="sh-in" inputmode="${numeric ? 'numeric' : 'text'}" placeholder="${esc(placeholder || '')}" value="${esc(value || '')}">`
    : `<textarea id="sh-in" rows="4" placeholder="${esc(placeholder || '')}">${esc(value || '')}</textarea>`;
  const el = sheet(`<h2>${esc(title)}</h2>${hint ? `<p class="hint">${esc(hint)}</p>` : ''}${field}
    ${chips ? `<div class="chips sh-chips">${chips.map((c) => `<button class="chip" data-chip="${esc(c[1])}">${esc(c[0])}</button>`).join('')}</div>` : ''}
    <button class="btn primary" id="sh-go">${esc(button)}</button>`);
  const input = el.querySelector('#sh-in');
  setTimeout(() => input.focus(), 350);
  el.addEventListener('click', async (ev) => {
    const chip = ev.target.closest('[data-chip]');
    if (chip) {
      haptic('select');
      input.value = chip.dataset.chip;
      return;
    }
    if (ev.target.id !== 'sh-go') return;
    const v = input.value.trim();
    if (!v) { haptic('error'); input.focus(); return; }
    try {
      await busy(ev.target, () => onSave(v));
      el.close();
    } catch (e) { haptic('error'); toast(e.message, true); }
  });
  return el;
}

async function write(body) {
  const res = await api('/api/write', body);
  replaceAd(res.ad);
  haptic('success');
  render();
}

function describeSheet() {
  const a = current();
  textSheet({ title: 'Что продаёшь?', hint: 'Бренд, модель, размер, состояние, комплект — своими словами.',
    placeholder: 'Кроссовки Nike Air Max 90, 42 размер, носил сезон, есть коробка', button: '✨ Написать описание',
    onSave: (v) => write({ id: a && a.id, info: v }) });
}

function fixSheet(a) {
  const chips = [['Короче', 'Сделай короче'], ['Живее', 'Напиши живее и убедительнее'],
    ['Про доставку', 'Добавь, что отправлю Авито Доставкой'], ['Торг', 'Добавь, что торг уместен']];
  textSheet({ title: 'Что исправить?', hint: 'Напиши правку — перепишу описание.', chips,
    placeholder: 'Например: добавь, что есть чек', button: '✏️ Переписать',
    onSave: (v) => write({ id: a.id, request: v }) });
}

function setRegionSheet() {
  const chips = [['Вся Россия', 'all'], ['Москва', 'moskva'], ['Петербург', 'sankt-peterburg'], ['Казань', 'kazan']];
  textSheet({ title: 'Где сравнивать цены', hint: 'Как в адресе avito.ru/moskva — или all для всей России.', chips,
    value: state.region === 'all' ? '' : state.region, placeholder: 'moskva', button: 'Сохранить', single: true,
    onSave: async (v) => {
      state = await api('/api/region', { region: v });
      haptic('success');
      toast('Регион: ' + state.region_name, 'ok');
      render();
    } });
}

function priceSheet(a) {
  const m = a.market;
  const chips = m && m.n ? [['⚡ ' + rub(m.fast), m.fast], ['🎯 ' + rub(m.median), m.median], ['💎 ' + rub(m.high), m.high]] : null;
  textSheet({ title: 'Цена', hint: m && m.n ? `Медиана на Авито — ${rub(m.median)}` : 'Сколько хочешь за вещь?', chips,
    value: a.price || '', placeholder: '3500', button: 'Поставить цену', numeric: true,
    onSave: (v) => setPrice(a.id, Number(String(v).replace(/\D/g, ''))) });
}

async function setPrice(id, price) {
  if (!price) throw new Error('Цена — число');
  const res = await api('/api/price', { id, price });
  replaceAd(res.ad);
  haptic('success');
  toast('Цена ' + res.ad.price_text, 'ok');
  render();
}

function viewPhoto(i) {
  const a = current();
  const p = a && a.photos[i];
  if (!p) return;
  const el = sheet(`<div class="viewer"><img data-src="${esc(p.url)}" alt=""></div>
    <p class="hint center">${p.state === 'clean' ? 'На белом фоне' : p.state === 'err' ? 'Фон убрать не получилось — это оригинал' : 'Фон ещё убираю…'} · ${i + 1} из ${a.photos.length}</p>
    <button class="btn tonal" data-act="send">📤 Все фото в чат</button>`);
  loadImages(el);
}

// ---------- вкладки и нажатия ----------

const tabs = Kit.tabbar($('tabs'), [
  { id: 'ad', icon: '🛍', label: 'Объявление' },
  { id: 'price', icon: '📊', label: 'Цены' },
  { id: 'hist', icon: '🗂', label: 'Все' },
], tab, (t) => switchTab(t, true));

$('tab-hist').insertAdjacentHTML('beforeend', Kit.supportRow('/api/support'));  // 🛠 и во вкладке «Все»

function switchTab(t, fromBar) {
  tab = t;
  if (!fromBar) tabs.set(t);
  for (const id of ['ad', 'price', 'hist']) $('tab-' + id).hidden = id !== t;
  window.scrollTo(0, 0);
  render();
}

$('file').addEventListener('change', (ev) => {
  const files = ev.target.files;
  if (files && files.length) upload(files);
  ev.target.value = '';
});

$('q-form').addEventListener('submit', (ev) => {
  ev.preventDefault();
  const query = $('q').value.trim();
  if (!query) { haptic('error'); toast('Напиши, что ищем', true); $('q').focus(); return; }
  haptic('tap');
  $('q').blur();
  findPrice({ query }, $('q-go'));
});

$('region').addEventListener('click', () => { haptic('tap'); setRegionSheet(); });

document.addEventListener('click', async (ev) => {
  const t = ev.target.closest('[data-act],[data-copy],[data-market],[data-price],[data-link],[data-open],[data-q],[data-view],[data-query]');
  if (!t || t.disabled) return;
  const a = current();
  if (t.dataset.query) { $('q').value = t.dataset.query; haptic('tap'); findPrice({ query: t.dataset.query }, $('q-go')); return; }
  if (t.dataset.link) { ev.preventDefault(); openLink(t.dataset.link); return; }
  if (t.dataset.open) { haptic('tap'); openAd(t.dataset.open); return; }
  if (t.dataset.view) { haptic('tap'); viewPhoto(Number(t.dataset.view)); return; }
  if (t.dataset.copy) {
    const ad = (t.dataset.ad && byId(t.dataset.ad)) || a;
    if (ad) copy(t.dataset.copy === 'title' ? ad.title : ad.description, t.dataset.copy === 'title' ? 'Название' : 'Описание');
    return;
  }
  if (t.dataset.q && a) {
    const q = a.questions[Number(t.dataset.q)];
    textSheet({ title: q, hint: 'Ответ добавлю в описание.', placeholder: 'Ответ', button: '✍️ Добавить в описание',
      onSave: (v) => write({ id: a.id, info: `${q} — ${v}` }) });
    return;
  }
  if (t.dataset.market && a) {
    haptic('tap');
    findPrice({ id: a.id, force: t.dataset.market === 'force' }, t);
    return;
  }
  if (t.dataset.price) {
    haptic('tap');
    try { await setPrice(t.dataset.ad, Number(t.dataset.price)); } catch (e) { haptic('error'); toast(e.message, true); }
    return;
  }
  const act = t.dataset.act;
  if (act === 'upload') { haptic('tap'); $('file').click(); }
  else if (act === 'describe') { haptic('tap'); describeSheet(); }
  else if (act === 'gen' && a) { haptic('tap'); busy(t, () => write({ id: a.id })).catch((e) => toast(e.message, true)); }
  else if (act === 'again' && a) {
    haptic('tap');
    busy(t, () => write({ id: a.id, request: 'Напиши другой вариант: живее и убедительнее, факты те же.' }))
      .catch((e) => toast(e.message, true));
  } else if (act === 'fix' && a) { haptic('tap'); fixSheet(a); }
  else if (act === 'setprice' && a) { haptic('tap'); priceSheet(a); }
  else if (act === 'more') {
    const d = $('desc');
    d.classList.toggle('clamp');
    t.textContent = d.classList.contains('clamp') ? 'Показать полностью' : 'Свернуть';
  } else if (act === 'send') {
    const id = t.dataset.ad || (a && a.id);
    try {
      await busy(t, () => api('/api/send', { id }));
      haptic('success');
      toast('Фото в чате — сохрани оттуда', 'ok');
    } catch (e) { haptic('error'); toast(e.message, true); }
  } else if (act === 'new') {
    const go = async () => {
      try { state = await api('/api/new', {}); haptic('success'); render(); } catch (e) { toast(e.message, true); }
    };
    if (tg && tg.showConfirm) tg.showConfirm('Начать новое объявление? Текущее останется во вкладке «Все».', (ok) => ok && go());
    else go();
  } else if (act === 'reload') { load(); }
});

load();
