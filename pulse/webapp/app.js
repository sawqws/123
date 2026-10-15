// Пульс — мини-приложение: что делают чаты Claude Code. Экраны: «Сессии» (сводка и карточки),
// «Подробно» (одна сессия), «Настройки» (уведомления в чат, скрытые, поддержка, «Перейти в чат»).
// Данные — GET api/state раз в 4 с, пока страница видна. Перерисовываются только изменившиеся блоки (patch),
// поэтому прокрутка, открытые «свёрнутые» блоки и экран сохраняются.
const { tg, $, esc, api, toast, haptic, busy } = Kit;

// ---------- состояние ----------
let S = null;            // ответ api/state
let gotAt = 0;           // когда пришёл (Date.now), чтобы «сейчас» шло между опросами
let view = { name: 'list', id: '' };
let shown = '';          // какой экран нарисован (для сброса и анимации появления)
let failed = 0;          // опросы подряд с ошибкой
const scrolls = {};      // прокрутка по экранам
const opened = new Set();  // открытые <details data-key>

const ORDER = { question: 0, working: 1, waiting: 2, offline: 3, ended: 4 };
const STALE = 180;       // без вестей дольше — «нет связи»
const ALERTS = [
  { key: 'done', ic: '✅', title: 'Закончил работу', meta: 'Чат ждёт твоего ответа' },
  { key: 'question', ic: '❓', title: 'Задал вопрос', meta: 'Нужен твой выбор' },
  { key: 'ctx', ic: '🧠', title: 'Контекст 60/80/90%', meta: 'Скоро сожмётся — пора в новый чат' },
  { key: 'offline', ic: '📡', title: 'Пропала связь', meta: 'Сессия молчит больше 3 минут' },
];

// ---------- мелочи ----------
const nowSec = () => (S ? S.now + (Date.now() - gotAt) / 1000 : Date.now() / 1000);
const plural = (n, a, b, c) => { const m = n % 10, h = n % 100; return m === 1 && h !== 11 ? a : m >= 2 && m <= 4 && (h < 12 || h > 14) ? b : c; };

function ago(t) {
  if (!t) return '';
  const s = Math.max(0, nowSec() - t);
  if (s < 45) return 'только что';
  const m = Math.round(s / 60);
  if (m < 60) return `${m} мин назад`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} ч назад`;
  return `${Math.floor(h / 24)} дн назад`;
}
function dur(sec) {
  const m = Math.floor(Math.max(0, sec) / 60);
  if (m < 1) return '<1 мин';
  if (m < 60) return `${m} мин`;
  const h = Math.floor(m / 60);
  return m % 60 ? `${h} ч ${m % 60} мин` : `${h} ч`;
}
function clock(t) {
  if (!t) return '';
  const d = new Date(t * 1000);
  const hm = d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  const today = new Date(nowSec() * 1000).toDateString() === d.toDateString();
  return today ? hm : `${d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' }).replace('.', '')}, ${hm}`;
}
function kTok(n) {  // 151988 → 152k
  n = +n || 0;
  if (n >= 1e6) return (Math.round(n / 1e5) / 10).toString().replace('.0', '') + 'M';
  if (n >= 1e3) return Math.round(n / 1e3) + 'k';
  return String(n);
}
function ruTok(n) {  // 151988 → «152 тыс.»
  n = +n || 0;
  if (n >= 1e6) return (Math.round(n / 1e5) / 10).toString().replace('.0', '').replace('.', ',') + ' млн';
  if (n >= 1e3) return Math.round(n / 1e3) + ' тыс.';
  return String(n);
}
function model(m) {  // claude-opus-5-5 → Opus 5.5
  const x = /claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:\b|$)/.exec(String(m || ''));
  if (!x) return m || '';
  return x[1][0].toUpperCase() + x[1].slice(1) + ' ' + x[2] + (x[3] ? '.' + x[3] : '');
}
const lvl = (p) => (p > 80 ? 'hi' : p >= 60 ? 'mid' : 'ok');

