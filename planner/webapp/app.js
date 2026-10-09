'use strict';
/* Ритм — планер внутри Telegram. Без сборки и библиотек.
   Данные: GET /api/all один раз, дальше изменения точечно (POST /api/...), интерфейс обновляется сразу. */

const tg = window.Telegram && window.Telegram.WebApp;
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const pad = n => String(n).padStart(2, '0');
const plural = (n, a, b, c) => { n = Math.abs(n); const m = n % 10, h = n % 100; return m === 1 && h !== 11 ? a : m >= 2 && m <= 4 && (h < 12 || h > 14) ? b : c; };

/* ---------- даты (строки YYYY-MM-DD, считаем в UTC, чтобы не мешали часовые пояса) ---------- */
const D = {
  parse: s => { const [y, m, d] = s.split('-').map(Number); return new Date(Date.UTC(y, m - 1, d)); },
  iso: d => d.toISOString().slice(0, 10),
  add: (s, n) => { const d = D.parse(s); d.setUTCDate(d.getUTCDate() + n); return D.iso(d); },
  wd: s => (D.parse(s).getUTCDay() + 6) % 7,               // 0 = пн
  monday: s => D.add(s, -D.wd(s)),
  first: s => s.slice(0, 8) + '01',
  addMonths: (s, n) => { const d = D.parse(D.first(s)); d.setUTCMonth(d.getUTCMonth() + n); return D.iso(d); },
  dim: s => { const d = D.parse(D.first(s)); d.setUTCMonth(d.getUTCMonth() + 1); d.setUTCDate(0); return d.getUTCDate(); },
  diff: (a, b) => Math.round((D.parse(a) - D.parse(b)) / 864e5),
  day: s => +s.slice(8, 10),
  mon: s => +s.slice(5, 7) - 1,
  year: s => +s.slice(0, 4),
  week: s => { const d = D.parse(s); const t = new Date(d); t.setUTCDate(d.getUTCDate() + 3 - D.wd(s)); const y = new Date(Date.UTC(t.getUTCFullYear(), 0, 4)); return 1 + Math.round(((t - y) / 864e5 - 3 + ((y.getUTCDay() + 6) % 7)) / 7); },
};
const WD = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс'];
const WDL = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'];
const MG = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
const MS = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
const MN = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
const dm = s => `${D.day(s)} ${MG[D.mon(s)]}`;
const dms = s => `${D.day(s)} ${MS[D.mon(s)]}`;
function rel(s) {
  const n = D.diff(s, S.today);
  return n === 0 ? 'Сегодня' : n === 1 ? 'Завтра' : n === -1 ? 'Вчера' : n === 2 ? 'Послезавтра' : null;
}
function span(mon) {
  const sun = D.add(mon, 6);
  return D.mon(mon) === D.mon(sun) ? `${D.day(mon)} – ${D.day(sun)} ${MG[D.mon(sun)]}` : `${dms(mon)} – ${dms(sun)}`;
}
function repeatText(r) {
  if (r === '1234567') return 'Каждый день';
  if (r === '12345') return 'По будням';
  if (r === '67') return 'По выходным';
  return 'По ' + [...r].map(c => WD[+c - 1]).join(', ');
}
function nowHM() { const d = new Date(); return pad(d.getHours()) + ':' + pad(d.getMinutes()); }

/* ---------- иконки ---------- */
const I = {
  day: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
  week: '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="17" rx="3"/><path d="M3 9h18M8 2v4M16 2v4M7.5 13.5h3M13.5 13.5h3M7.5 17h3"/></svg>',
  month: '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="17" rx="3"/><path d="M3 9h18M8 2v4M16 2v4"/><circle cx="8" cy="13.5" r=".6"/><circle cx="12" cy="13.5" r=".6"/><circle cx="16" cy="13.5" r=".6"/><circle cx="8" cy="17" r=".6"/><circle cx="12" cy="17" r=".6"/></svg>',
  goals: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.2"/></svg>',
  gear: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>',
  left: '<svg viewBox="0 0 24 24"><path d="M15 18l-6-6 6-6"/></svg>',
  right: '<svg viewBox="0 0 24 24"><path d="M9 18l6-6-6-6"/></svg>',
  plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
  check: '<svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
  repeat: '<svg viewBox="0 0 24 24"><path d="M17 2l4 4-4 4"/><path d="M3 11V9a3 3 0 0 1 3-3h15M7 22l-4-4 4-4"/><path d="M21 13v2a3 3 0 0 1-3 3H3"/></svg>',
  note: '<svg viewBox="0 0 24 24"><path d="M4 4h16v12l-4 4H4z"/><path d="M8 9h8M8 13h5"/></svg>',
  bell: '<svg viewBox="0 0 24 24"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.9 1.9 0 0 0 3.4 0"/></svg>',
  send: '<svg viewBox="0 0 24 24"><path d="M12 19V5M5 12l7-7 7 7"/></svg>',
  cal: '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="17" rx="3"/><path d="M3 9h18M8 2v4M16 2v4"/></svg>',
  clock: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
  x: '<svg viewBox="0 0 24 24"><path d="M18 6L6 18M6 6l12 12"/></svg>',
  flag: '<svg viewBox="0 0 24 24"><path d="M5 21V4M5 4h11l-2 4 2 4H5"/></svg>',
};
const COLORS = { violet: '#8b7cff', blue: '#4da3ff', teal: '#2dd4bf', green: '#4ade80', amber: '#fbbf24', orange: '#fb923c', rose: '#fb7185', pink: '#f472b6' };
const EMOJI = ['🎯', '🏆', '🥊', '💪', '🏃', '📚', '🧠', '💡', '💻', '🚀', '🎨', '🎵', '🎮', '📈', '💰', '🏠', '✈️', '🌱', '🧘', '❤️', '😴', '🍎', '💧', '📝', '🎓', '🗣️', '🤝', '🎉', '⭐', '🔥', '⚡', '🌍'];
const HZ = { week: 'Неделя', month: 'Месяц', year: 'Год', none: 'Без срока' };
const gc = g => COLORS[g && g.color] || COLORS.violet;

/* ---------- состояние ---------- */
const S = {
  user: null, today: null, tasks: new Map(), comp: new Set(), goals: new Map(), notes: new Map(),
  view: 'day', sel: null, week: null, month: null, msel: null, goalId: null, goalsTab: 'active',
  prev: 'goals', pop: null, lastKey: '',
};

async function api(path, body) {
  const r = await fetch('api/' + path, {
    method: body ? 'POST' : 'GET',
    headers: { 'Content-Type': 'application/json', 'X-Init-Data': (tg && tg.initData) || '' },
    body: body ? JSON.stringify(body) : undefined,
  });
  let j = {};
  try { j = await r.json(); } catch (e) { j = { error: 'Сервер не ответил' }; }
  if (!r.ok) throw new Error(j.error || 'Ошибка ' + r.status);
  return j;
}

function load(data) {
  S.user = data.user;
  S.today = data.today;
  S.tasks = new Map(data.tasks.map(t => [t.id, t]));
  S.comp = new Set(data.completions.map(c => c.task + '|' + c.date));
  S.goals = new Map(data.goals.map(g => [g.id, g]));
  S.notes = new Map(data.notes.map(n => [n.id, n]));
}
function mergeGoals(list) { if (list) list.forEach(g => S.goals.set(g.id, g)); }

/* ---------- логика (как в store.py) ---------- */
function occurs(t, day) {
  if (!t.repeat) return t.date === day;
  if (!t.date || day < t.date) return false;
  return t.repeat.includes(String(D.wd(day) + 1)) && !(t.skips || '').split(',').includes(day);
}
const isDone = (t, day) => t.repeat ? S.comp.has(t.id + '|' + day) : !!t.done;
function setDone(t, day, v) {
  if (t.repeat) { v ? S.comp.add(t.id + '|' + day) : S.comp.delete(t.id + '|' + day); }
  else { t.done = v ? 1 : 0; }
}
function dayTasks(day) {
  const out = [];
  for (const t of S.tasks.values()) if (occurs(t, day)) out.push(t);
  return out.sort((a, b) => (a.time ? 0 : 1) - (b.time ? 0 : 1) || (a.time || '').localeCompare(b.time || '') || b.important - a.important || a.pos - b.pos);
}
function overdue() {
  return [...S.tasks.values()].filter(t => !t.repeat && !t.done && t.date && t.date < S.today).sort((a, b) => a.date.localeCompare(b.date));
}
const steps = g => [...S.tasks.values()].filter(t => t.goal === g.id && !t.repeat);
const habits = g => [...S.tasks.values()].filter(t => t.goal === g.id && t.repeat);
function pct(g) {
  if (g.status === 'done') return 100;
  const st = steps(g);
  return st.length ? Math.round(100 * st.filter(t => t.done).length / st.length) : (g.progress || 0);
}
function periodStart(hz, day) {
  return hz === 'week' ? D.monday(day) : hz === 'month' ? D.first(day) : hz === 'year' ? day.slice(0, 4) + '-01-01' : null;
}
function periodEnd(g) {
  if (!g.start) return null;
  return g.horizon === 'week' ? D.add(g.start, 6) : g.horizon === 'month' ? D.add(g.start, D.dim(g.start) - 1) : g.horizon === 'year' ? g.start.slice(0, 4) + '-12-31' : null;
}
function goalsFor(hz, day) {
  const st = periodStart(hz, day);
  return [...S.goals.values()].filter(g => g.status === 'active' && g.horizon === hz && g.start === st);
}
const notesOf = g => [...S.notes.values()].filter(n => n.goal === g.id).sort((a, b) => b.created.localeCompare(a.created) || b.id - a.id);

