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

/* ---------- иконки (контур, 24×24) ---------- */
const I = {
  tasks: '<svg viewBox="0 0 24 24"><rect x="4" y="3" width="16" height="18" rx="4"/><path d="M8.5 9.5l1.6 1.6 3.4-3.4M8.5 15.5h7"/></svg>',
  grades: '<svg viewBox="0 0 24 24"><path d="M5 20V11M12 20V5M19 20v-6"/></svg>',
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
  chev: '<svg viewBox="0 0 24 24"><path d="M9 18l6-6-6-6"/></svg>',
  check: '<svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
  done: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.8 2.8L16.5 9.5"/></svg>',
  doc: '<svg viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/></svg>',
  bell: '<svg viewBox="0 0 24 24"><path d="M6 9a6 6 0 0 1 12 0c0 6 2.5 7.5 2.5 7.5h-17S6 15 6 9z"/><path d="M10 20a2 2 0 0 0 4 0"/></svg>',
  pulse: '<svg viewBox="0 0 24 24"><path d="M3 12h4l2.5-6 5 12 2.5-6h4"/></svg>',
  warn: '<svg viewBox="0 0 24 24"><path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17.2v.1"/></svg>',
};

/* ---------- состояние ---------- */
const S = {
  tab: 'tasks', data: null, loading: false, error: '', state: null, me: null, seen: 0, sheet: null, link: '',
  lastBusy: false, polledAt: 0,
};
try { S.seen = +localStorage.getItem('hdp.seen') || 0; S.tab = localStorage.getItem('hdp.tab') || 'tasks'; } catch (e) { /* без хранилища */ }
if (!['tasks', 'grades', 'teachers'].includes(S.tab)) S.tab = 'tasks';
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
const ic = (name, cls = '') => `<div class="ic ${cls}">${I[name]}</div>`;
const STATUS = { reworking: 'Доработка', failed: 'Не зачтено', appointed: 'Новое' };
const empty = (icon, cls, title, text) => `<div class="empty">${ic(icon, cls)}<h2>${title}</h2><p>${text}</p></div>`;
const skelRows = n => `<div class="skel-list">${Array.from({ length: n }, () =>
  '<div class="skel-row"><div class="sk ic"></div><div class="grow"><div class="sk l1"></div><div class="sk l2"></div></div><div class="sk pill"></div></div>').join('')}</div>`;

/* ---------- отрисовка ---------- */
function render() {
  const v = $('#view');
  v.className = 'enter';
  v.innerHTML = `<div id="head"></div>` + (S.tab === 'tasks' ? tasksView() : S.tab === 'grades' ? gradesView() : teachersView());
  renderHead(); renderStatus(); renderTabs();
  requestAnimationFrame(() => setTimeout(() => v.classList.remove('enter'), 400));
}

function renderHead() {
  const h = $('#head');
  if (!h) return;
  const titles = { tasks: 'Задания', grades: 'Оценки', teachers: 'Учителя' };
  const d = new Date();
  const when = S.loading ? 'обновляю с сайта…' : S.data ? `обновлено ${ago(S.data.at)}` : 'загружаю…';
  const html = `<div class="top">
      <div class="grow">
        <h1 class="title">${titles[S.tab]}</h1>
        <div class="sub one">${d.getDate()} ${MG[d.getMonth()]} · ${esc(when)}</div>
      </div>
      <button class="icon-btn ${S.loading ? 'spin' : ''}" data-a="refresh" aria-label="Обновить">${I.refresh}</button>
      <button class="icon-btn" data-a="settings" aria-label="Настройки">${I.gear}</button>
    </div>` + (S.error && !S.loading ? `<div class="card err">${I.warn}<div class="grow">${esc(S.error)}</div></div>` : '');
  if (h.dataset.html !== html) { h.innerHTML = html; h.dataset.html = html; }  // без лишней перерисовки
}