// статус с учётом свежести: молчит дольше 3 мин — «нет связи»
function stat(s) {
  const quiet = nowSec() - (s.seen || s.updated || 0);
  if (s.status !== 'ended' && (s.status === 'offline' || quiet > STALE)) {
    return { k: 'offline', label: `нет связи ${Math.max(1, Math.round(quiet / 60))} мин` };
  }
  const def = { working: 'работает', waiting: 'ждёт тебя', question: 'вопрос', ended: 'закончена' };
  return { k: s.status in def ? s.status : 'waiting', label: s.label || def[s.status] || s.status };
}
const chip = (st) => `<span class="st ${st.k}"><i></i>${esc(st.label)}</span>`;

function ring(s, size, w) {
  const c = s.ctx || {};
  const p = Math.max(0, Math.min(100, Math.round(+c.pct || 0)));
  const r = (size - w) / 2;
  const len = 2 * Math.PI * r;
  const fill = (len * p) / 100;
  return `<div class="ring2 ${lvl(p)}" style="--size:${size}px">` +
    `<svg viewBox="0 0 ${size} ${size}" aria-hidden="true">` +
    `<circle class="trk" cx="${size / 2}" cy="${size / 2}" r="${r}" stroke-width="${w}"/>` +
    `<circle class="val" cx="${size / 2}" cy="${size / 2}" r="${r}" stroke-width="${w}" ` +
    `stroke-dasharray="${fill.toFixed(1)} ${len.toFixed(1)}" transform="rotate(-90 ${size / 2} ${size / 2})"/></svg>` +
    `<div class="ring-c"><b>${c.sure === false ? '<span class="approx">≈</span>' : ''}${p}%</b>` +
    `<small>${esc(kTok(c.tokens))}</small></div></div>`;
}

function taskInfo(s) {
  const tasks = s.tasks || [];
  const total = s.total != null ? s.total : tasks.length;
  const done = s.done != null ? s.done : tasks.filter((t) => t.status === 'completed').length;
  const cur = tasks.find((t) => t.status === 'in_progress');
  return { tasks, total, done, cur, pct: total ? Math.round((done / total) * 100) : 0 };
}

function sorted() {
  return (S.sessions || []).slice().sort((a, b) =>
    (ORDER[stat(a).k] - ORDER[stat(b).k]) || ((b.updated || 0) - (a.updated || 0)));
}

// ---------- перерисовка по блокам ----------
// blocks: [{k, html}] — у каждого блока один корневой элемент; меняется только тот, чей html изменился.
function make(b) {
  const t = document.createElement('template');
  t.innerHTML = b.html.trim();
  const el = t.content.firstElementChild;
  el.dataset.k = b.k;
  el._h = b.html;
  return el;
}
function patch(root, blocks) {
  const old = new Map([...root.children].filter((el) => el.dataset.k).map((el) => [el.dataset.k, el]));
  [...root.children].forEach((el) => { if (!el.dataset.k) el.remove(); });  // заглушки загрузки
  let prev = null;
  for (const b of blocks) {
    let el = old.get(b.k);
    if (!el) el = make(b);
    else if (el._h !== b.html) { const n = make(b); el.replaceWith(n); el = n; }
    old.delete(b.k);
    const want = prev ? prev.nextSibling : root.firstChild;
    if (el !== want) root.insertBefore(el, want);
    prev = el;
  }
  old.forEach((el) => el.remove());
}

// ---------- экран «Сессии» ----------
function sumBlock(list) {
  const n = { working: 0, waiting: 0, question: 0 };
  list.forEach((s) => { const k = stat(s).k; if (k in n) n[k]++; });
  const tile = (k, v, label) => `<div class="stat s-${k}${v ? '' : ' zero'}"><div class="v">${v}</div><div class="k">${label}</div></div>`;
  return `<div class="sum">${tile('working', n.working, plural(n.working, 'работает', 'работают', 'работают'))}` +
    `${tile('waiting', n.waiting, 'ждут тебя')}${tile('question', n.question, plural(n.question, 'вопрос', 'вопроса', 'вопросов'))}</div>`;
}

