'use strict';
/* HDP — приложение бота внутри Telegram. Без сборки; общий вид и помощники — kit.css / kit.js.
   Данные с сайта: GET /api/overview (бот держит их в кэше и обновляет сам).
   Что бот делает сейчас: /api/state раз в пару секунд — живая работа, результат, черновик учителю.
   Действия (решить, сдать, отправить черновик) — те же функции, что у кнопок в чате; результат приходит и туда. */

const { tg, esc, api, toast, haptic } = Kit;
const q = (s, r = document) => r.querySelector(s);
const plural = (n, a, b, c) => { n = Math.abs(n); const m = n % 10, h = n % 100; return m === 1 && h !== 11 ? a : m >= 2 && m <= 4 && (h < 12 || h > 14) ? b : c; };
const LINK = /https:\/\/horodigital\.ru\/student\/topic\/[0-9a-f-]{36}\/task\/[0-9a-f-]{36}\S*/;
const comma = x => String(x).replace('.', ',');
const lvlFmt = x => comma((+x).toFixed(1));

/* ---------- состояние ---------- */
const S = { tab: 'tasks', data: null, loading: false, error: '', state: null, me: null, seen: 0, sheet: null, link: '', polledAt: 0 };
try { S.seen = +localStorage.getItem('hdp.seen') || 0; S.tab = localStorage.getItem('hdp.tab') || 'tasks'; } catch (e) { /* без хранилища */ }
if (!['tasks', 'prep', 'grades', 'teachers'].includes(S.tab)) S.tab = 'tasks';
const store = (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* без хранилища */ } };

/* ---------- сеть ---------- */
async function loadOverview(fresh) {
  try {
    const r = await api('/api/overview', fresh ? { fresh: 1 } : undefined);
    const changed = r.data && (!S.data || r.data.at !== S.data.at);
    S.loading = r.loading; S.error = r.error || '';
    if (changed) { S.data = r.data; render(); } else if (!S.data) render(); else renderHead();
  } catch (e) { S.error = e.message; renderHead(); if (!S.data) render(); }
}

