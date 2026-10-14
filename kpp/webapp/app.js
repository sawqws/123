// КПП — мини-приложение: номер такси → пропуск в Домиленде. Один экран: госномер, «Оформить пропуск»,
// быстрые действия (вставить, скриншот, повторить), последние пропуска. Плюс вход через Яндекс и выбор адреса.
const { tg, $, esc, api, toast, haptic, sheet, busy } = Kit;

// ---------- госномер ----------
const LET = 'АВЕКМНОРСТУХ';
const LAT = { A: 'А', B: 'В', E: 'Е', K: 'К', M: 'М', H: 'Н', O: 'О', P: 'Р', C: 'С', T: 'Т', Y: 'У', X: 'Х' };
const isL = (c) => LET.includes(c);
const isD = (c) => c >= '0' && c <= '9';
const norm = (s) => String(s || '').toUpperCase().replace(/Ё/g, 'Е').replace(/[A-Z]/g, (c) => LAT[c] || c);
// такси (жёлтый номер): «АВ 123 77» — две буквы в начале
const isTaxi = (v) => v.length > 1 && isL(v[1]);
const mainLen = (v) => (isTaxi(v) ? 5 : 6);
const maxLen = (v) => mainLen(v) + 3;
const TPL = { std: 'LDDDLLDDD', taxi: 'LLDDDDDD' };

// Что угодно (ввод, вставка, «водитель на Kia, а 123 вс 777») → «А123ВС777» или его начало
function plateOf(raw) {
  const s = norm(raw);
  const flat = s.replace(/[\s\-|·.,]+/g, '');
  const full = flat.match(new RegExp(`[${LET}]\\d{3}[${LET}]{2}\\d{2,3}|[${LET}]{2}\\d{3}\\d{2,3}`));
  if (full && flat.length > full[0].length) return full[0];
  let out = '';
  for (const c of s) {
    if (out.length >= maxLen(out + c)) break;
    const i = out.length;
    const want = i === 1 ? (isL(c) ? 'L' : 'D') : (isTaxi(out) ? TPL.taxi : TPL.std)[i];
    if ((want === 'L' && isL(c)) || (want === 'D' && isD(c))) out += c;
  }
  return out;
}
const complete = (v) => v.length >= mainLen(v) + 2;
const pretty = (v) => (isTaxi(v) ? [v.slice(0, 2), v.slice(2, 5), v.slice(5)]
  : [v.slice(0, 1), v.slice(1, 4), v.slice(4, 6), v.slice(6)]).filter(Boolean).join(' ');

// Табличка как настоящая: белая (или жёлтая у такси), регион справа с RUS и флагом.
// cur — номер позиции курсора (для поля ввода), ghost — показывать пустые места бледным образцом.
function plateHtml(value, opts) {
  const o = opts || {};
  const v = plateOf(value);
  const taxi = isTaxi(v);
  const tpl = taxi ? TPL.taxi : TPL.std;
  const sample = taxi ? 'АВ123777' : 'А123ВС777';
  const cut = mainLen(v);
  const slot = (i) => {
    const c = v[i];
    const kind = tpl[i] === 'L' ? 'l' : 'd';
    const cls = [kind, c ? '' : 'ghost', o.cur === i ? 'cur' : '', i >= cut + 2 && !c ? 'opt' : ''].join(' ');
    return `<span class="${cls}">${esc(c || (o.ghost === false ? '' : sample[i] || '7'))}</span>`;
  };
  let main = '';
  for (let i = 0; i < cut; i++) main += slot(i) + (i === 0 && !taxi || i === 3 && !taxi || i === 1 && taxi ? '<i class="gap"></i>' : '');
  let reg = '';
  for (let i = cut; i < cut + 3; i++) reg += slot(i);
  return `<span class="rp${taxi ? ' taxi' : ''}${o.cls ? ' ' + o.cls : ''}" aria-label="${esc(pretty(v))}">` +
    `<span class="rp-main">${main}</span>` +
    `<span class="rp-reg"><span class="rp-num">${reg}</span><span class="rp-rus">RUS<i class="flag"></i></span></span></span>`;
}

