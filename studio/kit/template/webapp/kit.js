// botkit — общие функции мини-приложений Студии. Не править под бота: своё — в app.js.
// api('state') / api('add', {...}) — запросы к боту (подпись Telegram добавляется сама); ошибка — исключение с текстом.
// toast(текст, ошибка?) · haptic('tap'|'success'|'error'|'warning') · esc(строка) · ask(вопрос) → Promise<bool>
// tabs(элемент, fn(имя)) — переключатель .tabs > button[data-tab] · mainButton(текст, fn) / mainButton(null) — синяя
// кнопка Telegram внизу · back(fn) / back(null) — кнопка «Назад» Telegram · sheet(html) → элемент, закрыть: .remove()
// fmt(число) — «1 234,5» · poll(fn, сек) — обновлять, пока приложение видно.
const tg = window.Telegram && window.Telegram.WebApp;
if (tg) {
  tg.ready(); tg.expand();
  try { tg.setHeaderColor('secondary_bg_color'); tg.setBackgroundColor('secondary_bg_color'); } catch (e) { /* старый Telegram */ }
}
const $ = (sel) => (sel[0] === '#' || sel[0] === '.' || sel.includes(' ') ? document.querySelector(sel) : document.getElementById(sel));
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (n, d = 1) => Number(n || 0).toLocaleString('ru-RU', { maximumFractionDigits: d });

function haptic(kind) {
  const h = tg && tg.HapticFeedback;
  if (!h) return;
  try { if (kind === 'tap') h.impactOccurred('light'); else if (kind === 'select') h.selectionChanged(); else h.notificationOccurred(kind); } catch (e) { /* нет */ }
}

async function api(name, body) {
  const headers = { 'X-Init-Data': (tg && tg.initData) || '' };
  if (body) headers['Content-Type'] = 'application/json';
  const r = await fetch('api/' + name, { method: body ? 'POST' : 'GET', headers, body: body ? JSON.stringify(body) : undefined });
  let data = {};
  try { data = await r.json(); } catch (e) { /* пусто */ }
  if (!r.ok) { const err = new Error(data.error || ('Ошибка ' + r.status)); err.data = data; throw err; }
  return data;
}

let toastTimer;
function toast(text, err) {
  let t = document.getElementById('toast');
  if (!t) { t = document.createElement('div'); t.id = 'toast'; t.className = 'toast'; t.setAttribute('role', 'status'); document.body.appendChild(t); }
  t.textContent = text;
  t.className = 'toast show' + (err ? ' err' : '');
  if (err) haptic('error');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = 'toast' + (err ? ' err' : ''); }, 2600);
}

function ask(question) {
  return new Promise((res) => (tg && tg.showConfirm) ? tg.showConfirm(question, res) : res(confirm(question)));
}

function tabs(el, fn) {
  el.addEventListener('click', (ev) => {
    const b = ev.target.closest('button[data-tab]');
    if (!b || b.classList.contains('on')) return;
    el.querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b));
    haptic('select');
    fn(b.dataset.tab);
  });
}

let mainFn = null;
function mainButton(text, fn) {
  const mb = tg && tg.MainButton;
  if (!mb) return;
  if (mainFn) mb.offClick(mainFn);
  mainFn = null;
  if (!text) { mb.hide(); return; }
  mainFn = () => { haptic('tap'); fn(); };
  mb.setText(text); mb.onClick(mainFn); mb.show();
}

let backFn = null;
function back(fn) {
  const bb = tg && tg.BackButton;
  if (!bb) return;
  if (backFn) bb.offClick(backFn);
  backFn = null;
  if (!fn) { bb.hide(); return; }
  backFn = () => { haptic('tap'); fn(); };
  bb.onClick(backFn); bb.show();
}

function sheet(html) {
  const s = document.createElement('div');
  s.className = 'sheet';
  s.innerHTML = `<div>${html}</div>`;
  s.addEventListener('click', (ev) => { if (ev.target === s) s.remove(); });
  document.body.appendChild(s);
  return s;
}

function poll(fn, sec = 30) {
  setInterval(() => { if (!document.hidden) fn(); }, sec * 1000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) fn(); });
}
