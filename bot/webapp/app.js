'use strict';
/* HDP — приложение бота внутри Telegram. Без сборки и библиотек.
   Данные с сайта: GET /api/overview (бот держит их в кэше и обновляет сам).
   Что бот делает сейчас: /api/state раз в пару секунд — живая карточка, результат, черновик учителю.
   Действия (решить, сдать, отправить черновик) — те же функции, что у кнопок в чате; результат приходит и туда. */

const tg = window.Telegram && window.Telegram.WebApp;
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const plural = (n, a, b, c) => { n = Math.abs(n); const m = n % 10, h = n % 100; return m === 1 && h !== 11 ? a : m >= 2 && m <= 4 && (h < 12 || h > 14) ? b : c; };
const LINK = /https:\/\/horodigital\.ru\/student\/topic\/[0-9a-f-]{36}\/task\/[0-9a-f-]{36}\S*/;
const comma = x => String(x).replace('.', ',');
const lvlFmt = x => comma((+x).toFixed(1));

/* ---------- иконки ---------- */
const I = {
  tasks: '<svg viewBox="0 0 24 24"><rect x="4" y="3" width="16" height="18" rx="4"/><path d="M8.5 9.5l1.6 1.6 3.4-3.4M8.5 15.5h7"/></svg>',
  grades: '<svg viewBox="0 0 24 24"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></svg>',
  teachers: '<svg viewBox="0 0 24 24"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/><path d="M8.5 11h7M8.5 14.5h4"/></svg>',
  refresh: '<svg viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-2.6-6.4"/><path d="M21 3v6h-6"/></svg>',
  gear: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>',
  paste: '<svg viewBox="0 0 24 24"><rect x="7" y="4" width="13" height="16" rx="3"/><path d="M4 8v10a3 3 0 0 0 3 3h8"/></svg>',
  bolt: '<svg viewBox="0 0 24 24"><path d="M13 2L4.5 13.5H12L11 22l8.5-11.5H12z"/></svg>',
  eye: '<svg viewBox="0 0 24 24"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>',
  pen: '<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/></svg>',
  ext: '<svg viewBox="0 0 24 24"><path d="M14 4h6v6M20 4l-9 9"/><path d="M19 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h4"/></svg>',
  send: '<svg viewBox="0 0 24 24"><path d="M21 3L10 14M21 3l-7 18-4-7-7-4z"/></svg>',
  x: '<svg viewBox="0 0 24 24"><path d="M18 6L6 18M6 6l12 12"/></svg>',
  spin: '<svg viewBox="0 0 24 24"><path d="M12 3a9 9 0 1 1-9 9"/></svg>',
  chev: '<svg viewBox="0 0 24 24"><path d="M9 18l6-6-6-6"/></svg>',
};
const RING_DEFS = '<svg width="0" height="0" style="position:absolute"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#34e0a1"/><stop offset="1" stop-color="#3bb2ff"/></linearGradient></defs></svg>';

/* ---------- состояние ---------- */
const S = {
  tab: 'tasks', data: null, loading: false, error: '', state: null, me: null, seen: 0, sheet: null, link: '',
  lastBusy: false, polledAt: 0,
};
try { S.seen = +localStorage.getItem('hdp.seen') || 0; S.tab = localStorage.getItem('hdp.tab') || 'tasks'; } catch (e) { /* без хранилища */ }
const store = (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* без хранилища */ } };

/* ---------- сеть ---------- */
async function api(path, body) {
  const r = await fetch(path, {
    method: body ? 'POST' : 'GET',
    headers: { 'Content-Type': 'application/json', 'X-Init-Data': (tg && tg.initData) || '' },
    body: body ? JSON.stringify(body) : undefined,
  });
  const j = await r.json().catch(() => ({ error: 'Бот не ответил — попробуй ещё раз' }));
  if (!r.ok) throw new Error(j.error || 'Что-то пошло не так');
  return j;
}

async function loadOverview(fresh) {
  try {
    const r = await api('/api/overview', fresh ? { fresh: 1 } : undefined);
    const changed = r.data && (!S.data || r.data.at !== S.data.at);
    S.loading = r.loading; S.error = r.error || '';
    if (changed) { S.data = r.data; render(); } else renderHead();
  } catch (e) { S.error = e.message; renderHead(); }
}

