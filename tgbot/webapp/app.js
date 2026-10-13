/* Секретарь — мини-приложение. Без сборки. Данные — tgbot/web.py (/api/*, подпись Telegram в X-Init-Data).
   Поручения идут тем же путём, что сообщения в чате: статус и ответ видны и здесь, и в чате; «Да/Нет» — в чате. */
(() => {
  'use strict';
  const tg = window.Telegram && window.Telegram.WebApp;
  const view = document.getElementById('view');
  const tabsEl = document.getElementById('tabs');
  const sheetRoot = document.getElementById('sheet');
  const toastEl = document.getElementById('toast');
  const dockEl = document.getElementById('dock');

  const S = {
    tab: 'home', state: null, run: null, history: [], memory: null, memDirty: false,
    error: '', gate: '', draft: '', open: {}, refreshing: false, timer: 0, seen: {}, hidden: {},
    queue: [], conv: '', pending: [], older: false, feedSig: null,
  };
  const QUEUE_MAX = 5;  // как в tgbot/agent.py

  // ---------- мелочи ----------
  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else if (k === 'style') el.style.cssText = v;
      else el.setAttribute(k, v === true ? '' : v);
    }
    const add = (c) => {
      if (c == null || c === false) return;
      if (Array.isArray(c)) c.forEach(add);
      else el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
    };
    kids.forEach(add);
    return el;
  }
  const svg = (inner, cls = 'ic', box = '0 0 24 24') => {
    const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    s.setAttribute('viewBox', box);
    s.setAttribute('class', cls);
    s.setAttribute('aria-hidden', 'true');
    s.innerHTML = inner; // только наши константы ниже
    return s;
  };
  const STAR = '<path d="M12 0C12.9 7.2 16.8 11.1 24 12 16.8 12.9 12.9 16.8 12 24 11.1 16.8 7.2 12.9 0 12 7.2 11.1 11.1 7.2 12 0Z"/>';
  const I = {
    home: '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
    ask: '<path d="M21 12c0 4.4-4 8-9 8-1.4 0-2.7-.3-3.9-.8L3 20l1.3-3.9C3.5 14.9 3 13.5 3 12c0-4.4 4-8 9-8s9 3.6 9 8z"/><path d="M8.5 12h.01M12 12h.01M15.5 12h.01"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    note: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>',
    send: '<path d="M12 19V5M5 12l7-7 7 7"/>',
    refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
    trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    chat: '<path d="M4 5h16v11H8l-4 4z"/>',
  };
  const star = (cls) => svg(STAR, cls);
  const icon = (name) => svg(I[name]);

  function haptic(kind = 'light') {
    try {
      const hf = tg && tg.HapticFeedback;
      if (!hf) return;
      if (kind === 'success' || kind === 'error' || kind === 'warning') hf.notificationOccurred(kind);
      else if (kind === 'select') hf.selectionChanged();
      else hf.impactOccurred(kind);
    } catch (e) { /* старый Telegram */ }
  }

  let toastTimer = 0;
  function toast(text, err) {
    toastEl.textContent = text;
    toastEl.className = 'toast show' + (err ? ' err' : '');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastEl.className = 'toast' + (err ? ' err' : ''); }, 2600);
  }

  const plural = (n, one, few, many) => {
    const a = n % 10, b = n % 100;
    return `${n} ${a === 1 && b !== 11 ? one : a >= 2 && a <= 4 && (b < 12 || b > 14) ? few : many}`;
  };
  const mmss = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  const GRADS = [['#7c5cff', '#22d3ee'], ['#ec4899', '#7c5cff'], ['#22d3ee', '#34d399'], ['#f59e0b', '#ec4899'],
    ['#6366f1', '#a855f7'], ['#14b8a6', '#3b82f6'], ['#f97316', '#ef4444']];
  const gradFor = (id) => { const g = GRADS[Math.abs(Number(id) || 0) % GRADS.length]; return `background:linear-gradient(135deg,${g[0]},${g[1]})`; };

  async function api(path, body) {
    const r = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Init-Data': (tg && tg.initData) || '' },
      body: JSON.stringify(body || {}),
    });
    let data = {};
    try { data = await r.json(); } catch (e) { /* пусто */ }
    if (!r.ok) { const e = new Error(data.error || 'Сервер не ответил'); e.status = r.status; throw e; }
    return data;
  }

  // ---------- ответ Секретаря: HTML из чата -> безопасный DOM (только разрешённые теги) ----------
  const ALLOWED = { B: 'b', STRONG: 'b', I: 'i', EM: 'i', U: 'u', S: 's', DEL: 'del', CODE: 'code', PRE: 'pre',
    BLOCKQUOTE: 'blockquote', A: 'a', 'TG-SPOILER': 'span', BR: 'br' };
  function safeHtml(html) {
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
        if (t === 'blockquote' && n.hasAttribute('expandable')) {
          e.className = 'fold';
          e.addEventListener('click', () => e.classList.toggle('open'));
        }
        walk(n, e);
        dst.appendChild(e);
      }
    };
    walk(doc.body.firstChild, out);
    return out;
  }

  function answerBlock(html, key, clip) {
    const box = h('div', { class: 'answer' }, safeHtml(html));
    const wrap = h('div', null, box);
    const long = (html || '').length > 420 || (html || '').split('\n').length > 8;
    if (long && clip !== false && !S.open[key]) {
      box.classList.add('clip');
      wrap.appendChild(h('button', {
        class: 'text-btn expand', text: 'Показать полностью',
        onclick: (e) => { S.open[key] = true; box.classList.remove('clip'); e.currentTarget.remove(); haptic('select'); },
      }));
    }
    return wrap;
  }

  // ---------- шторка ----------
  function closeSheet() {
    sheetRoot.classList.remove('open');
    try { tg && tg.BackButton.hide(); } catch (e) { /* */ }
    setTimeout(() => { if (!sheetRoot.classList.contains('open')) sheetRoot.replaceChildren(); }, 300);
  }
  function openSheet(...content) {
    sheetRoot.replaceChildren(
      h('div', { class: 'scrim', onclick: closeSheet }),
      h('div', { class: 'sheet', role: 'dialog' }, h('div', { class: 'grab' }), ...content),
    );
    requestAnimationFrame(() => requestAnimationFrame(() => sheetRoot.classList.add('open')));
    try { tg && tg.BackButton.show(); } catch (e) { /* */ }
    haptic('light');
  }
  const act = (em, title, sub, onclick) => h('button', { class: 'act', onclick },
    h('span', { class: 'em', text: em }), h('div', { class: 'grow' }, h('b', { class: 'ell', text: title }), sub ? h('span', { text: sub }) : null));

  // ---------- поручения ----------
  const busy = () => !!(S.run && S.run.state === 'work');
  const queueFull = () => S.queue.length >= QUEUE_MAX;
  const working = () => busy() || S.queue.length > 0;   // Секретарь занят: новые вопросы встают в очередь
  // законченное поручение показываем, пока его не скрыли и если оно свежее (или мы видели, как оно шло)
  const shownRun = () => {
    const r = S.run;
    if (!r) return null;
    if (r.state === 'work') { S.seen[r.id] = true; return r; }
    if (S.hidden[r.id] || (!S.seen[r.id] && (r.ago || 0) > 600)) return null;
    return r;
  };
  const PRESET_LABEL = { missed: 'Что я пропустил', sort: 'Навести порядок', psy_week: 'Разбор чатов за неделю',
    psy_month: 'Разбор чатов за месяц', psy: 'Разбор чата', brief: 'Что пишут' };

  async function ask(body) {
    if (queueFull()) { toast(`В очереди уже ${QUEUE_MAX} — дождитесь ответа`, true); haptic('warning'); return false; }
    haptic('medium');
    const mine = { q: body.text || ((PRESET_LABEL[body.preset] || 'Поручение') + (body.chat && body.preset !== 'brief' ? ': ' + body.chat : '')) };
    S.pending.push(mine);
    paintLive();
    try {
      const r = await api('/api/ask', body);
      S.pending = S.pending.filter((x) => x !== mine);
      S.run = r.run;
      if (r.queue) S.queue = r.queue;
      if (r.queued) { toast(`Добавил в очередь · №${r.queued}`); haptic('success'); }
      closeSheet();
      paintLive();
      followFeed(true);
      poll(800);
      return true;
    } catch (e) {
      S.pending = S.pending.filter((x) => x !== mine);
      paintLive();
      toast(e.message, true);
      haptic('error');
      return false;
    }
  }

  async function stop() {
    haptic('rigid');
    try { const r = await api('/api/stop'); S.run = r.run || S.run; S.queue = []; paintLive(); } catch (e) { toast(e.message, true); }
  }

  function closeToChat() {
    haptic('light');
    try { tg.close(); } catch (e) { toast('Откройте чат с Секретарём'); }
  }

  function psySheet(prefill) {
    const input = h('input', { class: 'field', placeholder: 'Чат или человек', value: prefill || '', enterkeyhint: 'go', maxlength: '200' });
    const go = () => {
      const v = input.value.trim();
      if (!v) { input.focus(); haptic('warning'); return; }
      ask({ preset: 'psy', chat: v });
    };
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
    const chats = ((S.state && S.state.chats) || []).slice(0, 6);
    openSheet(
      h('h3', { text: '🧠 Разбор чата' }),
      h('div', { class: 'sub', text: 'Какой чат или кого разобрать? Можно с уточнением: «группа 8Б за месяц, как ко мне относятся»' }),
      input,
      chats.length ? h('div', { class: 'chips' }, chats.map((c) => h('button', {
        class: 'chip', text: c.name, onclick: () => { input.value = c.name; haptic('select'); },
      }))) : null,
      h('button', { class: 'btn primary wide', onclick: go }, 'Разобрать'),
    );
  }

  // 🛠 Поддержка и пожелания (как Kit.support у остальных ботов): текст + скриншот -> /api/support -> правка Claude
  function shrink(file) {  // -> base64 JPEG ≤ 1600 px (через data:, его пускает CSP страницы)
    return new Promise((resolve, reject) => {
      const rd = new FileReader();
      const img = new Image();
      rd.onload = () => { img.src = rd.result; };
      rd.onerror = img.onerror = () => reject(new Error('Картинка не открылась'));
      img.onload = () => {
        const k = Math.min(1, 1600 / Math.max(img.naturalWidth, img.naturalHeight));
        const c = h('canvas', { width: Math.max(1, Math.round(img.naturalWidth * k)), height: Math.max(1, Math.round(img.naturalHeight * k)) });
        c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
        resolve(c.toDataURL('image/jpeg', 0.82).split(',')[1]);
      };
      rd.readAsDataURL(file);
    });
  }
  // плавающая кнопка «🛠 Поддержка» на каждом экране (как Kit.supportFab у остальных ботов)
  function supportFab() {
    if (document.querySelector('.sup-fab')) return;
    const b = h('button', { class: 'sup-fab', type: 'button', onclick: supportSheet, 'aria-label': 'Поддержка и пожелания' },
      h('span', { class: 'i', text: '🛠' }), h('span', { class: 't', text: 'Поддержка' }));
    document.body.appendChild(b);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', supportFab); else supportFab();
  function supportSheet() {
    let photo = null;
    const ta = h('textarea', { class: 'field sup-text', rows: '5', maxlength: '4000',
      placeholder: 'Например: «сводка пустая» или «добавьте ответы голосом»' });
    const prev = h('span', { class: 'sup-prev' });
    const file = h('input', { type: 'file', accept: 'image/*', hidden: true, onchange: async () => {
      const f = file.files && file.files[0];
      file.value = '';
      if (!f) return;
      try {
        photo = await shrink(f);
        prev.replaceChildren(h('img', { alt: '', src: 'data:image/jpeg;base64,' + photo }),
          h('button', { class: 'sup-x', 'aria-label': 'Убрать', text: '✕', onclick: () => { photo = null; prev.replaceChildren(); } }));
      } catch (e) { toast(e.message, true); }
    } });
    const send = h('button', { class: 'btn primary wide', onclick: async () => {
      const text = ta.value.trim();
      if (!text) { ta.focus(); haptic('warning'); return; }
      send.disabled = true;
      try {
        await api('/api/support', photo ? { text, photo } : { text });
        haptic('success');
        closeSheet();
        toast('Принял — работаю, ответ придёт в чат');
      } catch (e) { toast(e.message, true); haptic('error'); send.disabled = false; }
    } }, 'Отправить');
    openSheet(
      h('h3', { text: '🛠 Поддержка и пожелания' }),
      h('div', { class: 'sub', text: 'Напишите, что сломалось или чего не хватает — починю/добавлю.' }),
      ta,
      h('div', { class: 'sup-shot' }, h('button', { class: 'btn soft sm', onclick: () => file.click() }, '📎 Скриншот'), prev, file),
      send,
    );
  }

  function chatSheet(c) {
    openSheet(
      h('h3', { class: 'ell', text: c.name }),
      h('div', { class: 'sub', text: [c.kind, c.unread ? plural(c.unread, 'непрочитанное', 'непрочитанных', 'непрочитанных') : '',
        c.mentions ? plural(c.mentions, 'упоминание', 'упоминания', 'упоминаний') : ''].filter(Boolean).join(' · ') }),
      act('📬', 'Что пишут', 'кратко: кто что хочет и что ответить', () => ask({ preset: 'brief', chat: c.name, id: c.id })),
      act('🧠', 'Разбор как психолог', 'роли, тон, скрытое, советы', () => ask({ preset: 'psy', chat: c.name, id: c.id })),
    );
  }

  // ---------- блоки ----------
  // ---------- разговор: одна лента, как в мессенджере ----------
  const sameConv = (it) => (it.conv || '') === (S.conv || '');
  // вопросы и ответы текущего разговора по порядку (старые сверху)
  function convItems() {
    const items = (S.history || []).filter(sameConv).slice().reverse();
    const r = S.run;
    if (r && r.state !== 'work' && sameConv(r) && !items.some((it) => it.id === r.id)) items.push(r);  // ответ пришёл раньше истории
    return items;
  }
  const bubbleMe = (q, meta, cls) => h('div', { class: 'msg me ' + (cls || '') },
    h('div', { class: 'txt', text: q }), meta ? h('div', { class: 'msg-meta', text: meta }) : null);
  function bubbleAi(it, last) {
    const bits = [it.at, it.secs != null ? mmss(it.secs) : ''].filter(Boolean).join(' · ');
    return h('div', { class: 'msg ai' },
      it.state && it.state !== 'done' ? h('span', { class: 'tag err', text: it.state === 'stopped' ? 'остановлено' : 'ошибка' }) : null,
      answerBlock(it.html, 'h-' + it.id, !last),
      bits ? h('div', { class: 'msg-meta num', text: bits }) : null);
  }
  function bubbleWork(r) {
    const steps = r.steps || [];
    const shown = steps.slice(-5);
    const box = h('div', { class: 'msg ai work' },
      h('div', { class: 'run-head' }, star('spark'), h('b', { text: 'Работаю' }),
        h('span', { class: 't num', text: mmss(r.secs || 0) + (steps.length ? ` · ${plural(steps.length, 'шаг', 'шага', 'шагов')}` : '') })),
      h('ul', { class: 'steps' },
        shown.map((x, i) => h('li', { class: i === shown.length - 1 ? 'now' : '' }, h('i', { text: i === shown.length - 1 ? '◐' : '✓' }), h('span', { text: x }))),
        shown.length ? null : h('li', { class: 'now' }, h('i', { text: '◐' }), h('span', { text: 'Думаю' }))));
    if (r.confirm) {
      box.appendChild(h('div', { class: 'confirm' },
        h('b', null, h('i', { class: 'wait' }), 'Ждёт подтверждения в чате'),
        h('p', { text: r.confirm.replace(/\n{2,}/g, '\n') }),
        h('button', { class: 'btn sm soft', onclick: closeToChat }, icon('chat'), 'Перейти в чат')));
    }
    box.appendChild(h('div', { class: 'run-foot' }, h('button', { class: 'btn sm danger', onclick: stop }, S.queue.length ? 'Остановить всё' : 'Остановить')));
    return box;
  }

  // сама лента: пузыри текущего разговора, «работаю», очередь; «Раньше» — свёрнутый блок прежних разговоров
  function convoView() {
    const items = convItems();
    const run = busy() ? S.run : null;
    const out = [h('div', { class: 'section' }, h('h2', { text: 'Разговор' }),
      h('button', { class: 'text-btn', onclick: newConversation }, icon('plus'), 'Новый разговор'))];
    if (!items.length && !run && !S.queue.length && !S.pending.length) {
      out.push(h('div', { class: 'card empty' }, h('div', { class: 'big' }, star()), h('h3', { text: 'Поручите что угодно' }),
        h('p', { text: 'Уточнять можно, пока я думаю — вопросы встанут в очередь и пойдут следом, в этом же разговоре.' })));
    } else {
      const feed = h('div', { class: 'convo' });
      items.forEach((it, i) => feed.append(bubbleMe(it.q, it.source === 'app' ? '' : 'из чата'), bubbleAi(it, i === items.length - 1 && !run)));
      if (run) feed.append(bubbleMe(run.q), bubbleWork(run));
      S.queue.forEach((x, i) => feed.append(bubbleMe(x.q, `в очереди №${i + 1}`, 'wait')));
      S.pending.forEach((x) => feed.append(bubbleMe(x.q, 'отправляю…', 'wait')));
      out.push(feed);
    }
    const old = (S.history || []).filter((it) => !sameConv(it)).slice(0, 8);
    if (old.length) {
      out.push(h('button', { class: 'text-btn older', onclick: () => { S.older = !S.older; haptic('select'); paintLive(); } },
        S.older ? 'Скрыть прежние разговоры' : `Раньше · ${old.length}`));
      if (S.older) old.forEach((it) => out.push(h('article', { class: 'card h-item' },
        h('div', { class: 'h-head' }, h('span', { class: 'src', text: it.source === 'app' ? '📱' : '💬' }),
          h('div', { class: 'grow' }, h('div', { class: 'q clamp2' }, it.q, it.state !== 'done' ? h('span', { class: 'tag err', text: it.state === 'stopped' ? 'стоп' : 'ошибка' }) : null),
            h('div', { class: 'h-meta num', text: [it.at, it.secs != null ? mmss(it.secs) : ''].filter(Boolean).join(' · ') }))),
        answerBlock(it.html, 'o-' + it.id))));
    }
    return out;
  }

  // «Новый разговор»: контекст Claude сбрасывается, очередь чистится — спрашиваем явно
  function newConversation() {
    haptic('warning');
    const doit = async (ok) => {
      if (!ok) return;
      try {
        await api('/api/new');
        S.queue = []; S.older = false;
        haptic('success'); toast('Начинаем с чистого листа ✦');
        load(true);
      } catch (e) { toast(e.message, true); haptic('error'); }
    };
    const text = 'Начать новый разговор? Секретарь забудет, о чём вы говорили выше, а вопросы в очереди пропадут.';
    if (tg && tg.showConfirm && tg.platform !== 'unknown') tg.showConfirm(text, doit); else doit(window.confirm(text));
  }

  // автопрокрутка к новому пузырю — только когда в ленте что-то изменилось, не на каждый опрос
  function followFeed(force) {
    if (S.tab !== 'ask') return;
    const r = S.run;
    const sig = [convItems().length, r ? r.id + r.state : '', S.queue.length, S.pending.length].join('|');
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

  function chatRow(c) {
    return h('button', { class: 'row', onclick: () => chatSheet(c) },
      h('span', { class: 'ava', style: gradFor(c.id), text: c.initials }),
      h('div', { class: 'main' },
        h('div', { class: 'l1' }, h('span', { class: 'name ell', text: c.name }), h('span', { class: 'time', text: c.when })),
        h('div', { class: 'l2' }, h('span', { class: 'prev ell', text: c.preview || c.kind }),
          c.mentions ? h('span', { class: 'badge at', text: '@' }) : null,
          c.unread ? h('span', { class: 'badge' + (c.kind === 'канал' ? ' mute' : ''), text: c.unread > 999 ? '999+' : String(c.unread) }) : null)));
  }

  function top(eyebrow, title, right) {
    return h('header', { class: 'top' },
      h('div', { class: 'logo' }, star()),
      h('div', { class: 'grow' }, h('div', { class: 'eyebrow ell', text: eyebrow }), h('h1', { class: 'title ell', text: title })),
      right || null);
  }

  // ---------- экраны ----------
  // число + подпись в нужном падеже (подпись короткая — влезает и на 320px)
  function stat(n, one, few, many) {
    const word = plural(n, one, few, many).replace(/^\d+ /, '');
    return h('div', { class: 'stat' }, h('b', { class: 'num', text: n }), h('span', { text: word }));
  }

  // на главной — только строка «Секретарь работает», ответ и разговор — на вкладке «Поручение»
  function workNote() {
    if (!working()) return null;
    const n = S.queue.length;
    return h('button', { class: 'card note work-note', onclick: () => go('ask') }, star('spark'),
      h('div', { class: 'grow' }, h('b', { text: busy() ? 'Секретарь работает' : 'Секретарь берёт следующий вопрос' }),
        h('div', { class: 'hint small', text: (busy() ? S.run.q : '') + (n ? ` · в очереди ${n}` : '') })),
      h('span', { class: 'text-btn', text: 'Открыть' }));
  }

  function homeView() {
    const st = S.state;
    const refresh = h('button', { class: 'icon-btn' + (S.refreshing ? ' spin' : ''), 'aria-label': 'Обновить', onclick: () => load(true) }, icon('refresh'));
    if (!st) {
      return [top('Секретарь', 'Загружаю…', refresh), h('div', { class: 'sk sk-hero' }),
        h('div', { class: 'quick' }, [0, 1, 2].map(() => h('div', { class: 'sk sk-tile' }))),
        h('div', { class: 'section' }, h('h2', { text: 'Непрочитанное' })),
        h('div', { class: 'list' }, [0, 1, 2, 3].map(() => h('div', { class: 'row' }, h('span', { class: 'ava sk' }),
          h('div', { class: 'main' }, h('div', { class: 'sk sk-line', style: 'width:55%' }), h('div', { class: 'sk sk-line', style: 'width:85%' })))))];
    }
    const me = st.me;
    const hero = h('section', { class: 'hero' }, star('star-bg'),
      h('div', { class: 'hero-row' }, me
        ? h('span', { class: 'pill' }, h('i'), h('span', { class: 'ell', text: 'Аккаунт подключён' }))
        : h('span', { class: 'pill off' }, h('i'), h('span', { class: 'ell', text: 'Аккаунт не подключён' }))));
    if (me) {
      hero.append(h('div', { class: 'hero-name ell', text: me.name }),
        me.username ? h('div', { class: 'hero-sub ell', text: '@' + me.username }) : null,
        h('div', { class: 'stats' },
          stat(st.unread, 'новый', 'новых', 'новых'),
          stat(st.mentions, 'упоминание', 'упоминания', 'упоминаний'),
          stat(st.jobs.length, 'расписание', 'расписания', 'расписаний')));
    } else {
      hero.append(h('div', { class: 'hero-name', text: 'Подключите Telegram' }),
        h('div', { class: 'hero-sub', text: 'Чтобы я вёл ваши чаты, нажмите /login в чате. На вопросы отвечаю и без этого.' }),
        h('button', { class: 'btn glass', onclick: closeToChat }, icon('chat'), 'Открыть чат'));
    }
    const tile = (em, title, sub, fn) => h('button', { class: 'tile', onclick: fn },
      h('span', { class: 'em', text: em }), h('b', { text: title }), h('span', { text: sub }));
    const out = [
      top('Секретарь', st.first ? `${st.hello}, ${st.first}` : st.hello, refresh),
      hero,
      h('div', { id: 'runbox' }, workNote()),
      h('div', { class: 'section' }, h('h2', { text: 'Быстрые поручения' })),
      h('div', { class: 'quick' },
        tile('📬', 'Что я пропустил', 'сводка непрочитанного', () => ask({ preset: 'missed' }).then((ok) => ok && go('ask'))),
        tile('🗂', 'Навести порядок', 'папки и архив', () => ask({ preset: 'sort' }).then((ok) => ok && go('ask'))),
        tile('🧠', 'Разбор чата', 'как психолог', () => psySheet())),
      h('button', { class: 'btn primary', style: 'margin-top:10px;width:100%', onclick: () => go('ask') },
        '✨ Поручить своё задание'),
    ];
    if (me) {
      out.push(h('div', { class: 'section' }, h('h2', { text: 'Непрочитанное' }), st.chats.length ? h('span', { class: 'count num', text: st.unread }) : null));
      if (st.error) out.push(h('div', { class: 'card hint small', text: st.error }));
      if (st.chats.length) {
        const limit = S.open.allChats ? st.chats.length : 8;
        const list = h('div', { class: 'list' }, st.chats.slice(0, limit).map(chatRow));
        if (st.chats.length > limit) {
          list.appendChild(h('button', { class: 'row more-row', text: `Показать ещё ${st.chats.length - limit}`, onclick: () => { S.open.allChats = true; paint(); } }));
        }
        out.push(list);
      } else if (!st.error) {
        out.push(h('div', { class: 'card empty' }, h('div', { class: 'big' }, star()), h('h3', { text: 'Всё прочитано' }),
          h('p', { text: 'Новых сообщений нет. Загляну снова, когда напишут.' })));
      }
    }
    out.push(h('div', { class: 'section' }, h('h2', { text: 'Поддержка' })),
      h('div', { class: 'list' }, h('button', { class: 'row', onclick: supportSheet },
        h('span', { class: 'ava', style: gradFor(4), text: '🛠' }),
        h('div', { class: 'main' }, h('div', { class: 'l1' }, h('span', { class: 'name ell', text: 'Поддержка и пожелания' })),
          h('div', { class: 'l2' }, h('span', { class: 'prev ell', text: 'Ошибка или идея — починю/добавлю' }))))));
    return out;
  }

  function autosize(ta) {
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, window.innerHeight * 0.4) + 'px';
  }

  function askView() {
    buildDock();
    return [top('Секретарь', 'Поручение'), h('div', { id: 'convo' }, convoView())];
  }

  // поле поручения закреплено над вкладками (#dock) и всегда активно: пока Секретарь думает, вопрос встаёт в очередь
  let dockHint = null;
  let dockSend = null;
  function buildDock() {
    const st = S.state || {};
    const ta = h('textarea', { rows: '1', maxlength: '4000', placeholder: 'Поручение или уточнение…', 'aria-label': 'Поручение' });
    ta.value = S.draft;
    dockSend = h('button', { class: 'send', 'aria-label': 'Отправить' }, icon('send'));
    dockHint = h('div', { class: 'hint small dock-hint' });
    const sync = () => { S.draft = ta.value; syncDock(); autosize(ta); padDock(); };
    ta.addEventListener('input', sync);
    ta.addEventListener('focus', () => document.body.classList.add('typing'));
    ta.addEventListener('blur', () => document.body.classList.remove('typing'));
    // тап по «Отправить» не должен снимать фокус с поля: иначе вкладки возвращаются, раскладка прыгает и тап теряется
    dockSend.addEventListener('mousedown', (ev) => ev.preventDefault());
    dockSend.addEventListener('click', async () => {
      const text = ta.value.trim();
      if (!text) return;
      ta.blur();
      S.draft = ''; ta.value = ''; sync();  // пузырь уже в ленте; не ушло — вернём текст
      if (!(await ask({ text })) && !S.draft) { S.draft = text; ta.value = text; sync(); }
    });
    const examples = (st.examples || []).slice(0, 4);
    dockEl.replaceChildren(h('div', { class: 'dock-in' },
      examples.length ? h('div', { class: 'chips' }, examples.map((e) => h('button', {
        class: 'chip', text: e, onclick: () => { S.draft = e; ta.value = e; sync(); ta.focus(); haptic('select'); },
      }))) : null,
      h('div', { class: 'dock-bar' }, ta, dockSend), dockHint));
    document.body.classList.add('has-dock');
    requestAnimationFrame(() => { autosize(ta); syncDock(); padDock(); });
  }
  function syncDock() {
    if (!dockSend) return;
    dockSend.disabled = !S.draft.trim() || queueFull();
    if (dockHint) {
      dockHint.textContent = queueFull() ? `В очереди уже ${QUEUE_MAX} — дождитесь ответа или остановите` : working() ? 'Добавлю в очередь — отвечу следом' : '';
      dockHint.classList.toggle('hidden', !dockHint.textContent);
    }
  }
  function padDock() { document.body.style.setProperty('--dock-h', dockEl.firstChild ? dockEl.offsetHeight + 'px' : '0px'); }
  function clearDock() {
    dockEl.replaceChildren();
    dockSend = dockHint = null;
    document.body.classList.remove('has-dock', 'typing');
  }

  function jobsView() {
    const js = (S.state && S.state.jobs) || [];
    const out = [top('Секретарь', 'Расписание', js.length ? h('span', { class: 'hint small num', text: plural(js.length, 'задача', 'задачи', 'задач') }) : null)];
    if (!S.state) { out.push(h('div', { class: 'card' }, h('div', { class: 'sk sk-line' }), h('div', { class: 'sk sk-line', style: 'width:70%' }))); return out; }
    const example = 'Каждый будний день в 7:00 пишите маме „доброе утро“';
    if (!js.length) {
      out.push(h('div', { class: 'card empty' }, h('div', { class: 'big' }, star()), h('h3', { text: 'Пока пусто' }),
        h('p', { text: `Попросите, например: «${example}» — я буду отправлять сам и присылать отчёт.` }),
        h('button', { class: 'btn primary', onclick: () => { S.draft = example; go('ask'); } }, 'Написать поручение')));
      return out;
    }
    for (const j of js) {
      out.push(h('article', { class: 'card job' },
        h('div', { class: 'clock' }, h('b', { class: 'num', text: j.time }), h('span', { text: j.period })),
        h('div', { class: 'grow' },
          h('div', { class: 'to ell', text: '→ ' + j.to }),
          h('div', { class: 'what clamp2' }, j.text ? `«${j.text}»` : [h('em', { text: '✦ ', title: 'сочиняю каждый раз заново' }), j.ai]),
          h('div', { class: 'meta num' }, h('span', { text: `дальше ${j.next}` }), h('span', { text: `отправлено ${j.sent}` }))),
        h('button', { class: 'icon-btn', 'aria-label': 'Удалить', onclick: () => delJob(j) }, icon('trash'))));
    }
    out.push(h('div', { class: 'card note' }, h('span', { class: 'em', text: '✦' }),
      h('div', { class: 'grow' }, 'Добавить — поручением, например: ', h('b', { text: `«${example}»` }),
        js.some((j) => j.ai) ? '. Со звёздочкой ✦ — текст сочиняю каждый раз заново.' : '')));
    return out;
  }

  function delJob(j) {
    haptic('warning');
    const doit = async (ok) => {
      if (!ok) return;
      try {
        const r = await api('/api/jobs/delete', { id: j.id });
        S.state.jobs = r.jobs;
        haptic('success');
        toast('Расписание удалено');
        paint();
      } catch (e) { toast(e.message, true); haptic('error'); }
    };
    const text = `Удалить расписание?\n\n${j.time}, ${j.period} → ${j.to}`;
    if (tg && tg.showConfirm && tg.platform !== 'unknown') tg.showConfirm(text, doit); else doit(window.confirm(text));
  }

  function memoryView() {
    const out = [top('Секретарь', 'Память')];
    out.push(h('div', { class: 'card note' }, h('span', { class: 'em', text: '📝' }),
      h('div', { class: 'grow', text: 'Учитываю это в каждом ответе. В чате можно просто сказать: «запомните, что…».' })));
    if (S.memory == null) {
      out.push(h('div', { class: 'card' }, [90, 70, 80, 40].map((w) => h('div', { class: 'sk sk-line', style: `width:${w}%` }))));
      loadMemory();
      return out;
    }
    const ta = h('textarea', { maxlength: '20000', placeholder: '— тренировки по боксу: вт и чт в 18:00\n— маму зовут Ольга\n— отвечать Пете коротко', 'aria-label': 'Память' });
    ta.value = S.memDraft != null ? S.memDraft : S.memory;
    const count = h('span', { class: 'hint num' });
    const save = h('button', { class: 'btn primary sm' }, 'Сохранить');
    const undo = h('button', { class: 'btn soft sm' }, 'Отменить');
    const sync = () => {
      S.memDraft = ta.value;
      S.memDirty = ta.value !== S.memory;
      save.disabled = !S.memDirty;
      undo.classList.toggle('hidden', !S.memDirty);
      count.textContent = `${ta.value.length.toLocaleString('ru-RU')} / 20 000`;
    };
    ta.addEventListener('input', sync);
    ta.addEventListener('focus', () => document.body.classList.add('typing'));
    ta.addEventListener('blur', () => document.body.classList.remove('typing'));
    undo.addEventListener('click', () => { ta.value = S.memory; sync(); haptic('select'); });
    save.addEventListener('click', async () => {
      save.disabled = true;
      try {
        const r = await api('/api/memory/save', { text: ta.value });
        S.memory = r.text; S.memDraft = null; ta.value = r.text; sync();
        haptic('success'); toast('Сохранено ✓');
      } catch (e) { toast(e.message, true); haptic('error'); sync(); }
    });
    sync();
    out.push(h('section', { class: 'card mem' }, ta, h('div', { class: 'mem-bar' }, count, undo, save)));
    return out;
  }

  async function loadMemory() {
    try { const r = await api('/api/memory'); S.memory = r.text; if (S.tab === 'memory') paint(); } catch (e) { toast(e.message, true); }
  }

  // ---------- вкладки и отрисовка ----------
  const TABS = [['home', 'Главная', 'home'], ['ask', 'Поручение', 'ask'], ['jobs', 'Расписание', 'clock'], ['memory', 'Память', 'note']];

  function paintTabs() {
    tabsEl.replaceChildren(...TABS.map(([id, label, ic]) => h('button', {
      class: 'tab' + (S.tab === id ? ' on' : ''), 'aria-current': S.tab === id ? 'page' : null, onclick: () => go(id),
    }, h('span', { class: 'pad' }, icon(ic)), h('span', { text: label }), id === 'ask' && busy() && S.tab !== 'ask' ? h('i', { class: 'dot' }) : null)));
  }

  function paint(animate) {
    if (S.gate) {
      view.replaceChildren(h('div', { class: 'gate' }, h('div', { class: 'empty' }, h('div', { class: 'big' }, star()),
        h('h3', { text: 'Секретарь' }), h('p', { text: S.gate }))));
      tabsEl.classList.add('hidden');
      clearDock();
      return;
    }
    const make = { home: homeView, ask: askView, jobs: jobsView, memory: memoryView }[S.tab];
    if (S.tab !== 'ask') clearDock();
    view.replaceChildren(...[].concat(make()).filter(Boolean));
    view.classList.toggle('enter', !!animate);
    paintTabs();
  }

  // часть экрана без перерисовки поля ввода (чтобы не сбить набор текста)
  function paintLive() {
    if (S.tab === 'ask') {
      const cb = document.getElementById('convo');
      if (cb) cb.replaceChildren(...convoView());
      syncDock();
      padDock();
      paintTabs();
      followFeed();
    } else if (S.tab === 'memory') {
      paintTabs();
    } else if (!sheetRoot.classList.contains('open')) {
      paint();
    } else {
      const rb = document.getElementById('runbox');
      if (rb) rb.replaceChildren(...[workNote()].filter(Boolean));
      paintTabs();
    }
  }

  function go(tab) {
    if (tab === S.tab) { window.scrollTo({ top: 0, behavior: 'smooth' }); return; }
    haptic('select');
    S.tab = tab;
    try { sessionStorage.setItem('tab', tab); } catch (e) { /* */ }
    window.scrollTo(0, 0);
    paint(true);
    if (tab === 'ask') followFeed(true);  // как в мессенджере: открыли — видим последнее сообщение
  }

  // ---------- данные ----------
  async function load(fresh) {
    S.refreshing = true;
    if (fresh) { haptic('light'); paintLive(); }
    try {
      const st = await api('/api/state', { fresh: !!fresh });
      S.state = st;
      S.history = st.history || [];
      S.run = st.run;
      S.queue = st.queue || [];
      S.conv = st.conv || '';
      S.gate = '';
    } catch (e) {
      if (e.status === 401 || e.status === 403) S.gate = e.status === 401 ? 'Откройте приложение из чата с Секретарём.' : e.message;
      else toast(e.message, true);
    }
    S.refreshing = false;
    paintLive();
    poll();
  }

  async function poll(delay) {
    clearTimeout(S.timer);
    const live = working();
    S.timer = setTimeout(async () => {
      if (document.hidden) { poll(); return; }
      if (!live) { load(false); return; }
      try {
        const r = await api('/api/run', { history: true });
        const was = S.run;
        S.run = r.run;
        S.queue = r.queue || [];
        if (r.conv) S.conv = r.conv;
        if (r.history) S.history = r.history;
        // закончился шаг: тот же run стал done/error, либо очередь уже запустила следующий вопрос
        if (was && was.state === 'work' && (!r.run || r.run.id !== was.id || r.run.state !== 'work')) {
          const fin = (r.history || []).find((x) => x.id === was.id) || (r.run && r.run.id === was.id ? r.run : null);
          const ok = !fin || fin.state === 'done';
          haptic(ok ? 'success' : 'error');
          if (ok) toast('Готово ✦ Ответ и в чате');
          if (!working()) { load(false); return; }
        }
        paintLive();
      } catch (e) { /* сеть моргнула — попробуем снова */ }
      poll();
    }, delay != null ? delay : live ? 1500 : 30000);
  }

  // ---------- запуск ----------
  function theme() {
    const light = tg && tg.colorScheme ? tg.colorScheme === 'light' : window.matchMedia('(prefers-color-scheme: light)').matches;
    document.body.classList.toggle('light', light);
    try { tg.setHeaderColor('secondary_bg_color'); tg.setBackgroundColor('secondary_bg_color'); if (tg.setBottomBarColor) tg.setBottomBarColor('secondary_bg_color'); } catch (e) { /* */ }
  }

  if (tg) {
    try { tg.ready(); tg.expand(); if (tg.disableVerticalSwipes) tg.disableVerticalSwipes(); } catch (e) { /* */ }
    tg.onEvent && tg.onEvent('themeChanged', theme);
    try { tg.BackButton.onClick(closeSheet); } catch (e) { /* */ }
  }
  theme();
  try { const t = sessionStorage.getItem('tab'); if (t && TABS.some((x) => x[0] === t)) S.tab = t; } catch (e) { /* */ }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) load(false); });
  paint(true);
  load(false);
})();