// ---------- состояние ----------
let S = null;          // ответ /api/state
let plate = '';        // что набрано
let result = null;     // {kind: 'ok'|'repeat'|'err', ...} — карточка вместо поля после заказа

const canPaste = () => !!((navigator.clipboard && navigator.clipboard.readText) || (tg && tg.readTextFromClipboard));

// «💬 Перейти в чат»: бот переключается в режим «чат» (POST /api/mode), приложение закрывается,
// в чат приходит главное меню. Обратно — /mode или «⚙️ Режим» в чате.
function chatRow() {
  return '<div class="list mode-list"><button class="row tap" type="button" data-chat>' +
    '<span class="ic">💬</span><span class="main"><div class="title">Перейти в чат</div>' +
    '<div class="meta">Всё то же кнопками в Telegram</div></span><span class="end chev"></span></button></div>';
}

async function toChat(btn) {
  haptic('tap');
  await busy(btn, async () => {
    try {
      await api('/api/mode', { mode: 'chat' });
      haptic('success');
      if (tg && tg.close) tg.close(); else toast('Режим: чат — продолжай в Telegram', 'ok');
    } catch (e) { haptic('error'); toast(e.message, true); }
  });
}

function readClipboard() {
  if (navigator.clipboard && navigator.clipboard.readText) {
    return navigator.clipboard.readText().catch(() => (tg && tg.readTextFromClipboard ? tgClipboard() : ''));
  }
  return tgClipboard();
}
const tgClipboard = () => new Promise((res) => { try { tg.readTextFromClipboard((t) => res(t || '')); } catch (e) { res(''); } });

// ---------- экраны ----------
function render() {
  const s = S;
  const where = $('where');
  const many = s && s.login && s.place_id && (s.places || []).length > 1;
  where.disabled = !many;
  where.classList.toggle('tap', !!many);
  $('sub').textContent = s && s.login && s.address ? s.address : 'Пропуск для такси за секунду';
  $('sub').classList.toggle('pin', !!(s && s.login && s.address));
  if (!s.login) return loginView();
  if (!s.place_id) return placeView();
  mainView();
}

function loginView() {
  const expired = S.passes_error;
  $('view').innerHTML = (expired ? `<div class="note err rise">⚠️ ${esc(expired)}</div><div class="gap16"></div>` : '') +
    `<section class="card feature rise login">
      <h2 class="title-lg">Вход через Яндекс</h2>
      <p class="hint lead">Один раз — тем же Яндекс ID, что в Домиленде.</p>
      <ol class="steps">
        <li><span class="n">1</span><div><b>Открой Яндекс и войди</b>
          <button id="open-yandex" class="btn primary">Открыть Яндекс</button></div></li>
        <li><span class="n">2</span><div><b>Увидишь пустую страницу или ошибку</b>
          <div class="hint">Так и надо — вход уже прошёл.</div></div></li>
        <li><span class="n">3</span><div><b>Скопируй адрес страницы и вставь сюда</b>
          <textarea id="token" rows="3" placeholder="https://oauth.yandex.ru/…#access_token=…" autocomplete="off" spellcheck="false"></textarea>
          <div class="btns">${canPaste() ? '<button id="paste-token" class="btn tonal">Вставить</button>' : ''}
            <button id="do-login" class="btn primary">Войти</button></div></div></li>
      </ol>
    </section>
    <p class="hint center">Лучше с iPad или компьютера: телефон может открыть приложение Домиленд вместо страницы.</p>
    ${Kit.supportRow('/api/support', 'Не входит или что-то не так — напиши')}${chatRow()}`;
  $('open-yandex').onclick = () => {
    haptic('tap');
    if (tg && tg.openLink) tg.openLink(S.login_url); else window.open(S.login_url, '_blank');
  };
  const token = $('token');
  token.addEventListener('input', () => { if (/access_token=|y[0-3]_[\w-]{20,}/.test(token.value)) login(); });
  if ($('paste-token')) {
    $('paste-token').onclick = async () => {
      const t = await readClipboard();
      if (!t) { toast('Буфер пуст или закрыт — вставь долгим нажатием', true); return; }
      token.value = t.trim();
      login();
    };
  }
  $('do-login').onclick = login;
}