async function poll() {
  try {
    const st = await api('/api/state');
    const prev = S.state;
    S.state = st; S.polledAt = Date.now();
    S.loading = st.loading; S.error = st.error || '';
    if (S.data && st.at && st.at !== S.data.at && !st.loading) loadOverview();
    if (prev && prev.busy && !st.busy) finished(st);
    renderStatus(); renderHead();
  } catch (e) { /* сеть моргнула — следующий опрос */ }
}

function finished(st) {
  const l = st.last;
  if (!l || l.at <= S.seen) return;
  haptic(l.ok ? 'success' : 'error');
  if (l.score && l.score[2] >= 100) confetti();
}

/* ---------- даты ---------- */
const MS = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
const MG = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
const WD = ['Воскресенье', 'Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота'];
const dayNum = s => { const [y, m, d] = s.split('-').map(Number); return Date.UTC(y, m - 1, d) / 864e5; };
const todayIso = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
const left = s => dayNum(s) - dayNum(todayIso());
function whenLabel(s) {
  const n = left(s);
  if (n < 0) return [`${-n} ${plural(-n, 'день', 'дня', 'дней')}`, 'late'];
  if (n === 0) return ['сегодня', 'soon'];
  if (n === 1) return ['завтра', 'soon'];
  const [, m, d] = s.split('-').map(Number);
  return [`до ${d} ${MS[m - 1]}`, n <= 3 ? 'soon' : ''];
}
function ago(ms) {
  const m = Math.round((Date.now() - ms) / 60000);
  if (m < 1) return 'только что';
  if (m < 60) return `${m} мин назад`;
  const h = Math.round(m / 60);
  return h < 24 ? `${h} ч назад` : new Date(ms).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
}
const mmss = s => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
const hue = s => { let h = 0; for (const c of String(s)) h = (h * 31 + c.charCodeAt(0)) % 360; return h; };
const subjIc = (r, cls = '') => `<div class="subj-ic ${cls}" style="--h:${hue(r.subj)}">${esc(r.icon || '📘')}</div>`;
const STATUS = { reworking: 'Доработка', failed: 'Не зачтено', appointed: 'Новое' };

/* ---------- отрисовка ---------- */
function render() {
  const v = $('#view');
  v.className = 'enter';
  v.innerHTML = RING_DEFS + `<div id="head"></div>` + (S.tab === 'tasks' ? tasksView() : S.tab === 'grades' ? gradesView() : teachersView());
  renderHead(); renderStatus(); renderTabs();
  requestAnimationFrame(() => setTimeout(() => v.classList.remove('enter'), 600));
}

function renderHead() {
  const h = $('#head');
  if (!h) return;
  const titles = { tasks: 'Задания', grades: 'Оценки', teachers: 'Учителя' };
  const d = new Date();
  const when = S.loading ? 'обновляю с сайта…' : S.data ? `обновлено ${ago(S.data.at)}` : '';
  const html = `<div class="top">
      <div class="grow">
        <div class="eyebrow">${WD[d.getDay()]}, ${d.getDate()} ${MG[d.getMonth()]}</div>
        <h1 class="title">${titles[S.tab]}</h1>
        <div class="sub">${esc(when)}</div>
      </div>
      <button class="icon-btn ${S.loading ? 'spin' : ''}" data-a="refresh" aria-label="Обновить">${I.refresh}</button>
      <button class="icon-btn" data-a="settings" aria-label="Настройки">${I.gear}</button>
    </div>` + (S.error && !S.loading ? `<div class="card err">⚠️ ${esc(S.error)}</div>` : '');
  if (h.dataset.html !== html) { h.innerHTML = html; h.dataset.html = html; }  // без лишней перерисовки
}

function renderTabs() {
  const tasks = (S.data && S.data.tasks) || [];
  const late = tasks.filter(t => left(t.deadline) < 0).length;
  const comm = ((S.data && S.data.comments) || []).length;
  const tab = (id, label, n) => `<button class="tab ${S.tab === id ? 'on' : ''}" data-a="tab" data-id="${id}">${I[id]}<span>${label}</span>${n ? `<span class="badge">${n}</span>` : ''}</button>`;
  $('#tabs').innerHTML = tab('tasks', 'Задания', late) + tab('grades', 'Оценки', 0) + tab('teachers', 'Учителя', comm);
}