function cardHtml(s) {
  const st = stat(s);
  const ti = taskInfo(s);
  const now = nowSec();
  const start = (s.turn && s.turn.started) || (s.prompt && s.prompt.at) || s.started;
  let when;
  if (st.k === 'working' || st.k === 'question') when = start ? `⏱ ${dur(now - start)}` : '';
  else if (st.k === 'offline') when = s.seen ? `был ${ago(s.seen)}` : '';
  else when = ago(s.updated);
  const tasks = ti.total
    ? `<div class="prog"><div class="prog-h"><span class="clip">${ti.cur ? '▸ ' + esc(ti.cur.active || ti.cur.subject) : ti.done === ti.total ? 'Все задачи готовы' : 'Задачи'}</span>` +
      `<b>${ti.done} из ${ti.total}</b></div><div class="bar"><i style="width:${ti.pct}%"></i></div></div>`
    : `<div class="prog none">${s.turn && s.turn.tools ? `шагов: ${s.turn.tools}` : 'задач нет'}</div>`;
  let line = '';
  if (st.k === 'question' && s.question) line = `<div class="act q clip">❓ ${esc(s.question)}</div>`;
  else if (st.k === 'working' && s.action) line = `<div class="act clip">${esc(s.action)}</div>`;
  else if (s.reply && (st.k === 'waiting' || st.k === 'ended')) line = `<div class="act clip">💬 ${esc(s.reply)}</div>`;
  return `<button type="button" class="card ses st-${st.k}" data-open="${esc(s.id)}">
    <div class="ses-top">
      <div class="ses-main">
        <div class="ses-title">${esc(s.title || 'Без названия')}</div>
        <div class="ses-meta">${chip(st)}${when ? `<span class="when">${esc(when)}</span>` : ''}</div>
      </div>
      ${ring(s, 74, 7)}
    </div>
    ${tasks}${line}
  </button>`;
}

function listBlocks() {
  const list = sorted();
  const blocks = [{ k: 'sum', html: sumBlock(list) }];
  if (!list.length) {
    blocks.push({ k: 'empty', html: `<div class="card empty-card"><div class="empty">
      <span class="e"><svg viewBox="0 0 32 32"><path d="M3 17h6l3-8 5 15 4-11 2 4h6"/></svg></span>
      <b>Сессий пока нет</b>Открой чат Claude Code — он появится здесь через несколько секунд</div></div>` });
  }
  list.forEach((s) => blocks.push({ k: 's-' + s.id, html: cardHtml(s) }));
  if (S.hidden) {
    blocks.push({ k: 'hidden', html: `<button type="button" class="hidden-row" data-unhide>Скрыто: ${S.hidden} · <u>показать</u></button>` });
  }
  return blocks;
}

// ---------- экран «Подробно» ----------
function nowCard(s, st) {
  if (st.k === 'question') {
    return `<section class="card now q"><div class="card-title">❓ Вопрос тебе</div>
      <div class="txt">${esc(s.question || 'Ждёт ответа в чате')}</div></section>`;
  }
  if (st.k === 'working') {
    return `<section class="card now w"><div class="card-title"><span class="live"></span>Сейчас</div>
      <div class="txt">${esc(s.action || 'Думает…')}</div></section>`;
  }
  if (st.k === 'offline') {
    return `<section class="card now off"><div class="card-title">📡 ${esc(st.label)}</div>
      <div class="txt hint">Последний раз на связи ${esc(clock(s.seen))}. Чат мог закрыться или уснуть.</div></section>`;
  }
  if (st.k === 'ended') {
    return '<section class="card now off"><div class="card-title">🏁 Сессия закончена</div></section>';
  }
  return `<section class="card now wait"><div class="card-title">✋ Ждёт тебя</div>
    <div class="txt hint">Закончил ${esc(ago(s.updated))} — ответь в чате.</div></section>`;
}

