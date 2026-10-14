/* Главный — одно мини-приложение на двоих: Секретарь (вопросы, поручения, ваш Telegram), Боты (сервер) и Дела
   (расписание, напоминания, память). Без сборки, общий вид — common/webapp/kit.*.
   Данные: /app/api/* — Главный (hub.py), /app/agent/api/* — Секретарь (tgbot/web.py через прокси Главного).
   Поручения идут тем же путём, что сообщения в чате: статус и ответ — и здесь, и в чате; «Да/Нет» — в чате. */
(() => {
  'use strict';
  const { tg, $, esc, toast, haptic, sheet, busy } = Kit;
  const view = $('view');
  const S = {
    tab: 'sec', hub: null, sec: null, secErr: null, gate: null, draft: '', open: {},
    updating: null, logs: null, memory: null, queued: null,
    pending: [],      // вопросы, ушедшие на сервер, но ещё без ответа сервера (пузырь в ленте сразу)
    older: false,     // развёрнут ли блок «Раньше»
    painted: {}, feedSig: null,
  };
  const QUEUE_MAX = 5;  // как в tgbot/agent.py

  // ---------- мелочи ----------
  const NB = '\u00a0';
  const plural = (n, a, b, c) => { const m = Math.abs(n) % 100, k = m % 10; return m > 10 && m < 20 ? c : k > 1 && k < 5 ? b : k === 1 ? a : c; };
  const num = (n, a, b, c) => `${n}${NB}${plural(n, a, b, c)}`;
  const fmt = (x, d = 1) => Number(x).toFixed(d).replace(/\.0+$/, '').replace('.', ',');
  const mem = (mb) => (mb >= 1024 ? `${fmt(mb / 1024)}${NB}ГБ` : `${Math.round(mb)}${NB}МБ`);
  const ago = (s) => { s = Math.max(0, s | 0); if (s < 3600) return `${Math.max(1, (s / 60) | 0)}${NB}мин`; if (s < 86400) return `${(s / 3600) | 0}${NB}ч`; return num((s / 86400) | 0, 'день', 'дня', 'дней'); };
  const mmss = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  const emoji = (b) => (b.label.slice(0, Math.max(0, b.label.length - b.title.length)).trim() || '🤖');
  const confirmBox = (text) => new Promise((res) => {
    if (tg && tg.showConfirm && tg.platform !== 'unknown') { try { return tg.showConfirm(text, (ok) => res(!!ok)); } catch (e) { /* старый клиент */ } }
    res(window.confirm(text));
  });
  const toChat = () => { haptic('tap'); try { tg.close(); } catch (e) { toast('Откройте чат с Главным'); } };

  async function api(path, body) { return Kit.api('/app/api/' + path, body); }
  async function sec(path, body) { return Kit.api('/app/agent/api/' + path, body || {}); }

  // ---------- ответ Секретаря: HTML из чата -> безопасный DOM (только разрешённые теги) ----------
  const ALLOWED = { B: 'b', STRONG: 'b', I: 'i', EM: 'i', U: 'u', S: 's', DEL: 'del', CODE: 'code', PRE: 'pre',
    BLOCKQUOTE: 'blockquote', A: 'a', 'TG-SPOILER': 'span', BR: 'br' };
  function safeDom(html) {
    const doc = new DOMParser().parseFromString(`<div>${html || ''}</div>`, 'text/html');
    const out = document.createDocumentFragment();
    const walk = (src, dst) => {
      for (const n of src.childNodes) {
        if (n.nodeType === 3) { dst.appendChild(document.createTextNode(n.data)); continue; }
        if (n.nodeType !== 1) continue;
        const t = ALLOWED[n.tagName];
        if (!t) { walk(n, dst); continue; }
        const e = document.createElement(t);
        if (t === 'a') {
          const href = n.getAttribute('href') || '';
          if (/^https?:\/\//i.test(href)) {
            e.href = href;
            e.addEventListener('click', (ev) => {
              ev.preventDefault();
              if (/^https:\/\/t\.me\//i.test(href) && tg) tg.openTelegramLink(href);
              else if (tg) tg.openLink(href); else window.open(href, '_blank', 'noopener');
            });
          }
        }
        if (n.tagName === 'TG-SPOILER') { e.className = 'spoiler'; e.addEventListener('click', () => e.classList.add('open')); }
        walk(n, e);
        dst.appendChild(e);
      }
    };
    walk(doc.body.firstChild, out);
    return out;
  }
  // ответы вставляем после отрисовки: <div class="answer" data-ans="ключ">
  const answers = {};
  function answer(key, html, clip) {
    answers[key] = html;
    const long = clip && ((html || '').length > 380 || (html || '').split('\n').length > 7) && !S.open[key];
    return `<div class="answer ${long ? 'clip' : ''}" data-ans="${esc(key)}"></div>` +
      (long ? `<button class="more-btn" data-expand="${esc(key)}">Показать полностью</button>` : '');
  }
  function fillAnswers(root) {
    root.querySelectorAll('[data-ans]').forEach((el) => { el.replaceChildren(safeDom(answers[el.dataset.ans])); });
  }

  // ---------- вкладки ----------
  const TABS = [{ id: 'sec', icon: '✦', label: 'Секретарь' }, { id: 'bots', icon: '🤖', label: 'Боты' },
    { id: 'more', icon: '🗂', label: 'Дела' }];
  let bar;
  function go(tab) {
    S.tab = tab;
    try { sessionStorage.setItem('tab', tab); } catch (e) { /* приватный режим */ }
    window.scrollTo(0, 0);
    render(true);
    poll();
  }

  // ---------- отрисовка ----------
  function render(anim) {
    if (S.gate) { syncDock(); return gate(); }
    const html = S.tab === 'bots' ? botsView() : S.tab === 'more' ? moreView() : secView();
    view.innerHTML = html;
    view.classList.toggle('enter', !!anim);
    fillAnswers(view);
    S.painted = {};
    syncDock();
    requestAnimationFrame(() => view.querySelectorAll('.bar > i[data-w]').forEach((b) => { b.style.width = b.dataset.w; }));
  }

  // часть экрана Секретаря без перерисовки (поле ввода живёт в #dock и не пересоздаётся); одинаковый HTML не трогаем
  function paintLive() {
    const box = $('convo');
    if (S.tab !== 'sec' || !box || (box.dataset.down === '1') !== !!S.secErr) return render();  // Секретарь пропал или вернулся
    for (const [id, fn] of [['sechero', secHero], ['downbox', downBox], ['convo', convoBlock], ['psybox', psyCard], ['acc', accountCard], ['chats', chatsBlock]]) {
      const el = $(id);
      if (!el) continue;
      const html = fn();
      if (S.painted[id] === html) continue;
      S.painted[id] = html;
      el.innerHTML = html;
      fillAnswers(el);
    }
    syncSend();
    followFeed();
  }

  function gate() {
    const outside = !(tg && tg.initData);
    bar && document.getElementById('tabs').setAttribute('hidden', '');
    view.innerHTML = `<div class="empty gate"><span class="e">${outside ? '📱' : '🔒'}</span>
      <b>${outside ? 'Откройте в Telegram' : 'Нет доступа'}</b>
      ${outside ? 'Приложение работает внутри бота «Главный» — кнопка «Приложение» слева от поля ввода.' : esc(S.gate)}</div>`;
  }

  function hero(logo, title, sub, end) {
    return `<header class="hero"><div class="logo">${logo}</div><div class="titles"><h1>${esc(title)}</h1>
      <div class="sub">${esc(sub)}</div></div>${end ? `<div class="end">${end}</div>` : ''}</header>`;
  }

  // ---------- Секретарь ----------
  const isWork = () => !!(!S.secErr && S.sec && S.sec.run && S.sec.run.state === 'work');
  const queueOf = () => (S.sec && S.sec.queue) || [];
  const isBusy = () => isWork() || queueOf().length > 0;
  const queueFull = () => queueOf().length >= QUEUE_MAX;

  function secHero() {
    const st = S.sec;
    const hello = st ? (st.first ? `${st.hello}, ${st.first}` : st.hello) : 'Секретарь';
    const sub = S.secErr ? 'Секретарь не отвечает' : st && st.me ? `${st.me.name}${st.me.username ? ' · @' + st.me.username : ''}`
      : 'Поручите что угодно — найду, отвечу, сделаю';
    return hero('👑', hello, sub, `<button class="btn icon" data-act="refresh" aria-label="Обновить">↻</button>`);
  }

  // Подсказки-примеры: «заготовки» (data-fill) вставляются в поле и ждут дописки, пресеты (data-preset) уходят сразу
  const FILLS = [['✈️ Найти билеты', 'Найди билеты в Китай на '], ['🔎 Найти в интернете', 'Найди в интернете: ']];

  // Секретарь не отвечает (или поручение ждёт, пока он поднимется) — поле поручения при этом остаётся
  function downBox() {
    if (S.queued) {
      return `<section class="card down-note"><div class="row-flat"><span class="ava-ic">⏳</span><div class="main">
        <b>Жду Секретаря — отправлю сам</b><div class="hint clip-2">${esc(S.queued.text || '')}</div>
        <div class="hint">Не закрывайте приложение: как только он поднимется, поручение уйдёт.</div></div></div>
        <button class="btn ghost sm" data-act="unqueue">Отменить</button></section>`;
    }
    if (!S.secErr) return '';
    return `<section class="card down-note"><div class="row-flat"><span class="ava-ic">😴</span><div class="main">
      <b>Секретарь не отвечает</b><div class="hint">Всё равно пишите: отправка сама его поднимет и повторится.${S.secErr === 'down' ? '' : ' ' + esc(S.secErr)}</div></div></div>
      <button class="btn primary sm" data-act="revive">Поднять Секретаря</button></section>`;
  }

  function secView() {
    const down = !!S.secErr;
    let out = `<div id="sechero">${secHero()}</div>
      <div id="downbox">${downBox()}</div>
      <div id="convo" data-down="${down ? 1 : 0}">${convoBlock()}</div>`;
    if (!down) {
      out += `<div id="psybox">${psyCard()}</div>
      <div id="acc">${accountCard()}</div>
      <div id="chats">${chatsBlock()}</div>`;
    } else {
      out += `<p class="hint center">Боты и дела работают — они на соседних вкладках.</p>`;
    }
    return out + `<div class="list mode-list"><button class="row tap" data-act="tochat"><span class="ic">💬</span><div class="main">
        <div class="title">Перейти в чат</div><div class="meta">Всё кнопками в чате; вернуть приложение — /mode</div></div>
        <span class="end chev"></span></button></div>`;
  }

  // 🧠 Психолог: все чаты за неделю или месяц (ответ — как обычное поручение, и в чате с кнопками «Подробнее про …»)
  function psyCard() {
    const st = S.sec;
    if (!st) return '';
    const dis = queueFull() ? 'disabled' : '';
    const body = st.me
      ? `<div class="btns psy-btns"><button class="btn tonal sm" data-preset="psy_week" ${dis}>Все чаты · неделя</button>
          <button class="btn tonal sm" data-preset="psy_month" ${dis}>Все чаты · месяц</button>
          <button class="btn tonal sm" data-act="psy" ${dis}>Выбрать чат</button></div>`
      : `<div class="note">Разбираю переписки из вашего Telegram — для этого нужен вход в аккаунт.</div>
         <button class="btn tonal sm" data-act="login">Войти в аккаунт</button>`;
    return `<section class="card psy"><div class="row-flat"><span class="ava-ic">🧠</span><div class="main"><b>Психолог</b>
      <div class="hint">Отношения и роли, тон, скрытые конфликты, на кого уходят силы — и советы</div></div></div>${body}</section>`;
  }

  function accountCard() {
    const st = S.sec;
    if (!st) return `<div class="card"><div class="sk" style="height:18px;width:50%"></div><div class="sk" style="height:42px;margin-top:12px"></div></div>`;
    if (!st.me) {
      return `<section class="card"><div class="row-flat"><span class="ava-ic">👤</span><div class="main"><b>Аккаунт не подключён</b>
        <div class="hint">На вопросы отвечаю и без него. Подключите — буду вести ваш Telegram: сводки, ответы, папки, расписания.</div></div></div>
        <button class="btn tonal" data-act="login">Войти в аккаунт</button></section>`;
    }
    return `<div class="stats acc-stats">
      <div class="stat"><div class="v">${st.unread}</div><div class="k">${plural(st.unread, 'новый чат', 'новых чата', 'новых чатов')}</div></div>
      <div class="stat"><div class="v">${st.mentions}</div><div class="k">${plural(st.mentions, 'упоминание', 'упоминания', 'упоминаний')}</div></div>
      <div class="stat"><div class="v">${st.jobs.length}</div><div class="k">${plural(st.jobs.length, 'расписание', 'расписания', 'расписаний')}</div></div></div>`;
  }

  // пастельные аватарки: [фон, буквы] — как у Секретаря (tgbot/webapp/app.js)
  const AVAS = [['#f6e3c4', '#8a6324'], ['#e4ddf3', '#5f4f97'], ['#f4dbe0', '#97475a'], ['#d8ebe3', '#3f7a64'],
    ['#dbe5f2', '#3f6491'], ['#f5dfcf', '#985b36'], ['#e3e9d3', '#5d6f35']];
  const grad = (id) => { const g = AVAS[Math.abs(Number(id) || 0) % AVAS.length]; return `background:linear-gradient(160deg,#fff -40%,${g[0]});color:${g[1]}`; };

  function chatsBlock() {
    const st = S.sec;
    if (!st || !st.me) return '';
    let out = `<div class="label">Непрочитанное${st.chats.length ? `<span class="more tnum">${st.unread}</span>` : ''}</div>`;
    if (st.error) out += `<div class="note err">${esc(st.error)}</div>`;
    if (!st.chats.length) return out + (st.error ? '' : `<div class="card"><div class="empty"><span class="e">✨</span><b>Всё прочитано</b>Новых сообщений нет.</div></div>`);
    const limit = S.open.allChats ? st.chats.length : 6;
    out += `<div class="list">${st.chats.slice(0, limit).map((c, i) => `<button class="row" data-chat="${i}">
        <span class="ava" style="${grad(c.id)}">${esc(c.initials)}</span>
        <div class="main"><div class="title">${esc(c.name)}</div><div class="meta clip">${esc(c.preview || c.kind)}</div></div>
        <div class="end col"><span class="tnum">${esc(c.when)}</span><span class="badges">${c.mentions ? '<i class="badge at">@</i>' : ''}${c.unread
          ? `<i class="badge ${c.kind === 'канал' ? 'mute' : ''}">${c.unread > 999 ? '999+' : c.unread}</i>` : ''}</span></div></button>`).join('')}
      ${st.chats.length > limit ? `<button class="row more-row" data-act="allchats">Показать ещё ${st.chats.length - limit}</button>` : ''}</div>`;
    return out;
  }

  // ---------- разговор: одна лента, как в мессенджере ----------
  const sameConv = (it) => (it.conv || '') === ((S.sec && S.sec.conv) || '');
  // вопросы и ответы текущего разговора по порядку (старые сверху)
  function convItems() {
    const st = S.sec;
    if (!st) return [];
    const items = (st.history || []).filter(sameConv).slice().reverse();
    const r = st.run;
    if (r && r.state !== 'work' && sameConv(r) && !items.some((it) => it.id === r.id)) items.push(r);  // ответ пришёл раньше истории
    return items;
  }
  const PRESET_LABEL = { missed: 'Что я пропустил', sort: 'Навести порядок', psy_week: 'Разбор чатов за неделю',
    psy_month: 'Разбор чатов за месяц', psy: 'Разбор чата', brief: 'Что пишут' };

  const bubbleMe = (q, meta, cls) => `<div class="msg me ${cls || ''}"><div class="txt">${esc(q)}</div>${meta ? `<div class="msg-meta">${esc(meta)}</div>` : ''}</div>`;
  function bubbleAi(it, last) {
    const bits = [it.at, it.secs != null ? mmss(it.secs) : ''].filter(Boolean).join(' · ');
    return `<div class="msg ai">${it.state && it.state !== 'done' ? `<span class="pill err">${it.state === 'stopped' ? 'остановлено' : 'ошибка'}</span> ` : ''}${
      answer('h-' + it.id, it.html, !last)}${bits ? `<div class="msg-meta tnum">${esc(bits)}</div>` : ''}</div>`;
  }
  function bubbleWork(r) {
    const steps = r.steps || [];
    const shown = steps.slice(-4);
    const meta = mmss(r.secs || 0) + (steps.length ? ` · ${num(steps.length, 'шаг', 'шага', 'шагов')}` : '');
    return `<div class="msg ai work"><div class="run-head"><span class="pill dot accent live">Работаю</span><span class="hint tnum">${meta}</span></div>
      <ul class="steps">${(shown.length ? shown : ['Думаю']).map((x, i, a) => `<li class="${i === a.length - 1 ? 'now' : ''}">${esc(x)}</li>`).join('')}</ul>
      ${r.confirm ? `<div class="note warn-note"><div><b>Ждёт «Да» в чате</b><div class="hint">${esc(r.confirm.replace(/\n{2,}/g, '\n'))}</div>
        <button class="btn tonal sm" data-act="chat">Перейти в чат</button></div></div>` : ''}
      <div class="btns"><button class="btn danger sm" data-act="stop">Остановить${queueOf().length ? ' всё' : ''}</button></div></div>`;
  }

  function convoBlock() {
    const st = S.sec;
    if (!st) return `<div class="card"><div class="sk" style="height:18px;width:50%"></div><div class="sk" style="height:42px;margin-top:12px"></div></div>`;
    const items = convItems();
    const run = isWork() ? st.run : null;
    const queue = queueOf();
    const pend = S.pending;
    let out = `<div class="label">Разговор<button class="more" data-act="new">Новый разговор</button></div>`;
    if (!items.length && !run && !queue.length && !pend.length) {
      out += `<div class="card"><div class="empty"><span class="e">✦</span><b>Поручите что угодно</b>Найду, отвечу, сделаю. Уточнять можно, пока я думаю — вопросы встанут в очередь.</div></div>`;
    } else {
      out += `<div class="convo">`;
      items.forEach((it, i) => { out += bubbleMe(it.q, it.source === 'app' ? '' : 'из чата') + bubbleAi(it, i === items.length - 1 && !run); });
      if (run) out += bubbleMe(run.q) + bubbleWork(run);
      queue.forEach((x, i) => { out += bubbleMe(x.q, `в очереди №${i + 1}`, 'wait'); });
      pend.forEach((x) => { out += bubbleMe(x.q, 'отправляю…', 'wait'); });
      out += `</div>`;
    }
    const old = (st.history || []).filter((it) => !sameConv(it)).slice(0, 8);
    if (old.length) {
      out += `<button class="more-btn older" data-act="older">${S.older ? 'Скрыть прежние разговоры' : `Раньше · ${old.length}`}</button>`;
      if (S.older) {
        out += old.map((it) => `<article class="card hist">
          <div class="hist-head"><span class="src">${it.source === 'app' ? '📱' : '💬'}</span><div class="main">
            <div class="q">${esc(it.q)}${it.state !== 'done' ? ` <span class="pill err">${it.state === 'stopped' ? 'стоп' : 'ошибка'}</span>` : ''}</div>
            <div class="hint tnum">${esc([it.at, it.secs != null ? mmss(it.secs) : ''].filter(Boolean).join(' · '))}</div></div></div>
          ${answer('o-' + it.id, it.html, true)}</article>`).join('');
      }
    }
    return out;
  }

  // автопрокрутка к новому пузырю (только когда в ленте что-то изменилось, не на каждый опрос)
  function followFeed(force) {
    const st = S.sec;
    if (!st || S.tab !== 'sec') return;
    const r = st.run;
    const sig = [convItems().length, r ? r.id + r.state : '', queueOf().length, S.pending.length].join('|');
    const first = S.feedSig === null;
    const changed = sig !== S.feedSig;
    S.feedSig = sig;
    if (!force && (first || !changed)) return;
    requestAnimationFrame(() => {
      const bubbles = view.querySelectorAll('.convo > .msg');
      const last = bubbles[bubbles.length - 1];
      if (last) last.scrollIntoView({ block: 'end', behavior: 'smooth' });
    });
  }

  // ---------- поле поручения: закреплено внизу (#dock), всегда активно — и пока Секретарь думает ----------
  const FILL_HTML = FILLS.map(([t], i) => `<button class="chip" data-fill="${i}">${t}</button>`).join('');
  const dockEl = $('dock');
  function buildDock() {
    if (!dockEl || dockEl.dataset.ready) return;
    dockEl.dataset.ready = '1';
    dockEl.innerHTML = `<div class="dock-in">
      <div class="chips quick">${FILL_HTML}
        <button class="chip" data-preset="missed">📬 Что я пропустил</button>
        <button class="chip" data-preset="sort">🗂 Навести порядок</button>
        <button class="chip" data-act="psy">🧠 Разбор чата</button></div>
      <div class="dock-bar"><textarea id="ask" rows="1" maxlength="4000" aria-label="Поручение"
        placeholder="Поручение или уточнение…"></textarea>
        <button class="btn primary icon send" data-act="send" aria-label="Отправить">↑</button></div>
      <div class="hint dock-hint" id="dockhint"></div></div>`;
    const ta = $('ask');
    const size = () => { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, innerHeight * 0.3) + 'px'; padDock(); };
    ta.addEventListener('input', () => { S.draft = ta.value; size(); syncSend(); });
    ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) send(); });
    ta.value = S.draft;
    ta.sizeNow = size;
    dockEl.addEventListener('click', onClick);
  }
  // место под закреплённое поле: низ страницы не прячется за ним
  function padDock() {
    document.body.style.setProperty('--dock-h', (dockEl && !dockEl.hidden ? dockEl.offsetHeight : 0) + 'px');
  }
  function syncDock() {
    if (!dockEl) return;
    const on = S.tab === 'sec' && !S.gate;
    if (on) buildDock();
    dockEl.hidden = !on;
    document.body.classList.toggle('has-dock', on);
    padDock();
    if (on) syncSend();
  }
  function syncSend() {
    const s = dockEl && dockEl.querySelector('[data-act="send"]');
    if (!s) return;
    const full = queueFull();
    s.disabled = !S.draft.trim() || full;
    dockEl.querySelectorAll('[data-preset], [data-act="psy"]').forEach((b) => { b.disabled = full; });
    const hint = $('dockhint');
    if (hint) {
      hint.textContent = full ? `В очереди уже ${QUEUE_MAX} — дождитесь ответа или остановите` : isBusy() ? 'Добавлю в очередь — отвечу следом' : '';
      hint.hidden = !hint.textContent;
    }
    padDock();
  }

  async function ask(body, btn) {
    if (S.queued) { toast('Уже жду Секретаря с одним поручением', 'err'); haptic('warning'); return false; }
    if (queueFull()) { toast(`В очереди уже ${QUEUE_MAX} — дождитесь ответа`, 'err'); haptic('warning'); return false; }
    haptic('tap');
    const mine = { q: body.text || PRESET_LABEL[body.preset] || 'Поручение' + (body.chat ? ': ' + body.chat : '') };
    if (body.preset && body.chat && body.preset !== 'brief') mine.q = (PRESET_LABEL[body.preset] || 'Разбор') + ': ' + body.chat;
    S.pending.push(mine);
    if (S.sec) paintLive();
    try {
      const r = await busy(btn, () => sec('ask', body));
      S.pending = S.pending.filter((x) => x !== mine);
      started(r);
      return true;
    } catch (e) {
      S.pending = S.pending.filter((x) => x !== mine);
      if (e.status === 502 || (e.data && e.data.down)) return queue(body);  // Секретарь не отвечает — поднимем и повторим
      paintLive();
      toast(e.message, 'err'); haptic('error'); return false;
    }
  }
  function started(r) {
    S.secErr = null;
    if (S.sec) {
      S.sec.run = r.run;
      if (r.queue) S.sec.queue = r.queue;
      if (r.queued) { toast(`Добавил в очередь · №${r.queued}`, 'ok'); haptic('success'); }
    } else loadSec();
    paintLive();
    followFeed(true);
    pollSoon(900);
  }
  // Секретарь лежит: просим Главного поднять его и повторяем отправку, пока не ответит (до ~6 минут: установка долгая)
  async function queue(body) {
    S.queued = { body, text: body.text || body.chat || body.preset || '', left: 90 };
    S.secErr = S.secErr || 'down';
    haptic('warning');
    toast('Секретарь не отвечает — поднимаю и отправлю поручение', 'ok');
    paintLive();
    try { await api('action', { action: 'secretary' }); } catch (e) { /* Главный напишет в чат, что вышло */ }
    retryQueued();
    return true;
  }
  function retryQueued() {
    const q = S.queued;
    if (!q) return;
    setTimeout(async () => {
      if (S.queued !== q) return;
      try {
        const r = await sec('ask', q.body);
        S.queued = null;
        started(r);
        toast('Секретарь поднялся — поручение отправлено', 'ok');
        haptic('success');
      } catch (e) {
        const down = e.status === 502 || (e.data && e.data.down);
        if (down && --q.left > 0) return retryQueued();
        S.queued = null;
        if (q.body.text && !S.draft) S.draft = q.body.text;
        toast(down ? 'Секретарь так и не поднялся — проверьте чат с Главным' : e.message, 'err');
        haptic('error');
        render();
      }
    }, 4000);
  }
  async function send(btn) {
    const text = S.draft.trim();
    if (!text) return;
    const ta = $('ask');
    S.draft = '';  // поле очищаем сразу: пузырь уже в ленте; не ушло — вернём текст
    if (ta) { ta.value = ''; ta.style.height = 'auto'; }
    syncSend();
    if (!(await ask({ text }, btn))) {
      if (!S.draft) { S.draft = text; const now = $('ask'); if (now) { now.value = text; if (now.sizeNow) now.sizeNow(); } syncSend(); }
    }
  }

  function psySheet() {
    const chats = ((S.sec && S.sec.chats) || []).slice(0, 6);
    const el = sheet(`<h2>🧠 Разбор чата</h2><p class="hint">Какой чат или кого разобрать? Можно с уточнением: «группа 8Б за месяц, как ко мне относятся»</p>
      <input id="psy" placeholder="Чат или человек" maxlength="200" enterkeyhint="go">
      ${chats.length ? `<div class="chips gap">${chats.map((c) => `<button class="chip" data-name="${esc(c.name)}">${esc(c.name)}</button>`).join('')}</div>` : ''}
      <button class="btn primary" data-go>Разобрать</button>`);
    const input = el.querySelector('#psy');
    const goPsy = async () => {
      const v = input.value.trim();
      if (!v) { input.focus(); haptic('warning'); return; }
      if (await ask({ preset: 'psy', chat: v })) el.close();
    };
    el.addEventListener('click', (e) => {
      const c = e.target.closest('[data-name]');
      if (c) { input.value = c.dataset.name; haptic('select'); }
      if (e.target.closest('[data-go]')) goPsy();
    });
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') goPsy(); });
  }

  function chatSheet(c) {
    const meta = [c.kind, c.unread ? num(c.unread, 'непрочитанное', 'непрочитанных', 'непрочитанных') : '',
      c.mentions ? num(c.mentions, 'упоминание', 'упоминания', 'упоминаний') : ''].filter(Boolean).join(' · ');
    const el = sheet(`<h2 class="clip">${esc(c.name)}</h2><p class="hint">${esc(meta)}</p>
      <div class="list"><button class="row" data-p="brief"><span class="ic">📬</span><div class="main"><div class="title">Что пишут</div>
        <div class="meta">кратко: кто что хочет и что ответить</div></div><span class="chev"></span></button>
      <button class="row" data-p="psy"><span class="ic">🧠</span><div class="main"><div class="title">Разбор как психолог</div>
        <div class="meta">роли, тон, скрытое, советы</div></div><span class="chev"></span></button></div>`);
    el.addEventListener('click', async (e) => {
      const b = e.target.closest('[data-p]');
      if (b && await ask({ preset: b.dataset.p, chat: c.name, id: c.id })) el.close();
    });
  }

  // ---------- Боты ----------
  function statusOf(b) {
    if (!b.status) return { cls: '', text: b.part ? 'не запущен' : 'не поставлен', short: 'нет' };
    if (b.status === 'online') return { cls: 'ok', text: 'работает', short: ago(b.uptime) };
    return { cls: 'err', text: b.status === 'stopped' ? 'остановлен' : 'упал', short: b.status === 'stopped' ? 'стоп' : 'упал' };
  }
  const updatingNow = (d) => !!S.updating || !!(d.live && d.live.kind === 'update' && !d.live.done && d.now - d.live.at < 1800);

  function botsView() {
    const d = S.hub;
    if (!d) return skeleton('🤖', 'Боты');
    const bots = d.bots, run = bots.filter((b) => b.status), on = run.filter((b) => b.status === 'online');
    const down = run.length - on.length;
    const used = run.reduce((s, b) => s + (b.mem || 0), 0);
    const memPct = d.server.mem_total ? Math.round((1 - d.server.mem_free / d.server.mem_total) * 100) : null;
    const upd = updatingNow(d);
    const head = !d.pm2 ? { e: 'warn', t: 'pm2 не отвечает', s: 'Состояние неизвестно — проверю ещё раз' }
      : down ? { e: 'err', t: `${down} ${plural(down, 'бот не работает', 'бота не работают', 'ботов не работают')}`, s: `Работают ${on.length} из ${run.length}` }
        : { e: 'ok', t: 'Все боты работают', s: `${num(on.length, 'бот', 'бота', 'ботов')} · ${mem(used)}` };
    const liveText = upd && d.live && d.live.text ? d.live.text : upd ? 'Обновляю…' : '';
    return hero('🤖', 'Боты', `${num(bots.length, 'бот', 'бота', 'ботов')}${memPct != null ? ` · память ${memPct}%` : ''}`) +
      `<section class="card feature rise"><div class="state"><span class="state-e ${head.e}"><i></i></span><div class="main">
        <div class="state-t">${esc(head.t)}</div><div class="hint">${esc(head.s)}</div></div></div>
        ${liveText ? `<div class="note live-note"><span class="spin"></span><span>${esc(liveText)}</span></div>` : ''}
        <button class="btn primary" data-act="update" ${upd ? 'disabled' : ''}>${upd ? 'Обновляю…' : '⬆️ Обновить всех'}</button></section>
      <div class="label">Боты<span class="more tnum">${run.length} из ${bots.length}</span></div>
      <div class="list">${bots.map(botRow).join('')}</div>
      <div class="list sup-list"><button class="row tap" data-act="support"><span class="ic">🛠</span><div class="main">
        <div class="title">Поддержка и пожелания</div><div class="meta">Любому боту: ошибка или идея — починю/добавлю</div></div>
        <span class="end chev"></span></button></div>
      ${serverCard(d)}`;
  }
  function botRow(b) {
    const st = statusOf(b);
    const bits = [];
    if (b.part) bits.push('часть Главного');
    else if (b.username) bits.push('@' + b.username);
    if (b.status === 'online') bits.push(mem(b.mem));
    if (b.status && b.restarts) bits.push(`↻${NB}${b.restarts}`);
    if (!b.status) bits.push(b.about || st.text);
    return `<button class="row ${b.status ? '' : 'off'}" data-bot="${esc(b.name)}"><span class="ic">${esc(emoji(b))}</span>
      <div class="main"><div class="title">${esc(b.title)}</div><div class="meta clip">${esc(bits.join(' · '))}</div></div>
      <div class="end"><span class="pill dot ${st.cls} tnum">${esc(st.short)}</span><span class="chev"></span></div></button>`;
  }

  function metric(name, used, total, right, sub) {
    const p = total ? Math.max(0, Math.min(1, used / total)) : 0;
    const lvl = p >= 0.9 ? 'err' : p >= 0.7 ? 'warn' : 'ok';
    return `<div class="metric"><div class="m-top"><b>${name}</b><span class="tnum">${right}</span></div>
      <div class="bar ${lvl}"><i data-w="${(p * 100).toFixed(1)}%" style="width:0"></i></div><div class="hint tnum">${sub}</div></div>`;
  }
  function payLine(p) {
    return p.left > 0 ? `Оплачен до ${p.text} · ещё ${num(p.left, 'день', 'дня', 'дней')}` : p.left === 0 ? 'Оплата сервера — сегодня!' : 'Срок оплаты прошёл!';
  }
  function serverCard(d) {
    const s = d.server, rows = [];
    if (s.mem_total) {
      const used = s.mem_total - s.mem_free;
      rows.push(metric('Память', used, s.mem_total, `${Math.round(used / s.mem_total * 100)}%`, `${mem(used)} из ${mem(s.mem_total)}`));
    }
    if (s.disk_total) {
      const used = s.disk_total - s.disk_free;
      rows.push(metric('Диск', used, s.disk_total, `${Math.round(used / s.disk_total * 100)}%`, `свободно ${fmt(s.disk_free)}${NB}ГБ из ${fmt(s.disk_total)}`));
    }
    if (s.load != null) {
      const l = parseFloat(s.load) || 0;
      rows.push(metric('Нагрузка', l, s.cpus || 1, fmt(l, 2), `${num(s.cpus || 1, 'ядро', 'ядра', 'ядер')} · без перезагрузки ${ago(s.uptime || 0)}`));
    }
    const p = d.paid;
    const pay = p ? `<div class="row-flat pay"><span class="ava-ic">💳</span><div class="main"><b>${esc(p.left > 0 ? 'Оплачен до ' + p.text : payLine(p))}</b>
        <div class="hint">${p.left < 0 ? 'Продлите и пришлите новый /paid в чат' : p.left <= 7 ? 'Скоро срок — продлите, иначе боты выключатся' : 'Напомню за 7, 3 и 1 день'}</div></div>
        ${p.left >= 0 ? `<span class="pill ${p.left <= 3 ? 'err' : p.left <= 7 ? 'warn' : 'ok'} tnum">${p.left}${NB}дн</span>` : '<span class="pill err">!</span>'}</div>`
      : `<div class="row-flat pay"><span class="ava-ic">💳</span><div class="main"><b>Срок оплаты не указан</b>
        <div class="hint">Пришлите в чат <code>/paid 07.11</code> — напомню заранее</div></div></div>`;
    return `<div class="label">Сервер</div><section class="card">${rows.join('') || '<div class="hint">Нет данных о сервере</div>'}${pay}</section>`;
  }

  // 🛠 Поддержка и пожелания: выбор бота + текст → та же доработка, что «✏️ Доработать» в чате
  const canFix = (name) => !!(S.hub && (S.hub.support || []).some((t) => t.id === name));
  function support(name) {
    const targets = (S.hub && S.hub.support) || [];
    if (!targets.length) return toast('Нет запущенных ботов', 'err');
    Kit.support('/app/api/support', { targets, target: name || targets[0].id,
      hint: 'Выбери бота и напиши, что сломалось или чего не хватает — починю/добавлю.' });
  }
  Kit.supportOpts = { open: () => support() };  // плавающая кнопка 🛠 на всех вкладках — тот же лист с выбором бота

  function botSheet(name) {
    const b = S.hub && S.hub.bots.find((x) => x.name === name);
    if (!b) return;
    const st = statusOf(b);
    const html = () => `<div class="d-head"><span class="logo-sm">${esc(emoji(b))}</span><div class="main"><h2>${esc(b.title)}</h2>
        <span class="pill dot ${st.cls}">${esc(st.text)}${b.status === 'online' ? ' · ' + ago(b.uptime) : ''}</span></div></div>
      ${b.about ? `<p class="hint about">${esc(b.about)}</p>` : ''}
      ${b.status ? `<div class="stats">
        <div class="stat"><div class="v tnum">${b.status === 'online' ? mem(b.mem) : '—'}</div><div class="k">память</div></div>
        <div class="stat"><div class="v tnum">${fmt(b.cpu)}<small>%</small></div><div class="k">процессор</div></div>
        <div class="stat"><div class="v tnum">${b.restarts}</div><div class="k">рестарты</div></div></div>`
        : `<div class="note">${b.part ? 'Секретарь не запущен — подниму его сам.' : 'Бот не поставлен. Поставить — в чате Главного: «🤖 Боты» → «⋯ Ещё» → «➕ Поставить».'}</div>`}
      <div class="stack sheet-actions">
        ${b.status ? '<button class="btn primary" data-a="restart">Перезапустить</button>' : b.part ? '<button class="btn primary" data-a="revive">Поднять Секретаря</button>' : ''}
        <div class="btns">${b.status ? '<button class="btn tonal" data-a="logs">Логи</button>' : ''}
          ${b.username && !b.part ? '<button class="btn tonal" data-a="open">Открыть</button>' : ''}
          ${canFix(b.name) ? '<button class="btn tonal" data-a="support">🛠 Поддержка</button>' : ''}</div></div>
      <div id="logbox"></div>`;
    const el = sheet(html());
    el.addEventListener('click', async (e) => {
      const a = e.target.closest('[data-a]');
      if (!a) return;
      if (a.dataset.a === 'open') { haptic('tap'); const url = `https://t.me/${b.username}`; try { tg.openTelegramLink(url); } catch (er) { window.open(url, '_blank'); } return; }
      if (a.dataset.a === 'revive') { el.close(); return revive(); }
      if (a.dataset.a === 'support') { el.close(); return support(b.name); }
      if (a.dataset.a === 'logs') {
        const box = el.querySelector('#logbox');
        box.innerHTML = '<div class="sk" style="height:120px;margin-top:12px"></div>';
        try {
          const r = await busy(a, () => api('action', { action: 'logs', arg: b.name }));
          box.innerHTML = `<pre class="logs">${esc(r.text)}</pre>`;
          const pre = box.querySelector('pre');
          pre.scrollTop = pre.scrollHeight;
        } catch (er) { box.innerHTML = ''; toast(er.message, 'err'); }
        return;
      }
      if (a.dataset.a === 'restart') {
        if (!(await confirmBox(b.self ? 'Перезапустить Главного? Приложение переподключится через несколько секунд.' : `Перезапустить «${b.title}»?`))) return;
        try {
          const r = await busy(a, () => api('action', { action: 'restart', arg: b.name }));
          if (b.self) { S.updating = { boot: S.hub.boot, self: true }; el.close(); pollSoon(1500); return; }
          haptic(r.ok ? 'success' : 'error');
          toast(r.text, r.ok ? 'ok' : 'err');
          el.close();
          loadHub();
        } catch (er) { haptic('error'); toast(er.message, 'err'); }
      }
    });
  }

  async function updateAll(btn) {
    if (!(await confirmBox('Скачать свежий код из main и перезапустить всех ботов? Главный тоже перезапустится.'))) return;
    try {
      await busy(btn, () => api('action', { action: 'update' }));
      S.updating = { boot: S.hub.boot };
      haptic('tap');
      render();
      pollSoon(1500);
    } catch (e) { haptic('error'); toast(e.message, 'err'); }
  }
  async function revive(btn) {
    try {
      const r = await busy(btn, () => api('action', { action: 'secretary' }));
      toast(r.text, 'ok');
      setTimeout(() => loadSec(), 8000);
    } catch (e) { toast(e.message, 'err'); }
  }

  // ---------- Дела ----------
  function moreView() {
    const d = S.hub, st = S.sec;
    if (!d && !st) return skeleton('🗂', 'Дела');
    let out = hero('🗂', 'Дела', 'Расписание, напоминания и память');
    // расписание Секретаря
    const js = (st && st.jobs) || [];
    out += `<div class="label">Расписание Секретаря${js.length ? `<span class="more tnum">${js.length}</span>` : ''}</div>`;
    if (!st) out += `<div class="note">${S.secErr ? 'Секретарь не отвечает — расписание пока не видно.' : 'Загружаю…'}</div>`;
    else if (!js.length) {
      out += `<div class="card"><div class="empty"><span class="e">⏰</span><b>Пока пусто</b>Попросите, например: «каждый будний день в 7:00 пишите маме „доброе утро“» — буду отправлять сам.</div>
        <button class="btn tonal" data-act="jobexample">Написать поручение</button></div>`;
    } else {
      out += `<div class="list">${js.map((j) => `<div class="row job"><div class="clock"><b class="tnum">${esc(j.time)}</b><span>${esc(j.period)}</span></div>
        <div class="main"><div class="title">→ ${esc(j.to)}</div><div class="meta">${j.text ? '«' + esc(j.text) + '»' : '✦ ' + esc(j.ai)}</div>
        <div class="meta tnum">дальше ${esc(j.next)} · отправлено ${j.sent}</div></div>
        <button class="btn icon ghost" data-deljob="${esc(j.id)}" aria-label="Удалить">🗑</button></div>`).join('')}</div>`;
    }
    // напоминания Главного
    if (d) {
      const rs = d.reminders;
      out += `<div class="label">Напоминания${rs.length ? `<span class="more tnum">${rs.length}</span>` : ''}</div><div class="list">
        <div class="row"><span class="ic">🌙</span><div class="main"><div class="title">${d.digest === 'off' ? 'Вечерняя отбивка выключена' : 'Вечерняя отбивка в ' + esc(d.digest)}</div>
          <div class="meta">${d.digest === 'off' ? 'Включить: /digest 21:00 в чате' : 'боты, сервер, оплата, планы на завтра'}</div></div></div>
        ${rs.map((r) => `<div class="row"><span class="ic tnum t-ic">${esc(r.time)}</span><div class="main"><div class="title">${esc(r.text)}</div>
          <div class="meta">${esc(r.when.replace(/ в \d\d:\d\d$/, ''))}</div></div>
          <button class="btn icon ghost" data-delrmd="${esc(r.id)}" aria-label="Удалить">🗑</button></div>`).join('')}</div>
        ${rs.length ? '' : '<p class="hint pad">Добавить: в чате <code>/remind 09:00 текст</code>, <code>/remind пн-пт 18:30 текст</code> или <code>/remind 25.10 10:00 текст</code>.</p>'}`;
    }
    // память
    out += `<div class="label">Память Секретаря</div><button class="card tap mem-card" data-act="memory" ${st ? '' : 'disabled'}>
      <div class="row-flat"><span class="ava-ic">📝</span><div class="main"><b>Что я о вас знаю</b>
      <div class="hint">Учитываю в каждом ответе. В чате можно сказать: «запомните, что…»</div></div><span class="chev"></span></div></button>`;
    return out;
  }

  async function memorySheet() {
    let text = '';
    try { text = (await sec('memory')).text || ''; } catch (e) { return toast(e.message, 'err'); }
    const el = sheet(`<h2>📝 Память</h2><textarea id="mem" rows="9" maxlength="20000"
      placeholder="— тренировки по боксу: вт и чт в 18:00&#10;— маму зовут Ольга">${esc(text)}</textarea>
      <div class="mem-bar"><span class="hint tnum" id="memn"></span><button class="btn primary sm" data-save disabled>Сохранить</button></div>`);
    const ta = el.querySelector('#mem'), save = el.querySelector('[data-save]'), n = el.querySelector('#memn');
    const sync = () => { save.disabled = ta.value === text; n.textContent = `${ta.value.length.toLocaleString('ru-RU')} / 20 000`; };
    ta.addEventListener('input', sync);
    sync();
    save.addEventListener('click', async () => {
      try {
        const r = await busy(save, () => sec('memory/save', { text: ta.value }));
        text = r.text; sync(); haptic('success'); toast('Сохранено ✓', 'ok'); el.close();
      } catch (e) { toast(e.message, 'err'); }
    });
  }

  async function delJob(id) {
    const j = S.sec.jobs.find((x) => x.id === id);
    if (!j || !(await confirmBox(`Удалить расписание?\n\n${j.time}, ${j.period} → ${j.to}`))) return;
    try { const r = await sec('jobs/delete', { id }); S.sec.jobs = r.jobs; haptic('success'); toast('Расписание удалено', 'ok'); render(); } catch (e) { toast(e.message, 'err'); }
  }
  async function delReminder(id) {
    const r = S.hub.reminders.find((x) => x.id === id);
    if (!(await confirmBox(`Удалить напоминание «${r ? r.text : ''}»?`))) return;
    try {
      const res = await api('action', { action: 'reminder_delete', arg: id });
      S.hub.reminders = S.hub.reminders.filter((x) => x.id !== id);
      haptic('success'); toast(res.text, 'ok'); render();
    } catch (e) { toast(e.message, 'err'); }
  }

  function skeleton(logo, title) {
    const row = '<div class="row"><span class="ic sk"></span><div class="main"><div class="sk" style="height:14px;width:46%"></div><div class="sk" style="height:12px;width:70%;margin-top:6px"></div></div></div>';
    return hero(logo, title, 'Загружаю…') + `<div class="card"><div class="sk" style="height:22px;width:55%"></div><div class="sk" style="height:50px;margin-top:16px"></div></div>
      <div class="label"><span class="sk" style="width:70px;height:12px"></span></div><div class="list">${row.repeat(5)}</div>`;
  }

  // ---------- нажатия ----------
  async function onClick(e) {
    const t = e.target;
    const b = t.closest('[data-bot]');
    if (b) return botSheet(b.dataset.bot);
    const f = t.closest('[data-fill]');
    if (f) {  // заготовка поручения: вставляем в поле и ждём дописки (город, даты…)
      const ta = $('ask');
      S.draft = FILLS[+f.dataset.fill][1];
      if (ta) { ta.value = S.draft; ta.focus(); ta.setSelectionRange(S.draft.length, S.draft.length); ta.dispatchEvent(new Event('input')); }
      haptic('select');
      return;
    }
    const p = t.closest('[data-preset]');
    if (p) return ask({ preset: p.dataset.preset }, p);
    const c = t.closest('[data-chat]');
    if (c) return chatSheet(S.sec.chats[+c.dataset.chat]);
    const x = t.closest('[data-expand]');
    if (x) { S.open[x.dataset.expand] = true; x.previousElementSibling.classList.remove('clip'); x.remove(); haptic('select'); return; }
    const dj = t.closest('[data-deljob]');
    if (dj) return delJob(dj.dataset.deljob);
    const dr = t.closest('[data-delrmd]');
    if (dr) return delReminder(dr.dataset.delrmd);
    const a = t.closest('[data-act]');
    if (!a) return;
    const act = a.dataset.act;
    if (act === 'send') return send(a);
    if (act === 'psy') return psySheet();
    if (act === 'stop') {
      haptic('rigid');
      try { const r = await sec('stop'); S.sec.run = r.run || S.sec.run; S.sec.queue = []; paintLive(); } catch (er) { toast(er.message, 'err'); }
      return;
    }
    if (act === 'new') {  // новый разговор: контекст сбрасывается, очередь чистится
      if (!(await confirmBox('Начать новый разговор? Секретарь забудет, о чём вы говорили выше, а вопросы в очереди пропадут.'))) return;
      try {
        await sec('new');
        S.older = false;
        haptic('success'); toast('Начинаем с чистого листа ✦', 'ok');
        await loadSec(true);
      } catch (er) { toast(er.message, 'err'); }
      return;
    }
    if (act === 'older') { S.older = !S.older; haptic('select'); return paintLive(); }
    if (act === 'chat') return toChat();
    if (act === 'tochat') {  // режим «💬 чат»: бот пришлёт главное меню, приложение закрывается
      try { await busy(a, () => api('mode', { mode: 'chat' })); haptic('success'); toChat(); } catch (er) { toast(er.message, 'err'); }
      return;
    }
    if (act === 'login') {
      try { const r = await busy(a, () => sec('login')); toast(r.text, 'ok'); setTimeout(toChat, 1200); } catch (er) { toast(er.message, 'err'); }
      return;
    }
    if (act === 'refresh') { haptic('tap'); a.classList.add('busy'); await Promise.all([loadSec(true), loadHub()]); a.classList.remove('busy'); return; }
    if (act === 'allchats') { S.open.allChats = true; return paintLive(); }
    if (act === 'revive') return revive(a);
    if (act === 'unqueue') { S.queued = null; haptic('select'); return paintLive(); }
    if (act === 'update') return updateAll(a);
    if (act === 'support') return support();
    if (act === 'memory') return memorySheet();
    if (act === 'jobexample') {
      S.draft = 'Каждый будний день в 7:00 пишите маме «доброе утро»'; bar.set('sec'); go('sec');
      const ta = $('ask');
      if (ta) { ta.value = S.draft; if (ta.sizeNow) ta.sizeNow(); syncSend(); }
    }
  }
  view.addEventListener('click', onClick);

  // ---------- данные ----------
  async function loadHub() {
    try {
      const d = await api('state');
      const u = S.updating;
      if (u && d.boot !== u.boot) { S.updating = null; haptic('success'); toast(u.self ? 'Главный перезапущен' : 'Обновил — все боты перезапущены', 'ok'); }
      else if (u && !u.self && d.live && d.live.kind === 'update' && d.live.done) { S.updating = null; toast(d.live.text, d.live.ok ? 'ok' : 'err'); }
      S.hub = d;
    } catch (e) {
      if (e.status === 401 || e.status === 403) { S.gate = e.message; }
      else if (!S.updating && S.hub) toast('Нет связи с Главным — повторю', 'err');
    }
    if (S.tab !== 'sec') render();
  }
  async function loadSec(fresh) {
    try {
      S.sec = await sec('state', { fresh: !!fresh });
      S.secErr = null;
    } catch (e) {
      if (e.status === 401 || e.status === 403) S.gate = e.message;
      else S.secErr = e.status === 502 ? 'down' : e.message;
    }
    if (S.tab === 'sec') paintLive(); else if (S.tab === 'more') render();
  }
  async function pollRun() {
    try {
      const was = S.sec.run;
      const r = await sec('run', { history: true });
      S.sec.run = r.run;
      S.sec.queue = r.queue || [];
      if (r.conv) S.sec.conv = r.conv;
      if (r.history) S.sec.history = r.history;
      // закончился шаг: тот же run стал done/error, либо очередь уже запустила следующий вопрос
      if (was && was.state === 'work' && (!r.run || r.run.id !== was.id || r.run.state !== 'work')) {
        const fin = (r.history || []).find((x) => x.id === was.id) || (r.run && r.run.id === was.id ? r.run : null);
        const ok = !fin || fin.state === 'done';
        haptic(ok ? 'success' : 'error');
        if (ok) toast('Готово ✦ Ответ и в чате', 'ok');
        if (!isBusy()) return loadSec();
      }
      paintLive();
    } catch (e) { /* сеть моргнула — попробую снова */ }
  }

  let timer = 0;
  function pollSoon(ms) { clearTimeout(timer); timer = setTimeout(tick, ms); }
  function poll() { pollSoon(S.updating || isBusy() ? 1500 : S.tab === 'bots' ? 6000 : 20000); }
  async function tick() {
    if (!document.hidden && !S.gate) {
      if (isBusy() && S.tab === 'sec') await pollRun();
      else if (S.updating) await loadHub();
      else await Promise.all([S.tab !== 'sec' || !S.hub ? loadHub() : null, S.tab !== 'bots' ? loadSec() : null]);
    }
    poll();
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) pollSoon(50); });

  // ---------- старт ----------
  try {
    const q = new URLSearchParams(location.search).get('tab');
    const saved = sessionStorage.getItem('tab');
    const want = q === 'sec' || q === 'bots' || q === 'more' ? q : saved;
    if (want && TABS.some((x) => x.id === want)) S.tab = want;
  } catch (e) { /* без хранилища */ }
  bar = Kit.tabbar($('tabs'), TABS, S.tab, go);
  window.addEventListener('resize', padDock);
  if (!(tg && tg.initData)) { S.gate = 'нет initData'; render(); return; }
  render(true);
  Promise.all([loadHub(), loadSec()]).then(() => { if (S.gate) render(); poll(); });
})();