/* живая работа, результат, черновик — сверху на любой вкладке */
function renderStatus() {
  const box = $('#status');
  if (!box) return;
  const st = S.state || {};
  let out = '';
  if (box.classList.contains('mini')) {  // на «Оценках» и «Учителях» — одна строка, полная карточка на «Заданиях»
    const l = st.busy && (st.live || { title: 'Работаю', frac: null });
    const html = l ? `<button class="card banner mini-live" data-a="tab" data-id="tasks" style="width:100%;text-align:left">
      <div class="orb">${I.spin}</div><div class="grow"><div class="card-t">${esc(l.title)}</div><div class="card-s">${l.frac == null ? 'идёт работа' : Math.round(l.frac * 100) + '%'} · подробнее на «Заданиях»</div></div>${I.chev}</button>` : '';
    if (box.dataset.html !== html) { box.innerHTML = html; box.dataset.html = html; }
    return;
  }
  const live = $('.live', box);
  if (st.busy && live && box.dataset.rest === restKey(st)) { updateLive(live, st.live); return; }
  if (st.busy) {
    const l = st.live || { title: 'Работаю', sub: '', step: '', done: [], frac: null, secs: 0 };
    const pct = l.frac == null ? null : Math.round(l.frac * 100);
    out += `<div class="card live">
      <div class="card-h"><div class="orb">${I.spin}</div>
        <div class="grow" style="min-width:0"><div class="card-t">${esc(l.title)}</div><div class="card-s">${esc(l.sub)}</div></div></div>
      ${l.done && l.done.length ? `<div class="done-steps">${l.done.map(x => `<div>${esc(x)}</div>`).join('')}</div>` : ''}
      ${l.step ? `<div class="step">${esc(l.step)}</div>` : ''}
      <div class="bar ${pct == null ? 'indet' : ''}"><i style="width:${pct || 0}%"></i></div>
      <div class="meta"><span>${pct == null ? 'результат придёт сюда и в чат' : pct + '%'}</span><span class="clock" data-secs="${l.secs || 0}">${mmss(l.secs || 0)}</span></div>
    </div>`;
  }
  const l = st.last;
  if (!st.busy && l && l.at > S.seen) out += resultCard(l);
  if (st.draft) {
    const d = st.draft;
    out += `<button class="card banner draft" data-a="draft" style="width:100%;text-align:left">
      <div class="emoji">📄</div>
      <div class="grow"><div class="card-t">Черновик для учителя</div><div class="card-s">${esc(d.title)}</div></div>
      ${I.chev}</button>`;
  }
  box.innerHTML = out;
  box.dataset.rest = restKey(st);
}

/* всё, кроме живой карточки: если это не поменялось, карточку только подправляем — анимации не сбиваются */
const restKey = st => JSON.stringify([!!st.busy, st.last && st.last.at > S.seen ? st.last.at : 0, st.draft && st.draft.title, st.live && st.live.title]);

function updateLive(el, l) {
  if (!l) return;
  const pct = l.frac == null ? null : Math.round(l.frac * 100);
  const set = (sel, html) => { const e = $(sel, el); if (e && e.innerHTML !== html) e.innerHTML = html; };
  set('.card-s', esc(l.sub));
  const step = $('.step', el);
  if (step) step.textContent = l.step; else if (l.step) $('.bar', el).insertAdjacentHTML('beforebegin', `<div class="step">${esc(l.step)}</div>`);
  const done = (l.done || []).map(x => `<div>${esc(x)}</div>`).join('');
  const de = $('.done-steps', el);
  if (de) { if (de.innerHTML !== done) de.innerHTML = done; } else if (done) $('.card-h', el).insertAdjacentHTML('afterend', `<div class="done-steps">${done}</div>`);
  const bar = $('.bar', el);
  bar.classList.toggle('indet', pct == null);
  $('i', bar).style.width = (pct || 0) + '%';
  set('.meta span', pct == null ? 'результат придёт сюда и в чат' : pct + '%');
  $('.clock', el).dataset.secs = l.secs || 0;
}