function detailBlocks(s) {
  const st = stat(s);
  const c = s.ctx || {};
  const ti = taskInfo(s);
  const t = s.turn || {};
  const blocks = [];
  const meta = [model(s.model), (s.repos || []).join(', ')].filter(Boolean).map(esc).join(' · ');
  blocks.push({ k: 'head', html: `<header class="d-head">
      <button type="button" class="btn sm back" data-back>‹ Сессии</button>
      <h1 class="d-title">${esc(s.title || 'Без названия')}</h1>
      <div class="d-meta">${chip(st)}${meta ? `<span>${meta}</span>` : ''}</div>
      ${s.url ? `<button type="button" class="btn primary" data-link="${esc(s.url)}">↗️ Открыть чат</button>` : ''}
    </header>` });
  blocks.push({ k: 'ctx', html: `<section class="card ctx">
      ${ring(s, 128, 11)}
      <div class="ctx-t">
        <div class="hint">Контекст</div>
        <div class="ctx-v">${esc(ruTok(c.tokens))}<span>из ${esc(ruTok(c.window))}</span></div>
        <div class="hint">сжатий: ${+c.compacts || 0}</div>
        ${c.sure === false ? '<div class="hint small">≈ окно уточнится после первого сжатия</div>' : ''}
      </div>
    </section>` });
  blocks.push({ k: 'now', html: nowCard(s, st) });
  if (s.prompt) {
    const n = s.nprompts || 0;
    blocks.push({ k: 'prompt', html: `<section class="card prompt"><div class="card-title">Запрос
        <span class="hint">${esc(clock(s.prompt.at))}${n > 1 ? ` · всего ${n}` : ''}</span></div>
      <div class="txt">${esc(s.prompt.text)}</div></section>` });
  }
  if (ti.total) {
    const icon = (x) => (x.status === 'completed' ? '<span class="ti done">✓</span>'
      : x.status === 'in_progress' ? '<span class="ti run">▸</span>' : '<span class="ti">○</span>');
    blocks.push({ k: 'tasks', html: `<section class="card tasks">
        <div class="card-title">Задачи <b>${ti.done} из ${ti.total}</b></div>
        <div class="bar"><i style="width:${ti.pct}%"></i></div>
        <ul>${ti.tasks.map((x) => `<li class="${esc(x.status)}">${icon(x)}<span>${esc(x.status === 'in_progress' && x.active ? x.active : x.subject)}</span></li>`).join('')}</ul>
      </section>` });
  }
  const prs = t.prs || [];
  const edits = t.edits || [];
  const nedits = t.nedits != null ? t.nedits : edits.length;
  const tile = (v, k) => `<div class="stat"><div class="v">${v}</div><div class="k">${k}</div></div>`;
  blocks.push({ k: 'stats', html: `<section class="card turn">
      <div class="card-title">Этот запрос${t.started ? ` <span class="hint">${esc(dur(nowSec() - t.started))}</span>` : ''}</div>
      <div class="tiles">${tile(t.tools || 0, plural(t.tools || 0, 'шаг', 'шага', 'шагов'))}${tile(nedits, plural(nedits, 'файл', 'файла', 'файлов'))}${tile(t.commits || 0, plural(t.commits || 0, 'коммит', 'коммита', 'коммитов'))}${tile(t.agents || 0, plural(t.agents || 0, 'агент', 'агента', 'агентов'))}</div>
      ${edits.length ? `<details data-key="edits"${opened.has('edits') ? ' open' : ''}><summary>Изменённые файлы <span class="hint">${edits.length}</span></summary>
        <div class="files">${edits.map((f) => `<code>${esc(f)}</code>`).join('')}</div></details>` : ''}
      ${prs.length ? `<div class="prs">${prs.map((u) => {
        const m = /\/pull\/(\d+)/.exec(u);
        return `<button type="button" class="pill accent" data-link="${esc(u)}">PR ${m ? '#' + m[1] : ''} ↗</button>`;
      }).join('')}</div>` : ''}
    </section>` });
  if (s.reply) {
    blocks.push({ k: 'reply', html: `<details class="card fold" data-key="reply"${opened.has('reply') ? ' open' : ''}>
        <summary><span class="card-title">Последний ответ</span><span class="pv">${esc(s.reply)}</span></summary>
        <div class="txt">${esc(s.reply)}</div></details>` });
  }
  const older = (s.prompts || []).filter((p) => !(s.prompt && p.at === s.prompt.at && p.text === s.prompt.text))
    .sort((a, b) => (b.at || 0) - (a.at || 0));
  if (older.length) {
    blocks.push({ k: 'older', html: `<section class="older"><div class="label">Предыдущие запросы</div>
        <div class="list plain">${older.map((p) => `<div class="row"><div class="main"><div class="txt2">${esc(p.text)}</div>
          <div class="meta">${esc(clock(p.at))}</div></div></div>`).join('')}</div></section>` });
  }
  blocks.push({ k: 'hide', html: `<div class="hide-box"><button type="button" class="btn" data-hide="${esc(s.id)}">🙈 Скрыть</button>
      <div class="hint center">Вернуть — в «Настройках»</div></div>` });
  return blocks;
}

