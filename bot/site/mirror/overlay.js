'use strict';
/* HDP-бот поверх настоящего horodigital.ru (зеркало на сервере бота: bot/web.py вставляет этот файл в каждую страницу).
   Сайт — точь-в-точь платформа (Глеб, 15.10: «прям точь-в-точь»): в страницу НИЧЕГО не вставляем и родные элементы
   не двигаем. Всё бота — в панели «HDP-бот», которая открывается кнопкой-вкладкой справа (её можно спрятать в
   настройках; спрятана — панель открывает тройной тап по странице или Alt+B):
   · «Эта страница» — на задании: «⚡ Решить и сдать» / «👁 Ответы» (тест) или «✍️ Написать ответ», ход решения,
     черновик; на теме: «📖 Подготовь меня» (1/5/10 мин), «⚡ Автотесты темы» и кнопки у каждого задания темы;
   · Сейчас (ход работы, черновики учителю, автотесты), Ожидание, Подтверждения 2.0/3.0, Подготовка, Настройки.
   «⏻ Выключить всё» (шапка панели, Настройки) — /_hdp/off: cookie hdp_plain, сервер больше не вставляет этот файл,
   сайт — чистая платформа; включить обратно — 5 быстрых тапов или Alt+B (это ловит guard.js) → /_hdp/on.
   Данные и действия — API бота (bot/bot.py → ROUTES), те же функции, что кнопки в чате. Вход — cookie по ссылке из бота (/site).
   Тип и статус заданий — из API самой платформы (тот же запрос, что делает её страница темы), иначе из данных бота. */