function resultCard(l) {
  const sc = l.score;
  const pct = sc ? sc[2] : null;
  const head = !l.ok ? '😕 Не получилось' : l.kind === 'sent' ? '📬 Отправлено учителю' : l.show ? '👀 Ответы' : sc ? '' : '✅ Готово';
  const C = 2 * Math.PI * 26;
  const ring = pct == null ? '' : `<div class="ring"><svg viewBox="0 0 58 58"><circle class="track" cx="29" cy="29" r="26"/><circle class="val" cx="29" cy="29" r="26" stroke-dasharray="${C}" stroke-dashoffset="${C * (1 - Math.min(pct, 100) / 100)}"/></svg>${pct}%</div>`;
  const score = sc ? `<div class="score">${sc[1] ? `${comma(sc[0])}<small>/ ${comma(sc[1])}</small>` : `${pct}%`}</div>` : `<div class="card-t">${head}</div>`;
  const long = l.text && (l.text.length > 240 || l.text.split('\n').length > 7);
  const text = l.text ? `<div class="text ${long ? '' : 'open'}">${esc(l.text)}</div>${long ? '<button class="more" data-a="more">Показать всё</button>' : ''}` : '';
  return `<div class="card result">
    <div class="card-h">${ring}<div class="grow" style="min-width:0">${score}<div class="card-s" style="margin-top:4px">${esc(l.title)}${l.secs ? ` · ${mmss(l.secs)}` : ''}</div></div>
      <button class="x" data-a="seen" aria-label="Скрыть">${I.x}</button></div>
    ${pct != null && pct >= 100 ? '<div class="chips" style="margin:12px 0 0"><span class="chip good">🏆 Полный балл</span></div>' : ''}
    ${l.draft ? '<div class="card-s" style="margin-top:10px">Черновик учителю — ниже</div>' : ''}
    ${text}</div>`;
}

function tasksView() {
  if (!S.data) return '<div id="status"></div>' + linkCard() + '<div class="skel"></div><div class="skel"></div><div class="skel"></div>';
  const tasks = S.data.tasks.slice().sort((a, b) => a.deadline.localeCompare(b.deadline) || a.subj.localeCompare(b.subj) || a.order - b.order);
  const late = tasks.filter(t => left(t.deadline) < 0);
  const week = tasks.filter(t => left(t.deadline) >= 0 && left(t.deadline) <= 7);
  const later = tasks.filter(t => left(t.deadline) > 7);
  const auto = tasks.filter(t => t.auto && t.rawStatus === 'appointed');
  let out = '<div class="chips">'
    + `<span class="chip">${tasks.length} ${plural(tasks.length, 'задание', 'задания', 'заданий')}</span>`
    + (late.length ? `<span class="chip late"><span class="dot"></span>${late.length} просрочено</span>` : tasks.length ? '<span class="chip good">всё в срок ✨</span>' : '')
    + (auto.length ? `<span class="chip new">⚡ ${auto.length} сдам сам</span>` : '')
    + '</div><div id="status"></div>' + linkCard();
  if (auto.length) {
    out += `<div class="card banner accent"><div class="emoji">⚡</div>
      <div class="grow"><div class="card-t">${auto.length} ${plural(auto.length, 'автотест', 'автотеста', 'автотестов')}</div><div class="card-s">могу сдать все по очереди</div></div>
      <button class="btn primary small" data-a="auto">Сдать</button></div>`;
  }
  if (!tasks.length) {
    return out + '<div class="empty"><div class="big">🎉</div><h2>Всё сделано</h2><p>Новых заданий нет. Можно отдыхать.</p></div>';
  }
  const section = (title, list, cls = '') => list.length ? `<div class="section ${cls}"><h3>${title}</h3><span class="n">${list.length}</span></div><div class="list">${list.map(taskRow).join('')}</div>` : '';
  return out + section('Просрочено', late, 'late') + section('На этой неделе', week) + section('Позже', later);
}

function taskRow(t) {
  const [w, cls] = whenLabel(t.deadline);
  return `<button class="row" data-a="task" data-url="${esc(t.url)}">
    ${subjIc(t)}
    <div class="grow"><div class="t">${esc(t.title)}</div>
      <div class="m"><span class="tag ${t.rawStatus}">${STATUS[t.rawStatus] || esc(t.st)}</span>${t.auto ? '<span class="tag auto">⚡</span>' : ''}<span class="s">${esc(t.subj)}</span></div></div>
    <span class="when ${cls}">${w}</span></button>`;
}

function linkCard() {
  const ok = LINK.test(S.link);
  return `<div class="card">
    <div class="link-box"><input id="link" type="url" inputmode="url" autocomplete="off" placeholder="Вставь ссылку на задание" value="${esc(S.link)}">
      <button class="icon-btn" data-a="paste" aria-label="Вставить">${I.paste}</button></div>
    <div class="link-actions">
      <button class="btn primary" data-a="solve" data-show="0" ${ok ? '' : 'disabled'}>${I.bolt}Решить и сдать</button>
      <button class="btn" data-a="solve" data-show="1" ${ok ? '' : 'disabled'}>${I.eye}Только ответы</button>
    </div></div>`;
}