async function login() {
  const text = $('token').value.trim();
  if (!text) { haptic('error'); toast('Вставь адрес страницы после входа', true); return; }
  await busy($('do-login'), async () => {
    try {
      S = await api('/api/login', { text });
      haptic('success');
      toast('Вход выполнен', 'ok');
      render();
    } catch (e) { haptic('error'); toast(e.message, true); }
  });
}

function placesHtml() {
  return '<div class="list plain places">' + (S.places || []).map((p) =>
    `<button class="row" data-place="${esc(p.id)}"><span class="ic">🏢</span><span class="main"><span class="title">${esc(p.title)}</span></span>` +
    `<span class="end">${String(p.id) === String(S.place_id) ? '<span class="pill ok">выбран</span>' : '<span class="chev"></span>'}</span></button>`).join('') + '</div>';
}

function placeView() {
  $('view').innerHTML = `<section class="card feature rise">
      <h2 class="title-lg">Куда заказывать пропуск?</h2>
      <p class="hint lead">Выбери дом или корпус — потом можно сменить, нажав на адрес вверху.</p>
      ${S.places && S.places.length ? placesHtml() : '<div class="empty"><span class="e">🏚</span><b>Адресов нет</b>В Домиленде нет ни одного адреса — тот ли это аккаунт?</div>'}
    </section>`;
}

// Тонкие значки плиток (вместо эмодзи — спокойнее в пастели)
const IC = {
  paste: '<svg viewBox="0 0 24 24"><rect x="6" y="4.5" width="12" height="16" rx="2.5"/><path d="M9.5 4.5h5v2.5h-5z"/><path d="M9.5 12h5M9.5 15.5h3.5"/></svg>',
  shot: '<svg viewBox="0 0 24 24"><path d="M4 8.5A2.5 2.5 0 0 1 6.5 6h1.3l1.4-2h5.6l1.4 2h1.3A2.5 2.5 0 0 1 20 8.5v8a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 16.5z"/><circle cx="12" cy="12.5" r="3.6"/></svg>',
  again: '<svg viewBox="0 0 24 24"><path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M19.5 4.5v4h-4"/></svg>',
};

function orderCard() {
  if (result) return resultCard();
  const last = S.last;
  return `<section class="card feature order rise">
      <label class="plate-field${plate ? ' has' : ''}" id="field">
        <span class="plate-box" id="plate-box">${plateHtml(plate, { cur: plate.length, cls: 'big' })}</span>
        <input id="plate" autocomplete="off" autocapitalize="characters" autocorrect="off" spellcheck="false"
          enterkeyhint="go" aria-label="Номер машины" value="${esc(plate)}">
      </label>
      <div class="plate-hint" id="plate-hint">${plate ? esc(pretty(plate)) : 'Номер машины из Яндекс Go'}</div>
      <button id="go" class="btn primary go">Оформить пропуск</button>
      <div class="quick">
        <button class="tile" id="q-paste"${canPaste() ? '' : ' disabled'}><span class="i">${IC.paste}</span><span>Вставить</span><small>из буфера</small></button>
        <button class="tile" id="q-shot"><span class="i">${IC.shot}</span><span>Скриншот</span><small>Яндекс Go</small></button>
        <button class="tile" id="q-last"${last ? '' : ' disabled'}><span class="i">${IC.again}</span><span>Повторить</span>
          <small class="clip">${last ? esc(last.pretty) : 'последний'}</small></button>
      </div>
    </section>`;
}