/* ---------- маленькие куски разметки ---------- */
function ring(p, size = 56, sw = 6, label) {
  const r = size / 2 - sw / 2 - 1, c = 2 * Math.PI * r;
  return `<div class="ring${size > 70 ? ' big' : ''}${p >= 100 ? ' full' : ''}" style="width:${size}px;height:${size}px">
    <svg viewBox="0 0 ${size} ${size}"><circle class="track" cx="${size / 2}" cy="${size / 2}" r="${r}"/>
    <circle class="val" cx="${size / 2}" cy="${size / 2}" r="${r}" stroke-dasharray="${c}" stroke-dashoffset="${c * (1 - Math.min(p, 100) / 100)}"/></svg>
    <div class="lbl">${label != null ? label : p + '%'}</div></div>`;
}
const bar = (p, cls = '', color) => `<div class="bar ${cls}"><i style="width:${Math.max(0, Math.min(100, p))}%${color ? ';background:' + color : ''}"></i></div>`;
const checkBox = (on, color) => `<span class="check${on ? ' on' : ''}" style="--c:${color || 'var(--accent)'}">${I.check}</span>`;

function taskRow(t, day, opts = {}) {
  const done = isDone(t, day);
  const g = t.goal && S.goals.get(t.goal);
  const meta = [];
  if (opts.showDate && t.date && !t.repeat) meta.push(`<span>${I.cal}${rel(t.date) || dms(t.date)}</span>`);
  if (g) meta.push(`<span style="color:${gc(g)}">${esc(g.emoji)} ${esc(g.title)}</span>`);
  if (t.repeat) meta.push(`<span>${I.repeat}${repeatText(t.repeat)}</span>`);
  if (t.time && t.remind >= 0) meta.push(`<span>${I.bell}${t.remind ? t.remind + ' мин' : ''}</span>`);
  if (t.note) meta.push(`<span>${I.note}заметка</span>`);
  const key = t.id + '|' + day;
  return `<div class="task${done ? ' done' : ''}${t.important ? ' imp' : ''}${opts.now ? ' now' : ''}" data-a="edit-task" data-id="${t.id}" data-date="${day || ''}">
    ${opts.noTime ? '' : `<div class="time${t.time ? '' : ' none'}">${t.time || '—'}</div>`}
    <span data-a="toggle" data-id="${t.id}" data-date="${day || ''}">${checkBox(done, g ? gc(g) : null).replace('class="check', `class="check${S.pop === key ? ' pop' : ''}`)}</span>
    <div class="body"><div class="t">${t.important ? '<span class="flag">! </span>' : ''}${esc(t.title)}</div>${meta.length ? `<div class="meta">${meta.join('')}</div>` : ''}</div>
    ${opts.after || ''}
  </div>`;
}
function miniRow(t, day) {
  const done = isDone(t, day), g = t.goal && S.goals.get(t.goal), key = t.id + '|' + day;
  return `<div class="mini${done ? ' done' : ''}" data-a="edit-task" data-id="${t.id}" data-date="${day}">
    <span data-a="toggle" data-id="${t.id}" data-date="${day}">${checkBox(done, g ? gc(g) : null).replace('class="check', `class="check${S.pop === key ? ' pop' : ''}`)}</span>
    <span class="t">${t.important ? '<b style="color:var(--bad)">! </b>' : ''}${g ? esc(g.emoji) + ' ' : ''}${esc(t.title)}</span>
    ${t.time ? `<span class="tm">${t.time}</span>` : ''}${t.repeat ? `<span class="tm">${I.repeat.replace('<svg', '<svg style="width:13px;height:13px"')}</span>` : ''}
  </div>`;
}
function quickRow(date, goal, placeholder = 'Добавить задачу') {
  return `<div class="quick"><span class="plus">${I.plus}</span>
    <input data-quick enterkeyhint="done" placeholder="${placeholder}" data-date="${date || ''}" data-goal="${goal || ''}" maxlength="300"></div>`;
}
function goalChip(g) {
  const p = pct(g);
  return `<button class="gchip" style="--gc:${gc(g)}" data-a="open-goal" data-id="${g.id}">
    <div class="e">${esc(g.emoji)}</div><div class="gt">${esc(g.title)}</div>${bar(p, 'thin')}<div class="pct">${p}%</div></button>`;
}
function goalRow(g) {
  const p = pct(g), st = steps(g), nn = notesOf(g).length, sub = [];
  const end = periodEnd(g);
  if (g.due) sub.push(`🏁 ${dms(g.due)}`);
  else if (g.status === 'active' && end) sub.push(g.horizon === 'year' ? `до конца ${end.slice(0, 4)}` : `до ${dms(end)}`);
  if (st.length) sub.push(`${st.filter(t => t.done).length}/${st.length} ${plural(st.length, 'шаг', 'шага', 'шагов')}`);
  if (nn) sub.push(`📝 ${nn}`);
  return `<button class="goal${g.status === 'done' ? ' isdone' : ''}" style="--gc:${gc(g)}" data-a="open-goal" data-id="${g.id}">
    <div class="em">${esc(g.emoji)}</div>
    <div class="gb"><div class="gt">${esc(g.title)}</div><div class="gs">${sub.join('<span>·</span>') || '&nbsp;'}</div>${bar(p, 'thin')}</div>
    <div class="gp">${p}%</div></button>`;
}
function sec(title, extra = '', link = '') {
  return `<div class="sec"><h3>${title}</h3>${extra ? `<span class="count">${extra}</span>` : ''}<span class="spacer"></span>${link}</div>`;
}
function linkify(text) {
  return esc(text).replace(/https?:\/\/[^\s<]+/g, u => `<a href="${u}" data-link>${u}</a>`);
}
function noteDate(isoStr) {
  const d = new Date(isoStr);
  const local = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const r = rel(local);
  const base = r || (d.getFullYear() === D.year(S.today) ? dms(local) : `${dms(local)} ${d.getFullYear()}`);
  return `${base}, ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/* ---------- экраны ---------- */
function viewDay() {
  const day = S.sel, tasks = dayTasks(day), done = tasks.filter(t => isDone(t, day)).length;
  const isToday = day === S.today, mon = D.monday(day);
  let html = `<div class="top"><div class="grow"><div class="eyebrow">${WDL[D.wd(day)]}${rel(day) ? ' · ' + rel(day).toLowerCase() : ''}</div>
      <div class="title">${dm(day)}</div></div>
      ${isToday ? '' : `<button class="pill-btn" data-a="go-today">Сегодня</button>`}
      <button class="icon-btn" data-a="settings" aria-label="Настройки">${I.gear}</button></div>`;
  html += `<div class="strip" data-swipe="week">${[0, 1, 2, 3, 4, 5, 6].map(i => {
    const d = D.add(mon, i), ts = dayTasks(d), dn = ts.filter(t => isDone(t, d)).length;
    const dots = ts.slice(0, 3).map((t, k) => `<i class="${dn === ts.length ? 'ok' : ''}"></i>`).join('');
    return `<button class="d${d === S.today ? ' today' : ''}${d === day ? ' sel' : ''}" data-a="pick-day" data-date="${d}">
      <span class="wd">${WD[i]}</span><span class="n">${D.day(d)}</span><span class="dots">${dots}</span></button>`;
  }).join('')}</div>`;

  const nothing = !S.tasks.size && !S.goals.size;
  if (nothing && !store.get('welcomed')) {
    html += `<div class="card welcome"><button class="x" data-a="welcome-close">${I.x}</button>
      <b>Привет! Это твой планер ✨</b><p>Всё просто:</p>
      <ul><li>📝 Нажми <b>+</b> — добавь задачу на день, время и напоминание</li>
      <li>🎯 Во вкладке <b>Цели</b> поставь цель на неделю, месяц или год и веди к ней заметки</li>
      <li>💬 Или просто напиши боту: <i>«завтра в 18:00 тренировка»</i></li></ul>
      <button class="btn wide" data-a="fab">Добавить первую задачу</button></div>`;
  }

  if (tasks.length) {
    const p = Math.round(100 * done / tasks.length), left = tasks.length - done;
    const msg = left === 0 ? 'Всё сделано. Ты молодец 🎉' : done === 0 ? (isToday ? 'Начни с самого важного' : 'Запланировано') : `Осталось ${left} — продолжай в том же духе`;
    html += `<div class="card summary">${ring(p)}<div class="txt"><b>${done} из ${tasks.length} ${plural(tasks.length, 'задачи', 'задач', 'задач')}</b><span>${msg}</span></div></div>`;
  }

  if (isToday) {
    const late = overdue();
    if (late.length) {
      html += sec('Просрочено', late.length, `<button class="link" data-a="all-today">Все на сегодня</button>`);
      html += `<div class="card">${late.map(t => taskRow(t, t.date, { showDate: true, noTime: true, after: `<button class="arrow-btn" data-a="to-today" data-id="${t.id}">→ сегодня</button>` })).join('')}</div>`;
    }
  }

  const focus = [...goalsFor('week', day), ...goalsFor('month', day)];
  if (focus.length) {
    html += sec('Цели в фокусе', '', `<button class="link" data-a="tab" data-v="goals">Все</button>`);
    html += `<div class="goal-chips">${focus.map(goalChip).join('')}<button class="gchip add" data-a="new-goal" data-h="week">＋ Цель</button></div>`;
  }

  html += sec('План', tasks.length ? `${tasks.length}` : '');
  if (tasks.length) {
    let nowId = null;
    if (isToday) { const hm = nowHM(); const n = tasks.find(t => t.time && t.time >= hm && !isDone(t, day)); nowId = n && n.id; }
    html += `<div class="card">${tasks.map(t => taskRow(t, day, { now: t.id === nowId })).join('')}${quickRow(day)}</div>`;
  } else {
    html += `<div class="card"><div class="empty"><div class="big">${isToday ? '🌤' : '🗓'}</div><b>${isToday ? 'Свободный день' : 'Пока пусто'}</b>
      <p>Добавь задачи — со временем и напоминанием или просто списком.</p></div>${quickRow(day)}</div>`;
  }
  return html;
}

function viewWeek() {
  const mon = S.week, isCur = mon === D.monday(S.today);
  let total = 0, done = 0;
  const days = [0, 1, 2, 3, 4, 5, 6].map(i => {
    const d = D.add(mon, i), ts = dayTasks(d), dn = ts.filter(t => isDone(t, d)).length;
    total += ts.length; done += dn;
    return { d, ts, dn };
  });
  let html = `<div class="top"><div class="grow"><div class="eyebrow">Неделя ${D.week(mon)}${isCur ? ' · эта' : D.diff(mon, D.monday(S.today)) === 7 ? ' · следующая' : D.diff(mon, D.monday(S.today)) === -7 ? ' · прошлая' : ''}</div>
    <div class="title">${span(mon)}</div></div>
    ${isCur ? '' : `<button class="pill-btn" data-a="go-today">Сейчас</button>`}
    <div class="nav"><button class="icon-btn" data-a="shift" data-n="-1">${I.left}</button><button class="icon-btn" data-a="shift" data-n="1">${I.right}</button></div></div>`;
  if (total) {
    const p = Math.round(100 * done / total);
    html += `<div class="card summary" style="margin-top:10px">${ring(p)}<div class="txt"><b>${done} из ${total} за неделю</b>
      <span>${p === 100 ? 'Идеальная неделя 🔥' : p >= 60 ? 'Отличный темп' : 'Шаг за шагом'}</span></div></div>`;
  }
  const goals = goalsFor('week', mon);
  html += sec('Цели недели', goals.length || '');
  html += `<div class="goal-chips">${goals.map(goalChip).join('')}<button class="gchip add" data-a="new-goal" data-h="week" data-start="${mon}">＋ Цель на неделю</button></div>`;
  html += sec('Дни');
  html += days.map(({ d, ts, dn }) => {
    const past = d < S.today, today = d === S.today;
    return `<div class="card day-card${today ? ' today' : ''}${past && !today ? ' past' : ''}">
      <div class="dh" data-a="open-day" data-date="${d}"><div class="dn"><b>${D.day(d)}</b><small>${WD[D.wd(d)]}</small></div>
        <div class="dtx"><b>${WDL[D.wd(d)]}</b>${today ? ' <span style="color:var(--accent);font-size:13px;font-weight:600">· сегодня</span>' : ''}${ts.length ? bar(Math.round(100 * dn / ts.length), 'thin') : ''}</div>
        <span class="dc">${ts.length ? `${dn}/${ts.length}` : ''}</span>${I.right.replace('<svg', '<svg style="color:var(--muted);width:16px"')}</div>
      ${ts.length ? ts.map(t => miniRow(t, d)).join('') : '<div class="free">Свободно</div>'}
      <button class="add-line" data-a="new-task" data-date="${d}">${I.plus}Добавить</button></div>`;
  }).join('');
  const inbox = [...S.tasks.values()].filter(t => !t.date && !t.repeat && !t.done);
  html += sec('Без даты', inbox.length || '');
  html += `<div class="card">${inbox.map(t => taskRow(t, null, { noTime: true })).join('')}${quickRow('', '', 'Когда-нибудь…')}</div>`;
  return html;
}

function viewMonth() {
  const first = S.month, n = D.dim(first), start = D.monday(first);
  const cells = Math.ceil((D.diff(first, start) + n) / 7) * 7;
  let total = 0, done = 0;
  let grid = '';
  for (let i = 0; i < cells; i++) {
    const d = D.add(start, i), out = d.slice(0, 7) !== first.slice(0, 7);
    const ts = dayTasks(d), dn = ts.filter(t => isDone(t, d)).length;
    if (!out) { total += ts.length; done += dn; }
    const dots = ts.slice(0, 3).map(t => { const g = t.goal && S.goals.get(t.goal); return `<i style="${g ? '--gc:' + gc(g) : ''}"></i>`; }).join('');
    const cls = ['c', out && 'out', D.wd(d) >= 5 && 'we', d === S.today && 'today', d === S.msel && 'sel', ts.length && dn === ts.length && d <= S.today && 'alldone'].filter(Boolean).join(' ');
    grid += `<button class="${cls}" data-a="cal-day" data-date="${d}"><span class="n">${D.day(d)}</span><span class="dots">${dots}</span></button>`;
  }
  const isCur = first === D.first(S.today);
  let html = `<div class="top"><div class="grow"><div class="eyebrow">${D.year(first)}${isCur ? ' · этот месяц' : ''}</div><div class="title">${MN[D.mon(first)]}</div></div>
    ${isCur ? '' : `<button class="pill-btn" data-a="go-today">Сейчас</button>`}
    <div class="nav"><button class="icon-btn" data-a="shift" data-n="-1">${I.left}</button><button class="icon-btn" data-a="shift" data-n="1">${I.right}</button></div></div>`;
  html += `<div class="card cal" data-swipe="month" style="margin-top:10px"><div class="wds">${WD.map(w => `<div>${w}</div>`).join('')}</div><div class="grid">${grid}</div>
    <div class="legend"><span><i style="background:var(--accent)"></i>задачи</span><span><i style="background:var(--good)"></i>всё сделано</span></div></div>`;

  const sel = S.msel, ts = dayTasks(sel);
  html += sec(`${rel(sel) ? rel(sel) + ', ' : ''}${dm(sel)}`, ts.length ? `${ts.filter(t => isDone(t, sel)).length}/${ts.length}` : '', `<button class="link" data-a="open-day" data-date="${sel}">Открыть день</button>`);
  html += `<div class="card" style="padding:4px 0">${ts.length ? ts.map(t => miniRow(t, sel)).join('') : '<div class="free" style="padding-top:12px">Ничего не запланировано</div>'}${quickRow(sel)}</div>`;

  if (total) {
    const p = Math.round(100 * done / total);
    html += sec('Итог месяца');
    html += `<div class="card summary">${ring(p)}<div class="txt"><b>${done} из ${total} задач</b><span>${p >= 80 ? 'Мощный месяц 💪' : 'Каждый день — шаг вперёд'}</span></div></div>`;
  }
  const goals = goalsFor('month', first);
  html += sec('Цели месяца', goals.length || '');
  html += `<div class="goal-chips">${goals.map(goalChip).join('')}<button class="gchip add" data-a="new-goal" data-h="month" data-start="${first}">＋ Цель на месяц</button></div>`;
  return html;
}

function viewGoals() {
  const all = [...S.goals.values()];
  const active = all.filter(g => g.status === 'active'), doneG = all.filter(g => g.status === 'done'), arch = all.filter(g => g.status === 'archived');
  const tab = S.goalsTab;
  let html = `<div class="top"><div class="grow"><div class="eyebrow">${active.length} в работе · ${doneG.length} ${plural(doneG.length, 'достигнута', 'достигнуты', 'достигнуто')}</div><div class="title">Цели</div></div>
    <button class="icon-btn" data-a="settings">${I.gear}</button></div>
    <div class="seg">${[['active', 'В работе'], ['done', 'Достигнуты'], ['archived', 'Архив']].map(([k, l]) => `<button class="${tab === k ? 'on' : ''}" data-a="goals-tab" data-t="${k}">${l}</button>`).join('')}</div>`;
  const list = tab === 'active' ? active : tab === 'done' ? doneG : arch;
  if (!list.length) {
    if (tab === 'active') {
      html += `<div class="card" style="margin-top:14px"><div class="empty"><div class="big">🎯</div><b>Поставь первую цель</b>
        <p>Цель — это то, к чему ты идёшь неделю, месяц или год.</p>
        <div class="tips"><div><span>🪜</span><span>Разбей на <b>шаги</b> — прогресс посчитается сам</span></div>
        <div><span>📝</span><span>Пиши <b>заметки</b>: мысли, идеи, что получилось</span></div>
        <div><span>☀️</span><span>Цели недели и месяца бот присылает по утрам</span></div></div>
        <button class="btn" data-a="new-goal" data-h="month">＋ Новая цель</button></div></div>`;
    } else {
      html += `<div class="card" style="margin-top:14px"><div class="empty"><div class="big">${tab === 'done' ? '🏆' : '🗄'}</div>
        <b>${tab === 'done' ? 'Здесь будут твои победы' : 'Архив пуст'}</b><p>${tab === 'done' ? 'Достигнутые цели остаются здесь навсегда.' : 'Сюда уходят цели, которые больше не актуальны.'}</p></div></div>`;
    }
    return html;
  }
  if (tab !== 'active') return html + `<div class="card" style="margin-top:14px">${list.map(goalRow).join('')}</div>`;
  const label = { week: 'Неделя', month: 'Месяц', year: 'Год', none: 'Без срока' };
  for (const hz of ['week', 'month', 'year', 'none']) {
    const part = list.filter(g => g.horizon === hz).sort((a, b) => (b.start || '').localeCompare(a.start || '') || a.pos - b.pos);
    if (!part.length) continue;
    html += sec(label[hz], part.length);
    html += `<div class="card">${part.map(goalRow).join('')}</div>`;
  }
  return html;
}

function viewGoal() {
  const g = S.goals.get(S.goalId);
  if (!g) { S.view = S.prev || 'goals'; return render(); }
  const p = pct(g), st = steps(g).sort((a, b) => a.done - b.done || (a.date || '9').localeCompare(b.date || '9') || a.pos - b.pos);
  const hb = habits(g), notes = notesOf(g), end = periodEnd(g);
  let period = HZ[g.horizon];
  if (g.horizon === 'week' && g.start) period = 'Неделя · ' + span(g.start);
  if (g.horizon === 'month' && g.start) period = MN[D.mon(g.start)] + ' ' + D.year(g.start);
  if (g.horizon === 'year' && g.start) period = 'Год ' + D.year(g.start);
  const status = g.status === 'done' ? '🏆 Достигнута' : g.status === 'archived' ? '🗄 В архиве' : end && end < S.today ? '⌛ Срок прошёл' : '';
  let html = `<div class="hero" style="--gc:${gc(g)}"><div class="row"><div class="he">${esc(g.emoji)}</div><div class="grow"></div>${ring(p, 84, 8)}</div>
    <div class="ht">${esc(g.title)}</div>
    <div class="hs"><span>${period}</span>${g.due ? `<span>🏁 ${dm(g.due)}</span>` : ''}${status ? `<span>${status}</span>` : ''}</div>
    ${g.why ? `<div class="why">${esc(g.why)}</div>` : ''}</div>`;
  if (!st.length && g.status === 'active') {
    html += `<div class="card slider-card" style="margin-top:12px"><div class="lbl"><span>Прогресс вручную</span><b id="pv">${g.progress || 0}%</b></div>
      <input type="range" min="0" max="100" step="5" value="${g.progress || 0}" data-progress="${g.id}" style="--gc:${gc(g)}">
      <div class="hint">Или добавь шаги ниже — тогда прогресс посчитается сам.</div></div>`;
  }
  html += sec('Шаги', st.length ? `${st.filter(t => t.done).length}/${st.length}` : '');
  html += `<div class="card">${st.map(t => taskRow(t, t.date, { noTime: true, showDate: true })).join('')}${quickRow('', g.id, st.length ? 'Ещё шаг…' : 'Первый шаг…')}</div>`;
  if (hb.length) {
    html += sec('Регулярно');
    html += `<div class="card">${hb.map(t => `<div class="task" data-a="edit-task" data-id="${t.id}" data-date="${S.today}"><div class="body"><div class="t">${esc(t.title)}</div>
      <div class="meta"><span>${I.repeat}${repeatText(t.repeat)}${t.time ? ' · ' + t.time : ''}</span></div></div></div>`).join('')}</div>`;
  }
  html += sec('Заметки', notes.length || '');
  html += `<div class="card composer"><textarea id="note-in" rows="1" placeholder="Мысль, идея, что получилось…" maxlength="10000"></textarea>
    <button class="send" data-a="note-send" disabled aria-label="Сохранить">${I.send}</button></div>`;
  if (notes.length) {
    html += `<div class="timeline" style="--gc:${gc(g)}">${notes.map(n => `<button class="card note" data-a="edit-note" data-id="${n.id}">
      <div class="nd">${noteDate(n.created)}${n.updated && n.updated !== n.created ? ' · изменено' : ''}</div><div class="nt">${linkify(n.text)}</div></button>`).join('')}</div>`;
  }
  html += `<div class="actions">`;
  if (g.status === 'active') {
    html += `<button class="btn good wide" data-a="goal-status" data-s="done">🏆 Цель достигнута</button>
      <div class="two"><button class="btn ghost" data-a="edit-goal">Изменить</button><button class="btn ghost" data-a="goal-status" data-s="archived">В архив</button></div>`;
  } else {
    html += `<button class="btn ghost wide" data-a="goal-status" data-s="active">↩️ Вернуть в работу</button>
      <div class="two"><button class="btn ghost" data-a="edit-goal">Изменить</button><button class="btn danger" data-a="goal-delete">Удалить</button></div>`;
  }
  return html + `</div>`;
}

/* ---------- отрисовка ---------- */
const TABS = [['day', 'День', I.day], ['week', 'Неделя', I.week], ['month', 'Месяц', I.month], ['goals', 'Цели', I.goals]];
function render() {
  const v = $('#view');
  const fn = { day: viewDay, week: viewWeek, month: viewMonth, goals: viewGoals, goal: viewGoal }[S.view];
  const key = S.view + (S.view === 'day' ? S.sel : S.view === 'week' ? S.week : S.view === 'month' ? S.month : S.view === 'goal' ? S.goalId : S.goalsTab);
  const focus = document.activeElement && document.activeElement.matches('[data-quick]') ? document.activeElement.dataset.date + '|' + document.activeElement.dataset.goal : null;
  const html = fn();
  if (html === undefined) return;
  v.innerHTML = html;
  if (key !== S.lastKey) { v.classList.remove('enter'); void v.offsetWidth; v.classList.add('enter'); }
  if (key.slice(0, 4) !== S.lastKey.slice(0, 4)) window.scrollTo(0, 0);
  S.lastKey = key;
  S.pop = null;
  const active = S.view === 'goal' ? 'goals' : S.view;
  $('#tabs').innerHTML = TABS.map(([k, l, ic]) => `<button class="tab${k === active ? ' on' : ''}" data-a="tab" data-v="${k}">${ic}<span>${l}</span></button>`).join('');
  $('#fab').classList.toggle('hide', S.view === 'goal');
  if (focus) { const el = $$('[data-quick]').find(e => e.dataset.date + '|' + e.dataset.goal === focus); if (el) el.focus(); }
  const ta = $('#note-in');
  if (ta) ta.addEventListener('input', () => { ta.style.height = 'auto'; ta.style.height = ta.scrollHeight + 'px'; $('[data-a="note-send"]').disabled = !ta.value.trim(); });
  backButton();
}

function go(view, extra = {}) {
  if (view === 'goal' && S.view !== 'goal') S.prev = S.view;
  Object.assign(S, { view }, extra);
  render();
  haptic('select');
}

/* ---------- Telegram: тема, кнопка «назад», вибро ---------- */
function haptic(kind) {
  const h = tg && tg.HapticFeedback;
  if (!h || !tg.isVersionAtLeast || !tg.isVersionAtLeast('6.1')) return;
  try {
    if (kind === 'select') h.selectionChanged();
    else if (kind === 'success' || kind === 'error' || kind === 'warning') h.notificationOccurred(kind);
    else h.impactOccurred(kind || 'light');
  } catch (e) { /* старый клиент */ }
}
function applyTheme() {
  const light = tg ? tg.colorScheme === 'light' : matchMedia('(prefers-color-scheme: light)').matches;
  document.body.classList.toggle('light', light);
  const bg = getComputedStyle(document.body).getPropertyValue('--bg').trim();
  if (tg && tg.isVersionAtLeast && tg.isVersionAtLeast('6.1')) {
    try { tg.setHeaderColor(bg); tg.setBackgroundColor(bg); } catch (e) { /* */ }
    try { if (tg.isVersionAtLeast('7.10')) tg.setBottomBarColor(bg); } catch (e) { /* */ }
  }
}
function backButton() {
  if (!tg || !tg.BackButton || !tg.isVersionAtLeast || !tg.isVersionAtLeast('6.1')) return;
  if (sheetOpen || S.view === 'goal') tg.BackButton.show(); else tg.BackButton.hide();
}
function back() {
  if (sheetOpen) return closeSheet();
  if (S.view === 'goal') go(S.prev || 'goals');
}
function confirmPopup(message, buttons) {
  return new Promise(res => {
    if (tg && tg.showPopup && tg.isVersionAtLeast && tg.isVersionAtLeast('6.2')) {
      tg.showPopup({ message, buttons }, id => res(id || null));
    } else {
      res(window.confirm(message) ? buttons.find(b => b.type !== 'cancel').id : null);
    }
  });
}
const store = {
  get: k => { try { return localStorage.getItem('ritm.' + k); } catch (e) { return null; } },
  set: (k, v) => { try { localStorage.setItem('ritm.' + k, v); } catch (e) { /* */ } },
};

/* ---------- тост и конфетти ---------- */
let toastTimer;
function toast(text, action, fn) {
  const t = $('#toast');
  t.innerHTML = `<span>${esc(text)}</span>${action ? `<button>${esc(action)}</button>` : ''}`;
  t.classList.toggle('has-btn', !!action);
  if (action) t.querySelector('button').onclick = () => { t.classList.remove('show'); fn(); };
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), action ? 4500 : 2200);
}
function confetti() {
  const c = $('#confetti'), x = c.getContext('2d'), dpr = devicePixelRatio || 1;
  c.width = innerWidth * dpr; c.height = innerHeight * dpr; x.scale(dpr, dpr);
  const cols = Object.values(COLORS);
  const ps = Array.from({ length: 140 }, () => ({
    x: innerWidth / 2 + (Math.random() - .5) * 80, y: innerHeight * .45, vx: (Math.random() - .5) * 13, vy: -Math.random() * 15 - 5,
    r: Math.random() * 6 + 4, c: cols[Math.random() * cols.length | 0], a: Math.random() * 6, va: (Math.random() - .5) * .4,
  }));
  const t0 = performance.now();
  (function frame(t) {
    const k = (t - t0) / 1600;
    x.clearRect(0, 0, innerWidth, innerHeight);
    for (const p of ps) {
      p.vy += .45; p.vx *= .99; p.x += p.vx; p.y += p.vy; p.a += p.va;
      x.save(); x.globalAlpha = Math.max(0, 1 - k); x.translate(p.x, p.y); x.rotate(p.a); x.fillStyle = p.c;
      x.fillRect(-p.r / 2, -p.r / 4, p.r, p.r / 2); x.restore();
    }
    if (k < 1) requestAnimationFrame(frame); else x.clearRect(0, 0, innerWidth, innerHeight);
  })(t0);
}