const MINI = '<div id="status" class="mini"></div>';

function gradesView() {
  return MINI + gradesBody();
}

function gradesBody() {
  if (!S.data) return '<div class="skel" style="height:110px"></div><div class="skel"></div><div class="skel"></div>';
  const g = S.data.grades;
  if (!g.length) return '<div class="empty"><div class="big">📊</div><h2>Оценок пока нет</h2><p>Появятся, когда учителя проверят работы.</p></div>';
  const avg = g.reduce((s, r) => s + r.five, 0) / g.length;
  const cnt = f => g.filter(r => (f === 4 ? r.five >= 4 : r.five === f)).length;
  let out = `<div class="card hero">
    <div><div class="avg">${comma(avg.toFixed(1))}</div><div class="avg-l">средняя по ${g.length} ${plural(g.length, 'предмету', 'предметам', 'предметам')}</div></div>
    <div class="dist"><div><span class="chip good" style="height:24px">4–5</span>${cnt(4)}</div><div><span class="chip warn" style="height:24px">3</span>${cnt(3)}</div><div><span class="chip late" style="height:24px">2</span>${cnt(2)}</div></div>
  </div>`;
  out += '<div class="section"><h3>Предметы</h3><span class="n">от слабых к сильным</span></div>';
  const tick = x => `<b style="left:${x / 4 * 100}%"></b>`;
  const color = f => ({ 5: 'var(--good)', 4: '#8bd34a', 3: 'var(--warn)', 2: 'var(--late)' }[f]);
  out += g.map(r => `<div class="card grade">
    <div class="g g${r.five}">${r.five}</div>
    <div class="grow"><div class="n">${esc(r.name)}</div>
      <div class="lvl"><i style="width:${Math.min(100, r.lvl / 4 * 100)}%;background:${color(r.five)}"></i>${tick(2)}${tick(2.5)}${tick(3.5)}</div>
      <div class="hint">уровень <strong>${lvlFmt(r.lvl)}</strong> · ${r.next ? `до «${r.next[1]}» ещё <strong>${lvlFmt(Math.max(0.1, r.next[0] - r.lvl))}</strong>` : 'максимум 🏆'}</div></div>
  </div>`).join('');
  return out + '<div class="foot">Уровень → оценка: от 2,0 — 3, от 2,5 — 4, от 3,5 — 5</div>';
}

function teachersView() {
  return MINI + teachersBody();
}

function teachersBody() {
  if (!S.data) return '<div class="skel"></div><div class="skel"></div>';
  const c = S.data.comments;
  if (!c.length) return '<div class="empty"><div class="big">💬</div><h2>Доработок нет</h2><p>Учителя всё приняли.</p></div>';
  return '<div class="chips">' + `<span class="chip warn">${c.filter(r => !r.failed).length} на доработке</span>`
    + `<span class="chip late">${c.filter(r => r.failed).length} не зачтено</span></div>`
    + c.map(r => `<button class="card comment" data-a="task" data-url="${esc(r.url)}">
      <div class="card-h">${subjIc(r)}<div class="grow" style="min-width:0"><div class="card-s">${esc(r.subj)}</div><div class="card-t">${esc(r.title)}</div></div>
        ${r.score != null ? `<span class="tag ${r.failed ? 'failed' : 'reworking'}">${r.score}%</span>` : ''}</div>
      <div class="q ${r.comment ? '' : 'none'}">${esc(r.comment || (r.attempts ? `Тест: ${r.attempts}` : 'Комментария нет'))}</div>
    </button>`).join('');
}

/* ---------- нижние листы ---------- */
function openSheet(html, kind) {
  closeSheet(true);
  const root = $('#sheet-root');
  root.innerHTML = `<div class="backdrop" data-a="close"></div><div class="sheet" role="dialog"><div class="grip"></div>${html}</div>`;
  S.sheet = kind;
  requestAnimationFrame(() => requestAnimationFrame(() => { $('.backdrop', root).classList.add('on'); $('.sheet', root).classList.add('on'); }));
  if (tg && tg.BackButton) tg.BackButton.show();
  haptic('light');
}

function closeSheet(now) {
  const root = $('#sheet-root');
  S.sheet = null;
  if (tg && tg.BackButton) tg.BackButton.hide();
  if (now || !root.firstChild) { root.innerHTML = ''; return; }
  const b = $('.backdrop', root), s = $('.sheet', root);
  if (b) b.classList.remove('on');
  if (s) s.classList.remove('on');
  setTimeout(() => { if (!S.sheet) root.innerHTML = ''; }, 380);
}