function resultCard() {
  const r = result;
  const big = plateHtml(r.plate, { cls: 'big', ghost: false });
  if (r.kind === 'ok') {
    return `<section class="card feature result ok rise">
        <div class="tick" aria-hidden="true"><svg viewBox="0 0 52 52"><circle cx="26" cy="26" r="24"/><path d="M15 27l7 7 15-16"/></svg></div>
        <h2 class="title-lg">Пропуск заказан</h2>
        <div class="res-plate">${big}</div>
        <div class="res-meta"><span class="pill ok dot">Разовый</span><span>${esc(r.when)}</span></div>
        ${S.address ? `<div class="hint center clip2">${esc(S.address)}</div>` : ''}
        <div class="btns"><button class="btn danger" data-cancel="${esc(r.id == null ? '' : r.id)}"${r.id == null ? ' disabled' : ''}>Отменить</button>
          <button class="btn tonal" data-new>Ещё пропуск</button></div>
      </section>`;
  }
  if (r.kind === 'repeat') {
    return `<section class="card feature result rise">
        <div class="tick warn" aria-hidden="true"><svg viewBox="0 0 52 52"><circle cx="26" cy="26" r="24"/><path d="M26 15v11l7 5"/></svg></div>
        <h2 class="title-lg">Уже заказан</h2>
        <div class="res-plate">${big}</div>
        <div class="res-meta"><span class="pill warn dot">${esc(r.when)}</span></div>
        <p class="hint center">Пропуск на этот номер заказан меньше 20 минут назад.</p>
        <button class="btn primary" data-force="${esc(r.plate)}">Заказать ещё раз</button>
        <button class="btn ghost" data-new>Назад</button>
      </section>`;
  }
  return `<section class="card feature result err rise">
      <div class="tick err" aria-hidden="true"><svg viewBox="0 0 52 52"><circle cx="26" cy="26" r="24"/><path d="M26 15v14M26 36.5v.5"/></svg></div>
      <h2 class="title-lg">Пропуск не оформлен</h2>
      <div class="res-plate">${big}</div>
      <div class="note err">${esc(r.error)}</div>
      <div class="btns"><button class="btn tonal" data-new>Назад</button>
        <button class="btn primary" data-force="${esc(r.plate)}">Повторить</button></div>
    </section>`;
}

const PLATE_RE = new RegExp(`^[${LET}]{1,2} ?\\d{3} ?([${LET}]{2} ?)?\\d{2,3}$`);

function statusPill(text) {
  if (!text) return '';
  const k = /актив|действ|ожида|заказ|нов/i.test(text) ? 'ok' : /отмен|отклон|ошиб/i.test(text) ? 'err'
    : /исполь|заверш|прош/i.test(text) ? '' : 'warn';
  return `<span class="pill dot ${k}">${esc(text)}</span>`;
}

function passesHtml() {
  const s = S;
  const rows = [];
  if (s.passes && s.passes.length) {
    for (const p of s.passes) {
      const plateLike = PLATE_RE.test(norm(p.subtitle || ''));
      const when = (p.fields || []).map((f) => f.value).filter(Boolean)[0] || '';
      rows.push(`<div class="row pass">${plateLike ? plateHtml(p.subtitle, { cls: 'mini', ghost: false })
        : `<span class="ic">🎫</span>`}<div class="main">` +
        (plateLike ? '' : `<div class="title">${esc(p.subtitle || p.title)}</div>`) +
        `<div class="meta-line">${statusPill(p.status)}${when ? `<span class="meta">${esc(when)}</span>` : ''}</div></div>` +
        (p.cancel ? `<button class="btn sm danger" data-cancel="${esc(p.id)}">Отменить</button>` : '') + '</div>');
    }
  } else if (s.recent && s.recent.length) {
    for (const r of s.recent) {
      rows.push(`<div class="row pass">${plateHtml(r.plate, { cls: 'mini', ghost: false })}<div class="main">` +
        `<div class="meta-line">${r.fresh ? statusPill('Заказан') : ''}<span class="meta">${esc(r.when)}</span></div></div>` +
        (r.fresh && r.id != null ? `<button class="btn sm danger" data-cancel="${esc(r.id)}">Отменить</button>` : '') + '</div>');
    }
  }
  if (!rows.length) {
    return s.passes_error ? `<div class="note err">⚠️ ${esc(s.passes_error)}</div>`
      : '<div class="list"><div class="empty"><span class="e">🎫</span><b>Пока пусто</b>Оформи первый пропуск — он появится здесь.</div></div>';
  }
  return (s.passes_error ? `<div class="note err">⚠️ ${esc(s.passes_error)}</div><div class="gap8"></div>` : '') +
    `<div class="list">${rows.join('')}</div>`;
}

