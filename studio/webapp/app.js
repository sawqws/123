// Студия — мини-приложение: идея → вопросы → имя → план → сборка; «Мои боты». Общие функции — kit.js.
let S = null;
let tab = 'new';
let busyAction = false;

const ACTIVE = ['think', 'plan_wait', 'build'];

async function load() {
  try { render(await api('state')); } catch (e) { toast(e.message, true); }
}

async function act(name, body, okText) {
  if (busyAction) return;
  busyAction = true;
  try {
    render(await api(name, body || {}));
    haptic('success');
    if (okText) toast(okText);
  } catch (e) { toast(e.message, true); } finally { busyAction = false; }
}

function render(s) {
  S = s;
  if (s.sites_only) {  // «Студия сайтов»: шапка и вкладка — про сайты
    document.title = 'Сайты';
    document.querySelector('.top .title').innerHTML = '<span class="emo">🌐</span> Сайты';
    document.querySelector('.subtitle').textContent = 'Опиши сайт — уточню, покажу план и соберу сам.';
    const t = document.querySelector('[data-tab="bots"]');
    if (t) t.textContent = 'Мои сайты';
    const nt = document.querySelector('[data-tab="new"]');
    if (nt) nt.textContent = 'Новый сайт';
  }
  const n = s.bots.length, word = s.sites_only ? ['сайт', 'сайта', 'сайтов'] : ['бот', 'бота', 'ботов'];
  $('count').textContent = n ? n + ' ' + (n === 1 ? word[0] : n < 5 ? word[1] : word[2]) : '';
  renderNew(s);
  renderBots(s);
}

function busyBanner(s) {
  if (!s.busy) return '';
  const b = s.bots.find((x) => x.slug === s.busy.slug);
  return `<div class="card"><div class="thinking">Дорабатываю «${esc(b ? b.name : s.busy.slug)}»</div>` +
    `<div class="meta muted" style="margin-top:6px">${esc(s.busy.text)}</div></div>`;
}