/* ---------- действия с данными ---------- */
async function toggleTask(id, day) {
  const t = S.tasks.get(id);
  if (!t) return;
  if (t.repeat && !day) day = S.today;
  const was = isDone(t, day);
  setDone(t, day, !was);
  S.pop = !was ? id + '|' + day : null;
  haptic(was ? 'light' : 'medium');
  const list = day ? dayTasks(day) : [];
  const allDone = !was && day && list.length > 1 && list.every(x => isDone(x, day));
  render();
  if (allDone) { setTimeout(confetti, 150); haptic('success'); toast(day === S.today ? 'День закрыт! 🎉' : 'Всё сделано 🎉'); }
  try {
    const r = await api('task/toggle', { id, date: day, done: !was });
    if (r.task) S.tasks.set(r.task.id, r.task);
    mergeGoals(r.goals);
  } catch (e) {
    setDone(t, day, was); render(); toast(e.message);
  }
}

async function saveTask(data) {
  const r = await api('task', data);
  S.tasks.set(r.task.id, r.task);
  mergeGoals(r.goals);
  return r.task;
}

async function quickAdd(input) {
  let s = input.value.trim();
  if (!s) return;
  let time = null, important = false;
  if (s.includes('!')) { important = true; s = s.replace(/!/g, ' '); }
  s = s.replace(/(^|\s)(?:в\s+)?([01]?\d|2[0-3])[:.]([0-5]\d)(?=\s|$)/i, (m, a, h, mm) => { time = pad(h) + ':' + mm; return ' '; });
  s = s.replace(/\s+/g, ' ').trim();
  if (!s) return;
  const date = input.dataset.date || null, goal = input.dataset.goal ? +input.dataset.goal : null;
  input.value = '';
  try {
    await saveTask({ title: s[0].toUpperCase() + s.slice(1), date, time, goal, important, remind: time ? S.user.remind : -1 });
    haptic('light');
    render();
  } catch (e) { input.value = s; toast(e.message); }
}