(() => {
  if (window.__hdpx || window.top !== window) return;
  window.__hdpx = true;

  const BOT = 'HDP_DIGITALBOT';
  const ORIGIN = 'https://horodigital.ru';                 // бот принимает ссылки только на настоящий адрес
  const EDU = '/api/education/v1/students/current';
  const ID = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';
  const RX_TASK = new RegExp(`^/student/topic/(${ID})/task/(${ID})`);
  const RX_TOPIC = new RegExp(`^/student/topic/(${ID})/?$`);
  const RX_HREF = new RegExp(`/topic/(${ID})/task/(${ID})`);
  const NEED_LOGIN = 'Нужен вход: напиши боту /site — пришлю ссылку';
  const TABS = [['now', 'Сейчас'], ['wait', 'Ожидание'], ['exams', 'Подтверждения'], ['prep', 'Подготовка'], ['set', 'Настройки']];

  const store = {
    get: (k, d) => { try { const v = localStorage.getItem('hdpx.' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set: (k, v) => { try { localStorage.setItem('hdpx.' + k, JSON.stringify(v)); } catch (e) { /* без хранилища */ } },
  };
  const S = {
    authed: null, st: null, running: new Set(), busy: false, wasWorking: false, timer: 0,
    open: false, tab: store.get('tab', 'now'), wait: null, exams: null, examsLvl: store.get('examsLvl', 'all'),
settings: null, ov: null, times: {}, timeOpen: '', files: {}, filesOpen: {}, edits: {},
    prepSubjects: null, prep: null, prepSubj: '', prepSel: null, prepMin: store.get('prepMin', 5), prepTz: '',
    prepView: 'form', prepOpen: new Set(), prepTimer: 0, topics: {}, seen: store.get('seen', 0),
    chooser: false, lastDraftKeys: '', hideTab: store.get('hideTab', false), ctxKey: '',
  };

  /* ---------- мелочи ---------- */
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = s => esc(s).replace(/\*\*([^*\n]+?)\*\*/g, '<b>$1</b>').replace(/\*/g, '').replace(/\n/g, '<br>');
  const word = (n, a, b, c) => (n % 10 === 1 && n % 100 !== 11 ? a : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? b : c);
  const tasksWord = n => `${n} ${word(n, 'задание', 'задания', 'заданий')}`;
  const mmss = s => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
  const taskUrl = (topic, task) => `${ORIGIN}/student/topic/${topic}/task/${task}`;
  const idOf = url => ((String(url || '').match(RX_HREF) || [])[2]);
  const local = url => String(url || '').replace(/^https?:\/\/(www\.)?horodigital\.ru/, '') || '/student';  // ссылка внутри зеркала
  const MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
  const dm = s => { if (!s) return ''; const [, m, d] = String(s).slice(0, 10).split('-').map(Number); return `${d} ${MONTHS[m - 1]}`; };
  const hhmm = at => new Date(at * 1000).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow' });
  const soon = at => {
    const left = Math.max(0, Math.round(at - Date.now() / 1000));
    return left < 60 ? 'меньше минуты' : left < 3600 ? `через ${Math.floor(left / 60)} мин` : `через ${Math.floor(left / 3600)} ч ${Math.floor(left % 3600 / 60)} мин`;
  };
  const I = {
    bot: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="8" width="16" height="12" rx="4"/><path d="M12 8V5.5"/><circle cx="12" cy="4.2" r="1.2"/><path d="M9.2 13.2v1.6M14.8 13.2v1.6M2 13v2.6M22 13v2.6"/></svg>',
    x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" aria-hidden="true"><path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/></svg>',
    refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 12a8 8 0 1 1-2.34-5.66"/><path d="M20 4v4.5h-4.5"/></svg>',
    gear: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>',
    out: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4"/></svg>',
  };
  const spin = '<span class="hdpx-spin" aria-hidden="true"></span>';

  /* ---------- API бота ---------- */
  async function api(path, body) {
    let r;
    try {
      r = await fetch(path, {
        method: body ? 'POST' : 'GET', credentials: 'same-origin',
        headers: { 'X-Site': '1', 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined,
      });
    } catch (e) { throw new Error('Бот не отвечает — проверь интернет'); }
    let d = {};
    try { d = await r.json(); } catch (e) { /* не JSON */ }
    if (r.status === 401) { setAuth(false); throw new Error(NEED_LOGIN); }
    if (!r.ok) throw new Error(d.error || `Ошибка ${r.status}`);
    setAuth(true);
    return d;
  }

  function setAuth(v) {
    if (S.authed === v) return;
    S.authed = v;
    update();
  }

  // задания темы — из API платформы (страница темы делает тот же запрос), иначе из данных бота
  function norm(t) {
    const g = (t.progress && t.progress.grade) || {};
    return {
      uuid: t.uuid, title: (t.title || '').trim(), type: t.type, status: t.progress && t.progress.status ? t.progress.status.type : t.status,
      req: t.requirementType ? t.requirementType === 'mandatory' : t.req !== false, lvl: t.levelId != null ? t.levelId : t.lvl,
      used: g.usedAttemptsCount != null ? g.usedAttemptsCount : t.used || 0,
      left: g.remainingAttemptsCount !== undefined ? g.remainingAttemptsCount : t.left != null ? t.left : null,
    };
  }

  async function topicTasks(topic, fresh) {
    const c = S.topics[topic];
    if (c && !fresh && (c.loading || Date.now() - (c.at || 0) < 60000)) return c.loading || c.tasks;
    const job = (async () => {
      try {
        const r = await fetch(`${EDU}/topics/${topic}/tasks`, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
        if (!r.ok) throw new Error(String(r.status));
        const d = (await r.json()).data || {};
        return { title: (d.title || '').trim(), tasks: (d.tasks || []).filter(t => !t.hidden).map(norm) };
      } catch (e) {
        try {  // запасной путь: данные бота (overview.json)
          const ov = S.ov || (await api('/api/overview')).data;
          S.ov = ov || S.ov;
          const tp = ((ov || {}).topics || []).find(x => x.uuid === topic);
          return tp ? { title: tp.title, tasks: tp.tasks.map(norm) } : { title: '', tasks: [] };
        } catch (x) { return { title: '', tasks: [] }; }
      }
    })();
    S.topics[topic] = { ...(c || {}), loading: job.then(r => r.tasks) };
    const r = await job;
    S.topics[topic] = { at: Date.now(), tasks: r.tasks, title: r.title };
    update();
    return r.tasks;
  }
  const cachedTasks = topic => (S.topics[topic] && S.topics[topic].tasks) || null;
  const taskOf = (topic, task) => (cachedTasks(topic) || []).find(t => t.uuid === task);
  const isTest = t => t && t.type === 'test';
  const done = t => t && ['approved', 'reviewing'].includes(t.status);
  const autoOk = t => isTest(t) && !done(t) && t.req !== false && t.left !== 0;

  /* ---------- где мы ---------- */
  function where() {
    const p = location.pathname;
    let m = p.match(RX_TASK);
    if (m) return { kind: 'task', topic: m[1], task: m[2] };
    m = p.match(RX_TOPIC);
    if (m) return { kind: 'topic', topic: m[1] };
    return { kind: 'other' };
  }

  // переход внутри SPA платформы (как её собственные ссылки), без перезагрузки
  function go(path) {
    if (location.pathname + location.search === path) return;
    try {
      history.pushState({}, '', path);
      window.dispatchEvent(new PopStateEvent('popstate', { state: {} }));
    } catch (e) { location.href = path; }
  }

  /* ---------- всплывашки и подтверждение ---------- */
  function toast(text, kind) {
    let box = $('.hdpx-toasts');
    if (!box) { box = el('div', 'hdpx-toasts'); layer().append(box); }
    const t = el('div', 'hdpx-toast' + (kind ? ' hdpx-toast-' + kind : ''));
    t.textContent = text;
    box.append(t);
    setTimeout(() => t.remove(), kind === 'err' ? 6500 : 4000);
  }

  // Слой ровно по видимой части экрана. Платформа на телефоне шире экрана (у темы — 674 px при экране 390), и position:
  // fixed считается от этой ширины: вкладка и панель уезжали за правый край. Слой двигаем за visualViewport.
  function layer() {
    let L = document.querySelector('body > .hdpx-vv');
    if (!L) { L = el('div', 'hdpx-vv'); document.body.append(L); fitLayer(); }
    return L;
  }
  function fitLayer() {
    const L = document.querySelector('body > .hdpx-vv');
    if (!L) return;
    const v = window.visualViewport;
    const box = v ? { l: v.offsetLeft, t: v.offsetTop, w: v.width, h: v.height } : { l: 0, t: 0, w: document.documentElement.clientWidth, h: window.innerHeight };
    L.style.cssText = `left:${box.l}px;top:${box.t}px;width:${box.w}px;height:${box.h}px`;
    L.classList.toggle('hdpx-narrow', box.w <= 640);
  }
  const narrow = () => (window.visualViewport ? visualViewport.width : document.documentElement.clientWidth) <= 640;
  if (window.visualViewport) { visualViewport.addEventListener('resize', fitLayer); visualViewport.addEventListener('scroll', fitLayer); }
  window.addEventListener('resize', () => { fitLayer(); schedule(); });
  if (window.visualViewport) visualViewport.addEventListener('resize', () => schedule());

  function el(tag, cls, html) {
    const e = document.createElement(tag);
    e.className = cls;
    e.dataset.hdpx = '';
    if (html != null) e.innerHTML = html;
    return e;
  }

  function ask({ title, text = '', ok = 'Да', cancel = 'Отмена', danger = false }) {
    return new Promise(res => {
      const m = el('div', 'hdpx-modal', `<div class="hdpx-dialog" role="dialog" aria-modal="true">
        <p class="hdpx-h2">${esc(title)}</p>${text ? `<p class="hdpx-p">${esc(text)}</p>` : ''}
        <div class="hdpx-acts hdpx-acts-end"><button class="hdpx-btn hdpx-outline" data-v="0">${esc(cancel)}</button>
        <button class="hdpx-btn ${danger ? 'hdpx-danger' : 'hdpx-primary'}" data-v="1">${esc(ok)}</button></div></div>`);
      const finish = v => { m.remove(); document.removeEventListener('keydown', key, true); res(v); };
      const key = e => { if (e.key === 'Escape') { e.stopPropagation(); finish(false); } };
      m.addEventListener('click', e => {
        const b = e.target.closest('[data-v]');
        if (b) finish(b.dataset.v === '1');
        else if (e.target === m) finish(false);
      });
      document.addEventListener('keydown', key, true);
      layer().append(m);
      $('[data-v="1"]', m).focus();
    });
  }

  function lightbox(src, name) {
    const m = el('div', 'hdpx-modal hdpx-lightbox', `<img src="${esc(src)}" alt="${esc(name)}"><button class="hdpx-iconbtn hdpx-lb-x" aria-label="Закрыть">${I.x}</button>`);
    m.addEventListener('click', () => m.remove());
    layer().append(m);
  }

  // заменить содержимое своего блока, если оно поменялось; раскрытые <details data-k> остаются раскрытыми
  function swap(box, html) {
    if (box.dataset.html === html) return false;
    const open = $$('details[open][data-k]', box).map(x => x.dataset.k);
    box.innerHTML = html;
    box.dataset.html = html;
    for (const k of open) { const x = $(`details[data-k="${k}"]`, box); if (x) x.open = true; }
    return true;
  }

  // кнопка «ждёт», пока идёт действие
  async function busy(btn, fn) {
    if (btn) { btn.disabled = true; btn.classList.add('hdpx-wait'); }
    try { return await fn(); } catch (e) { toast(e.message, 'err'); return null; } finally {
      if (btn && btn.isConnected) { btn.disabled = false; btn.classList.remove('hdpx-wait'); }
    }
  }

  /* ---------- опрос бота ---------- */
  async function tick() {
    clearTimeout(S.timer);
    try {
      const [st, run] = await Promise.all([api('/api/state'), api('/api/site/running')]);
      S.st = st;
      S.running = new Set(run.running || []);
      S.busy = !!run.busy;
      const working = st.working || st.busy || S.running.size > 0;
      if (S.wasWorking && !working) {  // решение закончилось — статусы заданий на платформе поменялись
        for (const k of Object.keys(S.topics)) S.topics[k].at = 0;
        S.ov = null;
      }
      S.wasWorking = working;
      if (S.open) loadWait(true);
    } catch (e) { /* бот недоступен или нет входа — следующий опрос */ }
    update();
    const fast = (S.st && (S.st.working || S.st.busy)) || S.running.size || (S.prep && S.prep.running);
    S.timer = setTimeout(tick, document.hidden ? 30000 : S.authed === false ? 20000 : fast ? 2500 : 10000);
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) tick(); });

  async function loadWait(quiet) {
    try { S.wait = await api('/api/site/wait'); } catch (e) { if (!quiet) toast(e.message, 'err'); }
    if (S.open) renderPanel();
  }

  async function loadExams(fresh) {
    S.examsLoading = true;
    renderPanel();
    try {
      S.exams = await api('/api/site/exams', fresh ? { fresh: true } : null);
      if (S.exams.loading) setTimeout(() => { if (S.open && S.tab === 'exams') loadExams(false); }, 5000);
    } catch (e) { if (S.authed !== false) toast(e.message, 'err'); }  // нет входа — это и так видно в панели
    S.examsLoading = false;
    renderPanel();
  }

  async function loadSettings() {
    try { S.settings = await api('/api/site/settings'); } catch (e) { /* покажем позже */ }
    renderPanel();
  }

  async function loadOv() {
    try { const r = await api('/api/overview'); if (r.data) S.ov = r.data; } catch (e) { /* без счётчика */ }
    renderPanel();
  }

  /* ---------- вкладка справа и панель ---------- */
  function ensureTab() {
    let tab = $('.hdpx-tab');
    if (S.hideTab && !S.open) { if (tab) tab.remove(); return; }
    if (!tab) {
      tab = el('button', 'hdpx-tab', I.bot + '<span class="hdpx-badge" hidden></span>');
      tab.type = 'button';
      tab.title = 'HDP-бот';
      tab.setAttribute('aria-label', 'HDP-бот');
      tab.addEventListener('click', () => (S.open ? closePanel() : openPanel()));
      layer().append(tab);
    } else if (tab.parentElement !== layer()) layer().append(tab);
    // над родной вкладкой-календарём (если она есть на странице), с тем же шагом
    const own = $('[data-testid="aiAssistantButton"]');
    const r = own && own.getBoundingClientRect();
    const vv = window.visualViewport;
    const vw = vv ? vv.offsetLeft + vv.width : window.innerWidth, vh = vv ? vv.offsetTop + vv.height : window.innerHeight;
    const bottom = r && r.height && r.right >= vw - 2 ? Math.round(vh - r.top + 12) : 190;
    if (tab.style.bottom !== bottom + 'px') tab.style.bottom = bottom + 'px';
    const n = (S.st && (S.st.drafts || []).length) || 0;
    const badge = $('.hdpx-badge', tab);
    const work = S.running.size || (S.st && S.st.working);
    badge.hidden = !n && !work;
    badge.textContent = n ? String(n) : '';
    badge.classList.toggle('hdpx-badge-dot', !n && !!work);
    tab.classList.toggle('hdpx-tab-on', S.open);
  }

  function openPanel(tab, opts = {}) {
    if (tab) { S.tab = tab; store.set('tab', tab); } else if (where().kind !== 'other') S.tab = 'now';  // задание/тема — их кнопки сверху
    if (opts.draft) setTimeout(() => focusDraft(opts.draft), 320);
    let d = $('.hdpx-drawer');
    if (!d) {
      d = el('div', 'hdpx-drawer', `<div class="hdpx-dr-head"><span class="hdpx-ava">${I.bot}</span><p class="hdpx-dr-title">HDP-бот</p>
        <span class="hdpx-sp"></span><button class="hdpx-btn hdpx-s hdpx-outline" data-a="plain" title="Обычная платформа без кнопок бота">⏻ Выключить всё</button>
        <button class="hdpx-iconbtn" data-a="close" aria-label="Закрыть" title="Закрыть">${I.x}</button></div>
        <div class="hdpx-tabs" role="tablist"></div><div class="hdpx-dr-body"></div>`);
      d.setAttribute('role', 'dialog');
      d.setAttribute('aria-label', 'HDP-бот');
      d.addEventListener('focusout', () => setTimeout(() => { if (S.pending && !typing()) renderPanel(); }, 0));
      layer().append(d);
    }
    S.open = true;
    requestAnimationFrame(() => d.classList.add('hdpx-dr-open'));
    ensureBar();
    renderPanel(true);
    loadWait(true);
    if (!S.settings) loadSettings();
    if (S.tab === 'exams' && !S.exams) loadExams(false);
    if (S.tab === 'prep') prepOpen();
    if (S.tab === 'now' && !S.ov) loadOv();
    ensureTab();
  }

  function closePanel() {
    S.open = false;
    const d = $('.hdpx-drawer');
    if (d) { d.classList.remove('hdpx-dr-open'); setTimeout(() => { if (!S.open) d.remove(); }, 260); }
    ensureTab();
    ensureBar();
  }

  const typing = () => { const a = document.activeElement; return a && a.closest && a.closest('.hdpx-dr-body') && /^(TEXTAREA|INPUT)$/.test(a.tagName); };

  function renderPanel(force) {
    const d = $('.hdpx-drawer');
    if (!d || !S.open) return;
    const tabs = $('.hdpx-tabs', d);
    const th = TABS.map(([k, name]) => `<button class="hdpx-tabbtn${S.tab === k ? ' hdpx-tabbtn-on' : ''}" role="tab" aria-selected="${S.tab === k}" data-a="tab" data-v="${k}"${k === 'set' ? ` aria-label="${name}" title="${name}"` : ''}>${k === 'set' ? I.gear : name}${k === 'wait' && S.wait && S.wait.timed.length ? ` <span class="hdpx-count">${S.wait.timed.length}</span>` : ''}</button>`).join('');
    if (swap(tabs, th)) { const on = $('.hdpx-tabbtn-on', tabs); if (on) on.scrollIntoView({ block: 'nearest', inline: 'nearest' }); }
    if (typing() && !force) { S.pending = true; return; }  // не сбивать ввод текста
    S.pending = false;
    const body = $('.hdpx-dr-body', d);
    const html = S.authed === false ? loginCard() : ({ now: nowTab, wait: waitTab, exams: examsTab, prep: prepTab, set: setTab }[S.tab] || nowTab)();
    const y = body.scrollTop;
    if (!swap(body, html)) return;
    body.scrollTop = y;
    for (const ta of $$('textarea[data-draft]', body)) {  // правка, которую ещё не сохранили, остаётся
      if (S.edits[ta.dataset.draft] != null) ta.value = S.edits[ta.dataset.draft];
    }
  }

  // «Открыть черновик» на странице задания: панель прокручивается к его карточке (когда панель уже выехала)
  function focusDraft(task, tries = 8) {
    const body = $('.hdpx-dr-body');
    const card = body && $(`.hdpx-draft[data-task="${task}"]`, body);
    if (!card) { if (tries) setTimeout(() => focusDraft(task, tries - 1), 250); return; }
    body.scrollTop += card.getBoundingClientRect().top - body.getBoundingClientRect().top - 12;
    card.classList.remove('hdpx-flash');
    void card.offsetWidth;
    card.classList.add('hdpx-flash');
  }

  function loginCard(compact) {
    if (compact) return `<span class="hdpx-tb-hint">${esc(NEED_LOGIN)}</span>`;
    return `<div class="hdpx-card hdpx-center"><div class="hdpx-big">🔐</div><p class="hdpx-h3">Нужен вход</p>
      <p class="hdpx-p hdpx-muted">Напиши боту <b>/site</b> — пришлю ссылку. Браузер запомнит вход на 30 дней.</p>
      <a class="hdpx-btn hdpx-primary" href="https://t.me/${BOT}?start=site" target="_blank" rel="noopener">Открыть @${BOT}</a></div>`;
  }

  /* --- Сейчас --- */
  function liveCard() {
    const st = S.st || {};
    const out = [];
    const titles = (S.wait && S.wait.work) || [];
    if (st.live) {
      const f = st.live.frac;
      out.push(`<div class="hdpx-card hdpx-live"><div class="hdpx-row">${spin}<b class="hdpx-grow">${esc(st.live.title)}</b><span class="hdpx-muted">${mmss(st.live.secs || 0)}</span></div>
        ${st.live.sub ? `<p class="hdpx-p">${esc(st.live.sub)}</p>` : ''}
        ${st.live.step ? `<p class="hdpx-small hdpx-muted">▸ ${esc(st.live.step)}</p>` : ''}
        <div class="hdpx-bar${f == null ? ' hdpx-bar-ind' : ''}"><i style="width:${Math.round((f || 0) * 100)}%"></i></div>
        ${st.others ? `<p class="hdpx-small hdpx-muted">Рядом идёт ещё: ${st.others}</p>` : ''}</div>`);
    }
    const rest = titles.filter(w => !(st.live && st.live.sub && st.live.sub === w.title));
    if (rest.length) {
      out.push(`<div class="hdpx-card"><p class="hdpx-h3">${st.busy ? 'В работе и в очереди' : 'В работе'}</p>${rest.map(w => `<div class="hdpx-li">${spin}
        ${w.url ? `<a class="hdpx-link" href="${esc(local(w.url))}" data-a="go">${esc(w.title)}</a>` : `<span>${esc(w.title)}</span>`}</div>`).join('')}</div>`);
    } else if (!st.live && S.running.size) {
      out.push(`<div class="hdpx-card"><div class="hdpx-row">${spin}<b>В работе: ${tasksWord(S.running.size)}</b></div></div>`);
    }
    return out.join('');
  }

  function lastCard(onlyUrl) {
    const l = S.st && S.st.last;
    if (!l || l.at <= S.seen || l.at < Date.now() / 1000 - 3600 || (S.st && S.st.live && !onlyUrl)) return '';
    if (onlyUrl && idOf(l.url) !== onlyUrl) return '';
    const icon = l.ok ? (l.kind === 'sent' ? '📬' : l.show ? '👁' : '✅') : '⚠️';
    return `<div class="hdpx-card hdpx-last ${l.ok ? 'hdpx-ok' : 'hdpx-fail'}"><div class="hdpx-row"><span>${icon}</span><b class="hdpx-grow">${esc(l.title)}</b>
      <button class="hdpx-iconbtn hdpx-iconbtn-s" data-a="seen" aria-label="Скрыть" title="Скрыть">${I.x}</button></div>
      ${l.score ? `<p class="hdpx-p"><b>Баллы: ${esc(l.score.join(' из '))}</b></p>` : ''}
      ${l.text ? `<details class="hdpx-more" data-k="last-${l.at}"><summary>${esc(l.text.split('\n')[0].slice(0, 140))}</summary><div class="hdpx-pre">${esc(l.text)}</div></details>` : ''}
      ${l.url && !onlyUrl ? `<a class="hdpx-link" href="${esc(local(l.url))}" data-a="go">Открыть задание</a>` : ''}</div>`;
  }

  function draftCard(d) {
    const label = S.settings ? S.settings.label : '';
    const edited = S.edits[d.task] != null && S.edits[d.task] !== d.text;
    const files = S.files[d.task];
    const tOpen = S.timeOpen === d.task;
    const times = S.times[d.task] || [];
    let status = '';
    if (d.sending) status = `<div class="hdpx-note">${spin}<span>Отправляю учителю…</span></div>`;
    else if (d.send_at) {
      status = `<div class="hdpx-note hdpx-note-timer"><span class="hdpx-grow">⏳ Отправлю в <b>${hhmm(d.send_at)}</b> · ${soon(d.send_at)}</span>
        <button class="hdpx-btn hdpx-s hdpx-outline" data-a="send" data-m="now" data-task="${esc(d.task)}">⚡ Сейчас</button>
        <button class="hdpx-btn hdpx-s hdpx-outline" data-a="hold" data-task="${esc(d.task)}">✖ Отменить</button></div>`;
    }
    return `<div class="hdpx-card hdpx-draft" data-task="${esc(d.task)}">
      <div class="hdpx-row"><span>✍️</span><b class="hdpx-grow">${esc(d.title)}</b>
        ${d.url ? `<a class="hdpx-iconbtn hdpx-iconbtn-s" href="${esc(local(d.url))}" data-a="go" title="Открыть задание" aria-label="Открыть задание">${I.out}</a>` : ''}</div>
      <p class="hdpx-small hdpx-muted">Черновик ответа учителю — поправь текст, бот запомнит правки</p>
      ${status}
      <textarea class="hdpx-ta" data-draft="${esc(d.task)}" rows="5" placeholder="Ответ — на картинках, без текста">${esc(d.text)}</textarea>
      ${d.files ? (files ? `<div class="hdpx-thumbs">${files.map((f, i) => (f.data
        ? `<button class="hdpx-thumb" data-a="img" data-task="${esc(d.task)}" data-i="${i}"><img src="${esc(f.data)}" alt="${esc(f.name)}"></button>`
        : `<span class="hdpx-thumb hdpx-thumb-file">📄 ${esc(f.name)}</span>`)).join('')}</div>`
        : `<button class="hdpx-btn hdpx-s hdpx-outline" data-a="files" data-task="${esc(d.task)}">🖼 Картинки (${d.files})</button>`) : ''}
      ${edited ? `<div class="hdpx-acts"><button class="hdpx-btn hdpx-s hdpx-outline" data-a="save" data-task="${esc(d.task)}">💾 Сохранить правку</button></div>` : ''}
      ${d.sending ? '' : `<div class="hdpx-acts"><button class="hdpx-btn hdpx-primary hdpx-grow" data-a="send" data-m="send" data-task="${esc(d.task)}">✅ Отправить учителю</button></div>
      ${label ? `<p class="hdpx-small hdpx-muted hdpx-tc">по настройке: ${label === 'сразу' ? 'сразу' : 'через ' + esc(label)}</p>` : ''}
      <div class="hdpx-acts hdpx-acts-3"><button class="hdpx-btn hdpx-s hdpx-outline" data-a="send" data-m="now" data-task="${esc(d.task)}">⚡ Сразу</button>
        <button class="hdpx-btn hdpx-s hdpx-outline" data-a="send" data-m="later" data-task="${esc(d.task)}">⏳ С задержкой</button>
        <button class="hdpx-btn hdpx-s hdpx-outline${tOpen ? ' hdpx-on' : ''}" data-a="times" data-task="${esc(d.task)}">🕐 Ко времени</button></div>
      ${tOpen ? `<div class="hdpx-times">${times.length ? times.map(t => `<button class="hdpx-chip" data-a="at" data-task="${esc(d.task)}" data-at="${t.at}">${esc(t.label)}</button>`).join('') : `<span class="hdpx-muted hdpx-small">${spin} загружаю…</span>`}
        <form class="hdpx-timeform" data-task="${esc(d.task)}"><input class="hdpx-input" name="t" placeholder="19:40 или через 40 минут" autocomplete="off"><button class="hdpx-btn hdpx-s hdpx-primary">ОК</button></form></div>` : ''}
      <div class="hdpx-acts"><button class="hdpx-btn hdpx-s hdpx-ghost hdpx-red" data-a="drop" data-task="${esc(d.task)}">Удалить черновик</button></div>`}
    </div>`;
  }

  function nowTab() {
    const st = S.st;
    if (!st) return `<div class="hdpx-card hdpx-center">${spin}<p class="hdpx-p hdpx-muted">Связываюсь с ботом…</p></div>`;
    const parts = [hereCard(), liveCard(), lastCard()];
    const drafts = st.drafts || [];
    if (drafts.length) parts.push(`<p class="hdpx-h3 hdpx-sec">Черновики учителю <span class="hdpx-count">${drafts.length}</span></p>` + drafts.map(draftCard).join(''));
    const autos = S.ov ? (S.ov.tasks || []).filter(t => t.auto && ['appointed', 'reworking'].includes(t.rawStatus)).length : null;
    parts.push(`<div class="hdpx-card"><div class="hdpx-row"><b class="hdpx-grow">⚡ Автотесты</b>${autos == null ? `<span class="hdpx-muted hdpx-small">${spin}</span>` : ''}</div>
      <p class="hdpx-small hdpx-muted">${autos ? `Новых тестов с автопроверкой: ${autos}. Бот решит и сдаст их — до 3 сразу, остальные в очереди.` : autos === 0 ? 'Новых тестов с автопроверкой нет' : 'Считаю тесты…'}</p>
      ${autos ? `<div class="hdpx-acts"><button class="hdpx-btn hdpx-primary hdpx-grow" data-a="auto" data-n="${autos}">⚡ Сдать все автотесты (${autos})</button></div>` : ''}</div>`);
    if (!st.live && !S.running.size && !drafts.length) {
      if (where().kind === 'other') parts.push('<p class="hdpx-small hdpx-muted hdpx-tc">Открой задание или тему — здесь появятся их кнопки</p>');
    }
    return parts.join('');
  }

  /* --- Ожидание --- */
  function waitTab() {
    const w = S.wait;
    if (!w) return `<div class="hdpx-card hdpx-center">${spin}</div>`;
    const out = [];
    if (w.timed.length) {
      out.push(`<p class="hdpx-h3 hdpx-sec">Отправлю учителю</p>` + w.timed.map(d => `<div class="hdpx-card"><div class="hdpx-row"><span>📬</span>
        <a class="hdpx-link hdpx-grow" href="${esc(local(d.url))}" data-a="go">${esc(d.title)}</a></div>
        <p class="hdpx-p"><b>${esc(d.when)}</b> <span class="hdpx-muted">· ${esc(soon(d.at))}</span></p>
        <div class="hdpx-acts"><button class="hdpx-btn hdpx-s hdpx-primary" data-a="send" data-m="now" data-task="${esc(d.task)}">⚡ Отправить сейчас</button>
        <button class="hdpx-btn hdpx-s hdpx-outline" data-a="hold" data-task="${esc(d.task)}">✖ Отменить</button></div></div>`).join(''));
    }
    if (w.sending.length) {
      out.push(`<p class="hdpx-h3 hdpx-sec">Отправляю сейчас</p>` + w.sending.map(d => `<div class="hdpx-card"><div class="hdpx-row">${spin}<span>${esc(d.title)}</span></div></div>`).join(''));
    }
    if (w.held.length) {
      out.push(`<p class="hdpx-h3 hdpx-sec">Ждут твоего «Отправить»</p>` + w.held.map(d => `<div class="hdpx-card"><div class="hdpx-row"><span>📄</span><b class="hdpx-grow">${esc(d.title)}</b></div>
        <div class="hdpx-acts"><button class="hdpx-btn hdpx-s hdpx-primary" data-a="send" data-m="send" data-task="${esc(d.task)}">✅ Отправить</button>
        <button class="hdpx-btn hdpx-s hdpx-outline" data-a="draft" data-task="${esc(d.task)}">✏️ Открыть</button></div></div>`).join(''));
    }
    if (w.work.length) {
      out.push(`<p class="hdpx-h3 hdpx-sec">Решаю или в очереди</p><div class="hdpx-card">` + w.work.map(d => `<div class="hdpx-li">${spin}
        ${d.url ? `<a class="hdpx-link" href="${esc(local(d.url))}" data-a="go">${esc(d.title)}</a>` : `<span>${esc(d.title)}</span>`}</div>`).join('') + '</div>');
    }
    if (!out.length) out.push('<div class="hdpx-card hdpx-center"><div class="hdpx-big">🕊️</div><p class="hdpx-p hdpx-muted">Ничего не ждёт: таймеров на отправку нет, черновиков и заданий в работе тоже</p></div>');
    out.push(`<p class="hdpx-small hdpx-muted hdpx-tc">Ответ учителю после «Отправить»: ${esc(w.delay)} · <button class="hdpx-linkbtn" data-a="tab" data-v="set">изменить</button></p>`);
    return out.join('');
  }

  /* --- Подтверждения --- */
  const EX_WORD = { reworking: 'hdpx-st-rew', failed: 'hdpx-st-rew', appointed: 'hdpx-st-apt', reviewing: 'hdpx-st-rev', approved: 'hdpx-st-ok' };
  function examItem(x) {
    const bits = [];
    if (x.pct != null) bits.push(`${x.pct}%` + (x.pass && ['reworking', 'failed'].includes(x.status) ? ` из ${x.pass}%` : ''));
    if (['appointed', 'reworking', 'failed'].includes(x.status) && /^\d{4}-\d\d-\d\d/.test(x.deadline || '')) {
      const late = x.deadline.slice(0, 10) < new Date().toISOString().slice(0, 10);
      bits.push(late ? `<span class="hdpx-late">срок был ${dm(x.deadline)}</span>` : `до ${dm(x.deadline)}`);
    }
    return `<a class="hdpx-exam" href="${esc(local(x.url))}" data-a="go"><span class="hdpx-exam-ic">${esc(x.sicon || '📘')}</span>
      <span class="hdpx-grow"><span class="hdpx-small hdpx-muted">${esc(x.subj)}</span><b>${esc(x.title)}</b>
      ${bits.length ? `<span class="hdpx-small hdpx-muted">${bits.join(' · ')}</span>` : ''}</span>
      <span class="hdpx-status ${EX_WORD[x.status] || ''}">${esc(x.word)}</span></a>`;
  }

  function examsTab() {
    const e = S.exams;
    const lv = S.examsLvl;
    const left = e ? e.left : {};
    const seg = [['2', '2.0'], ['3', '3.0'], ['all', 'Все']].map(([k, n]) => `<button class="hdpx-seg${lv === k ? ' hdpx-seg-on' : ''}" data-a="exlvl" data-v="${k}">${n}${left && left[k] ? ` <small>${left[k]}</small>` : ''}</button>`).join('');
    const head = `<div class="hdpx-row hdpx-segs">${seg}<span class="hdpx-sp"></span>
      <button class="hdpx-iconbtn${S.examsLoading || (e && e.loading) ? ' hdpx-rot' : ''}" data-a="exfresh" title="Обновить с HDP" aria-label="Обновить">${I.refresh}</button></div>`;
    if (!e) return head + `<div class="hdpx-card hdpx-center">${spin}<p class="hdpx-p hdpx-muted">Смотрю подтверждения…</p></div>`;
    const items = e.items.filter(x => lv === 'all' || String(x.lvl) === lv);
    if (!items.length) return head + `<div class="hdpx-card hdpx-center"><p class="hdpx-p hdpx-muted">${e.loading ? 'Беру свежие данные с HDP…' : 'Подтверждений и контрольных не нашёл'}</p></div>`;
    const out = [head];
    const n = f => items.filter(f).length;
    out.push(`<p class="hdpx-small hdpx-muted">Зачтено ${n(x => x.status === 'approved')} из ${items.length} · на доработке ${n(x => ['reworking', 'failed'].includes(x.status))} · не сдано ${n(x => x.status === 'appointed')} · на проверке ${n(x => x.status === 'reviewing')}</p>`);
    for (const l of [2, 3]) {
      const mine = items.filter(x => x.lvl === l);
      if (!mine.length) continue;
      if (lv === 'all') out.push(`<p class="hdpx-h3 hdpx-sec">${l}.0</p>`);
      const open = mine.filter(x => x.status !== 'approved');
      const ok = mine.filter(x => x.status === 'approved');
      if (open.length) out.push(`<div class="hdpx-card hdpx-list">${open.map(examItem).join('')}</div>`);
      if (ok.length) out.push(`<details class="hdpx-card hdpx-list hdpx-more" data-k="ok${l}"><summary>✅ Зачтено: ${ok.length}</summary>${ok.map(examItem).join('')}</details>`);
    }
    if (e.error) out.push(`<p class="hdpx-small hdpx-red">${esc(e.error)}</p>`);
    return out.join('');
  }

  /* --- Подготовка (как в bot/site/site.js) --- */
  async function prepOpen() {
    if (!S.prepSubjects) loadPrep();
    pollPrep();
  }

  async function loadPrep() {
    try {
      const r = await api('/api/prep/subjects');
      if (r.subjects.length || !r.loading) S.prepSubjects = r.subjects;
      if (r.loading) setTimeout(loadPrep, 4000);
      if (S.prepSubjects && !S.prepSubjects.find(s => s.name === S.prepSubj)) S.prepSubj = (S.prepSubjects[0] || {}).name || '';
    } catch (e) { /* покажем позже */ }
    renderPanel();
  }

  async function pollPrep() {
    clearTimeout(S.prepTimer);
    try {
      const was = S.prep;
      S.prep = await api('/api/prep/state');
      if (was && was.running && !S.prep.running) {
        if (S.prep.error) toast(S.prep.error, 'err');
        else { S.prepView = 'result'; S.prepOpen.clear(); if (!S.open || S.tab !== 'prep') toast('📖 Подготовка готова — открой панель бота'); }
      }
    } catch (e) { /* следующий опрос */ }
    renderPanel();
    if ((S.open && S.tab === 'prep') || (S.prep && S.prep.running)) S.prepTimer = setTimeout(pollPrep, S.prep && S.prep.running ? 2500 : 10000);
  }

  function prepTab() {
    const p = S.prep || {};
    if (p.running) {
      return `<div class="hdpx-card"><div class="hdpx-row">${spin}<b class="hdpx-grow">${esc(p.title || 'Готовлю')}</b><span class="hdpx-muted">${mmss(p.secs || 0)}</span></div>
        <p class="hdpx-p">${esc(p.sub || '')}</p><p class="hdpx-small hdpx-muted">Claude читает теорию темы и вопросы прошлых тестов — обычно 1–3 минуты</p></div>`;
    }
    if (S.prepView === 'result' && p.result) return prepResult(p.result);
    const subs = S.prepSubjects;
    const cur = (subs || []).find(s => s.name === S.prepSubj);
    const sel = S.prepSel || (cur ? cur.recent.slice() : []);
    return `<div class="hdpx-card">
      <p class="hdpx-label">Предмет</p>
      ${subs ? `<div class="hdpx-chips">${subs.map(s => `<button class="hdpx-chip${s.name === S.prepSubj ? ' hdpx-chip-on' : ''}" data-a="psubj" data-v="${esc(s.name)}">${esc(s.icon)} ${esc(s.short || s.name)}</button>`).join('')}</div>`
        : `<div class="hdpx-row">${spin}<span class="hdpx-muted hdpx-small">Беру темы с HDP…</span></div>`}
      ${cur ? `<p class="hdpx-label">Что прошли <span class="hdpx-muted">— отмечены недавние</span></p>
        <div class="hdpx-chips">${cur.topics.map(t => `<button class="hdpx-chip${sel.includes(t.uuid) ? ' hdpx-chip-on' : ''}" data-a="ptopic" data-v="${t.uuid}">${esc(t.title)}</button>`).join('')}</div>` : ''}
      <p class="hdpx-label">Сколько есть времени</p>
      <div class="hdpx-mins">${[[1, '⚡ 1 мин', 'главное'], [5, '📝 5 мин', 'конспект'], [10, '📚 10 мин', 'с задачами']].map(([m, t, s]) =>
        `<button class="hdpx-min${S.prepMin === m ? ' hdpx-min-on' : ''}" data-a="pmin" data-v="${m}"><b>${t}</b><small>${s}</small></button>`).join('')}</div>
      <p class="hdpx-label">Своё ТЗ <span class="hdpx-muted">— необязательно</span></p>
      <textarea class="hdpx-ta" id="hdpx-ptz" rows="3" placeholder="Например: проверочная по квадратным уравнениям">${esc(S.prepTz)}</textarea>
      <div class="hdpx-acts"><button class="hdpx-btn hdpx-primary hdpx-grow" data-a="prun">📖 Подготовить</button>
        ${p.result ? '<button class="hdpx-btn hdpx-outline" data-a="plast">Последняя</button>' : ''}</div></div>`;
  }

  function prepResult(r) {
    const SPAN = { 1: 'за 1 минуту', 5: 'за 5 минут', 10: 'за 10 минут' };
    let out = `<div class="hdpx-card"><div class="hdpx-row"><span class="hdpx-big">${esc(r.icon || '📖')}</span><div class="hdpx-grow">
      <p class="hdpx-small hdpx-muted">${esc(r.subj || 'Своё ТЗ')} · ${SPAN[r.minutes] || ''}</p><p class="hdpx-h3">${esc(r.title || r.subj || 'Подготовка')}</p>
      ${r.tz ? `<p class="hdpx-small hdpx-muted">ТЗ: ${esc(r.tz)}</p>` : ''}</div></div></div>`;
    if (r.text) out += `<div class="hdpx-card hdpx-prose">${fmt(r.text)}</div>`;
    if ((r.points || []).length) out += `<p class="hdpx-h3 hdpx-sec">Главное</p><div class="hdpx-card hdpx-prose"><ul>${r.points.map(x => `<li>${fmt(x)}</li>`).join('')}</ul></div>`;
    if ((r.sections || []).length) out += `<p class="hdpx-h3 hdpx-sec">Конспект</p>` + r.sections.map(s => `<div class="hdpx-card hdpx-prose">${s.title ? `<b>${fmt(s.title)}</b><br>` : ''}${fmt(s.text)}</div>`).join('');
    if ((r.mistakes || []).length) out += `<p class="hdpx-h3 hdpx-sec">Типовые ошибки</p><div class="hdpx-card hdpx-prose"><ul>${r.mistakes.map(x => `<li>${fmt(x)}</li>`).join('')}</ul></div>`;
    const qs = r.questions || [];
    if (qs.length) {
      out += `<p class="hdpx-h3 hdpx-sec">Проверь себя <span class="hdpx-muted hdpx-small">${S.prepOpen.size} / ${qs.length}</span></p>` + qs.map((q, i) => `<button class="hdpx-card hdpx-q${S.prepOpen.has(i) ? ' hdpx-q-open' : ''}" data-a="pq" data-i="${i}">
        <span class="hdpx-q-n">${i + 1}</span><span class="hdpx-grow"><span>${fmt(q.q)}</span>
        <span class="hdpx-q-a">${S.prepOpen.has(i) ? fmt(q.a || '—') : '<span class="hdpx-muted hdpx-small">Нажми — покажу ответ</span>'}</span></span></button>`).join('');
    }
    out += `<div class="hdpx-acts"><button class="hdpx-btn hdpx-s hdpx-outline" data-a="pmore">🔁 Ещё вопросы</button>
      ${r.minutes < 10 ? '<button class="hdpx-btn hdpx-s hdpx-outline" data-a="pdeep">📚 Подробнее</button>' : ''}
      <button class="hdpx-btn hdpx-s hdpx-ghost" data-a="pnew">Новая подготовка</button></div>`;
    return out;
  }

  async function prepRun(btn, body) {
    const r = await busy(btn, () => api('/api/prep/run', body));
    if (!r) return;
    S.prepView = 'result';
    S.prep = { ...(S.prep || {}), running: true, title: 'Готовлю', sub: '', secs: 0 };
    renderPanel();
    pollPrep();
  }

  /* --- Настройки --- */
  function setTab() {
    const s = S.settings;
    if (!s) return `<div class="hdpx-card hdpx-center">${spin}</div>`;
    return `<div class="hdpx-card"><p class="hdpx-h3">Ответ учителю после «Отправить»</p>
      <p class="hdpx-small hdpx-muted">Как будто ты сам отправил чуть позже. На каждом черновике можно выбрать иначе: ⚡ Сразу · ⏳ С задержкой · 🕐 Ко времени</p>
      <div class="hdpx-chips">${s.choices.map(c => `<button class="hdpx-chip${c.v === s.delay ? ' hdpx-chip-on' : ''}" data-a="delay" data-v="${esc(c.v)}">${esc(c.label)}</button>`).join('')}</div></div>
      <div class="hdpx-card"><label class="hdpx-row hdpx-switchrow"><span class="hdpx-grow"><b>Тесты «как человек»</b>
        <span class="hdpx-small hdpx-muted">${s.human ? 'Пауза 20–45 с над каждым вопросом' : 'Сдаю сразу, без пауз'}</span></span>
        <button class="hdpx-switch${s.human ? ' hdpx-switch-on' : ''}" role="switch" aria-checked="${s.human}" data-a="human"><i></i></button></label></div>
      <div class="hdpx-card"><label class="hdpx-row hdpx-switchrow"><span class="hdpx-grow"><b>Кнопки бота на странице</b>
        <span class="hdpx-small hdpx-muted">${S.hideTab ? 'Спрятаны — сайт ровно как платформа. Панель открывает тройной тап по странице или Alt+B' : '«⚡ Решить и сдать», «👁 Ответы»… внизу страницы и вкладка 🤖 справа. Спрячешь — панель открывает тройной тап или Alt+B'}</span></span>
        <button class="hdpx-switch${S.hideTab ? '' : ' hdpx-switch-on'}" role="switch" aria-checked="${!S.hideTab}" data-a="tabvis"><i></i></button></label></div>
      <div class="hdpx-card"><div class="hdpx-row"><span class="hdpx-grow"><b>Обычная платформа</b>
        <span class="hdpx-small hdpx-muted">Выключить все функции бота. Включить обратно — 5 быстрых тапов или Alt+B</span></span>
        <button class="hdpx-btn hdpx-s hdpx-outline" data-a="plain">⏻ Выключить всё</button></div></div>
      <div class="hdpx-card"><div class="hdpx-row"><b class="hdpx-grow">Данные бота</b><button class="hdpx-btn hdpx-s hdpx-outline" data-a="ovfresh">↻ Обновить с HDP</button></div>
        <p class="hdpx-small hdpx-muted">Задания, оценки и подтверждения бот берёт с этого сайта — обновляются сами раз в пару минут</p></div>
      <p class="hdpx-small hdpx-muted hdpx-tc">Действия идут через @${BOT} — результат приходит и сюда, и в чат · <a class="hdpx-link" href="/site/logout">выйти</a></p>`;
  }

  /* ---------- страница задания ---------- */
  // запасной путь: тип, статус и попытки — по самой странице задания
  const ST_WORDS = { 'не начато': 'appointed', 'пора начать': 'appointed', 'на доработке': 'reworking', 'не зачтено': 'failed', 'на проверке': 'reviewing', 'зачтено': 'approved' };
  function guessTask(p) {
    const main = $('main');
    if (!main) return null;
    const txt = main.textContent || '';
    const test = /Баллов нужно для зачёта|Нельзя проверить правильность ответов/.test(txt);
    const m = txt.match(/Использовано попыток:\s*(\d+)\s*из\s*(\d+)/);
    const status = ST_WORDS[(($('[data-testid="taskStatus"]') || {}).textContent || '').trim().toLowerCase()] || 'appointed';
    return { uuid: p.task, title: (($('[class*="navigationBarTaskTitle"]') || {}).textContent || '').trim(), type: test ? 'test' : 'detailedAnswer',
      status, req: true, left: m ? Math.max(0, m[2] - m[1]) : null };
  }

  function taskBarHtml(p) {
    const t = taskOf(p.topic, p.task) || (cachedTasks(p.topic) ? guessTask(p) : null);
    const st = S.st || {};
    const running = S.running.has(p.task);
    const title = (t && t.title) || (($('[class*="navigationBarTaskTitle"]') || {}).textContent || '').trim();
    const draft = (st.drafts || []).find(d => d.task === p.task);
    let hint;
    let acts = '';
    if (S.authed === false) {
      hint = loginCard(true);
      acts = `<a class="hdpx-btn hdpx-s hdpx-outline" href="https://t.me/${BOT}?start=site" target="_blank" rel="noopener">Открыть бота</a>`;
    } else if (!t) {
      hint = `<span class="hdpx-tb-hint">${spin} Смотрю задание…</span>`;
    } else if (isTest(t)) {
      const noTries = t.left === 0;
      hint = `<span class="hdpx-tb-hint">${t.status === 'approved' ? 'Тест зачтён — могу показать ответы' : noTries ? 'Попытки закончились — могу показать ответы'
        : `Тест с автопроверкой${t.left != null ? ` · осталось ${t.left} ${word(t.left, 'попытка', 'попытки', 'попыток')}` : ''}`}</span>`;
      acts = `<button class="hdpx-btn hdpx-s hdpx-outline" data-a="solve" data-m="show">👁 ${done(t) || noTries ? 'Ответы' : 'Только ответы'}</button>`
        + (done(t) || noTries ? '' : '<button class="hdpx-btn hdpx-s hdpx-primary" data-a="solve" data-m="do">⚡ Решить и сдать</button>');
    } else {
      const rew = ['reworking', 'failed'].includes(t.status);
      hint = `<span class="hdpx-tb-hint">${t.status === 'approved' ? 'Ответ зачтён' : t.status === 'reviewing' ? 'Ответ на проверке у учителя'
        : rew ? 'На доработке — доделаю по комментарию учителя, черновик покажу тебе' : 'Проверка учителем — напишу черновик, ты проверишь и отправишь'}</span>`;
      if (t.status !== 'approved') {
        acts = `<button class="hdpx-btn hdpx-s ${t.status === 'reviewing' ? 'hdpx-outline' : 'hdpx-primary'}" data-a="solve" data-m="write">${rew ? '🔁 Доделать' : t.status === 'reviewing' ? '✍️ Написать заново' : '✍️ Написать ответ'}</button>`;
      }
    }
    let live = '';
    if (running) {
      const l = st.live && (!title || st.live.sub === title) ? st.live : null;
      acts = '';
      live = `<div class="hdpx-tb-live"><div class="hdpx-row">${spin}<b class="hdpx-grow">${l ? esc(l.title) : st.busy && !st.live ? 'В очереди' : 'Решаю'}…</b>
        ${l ? `<span class="hdpx-muted hdpx-small">${l.frac != null ? Math.round(l.frac * 100) + '% · ' : ''}${mmss(l.secs || 0)}</span>` : ''}</div>
        ${l && l.step ? `<p class="hdpx-small hdpx-muted">▸ ${esc(l.step)}</p>` : '<p class="hdpx-small hdpx-muted">Результат придёт сюда и в чат</p>'}
        <div class="hdpx-bar${!l || l.frac == null ? ' hdpx-bar-ind' : ''}"><i style="width:${l ? Math.round((l.frac || 0) * 100) : 0}%"></i></div></div>`;
    }
    const dr = draft ? `<div class="hdpx-tb-draft"><span class="hdpx-grow">✍️ <b>Черновик учителю готов</b>${draft.files ? ` · картинок: ${draft.files}` : ''}${draft.sending ? ' · отправляю…' : draft.send_at ? ` · отправлю в ${hhmm(draft.send_at)}` : ''}</span>
      <button class="hdpx-btn hdpx-s hdpx-primary" data-a="draft" data-task="${esc(p.task)}">Открыть черновик</button></div>` : '';
    return `<div class="hdpx-tb-row"><span class="hdpx-ava">${I.bot}</span><div class="hdpx-tb-txt"><b>HDP-бот</b>${hint}</div>
      ${acts ? `<div class="hdpx-tb-acts">${acts}</div>` : ''}</div>${live}${dr}${running ? '' : lastCard(p.task)}`;
  }

  async function solve(btn, p, mode) {
    const t = taskOf(p.topic, p.task) || {};
    const url = taskUrl(p.topic, p.task);
    if (mode === 'do') {
      const ok = await ask({
        title: 'Решить и сдать тест?',
        text: `Бот решит «${t.title || 'тест'}» и отправит ответы на HDP — это тратит попытку${t.left != null ? ` (осталось ${t.left})` : ''}. Результат придёт сюда и в чат.`,
        ok: '⚡ Решить и сдать',
      });
      if (!ok) return false;
    }
    const r = await busy(btn, () => api('/api/solve', { url, show: mode === 'show' }));
    if (!r) return false;
    toast(mode === 'show' ? 'Смотрю ответы — покажу здесь и в чате' : mode === 'do' ? 'Решаю и сдаю — результат придёт сюда и в чат' : 'Пишу ответ — черновик придёт тебе на проверку');
    S.running.add(p.task);
    update();
    setTimeout(tick, 800);
    return true;
  }

  /* ---------- страница темы (в панели, на странице ничего не меняем) ---------- */
  function topicHtml(p) {
    topicTasks(p.topic);
    const tasks = cachedTasks(p.topic);
    if (!tasks) return `<div class="hdpx-card hdpx-center">${spin}<p class="hdpx-p hdpx-muted">Смотрю тему…</p></div>`;
    const autos = tasks.filter(autoOk).filter(t => !S.running.has(t.uuid));
    const title = (S.topics[p.topic] || {}).title || '';
    const row = t => {
      let acts;
      if (S.running.has(t.uuid)) acts = `<span class="hdpx-chip-w">${spin}решаю</span>`;
      else if (isTest(t)) acts = (done(t) || t.left === 0 ? '' : `<button class="hdpx-mb" data-a="mini" data-m="do" data-topic="${esc(p.topic)}" data-task="${esc(t.uuid)}" title="Решить и сдать">⚡</button>`)
        + `<button class="hdpx-mb" data-a="mini" data-m="show" data-topic="${esc(p.topic)}" data-task="${esc(t.uuid)}" title="Только ответы">👁</button>`;
      else acts = t.status === 'approved' ? '' : `<button class="hdpx-mb" data-a="mini" data-m="write" data-topic="${esc(p.topic)}" data-task="${esc(t.uuid)}" title="Написать ответ">✍️</button>`;
      return `<div class="hdpx-row hdpx-trow"><a class="hdpx-link hdpx-grow" data-a="go" href="/student/topic/${esc(p.topic)}/task/${esc(t.uuid)}">${esc(t.title)}</a>${acts}</div>`;
    };
    const open = tasks.filter(t => t.status !== 'approved' && t.req !== false);
    return `<div class="hdpx-card hdpx-here"><p class="hdpx-h3">📚 Эта тема${title ? ` · ${esc(title)}` : ''}</p>
      <p class="hdpx-small hdpx-muted">📖 Подготовь меня по теме</p>
      <div class="hdpx-chips">${[[1, '⚡ 1 мин'], [5, '📝 5 мин'], [10, '📚 10 мин']].map(([m, a]) => `<button class="hdpx-chip" data-a="tprep" data-m="${m}">${a}</button>`).join('')}</div>
      ${autos.length ? `<div class="hdpx-acts"><button class="hdpx-btn hdpx-primary hdpx-grow" data-a="tauto">⚡ Автотесты темы (${autos.length})</button></div>` : ''}
      ${open.length ? `<p class="hdpx-small hdpx-muted hdpx-sec">Не сдано: ${open.length}</p>${open.map(row).join('')}` : '<p class="hdpx-small hdpx-muted">Обязательное в теме сдано</p>'}
      ${tasks.length > open.length ? `<details class="hdpx-more"><summary class="hdpx-small hdpx-muted">Остальные задания (${tasks.length - open.length})</summary>${tasks.filter(t => !open.includes(t)).map(row).join('')}</details>` : ''}</div>`;
  }

  // «Эта страница» сверху вкладки «Сейчас»
  function hereCard() {
    const p = where();
    if (p.kind === 'task') return `<div class="hdpx-card hdpx-here" data-task="${esc(p.task)}">${taskBarHtml(p)}</div>`;
    if (p.kind === 'topic') return topicHtml(p);
    return '';
  }

  async function topicAuto(btn, topic) {
    const tasks = (await topicTasks(topic, true)).filter(autoOk).filter(t => !S.running.has(t.uuid));
    if (!tasks.length) { toast('Несданных тестов с автопроверкой в теме нет'); return; }
    const ok = await ask({
      title: `Сдать ${tasksWord(tasks.length)} с автопроверкой?`,
      text: tasks.map(t => '• ' + t.title).slice(0, 8).join('\n') + (tasks.length > 8 ? `\n… и ещё ${tasks.length - 8}` : '') + '\n\nБот решит и отправит их на HDP — до 3 сразу, остальные в очереди. Каждый тест тратит попытку.',
      ok: '⚡ Сдать все',
    });
    if (!ok) return;
    let started = 0;
    await busy(btn, async () => {  // ошибка на одном тесте — покажем; запущенные до неё уже идут
      for (const t of tasks) {
        await api('/api/solve', { url: taskUrl(topic, t.uuid) });
        S.running.add(t.uuid);
        started++;
      }
    });
    if (started) toast(started === tasks.length ? 'Запустил — по каждому тесту результат придёт сюда и в чат' : `Запустил ${started} из ${tasks.length}`);
    update();
    setTimeout(tick, 800);
  }

  async function topicPrep(btn, topic, minutes) {
    const r = await busy(btn, () => api('/api/site/prep', { topic, minutes }));
    S.chooser = false;
    update();
    if (!r) return;
    S.prepView = 'result';
    S.prep = { ...(S.prep || {}), running: true, title: 'Готовлю', sub: (S.topics[topic] || {}).title || '', secs: 0 };
    openPanel('prep');
  }

  /* ---------- кнопки на странице: плавающая полоса внизу ----------
     Глеб, 16.10: «кнопок по типу решить нету — это просто обычный сайт». Полоса лежит поверх страницы (в слое
     hdpx-vv) над родной нижней панелью платформы и вёрстку не сдвигает. Прячется вместе с вкладкой (Настройки). */
  function bottomInset() {
    // высота родных фиксированных панелей у нижнего края (футер теста «Баллов нужно…», «Отправить на проверку»)
    const v = window.visualViewport;
    const vw = v ? v.width : document.documentElement.clientWidth, vh = v ? v.height : window.innerHeight;
    let inset = 0;
    for (const fx of [0.15, 0.5, 0.85]) {
      // фиксированная панель — или полоса у самого низа экрана (у платформы футер — часть раскладки высотой 100vh)
      let n = document.elementFromPoint(vw * fx, vh - 3);
      while (n && n !== document.body && n !== document.documentElement) {
        if (n.closest && n.closest('[data-hdpx]')) break;
        const r = n.getBoundingClientRect();
        const pos = getComputedStyle(n).position;
        if (pos === 'fixed' || pos === 'sticky') { inset = Math.max(inset, Math.round(vh - r.top)); break; }
        if (Math.abs(r.bottom - vh) <= 2 && r.top > vh * 0.6) inset = Math.max(inset, Math.round(vh - r.top));
        n = n.parentElement;
      }
    }
    return Math.min(inset, Math.round(vh / 3));
  }

  function barHtml(p) {
    if (S.authed === false) return `<span class="hdpx-ab-txt">${I.bot} Нужен вход — напиши боту /site</span>`;
    const open = `<button class="hdpx-ab-ico" data-a="openhere" aria-label="Панель бота" title="Панель бота">${I.bot}</button>`;
    if (p.kind === 'topic') {
      const tasks = cachedTasks(p.topic);
      if (!tasks) return '';
      const autos = tasks.filter(autoOk).filter(t => !S.running.has(t.uuid)).length;
      const work = tasks.filter(t => S.running.has(t.uuid)).length;
      return open + `<button class="hdpx-ab-btn" data-a="openhere">📖 Подготовь меня</button>`
        + (autos ? `<button class="hdpx-ab-btn hdpx-ab-main" data-a="tauto">⚡ Автотесты (${autos})</button>` : '')
        + (work ? `<button class="hdpx-ab-btn" data-a="openhere">${spin} Решаю: ${work}</button>` : '');
    }
    const t = taskOf(p.topic, p.task) || (cachedTasks(p.topic) ? guessTask(p) : null);
    if (!t) return '';
    const st = S.st || {};
    if (S.running.has(p.task)) {
      const l = st.live;
      return open + `<button class="hdpx-ab-btn hdpx-ab-wide" data-a="openhere">${spin} ${l && l.step ? esc(l.step) : 'Решаю'}${l && l.frac != null ? ` · ${Math.round(l.frac * 100)}%` : ''}</button>`;
    }
    const draft = (st.drafts || []).find(d => d.task === p.task);
    if (draft) return open + `<button class="hdpx-ab-btn hdpx-ab-main" data-a="draft" data-task="${esc(p.task)}">✍️ Черновик готов${draft.send_at ? ` · ${hhmm(draft.send_at)}` : ''}</button>`;
    if (isTest(t)) {
      const over = done(t) || t.left === 0;
      return open + `<button class="hdpx-ab-btn" data-a="solve" data-m="show">👁 Ответы</button>`
        + (over ? '' : '<button class="hdpx-ab-btn hdpx-ab-main" data-a="solve" data-m="do">⚡ Решить и сдать</button>');
    }
    if (t.status === 'approved') return '';
    const rew = ['reworking', 'failed'].includes(t.status);
    return open + `<button class="hdpx-ab-btn hdpx-ab-main" data-a="solve" data-m="write">${rew ? '🔁 Доделать' : t.status === 'reviewing' ? '✍️ Написать заново' : '✍️ Написать ответ'}</button>`;
  }

  function ensureBar() {
    let bar = $('.hdpx-actionbar');
    const p = where();
    const html = S.hideTab || S.open || p.kind === 'other' ? '' : barHtml(p);
    if (!html) { if (bar) bar.remove(); return; }
    if (!bar) { bar = el('div', 'hdpx-actionbar'); bar.setAttribute('role', 'toolbar'); bar.setAttribute('aria-label', 'HDP-бот'); layer().append(bar); }
    swap(bar, html);
    const b = bottomInset() + 12 + 'px';
    if (bar.style.bottom !== b) bar.style.bottom = b;
  }

  /* ---------- всё вместе ---------- */
  let pathNow = '';
  function update() {
    if (!document.body) return;
    if (location.pathname !== pathNow) {  // сменилась страница SPA
      pathNow = location.pathname;
      S.routeAt = Date.now();
      S.chooser = false;
      setTimeout(update, 2600);  // если якоря так и нет — плавающий блок
    }
    ensureTab();
    ensureBar();
    const p = where();
    const key = location.pathname;
    if (S.open && key !== S.ctxKey) { S.ctxKey = key; if (p.kind !== 'other') S.tab = 'now'; }
    if (p.kind !== 'other') topicTasks(p.topic);  // данные для «Эта страница» — заранее, чтобы панель открылась сразу
    if (S.open) {
      const ds = (S.st && S.st.drafts) || [];
      const keys = JSON.stringify(ds.map(d => [d.task, d.text, d.files, d.send_at, d.sending]));
      if (keys !== S.lastDraftKeys) {  // черновик ушёл или удалён — его несохранённая правка больше не нужна
        S.lastDraftKeys = keys;
        for (const k of Object.keys(S.edits)) if (!ds.find(d => d.task === k)) delete S.edits[k];
      }
      renderPanel();
    }
  }

  let queued = 0;
  function schedule() {
    if (queued) return;
    queued = setTimeout(() => { queued = 0; update(); }, 150);
  }

  for (const k of ['pushState', 'replaceState']) {
    const orig = history[k];
    history[k] = function () { const r = orig.apply(this, arguments); schedule(); return r; };
  }
  window.addEventListener('popstate', schedule);

  /* ---------- клики ---------- */
  document.addEventListener('click', async e => {
    const a = e.target.closest('[data-a]');
    if (!a || !a.closest('[data-hdpx]')) {
      if (S.chooser && !e.target.closest('.hdpx-pop-wrap')) { S.chooser = false; update(); }
      if (S.open && !narrow() && !e.target.closest('[data-hdpx]') && e.isTrusted) closePanel();  // клик мимо панели
      return;
    }
    const act = a.dataset.a;
    const d = a.dataset;
    if (act === 'go') {
      if (e.ctrlKey || e.metaKey || e.shiftKey || e.button) return;  // новая вкладка — как обычная ссылка
      e.preventDefault();
      if (narrow()) closePanel();
      go(a.getAttribute('href'));
      return;
    }
    e.preventDefault();
    e.stopPropagation();  // кнопка внутри карточки-ссылки платформы: не переходить по ссылке
    const p = where();
    const findDraft = () => ((S.st && S.st.drafts) || []).find(x => x.task === d.task);
    const text = task => { const ta = $(`textarea[data-draft="${task}"]`); return ta ? ta.value : (findDraft() || {}).text || ''; };
    const saveFirst = async (btn, task) => {  // правка не сохранилась — не отправляем старый текст
      const dr = findDraft();
      const now = text(task);
      if (!dr || now === dr.text) return true;
      const r = await busy(btn, () => api('/api/draft/save', { task, text: now }));
      if (r) { S.st = r; delete S.edits[task]; }
      return !!r;
    };
    switch (act) {
      case 'close': closePanel(); break;
      case 'plain':  // Глеб 19.10: «одной кнопкой выключить все доп. функции — просто обычная платформа»
        if (await ask({ title: 'Выключить всё?', text: 'Будет обычная платформа, без кнопок бота. Включить обратно — 5 быстрых тапов по странице или Alt+B.', ok: '⏻ Выключить' }))
          location.href = '/_hdp/off?to=' + encodeURIComponent(location.pathname + location.search);
        break;
      case 'tab':
        S.tab = d.v; store.set('tab', d.v);
        if (d.v === 'exams' && !S.exams) loadExams(false);
        if (d.v === 'prep') prepOpen();
        if (d.v === 'set') loadSettings();
        if (d.v === 'now' && !S.ov) loadOv();
        renderPanel(true);
        break;
      case 'solve': solve(a, p, d.m); break;
      case 'mini': solve(a, { topic: d.topic, task: d.task }, d.m); break;
      case 'draft': openPanel('now', { draft: d.task }); break;
      case 'openhere': openPanel('now'); break;
      case 'tabvis':
        S.hideTab = !S.hideTab; store.set('hideTab', S.hideTab);
        toast(S.hideTab ? 'Кнопки спрятаны — панель: тройной тап по странице или Alt+B' : 'Кнопки снова на странице');
        renderPanel(true); ensureTab();
        break;
      case 'seen': S.seen = (S.st && S.st.last && S.st.last.at) || S.seen; store.set('seen', S.seen); update(); break;
      case 'save': {
        const ok = await saveFirst(a, d.task);
        if (ok) { toast('Сохранил — бот запомнит правку'); update(); }
        break;
      }
      case 'send': {
        const dr = findDraft() || {};
        if (d.m === 'now' && !(await ask({ title: 'Отправить учителю сейчас?', text: `«${dr.title || 'Ответ'}» уйдёт на HDP сразу.`, ok: '⚡ Отправить' }))) return;
        if (d.m === 'send' && !(await ask({ title: 'Отправить ответ учителю?', text: S.settings && S.settings.delay !== '0' ? `Уйдёт через ${S.settings.label}, как будто ты сам отправил. Отменить можно до отправки.` : 'Ответ уйдёт на HDP сразу.', ok: '✅ Отправить' }))) return;
        if (!(await saveFirst(a, d.task))) return;
        const r = await busy(a, () => api('/api/site/send', { task: d.task, mode: d.m }));
        if (r) {
          S.st = r;
          toast(d.m === 'now' ? 'Отправляю учителю…' : d.m === 'later' ? 'Отправлю с задержкой — время видно на черновике' : 'Готово — время отправки видно на черновике');
          setTimeout(tick, 1200);
          loadWait(true);
        }
        break;
      }
      case 'times':
        S.timeOpen = S.timeOpen === d.task ? '' : d.task;
        renderPanel(true);
        if (S.timeOpen) {
          try { S.times[d.task] = (await api('/api/site/times')).times; } catch (x) { toast(x.message, 'err'); }
          renderPanel(true);
        }
        break;
      case 'at': {
        if (!(await saveFirst(a, d.task))) return;
        const r = await busy(a, () => api('/api/site/send', { task: d.task, mode: 'at', at: Number(d.at) }));
        if (r) { S.st = r; S.timeOpen = ''; toast(`Отправлю в ${hhmm(Number(d.at))}`); setTimeout(tick, 800); loadWait(true); }
        break;
      }
      case 'hold': {
        const r = await busy(a, () => api('/api/site/hold', { task: d.task }));
        if (r) { S.st = r; toast('Не отправляю — черновик остался'); loadWait(true); update(); }
        break;
      }
      case 'drop': {
        const dr = findDraft() || {};
        if (!(await ask({ title: 'Удалить черновик?', text: `«${dr.title || 'Черновик'}» не уйдёт учителю. Передумаешь — нажми «Написать ответ» на задании ещё раз.`, ok: 'Удалить', danger: true }))) return;
        const r = await busy(a, () => api('/api/draft/drop', { task: d.task }));
        if (r) { S.st = r; delete S.edits[d.task]; toast('Черновик удалён'); loadWait(true); update(); }
        break;
      }
      case 'files': {
        const r = await busy(a, () => api('/api/site/draft/files', { task: d.task }));
        if (r) { S.files[d.task] = r.files; renderPanel(true); }
        break;
      }
      case 'img': {
        const f = (S.files[d.task] || [])[Number(d.i)];
        if (f && f.data) lightbox(f.data, f.name);
        break;
      }
      case 'auto': {
        if (!(await ask({ title: `Сдать все автотесты (${d.n})?`, text: 'Бот решит и отправит на HDP новые тесты с автопроверкой — до 3 сразу, остальные в очереди. Каждый тест тратит попытку.', ok: '⚡ Сдать все' }))) return;
        const r = await busy(a, () => api('/api/auto', {}));
        if (r) { toast(`Запустил: ${tasksWord(r.n)} — результат придёт сюда и в чат`); setTimeout(tick, 800); }
        break;
      }
      case 'exlvl': S.examsLvl = d.v; store.set('examsLvl', d.v); renderPanel(true); break;
      case 'exfresh': loadExams(true); break;
      case 'delay': {
        const r = await busy(a, () => api('/api/site/settings/set', { delay: d.v }));
        if (r) { S.settings = r; toast(`Ответ учителю: ${r.label === 'сразу' ? 'сразу' : 'через ' + r.label}`); renderPanel(true); }
        break;
      }
      case 'human': {
        const on = !(S.settings && S.settings.human);
        const r = await busy(a, () => api('/api/human', { on }));
        if (r) { S.settings = { ...(S.settings || {}), human: r.human }; renderPanel(true); }
        break;
      }
      case 'ovfresh': {
        const r = await busy(a, () => api('/api/overview', { fresh: true }));
        if (r) { toast('Обновляю данные с HDP — около минуты'); S.exams = null; S.ov = null; }
        break;
      }
      case 'chooser': S.chooser = !S.chooser; update(); break;
      case 'tprep': topicPrep(a, p.topic, Number(d.m)); break;
      case 'tauto': topicAuto(a, p.topic); break;
      case 'psubj': S.prepSubj = d.v; S.prepSel = null; S.prepTz = ($('#hdpx-ptz') || {}).value || S.prepTz; renderPanel(true); break;
      case 'ptopic': {
        const cur = (S.prepSubjects || []).find(s => s.name === S.prepSubj);
        const sel = S.prepSel || (cur ? cur.recent.slice() : []);
        S.prepSel = sel.includes(d.v) ? sel.filter(x => x !== d.v) : [...sel, d.v];
        S.prepTz = ($('#hdpx-ptz') || {}).value || S.prepTz;
        renderPanel(true);
        break;
      }
      case 'pmin': S.prepMin = Number(d.v); store.set('prepMin', S.prepMin); S.prepTz = ($('#hdpx-ptz') || {}).value || S.prepTz; renderPanel(true); break;
      case 'prun': {
        S.prepTz = ($('#hdpx-ptz') || {}).value || '';
        const cur = (S.prepSubjects || []).find(s => s.name === S.prepSubj);
        prepRun(a, { subj: S.prepSubj, topics: S.prepSel || (cur ? cur.recent : []), minutes: S.prepMin, tz: S.prepTz });
        break;
      }
      case 'pmore': prepRun(a, { more: true, minutes: ((S.prep || {}).result || {}).minutes || 5 }); break;
      case 'pdeep': prepRun(a, { again: true, minutes: 10 }); break;
      case 'pnew': S.prepView = 'form'; renderPanel(true); break;
      case 'plast': S.prepView = 'result'; renderPanel(true); break;
      case 'pq': { const i = Number(d.i); S.prepOpen.has(i) ? S.prepOpen.delete(i) : S.prepOpen.add(i); renderPanel(true); break; }
      default: break;
    }
  }, true);

  // правка черновика и «своё время» текстом
  document.addEventListener('input', e => {
    const ta = e.target.closest && e.target.closest('textarea[data-draft]');
    if (!ta) return;
    S.edits[ta.dataset.draft] = ta.value;
    const card = ta.closest('.hdpx-draft');
    const dr = ((S.st && S.st.drafts) || []).find(x => x.task === ta.dataset.draft);
    if (card && dr && !$('[data-a="save"]', card) && ta.value !== dr.text) {
      const row = el('div', 'hdpx-acts', `<button class="hdpx-btn hdpx-s hdpx-outline" data-a="save" data-task="${esc(dr.task)}">💾 Сохранить правку</button>`);
      ta.insertAdjacentElement('afterend', row);
    }
  });
  document.addEventListener('submit', async e => {
    const f = e.target.closest && e.target.closest('.hdpx-timeform');
    if (!f) return;
    e.preventDefault();
    const btn = $('button', f);
    const task = f.dataset.task;
    const r = await busy(btn, () => api('/api/site/send', { task, mode: 'at', text: f.t.value }));
    if (r) { S.st = r; S.timeOpen = ''; toast('Время отправки — на черновике'); setTimeout(tick, 800); loadWait(true); }
  }, true);
  // кнопка спрятана — панель открывают тройной тап (телефон) / тройной клик по странице или Alt+B
  let taps = [];
  document.addEventListener('pointerup', e => {
    if (!S.hideTab || S.open || (e.target.closest && e.target.closest('[data-hdpx], a, button, input, textarea, select, label'))) return;
    const now = Date.now();
    taps = taps.filter(t => now - t < 700).concat(now);
    if (taps.length >= 3) { taps = []; openPanel(); }
  }, { passive: true });
  document.addEventListener('keydown', e => {
    if (e.altKey && (e.code === 'KeyB' || e.key === 'b' || e.key === 'и')) { e.preventDefault(); S.open ? closePanel() : openPanel(); return; }
    if (e.key !== 'Escape') return;
    if (S.chooser) { S.chooser = false; update(); } else if (S.open && !$('.hdpx-modal')) closePanel();
  });

  /* ---------- старт ---------- */
  function boot() {
    new MutationObserver(recs => {
      // свои изменения не считаем, иначе блоки перерисовывались бы по кругу
      if (recs.every(r => { const n = r.target.nodeType === 1 ? r.target : r.target.parentElement; return n && n.closest && n.closest('[data-hdpx]'); })) return;
      schedule();
    }).observe(document.body, { childList: true, subtree: true });
    update();
    tick();
    pollPrep();
  }
  if (document.body) boot(); else document.addEventListener('DOMContentLoaded', boot);
})();