function renderNew(s) {
  const d = s.draft;
  const el = $('new');
  let h = busyBanner(s);
  const st = d.stage;
  if (!st || st === 'done') {
    if (st === 'done' && d.result && d.result.name) {
      const r = d.result;
      h += `<div class="card hero fade"><span class="emoji">🎉</span><h2>«${esc(r.name)}» готов</h2>` +
        `<div class="muted">${esc(r.about || '')}</div>` +
        (r.url ? `<button class="btn primary" style="margin-top:16px" data-link="${esc(r.url)}">🌐 Открыть сайт</button>` +
          `<p class="muted" style="font-size:14px">Сайт пока закрыт. Защитить паролем или выпустить для всех — во вкладке «Мои».</p>` : '') +
        (r.username ? `<button class="btn primary" style="margin-top:16px" data-open="${esc(r.username)}">Открыть @${esc(r.username)}</button>` : '') + '</div>';
    }
    const so = !!s.sites_only;  // «Студия сайтов» (STUDIO_MODE=sites): только сайты
    h += `<div class="card fade"><h2>${so ? 'Какой сайт нужен?' : 'Какой бот или сайт нужен?'}</h2>` +
      `<textarea id="idea" rows="4" placeholder="${so ? 'Например: сайт-визитка для фотографа с работами и контактами' : 'Например: напоминает пить воду и считает стаканы за день'}"></textarea>` +
      `<div class="chips" style="margin:12px 0 0">${s.examples.map((x) => `<button class="chip" data-example="${esc(x)}">${esc(x)}</button>`).join('')}</div>` +
      `<button class="btn primary" id="start" style="margin-top:12px">Дальше →</button></div>` +
      `<p class="muted" style="font-size:14px;margin:0 4px">${so ? 'Я задам пару вопросов, ты дашь имя и подтвердишь план. Дальше соберу сайт сам, а потом закроешь его паролем или откроешь для всех.' : 'Я задам пару вопросов, ты дашь имя и подтвердишь план. Дальше всё сам: код, мини-приложение, аватар и запуск.'}</p>`;
  } else if (st === 'think') {
    h += idea(d) + `<div class="card fade"><div class="thinking">Думаю, что уточнить…</div></div>`;
  } else if (st === 'ask') {
    const q = d.qs[d.qi];
    h += idea(d) + answered(d);
    if (q) {
      h += `<div class="card fade"><div class="step-no">Вопрос ${d.qi + 1} из ${d.qs.length}</div>` +
        `<div class="question">${esc(q.q)}</div>` +
        q.options.map((o) => `<button class="btn" data-answer="${esc(o)}">${esc(o)}</button>`).join('') +
        `<input id="own" placeholder="Свой вариант…" style="margin-top:12px" enterkeyhint="send">` +
        `<div class="actions" style="margin-top:8px"><button class="btn ghost" data-answer="">Пропустить</button>` +
        `<button class="btn ghost" id="enough">Хватит, дальше</button></div></div>`;
    }
  } else if (st === 'name') {
    h += idea(d) + answered(d) + `<div class="card fade"><h2>Как назовём бота?</h2>` +
      `<div class="chips">${(d.names || []).map((n) => `<button class="chip" data-name="${esc(n)}">${esc((d.emoji || '') + ' ' + n)}</button>`).join('')}</div>` +
      `<input id="name" placeholder="Или своё имя…" maxlength="40" enterkeyhint="done">` +
      `<button class="btn primary" id="set-name">Дальше →</button></div>`;
  } else if (st === 'plan_wait') {
    h += idea(d) + `<div class="card fade"><div class="thinking">${d.plan ? 'Переделываю план…' : 'Составляю план «' + esc(d.name) + '»…'}</div></div>`;
  } else if (st === 'plan') {
    h += planCard(d) + `<div class="card"><h2>Что поменять?</h2>` +
      `<textarea id="fix" rows="2" placeholder="Например: добавь недельную статистику"></textarea>` +
      `<button class="btn" id="correct">Поправить план</button>` +
      `<button class="btn primary" id="confirm">✅ Создать бота</button>` +
      `<button class="btn danger" id="cancel">Отменить</button></div>`;
  } else if (st === 'build') {
    const steps = (d.steps || []).slice(-7);
    const mins = d.started ? Math.floor((Date.now() / 1000 - d.started) / 60) : 0;
    h += `<div class="card fade"><div class="step-no">${mins} мин · обычно 3–10 минут</div>` +
      `<h2 style="margin-top:4px">${esc(d.emoji || '')} Создаю «${esc(d.name)}»</h2>` +
      `<ul class="steps">${steps.map((x, i) => `<li class="${i === steps.length - 1 ? 'now' : ''}">${esc(x)}</li>`).join('') || '<li class="now">начинаю</li>'}</ul>` +
      `<p class="muted" style="font-size:14px;margin:12px 0 0">Можно закрыть — напишу в чат, когда будет готово.</p>` +
      (d.newbot ? `<button class="btn primary" style="margin-top:12px" data-link="${esc(d.newbot)}">⚡ Создать бота в Telegram</button>` : '') + '</div>';
  } else if (st === 'token') {
    h += d.newbot
      ? `<div class="card fade"><h2>⚡ Последний шаг</h2><p class="muted">Код готов. Нажми и подтверди окно Telegram — ` +
        `бот «${esc(d.name)}» создастся, а я сразу его запущу.</p>` +
        `<button class="btn primary" data-link="${esc(d.newbot)}">⚡ Создать бота</button></div>`
      : `<div class="card fade"><h2>🔑 Последний шаг</h2><p class="muted">Код готов. Создай бота в @BotFather ` +
        `(имя «${esc(d.name)}») и пришли токен в чат Студии — запущу сам.</p>` +
        `<button class="btn primary" data-tg="BotFather">Открыть @BotFather</button></div>`;
  } else if (st === 'failed') {
    h += `<div class="card fade"><h2>😕 Не получилось</h2><div class="err">${esc(d.error || '')}</div>` +
      `<button class="btn primary" id="confirm" style="margin-top:12px">🔁 Собрать ещё раз</button>` +
      `<button class="btn danger" id="cancel">Удалить черновик</button></div>`;
  }
  const focus = document.activeElement && document.activeElement.id;
  const keep = {};
  ['idea', 'own', 'name', 'fix'].forEach((id) => { const x = document.getElementById(id); if (x) keep[id] = x.value; });
  if (el.dataset.html !== h) {
    el.innerHTML = h;
    el.dataset.html = h;
    Object.keys(keep).forEach((id) => { const x = document.getElementById(id); if (x) x.value = keep[id]; });
    if (focus && document.getElementById(focus)) document.getElementById(focus).focus();
  }
}

function idea(d) {
  return `<div class="card"><div class="step-no">Идея</div><div style="margin-top:4px">${esc(d.idea)}</div></div>`;
}

function answered(d) {
  const done = d.qs.filter((q, i) => i < d.qi);
  if (!done.length) return '';
  return `<div class="card">${done.map((q) => `<div class="qa">${esc(q.q)}<br><b>→ ${esc(q.a || 'на твоё усмотрение')}</b></div>`).join('<div style="height:8px"></div>')}</div>`;
}