async function deleteTask(t, day) {
  let only = null;
  if (t.repeat) {
    const id = await confirmPopup('Это повторяющаяся задача. Что удалить?', [
      { id: 'one', type: 'default', text: 'Только этот день' }, { id: 'all', type: 'destructive', text: 'Все повторы' }, { type: 'cancel' }]);
    if (!id) return false;
    only = id === 'one' ? day : null;
  }
  const r = await api('task/delete', { id: t.id, date: only });
  if (r.task) S.tasks.set(t.id, r.task); else S.tasks.delete(t.id);
  mergeGoals(r.goals);
  render();
  if (!t.repeat) toast('Задача удалена', 'Вернуть', async () => {
    try {
      const c = await saveTask({ title: t.title, note: t.note, date: t.date, time: t.time, goal: t.goal, important: t.important, remind: t.remind });
      if (t.done) { const x = await api('task/toggle', { id: c.id, done: true }); S.tasks.set(x.task.id, x.task); mergeGoals(x.goals); }
      render();
    } catch (e) { toast(e.message); }
  });
  return true;
}

async function saveGoal(data) {
  const r = await api('goal', data);
  S.goals.set(r.goal.id, r.goal);
  return r.goal;
}

/* ---------- шторки ---------- */
let sheetOpen = null;
function openSheet(html, mount) {
  closeSheet(true);
  const root = $('#sheet-root');
  root.innerHTML = `<div class="backdrop"></div><div class="sheet" role="dialog"><div class="grab"></div>${html}</div>`;
  const bd = $('.backdrop', root), sh = $('.sheet', root);
  bd.onclick = () => closeSheet();
  requestAnimationFrame(() => { bd.classList.add('show'); sh.classList.add('show'); });
  // свайп вниз по ручке закрывает
  let y0 = null;
  sh.addEventListener('touchstart', e => { y0 = sh.scrollTop <= 0 ? e.touches[0].clientY : null; }, { passive: true });
  sh.addEventListener('touchmove', e => { if (y0 != null) { const dy = e.touches[0].clientY - y0; if (dy > 0) sh.style.transform = `translateY(${dy}px)`; } }, { passive: true });
  sh.addEventListener('touchend', e => { if (y0 == null) return; const dy = e.changedTouches[0].clientY - y0; sh.style.transform = ''; if (dy > 110) closeSheet(); y0 = null; });
  sheetOpen = sh;
  mount && mount(sh);
  // выбранный вариант в длинном ряду — сразу на виду
  $$('.chips.scroll', sh).forEach(row => { const on = $('.chip.on', row); if (on && on.offsetLeft + on.offsetWidth > row.clientWidth) row.scrollLeft = on.offsetLeft - 18; });
  backButton();
  return sh;
}
function closeSheet(instant) {
  const root = $('#sheet-root');
  if (!sheetOpen) return;
  sheetOpen = null;
  if (document.activeElement) document.activeElement.blur();
  if (instant) { root.innerHTML = ''; return; }
  $('.backdrop', root).classList.remove('show');
  $('.sheet', root).classList.remove('show');
  setTimeout(() => { if (!sheetOpen) root.innerHTML = ''; }, 350);
  backButton();
}
function chips(name, items, cur, cls = '') {
  return `<div class="chips ${cls}" data-group="${name}">${items.map(([v, l]) => `<button class="chip${String(v) === String(cur) ? ' on' : ''}" data-v="${v}">${l}</button>`).join('')}</div>`;
}
function bindChips(sh, name, fn) {
  const g = $(`[data-group="${name}"]`, sh);
  if (!g) return;
  g.addEventListener('click', e => {
    const b = e.target.closest('.chip');
    if (!b || b.dataset.v === undefined || b.querySelector('input')) return;
    $$('.chip', g).forEach(c => c.classList.toggle('on', c === b));
    haptic('select');
    fn(b.dataset.v, b);
  });
}