function renderTabs() {
  const tasks = (S.data && S.data.tasks) || [];
  const late = tasks.filter(t => left(t.deadline) < 0).length;
  const comm = ((S.data && S.data.comments) || []).length;
  const tab = (id, label, n) => `<button class="tab ${S.tab === id ? 'on' : ''}" data-a="tab" data-id="${id}" data-h="select">${I[id]}<span>${label}</span>${n ? `<span class="badge">${n}</span>` : ''}</button>`;
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
    const html = l ? `<button class="card banner" data-a="tab" data-id="tasks">
      <div class="spinner"><i></i></div><div class="grow"><div class="card-t one">${esc(l.title)}</div><div class="card-s one">${l.frac == null ? 'идёт работа' : Math.round(l.frac * 100) + '%'} · подробнее на «Заданиях»</div></div>${I.chev}</button>` : '';
    if (box.dataset.html !== html) { box.innerHTML = html; box.dataset.html = html; }
    return;
  }
  const live = $('.live', box);
  if (st.busy && live && box.dataset.rest === restKey(st)) { updateLive(live, st.live); return; }
  if (st.busy) {
    const l = st.live || { title: 'Работаю', sub: '', step: '', done: [], frac: null, secs: 0 };
    const pct = l.frac == null ? null : Math.round(l.frac * 100);
    out += `<div class="card live">
      <div class="card-h"><div class="spinner"><i></i></div>
        <div class="grow"><div class="card-t one">${esc(l.title)}</div><div class="card-s one">${esc(l.sub)}</div></div></div>
      <ul class="steps">${stepsHtml(l)}</ul>
      <div class="bar ${pct == null ? 'indet' : ''}"><i style="width:${pct || 0}%"></i></div>
      <div class="meta"><span>${pct == null ? 'результат придёт сюда и в чат' : pct + '%'}</span><span class="clock" data-secs="${l.secs || 0}">${mmss(l.secs || 0)}</span></div>
    </div>`;
  }
  const l = st.last;
  if (!st.busy && l && l.at > S.seen) out += resultCard(l);
  if (st.draft) {
    const d = st.draft;
    out += `<button class="card banner" data-a="draft">
      ${ic('doc')}
      <div class="grow"><div class="card-t">Черновик для учителя</div><div class="card-s one">${esc(d.title)}</div></div>
      ${I.chev}</button>`;
  }
  box.innerHTML = out;
  box.dataset.rest = restKey(st);
}

const stepsHtml = l => (l.done || []).map(x => `<li>${I.check}<span>${esc(x)}</span></li>`).join('')
  + (l.step ? `<li class="now"><span>${esc(l.step)}</span></li>` : '');

/* всё, кроме живой карточки: если это не поменялось, карточку только подправляем — анимации не сбиваются */
const restKey = st => JSON.stringify([!!st.busy, st.last && st.last.at > S.seen ? st.last.at : 0, st.draft && st.draft.title, st.live && st.live.title]);

function updateLive(el, l) {
  if (!l) return;
  const pct = l.frac == null ? null : Math.round(l.frac * 100);
  const set = (sel, html) => { const e = $(sel, el); if (e && e.innerHTML !== html) e.innerHTML = html; };
  set('.card-s', esc(l.sub));
  set('.steps', stepsHtml(l));
  const bar = $('.bar', el);
  bar.classList.toggle('indet', pct == null);
  $('i', bar).style.width = (pct || 0) + '%';
  set('.meta span', pct == null ? 'результат придёт сюда и в чат' : pct + '%');
  $('.clock', el).dataset.secs = l.secs || 0;
}