function findTask(url) {
  const t = ((S.data && S.data.tasks) || []).find(x => x.url === url);
  if (t) return t;
  const c = ((S.data && S.data.comments) || []).find(x => x.url === url);
  return c && { ...c, rawStatus: c.failed ? 'failed' : 'reworking', auto: !!c.attempts, deadline: null };
}

function taskSheet(url) {
  const t = findTask(url);
  if (!t) return;
  const c = ((S.data && S.data.comments) || []).find(x => x.url === url);
  const [w, cls] = t.deadline ? whenLabel(t.deadline) : ['', ''];
  const busy = S.state && S.state.busy;
  const chips = `<div class="chips">
    <span class="chip ${t.rawStatus === 'failed' ? 'late' : t.rawStatus === 'reworking' ? 'warn' : 'new'}">${STATUS[t.rawStatus] || ''}</span>
    ${w ? `<span class="chip ${cls === 'late' ? 'late' : cls === 'soon' ? 'warn' : ''}">${cls === 'late' ? 'просрочено на ' + w : w}</span>` : ''}
    ${t.auto ? '<span class="chip good">⚡ автотест</span>' : ''}</div>`;
  const comment = c && (c.comment || c.score != null) ? `<div class="s-sub">Учитель</div><div class="comment"><div class="q ${c.comment ? '' : 'none'}">${esc(c.comment || 'Без комментария')}${c.score != null ? ` · ${c.score}%` : ''}</div></div>` : '';
  const actions = t.auto
    ? `<button class="btn primary" data-a="solve-url" data-url="${esc(url)}" data-show="0" ${busy ? 'disabled' : ''}>${I.bolt}Решить и сдать</button>
       <button class="btn" data-a="solve-url" data-url="${esc(url)}" data-show="1" ${busy ? 'disabled' : ''}>${I.eye}Только ответы</button>`
    : `<button class="btn primary" data-a="solve-url" data-url="${esc(url)}" data-show="0" ${busy ? 'disabled' : ''}>${I.pen}Подготовить ответ</button>
       <button class="btn" data-a="solve-url" data-url="${esc(url)}" data-show="1" ${busy ? 'disabled' : ''}>${I.eye}Только посмотреть</button>`;
  openSheet(`${subjIc(t, 's-ic')}
    <div class="s-sub" style="margin-top:12px">${esc(t.subj)}${t.topic ? ' · ' + esc(t.topic) : ''}</div>
    <h2>${esc(t.title)}</h2>${chips}${comment}
    <div class="btn-col">${actions}
      <button class="btn ghost" data-a="open" data-url="${esc(url)}">${I.ext}Открыть на сайте</button></div>
    <div class="note">${busy ? 'Сейчас занят другим заданием' : t.auto ? 'Результат покажу здесь и пришлю в чат' : 'Сначала покажу черновик — отправишь сам'}</div>`, 'task');
}

function draftSheet() {
  const d = S.state && S.state.draft;
  if (!d) return;
  openSheet(`<div class="s-sub">Черновик для учителя</div><h2>${esc(d.title)}</h2>
    <textarea id="draft-text">${esc(d.text)}</textarea>
    ${d.files ? `<div class="note">🖼 ${d.files} ${plural(d.files, 'картинка', 'картинки', 'картинок')} — в чате, уйдут вместе с текстом</div>` : ''}
    <div class="btn-col">
      <button class="btn primary" data-a="draft-send">${I.send}Отправить учителю</button>
      <button class="btn danger" data-a="draft-drop">Не отправлять</button></div>
    <div class="note">Можно поправить текст прямо здесь — запомню, как ты пишешь</div>`, 'draft');
}