function taskSheet(t, day, defaults = {}) {
  const isNew = !t;
  const f = t ? { ...t } : { title: '', date: day === undefined ? S.today : day, time: null, repeat: '', goal: null, remind: -1, important: 0, note: '', ...defaults };
  if (isNew && f.time) f.remind = S.user.remind;
  const tomorrow = D.add(S.today, 1);
  const goals = [...S.goals.values()].filter(g => g.status === 'active' || g.id === f.goal);
  const dateLabel = d => !d ? 'Дата' : d === S.today || d === tomorrow ? 'Дата' : `${rel(d) || dms(d)}`;
  const dateKey = d => !d ? 'none' : d === S.today ? 'today' : d === tomorrow ? 'tomorrow' : 'pick';
  const repKey = r => !r ? '' : r === '1234567' ? 'daily' : r === '12345' ? 'weekdays' : 'custom';
  const html = `<h2><span class="grow">${isNew ? 'Новая задача' : 'Задача'}</span></h2>
    <textarea class="big-input" id="f-title" rows="1" placeholder="Что сделать?" maxlength="300">${esc(f.title)}</textarea>
    <div class="row-lbl" id="date-lbl">${f.repeat ? 'Начиная с' : 'Когда'}</div>
    <div class="chips scroll" data-group="date">
      <button class="chip" data-v="today">Сегодня</button><button class="chip" data-v="tomorrow">Завтра</button>
      <label class="chip" data-v="pick">${I.cal}<span id="date-txt">${dateLabel(f.date)}</span><input type="date" id="f-date" value="${f.date || ''}"></label>
      <button class="chip" data-v="none">Без даты</button></div>
    <div class="row-lbl">Время</div>
    <div class="chips scroll" data-group="time">
      <button class="chip" data-v="">Весь день</button>
      <label class="chip" data-v="pick">${I.clock}<span id="time-txt">${f.time && !['09:00', '16:00', '18:00', '20:00'].includes(f.time) ? f.time : 'Выбрать'}</span><input type="time" id="f-time" value="${f.time || ''}"></label>
      ${['09:00', '16:00', '18:00', '20:00'].map(x => `<button class="chip" data-v="${x}">${x}</button>`).join('')}</div>
    <div id="remind-box" class="${f.time ? '' : 'hidden'}"><div class="row-lbl">${I.bell.replace('<svg', '<svg style="width:14px;height:14px"')} Напомнить</div>
      ${chips('remind', [[-1, 'Нет'], [0, 'В это время'], [10, 'За 10 мин'], [15, 'За 15 мин'], [30, 'За 30 мин'], [60, 'За час']], f.remind, 'scroll')}</div>
    <div class="row-lbl">Повтор</div>
    ${chips('repeat', [['', 'Нет'], ['daily', 'Каждый день'], ['weekdays', 'По будням'], ['custom', 'Дни недели']], repKey(f.repeat), 'scroll')}
    <div id="wd-box" class="${repKey(f.repeat) === 'custom' ? '' : 'hidden'}" style="margin-top:10px"><div class="chips" data-wd>${WD.map((w, i) => `<button class="chip wd${(f.repeat || '').includes(String(i + 1)) ? ' on' : ''}" data-d="${i + 1}">${w}</button>`).join('')}</div></div>
    ${goals.length ? `<div class="row-lbl">Цель</div>${chips('goal', [['', 'Без цели'], ...goals.map(g => [g.id, `${esc(g.emoji)} ${esc(g.title.length > 22 ? g.title.slice(0, 21) + '…' : g.title)}`])], f.goal || '', 'scroll')}` : ''}
    <div class="switch-row" data-a2="imp"><span style="font-size:20px">❗</span><div class="grow"><b>Важное</b><small>Будет выше в списке и с красной отметкой</small></div><span class="switch${f.important ? ' on' : ''}" id="f-imp"></span></div>
    <div class="row-lbl">Заметка</div>
    <textarea class="field" id="f-note" placeholder="Детали, ссылки, мысли…" maxlength="5000">${esc(f.note || '')}</textarea>
    <div class="actions"><button class="btn wide" id="f-save">${isNew ? 'Добавить' : 'Сохранить'}</button>
      ${isNew ? '' : `<div class="two">${t.repeat ? '<span></span>' : `<button class="btn ghost" id="f-move">${t.date === S.today ? 'На завтра' : 'На сегодня'}</button>`}<button class="btn danger" id="f-del">Удалить</button></div>`}</div>`;
  openSheet(html, sh => {
    const title = $('#f-title', sh);
    const grow = () => { title.style.height = 'auto'; title.style.height = title.scrollHeight + 'px'; };
    title.addEventListener('input', grow); grow();
    if (isNew) setTimeout(() => title.focus(), 380);
    const markDate = () => {
      $$('[data-group="date"] .chip', sh).forEach(c => c.classList.toggle('on', c.dataset.v === dateKey(f.date)));
      $('#date-txt', sh).textContent = dateLabel(f.date);
    };
    const markTime = () => {
      const preset = ['09:00', '16:00', '18:00', '20:00'];
      $$('[data-group="time"] .chip', sh).forEach(c => c.classList.toggle('on', c.dataset.v === 'pick' ? !!f.time && !preset.includes(f.time) : c.dataset.v === (f.time || '')));
      $('#time-txt', sh).textContent = f.time && !preset.includes(f.time) ? f.time : 'Выбрать';
      $('#remind-box', sh).classList.toggle('hidden', !f.time);
    };
    markDate(); markTime();
    $('[data-group="date"]', sh).addEventListener('click', e => {
      const b = e.target.closest('.chip'); if (!b || b.dataset.v === 'pick') return;
      f.date = { today: S.today, tomorrow, none: null }[b.dataset.v];
      if (!f.date && f.repeat) f.date = S.today;
      haptic('select'); markDate();
    });
    $('#f-date', sh).addEventListener('change', e => { f.date = e.target.value || f.date; markDate(); });
    $('[data-group="time"]', sh).addEventListener('click', e => {
      const b = e.target.closest('.chip'); if (!b || b.dataset.v === 'pick') return;
      const had = !!f.time;
      f.time = b.dataset.v || null;
      if (f.time && !had && f.remind < 0) { f.remind = S.user.remind; $$('[data-group="remind"] .chip', sh).forEach(c => c.classList.toggle('on', +c.dataset.v === f.remind)); }
      haptic('select'); markTime();
    });
    $('#f-time', sh).addEventListener('change', e => {
      const had = !!f.time; f.time = e.target.value || null;
      if (f.time && !had && f.remind < 0) { f.remind = S.user.remind; $$('[data-group="remind"] .chip', sh).forEach(c => c.classList.toggle('on', +c.dataset.v === f.remind)); }
      markTime();
    });
    bindChips(sh, 'remind', v => { f.remind = +v; });
    bindChips(sh, 'repeat', v => {
      f.repeat = { '': '', daily: '1234567', weekdays: '12345', custom: f.repeat && repKey(f.repeat) === 'custom' ? f.repeat : String(D.wd(f.date || S.today) + 1) }[v];
      $('#wd-box', sh).classList.toggle('hidden', v !== 'custom');
      $$('[data-wd] .chip', sh).forEach(c => c.classList.toggle('on', f.repeat.includes(c.dataset.d)));
      $('#date-lbl', sh).textContent = f.repeat ? 'Начиная с' : 'Когда';
      if (f.repeat && !f.date) { f.date = S.today; markDate(); }
    });
    $('[data-wd]', sh).addEventListener('click', e => {
      const b = e.target.closest('.chip'); if (!b) return;
      b.classList.toggle('on'); haptic('select');
      f.repeat = $$('[data-wd] .chip.on', sh).map(c => c.dataset.d).join('');
    });
    bindChips(sh, 'goal', v => { f.goal = v ? +v : null; });
    $('[data-a2="imp"]', sh).addEventListener('click', () => { f.important = f.important ? 0 : 1; $('#f-imp', sh).classList.toggle('on', !!f.important); haptic('light'); });
    $('#f-save', sh).addEventListener('click', async e => {
      f.title = title.value.trim(); f.note = $('#f-note', sh).value;
      if (!f.title) { title.focus(); haptic('error'); return; }
      e.target.disabled = true;
      try {
        await saveTask({ id: t && t.id, title: f.title, date: f.date, time: f.time, repeat: f.repeat || '', goal: f.goal, remind: f.time ? f.remind : -1, important: f.important, note: f.note });
        haptic('success'); closeSheet(); render();
        if (isNew) toast('Добавлено ✓');
      } catch (err) { e.target.disabled = false; toast(err.message); }
    });
    const mv = $('#f-move', sh);
    if (mv) mv.addEventListener('click', async () => {
      try { await saveTask({ id: t.id, date: t.date === S.today ? tomorrow : S.today }); closeSheet(); render(); toast(t.date === S.today ? 'Перенесено на завтра' : 'Перенесено на сегодня'); } catch (err) { toast(err.message); }
    });
    const del = $('#f-del', sh);
    if (del) del.addEventListener('click', async () => {
      try { if (await deleteTask(t, day)) closeSheet(); } catch (err) { toast(err.message); }
    });
  });
}