function goneBlocks() {
  return [{ k: 'gone', html: `<div class="card"><div class="empty"><span class="e">🫥</span><b>Сессии больше нет</b>
      Её скрыли или она пропала из списка.</div><button type="button" class="btn tonal" data-back>К сессиям</button></div>` }];
}

// ---------- экран «Настройки» ----------
function settingsBlocks() {
  const a = S.alerts || {};
  const rows = ALERTS.map((x) => `<div class="row"><span class="ic">${x.ic}</span><span class="main">
      <div class="title">${esc(x.title)}</div><div class="meta">${esc(x.meta)}</div></span>
      <button type="button" class="switch${a[x.key] ? ' on' : ''}" role="switch" aria-checked="${a[x.key] ? 'true' : 'false'}"
        aria-label="${esc(x.title)}" data-alert="${x.key}"></button></div>`).join('');
  const h = S.hidden || 0;
  return [
    { k: 'h', html: '<div class="label">Уведомления в чат</div>' },
    { k: 'alerts', html: `<div class="list">${rows}</div>` },
    { k: 'h2', html: '<div class="label">Список</div>' },
    { k: 'hidden', html: `<div class="list"><button type="button" class="row tap" data-unhide${h ? '' : ' disabled'}>
        <span class="ic">👁</span><span class="main"><div class="title">Показать скрытые (${h})</div>
        <div class="meta">${h ? 'Вернуть все скрытые сессии' : 'Скрытых нет'}</div></span><span class="end chev"></span></button></div>` },
    { k: 'how', html: '<p class="hint how">Каждый чат Claude Code сам присылает сводку раз в несколько секунд. Закрытый чат через 3 минуты станет «нет связи».</p>' },
    { k: 'sup', html: Kit.supportRow('api/support') },
    { k: 'chat', html: `<div class="list mode-list"><button class="row tap" type="button" data-chat>
        <span class="ic">💬</span><span class="main"><div class="title">Перейти в чат</div>
        <div class="meta">Всё то же кнопками в Telegram</div></span><span class="end chev"></span></button></div>` },
  ];
}

// ---------- отрисовка ----------
function render() {
  if (!S) return;
  const key = view.name + ':' + view.id;
  const root = $('view');
  const fresh = shown !== key;
  if (fresh) {
    if (shown) scrolls[shown] = scrollY;
    root.innerHTML = '';
    shown = key;
  }
  let blocks;
  if (view.name === 'detail') {
    const s = (S.sessions || []).find((x) => x.id === view.id);
    blocks = s ? detailBlocks(s) : goneBlocks();
  } else if (view.name === 'settings') blocks = settingsBlocks();
  else blocks = listBlocks();
  patch(root, blocks);
  $('hero').hidden = view.name === 'detail';
  if (fresh) {
    [...root.children].forEach((el, i) => { el.classList.add('rise'); el.style.animationDelay = Math.min(i, 6) * 0.035 + 's'; });
    scrollTo(0, scrolls[key] || 0);
  }
  syncBack();
  live();
}

function live() {
  const el = $('live');
  if (!el) return;
  const bad = failed > 1;
  el.textContent = bad ? 'нет связи' : 'на связи';
  el.className = 'pill live-pill dot' + (bad ? ' err' : ' accent live');
}

// ---------- переходы ----------
let tabs;
function go(name, id) {
  view = { name, id: id || '' };
  if (tabs) tabs.set(name === 'settings' ? 'settings' : 'list');
  render();
}
function back() {
  if (document.querySelector('.sheet.open')) return;  // открыт лист поддержки — назад закрывает его
  haptic('tap');
  go('list');
}
let backOn = false;
function syncBack() {
  if (!tg || !tg.BackButton) return;
  const want = view.name === 'detail';
  if (want === backOn && (!want || tg.BackButton.isVisible !== false)) return;
  backOn = want;
  if (want) { tg.BackButton.onClick(back); tg.BackButton.show(); } else { tg.BackButton.offClick(back); tg.BackButton.hide(); }
}