function settingsSheet() {
  const me = S.me || {};
  openSheet(`<h2 style="margin-top:0">Настройки</h2>
    <button class="set-row" data-a="digest"><span class="emoji">🔔</span><span class="grow"><div class="card-t">Сводка каждый день</div><div class="card-s">в ${me.hour || 15}:00 по Москве — что надо сделать</div></span><span class="switch ${me.digest ? 'on' : ''}"></span></button>
    <button class="set-row" data-a="check"><span class="emoji">🩺</span><span class="grow"><div class="card-t">Проверить, что всё работает</div><div class="card-s">сайт, Claude, почерк — ответ придёт в чат</div></span>${I.chev}</button>
    <button class="set-row" data-a="alphabet"><span class="emoji">✍️</span><span class="grow"><div class="card-t">Мой почерк</div><div class="card-s">${me.alphabet ? 'алфавит заполнен · прислать заново' : 'алфавит не заполнен — пришлю листы в чат'}</div></span>${I.chev}</button>
    <button class="set-row" data-a="chat"><span class="emoji">💬</span><span class="grow"><div class="card-t">Вернуться в чат</div><div class="card-s">кнопки бота там тоже работают</div></span>${I.chev}</button>
    <div class="foot">HDP · версия ${esc(me.version || '—')}</div>`, 'settings');
}

/* ---------- действия ---------- */
async function solve(url, show) {
  try {
    await api('/api/solve', { url, show: !!show });
    closeSheet();
    S.link = '';
    if (S.tab === 'tasks') { const i = $('#link'); if (i) i.value = ''; updateLinkButtons(); }
    toast(show ? '👀 Смотрю ответы — прогресс сверху' : '⚡ Начал! Прогресс — сверху');
    haptic('success');
    setTimeout(poll, 400);
  } catch (e) { toast(e.message); haptic('error'); }
}

function updateLinkButtons() {
  const ok = LINK.test(S.link);
  document.querySelectorAll('[data-a="solve"]').forEach(b => { b.disabled = !ok; });
}

const ACT = {
  tab: el => { if (S.tab === el.dataset.id) return; S.tab = el.dataset.id; store('hdp.tab', S.tab); haptic('select'); window.scrollTo(0, 0); render(); },
  refresh: () => { S.loading = true; renderHead(); haptic('light'); loadOverview(true); },
  settings: async () => { settingsSheet(); try { S.me = await api('/api/me'); if (S.sheet === 'settings') settingsSheet(); } catch (e) { /* покажем без данных */ } },
  task: el => taskSheet(el.dataset.url),
  close: () => closeSheet(),
  open: el => { if (tg && tg.openLink) tg.openLink(el.dataset.url); else window.open(el.dataset.url); },
  solve: el => solve(S.link.match(LINK)[0], el.dataset.show === '1'),
  'solve-url': el => solve(el.dataset.url, el.dataset.show === '1'),
  paste: async () => {
    try {
      const t = await navigator.clipboard.readText();
      const m = t && t.match(LINK);
      if (!m) { toast('В буфере нет ссылки на задание'); return; }
      S.link = m[0]; $('#link').value = S.link; updateLinkButtons(); haptic('light');
    } catch (e) { $('#link').focus(); toast('Вставь ссылку в поле'); }
  },
  auto: () => {
    const n = ((S.data && S.data.tasks) || []).filter(t => t.auto && t.rawStatus === 'appointed').length;
    confirmDo(`Сдать ${n} ${plural(n, 'автотест', 'автотеста', 'автотестов')} по очереди? Это ${n * 2}–${n * 3} мин.`, async () => {
      try { await api('/api/auto', {}); toast('⚡ Сдаю — прогресс сверху'); haptic('success'); setTimeout(poll, 400); } catch (e) { toast(e.message); }
    });
  },
  seen: () => { S.seen = (S.state && S.state.last && S.state.last.at) || Date.now() / 1000; store('hdp.seen', S.seen); renderStatus(); },
  more: el => { const t = el.previousElementSibling; t.classList.add('open'); el.remove(); },
  draft: () => draftSheet(),
  'draft-send': async () => {
    const text = $('#draft-text').value;
    try {
      await api('/api/draft/save', { text });
      await api('/api/draft/send', {});
      closeSheet(); toast('📬 Отправляю учителю'); haptic('success'); setTimeout(poll, 400);
    } catch (e) { toast(e.message); }
  },
  'draft-drop': () => confirmDo('Не отправлять этот черновик?', async () => {
    try { S.state = await api('/api/draft/drop', {}); closeSheet(); renderStatus(); toast('👌 Не отправляю'); } catch (e) { toast(e.message); }
  }),
  digest: async () => {
    try { S.me = await api('/api/digest', { on: !(S.me && S.me.digest) }); haptic('select'); settingsSheet(); } catch (e) { toast(e.message); }
  },
  check: async () => { try { await api('/api/check', {}); closeSheet(); toast('🩺 Проверяю — ответ придёт в чат'); } catch (e) { toast(e.message); } },
  alphabet: async () => { try { await api('/api/alphabet', {}); closeSheet(); toast('✍️ Листы алфавита — в чате'); } catch (e) { toast(e.message); } },
  chat: () => { if (tg) tg.close(); },
};