function resultCard(l) {
  const sc = l.score;
  const pct = sc ? sc[2] : null;
  const head = !l.ok ? 'Не получилось' : l.kind === 'sent' ? 'Отправлено учителю' : l.show ? 'Ответы' : 'Готово';
  const C = 2 * Math.PI * 25;
  const col = pct == null ? '' : pct >= 100 ? 'var(--good)' : pct >= 70 ? 'var(--accent)' : 'var(--warn)';
  const ring = pct == null
    ? ic(!l.ok ? 'warn' : l.kind === 'sent' ? 'send' : l.show ? 'eye' : 'done', !l.ok ? 'late' : 'good')
    : `<div class="ring" style="--c:${col}"><svg viewBox="0 0 56 56"><circle class="track" cx="28" cy="28" r="25"/><circle class="val" cx="28" cy="28" r="25" stroke-dasharray="${C}" stroke-dashoffset="${C * (1 - Math.min(pct, 100) / 100)}"/></svg>${pct}%</div>`;
  const score = sc ? `<div class="score">${sc[1] ? `${comma(sc[0])}<small>/ ${comma(sc[1])}</small>` : `${pct}%`}</div>` : `<div class="card-t">${head}</div>`;
  const long = l.text && (l.text.length > 240 || l.text.split('\n').length > 7);
  const text = l.text ? `<div class="text ${long ? '' : 'open'}">${esc(l.text)}</div>${long ? '<button class="more" data-a="more">Показать всё</button>' : ''}` : '';
  const notes = [pct != null && pct >= 100 ? 'Полный балл' : '', l.draft ? 'черновик учителю — ниже' : ''].filter(Boolean).join(' · ');
  return `<div class="card result">
    <div class="card-h">${ring}<div class="grow">${score}<div class="card-s">${esc(l.title)}${l.secs ? ` · ${mmss(l.secs)}` : ''}</div></div>
      <button class="x" data-a="seen" aria-label="Скрыть">${I.x}</button></div>
    ${notes ? `<div class="badge-line">${notes}</div>` : ''}
    ${text}</div>`;
}

function tasksView() {
  if (!S.data) return '<div id="status"></div>' + linkCard() + skelRows(4);
  const tasks = S.data.tasks.slice().sort((a, b) => a.deadline.localeCompare(b.deadline) || a.subj.localeCompare(b.subj) || a.order - b.order);
  const late = tasks.filter(t => left(t.deadline) < 0);
  const week = tasks.filter(t => left(t.deadline) >= 0 && left(t.deadline) <= 7);
  const later = tasks.filter(t => left(t.deadline) > 7);
  const auto = tasks.filter(t => t.auto && t.rawStatus === 'appointed');
  let out = '<div id="status"></div>';
  if (tasks.length) {
    out += `<div class="stats">
      <div class="stat"><b>${tasks.length}</b><span>${plural(tasks.length, 'задание', 'задания', 'заданий')}</span></div>
      <div class="stat ${late.length ? 'late' : 'good'}"><b>${late.length}</b><span>просрочено</span></div>
      <div class="stat ${week.length ? 'warn' : ''}"><b>${week.length}</b><span>на неделе</span></div></div>`;
  }
  out += linkCard();
  if (auto.length) {
    out += `<div class="card banner">${ic('bolt', 'good')}
      <div class="grow"><div class="card-t">${auto.length} ${plural(auto.length, 'автотест', 'автотеста', 'автотестов')}</div><div class="card-s">могу сдать все по очереди</div></div>
      <button class="btn primary small" data-a="auto">Сдать</button></div>`;
  }
  if (!tasks.length) return out + empty('done', 'good', 'Всё сделано', 'Новых заданий нет — можно отдыхать.');
  const section = (title, list, cls = '') => list.length ? `<div class="section ${cls}"><h3>${title}</h3><span class="n">${list.length}</span></div><div class="list">${list.map(taskRow).join('')}</div>` : '';
  return out + section('Просрочено', late, 'late') + section('На этой неделе', week) + section('Позже', later);
}

function taskRow(t) {
  const [w, cls] = whenLabel(t.deadline);
  return `<button class="row" data-a="task" data-url="${esc(t.url)}">
    ${subjIc(t)}
    <div class="grow"><div class="t">${esc(t.title)}</div>
      <div class="m"><span class="tag ${t.rawStatus}">${STATUS[t.rawStatus] || esc(t.st)}</span>${t.auto ? '<span class="tag auto">автотест</span>' : ''}<span class="s">${esc(t.subj)}</span></div></div>
    <span class="when ${cls}">${w}</span></button>`;
}

function linkCard() {
  const ok = LINK.test(S.link);
  return `<div class="card">
    <div class="link-box"><input id="link" type="url" inputmode="url" autocomplete="off" placeholder="Ссылка на задание" value="${esc(S.link)}">
      <button class="icon-btn" data-a="paste" aria-label="Вставить">${I.paste}</button></div>
    <div class="link-actions">
      <button class="btn primary" data-a="solve" data-show="0" ${ok ? '' : 'disabled'}><span>Решить и сдать</span></button>
      <button class="btn" data-a="solve" data-show="1" ${ok ? '' : 'disabled'}><span>Только ответы</span></button>
    </div></div>`;
}