function periodLabel(hz, start) {
  if (hz === 'week') { const cur = D.monday(S.today); const n = D.diff(start, cur) / 7; return span(start) + (n === 0 ? ' · эта' : n === 1 ? ' · следующая' : ''); }
  if (hz === 'month') return MN[D.mon(start)] + ' ' + D.year(start) + (start === D.first(S.today) ? ' · этот' : '');
  if (hz === 'year') return D.year(start) + ' год';
  return '';
}
function goalSheet(g, defaults = {}) {
  const isNew = !g;
  const f = g ? { ...g } : { title: '', emoji: '🎯', color: 'violet', horizon: 'month', start: null, due: null, why: '', ...defaults };
  if (!f.start && f.horizon !== 'none') f.start = periodStart(f.horizon, S.today);
  const html = `<h2><span class="grow">${isNew ? 'Новая цель' : 'Цель'}</span></h2>
    <div class="head-row"><button class="emoji-btn" id="g-emoji" style="--gc:${gc(f)}">${esc(f.emoji)}</button>
      <textarea class="big-input" id="g-title" rows="1" placeholder="Чего хочу достичь?" maxlength="200">${esc(f.title)}</textarea></div>
    <div class="emoji-grid hidden" id="g-emojis">${EMOJI.map(e => `<button class="${e === f.emoji ? 'on' : ''}" data-e="${e}">${e}</button>`).join('')}</div>
    <div class="row-lbl">Срок</div>
    <div class="seg" data-group="hz" style="margin:0">${Object.entries(HZ).map(([k, l]) => `<button class="${k === f.horizon ? 'on' : ''}" data-v="${k}">${l}</button>`).join('')}</div>
    <div class="period ${f.horizon === 'none' ? 'hidden' : ''}" id="g-period"><button data-n="-1">${I.left}</button><div class="pl" id="g-pl">${periodLabel(f.horizon, f.start)}</div><button data-n="1">${I.right}</button></div>
    <div class="row-lbl">Дедлайн</div>
    <div class="chips"><label class="chip${f.due ? ' on' : ''}" id="g-due-chip">🏁 <span id="g-due-txt">${f.due ? dm(f.due) : 'Выбрать дату'}</span><input type="date" id="g-due" value="${f.due || ''}"></label>
      <button class="chip ${f.due ? '' : 'hidden'}" id="g-due-x">${I.x.replace('<svg', '<svg style="width:14px;height:14px"')} Убрать</button></div>
    <div class="row-lbl">Цвет</div>
    <div class="swatches">${Object.entries(COLORS).map(([k, c]) => `<button class="sw${k === f.color ? ' on' : ''}" style="--gc:${c}" data-c="${k}"></button>`).join('')}</div>
    <div class="row-lbl">Зачем мне это</div>
    <textarea class="field" id="g-why" placeholder="Что изменится, когда достигну? Это помогает не бросить." maxlength="2000">${esc(f.why || '')}</textarea>
    <div class="hint">Шаги и заметки добавляются на странице цели.</div>
    <div class="actions"><button class="btn wide" id="g-save">${isNew ? 'Поставить цель' : 'Сохранить'}</button></div>`;
  openSheet(html, sh => {
    const title = $('#g-title', sh);
    const grow = () => { title.style.height = 'auto'; title.style.height = title.scrollHeight + 'px'; };
    title.addEventListener('input', grow); grow();
    if (isNew) setTimeout(() => title.focus(), 380);
    $('#g-emoji', sh).addEventListener('click', () => { $('#g-emojis', sh).classList.toggle('hidden'); haptic('light'); });
    $('#g-emojis', sh).addEventListener('click', e => {
      const b = e.target.closest('[data-e]'); if (!b) return;
      f.emoji = b.dataset.e; $('#g-emoji', sh).textContent = f.emoji;
      $$('#g-emojis button', sh).forEach(x => x.classList.toggle('on', x === b));
      $('#g-emojis', sh).classList.add('hidden'); haptic('select');
    });
    $('[data-group="hz"]', sh).addEventListener('click', e => {
      const b = e.target.closest('button'); if (!b) return;
      $$('[data-group="hz"] button', sh).forEach(x => x.classList.toggle('on', x === b));
      f.horizon = b.dataset.v;
      f.start = periodStart(f.horizon, defaults.start && defaults.horizon === f.horizon ? defaults.start : S.today);
      $('#g-period', sh).classList.toggle('hidden', f.horizon === 'none');
      $('#g-pl', sh).textContent = periodLabel(f.horizon, f.start);
      haptic('select');
    });
    $('#g-period', sh).addEventListener('click', e => {
      const b = e.target.closest('button'); if (!b) return;
      const n = +b.dataset.n;
      f.start = f.horizon === 'week' ? D.add(f.start, 7 * n) : f.horizon === 'month' ? D.addMonths(f.start, n) : (D.year(f.start) + n) + '-01-01';
      $('#g-pl', sh).textContent = periodLabel(f.horizon, f.start);
      haptic('select');
    });
    const markDue = () => {
      $('#g-due-txt', sh).textContent = f.due ? dm(f.due) : 'Выбрать дату';
      $('#g-due-chip', sh).classList.toggle('on', !!f.due);
      $('#g-due-x', sh).classList.toggle('hidden', !f.due);
    };
    $('#g-due', sh).addEventListener('change', e => { f.due = e.target.value || null; markDue(); });
    $('#g-due-x', sh).addEventListener('click', () => { f.due = null; $('#g-due', sh).value = ''; markDue(); });
    $('.swatches', sh).addEventListener('click', e => {
      const b = e.target.closest('.sw'); if (!b) return;
      f.color = b.dataset.c;
      $$('.sw', sh).forEach(x => x.classList.toggle('on', x === b));
      $('#g-emoji', sh).style.setProperty('--gc', COLORS[f.color]);
      haptic('select');
    });
    $('#g-save', sh).addEventListener('click', async e => {
      f.title = title.value.trim(); f.why = $('#g-why', sh).value;
      if (!f.title) { title.focus(); haptic('error'); return; }
      e.target.disabled = true;
      try {
        const saved = await saveGoal({ id: g && g.id, title: f.title, emoji: f.emoji, color: f.color, horizon: f.horizon, start: f.horizon === 'none' ? null : f.start, due: f.due, why: f.why });
        haptic('success'); closeSheet();
        if (isNew) go('goal', { goalId: saved.id }); else render();
      } catch (err) { e.target.disabled = false; toast(err.message); }
    });
  });
}