function confirmDo(text, fn) {
  if (tg && tg.showConfirm && tg.isVersionAtLeast && tg.isVersionAtLeast('6.2')) tg.showConfirm(text, ok => ok && fn());
  else if (window.confirm(text)) fn();
}

document.addEventListener('click', e => {
  const el = e.target.closest('[data-a]');
  if (!el || el.disabled) return;
  const fn = ACT[el.dataset.a];
  if (fn) fn(el);
});
document.addEventListener('input', e => {
  if (e.target.id === 'link') { S.link = e.target.value.trim(); updateLinkButtons(); }
});

/* ---------- мелочи ---------- */
let toastTimer;
function toast(text) {
  const t = $('#toast');
  t.textContent = text;
  t.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('on'), 2600);
}

function haptic(kind) {
  const h = tg && tg.HapticFeedback;
  if (!h || !tg.isVersionAtLeast || !tg.isVersionAtLeast('6.1')) return;
  try {
    if (kind === 'success' || kind === 'error' || kind === 'warning') h.notificationOccurred(kind);
    else if (kind === 'select') h.selectionChanged();
    else h.impactOccurred(kind || 'light');
  } catch (e) { /* без вибрации */ }
}

function confetti() {
  const c = $('#confetti'), x = c.getContext('2d');
  const W = c.width = innerWidth * devicePixelRatio, H = c.height = innerHeight * devicePixelRatio;
  const cols = ['#34e0a1', '#3bb2ff', '#ffd166', '#ff6b9a', '#b18cff'];
  const ps = Array.from({ length: 140 }, () => ({
    x: W / 2 + (Math.random() - .5) * W * .3, y: H * .35, vx: (Math.random() - .5) * 26 * devicePixelRatio,
    vy: (-Math.random() * 22 - 8) * devicePixelRatio, r: (4 + Math.random() * 5) * devicePixelRatio, c: cols[Math.random() * cols.length | 0],
    a: Math.random() * 6, va: (Math.random() - .5) * .4,
  }));
  let n = 0;
  (function frame() {
    x.clearRect(0, 0, W, H);
    for (const p of ps) {
      p.vy += .9 * devicePixelRatio; p.vx *= .985; p.x += p.vx; p.y += p.vy; p.a += p.va;
      x.save(); x.translate(p.x, p.y); x.rotate(p.a); x.fillStyle = p.c; x.fillRect(-p.r, -p.r / 2, p.r * 2, p.r); x.restore();
    }
    if (++n < 150) requestAnimationFrame(frame); else x.clearRect(0, 0, W, H);
  })();
}

function theme() {
  const light = tg && tg.colorScheme === 'light';
  document.body.classList.toggle('light', !!light);
  const bg = light ? '#f3f5f8' : '#0a0b0f';
  if (tg && tg.isVersionAtLeast && tg.isVersionAtLeast('6.1')) {
    try { tg.setHeaderColor(bg); tg.setBackgroundColor(bg); } catch (e) { /* старый клиент */ }
  }
  if (tg && tg.isVersionAtLeast && tg.isVersionAtLeast('7.10')) { try { tg.setBottomBarColor(bg); } catch (e) { /* старый клиент */ } }
}

/* таймер живой карточки тикает сам между опросами */
setInterval(() => {
  const el = $('.live .clock');
  if (el) el.textContent = mmss(+el.dataset.secs + (Date.now() - S.polledAt) / 1000);
}, 1000);

function start() {
  if (!tg || !tg.initData) {
    document.body.innerHTML = '<div class="lock"><div class="big">🔒</div><h2>Открой через Telegram</h2><p>Кнопка «HDP» слева от поля ввода в боте</p></div>';
    return;
  }
  tg.ready();
  tg.expand();
  if (tg.isVersionAtLeast('7.7') && tg.disableVerticalSwipes) tg.disableVerticalSwipes();
  theme();
  tg.onEvent('themeChanged', theme);
  if (tg.BackButton) tg.BackButton.onClick(() => closeSheet());
  render();
  loadOverview();
  poll();
  setInterval(() => { if (document.visibilityState === 'visible') poll(); }, 2500);
  setInterval(() => { if (document.visibilityState === 'visible') renderHead(); }, 30000);
}

start();