const MINI = '<div id="status" class="mini"></div>';

function gradesView() {
  return MINI + gradesBody();
}

function gradesBody() {
  if (!S.data) return '<div class="sk block"></div>' + skelRows(4);
  const g = S.data.grades;
  if (!g.length) return empty('grades', '', 'Оценок пока нет', 'Появятся, когда учителя проверят работы.');
  const avg = g.reduce((s, r) => s + r.five, 0) / g.length;
  const cnt = f => g.filter(r => (f === 4 ? r.five >= 4 : r.five === f)).length;
  let out = `<div class="card hero">
    <div class="grow"><div class="avg">${comma(avg.toFixed(1))}</div><div class="avg-l">средняя по ${g.length} ${plural(g.length, 'предмету', 'предметам', 'предметам')}</div></div>
    <div class="dist"><div><span class="chip good">4–5</span>${cnt(4)}</div><div><span class="chip warn">3</span>${cnt(3)}</div><div><span class="chip late">2</span>${cnt(2)}</div></div>
  </div>`;
  out += '<div class="section"><h3>Предметы</h3><span class="n">от слабых к сильным</span></div>';
  const tick = x => `<b style="left:${x / 4 * 100}%"></b>`;
  const color = f => ({ 5: 'var(--good)', 4: 'var(--accent)', 3: 'var(--warn)', 2: 'var(--late)' }[f]);
  out += '<div class="list">' + g.map(r => `<div class="grade">
    <div class="g g${r.five}">${r.five}</div>
    <div class="grow"><div class="n">${esc(r.name)}</div>
      <div class="lvl"><i style="width:${Math.min(100, r.lvl / 4 * 100)}%;background:${color(r.five)}"></i>${tick(2)}${tick(2.5)}${tick(3.5)}</div>
      <div class="hint">уровень <strong>${lvlFmt(r.lvl)}</strong> · ${r.next ? `до «${r.next[1]}» ещё <strong>${lvlFmt(Math.max(0.1, r.next[0] - r.lvl))}</strong>` : 'максимум'}</div></div>
  </div>`).join('') + '</div>';
  return out + '<div class="foot">Уровень → оценка: от 2,0 — 3, от 2,5 — 4, от 3,5 — 5</div>';
}

function teachersView() {
  return MINI + teachersBody();
}