function mainView() {
  $('view').innerHTML = orderCard() + '<div class="label">Последние пропуска</div><div id="passes">' + passesHtml() + '</div>' +
    Kit.supportRow('/api/support') + chatRow();
  bindOrder();
}

function bindOrder() {
  const inp = $('plate');
  if (!inp) return;
  const draw = () => {
    $('plate-box').innerHTML = plateHtml(plate, { cur: document.activeElement === inp ? plate.length : -1, cls: 'big' });
    $('plate-hint').textContent = plate ? pretty(plate) : 'Номер машины из Яндекс Go';
    $('field').classList.toggle('has', !!plate);
    $('field').classList.toggle('ok', complete(plate));
  };
  inp.addEventListener('input', () => {
    const before = plate;
    plate = plateOf(inp.value);
    inp.value = plate;
    if (plate.length !== before.length) haptic('select');
    if (complete(plate) && !complete(before) && plate.length === maxLen(plate)) haptic('tap');
    draw();
  });
  inp.addEventListener('focus', () => { $('field').classList.add('focus'); draw(); });
  inp.addEventListener('blur', () => { $('field').classList.remove('focus'); draw(); });
  inp.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); submit(); } });
  $('go').onclick = submit;
  $('q-paste').onclick = paste;
  $('q-shot').onclick = () => { haptic('tap'); $('file').click(); };
  $('q-last').onclick = () => { if (S.last) { haptic('tap'); order(S.last.plate, false, $('q-last')); } };
  draw();
}

function setPlate(v) {
  plate = plateOf(v);
  if ($('plate')) { $('plate').value = plate; $('plate').dispatchEvent(new Event('input')); }
}

async function paste() {
  haptic('tap');
  const t = await readClipboard();
  const p = plateOf(t);
  if (!t) { toast('Буфер пуст или закрыт — вставь долгим нажатием на номер', true); return; }
  if (!complete(p)) { haptic('error'); toast('В буфере нет номера', true); return; }
  setPlate(p);
  haptic('success');
  toast('Вставил ' + pretty(p), 'ok');
}

function submit() {
  if (!complete(plate)) {
    haptic('error');
    toast(plate ? 'Номер не полный — например, А 123 ВС 777' : 'Впиши номер, например А 123 ВС 777', true);
    $('field').classList.add('shake');
    setTimeout(() => $('field') && $('field').classList.remove('shake'), 450);
    return;
  }
  haptic('tap');
  order(plate, false, $('go'));
}

async function order(p, force, btn) {
  await busy(btn, async () => {
    try {
      const res = await api('/api/order', { text: p, force: !!force });
      result = res.repeat ? { kind: 'repeat', plate: res.plate, when: res.when }
        : { kind: 'ok', plate: res.plate, when: res.when, id: res.id };
      haptic(res.repeat ? 'warning' : 'success');
      if (!res.repeat) { plate = ''; refresh(); }
    } catch (e) {
      haptic('error');
      if (e.data && e.data.login) { toast(e.message, true); return refresh(); }
      result = { kind: 'err', plate: plateOf(p), error: e.message };
    }
    mainView();
  });
}