async function poll() {
  try {
    const st = await api('/api/state');
    const prev = S.state;
    S.state = st; S.polledAt = Date.now();
    S.loading = st.loading; S.error = st.error || '';
    if (S.data && st.at && st.at !== S.data.at && !st.loading) loadOverview();
    if (prev && prev.busy && !st.busy) finished(st);
    renderLive(); renderStatus(); renderHead();
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
const dayNum = s => { const [y, m, d] = s.split('-').map(Number); return Date.UTC(y, m - 1, d) / 864e5; };
const todayIso = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
const left = s => dayNum(s) - dayNum(todayIso());
function whenLabel(s) {
  const n = left(s);
  if (n < 0) return [`−${-n} ${plural(-n, 'день', 'дня', 'дней')}`, 'err'];
  if (n === 0) return ['сегодня', 'warn'];
  if (n === 1) return ['завтра', 'warn'];
  const [, m, d] = s.split('-').map(Number);
  return [`${d} ${MS[m - 1]}`, n <= 3 ? 'warn' : ''];
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
const subjIc = r => `<span class="ic subj" style="--h:${hue(r.subj)}">${esc(r.icon || '📘')}</span>`;
const STATUS = { reworking: 'Доработка', failed: 'Не зачтено', appointed: 'Новое' };
const empty = (e, title, text) => `<div class="card empty rise"><span class="e">${e}</span><b>${title}</b>${text}</div>`;
const skel = n => '<div class="sk big"></div>' + Array.from({ length: n }, () => '<div class="sk row-sk"></div>').join('');

/* ---------- отрисовка ---------- */
const TITLES = { tasks: 'Задания', prep: 'Подготовка', grades: 'Оценки', teachers: 'Учителя' };
let tabs;

function render() {
  const v = q('#view');
  v.innerHTML = S.tab === 'tasks' ? tasksView() : S.tab === 'prep' ? Prep.view() : S.tab === 'grades' ? gradesView() : teachersView();
  q('#title').textContent = TITLES[S.tab];
  renderHead(); renderLive(); renderStatus(); badges();
}

function renderHead() {
  const when = S.loading ? 'обновляю с сайта…' : S.data ? `обновлено ${ago(S.data.at)}` : S.error ? 'не загрузилось' : 'загружаю…';
  q('#sub').textContent = when;
  q('#refresh').classList.toggle('loading', !!S.loading);
  const html = S.error && !S.loading ? `<div class="note err rise" style="margin-bottom:12px">⚠️ <span>${esc(S.error)}</span></div>` : '';
  const box = q('#err');
  if (box.dataset.html !== html) { box.innerHTML = html; box.dataset.html = html; }
}

function badges() {
  const tasks = (S.data && S.data.tasks) || [];
  const n = { tasks: tasks.filter(t => left(t.deadline) < 0).length, teachers: ((S.data && S.data.comments) || []).length };
  document.querySelectorAll('#tabs button').forEach(b => {
    const old = q('.badge', b);
    if (old) old.remove();
    const k = n[b.dataset.tab];
    if (k) b.insertAdjacentHTML('beforeend', `<span class="badge">${k}</span>`);
  });
}

/* живая работа: полная карточка на «Заданиях», одна строка на остальных вкладках */
function renderLive() {
  const box = q('#live');
  const st = S.state || {};
  if (!st.busy) { if (box.innerHTML) { box.innerHTML = ''; box.dataset.key = ''; } return; }
  const l = st.live || { title: 'Работаю', sub: '', step: '', done: [], frac: null, secs: 0 };
  const pct = l.frac == null ? null : Math.round(l.frac * 100);
  const key = S.tab === 'tasks' ? 'full:' + l.title : 'mini';
  if (box.dataset.key !== key) {
    box.dataset.key = key;
    box.innerHTML = S.tab === 'tasks'
      ? `<div class="card live rise"><div class="live-h"><span class="spin"></span>
          <div class="main"><div class="title"></div><div class="hint clip sub"></div></div><span class="clock"></span></div>
          <ul class="steps"></ul><div class="bar"><i></i></div>
          <div class="live-f"><span class="pct"></span><span>результат придёт сюда и в чат</span></div></div>`
      : `<button class="card banner rise" data-a="tab" data-id="tasks"><span class="spin"></span>
          <span class="main"><div class="title"></div><div class="hint sub"></div></span><span class="chev"></span></button>`;
  }
  const set = (sel, text, html) => { const e = q(sel, box); if (e && e[html ? 'innerHTML' : 'textContent'] !== text) e[html ? 'innerHTML' : 'textContent'] = text; };
  set('.title', l.title);
  if (S.tab === 'tasks') {
    set('.sub', l.sub || 'работаю');
    set('.steps', (l.done || []).map(x => `<li><span>${esc(x)}</span></li>`).join('') + (l.step ? `<li class="now"><span>${esc(l.step)}</span></li>` : ''), true);
    const bar = q('.bar', box);
    bar.classList.toggle('indet', pct == null);
    q('i', bar).style.width = (pct || 0) + '%';
    set('.pct', pct == null ? 'идёт работа' : pct + '%');
    const c = q('.clock', box);
    c.dataset.secs = l.secs || 0;
    c.textContent = mmss(l.secs || 0);
  } else {
    set('.sub', (pct == null ? 'идёт работа' : pct + '%') + ' · подробнее на «Заданиях»');
  }
}

/* результат и черновик учителю — на «Заданиях» под ссылкой */
function renderStatus() {
  const box = q('#status');
  if (!box) return;
  const st = S.state || {};
  const l = st.last;
  const ds = st.drafts || (st.draft ? [st.draft] : []);
  const key = JSON.stringify([!st.busy && l && l.at > S.seen ? l.at : 0, ds.map(d => [d.task, d.title, d.files])]);
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  let out = '';
  if (!st.busy && l && l.at > S.seen) out += resultCard(l);
  for (const d of ds) {  // черновиков может быть несколько — у каждого своя карточка
    out += `<button class="card banner rise" data-a="draft" data-task="${esc(d.task || '')}"><span class="ic">📄</span>
      <span class="main"><div class="title">Черновик для учителя</div><div class="hint">${esc(d.title)}</div></span><span class="chev"></span></button>`;
  }
  box.innerHTML = out;
}

function resultCard(l) {
  const sc = l.score;
  const pct = sc ? sc[2] : null;
  const head = !l.ok ? 'Не получилось' : l.kind === 'sent' ? 'Отправлено учителю' : l.show ? 'Ответы' : 'Готово';
  const col = pct == null ? '' : pct >= 100 ? 'var(--g5)' : pct >= 70 ? 'var(--accent)' : 'var(--warn)';
  const icon = !l.ok ? '⚠️' : l.kind === 'sent' ? '📬' : l.show ? '👀' : '✅';
  const left = pct == null ? `<span class="res-ic">${icon}</span>`
    : `<div class="ring-wrap"><div class="ring" style="--p:${Math.min(pct, 100)};--size:60px;--w:6px;--c:${col}"></div><div class="ring-v">${pct}%</div></div>`;
  const main = sc ? `<div class="score">${sc[1] ? `${comma(sc[0])} <small>/ ${comma(sc[1])}</small>` : `${pct}%`}</div>` : `<div class="res-title">${head}</div>`;
  const long = l.text && (l.text.length > 240 || l.text.split('\n').length > 7);
  const text = l.text ? `<div class="res-text ${long ? '' : 'open'}">${esc(l.text)}</div>${long ? '<button class="btn ghost sm more" data-a="more">Показать всё</button>' : ''}` : '';
  const sub = [l.title, l.secs ? mmss(l.secs) : '', pct != null && pct >= 100 ? 'полный балл 🏆' : ''].filter(Boolean).join(' · ');
  return `<div class="card result rise"><div class="res-h">${left}
      <div class="main">${main}<div class="hint">${esc(sub)}</div></div>
      <button class="btn icon x" data-a="seen" aria-label="Скрыть">✕</button></div>${text}</div>`;
}

function solveCard() {
  const ok = LINK.test(S.link);
  return `<section class="card feature solve rise">
    <div class="t">Решить задание</div>
    <div class="hint">Вставь ссылку с horodigital.ru — решу сам</div>
    <div class="link-box ${ok ? 'ok' : ''}"><input id="link" type="url" inputmode="url" autocomplete="off" spellcheck="false"
        placeholder="https://horodigital.ru/student/…" value="${esc(S.link)}">
      <button class="btn icon" data-a="paste" aria-label="Вставить из буфера">📋</button></div>
    <button class="btn primary big" data-a="solve" data-show="0">📝 Решить и сдать</button>
    <button class="btn tonal" data-a="solve" data-show="1">👀 Только ответы</button>
  </section><div id="status"></div>`;
}

function tasksView() {
  if (!S.data) return solveCard() + (S.loading || !S.error ? skel(3) : '');
  const tasks = S.data.tasks.slice().sort((a, b) => a.deadline.localeCompare(b.deadline) || a.subj.localeCompare(b.subj) || a.order - b.order);
  const late = tasks.filter(t => left(t.deadline) < 0);
  const week = tasks.filter(t => left(t.deadline) >= 0 && left(t.deadline) <= 7);
  const later = tasks.filter(t => left(t.deadline) > 7);
  const auto = tasks.filter(t => t.auto && (t.rawStatus === 'appointed' || t.rawStatus === 'reworking'));  // новые и на доработке
  let out = solveCard();
  if (tasks.length) {
    out += `<div class="stats3 rise">
      <div class="stat"><div class="v">${tasks.length}</div><div class="k">${plural(tasks.length, 'задание', 'задания', 'заданий')}</div></div>
      <div class="stat ${late.length ? 'late' : 'good'}"><div class="v">${late.length}</div><div class="k">просрочено</div></div>
      <div class="stat ${week.length ? 'warn' : ''}"><div class="v">${week.length}</div><div class="k">на неделе</div></div></div>`;
  }
  if (auto.length) {
    out += `<div class="card auto-card rise"><span class="ic">⚡</span>
      <div class="main"><div class="title">${auto.length} ${plural(auto.length, 'автотест', 'автотеста', 'автотестов')}</div>
        <div class="hint">сдам сам, по очереди</div></div>
      <button class="btn primary sm" data-a="auto">Сдать все</button></div>`;
  }
  if (!tasks.length) return out + empty('🎉', 'Всё сделано', 'Новых заданий нет — можно отдыхать');
  const section = (title, list, cls = '') => list.length
    ? `<div class="label ${cls}"><span>${title}<span class="n">${list.length}</span></span></div><div class="list rise">${list.map(taskRow).join('')}</div>` : '';
  return out + section('Просрочено', late, 'late') + section('На этой неделе', week) + section('Позже', later);
}

function taskRow(t) {
  const [w, cls] = whenLabel(t.deadline);
  return `<button class="row" data-a="task" data-url="${esc(t.url)}">${subjIc(t)}
    <span class="main"><div class="title two">${esc(t.title)}</div>
      <div class="meta"><span class="tag ${t.rawStatus}">${STATUS[t.rawStatus] || esc(t.st)}</span>${t.auto ? '<span class="tag auto">⚡ сам</span>' : ''}<span class="s">${esc(t.subj)}</span></div></span>
    <span class="end"><span class="pill when ${cls}">${w}</span></span></button>`;
}

function gradesView() {
  if (!S.data) return S.loading || !S.error ? skel(4) : '';
  const g = S.data.grades;
  if (!g.length) return empty('📊', 'Оценок пока нет', 'Появятся, когда учителя проверят работы');
  const avg = g.reduce((s, r) => s + r.five, 0) / g.length;
  const cnt = f => g.filter(r => r.five === f).length;
  const gcls = Math.max(2, Math.min(5, Math.round(avg)));
  let out = `<section class="card feature gpa rise">
    <div class="main"><div class="big-num g${gcls}" style="color:var(--gc)">${comma(avg.toFixed(1))}</div>
      <div class="hint">средний балл · ${g.length} ${plural(g.length, 'предмет', 'предмета', 'предметов')}</div></div>
    <div class="dist">${[5, 4, 3, 2].map(f => `<div><span class="gb g${f}">${f}</span><b>${cnt(f)}</b></div>`).join('')}</div>
  </section>`;
  out += '<div class="label"><span>Предметы</span><span class="n" style="text-transform:none;letter-spacing:0">от слабых к сильным</span></div>';
  const tick = x => `<b style="left:${x / 4 * 100}%"></b>`;
  out += '<div class="list rise">' + g.map(r => `<div class="row grade-row g${r.five}">
    <span class="gb">${r.five}</span>
    <span class="main"><div class="title">${esc(r.name)}</div>
      <div class="bar lvl"><i style="width:${Math.min(100, r.lvl / 4 * 100)}%"></i>${tick(2)}${tick(2.5)}${tick(3.5)}</div>
      <div class="meta">${r.next ? `до «${r.next[1]}» ещё ${lvlFmt(Math.max(0.1, r.next[0] - r.lvl))}` : 'максимум 🏆'}</div></span>
    <span class="lvl-num">${lvlFmt(r.lvl)}</span>
  </div>`).join('') + '</div>';
  return out + '<p class="hint foot">Уровень → оценка: от 2,0 — «3», от 2,5 — «4», от 3,5 — «5»</p>';
}

function teachersView() {
  if (!S.data) return S.loading || !S.error ? skel(2) : '';
  const c = S.data.comments;
  if (!c.length) return empty('✅', 'Доработок нет', 'Учителя всё приняли');
  return `<div class="stats3 two rise">
      <div class="stat warn"><div class="v">${c.filter(r => !r.failed).length}</div><div class="k">на доработке</div></div>
      <div class="stat late"><div class="v">${c.filter(r => r.failed).length}</div><div class="k">не зачтено</div></div></div>`
    + c.map(r => `<button class="card comment rise" data-a="task" data-url="${esc(r.url)}">
      <div class="c-head">${subjIc(r)}<div class="main"><div class="hint clip">${esc(r.subj)}</div><div class="title">${esc(r.title)}</div></div>
        ${r.score != null ? `<span class="pill ${r.failed ? 'err' : 'warn'}">${r.score}%</span>` : ''}</div>
      <div class="quote ${r.comment ? '' : 'none'}">${esc(r.comment || (r.attempts ? `Тест: ${r.attempts}` : 'Комментария нет'))}</div>
    </button>`).join('');
}

/* ---------- листы снизу ---------- */
function openSheet(html, kind) {
  closeSheet();
  S.sheet = kind;
  const el = Kit.sheet(html, () => { if (S.sheetEl === el) { S.sheet = null; S.sheetEl = null; } });
  S.sheetEl = el;
  return el;
}
function closeSheet() { if (S.sheetEl) S.sheetEl.close(); }

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
  const pills = `<div class="pills">
    <span class="pill ${t.rawStatus === 'failed' ? 'err' : t.rawStatus === 'reworking' ? 'warn' : 'accent'}">${STATUS[t.rawStatus] || ''}</span>
    ${w ? `<span class="pill ${cls}">${cls === 'err' ? 'просрочено ' + w.replace('−', 'на ') : 'до ' + w}</span>` : ''}
    ${t.auto ? '<span class="pill ok">⚡ автотест</span>' : ''}</div>`;
  const comment = c && (c.comment || c.score != null)
    ? `<div class="label" style="margin-top:0">Учитель${c.score != null ? ` · ${c.score}%` : ''}</div><div class="quote ${c.comment ? '' : 'none'}" style="margin:0 0 16px">${esc(c.comment || 'Без комментария')}</div>` : '';
  const dis = busy ? 'disabled' : '';
  const actions = t.auto
    ? `<button class="btn primary big" data-a="solve-url" data-url="${esc(url)}" data-show="0" ${dis}>📝 Решить и сдать</button>
       <button class="btn tonal" data-a="solve-url" data-url="${esc(url)}" data-show="1" ${dis}>👀 Только ответы</button>`
    : `<button class="btn primary big" data-a="solve-url" data-url="${esc(url)}" data-show="0" ${dis}>✍️ Подготовить ответ</button>
       <button class="btn tonal" data-a="solve-url" data-url="${esc(url)}" data-show="1" ${dis}>👀 Только посмотреть</button>`;
  openSheet(`<div class="s-sub">${esc(t.icon || '📘')} ${esc(t.subj)}${t.topic ? ' · ' + esc(t.topic) : ''}</div>
    <h2>${esc(t.title)}</h2>${pills}${comment}${actions}
    <button class="btn ghost" data-a="open" data-url="${esc(url)}">Открыть на сайте ↗</button>
    <div class="hint c">${busy ? 'Сейчас занят другим заданием' : t.auto ? 'Результат покажу здесь и пришлю в чат' : 'Сначала покажу черновик — отправишь сам'}</div>`, 'task');
}

function draftSheet(task) {
  const st = S.state || {};
  const d = (st.drafts || []).find(x => x.task === task) || st.draft;
  if (!d) return;
  S.draftTask = d.task;
  openSheet(`<div class="s-sub">📄 Черновик для учителя</div><h2>${esc(d.title)}</h2>
    <textarea id="draft-text" rows="8">${esc(d.text)}</textarea>
    ${d.files ? `<div class="note" style="margin-bottom:12px">🖼 ${d.files} ${plural(d.files, 'картинка', 'картинки', 'картинок')} — в чате, уйдут вместе с текстом</div>` : ''}
    <button class="btn primary big" data-a="draft-send">✅ Отправить учителю</button>
    <button class="btn danger" data-a="draft-drop">Не отправлять</button>
    <div class="hint c">Поправь текст прямо здесь — запомню, как ты пишешь. Свои фото пришли в чат</div>`, 'draft');
}

function settingsSheet() {
  // данные /api/me приходят, когда лист уже открыт: обновляем его на месте, а не открываем заново —
  // иначе лист мигает, а тап по «Поддержке» сразу после этого Kit принимает за «призрачный» и глотает
  if (S.sheet === 'settings' && S.sheetEl && S.sheetEl.isConnected) {
    const x = S.sheetEl.querySelector('.sheet-x');
    S.sheetEl.innerHTML = settingsHtml();
    if (x) S.sheetEl.prepend(x);
    return;
  }
  openSheet(settingsHtml(), 'settings');
}

function settingsHtml() {
  const me = S.me || {};
  return `<h2>Настройки</h2>
    <div class="list">
      <button class="row" data-a="check"><span class="ic">🩺</span><span class="main"><div class="title">Проверка</div>
        <div class="meta">сайт, Claude, почерк — ответ в чат</div></span><span class="chev"></span></button>
      <button class="row" data-a="alphabet"><span class="ic">✍️</span><span class="main"><div class="title">Мой почерк</div>
        <div class="meta">${me.alphabet ? 'алфавит заполнен · прислать заново' : 'не заполнен — листы придут в чат'}</div></span><span class="chev"></span></button>
      <button class="row" data-a="human"><span class="ic">⏳</span><span class="main"><div class="title">Сдача как человек: ${me.human === false ? 'выкл' : 'вкл'}</div>
        <div class="meta">${me.human === false ? 'сдаю и отправляю сразу' : 'паузы в тестах, учителю — через 4–12 мин'} · нажми, чтобы переключить</div></span></button>
      <button class="row" data-support="/api/support"><span class="ic">🛠</span><span class="main"><div class="title">Поддержка и пожелания</div>
        <div class="meta">ошибка или идея — починю/добавлю</div></span><span class="chev"></span></button>
      <button class="row" data-a="chat"><span class="ic">💬</span><span class="main"><div class="title">Перейти в чат</div>
        <div class="meta">${me.mode === 'chat' ? 'режим чата уже включён' : 'бот перейдёт в режим чата: всё кнопками там'}</div></span><span class="chev"></span></button>
    </div>
    <div class="hint c">HDP · версия ${esc(me.version || '—')} · режим: ${me.mode === 'chat' ? '💬 чат' : me.mode === 'app' ? '📱 приложение' : '—'}</div>`;
}

/* ---------- действия ---------- */
async function solve(url, show, btn) {
  await Kit.busy(btn, async () => {
    try {
      await api('/api/solve', { url, show: !!show });
      closeSheet();
      S.link = '';
      if (S.tab !== 'tasks') { S.tab = 'tasks'; store('hdp.tab', S.tab); if (tabs) tabs.set('tasks'); }
      render();
      window.scrollTo({ top: 0, behavior: 'smooth' });
      toast(show ? 'Смотрю ответы — прогресс сверху' : 'Начал — прогресс сверху', 'ok');
      haptic('success');
      setTimeout(poll, 400);
    } catch (e) { toast(e.message, 'err'); haptic('error'); }
  });
}

async function clipboardLink() {
  try {
    const t = await navigator.clipboard.readText();
    const m = t && t.match(LINK);
    return m ? m[0] : '';
  } catch (e) { return ''; }
}

function setLink(v) {
  S.link = v;
  const i = q('#link');
  if (i && i.value !== v) i.value = v;
  const box = q('.link-box');
  if (box) box.classList.toggle('ok', LINK.test(v));
}

const ACT = {
  tab: el => switchTab(el.dataset.id),
  refresh: () => { S.loading = true; renderHead(); loadOverview(true); },
  settings: async () => { settingsSheet(); try { S.me = await api('/api/me'); if (S.sheet === 'settings') settingsSheet(); } catch (e) { /* покажем без данных */ } },
  task: el => taskSheet(el.dataset.url),
  open: el => { if (tg && tg.openLink) tg.openLink(el.dataset.url); else window.open(el.dataset.url); },
  solve: async el => {
    let m = S.link.match(LINK);
    if (!m) {
      const got = await clipboardLink();
      if (got) { setLink(got); m = [got]; }
    }
    if (!m) { const i = q('#link'); if (i) i.focus(); toast(S.link ? 'Это не ссылка на задание HDP' : 'Вставь ссылку на задание', 'err'); haptic('warning'); return; }
    solve(m[0], el.dataset.show === '1', el);
  },
  'solve-url': el => solve(el.dataset.url, el.dataset.show === '1', el),
  paste: async () => {
    const got = await clipboardLink();
    if (got) { setLink(got); haptic('success'); return; }
    const i = q('#link'); if (i) i.focus();
    toast('В буфере нет ссылки — вставь её в поле');
  },
  auto: el => {
    const n = ((S.data && S.data.tasks) || []).filter(t => t.auto && (t.rawStatus === 'appointed' || t.rawStatus === 'reworking')).length;  // как на карточке и в боте (AUTO_STATUSES)
    confirmDo(`Сдать ${n} ${plural(n, 'автотест', 'автотеста', 'автотестов')} по очереди? Это ${n * 2}–${n * 3} мин.`, () => Kit.busy(el, async () => {
      try { await api('/api/auto', {}); toast('Сдаю — прогресс сверху', 'ok'); haptic('success'); setTimeout(poll, 400); } catch (e) { toast(e.message, 'err'); haptic('error'); }
    }));
  },
  seen: () => { S.seen = (S.state && S.state.last && S.state.last.at) || Date.now() / 1000; store('hdp.seen', S.seen); renderStatus(); },
  more: el => { const t = el.previousElementSibling; t.classList.add('open'); el.remove(); },
  draft: el => draftSheet(el.dataset.task),
  human: async () => {
    try { const r = await api('/api/human', { on: (S.me || {}).human === false }); S.me = { ...(S.me || {}), human: r.human }; settingsSheet(); toast(r.human ? 'Сдаю как человек' : 'Сдаю сразу', 'ok'); } catch (e) { toast(e.message, 'err'); }
  },
  'draft-send': el => Kit.busy(el, async () => {
    try {
      await api('/api/draft/save', { task: S.draftTask, text: q('#draft-text').value });
      await api('/api/draft/send', { task: S.draftTask });
      closeSheet(); toast('Отправляю учителю', 'ok'); haptic('success'); setTimeout(poll, 400);
    } catch (e) { toast(e.message, 'err'); haptic('error'); }
  }),
  'draft-drop': () => confirmDo('Не отправлять этот черновик?', async () => {
    try { S.state = await api('/api/draft/drop', { task: S.draftTask }); closeSheet(); renderStatus(); toast('Не отправляю'); } catch (e) { toast(e.message, 'err'); }
  }),
  check: async () => { try { await api('/api/check', {}); closeSheet(); toast('Проверяю — ответ придёт в чат', 'ok'); } catch (e) { toast(e.message, 'err'); } },
  alphabet: async () => { try { await api('/api/alphabet', {}); closeSheet(); toast('Листы алфавита — в чате', 'ok'); } catch (e) { toast(e.message, 'err'); } },
  chat: async el => {  // режим «💬 чат»: бот запомнит и пришлёт главное меню в чат, приложение закрывается
    try { await Kit.busy(el, () => api('/api/mode', { mode: 'chat' })); } catch (e) { toast(e.message, 'err'); return; }
    if (tg) tg.close();
  },
};
Object.assign(ACT, Prep.act);  // 📖 подготовка — prep.js

function switchTab(id) {
  if (S.tab === id) { window.scrollTo({ top: 0, behavior: 'smooth' }); badges(); return; }
  S.tab = id; store('hdp.tab', id);
  if (tabs) tabs.set(id);
  window.scrollTo(0, 0);
  render();
}

function confirmDo(text, fn) {
  if (tg && tg.showConfirm && tg.isVersionAtLeast && tg.isVersionAtLeast('6.2')) tg.showConfirm(text, ok => ok && fn());
  else if (window.confirm(text)) fn();
}

document.addEventListener('click', e => {
  const el = e.target.closest('[data-a]');
  if (!el || el.disabled) return;
  const fn = ACT[el.dataset.a];
  if (!fn) return;
  haptic('tap');
  fn(el);
});
document.addEventListener('input', e => {
  if (e.target.id === 'link') setLink(e.target.value.trim());
});

/* ---------- конфетти за полный балл ---------- */
function confetti() {
  const c = q('#confetti'), x = c.getContext('2d');
  const W = c.width = innerWidth * devicePixelRatio, H = c.height = innerHeight * devicePixelRatio;
  const css = getComputedStyle(document.documentElement);
  const cols = ['--accent', '--g5', '--g3', '--g4'].map(v => css.getPropertyValue(v).trim() || '#6e7bff');
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

/* таймер живой карточки тикает сам между опросами */
setInterval(() => {
  const el = q('#live .clock');
  if (el) el.textContent = mmss(+el.dataset.secs + (Date.now() - S.polledAt) / 1000);
}, 1000);

function start() {
  if (!tg || !tg.initData) {
    document.body.innerHTML = '<div class="app"><div class="empty" style="margin-top:30vh"><span class="e">🎓</span><b>Открой через Telegram</b>Кнопка «HDP» слева от поля ввода в боте или /app</div></div>';
    return;
  }
  tabs = Kit.tabbar(q('#tabs'), [
    { id: 'tasks', icon: '📋', label: 'Задания' },
    { id: 'prep', icon: '📖', label: 'Подготовка' },
    { id: 'grades', icon: '📊', label: 'Оценки' },
    { id: 'teachers', icon: '💬', label: 'Учителя' },
  ], S.tab, switchTab);
  render();
  loadOverview();
  poll();
  setInterval(() => { if (document.visibilityState === 'visible') poll(); }, 2500);
  setInterval(() => { if (document.visibilityState === 'visible') renderHead(); }, 30000);
}

start();