function noteSheet(n) {
  openSheet(`<h2><span class="grow">Заметка</span></h2><div class="hint" style="margin:-8px 0 12px">${noteDate(n.created)}</div>
    <textarea class="field" id="n-text" style="min-height:180px" maxlength="10000">${esc(n.text)}</textarea>
    <div class="actions"><button class="btn wide" id="n-save">Сохранить</button><button class="btn danger wide" id="n-del">Удалить заметку</button></div>`, sh => {
    $('#n-save', sh).addEventListener('click', async () => {
      const text = $('#n-text', sh).value.trim();
      if (!text) return;
      try { const r = await api('note', { id: n.id, text }); S.notes.set(r.note.id, r.note); closeSheet(); render(); haptic('success'); } catch (e) { toast(e.message); }
    });
    $('#n-del', sh).addEventListener('click', async () => {
      if (!await confirmPopup('Удалить заметку?', [{ id: 'y', type: 'destructive', text: 'Удалить' }, { type: 'cancel' }])) return;
      try {
        await api('note/delete', { id: n.id }); S.notes.delete(n.id); closeSheet(); render();
        toast('Заметка удалена', 'Вернуть', async () => { const r = await api('note', { goal: n.goal, text: n.text }); S.notes.set(r.note.id, r.note); render(); });
      } catch (e) { toast(e.message); }
    });
  });
}

const TZ = [['Europe/Kaliningrad', 'Калининград (UTC+2)'], ['Europe/Moscow', 'Москва (UTC+3)'], ['Europe/Minsk', 'Минск (UTC+3)'], ['Europe/Samara', 'Самара (UTC+4)'],
  ['Asia/Yekaterinburg', 'Екатеринбург (UTC+5)'], ['Asia/Omsk', 'Омск (UTC+6)'], ['Asia/Novosibirsk', 'Новосибирск (UTC+7)'], ['Asia/Krasnoyarsk', 'Красноярск (UTC+7)'],
  ['Asia/Irkutsk', 'Иркутск (UTC+8)'], ['Asia/Yakutsk', 'Якутск (UTC+9)'], ['Asia/Vladivostok', 'Владивосток (UTC+10)'], ['Asia/Magadan', 'Магадан (UTC+11)'],
  ['Asia/Kamchatka', 'Камчатка (UTC+12)'], ['Europe/Kyiv', 'Киев'], ['Asia/Almaty', 'Алматы'], ['Asia/Tashkent', 'Ташкент'], ['Asia/Tbilisi', 'Тбилиси'],
  ['Asia/Yerevan', 'Ереван'], ['Asia/Dubai', 'Дубай'], ['Europe/Istanbul', 'Стамбул'], ['Europe/Berlin', 'Берлин'], ['Europe/London', 'Лондон']];
function settingsSheet() {
  const u = S.user;
  const f = { morning: u.morning, evening: u.evening, remind: u.remind, tz: u.tz };
  let tzs = TZ.slice();
  try { const dev = Intl.DateTimeFormat().resolvedOptions().timeZone; if (dev && !tzs.some(t => t[0] === dev)) tzs.push([dev, dev + ' (это устройство)']); } catch (e) { /* */ }
  if (!tzs.some(t => t[0] === f.tz)) tzs.push([f.tz, f.tz]);
  const row = (key, icon, title, sub) => `<div class="switch-row"><span style="font-size:22px">${icon}</span><div class="grow"><b>${title}</b><small>${sub}</small>
    <div class="${f[key] ? '' : 'hidden'}" id="s-${key}-box" style="margin-top:8px"><input type="time" class="time-input" id="s-${key}-t" value="${f[key] || (key === 'morning' ? '07:30' : '21:30')}"></div></div>
    <span class="switch${f[key] ? ' on' : ''}" id="s-${key}"></span></div>`;
  openSheet(`<h2><span class="grow">Настройки</span></h2>
    ${row('morning', '☀️', 'Утренний план', 'Задачи и цели на день')}
    ${row('evening', '🌙', 'Итоги дня', 'Что сделано и перенос на завтра')}
    <div class="row-lbl">Напоминание для новых задач со временем</div>
    ${chips('remind', [[-1, 'Нет'], [0, 'В это время'], [10, 'За 10 мин'], [15, 'За 15 мин'], [30, 'За 30 мин'], [60, 'За час']], f.remind, 'scroll')}
    <div class="row-lbl">Часовой пояс</div>
    <select class="field" id="s-tz">${tzs.map(([v, l]) => `<option value="${v}"${v === f.tz ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select>
    <div class="row-lbl">Подсказка</div>
    <div class="card" style="padding:14px;font-size:14px;line-height:1.5;color:var(--text-2)">Задачи можно добавлять сообщением боту:<br>
      <i>«завтра в 18:00 тренировка»</i>, <i>«по пн и ср английский в 19:00»</i>, <i>«12.10 контрольная!»</i>.<br>Команда /today — план с галочками прямо в чате.</div>
    <div class="actions"><button class="btn wide" id="s-save">Сохранить</button></div>`, sh => {
    for (const k of ['morning', 'evening']) {
      $(`#s-${k}`, sh).addEventListener('click', e => {
        const on = !e.target.classList.contains('on');
        e.target.classList.toggle('on', on);
        $(`#s-${k}-box`, sh).classList.toggle('hidden', !on);
        haptic('light');
      });
    }
    bindChips(sh, 'remind', v => { f.remind = +v; });
    $('#s-save', sh).addEventListener('click', async () => {
      const body = { remind: f.remind, tz: $('#s-tz', sh).value };
      for (const k of ['morning', 'evening']) body[k] = $(`#s-${k}`, sh).classList.contains('on') ? $(`#s-${k}-t`, sh).value : '';
      try { const r = await api('settings', body); S.user = r.user; closeSheet(); toast('Сохранено ✓'); haptic('success'); } catch (e) { toast(e.message); }
    });
  });
}

