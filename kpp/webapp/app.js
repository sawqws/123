// КПП — мини-приложение: номер такси → пропуск, последние пропуска, вход через Яндекс.
const tg = window.Telegram && window.Telegram.WebApp;
if (tg) { tg.ready(); tg.expand(); }
const $ = (id) => document.getElementById(id);
const haptic = (kind) => {
  const h = tg && tg.HapticFeedback;
  if (!h) return;
  if (kind === 'tap') h.impactOccurred('light'); else h.notificationOccurred(kind);
};
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

async function api(path, body) {
  const headers = { 'X-Init-Data': (tg && tg.initData) || '' };
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
  toastTimer = setTimeout(() => { t.className = 'toast' + (err ? ' err' : ''); }, 2600);
}

// «А 123 ВС 777» → номер-табличка; жёлтая, если номер такси (две буквы в начале)
function plateHtml(pretty, big) {
  const parts = String(pretty || '').split(' ');
  const reg = parts.length > 1 ? parts.pop() : '';
  const taxi = /^[А-ЯA-Z]{2}$/.test(parts[0] || '');
  return `<span class="plate${taxi ? ' taxi' : ''}${big ? ' big' : ''}"><span>${esc(parts.join(' '))}</span>` +
    (reg ? `<span class="reg">${esc(reg)}</span>` : '') + '</span>';
}

const PLATE_RE = /^[А-ЯA-Z]{1,2} ?\d{3} ?([А-ЯA-Z]{2} ?)?\d{2,3}$/i;
let state = null;

function render(s) {
  state = s;
  const who = $('who');
  who.textContent = s.login ? (s.name ? 'Вход: ' + s.name : 'Вход выполнен') : 'Нет входа';
  who.className = 'chip ' + (s.login ? 'on' : 'off');
  $('address').textContent = s.login && s.address ? s.address : '';
  $('login').hidden = s.login;
  const choose = s.login && !s.place_id && s.places.length > 0;
  $('places').hidden = !choose;
  $('order').hidden = !s.login || !s.place_id;
  if (choose) {
    $('place-list').innerHTML = s.places.map((p) =>
      `<button class="row" data-place="${esc(p.id)}"><div class="main"><div class="title">${esc(p.title)}</div></div></button>`).join('');
  }
  renderPasses(s);
}

function renderPasses(s) {
  const box = $('passes');
  let html = '';
  if (s.passes && s.passes.length) {
    html = s.passes.map((p) => {
      const isPlate = PLATE_RE.test(p.subtitle || '');
      const meta = [isPlate ? '' : p.subtitle].concat((p.fields || []).map((f) => (f.title ? f.title + ' ' : '') + f.value))
        .filter(Boolean).map(esc).join(' · ');
      const active = /актив|действ/i.test(p.status || '');
      return `<div class="row">${isPlate ? plateHtml(p.subtitle) : ''}<div class="main">` +
        `<div class="title">${esc(p.title)}${p.status ? ` <span class="status${active ? ' on' : ''}">· ${esc(p.status)}</span>` : ''}</div>` +
        `<div class="meta">${meta}</div>` +
        (p.cancel ? `<button class="linkbtn" data-cancel="${esc(p.id)}">Отменить</button>` : '') + '</div></div>';
    }).join('');
  } else if (s.recent && s.recent.length) {
    html = s.recent.map((r) => `<div class="row">${plateHtml(r.pretty)}<div class="main"><div class="meta">${esc(r.when)}</div>` +
      (r.fresh && r.id != null ? `<button class="linkbtn" data-cancel="${esc(r.id)}">Отменить</button>` : '') + '</div></div>').join('');
  } else if (s.passes_error) {
    html = `<div class="empty">${esc(s.passes_error)}</div>`;
  } else {
    html = '<div class="empty">Пока пусто — оформи первый пропуск.</div>';
  }
  box.innerHTML = html;
  $('passes-box').hidden = !s.login && !(s.recent && s.recent.length);
}

function showResult(res) {
  const box = $('result');
  box.hidden = false;
  if (res.repeat) {
    box.className = 'result';
    box.innerHTML = `<div class="head">Пропуск уже заказан</div>${plateHtml(res.pretty, true)}` +
      `<div class="when">${esc(res.when)}</div>` +
      `<div class="actions"><button class="btn small" data-again="${esc(res.plate)}">Заказать ещё раз</button></div>`;
    return;
  }
  box.className = 'result ok';
  box.innerHTML = `<div class="head">Пропуск заказан</div>${plateHtml(res.pretty, true)}` +
    `<div class="when">Разовый · ${esc(res.when)}${state && state.address ? '<br>' + esc(state.address) : ''}</div>` +
    (res.id != null ? `<div class="actions"><button class="btn small danger" data-cancel="${esc(res.id)}">Отменить</button></div>` : '');
}

async function load() {
  try { render(await api('/api/state')); } catch (e) {
    $('who').textContent = 'Нет связи';
    toast(e.message, true);
  }
}

async function order(text, force) {
  const go = $('go');
  go.disabled = true;
  go.textContent = 'Оформляю…';
  try {
    const res = await api('/api/order', { text, force: !!force });
    showResult(res);
    haptic(res.repeat ? 'warning' : 'success');
    if (!res.repeat) { $('plate').value = ''; load(); }
  } catch (e) {
    haptic('error');
    toast(e.message, true);
    if (e.data && e.data.login) load();
  } finally {
    go.disabled = false;
    go.textContent = 'Оформить пропуск';
  }
}

$('order-form').addEventListener('submit', (ev) => {
  ev.preventDefault();
  const text = $('plate').value.trim();
  if (!text) { haptic('error'); toast('Впиши номер, например А123ВС777', true); return; }
  haptic('tap');
  order(text);
});

$('open-yandex').addEventListener('click', () => {
  haptic('tap');
  const url = state && state.login_url;
  if (!url) return;
  if (tg && tg.openLink) tg.openLink(url); else window.open(url, '_blank');
});

$('do-login').addEventListener('click', async () => {
  const text = $('token').value.trim();
  if (!text) { haptic('error'); toast('Вставь адрес страницы после входа', true); return; }
  const b = $('do-login');
  b.disabled = true;
  b.textContent = 'Вхожу…';
  try {
    render(await api('/api/login', { text }));
    $('token').value = '';
    haptic('success');
    toast('Вход выполнен');
  } catch (e) { haptic('error'); toast(e.message, true); }
  finally { b.disabled = false; b.textContent = 'Войти'; }
});

document.addEventListener('click', async (ev) => {
  const t = ev.target.closest('[data-cancel],[data-place],[data-again]');
  if (!t) return;
  haptic('tap');
  try {
    if (t.dataset.cancel) {
      const ok = await new Promise((res) => (tg && tg.showConfirm) ? tg.showConfirm('Отменить пропуск?', res) : res(confirm('Отменить пропуск?')));
      if (!ok) return;
      t.disabled = true;
      await api('/api/cancel', { id: t.dataset.cancel });
      haptic('success');
      toast('Пропуск отменён');
      $('result').hidden = true;
      load();
    } else if (t.dataset.place) {
      render(await api('/api/place', { id: t.dataset.place }));
      haptic('success');
    } else if (t.dataset.again) {
      order(t.dataset.again, true);
    }
  } catch (e) { haptic('error'); toast(e.message, true); t.disabled = false; }
});

load();
setInterval(() => { if (!document.hidden) load(); }, 60000);
