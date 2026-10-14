// botkit — общие функции мини-приложений Студии. Не править под бота: своё — в app.js.
// api('state') / api('add', {...}) — запросы к боту (подпись Telegram добавляется сама); ошибка — исключение с текстом.
// toast(текст, ошибка?) · haptic('tap'|'success'|'error'|'warning') · esc(строка) · ask(вопрос) → Promise<bool>
// tabs(элемент, fn(имя)) — переключатель .tabs > button[data-tab] · mainButton(текст, fn) / mainButton(null) — синяя
// кнопка Telegram внизу · back(fn) / back(null) — кнопка «Назад» Telegram · sheet(html) → элемент, закрыть: .remove()
// fmt(число) — «1 234,5» · poll(fn, сек) — обновлять, пока приложение видно.
// 🛠️ support() — лист «Поддержка и пожелания» (текст + скриншот → api('support') → Claude правит бота, ответ в чат);
// строка с ним сама встаёт внизу main (не нужна — <body data-no-support>), своя кнопка — data-support.
// 💬 toChat() — «Перейти в чат»: режим «чат» (api('mode')) и закрыть приложение; строка сама встаёт внизу main
// (не нужна — <body data-no-mode>), своя кнопка — data-mode.
const tg = window.Telegram && window.Telegram.WebApp;
if (tg) {
  tg.ready(); tg.expand();
  try { const bg = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim() || '#f6f3ee'; tg.setHeaderColor(bg); tg.setBackgroundColor(bg); } catch (e) { /* старый Telegram */ }
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

// ---------- 🛠️ поддержка и пожелания (сервер — встроенный api('support') в botkit) ----------
function shrinkImage(file, max = 1600) {  // -> base64 JPEG (без «data:»), длинная сторона ≤ max
  return new Promise((resolve, reject) => {
    const rd = new FileReader();
    const img = new Image();
    rd.onload = () => { img.src = rd.result; };
    rd.onerror = img.onerror = () => reject(new Error('Картинка не открылась'));
    img.onload = () => {
      const k = Math.min(1, max / Math.max(img.naturalWidth, img.naturalHeight));
      const c = document.createElement('canvas');
      c.width = Math.max(1, Math.round(img.naturalWidth * k));
      c.height = Math.max(1, Math.round(img.naturalHeight * k));
      c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
      resolve(c.toDataURL('image/jpeg', 0.82).split(',')[1]);
    };
    rd.readAsDataURL(file);
  });
}

function support() {
  let photo = null;
  const s = sheet(`<h2>🛠️ Поддержка и пожелания</h2>
    <p class="muted">Напиши, что сломалось или чего не хватает — починю/добавлю.</p>
    <textarea class="sup-text" rows="5" maxlength="4000" placeholder="Например: «кнопка не жмётся» или «добавь итог за неделю»"></textarea>
    <div class="sup-shot"><label class="btn small">📎 Скриншот<input type="file" accept="image/*" hidden></label><span class="sup-prev"></span></div>
    <button class="btn primary sup-send">Отправить</button>`);
  const q = (sel) => s.querySelector(sel);
  q('input[type=file]').addEventListener('change', async (ev) => {
    const f = ev.target.files && ev.target.files[0];
    ev.target.value = '';
    if (!f) return;
    try {
      photo = await shrinkImage(f);
      q('.sup-prev').innerHTML = `<img alt="" src="data:image/jpeg;base64,${photo}">`;
    } catch (e) { toast(e.message, true); }
  });
  q('.sup-send').addEventListener('click', async (ev) => {
    const text = q('.sup-text').value.trim();
    if (!text) { toast('Напиши, что поправить', true); return; }
    ev.target.disabled = true;
    try {
      await api('support', photo ? { text, photo } : { text });
      haptic('success');
      s.remove();
      toast('Принял — работаю, ответ придёт в чат');
    } catch (e) { toast(e.message, true); ev.target.disabled = false; }
  });
  return s;
}

document.addEventListener('click', (ev) => {  // <body data-support="…"> — не кнопка, а признак приложения
  const el = ev.target.closest && ev.target.closest('[data-support]');
  if (el && el !== document.body) { haptic('tap'); support(); }
});

// ---------- 💬 перейти в чат (режим «чат»: бот не предлагает приложение, всё кнопками в чате) ----------
async function toChat() {
  try {
    await api('mode', { mode: 'chat' });
    haptic('success');
    toast('Теперь в чате — бот прислал меню');
    setTimeout(() => { if (tg && tg.close) tg.close(); }, 600);
  } catch (e) { toast(e.message, true); }
}
document.addEventListener('click', (ev) => {
  if (ev.target.closest && ev.target.closest('[data-mode]')) { haptic('tap'); toChat(); }
});
if (!document.body.hasAttribute('data-no-mode')) {
  (document.querySelector('main') || document.body).insertAdjacentHTML('beforeend',
    '<div class="list mode-list"><button class="row" data-mode><span class="icon">💬</span><span class="main">' +
    '<span class="name">Перейти в чат</span><span class="meta">Всё то же кнопками в чате; вернуться — /mode</span></span>' +
    '<span class="end">›</span></button></div>');
}
if (!document.body.hasAttribute('data-no-support')) {
  // плавающая кнопка — видна на любом экране, над вкладками
  document.body.insertAdjacentHTML('beforeend',
    '<button class="sup-fab" type="button" data-support aria-label="Поддержка и пожелания"><span class="i">🛠️</span><span class="t">Поддержка</span></button>');
  // при прокрутке вниз кнопка уезжает, вверх и у верха — возвращается, в самом низу (там строка «Поддержка») — прячется
  const fab = document.querySelector('.sup-fab');
  let lastY = scrollY;
  addEventListener('scroll', () => requestAnimationFrame(() => {
    const y = scrollY;
    if (y < 40) fab.classList.remove('away');
    else if (innerHeight + y >= document.documentElement.scrollHeight - 90) fab.classList.add('away');
    else if (Math.abs(y - lastY) > 6) fab.classList.toggle('away', y > lastY);
    if (Math.abs(y - lastY) > 6 || y < 40) lastY = y;
  }), { passive: true });
  (document.querySelector('main') || document.body).insertAdjacentHTML('beforeend',
    '<div class="list sup-list"><button class="row" data-support><span class="icon">🛠️</span><span class="main">' +
    '<span class="name">Поддержка и пожелания</span><span class="meta">Ошибка или идея — починю/добавлю</span></span>' +
    '<span class="end">›</span></button></div>');
}