/* ---------- клики ---------- */
const A = {
  tab: el => {
    const v = el.dataset.v;
    if (v === 'day' && S.view !== 'day') S.sel = S.sel || S.today;
    go(v);
  },
  'pick-day': el => { S.sel = el.dataset.date; render(); haptic('select'); },
  'open-day': el => go('day', { sel: el.dataset.date }),
  'cal-day': el => {
    const d = el.dataset.date;
    if (d === S.msel) return go('day', { sel: d });
    S.msel = d; if (d.slice(0, 7) !== S.month.slice(0, 7)) S.month = D.first(d);
    render(); haptic('select');
  },
  'go-today': () => {
    if (S.view === 'day') S.sel = S.today;
    if (S.view === 'week') S.week = D.monday(S.today);
    if (S.view === 'month') { S.month = D.first(S.today); S.msel = S.today; }
    render(); haptic('select');
  },
  shift: el => shift(+el.dataset.n),
  toggle: (el, e) => { e.stopPropagation(); toggleTask(+el.dataset.id, el.dataset.date || null); },
  'edit-task': el => { const t = S.tasks.get(+el.dataset.id); if (t) taskSheet(t, el.dataset.date || null); },
  'new-task': el => taskSheet(null, el.dataset.date),
  'to-today': async (el, e) => {
    e.stopPropagation();
    try { await saveTask({ id: +el.dataset.id, date: S.today }); haptic('light'); render(); } catch (err) { toast(err.message); }
  },
  'all-today': async () => {
    const late = overdue();
    try { for (const t of late) await saveTask({ id: t.id, date: S.today }); render(); toast(`Перенесено: ${late.length}`); } catch (err) { toast(err.message); }
  },
  'open-goal': el => go('goal', { goalId: +el.dataset.id }),
  'new-goal': el => goalSheet(null, { horizon: el.dataset.h || 'month', start: el.dataset.start || null }),
  'edit-goal': () => goalSheet(S.goals.get(S.goalId)),
  'goal-status': async el => {
    const s = el.dataset.s;
    try {
      await saveGoal({ id: S.goalId, status: s });
      render();
      if (s === 'done') { confetti(); haptic('success'); toast('Цель достигнута! Горжусь 🏆'); }
      else toast(s === 'archived' ? 'Цель в архиве' : 'Снова в работе');
    } catch (e) { toast(e.message); }
  },
  'goal-delete': async () => {
    const g = S.goals.get(S.goalId);
    if (!await confirmPopup(`Удалить цель «${g.title}» вместе с заметками? Шаги останутся задачами.`, [{ id: 'y', type: 'destructive', text: 'Удалить' }, { type: 'cancel' }])) return;
    try {
      await api('goal/delete', { id: g.id });
      S.goals.delete(g.id);
      for (const n of [...S.notes.values()]) if (n.goal === g.id) S.notes.delete(n.id);
      for (const t of S.tasks.values()) if (t.goal === g.id) t.goal = null;
      go('goals'); toast('Цель удалена');
    } catch (e) { toast(e.message); }
  },
  'goals-tab': el => { S.goalsTab = el.dataset.t; render(); haptic('select'); },
  'note-send': async el => {
    const ta = $('#note-in'), text = ta.value.trim();
    if (!text) return;
    el.disabled = true;
    try { const r = await api('note', { goal: S.goalId, text }); S.notes.set(r.note.id, r.note); render(); haptic('success'); }
    catch (e) { el.disabled = false; toast(e.message); }
  },
  'edit-note': (el, e) => { if (e.target.closest('a')) return; const n = S.notes.get(+el.dataset.id); if (n) noteSheet(n); },
  settings: () => settingsSheet(),
  'welcome-close': () => { store.set('welcomed', '1'); render(); },
  fab: () => {
    store.set('welcomed', '1');
    if (S.view === 'goals') return goalSheet(null, {});
    const day = S.view === 'day' ? S.sel : S.view === 'month' ? S.msel : (S.today >= S.week && S.today <= D.add(S.week, 6) ? S.today : S.week);
    taskSheet(null, day);
  },
};
function shift(n) {
  if (S.view === 'week') S.week = D.add(S.week, 7 * n);
  else if (S.view === 'month') { S.month = D.addMonths(S.month, n); S.msel = S.month.slice(0, 7) === S.today.slice(0, 7) ? S.today : S.month; }
  else if (S.view === 'day') S.sel = D.add(S.sel, 7 * n);
  render(); haptic('select');
}

document.addEventListener('click', e => {
  const a = e.target.closest('a[data-link]');
  if (a) { e.preventDefault(); if (tg && tg.openLink) tg.openLink(a.href); else window.open(a.href, '_blank'); return; }
  const el = e.target.closest('[data-a]');
  if (!el || !A[el.dataset.a]) return;
  A[el.dataset.a](el, e);
});
document.addEventListener('keydown', e => {
  if (e.key === 'Enter' && e.target.matches('[data-quick]')) { e.preventDefault(); quickAdd(e.target); }
});
document.addEventListener('change', e => {
  if (e.target.matches('[data-progress]')) {
    const g = S.goals.get(+e.target.dataset.progress);
    saveGoal({ id: g.id, progress: +e.target.value }).then(() => { render(); haptic('light'); if (+e.target.value === 100) toast('100%! Отметь цель достигнутой 🏆'); }).catch(err => toast(err.message));
  }
});
document.addEventListener('input', e => {
  if (e.target.matches('[data-progress]')) { const pv = $('#pv'); if (pv) pv.textContent = e.target.value + '%'; }
});
// свайпы: полоса недели и календарь листаются пальцем
let sx = null, sy = null;
document.addEventListener('touchstart', e => { if (e.target.closest('[data-swipe]')) { sx = e.touches[0].clientX; sy = e.touches[0].clientY; } else sx = null; }, { passive: true });
document.addEventListener('touchend', e => {
  if (sx == null) return;
  const dx = e.changedTouches[0].clientX - sx, dy = e.changedTouches[0].clientY - sy;
  if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.5) shift(dx < 0 ? 1 : -1);
  sx = null;
}, { passive: true });
// при открытой клавиатуре прячем нижнее меню
document.addEventListener('focusin', e => { if (e.target.matches('input:not([type=range]):not([type=date]):not([type=time]), textarea')) document.body.classList.add('kb'); });
document.addEventListener('focusout', () => document.body.classList.remove('kb'));

/* ---------- запуск ---------- */
async function boot() {
  document.body.insertAdjacentHTML('afterbegin', `<svg width="0" height="0" style="position:absolute"><defs><linearGradient id="rg" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#9b7cff"/><stop offset="1" stop-color="#4fb2ff"/></linearGradient></defs></svg>`);
  const style = document.createElement('style');
  style.textContent = 'body.kb .tabs, body.kb .fab { transform: translateY(150%); opacity: 0; } .tabs { transition: transform .25s var(--ease), opacity .2s; }';
  document.head.appendChild(style);
  if (tg) {
    tg.ready(); tg.expand();
    try { if (tg.isVersionAtLeast('7.7')) tg.disableVerticalSwipes(); } catch (e) { /* */ }
    tg.onEvent('themeChanged', applyTheme);
    if (tg.BackButton) tg.BackButton.onClick(back);
  }
  applyTheme();
  const v = $('#view');
  if (!tg || !tg.initData) {
    v.innerHTML = `<div class="error-box"><div class="big">📱</div><h2>Открой через Telegram</h2><p>Планер работает внутри Telegram: кнопка «Планер» в чате с ботом.</p></div>`;
    $('#fab').classList.add('hide');
    return;
  }
  v.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
  try {
    load(await api('all'));
  } catch (e) {
    v.innerHTML = `<div class="error-box"><div class="big">😕</div><h2>Не загрузилось</h2><p>${esc(e.message)}</p><button class="btn" onclick="location.reload()">Обновить</button></div>`;
    return;
  }
  S.sel = S.today; S.week = D.monday(S.today); S.month = D.first(S.today); S.msel = S.today;
  const p = new URLSearchParams(location.search).get('p') || '';
  const m = p.match(/^\/(day|week|month|goals|goal)(?:\/([\w-]+))?/);
  if (m) {
    const [, view, arg] = m;
    if (view === 'day' && arg && /^\d{4}-\d\d-\d\d$/.test(arg)) S.sel = arg;
    if (view === 'goal' && arg && S.goals.has(+arg)) { S.goalId = +arg; S.prev = 'goals'; }
    S.view = view === 'goal' && !S.goalId ? 'goals' : view;
  }
  render();
  // в полночь (и когда приложение снова открыли) обновляем «сегодня»
  document.addEventListener('visibilitychange', async () => {
    if (document.hidden) return;
    try { const d = await api('all'); const was = S.today; load(d); if (S.sel === was) S.sel = S.today; if (!sheetOpen) render(); } catch (e) { /* */ }
  });
}
boot();