function planCard(d) {
  const p = d.plan || {};
  const list = (xs) => `<ul>${(xs || []).map((x) => `<li>${esc(x)}</li>`).join('')}</ul>`;
  if (d.kind === 'site') {
    return `<div class="card plan fade"><h2>${esc(d.emoji || '')} ${esc(d.name)}</h2><div class="summary">${esc(p.summary || '')}</div>` +
      `<h3>📄 Страницы</h3>${list(p.pages)}<h3>🎨 Стиль</h3>${list(p.style)}` +
      (p.features && p.features.length ? `<h3>⚙️ Умеет</h3>${list(p.features)}` : '') +
      `<div class="muted">Готовый сайт сначала закрыт — потом решишь: пароль или для всех.</div></div>`;
  }
  return `<div class="card plan fade"><h2>${esc(d.emoji || '')} ${esc(d.name)}</h2><div class="summary">${esc(p.summary || '')}</div>` +
    `<h3>💬 В чате</h3>${list(p.chat)}<h3>📱 Мини-приложение</h3>${list(p.app)}` +
    (p.auto && p.auto.length ? `<h3>⏰ Пишет сам</h3>${list(p.auto)}` : '') +
    `<h3>🔗 Адрес</h3><div class="muted">@${esc(p.username || '')}_bot, если свободно</div></div>`;
}

function renderBots(s) {
  const el = $('bots');
  let h = busyBanner(s);
  if (!s.bots.length) {
    h += '<div class="empty"><span class="emoji">🧩</span>Пока ни одного бота.<br>Опиши идею во вкладке «Новый бот».</div>';
  } else {
    h += '<div class="list fade">' + s.bots.map((b) =>
      `<button class="row" data-bot="${esc(b.slug)}"><span class="icon">${esc(b.emoji || '🧩')}</span>` +
      `<span class="main"><span class="name">${esc(b.name)}</span><span class="meta">${b.username ? '@' + esc(b.username) + ' · ' : ''}${esc(b.summary || '')}</span></span>` +
      `<span class="dot ${b.status === 'online' ? 'on' : b.status ? 'off' : ''}"></span></button>`).join('') + '</div>';
  }
  if (el.dataset.html !== h) { el.innerHTML = h; el.dataset.html = h; }
}

function openBot(slug) {
  const b = S.bots.find((x) => x.slug === slug);
  if (!b) return;
  if (b.kind === 'site') return openSite(b);
  const status = b.status === 'online' ? '<span class="badge ok">работает</span>' : b.status ? '<span class="badge bad">не работает</span>' : '<span class="badge">не запущен</span>';
  const sh = sheet(`<div class="sheet-icon">${esc(b.emoji || '🧩')}</div><h2>${esc(b.name)}</h2>` +
    `<div style="margin-bottom:8px">${status}${b.username ? ' <span class="muted">@' + esc(b.username) + '</span>' : ''}</div>` +
    `<p class="muted">${esc(b.summary || '')}</p>` +
    (b.username ? `<button class="btn primary" data-open="${esc(b.username)}">▶️ Открыть бота</button>` : '') +
    `<textarea id="bot-fix" rows="3" placeholder="Что поменять? Например: добавь кнопку «Сброс»" style="margin-top:12px"></textarea>` +
    `<button class="btn" id="bot-fix-go">✏️ Отправить правку</button>` +
    `<div class="actions" style="margin-top:8px"><button class="btn" id="bot-restart">🔄 Перезапустить</button>` +
    `<button class="btn danger" id="bot-del">🗑 Удалить</button></div>`);
  sh.querySelector('#bot-fix-go').onclick = async () => {
    const text = sh.querySelector('#bot-fix').value.trim();
    if (!text) { toast('Напиши, что поменять', true); return; }
    await act('fix', { slug, text }, 'Дорабатываю — напишу в чат');
    sh.remove();
  };
  sh.querySelector('#bot-restart').onclick = () => act('restart', { slug }, 'Перезапустил');
  sh.querySelector('#bot-del').onclick = async () => {
    if (!(await ask(`Удалить «${b.name}»? Код и данные сотрутся.`))) return;
    await act('delete', { slug }, 'Удалил');
    sh.remove();
  };
}

const ACCESS = { private: '🔑 по ссылке-ключу', password: '🔒 по паролю', public: '🌍 открыт всем' };