// ---------- скриншот из Яндекс Go ----------
async function shrink(file) {
  try {
    const bmp = await createImageBitmap(file);
    const k = Math.min(1, 1600 / Math.max(bmp.width, bmp.height));
    if (k === 1 && file.size < 2.5e6) return file;
    const c = document.createElement('canvas');
    c.width = Math.round(bmp.width * k);
    c.height = Math.round(bmp.height * k);
    c.getContext('2d').drawImage(bmp, 0, 0, c.width, c.height);
    return (await new Promise((res) => c.toBlob(res, 'image/jpeg', 0.9))) || file;
  } catch (e) { return file; }
}

$('file').addEventListener('change', async () => {
  const file = $('file').files[0];
  $('file').value = '';
  if (!file) return;
  const tile = $('q-shot');
  await busy(tile, async () => {
    toast('Ищу номер на скриншоте…');
    try {
      const blob = await shrink(file);
      const r = await fetch('/api/photo', { method: 'POST', body: blob,
        headers: { 'X-Init-Data': (tg && tg.initData) || '', 'Content-Type': blob.type || 'image/jpeg' } });
      let data = {};
      try { data = await r.json(); } catch (e) { /* пусто */ }
      if (!r.ok) throw new Error(data.error || 'Ошибка ' + r.status);
      result = null;
      if (!$('plate')) mainView();
      setPlate(data.plates[0]);
      haptic('success');
      toast('Нашёл ' + data.pretty[0] + ' — проверь и жми «Оформить»', 'ok');
    } catch (e) { haptic('error'); toast(e.message, true); }
  });
});

// ---------- общие нажатия ----------
document.addEventListener('click', async (ev) => {
  const t = ev.target.closest('[data-cancel],[data-place],[data-force],[data-new],[data-chat]');
  if (!t) return;
  if (t.dataset.chat !== undefined) { toChat(t); return; }
  if (t.dataset.new !== undefined) { haptic('tap'); result = null; mainView(); return; }
  if (t.dataset.force) { haptic('tap'); order(t.dataset.force, true, t); return; }
  if (t.dataset.place) {
    haptic('tap');
    try {
      S = await api('/api/place', { id: t.dataset.place });
      haptic('success');
      if (placeSheet) placeSheet.close();
      toast('Адрес выбран', 'ok');
      render();
    } catch (e) { haptic('error'); toast(e.message, true); }
    return;
  }
  if (t.dataset.cancel) {
    const ok = await new Promise((res) => (tg && tg.showConfirm ? tg.showConfirm('Отменить пропуск?', res) : res(confirm('Отменить пропуск?'))));
    if (!ok) return;
    await busy(t, async () => {
      try {
        await api('/api/cancel', { id: t.dataset.cancel });
        haptic('success');
        toast('Пропуск отменён', 'ok');
        if (result && String(result.id) === t.dataset.cancel) result = null;
        await refresh();
      } catch (e) { haptic('error'); toast(e.message, true); }
    });
  }
});

let placeSheet = null;
$('where').addEventListener('click', () => {
  if (!S || !S.login || !S.place_id || (S.places || []).length < 2) return;
  haptic('tap');
  placeSheet = sheet('<h2>Адрес пропуска</h2>' + placesHtml(), () => { placeSheet = null; });
});

async function refresh() {
  try {
    S = await api('/api/state');
  } catch (e) {
    if (!S) {
      $('view').innerHTML = `<div class="card"><div class="empty"><span class="e">📡</span><b>Нет связи</b>${esc(e.message)}</div>` +
        '<button class="btn tonal" id="retry">Повторить</button></div>';
      $('retry').onclick = () => { haptic('tap'); refresh(); };
    } else toast(e.message, true);
    return;
  }
  if (S.login && S.place_id && $('plate') && !result) {  // не перерисовываем поле, пока человек печатает
    $('passes').innerHTML = passesHtml();
    return render0();
  }
  render();
}
function render0() {  // только шапка
  $('sub').textContent = S.address || 'Пропуск для такси за секунду';
}

refresh();
setInterval(() => { if (!document.hidden) refresh(); }, 60000);