function openLink(url) {
  haptic('tap');
  if (tg && tg.openLink) tg.openLink(url); else window.open(url, '_blank', 'noopener');
}

document.addEventListener('toggle', (ev) => {
  const d = ev.target;
  if (d.tagName !== 'DETAILS' || !d.dataset.key) return;
  if (d.open) opened.add(d.dataset.key); else opened.delete(d.dataset.key);
}, true);

document.addEventListener('click', async (ev) => {
  const t = ev.target.closest('[data-open],[data-back],[data-link],[data-hide],[data-unhide],[data-alert],[data-chat]');
  if (!t) return;
  if (t.dataset.open) { haptic('tap'); go('detail', t.dataset.open); return; }
  if (t.hasAttribute('data-back')) { back(); return; }
  if (t.dataset.link) { openLink(t.dataset.link); return; }
  if (t.dataset.hide) {
    await busy(t, async () => {
      try {
        await api('api/hide', { id: t.dataset.hide });
        haptic('success');
        S.sessions = (S.sessions || []).filter((x) => x.id !== t.dataset.hide);
        S.hidden = (S.hidden || 0) + 1;
        toast('Скрыта — вернуть можно в «Настройках»', 'ok');
        go('list');
      } catch (e) { haptic('error'); toast(e.message, true); }
    });
    return;
  }
  if (t.hasAttribute('data-unhide')) {
    await busy(t, async () => {
      try {
        await api('api/unhide_all', {});
        haptic('success');
        toast('Скрытые снова в списке', 'ok');
        await refresh();
      } catch (e) { haptic('error'); toast(e.message, true); }
    });
    return;
  }
  if (t.dataset.alert) {
    const k = t.dataset.alert;
    const on = !t.classList.contains('on');
    haptic('select');
    S.alerts = Object.assign({}, S.alerts, { [k]: on });
    render();
    try {
      const r = await api('api/alerts', { key: k, on });
      if (r && r.alerts) S.alerts = r.alerts;
    } catch (e) {
      S.alerts = Object.assign({}, S.alerts, { [k]: !on });
      haptic('error');
      toast(e.message, true);
    }
    render();
    return;
  }
  if (t.hasAttribute('data-chat')) {
    haptic('tap');
    await busy(t, async () => {
      try {
        await api('api/mode', { mode: 'chat' });
        haptic('success');
        if (tg && tg.close) tg.close(); else toast('Режим: чат — продолжай в Telegram', 'ok');
      } catch (e) { haptic('error'); toast(e.message, true); }
    });
  }
});

// ---------- опрос ----------
let inflight = false;
async function refresh() {
  if (inflight) return;
  inflight = true;
  try {
    const d = await api('api/state');
    S = d;
    gotAt = Date.now();
    failed = 0;
    render();
  } catch (e) {
    failed++;
    if (!S) {
      $('view').innerHTML = `<div class="card"><div class="empty"><span class="e">📡</span><b>Нет связи</b>${esc(e.message)}</div>` +
        '<button class="btn tonal" id="retry">Повторить</button></div>';
      $('retry').onclick = () => { haptic('tap'); refresh(); };
    } else {
      live();
      if (failed === 2) toast(e.message, true);
    }
  } finally { inflight = false; }
}

tabs = Kit.tabbar($('tabs'), [
  { id: 'list', icon: '📊', label: 'Сессии' },
  { id: 'settings', icon: '⚙️', label: 'Настройки' },
], 'list', (id) => go(id));

// ссылка прямо на сессию: …/?s=<id> или start_param «s_<id>»
(() => {
  const q = new URLSearchParams(location.search).get('s');
  const sp = tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param;
  const id = q || (sp && sp.startsWith('s_') ? sp.slice(2) : '');
  if (id) view = { name: 'detail', id };
})();

refresh();
setInterval(() => { if (!document.hidden) refresh(); }, 4000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
