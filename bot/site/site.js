'use strict';
/* Сайт HDP в браузере: те же разделы, что на horodigital.ru (профиль, предметы, тема, статусы, успеваемость),
   и у каждого задания — «⚡ Сделать» / «👁 Ответы», у каждой темы — «📖 Подготовь меня».
   Данные и действия — API HDP-бота (bot/bot.py → ROUTES): то же, что кнопки в чате; результат приходит и сюда, и в чат.
   Вход — cookie по одноразовой ссылке из бота (/site); каждый запрос несёт заголовок X-Site. */
(() => {
  const BOT = 'HDP_DIGITALBOT';
  const HDP = 'https://horodigital.ru';
  const S = {
    authed: null, ov: null, ovLoading: false, ovError: '', st: null, running: new Set(), wasBusy: false,
    prepSubjects: null, prep: null, prepSel: null, prepSubj: '', prepMin: 5, prepTz: '', prepView: 'form', prepOpen: new Set(),
    theory: {}, theoryErr: {}, onlyReq: true, onlyLate: false, filt: {}, tab: {}, chooser: '', timer: 0,
  };
  const store = {
    get: (k, d) => { try { const v = localStorage.getItem('hdp.site.' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set: (k, v) => { try { localStorage.setItem('hdp.site.' + k, JSON.stringify(v)); } catch (e) { /* без хранилища */ } },
  };
  S.seenLast = store.get('seenLast', 0);
  S.prepMin = store.get('prepMin', 5);

  /* ---------- мелочи ---------- */
  const $ = s => document.querySelector(s);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = s => esc(s).replace(/\*\*([^*\n]+?)\*\*/g, '<b>$1</b>').replace(/\*/g, '').replace(/\n/g, '<br>');
  const MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
  const dm = s => { if (!s) return ''; const [, m, d] = s.split('-').map(Number); return `${String(d).padStart(2, '0')} ${MONTHS[m - 1]}`; };
  const today = () => new Date().toISOString().slice(0, 10);
  const word = (n, a, b, c) => (n % 10 === 1 && n % 100 !== 11 ? a : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? b : c);
  const tasksWord = n => `${n} ${word(n, 'задание', 'задания', 'заданий')}`;
  const lv = x => (x == null ? '—' : Number.isInteger(x * 10) ? Number(x).toFixed(1) : Number(x).toFixed(2));
  const taskUrl = (topic, task) => `${HDP}/student/topic/${topic}/task/${task}`;
  const idOf = url => ((String(url || '').match(/task\/([0-9a-f-]{36})/) || [])[1]);
  const enc = encodeURIComponent;
  const STATUS = {
    reworking: ['На доработке', 'rew', 'Нужна доработка'], failed: ['Не зачтено', 'bad', 'Не зачтено'],
    appointed: ['Пора начать', 'apt', 'Пора начать'], reviewing: ['На проверке', 'rev', 'На проверке'],
    approved: ['Зачтено', 'ok', 'Зачтено'],
  };
  const st = s => STATUS[s] || ['Не начато', 'non', 'Не начато'];
  const ORDER = ['reworking', 'failed', 'appointed', 'reviewing', 'other', 'approved'];
  const key = s => (STATUS[s] ? s : 'other');
  const KIND = { test: 'Автопроверка', detailedAnswer: 'Проверка учителем' };
  const ICON = { rew: '📝', apt: '🚀', rev: '⏱️' };

  function toast(text, kind) {
    const el = document.createElement('div');
    el.className = 'toast' + (kind ? ' ' + kind : '');
    el.textContent = text;
    $('#toasts').append(el);
    setTimeout(() => el.remove(), kind === 'err' ? 6000 : 3500);
  }

  async function api(path, body) {
    const r = await fetch(path, {
      method: body ? 'POST' : 'GET', credentials: 'same-origin',
      headers: body ? { 'X-Site': '1', 'Content-Type': 'application/json' } : { 'X-Site': '1' },
      body: body ? JSON.stringify(body) : undefined,
    });
    let d = {};
    try { d = await r.json(); } catch (e) { /* не JSON */ }
    if (r.status === 401) {
      if (S.authed !== false) { S.authed = false; render(); }
      throw new Error(d.error || 'Нужен вход');
    }
    if (!r.ok) throw new Error(d.error || `Ошибка ${r.status}`);
    return d;
  }

  // кнопка «ждёт», пока идёт действие
  async function busy(btn, fn) {
    if (btn) { btn.classList.add('wait'); btn.dataset.html = btn.innerHTML; btn.innerHTML = '<span class="spin"></span>' + btn.innerHTML; }
    try { return await fn(); } catch (e) { toast(e.message, 'err'); return null; } finally {
      if (btn && btn.isConnected) { btn.classList.remove('wait'); btn.innerHTML = btn.dataset.html; }
    }
  }

  /* ---------- данные ---------- */
  const ov = () => (S.ov || {});
  const topics = () => ov().topics || [];
  const topic = uuid => topics().find(t => t.uuid === uuid);
  const grade = name => (ov().grades || []).find(g => g.name === name);
  const subjects = () => {
    const names = new Set([...topics().map(t => t.subj), ...(ov().grades || []).map(g => g.name)]);
    return [...names].filter(Boolean).sort((a, b) => a.localeCompare(b, 'ru'));
  };
  const icon = name => (topics().find(t => t.subj === name) || (ov().tasks || []).find(t => t.subj === name) || {}).icon || '📘';
  const debts = () => ov().tasks || [];
  // «На проверке» в списке долгов нет — берём из тем (обязательные, уровни 1–3, как на главной платформы)
  const reviewing = () => topics().flatMap(tp => tp.tasks
    .filter(t => t.status === 'reviewing' && t.req !== false && (t.lvl == null || t.lvl <= 3))
    .map(t => ({ ...t, subj: tp.subj, icon: tp.icon, topic: tp.title, topicId: tp.uuid, url: taskUrl(tp.uuid, t.uuid), rawStatus: 'reviewing', late: false, dl: dm(tp.deadline) })));

  async function loadOv(fresh) {
    try {
      const r = fresh ? await api('/api/overview', { fresh: true }) : await api('/api/overview');
      if (r.data) S.ov = r.data;
      S.ovLoading = r.loading;
      S.ovError = r.error || '';
      if (S.ovError && !r.loading) toast(S.ovError, 'err');
      render();
      if (r.loading) setTimeout(() => loadOv(false), 4000);
    } catch (e) { if (S.authed !== false) toast(e.message, 'err'); }
  }

  async function tick() {
    clearTimeout(S.timer);
    try {
      const [state, run] = await Promise.all([api('/api/state'), api('/api/site/running')]);
      const before = [...S.running].join();
      if (!S.st && !S.seenLast && state.last) { S.seenLast = state.last.at; store.set('seenLast', S.seenLast); }  // старое не показываем
      S.st = state;
      S.running = new Set(run.running || []);
      const busyNow = state.busy || S.running.size > 0;
      if (S.wasBusy && !busyNow) setTimeout(() => loadOv(false), 4000);  // бот обновит данные после решения
      if (before !== [...S.running].join()) render();
      S.wasBusy = busyNow;
      dock();
    } catch (e) { /* сеть моргнула — следующий опрос */ }
    const fast = (S.st && S.st.busy) || S.running.size || (S.prep && S.prep.running);
    S.timer = setTimeout(tick, fast ? 2500 : 10000);
  }

  /* ---------- общие куски ---------- */
  function hero({ title, sub = '', back = '', backTo = '', green = false, center = false, dates = '', note = '' }) {
    const upd = ov().at ? new Date(ov().at).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }) : '';
    return `<header class="hero${green ? ' green' : ''}"><div class="wrap">
      <div class="topbar">
        ${back ? `<a class="back" href="${backTo}">← ${esc(back)}</a>` : '<button class="theme" data-a="theme"><i></i><span>Тёмная тема</span></button>'}
        <span class="sp"></span>
        ${S.ovLoading ? '<span class="upd"><span class="spin" style="border-color:rgba(255,255,255,.3);border-top-color:#fff;width:14px;height:14px;border-width:2px"></span>обновляю</span>'
          : upd ? `<span class="upd">данные ${upd}</span>` : ''}
        <button class="iconbtn" data-a="refresh" title="Обновить с HDP" aria-label="Обновить">↻</button>
        ${back ? '<a class="iconbtn" href="#/" title="Домой" aria-label="Домой">⌂</a>' : '<a class="pillbtn" href="#/prep">📖<span class="lbl"> Подготовка</span></a><span class="avatar">ГЛ</span>'}
      </div>
      <h1 class="${center ? 'center' : ''}">${esc(title)}</h1>
      ${sub ? `<div class="sub ${center ? 'center' : ''}">${esc(sub)}</div>` : ''}
      ${dates}
      ${note}
    </div></header>`;
  }

  function counts(list) {
    const c = { reworking: 0, appointed: 0, reviewing: 0, other: 0, approved: 0 };
    for (const t of list) {
      const k = t.status === 'failed' ? 'reworking' : key(t.status);
      c[k] = (c[k] || 0) + 1;
    }
    return `<span class="counts" title="На доработке · Пора начать · На проверке · Не начато · Зачтено">
      <span class="s-rew">${c.reworking}</span><span class="s-apt">${c.appointed}</span><span class="s-rev">${c.reviewing}</span><span class="s-non">${c.other}</span><span class="s-ok">${c.approved}</span></span>`;
  }

  // полоса уровня 0.0 → 4.0 с огоньком, как на платформе
  function lvbar(level) {
    const pts = [0, 1, 2, 3, 4];
    const pct = ['0%', '20%', '50%', '90%', '120%'];
    const x = level == null ? 0 : Math.max(0, Math.min(4, level));
    const pos = v => 4 + v / 4 * 92;
    const on = p => (level != null && level >= p ? 'on' : '');
    return `<div class="lvbar"><div class="track"><div class="fill" style="width:${x / 4 * 100}%"></div></div>
      ${pts.map((p, i) => `<span class="flag ${on(p)}" style="left:${pos(p)}%">${p.toFixed(1)}</span>
        <span class="node ${on(p)}" style="left:${pos(p)}%"></span>
        <span class="pct" style="left:${pos(p)}%">${pct[i]}</span>`).join('')}
      ${level != null ? `<span class="fire" style="left:${pos(x)}%">🔥</span>` : ''}</div>`;
  }

  // карточка задания с кнопками
  function taskCard(t) {
    const [name, cls, label] = st(t.status || t.rawStatus);
    const status = t.status || t.rawStatus;
    const id = t.uuid || idOf(t.url);
    const url = t.url || taskUrl(t.topicId, id);
    const isTest = t.type === 'test' || t.auto;
    const done = status === 'approved' || status === 'reviewing';
    const run = S.running.has(id);
    const meta = [];
    if (t.score != null && t.max) meta.push(`${t.score} из ${t.max} баллов`);
    if (t.used) meta.push(`попыток: ${t.used}${t.left != null ? ` из ${t.used + t.left}` : ''}`);
    if (t.late) meta.push(`<span class="late">⏰ срок вышел ${esc(t.dl || '')}</span>`);
    else if (t.dl && !done) meta.push(`до ${esc(t.dl)}`);
    let acts;
    if (run) acts = '<div class="running"><span class="spin" style="width:16px;height:16px;border-width:2px"></span>Решаю… результат придёт сюда и в чат</div>';
    else if (done) acts = `<div class="acts">${isTest ? `<button class="btn small ghost" data-a="solve" data-show="1" data-url="${esc(url)}" data-t="${esc(t.title)}">👁 Ответы</button>` : ''}
      <a class="btn small ghost" href="${esc(url)}" target="_blank" rel="noopener">↗ HDP</a></div>`;
    else acts = `<div class="acts">
      <button class="btn small primary" data-a="solve" data-url="${esc(url)}" data-t="${esc(t.title)}" data-test="${isTest ? 1 : ''}">${isTest ? '⚡ Сделать' : '✍️ Написать'}</button>
      <button class="btn small ghost" data-a="solve" data-show="1" data-url="${esc(url)}" data-t="${esc(t.title)}">👁 ${isTest ? 'Ответы' : 'Ответ'}</button>
      <a class="btn small ghost" href="${esc(url)}" target="_blank" rel="noopener" title="Открыть на HDP">↗</a></div>`;
    return `<div class="task ${cls}${run ? ' run' : ''}">
      <div class="kind">${KIND[t.type] || (isTest ? KIND.test : KIND.detailedAnswer)}${t.req === false ? ' · не обязательное' : ''}</div>
      <a class="nm" href="${esc(url)}" target="_blank" rel="noopener">${esc(t.title)}</a>
      ${t.topic && t.showTopic ? `<div class="meta">${esc(t.topic)}</div>` : ''}
      ${meta.length ? `<div class="meta">${meta.join(' · ')}</div>` : ''}
      <div class="foot">${acts}<span class="st s-${cls}">${esc(label || name)}</span></div></div>`;
  }

  // «📖 Подготовь меня»: выбор времени прямо на месте
  function prepButton(tp, cls = 'btn') {
    const open = S.chooser === tp.uuid;
    if (!open) return `<button class="${cls} primary" data-a="chooser" data-id="${tp.uuid}">📖 Подготовь меня</button>`;
    return `<span class="prep-chooser"><span class="muted">За сколько?</span>
      ${[[1, '⚡ 1 мин'], [5, '📝 5 мин'], [10, '📚 10 мин']].map(([m, t]) =>
        `<button class="${cls} ${m === 5 ? 'primary' : ''}" data-a="prep-topic" data-id="${tp.uuid}" data-m="${m}">${t}</button>`).join('')}
      <button class="${cls} ghost" data-a="chooser" data-id="">✕</button></span>`;
  }

  function topicCard(tp, label) {
    const req = tp.tasks.filter(t => t.req !== false);
    const autos = tp.tasks.filter(t => t.type === 'test' && !['approved', 'reviewing'].includes(t.status) && t.req !== false);
    return `<div class="card topic-card">
      <div class="topic-head">${label ? `<span class="lvl">${esc(label)} <b>${lv(tp.achieved)}</b></span>` : ''}</div>
      <div class="topic-head" style="margin-top:12px"><h3><a href="#/t/${tp.uuid}" style="text-decoration:none">${esc(tp.title)}</a></h3>
        <span class="muted">${dm(tp.start)} — ${dm(tp.deadline)}</span></div>
      <div class="row"><div>${lvbar(tp.achieved)}</div>
        <div class="side"><small>Обязательные задания по статусам</small>${counts(req)}</div></div>
      <div class="topic-actions"><a class="btn" href="#/t/${tp.uuid}">Открыть тему</a>${prepButton(tp)}
        ${autos.length ? `<button class="btn ghost" data-a="auto-topic" data-id="${tp.uuid}">⚡ Автотесты (${autos.length})</button>` : ''}</div></div>`;
  }

  const skeleton = n => Array.from({ length: n }, () => '<div class="sk" style="height:150px"></div>').join('');
  const loadingOr = html => (S.ov ? html : `<div class="grid">${skeleton(4)}</div>`);

  /* ---------- страницы ---------- */
  function home() {
    const d = debts();
    const rew = d.filter(t => ['reworking', 'failed'].includes(t.rawStatus)).length;
    const apt = d.filter(t => t.rawStatus === 'appointed').length;
    const rev = reviewing().length;
    const late = d.filter(t => t.late).length;
    const autos = d.filter(t => t.auto).length;
    const comments = (ov().comments || []).length;
    return hero({ title: 'Мой профиль' }) + `<main class="page"><div class="wrap">
      <section class="card status-card">
        <h2>Статусы по заданиям<span class="sp"></span><span class="levels"><span class="on">1.0</span><span class="on">2.0</span><span class="on">3.0</span><span>4.0</span></span></h2>
        ${late ? `<div class="warn"><b>!</b>Из них по ${tasksWord(late)} вышли сроки</div>` : ''}
        ${S.ov ? `<div class="stats">
          <a class="stat" href="#/tasks?f=rew"><span class="ic s-rew">📝</span><span><small>На доработке</small><b>${tasksWord(rew)}</b></span></a>
          <a class="stat" href="#/tasks?f=apt"><span class="ic s-apt">🚀</span><span><small>Пора начать</small><b>${tasksWord(apt)}</b></span></a>
          <a class="stat" href="#/tasks?f=rev"><span class="ic s-rev">⏱️</span><span><small>На проверке</small><b>${tasksWord(rev)}</b></span></a>
          <a class="go" href="#/tasks" aria-label="Все задания">›</a></div>
          ${autos ? `<div class="topic-actions"><button class="btn primary" data-a="auto">⚡ Сдать все автотесты (${autos})</button><span class="muted" style="align-self:center">бот решит и отправит тесты с автопроверкой</span></div>` : ''}`
          : '<div class="sk" style="height:60px;margin-top:18px"></div>'}
      </section>
      <div class="grid" style="margin-top:14px">
        <a class="tile" href="#/subjects"><div class="tx"><h3>Мои предметы</h3><p>Темы, теория и задания — с кнопками «Сделать» и «Подготовь меня»</p></div><div class="art">🎓</div></a>
        <a class="tile" href="#/grades"><div class="tx"><h3>Успеваемость</h3><p>Уровень и оценка по каждому предмету, сколько до следующей</p></div><div class="art">🚀</div></a>
        <a class="tile new" href="#/prep"><div class="tx"><h3>Подготовка</h3><p>Подготовь меня к проверочной: конспект и вопросы по тому, что прошли</p><span class="tag">✨ от бота</span></div><div class="art">📖</div></a>
        <a class="tile" href="#/comments"><div class="tx"><h3>Комментарии учителей</h3><p>Что написали учителя к заданиям</p>${comments ? `<span class="tag">💬 ${comments}</span>` : ''}</div><div class="art">💬</div></a>
        <a class="tile" href="#/tasks"><div class="tx"><h3>Все задания</h3><p>Долги по предметам: доработка, новые, на проверке</p></div><div class="art">📋</div></a>
        <a class="tile" href="${HDP}/student" target="_blank" rel="noopener"><div class="tx"><h3>Платформа HDP</h3><p>Открыть настоящий сайт horodigital.ru</p></div><div class="art">🏫</div></a>
      </div>
      <div class="foot-note">Действия идут через бота @${BOT} — результат приходит и сюда, и в чат · <a href="/site/logout">выйти</a></div>
    </div></main>`;
  }

  function tasksPage(q) {
    const f = S.filt.tasks || new Set(q.get('f') ? [q.get('f')] : []);
    S.filt.tasks = f;
    const all = [...debts(), ...reviewing()].map(t => ({ ...t, cls: st(t.rawStatus)[1], showTopic: true }));
    const n = c => all.filter(t => t.cls === c && (!S.onlyLate || t.late)).length;
    let list = all.filter(t => (!f.size || f.has(t.cls === 'bad' ? 'rew' : t.cls)) && (!S.onlyLate || t.late));
    const by = {};
    for (const t of list) (by[t.subj] ||= []).push(t);
    const subs = Object.keys(by).sort((a, b) => a.localeCompare(b, 'ru'));
    const chip = (c, name, k) => `<button class="filter s-${c}${f.has(c) ? ' on' : ''}" data-a="filt" data-k="${c}">${name}<small>(${k})</small></button>`;
    return hero({ title: 'Статусы по заданиям', sub: 'Обязательные задания уровней 1.0–3.0', center: true, back: 'Профиль', backTo: '#/' }) + `<main class="page"><div class="wrap">
      <section class="card">
        <div class="section" style="margin-top:0"><h2>Задания по предметам<span class="sp"></span>
          <button class="toggle${S.onlyLate ? ' on' : ''}" data-a="late"><i></i>Вышли сроки</button></h2></div>
        <div class="filters">${chip('rew', 'На доработке', n('rew') + n('bad'))}${chip('apt', 'Пора начать', n('apt'))}${chip('rev', 'На проверке', n('rev'))}</div>
        ${!S.ov ? skeleton(2) : !subs.length ? '<div class="empty">🎉 Тут пусто — всё сделано</div>'
          : subs.map(s => `<div class="subj-h">${esc(icon(s))} <a href="#/s/${enc(s)}" style="text-decoration:none">${esc(s)}</a></div>
            <div class="tasks">${by[s].sort((a, b) => ORDER.indexOf(key(a.rawStatus)) - ORDER.indexOf(key(b.rawStatus))).map(taskCard).join('')}</div>`).join('')}
      </section></div></main>`;
  }

  function subjectsPage() {
    const late = new Set(debts().filter(t => t.late).map(t => t.subj));
    return hero({ title: 'Мои предметы', center: true, back: 'Профиль', backTo: '#/',
      note: late.size ? '<div class="note-top" style="margin-top:14px"><span class="alert" style="position:static;width:18px;height:18px;border-radius:50%;background:#ef6a3a;display:inline-grid;place-items:center;font-size:11px">!</span>Есть темы с просроченными заданиями</div>' : '' }) +
      `<main class="page"><div class="wrap">${loadingOr(`<div class="grid three">${subjects().map(s => {
        const g = grade(s);
        return `<a class="subj" href="#/s/${enc(s)}"><span class="nm">${esc(s)}</span><span class="die">🎲</span>
          ${g ? `<span class="lv">${lv(g.lvl)} · «${g.five}»</span>` : ''}${late.has(s) ? '<span class="alert">!</span>' : ''}</a>`;
      }).join('')}</div>`)}</div></main>`;
  }

  function subjectPage(name) {
    const g = grade(name);
    const mine = topics().filter(t => t.subj === name).sort((a, b) => b.start.localeCompare(a.start));
    const d = today();
    const now = mine.filter(t => t.start <= d && t.deadline >= d);
    const past = mine.filter(t => t.deadline < d);
    const future = mine.filter(t => t.start > d).reverse();
    const tab = S.tab[name] || 'past';
    const list = tab === 'past' ? past : future;
    return hero({ title: name, center: true, green: true, back: 'К предметам', backTo: '#/subjects' }) + `<main class="page"><div class="wrap">
      ${g ? `<section class="card target"><span class="ic">🏆</span><span class="sp">Уровень по предмету · оценка «${g.five}»${g.next ? ` · на «${g.next[1]}» нужно ${lv(g.next[0])}` : ' · максимум'}</span><span class="big-lvl">${lv(g.lvl)}</span></section>
      <section class="card kpis">
        <div class="kpi"><span class="ic">🎓</span><span><b>${g.ia == null ? '—' : g.ia + '%'}</b><small>Интегральная оценка за год</small></span></div>
        <div class="kpi"><span class="ic">📊</span><span><b>«${g.five}»</b><small>Оценка по шкале школы</small></span></div>
        <div class="kpi"><span class="ic">🧱</span><span><b>${lv(g.lvl)}</b><small>Средний уровень</small></span></div>
      </section>` : ''}
      ${!S.ov ? skeleton(2) : ''}
      ${now.length ? `<section class="card" style="margin-top:14px"><h2 style="font-size:22px">Сейчас изучается</h2>
        <div class="muted">Подготовка по теме — конспект и вопросы от бота по материалам этой темы</div></section>
        ${now.map(t => topicCard(t, 'Текущий уровень:')).join('')}` : ''}
      <div class="tabs"><button class="${tab === 'past' ? 'on' : ''}" data-a="tab" data-s="${esc(name)}" data-v="past">Завершённые темы (${past.length})</button>
        <button class="${tab === 'future' ? 'on' : ''}" data-a="tab" data-s="${esc(name)}" data-v="future">Будущие темы (${future.length})</button></div>
      ${list.length ? list.map(t => topicCard(t, tab === 'past' ? 'Итоговый уровень:' : '')).join('') : S.ov ? '<div class="empty">Тем нет</div>' : ''}
    </div></main>`;
  }

  function topicPage(uuid) {
    const tp = topic(uuid);
    if (!tp) {
      return hero({ title: S.ov ? 'Тема не найдена' : 'Загружаю…', green: true, back: 'К предметам', backTo: '#/subjects' })
        + `<main class="page"><div class="wrap">${S.ov ? '<div class="card empty">Такой темы нет в данных — нажми ↻, чтобы обновить с HDP</div>' : skeleton(2)}</div></main>`;
    }
    const all = S.onlyReq ? tp.tasks.filter(t => t.req !== false) : tp.tasks;
    const f = S.filt[uuid] || new Set();
    const c = k => all.filter(t => (k === 'rew' ? ['reworking', 'failed'].includes(t.status) : st(t.status)[1] === k)).length;
    const shown = all.filter(t => !f.size || f.has(st(t.status)[1] === 'bad' ? 'rew' : st(t.status)[1]));
    const levels = {};
    for (const t of shown) (levels[t.lvl == null ? 0 : t.lvl] ||= []).push(t);
    const req = tp.tasks.filter(t => t.req !== false);
    const done = req.filter(t => t.status === 'approved').length;
    const autos = tp.tasks.filter(t => t.type === 'test' && !['approved', 'reviewing'].includes(t.status) && t.req !== false);
    const th = S.theory[uuid];
    const chip = (k, name) => `<button class="filter s-${k}${f.has(k) ? ' on' : ''}" data-a="tfilt" data-id="${uuid}" data-k="${k}">${name}<small>(${c(k)})</small></button>`;
    if (!th && !S.theoryErr[uuid]) loadTheory(uuid);
    return hero({ title: tp.title, green: true, back: tp.subj, backTo: `#/s/${enc(tp.subj)}`,
      dates: `<div class="dates"><span class="ic">${esc(tp.icon || '📘')}</span>${dm(tp.start)} — ${dm(tp.deadline)}</div>` }) + `<main class="page"><div class="wrap">
      <section class="card">
        <div class="topic-head"><span class="lvl">Итоговый уровень <b>${lv(tp.achieved)}</b></span><span class="sp" style="flex:1"></span>
          <span class="muted">Сдано ${done} из ${req.length} обязательных</span></div>
        ${lvbar(tp.achieved)}
        <div class="topic-actions">${prepButton(tp)}
          ${autos.length ? `<button class="btn" data-a="auto-topic" data-id="${uuid}">⚡ Сдать автотесты темы (${autos.length})</button>` : ''}
          <a class="btn ghost" href="${HDP}/student/topic/${uuid}" target="_blank" rel="noopener">↗ Тема на HDP</a></div>
      </section>
      <div class="section"><h2>Теория</h2>
        ${th ? (th.length ? `<div class="theory">${th.map((m, i) => `<button class="mat" data-a="mat" data-id="${uuid}" data-i="${i}"><span>${esc(m.title || 'Материал')}</span><span class="n">${i + 1}</span></button>`).join('')}</div>`
          : '<div class="card empty">В этой теме нет материалов</div>')
          : S.theoryErr[uuid] ? `<div class="card empty">${esc(S.theoryErr[uuid])} <button class="btn small" data-a="theory" data-id="${uuid}">Ещё раз</button></div>`
          : `<div class="theory">${Array.from({ length: 3 }, () => '<div class="sk" style="height:120px"></div>').join('')}</div><div class="muted" style="margin-top:8px">Беру теорию с HDP — до минуты</div>`}
      </div>
      <div class="section"><h2>Задания в теме по уровням<span class="sp"></span><button class="toggle${S.onlyReq ? ' on' : ''}" data-a="req"><i></i>Только обязательные</button></h2>
        <div class="filters">${chip('rew', 'На доработке')}${chip('apt', 'Пора начать')}${chip('rev', 'На проверке')}${chip('non', 'Не начато')}${chip('ok', 'Зачтено')}</div>
        ${Object.keys(levels).sort((a, b) => a - b).map(l => {
          const inLvl = req.filter(t => String(t.lvl == null ? 0 : t.lvl) === l);
          return `<div class="lvl-h">${Number(l) ? `${Number(l).toFixed(1)} Уровень` : 'Задания'}<small>${inLvl.filter(t => t.status === 'approved').length}/${inLvl.length} обязательных зачтено</small></div>
          <div class="tasks">${levels[l].map(t => taskCard({ ...t, topicId: uuid, url: taskUrl(uuid, t.uuid), dl: dm(tp.deadline), late: tp.deadline < today() && !['approved', 'reviewing'].includes(t.status) && t.req !== false })).join('')}</div>`;
        }).join('') || '<div class="empty">Заданий нет</div>'}
      </div></div></main>`;
  }

  function gradesPage() {
    const gs = ov().grades || [];
    const avg = gs.length ? gs.reduce((s, g) => s + g.five, 0) / gs.length : 0;
    return hero({ title: 'Успеваемость', sub: 'Уровень → оценка: от 2.0 — «3», от 2.5 — «4», от 3.5 — «5»', center: true, back: 'Профиль', backTo: '#/' }) + `<main class="page"><div class="wrap">
      ${!S.ov ? skeleton(2) : `<section class="card kpis">
        <div class="kpi"><span class="ic">📊</span><span><b>${avg.toFixed(1)}</b><small>Средняя оценка</small></span></div>
        <div class="kpi"><span class="ic">🟢</span><span><b>${gs.filter(g => g.five >= 4).length}</b><small>Предметов на «4» и «5»</small></span></div>
        <div class="kpi"><span class="ic">🔴</span><span><b>${gs.filter(g => g.five <= 2).length}</b><small>Предметов на «2»</small></span></div></section>
        <section class="card">${gs.map(g => `<div class="grade">
          <a class="nm" href="#/s/${enc(g.name)}">${esc(icon(g.name))} ${esc(g.name)}<div class="muted" style="font-weight:400;font-size:13px">${lv(g.lvl)}${g.next ? ` · на «${g.next[1]}» нужно ${lv(g.next[0])}` : ' · максимум 🏆'}${g.ia != null ? ` · ${g.ia}%` : ''}</div></a>
          <div class="gbar"><i style="width:${Math.min(100, g.lvl / 4 * 100)}%"></i></div>
          <span class="five f${g.five}">${g.five}</span></div>`).join('')}</section>`}
    </div></main>`;
  }

  function commentsPage() {
    const cs = ov().comments || [];
    return hero({ title: 'Комментарии учителей', center: true, back: 'Профиль', backTo: '#/' }) + `<main class="page"><div class="wrap">
      ${!S.ov ? skeleton(2) : !cs.length ? '<div class="card empty">Комментариев нет</div>' : cs.map(c => `<section class="card">
        <div class="topic-head"><span style="font-size:22px">${esc(c.icon || '📘')}</span><h3><a href="${esc(c.url)}" target="_blank" rel="noopener" style="text-decoration:none">${esc(c.title)}</a></h3>
          <span class="muted">${esc(c.subj)}</span>${c.failed ? '<span class="st s-bad" style="border-radius:5px;padding:1px 8px">не зачтено</span>' : ''}</div>
        <blockquote style="margin:12px 0 0;padding:10px 14px;border-left:3px solid var(--brand);background:var(--card2);border-radius:8px">${fmt(c.comment)}</blockquote>
        ${c.score != null ? `<div class="muted" style="margin-top:8px">Баллы: ${esc(c.score)}${c.attempts ? ' · ' + esc(c.attempts) : ''}</div>` : ''}
        <div class="topic-actions"><button class="btn small primary" data-a="solve" data-url="${esc(c.url)}" data-t="${esc(c.title)}">🔁 Доделать по комментарию</button></div>
      </section>`).join('')}
    </div></main>`;
  }

  /* ---------- подготовка ---------- */
  async function loadPrep() {
    try {
      const r = await api('/api/prep/subjects');
      if (r.subjects.length || !r.loading) S.prepSubjects = r.subjects;
      if (r.loading) setTimeout(loadPrep, 4000);
      if (S.prepSubjects && !S.prepSubjects.find(s => s.name === S.prepSubj)) S.prepSubj = (S.prepSubjects[0] || {}).name || '';
      render();
    } catch (e) { toast(e.message, 'err'); }
  }

  async function pollPrep() {
    clearTimeout(S.prepTimer);
    S.prepTimer = 0;
    try {
      const was = S.prep;
      S.prep = await api('/api/prep/state');
      if (was && was.running && !S.prep.running) {
        if (S.prep.error) toast(S.prep.error, 'err');
        else { S.prepView = 'result'; S.prepOpen.clear(); window.scrollTo({ top: 0, behavior: 'smooth' }); }
      }
      if (route()[0] === 'prep') render();
    } catch (e) { /* следующий опрос */ }
    if (route()[0] === 'prep' || (S.prep && S.prep.running)) S.prepTimer = setTimeout(pollPrep, S.prep && S.prep.running ? 2000 : 8000);
  }

  function prepPage() {
    if (!S.prepSubjects) loadPrep();
    if (!S.prepTimer) S.prepTimer = setTimeout(pollPrep, 0);  // опрос хода подготовки, пока страница открыта
    const p = S.prep || {};
    let body;
    if (p.running) {
      body = `<section class="card"><div class="topic-head"><span class="spin"></span><h3>${esc(p.title || 'Готовлю')}</h3><span class="muted">${p.secs || 0} с</span></div>
        <div class="muted" style="margin-top:8px">${esc(p.sub || '')}</div><div class="muted" style="margin-top:8px">Claude читает теорию темы и вопросы прошлых тестов — обычно 1–3 минуты</div></section>`;
    } else if (S.prepView === 'result' && p.result) {
      body = prepResult(p.result);
    } else {
      const subs = S.prepSubjects;
      const cur = (subs || []).find(s => s.name === S.prepSubj);
      const sel = S.prepSel || (cur ? cur.recent.slice() : []);
      body = `<section class="card prep-form">
        <label class="h">Предмет</label>
        ${subs ? `<div class="chips">${subs.map(s => `<button class="chip${s.name === S.prepSubj ? ' on' : ''}" data-a="psubj" data-v="${esc(s.name)}">${esc(s.icon)} ${esc(s.short || s.name)}</button>`).join('')}</div>`
          : '<div class="sk" style="height:40px"></div>'}
        ${cur ? `<label class="h">Что прошли <span class="muted" style="font-weight:400">— отмечены недавние</span></label>
          <div class="chips">${cur.topics.map(t => `<button class="chip${sel.includes(t.uuid) ? ' on' : ''}" data-a="ptopic" data-v="${t.uuid}">${esc(t.title)}<small>${dm(t.start)}</small></button>`).join('')}</div>` : ''}
        <label class="h">Сколько есть времени</label>
        <div class="mins">${[[1, '⚡ 1 мин', 'главное'], [5, '📝 5 мин', 'конспект'], [10, '📚 10 мин', 'с задачами']].map(([m, t, s]) =>
          `<button class="min${S.prepMin === m ? ' on' : ''}" data-a="pmin" data-v="${m}"><b>${t}</b><small>${s}</small></button>`).join('')}</div>
        <label class="h" for="ptz">Своё ТЗ <span class="muted" style="font-weight:400">— необязательно</span></label>
        <textarea id="ptz" rows="3" placeholder="Например: проверочная по квадратным уравнениям, объясни теорему Виета">${esc(S.prepTz)}</textarea>
        <div class="topic-actions"><button class="btn primary" data-a="prep-run">📖 Подготовить</button>
          ${p.result ? '<button class="btn ghost" data-a="prep-last">Последняя подготовка</button>' : ''}</div>
      </section>`;
    }
    return hero({ title: 'Подготовка', sub: 'Конспект и вопросы с ответами по тому, что прошли на HDP', center: true, back: 'Профиль', backTo: '#/' })
      + `<main class="page"><div class="wrap">${body}</div></main>`;
  }

  function prepResult(r) {
    const SPAN = { 1: 'за 1 минуту', 5: 'за 5 минут', 10: 'за 10 минут' };
    let out = `<section class="card"><div class="topic-head"><span style="font-size:30px">${esc(r.icon || '📖')}</span>
      <div style="flex:1"><div class="muted">${esc(r.subj || 'Своё ТЗ')} · ${SPAN[r.minutes] || ''}</div><h3 style="font-size:22px;font-weight:600">${esc(r.title || r.subj || 'Подготовка')}</h3>
      ${r.tz ? `<div class="muted">ТЗ: ${esc(r.tz)}</div>` : ''}</div></div></section>`;
    if (r.text) out += `<section class="card prep-res"><div class="txt">${fmt(r.text)}</div></section>`;
    if ((r.points || []).length) out += `<div class="section"><h2>Главное</h2><section class="card prep-res"><ul>${r.points.map(x => `<li>${fmt(x)}</li>`).join('')}</ul></section></div>`;
    if ((r.sections || []).length) out += `<div class="section"><h2>Конспект</h2>${r.sections.map(s => `<section class="card prep-res">${s.title ? `<h3 style="margin-bottom:8px">${fmt(s.title)}</h3>` : ''}<div class="txt">${fmt(s.text)}</div></section>`).join('')}</div>`;
    if ((r.mistakes || []).length) out += `<div class="section"><h2>Типовые ошибки</h2><section class="card prep-res"><ul>${r.mistakes.map(x => `<li>${fmt(x)}</li>`).join('')}</ul></section></div>`;
    const qs = r.questions || [];
    if (qs.length) {
      const fresh = r.more ? qs.length - (r.added || 0) : qs.length;
      out += `<div class="section"><h2>Проверь себя<span class="sp"></span><span class="muted" style="font-size:15px;font-weight:400">${S.prepOpen.size} / ${qs.length}</span></h2><div class="qs">
        ${qs.map((q, i) => `<button class="card q${S.prepOpen.has(i) ? ' open' : ''}${i >= fresh ? ' new' : ''}" data-a="pq" data-i="${i}">
          <div class="qh"><span class="num">${i + 1}</span><div>${fmt(q.q)}</div></div>
          <div class="a"><span class="hint">Нажми — покажу ответ</span><div class="t">${fmt(q.a || '—')}</div></div></button>`).join('')}</div></div>`;
    }
    out += `<div class="topic-actions" style="justify-content:center;margin-top:24px">
      <button class="btn" data-a="prep-more">🔁 Ещё вопросы</button>
      ${r.minutes < 10 ? '<button class="btn primary" data-a="prep-deep">📚 Подробнее</button>' : ''}
      <button class="btn ghost" data-a="prep-new">Новая подготовка</button></div>`;
    return out;
  }

  function loginPage() {
    const expired = location.hash.includes('login-expired');
    return hero({ title: 'HDP', center: true }) + `<main class="page"><div class="wrap"><section class="card login">
      <div style="font-size:54px">🔐</div>
      <h2>${expired ? 'Ссылка уже не работает' : 'Вход на сайт HDP'}</h2>
      <p>${expired ? 'Ссылка входа одноразовая и живёт 15 минут.' : 'Сайт личный.'} Напиши боту <b>/site</b> — пришлю новую ссылку. Браузер запомнит вход на 30 дней.</p>
      <a class="btn primary" href="https://t.me/${BOT}?start=site" target="_blank" rel="noopener">Открыть @${BOT}</a>
    </section></div></main>`;
  }

  /* ---------- живой ход работы ---------- */
  function dock() {
    const s = S.st;
    const el = $('#dock');
    if (!s || S.authed === false) { el.innerHTML = ''; return; }
    const parts = [];
    if (s.live && s.busy) {
      const frac = s.live.frac;
      parts.push(`<div class="dock"><div class="hd"><span class="spin" style="width:18px;height:18px;border-width:2px"></span><b>${esc(s.live.title)}</b><span class="muted">${s.live.secs || 0} с</span></div>
        <div class="sub">${esc(s.live.sub)}${s.live.step ? ' · ' + esc(s.live.step) : ''}</div>
        <div class="bar${frac == null ? ' ind' : ''}"><i style="width:${Math.round((frac || 0) * 100)}%"></i></div>
        ${S.running.size > 1 ? `<div class="sub">В работе ещё: ${S.running.size - 1}</div>` : ''}</div>`);
    } else if (S.running.size) {
      parts.push(`<div class="dock"><div class="hd"><span class="spin" style="width:18px;height:18px;border-width:2px"></span><b>В работе: ${tasksWord(S.running.size)}</b></div></div>`);
    }
    if (s.draft) {
      parts.push(`<div class="dock draft"><div class="hd"><span>✍️</span><b>Черновик учителю готов</b></div>
        <div class="sub">${esc(s.draft.title)}${s.draft.files ? ` · картинок: ${s.draft.files}` : ''}</div>
        <div class="acts"><button class="btn small primary" data-a="draft-open">Посмотреть и отправить</button></div></div>`);
    }
    const l = s.last;
    if (l && l.at > S.seenLast && l.at > Date.now() / 1000 - 3600 && !(s.live && s.busy)) {
      parts.push(`<div class="dock ${l.ok ? 'ok' : 'fail'}"><div class="hd"><span>${l.ok ? (l.show ? '👁' : '✅') : '⚠️'}</span><b>${esc(l.title)}</b>
        <button class="x" data-a="last-x" aria-label="Закрыть">✕</button></div>
        ${l.score ? `<div class="sub">Баллы: ${esc(l.score.join(' из '))}</div>` : ''}
        ${l.text ? `<div class="body">${esc(l.text.slice(0, 1200))}</div>` : ''}
        <div class="acts">${l.url ? `<a class="btn small ghost" href="${esc(l.url)}" target="_blank" rel="noopener">↗ Задание на HDP</a>` : ''}</div></div>`);
    }
    el.innerHTML = parts.join('');
  }

  function draftModal() {
    const d = S.st && S.st.draft;
    if (!d) return;
    $('#modal').innerHTML = `<div class="overlay" data-a="modal-x"><div class="sheet" role="dialog" aria-modal="true">
      <div class="hd"><h2>✍️ ${esc(d.title)}</h2><button class="btn small ghost" data-a="modal-x">✕</button></div>
      <div class="muted" style="margin-bottom:10px">Черновик ответа учителю. Поправь текст — бот запомнит правки${d.files ? ` · картинок: ${d.files} (видны в чате)` : ''}</div>
      <textarea id="dtext">${esc(d.text)}</textarea>
      <div class="acts"><button class="btn ghost" data-a="draft" data-v="drop">❌ Не отправлять</button>
        <button class="btn" data-a="draft" data-v="save">💾 Сохранить</button>
        <button class="btn green" data-a="draft" data-v="send">✅ Отправить учителю</button></div></div></div>`;
  }

  function matModal(uuid, i) {
    const m = (S.theory[uuid] || [])[i];
    if (!m) return;
    S.mat = [uuid, i];
    const tp = topic(uuid);
    $('#modal').innerHTML = `<div class="overlay" data-a="modal-x"><div class="sheet" role="dialog" aria-modal="true">
      <div class="hd"><h2>${esc(m.title || 'Материал')}</h2><button class="btn small ghost" data-a="modal-x">✕</button></div>
      <div class="theory-text">${esc(m.text || 'Пусто (в материале только картинки или видео — открой на HDP)')}</div>
      <div class="acts">${tp ? prepButton(tp) : ''}<a class="btn ghost" href="${HDP}/student/topic/${uuid}" target="_blank" rel="noopener">↗ На HDP</a></div></div></div>`;
  }

  async function loadTheory(uuid) {
    if (S.theory['_' + uuid]) return;
    S.theory['_' + uuid] = true;
    try {
      const r = await api('/api/site/theory', { uuid });
      S.theory[uuid] = r.items;
    } catch (e) {
      S.theoryErr[uuid] = e.message;
    }
    delete S.theory['_' + uuid];
    if (route()[1] === uuid) render();
  }

  /* ---------- действия ---------- */
  async function solve(btn) {
    const show = btn.dataset.show === '1';
    const title = btn.dataset.t;
    if (!show && btn.dataset.test && !confirm(`Сдать «${title}»?\nБот решит тест и отправит ответы на HDP — это тратит попытку.`)) return;
    const r = await busy(btn, () => api('/api/solve', { url: btn.dataset.url, show }));
    if (!r) return;
    toast(show ? 'Смотрю ответы — покажу здесь и в чате' : btn.dataset.test ? 'Решаю и сдаю — результат придёт сюда и в чат' : 'Пишу ответ — черновик придёт на проверку тебе');
    const id = idOf(btn.dataset.url);
    if (id) S.running.add(id);
    render();
    tick();
  }

  async function autoTopic(btn, uuid) {
    const tp = topic(uuid);
    const autos = tp.tasks.filter(t => t.type === 'test' && !['approved', 'reviewing'].includes(t.status) && t.req !== false);
    if (!autos.length || !confirm(`Сдать ${tasksWord(autos.length)} с автопроверкой в теме «${tp.title}»?\nБот решает до 3 сразу, остальные — в очереди.`)) return;
    await busy(btn, async () => {
      for (const t of autos) {
        await api('/api/solve', { url: taskUrl(uuid, t.uuid) });
        S.running.add(t.uuid);
      }
    });
    toast('Запустил — по каждому тесту результат придёт сюда и в чат');
    render();
    tick();
  }

  async function prepTopic(btn, uuid, minutes) {
    const tp = topic(uuid);
    const r = await busy(btn, () => api('/api/prep/run', { subj: tp.subj, topics: [uuid], minutes }));
    if (!r) return;
    S.chooser = '';
    S.prepView = 'result';
    $('#modal').innerHTML = '';
    S.prep = { ...(S.prep || {}), running: true, title: 'Готовлю', sub: tp.title, secs: 0 };
    location.hash = '#/prep';
    pollPrep();
  }

  async function prepRun(btn, extra) {
    const tz = ($('#ptz') || {}).value || S.prepTz;
    S.prepTz = tz;
    const cur = (S.prepSubjects || []).find(s => s.name === S.prepSubj);
    const body = extra || { subj: S.prepSubj, topics: S.prepSel || (cur ? cur.recent : []), minutes: S.prepMin, tz };
    const r = await busy(btn, () => api('/api/prep/run', body));
    if (!r) return;
    S.prepView = 'result';
    S.prep = { ...(S.prep || {}), running: true, title: 'Готовлю', sub: '', secs: 0 };
    render();
    pollPrep();
  }

  document.addEventListener('click', async e => {
    const el = e.target.closest('[data-a]');
    if (!el) return;
    const a = el.dataset.a;
    if (a === 'modal-x') { if (e.target === el || el.tagName === 'BUTTON') $('#modal').innerHTML = ''; return; }
    e.preventDefault();
    const d = el.dataset;
    if (a === 'theme') {
      const dark = document.documentElement.dataset.theme !== 'dark';
      document.documentElement.dataset.theme = dark ? 'dark' : 'light';
      store.set('theme', dark ? 'dark' : 'light');
    } else if (a === 'refresh') {
      await busy(el, () => loadOv(true));
      toast('Обновляю с HDP — около минуты');
    } else if (a === 'solve') solve(el);
    else if (a === 'auto') {
      const n = debts().filter(t => t.auto).length;
      if (!confirm(`Сдать все тесты с автопроверкой (${n})?\nБот решит и отправит их на HDP.`)) return;
      const r = await busy(el, () => api('/api/auto', {}));
      if (r) { toast(`Запустил: ${tasksWord(r.n)} — результат придёт сюда и в чат`); tick(); }
    } else if (a === 'auto-topic') autoTopic(el, d.id);
    else if (a === 'chooser') { S.chooser = d.id; render(); if (S.mat && $('#modal').innerHTML) matModal(...S.mat); }
    else if (a === 'prep-topic') prepTopic(el, d.id, Number(d.m));
    else if (a === 'filt') { const f = S.filt.tasks; f.has(d.k) ? f.delete(d.k) : f.add(d.k); render(); }
    else if (a === 'tfilt') { const f = S.filt[d.id] ||= new Set(); f.has(d.k) ? f.delete(d.k) : f.add(d.k); render(); }
    else if (a === 'late') { S.onlyLate = !S.onlyLate; render(); }
    else if (a === 'req') { S.onlyReq = !S.onlyReq; render(); }
    else if (a === 'tab') { S.tab[d.s] = d.v; render(); }
    else if (a === 'theory') { delete S.theoryErr[d.id]; render(); }
    else if (a === 'mat') matModal(d.id, Number(d.i));
    else if (a === 'last-x') { S.seenLast = S.st.last.at; store.set('seenLast', S.seenLast); dock(); }
    else if (a === 'draft-open') draftModal();
    else if (a === 'draft') {
      const body = { text: ($('#dtext') || {}).value || '' };
      if (d.v === 'send' && !confirm('Отправить ответ учителю на HDP?')) return;
      if (d.v !== 'drop') await api('/api/draft/save', body).catch(x => toast(x.message, 'err'));
      const r = d.v === 'save' ? true : await busy(el, () => api('/api/draft/' + d.v, body));
      if (r) { toast(d.v === 'send' ? 'Отправляю учителю…' : d.v === 'drop' ? 'Не отправляю' : 'Сохранил'); if (d.v !== 'save') $('#modal').innerHTML = ''; tick(); }
    } else if (a === 'psubj') { S.prepSubj = d.v; S.prepSel = null; S.prepTz = ($('#ptz') || {}).value || S.prepTz; render(); }
    else if (a === 'ptopic') {
      const cur = (S.prepSubjects || []).find(s => s.name === S.prepSubj);
      const sel = S.prepSel || (cur ? cur.recent.slice() : []);
      S.prepSel = sel.includes(d.v) ? sel.filter(x => x !== d.v) : [...sel, d.v];
      S.prepTz = ($('#ptz') || {}).value || S.prepTz;
      render();
    } else if (a === 'pmin') { S.prepMin = Number(d.v); store.set('prepMin', S.prepMin); S.prepTz = ($('#ptz') || {}).value || S.prepTz; render(); }
    else if (a === 'prep-run') prepRun(el);
    else if (a === 'prep-more') prepRun(el, { more: true, minutes: (S.prep.result || {}).minutes || 5 });
    else if (a === 'prep-deep') prepRun(el, { again: true, minutes: 10 });
    else if (a === 'prep-new') { S.prepView = 'form'; render(); }
    else if (a === 'prep-last') { S.prepView = 'result'; render(); }
    else if (a === 'pq') { const i = Number(d.i); S.prepOpen.has(i) ? S.prepOpen.delete(i) : S.prepOpen.add(i); render(); }
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') $('#modal').innerHTML = ''; });

  /* ---------- маршруты ---------- */
  function route() {
    const h = location.hash.replace(/^#\/?/, '');
    const [path, qs] = h.split('?');
    const parts = path.split('/').map(x => { try { return decodeURIComponent(x); } catch (e) { return x; } });
    return [parts[0] || '', parts[1] || '', new URLSearchParams(qs || '')];
  }

  function render() {
    const [page, arg, q] = route();
    let html;
    if (S.authed === false) html = loginPage();
    else if (S.authed === null) html = '<div class="boot"><span class="spin"></span></div>';
    else if (page === 'tasks') html = tasksPage(q);
    else if (page === 'subjects') html = subjectsPage();
    else if (page === 's') html = subjectPage(arg);
    else if (page === 't') html = topicPage(arg);
    else if (page === 'grades') html = gradesPage();
    else if (page === 'comments') html = commentsPage();
    else if (page === 'prep') html = prepPage();
    else html = home();
    const y = window.scrollY;
    $('#app').innerHTML = html;
    if (S.lastRoute === location.hash) window.scrollTo(0, y);
    S.lastRoute = location.hash;
    const titles = { tasks: 'Задания', subjects: 'Предметы', grades: 'Успеваемость', comments: 'Комментарии', prep: 'Подготовка' };
    const name = page === 's' ? arg : page === 't' ? (topic(arg) || {}).title : titles[page];
    document.title = name ? `${name} · HDP` : 'HDP';
    dock();
  }

  window.addEventListener('hashchange', () => {
    S.filt.tasks = null;
    S.chooser = '';
    render();
    window.scrollTo(0, 0);
  });

  (async () => {
    try {
      await api('/api/me');
      S.authed = true;
      if (location.hash.includes('login-expired')) location.hash = '#/';
    } catch (e) {
      if (S.authed !== false) { S.authed = false; toast(e.message, 'err'); }
      render();
      return;
    }
    render();
    loadOv(false);
    tick();
    pollPrep();
  })();
})();