function openSite(b) {
  const slug = b.slug;
  const sh = sheet(`<div class="sheet-icon">${esc(b.emoji || '🌐')}</div><h2>${esc(b.name)}</h2>` +
    `<div style="margin-bottom:8px"><span class="badge ${b.access === 'public' ? 'ok' : ''}">${ACCESS[b.access] || ''}</span></div>` +
    `<p class="muted">${esc(b.summary || '')}</p>` +
    (b.password ? `<p>Пароль: <b>${esc(b.password)}</b></p>` : '') +
    `<button class="btn primary" data-link="${esc(b.url || '')}">🌐 Открыть сайт</button>` +
    `<div class="actions" style="margin-top:8px">` +
    (b.access !== 'password' ? `<button class="btn" id="s-pass">🔒 Защитить паролем</button>` : '') +
    (b.access !== 'public' ? `<button class="btn" id="s-pub">🌍 Выпустить для всех</button>` : `<button class="btn" id="s-priv">🔑 Закрыть снова</button>`) + `</div>` +
    `<textarea id="bot-fix" rows="3" placeholder="Что поменять на сайте? Например: сделай шапку тёмной" style="margin-top:12px"></textarea>` +
    `<button class="btn" id="bot-fix-go">✏️ Отправить правку</button>` +
    `<div class="actions" style="margin-top:8px"><button class="btn danger" id="bot-del">🗑 Удалить</button></div>`);
  const mode = async (m, msg) => { await act('access', { slug, mode: m }, msg); sh.remove(); };
  const q = (id) => sh.querySelector(id);
  if (q('#s-pass')) q('#s-pass').onclick = () => mode('password', 'Закрыл паролем');
  if (q('#s-priv')) q('#s-priv').onclick = () => mode('private', 'Закрыл ключом');
  if (q('#s-pub')) q('#s-pub').onclick = async () => {
    if (await ask('Выпустить сайт для всех? Его откроет любой, у кого есть адрес, и найдут поисковики.')) mode('public', 'Открыт всем');
  };
  q('#bot-fix-go').onclick = async () => {
    const text = q('#bot-fix').value.trim();
    if (!text) { toast('Напиши, что поменять', true); return; }
    await act('fix', { slug, text }, 'Дорабатываю — напишу в чат');
    sh.remove();
  };
  q('#bot-del').onclick = async () => {
    if (!(await ask(`Удалить сайт «${b.name}»?`))) return;
    await act('delete', { slug }, 'Удалил');
    sh.remove();
  };
}

function startIdea() {
  const x = $('idea');
  const text = x ? x.value.trim() : '';
  if (text.length < 3) { toast('Опиши идею парой слов', true); return; }
  act('idea', { text });
}

function sendOwn() {
  const text = $('own').value.trim();
  if (!text) { toast('Впиши ответ или выбери вариант', true); return; }
  act('answer', { text, i: S.draft.qi });
}

function sendName(text) {
  text = (text || ($('name') && $('name').value) || '').trim();
  if (!text) { toast('Впиши имя или выбери вариант', true); return; }
  act('name', { text });
}

document.addEventListener('click', (ev) => {
  const t = ev.target.closest('button');
  if (!t) return;
  haptic('tap');
  if (t.dataset.example !== undefined) { $('idea').value = t.dataset.example; return; }
  if (t.dataset.answer !== undefined) return act('answer', { text: t.dataset.answer, i: S.draft.qi });
  if (t.dataset.name !== undefined) return sendName(t.dataset.name);
  if (t.dataset.bot) return openBot(t.dataset.bot);
  if (t.dataset.open) { if (tg) tg.openTelegramLink('https://t.me/' + t.dataset.open); return; }
  if (t.dataset.link) { if (tg && tg.openTelegramLink) tg.openTelegramLink(t.dataset.link); else window.open(t.dataset.link); return; }
  if (t.dataset.tg) { if (tg) tg.openTelegramLink('https://t.me/' + t.dataset.tg); return; }
  switch (t.id) {
    case 'start': return startIdea();
    case 'enough': return act('enough');
    case 'set-name': return sendName();
    case 'confirm': return act('confirm', {}, 'Начал собирать');
    case 'cancel': return ask('Удалить черновик?').then((ok) => ok && act('cancel'));
    case 'correct': {
      const text = $('fix').value.trim();
      if (!text) { toast('Напиши, что поменять', true); return; }
      return act('correct', { text });
    }
    default:
  }
});

document.addEventListener('keydown', (ev) => {
  if (ev.key !== 'Enter') return;
  if (ev.target.id === 'own') { ev.preventDefault(); sendOwn(); }
  if (ev.target.id === 'name') { ev.preventDefault(); sendName(); }
});

tabs($('tabs'), (name) => {
  tab = name;
  $('new').hidden = name !== 'new';
  $('bots').hidden = name !== 'bots';
  if (S) render(S);
});

load();
setInterval(() => {
  if (document.hidden || !S) return;
  const fast = ACTIVE.includes(S.draft.stage) || S.busy;
  const t = Date.now();
  if (fast || !window.lastLoad || t - window.lastLoad > 15000) { window.lastLoad = t; load(); }
}, 3000);