function teachersBody() {
  if (!S.data) return skelRows(3);
  const c = S.data.comments;
  if (!c.length) return empty('done', 'good', 'Доработок нет', 'Учителя всё приняли.');
  return `<div class="stats two">
      <div class="stat warn"><b>${c.filter(r => !r.failed).length}</b><span>на доработке</span></div>
      <div class="stat late"><b>${c.filter(r => r.failed).length}</b><span>не зачтено</span></div></div>`
    + c.map(r => `<button class="card comment" data-a="task" data-url="${esc(r.url)}">
      <div class="card-h">${subjIc(r)}<div class="grow"><div class="card-s one">${esc(r.subj)}</div><div class="card-t">${esc(r.title)}</div></div>
        ${r.score != null ? `<span class="pct ${r.failed ? 'failed' : 'reworking'}">${r.score}%</span>` : ''}</div>
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
    ${t.auto ? '<span class="chip good">автотест</span>' : ''}</div>`;
  const comment = c && (c.comment || c.score != null) ? `<div class="s-label">Учитель</div><div class="comment"><div class="q ${c.comment ? '' : 'none'}">${esc(c.comment || 'Без комментария')}${c.score != null ? ` · ${c.score}%` : ''}</div></div>` : '';
  const dis = busy ? 'disabled' : '';
  const actions = t.auto
    ? `<button class="btn primary" data-a="solve-url" data-url="${esc(url)}" data-show="0" ${dis}>${I.bolt}<span>Решить и сдать</span></button>
       <button class="btn" data-a="solve-url" data-url="${esc(url)}" data-show="1" ${dis}>${I.eye}<span>Только ответы</span></button>`
    : `<button class="btn primary" data-a="solve-url" data-url="${esc(url)}" data-show="0" ${dis}>${I.pen}<span>Подготовить ответ</span></button>
       <button class="btn" data-a="solve-url" data-url="${esc(url)}" data-show="1" ${dis}>${I.eye}<span>Только посмотреть</span></button>`;
  openSheet(`<div class="card-h">${subjIc(t, 's-ic')}<div class="grow"><div class="card-s">${esc(t.subj)}${t.topic ? ' · ' + esc(t.topic) : ''}</div></div></div>
    <h2>${esc(t.title)}</h2>${chips}${comment}
    <div class="btn-col">${actions}
      <button class="btn ghost" data-a="open" data-url="${esc(url)}">${I.ext}<span>Открыть на сайте</span></button></div>
    <div class="note">${busy ? 'Сейчас занят другим заданием' : t.auto ? 'Результат покажу здесь и пришлю в чат' : 'Сначала покажу черновик — отправишь сам'}</div>`, 'task');
}

function draftSheet() {
  const d = S.state && S.state.draft;
  if (!d) return;
  openSheet(`<div class="s-sub" style="margin-top:0">Черновик для учителя</div><h2>${esc(d.title)}</h2>
    <textarea id="draft-text">${esc(d.text)}</textarea>
    ${d.files ? `<div class="note">${d.files} ${plural(d.files, 'картинка', 'картинки', 'картинок')} — в чате, уйдут вместе с текстом</div>` : ''}
    <div class="btn-col">
      <button class="btn primary" data-a="draft-send">${I.send}<span>Отправить учителю</span></button>
      <button class="btn danger" data-a="draft-drop"><span>Не отправлять</span></button></div>
    <div class="note">Поправь текст прямо здесь — запомню, как ты пишешь</div>`, 'draft');
}

function settingsSheet() {
  const me = S.me || {};
  openSheet(`<h2 style="margin-top:0">Настройки</h2>
    <div class="set-group">
      <button class="set-row" data-a="digest" data-h="select">${ic('bell', 'warn')}<span class="grow"><div class="card-t">Сводка каждый день</div><div class="card-s">в ${me.hour || 15}:00 по Москве — что надо сделать</div></span><span class="switch ${me.digest ? 'on' : ''}"></span></button>
      <button class="set-row" data-a="check">${ic('pulse', 'good')}<span class="grow"><div class="card-t">Проверить, что всё работает</div><div class="card-s">сайт, Claude, почерк — ответ придёт в чат</div></span>${I.chev}</button>
      <button class="set-row" data-a="alphabet">${ic('pen')}<span class="grow"><div class="card-t">Мой почерк</div><div class="card-s">${me.alphabet ? 'алфавит заполнен · прислать заново' : 'алфавит не заполнен — пришлю листы в чат'}</div></span>${I.chev}</button>
    </div>
    <div class="set-group">
      <button class="set-row" data-a="chat">${ic('teachers')}<span class="grow"><div class="card-t">Вернуться в чат</div><div class="card-s">кнопки бота там тоже работают</div></span>${I.chev}</button>
    </div>
    <div class="foot">HDP · версия ${esc(me.version || '—')}</div>`, 'settings');
}

/* ---------- действия ---------- */
async function solve(url, show) {
  try {
    await api('/api/solve', { url, show: !!show });
    closeSheet();
    S.link = '';
    if (S.tab === 'tasks') { const i = $('#link'); if (i) i.value = ''; updateLinkButtons(); }
    else { S.tab = 'tasks'; store('hdp.tab', S.tab); render(); }
    window.scrollTo({ top: 0, behavior: 'smooth' });
    toast(show ? 'Смотрю ответы — прогресс сверху' : 'Начал — прогресс сверху');
    haptic('success');
    setTimeout(poll, 400);
  } catch (e) { toast(e.message); haptic('error'); }
}

function updateLinkButtons() {
  const ok = LINK.test(S.link);
  document.querySelectorAll('[data-a="solve"]').forEach(b => { b.disabled = !ok; });
}

const ACT = {
  tab: el => { if (S.tab === el.dataset.id) { window.scrollTo({ top: 0, behavior: 'smooth' }); return; } S.tab = el.dataset.id; store('hdp.tab', S.tab); window.scrollTo(0, 0); render(); },
  refresh: () => { S.loading = true; renderHead(); loadOverview(true); },
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
      if (!m) { toast('В буфере нет ссылки на задание'); haptic('warning'); return; }
      S.link = m[0]; $('#link').value = S.link; updateLinkButtons();
    } catch (e) { $('#link').focus(); toast('Вставь ссылку в поле'); }
  },
  auto: () => {
    const n = ((S.data && S.data.tasks) || []).filter(t => t.auto && t.rawStatus === 'appointed').length;
    confirmDo(`Сдать ${n} ${plural(n, 'автотест', 'автотеста', 'автотестов')} по очереди? Это ${n * 2}–${n * 3} мин.`, async () => {
      try { await api('/api/auto', {}); toast('Сдаю — прогресс сверху'); haptic('success'); setTimeout(poll, 400); } catch (e) { toast(e.message); haptic('error'); }
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
      closeSheet(); toast('Отправляю учителю'); haptic('success'); setTimeout(poll, 400);
    } catch (e) { toast(e.message); haptic('error'); }
  },
  'draft-drop': () => confirmDo('Не отправлять этот черновик?', async () => {
    try { S.state = await api('/api/draft/drop', {}); closeSheet(); renderStatus(); toast('Не отправляю'); } catch (e) { toast(e.message); }
  }),
  digest: async () => {
    try { S.me = await api('/api/digest', { on: !(S.me && S.me.digest) }); settingsSheet(); } catch (e) { toast(e.message); }
  },
  check: async () => { try { await api('/api/check', {}); closeSheet(); toast('Проверяю — ответ придёт в чат'); } catch (e) { toast(e.message); } },
  alphabet: async () => { try { await api('/api/alphabet', {}); closeSheet(); toast('Листы алфавита — в чате'); } catch (e) { toast(e.message); } },
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
  if (!fn) return;
  haptic(el.dataset.h || 'light');  // отклик на каждое нажатие; успех и ошибка — отдельной вибрацией
  fn(el);
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
  const css = getComputedStyle(document.body);
  const cols = ['--accent', '--good', '--warn', '--late'].map(v => css.getPropertyValue(v).trim() || '#3e88f7');
  const ps = Array.from({ length: 120 }, () => ({
    x: W / 2 + (Math.random() - .5) * W * .3, y: H * .35, vx: (Math.random() - .5) * 24 * devicePixelRatio,
    vy: (-Math.random() * 20 - 8) * devicePixelRatio, r: (3 + Math.random() * 4) * devicePixelRatio, c: cols[Math.random() * cols.length | 0],
    a: Math.random() * 6, va: (Math.random() - .5) * .4,
  }));
  let n = 0;
  (function frame() {
    x.clearRect(0, 0, W, H);
    for (const p of ps) {
      p.vy += .9 * devicePixelRatio; p.vx *= .985; p.x += p.vx; p.y += p.vy; p.a += p.va;
      x.save(); x.translate(p.x, p.y); x.rotate(p.a); x.fillStyle = p.c; x.fillRect(-p.r, -p.r / 2, p.r * 2, p.r); x.restore();
    }
    if (++n < 140) requestAnimationFrame(frame); else x.clearRect(0, 0, W, H);
  })();
}

function theme() {
  const light = tg && tg.colorScheme === 'light';
  document.body.classList.toggle('light', !!light);
  if (!tg || !tg.isVersionAtLeast) return;
  // шапка и фон Telegram — тем же цветом, что и страница (цвет «вторичного фона» темы)
  try { if (tg.isVersionAtLeast('6.1')) { tg.setHeaderColor('secondary_bg_color'); tg.setBackgroundColor('secondary_bg_color'); } } catch (e) { /* старый клиент */ }
  try { if (tg.isVersionAtLeast('7.10')) tg.setBottomBarColor('secondary_bg_color'); } catch (e) { /* старый клиент */ }
}

/* таймер живой карточки тикает сам между опросами */
setInterval(() => {
  const el = $('.live .clock');
  if (el) el.textContent = mmss(+el.dataset.secs + (Date.now() - S.polledAt) / 1000);
}, 1000);

function start() {
  if (!tg || !tg.initData) {
    document.body.innerHTML = `<div class="lock">${ic('teachers')}<h2>Открой через Telegram</h2><p>Кнопка «HDP» слева от поля ввода в боте или «✨ Приложение» в меню</p></div>`;
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
