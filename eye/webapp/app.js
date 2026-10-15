// «Глаз»: разговор без рук через очки (Bluetooth-гарнитура iPhone) и фото/видео с очков.
// Микрофон -> свой детектор речи (громкость) -> WAV 16 кГц -> /api/talk -> ответ голосом через AudioContext.
// Общий вид и помощники — kit.css/kit.js (Kit.*), здесь только своё.
'use strict';

const { tg, $, esc } = Kit;

// ---------- доступ: подпись Telegram или ключ из ссылки /link (для Safari) ----------
let key = '';
try {
  const m = location.hash.match(/k=([0-9a-f]{32})/);
  if (m) { localStorage.setItem('eye-key', m[1]); history.replaceState(null, '', location.pathname); }
  key = localStorage.getItem('eye-key') || '';
} catch (e) { /* приватный режим */ }

function auth(extra) {
  const h = Object.assign({}, extra || {});
  if (tg && tg.initData) h['X-Init-Data'] = tg.initData;
  else if (key) h['X-Key'] = key;
  return h;
}

let where = null; // {lat, lon}
function withWhere(url) {
  if (!where) return url;
  return url + (url.includes('?') ? '&' : '?') + 'lat=' + where.lat + '&lon=' + where.lon;
}

async function api(path, opts) {
  opts = opts || {};
  const r = await fetch(withWhere(path), Object.assign({}, opts, { headers: auth(opts.headers) }));
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || ('ошибка ' + r.status));
  return data;
}

// 🛠 поддержка (Kit.support) — с той же авторизацией, что у страницы (в Safari — ключ из /link)
Kit.supportOpts = { api: (path, body) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body) }) };
$('tab-seen').insertAdjacentHTML('beforeend', Kit.supportRow('/api/support'));

// «💬 Перейти в чат» внизу главного экрана: режим «чат» — бот пришлёт главное меню в чат, страница закрывается
let uiMode = '';
function chatRow() {
  return '<div class="list mode-list"><button class="row tap" id="tochat"><span class="ic">💬</span><span class="main"><div class="title">Перейти в чат</div>' +
    `<div class="meta" id="tochat-meta">${uiMode === 'chat' ? 'режим чата уже включён' : 'бот перейдёт в режим чата: фото и голос туда, ответы там же'}</div></span><span class="end chev"></span></button></div>`;
}
$('tab-talk').insertAdjacentHTML('beforeend', chatRow());
$('tochat').addEventListener('click', async (ev) => {
  const b = ev.currentTarget;
  try {
    await Kit.busy(b, () => api('/api/mode', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode: 'chat' }) }));
  } catch (e) { toast(e.message, 'error'); return; }
  uiMode = 'chat';
  $('tochat-meta').textContent = 'режим чата уже включён';
  if (tg && tg.close) tg.close(); else toast('Режим чата включён — главное меню в Telegram', 'success');
});

function haptic(kind) { Kit.haptic(kind); }
function toast(text, kind) {
  Kit.toast(text, kind === 'error' ? 'err' : kind === 'success' ? 'ok' : '');
  if (kind) haptic(kind === 'light' ? 'tap' : kind);
}

// ---------- вкладки ----------
const TABS = [{ id: 'talk', icon: '🎙', label: 'Разговор' }, { id: 'seen', icon: '👁', label: 'Что видел' }];
let tab = 'talk';
try { tab = localStorage.getItem('eye-tab') === 'seen' ? 'seen' : 'talk'; } catch (e) { /* нет памяти */ }
function showTab(id) {
  tab = id;
  $('tab-talk').hidden = id !== 'talk';
  $('tab-seen').hidden = id !== 'seen';
  try { localStorage.setItem('eye-tab', id); } catch (e) { /* нет памяти */ }
  window.scrollTo(0, 0);
}
const tabs = Kit.tabbar($('tabs'), TABS, tab, showTab);
showTab(tab);

// ---------- состояние разговора ----------
// off — выключен; listen — слушаю; think — Claude думает; speak — говорю
let mode = 'off';
const LABELS = { off: 'Готов слушать', listen: 'Слушаю', think: 'Думаю', speak: 'Говорю' };
const HINTS = {
  off: 'Нажми «Говорить» — дальше без рук',
  listen: 'Говори — замолчишь, и я отвечу',
  think: 'Секунду…',
  speak: 'Отвечаю в динамики очков',
};
let talking = false; // разговор включён (после ответа снова слушаем)

function setMode(m) {
  const was = mode;
  mode = m;
  document.body.dataset.mode = m;
  document.body.classList.toggle('talking', talking);
  document.body.classList.remove('mic-err');
  $('state').textContent = LABELS[m];
  $('hint').textContent = HINTS[m];
  $('talk-label').textContent = talking ? '■ Закончить' : '🎙 Говорить';
  $('talk-sub').textContent = talking ? 'удерживай кнопку' : '';
  if (m !== 'listen') $('lvl').style.transform = '';
  if (was !== m && (m === 'think' || m === 'speak')) haptic('select');
}

// ---------- лента разговора: пузыри ----------
const MAX_BUBBLES = 8;
const CHAT_EMPTY = '<div class="empty"><span class="e">💬</span><b>Пока тихо</b>' +
  'Нажми «Говорить» — отвечу в динамики очков</div>';
function bubble(role, text) {
  const chat = $('chat');
  chat.querySelectorAll('.empty, .sk').forEach((x) => x.remove());
  const b = document.createElement('div');
  b.className = 'bubble ' + (role === 'user' ? 'me' : 'ai');
  b.textContent = text;
  chat.appendChild(b);
  while (chat.children.length > MAX_BUBBLES) chat.firstElementChild.remove();
}
function renderChat(items) {
  const chat = $('chat');
  if (!items || !items.length) { if (!chat.querySelector('.bubble')) chat.innerHTML = CHAT_EMPTY; return; }
  chat.innerHTML = '';
  items.slice(-MAX_BUBBLES).forEach((h) => {
    // «[фото Зимний дворец] что это?» -> «📷 Зимний дворец — что это?»; видео — 🎬
    const m = h.role === 'user' && /^\[(фото|видео) ?([^\]]*)\]\s*(.*)$/s.exec(h.text || '');
    const text = m ? (m[1] === 'видео' ? '🎬 ' : '📷 ') + (m[2] || (m[1] === 'видео' ? 'Видео' : 'Фото'))
      + (m[3] ? ' — ' + m[3] : '') : h.text;
    bubble(h.role, text);
  });
}
function clearChat() { $('chat').innerHTML = CHAT_EMPTY; }

// ---------- звук: AudioContext (на iPhone играет в любой момент после первого нажатия) ----------
let ctx = null;
let playing = null;
function audioCtx() {
  if (!ctx) ctx = new (window.AudioContext || window.webkitAudioContext)();
  if (ctx.state === 'suspended') ctx.resume();
  return ctx;
}

function unlockSpeech() {
  try { if (window.speechSynthesis) speechSynthesis.speak(new SpeechSynthesisUtterance('')); } catch (e) {}
}

async function playUrl(url) {
  const buf = await (await fetch(url)).arrayBuffer();
  const c = audioCtx();
  const audio = await new Promise((ok, fail) => c.decodeAudioData(buf, ok, fail));
  return new Promise((done) => {
    const s = c.createBufferSource();
    s.buffer = audio;
    s.connect(c.destination);
    s.onended = () => { if (playing === s) playing = null; done(); };
    playing = s;
    s.start();
  });
}

function speakLocal(text) {
  return new Promise((done) => {
    if (!window.speechSynthesis) return done();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = 'ru-RU';
    u.rate = 1.05;
    const ru = speechSynthesis.getVoices().find((v) => v.lang && v.lang.startsWith('ru'));
    if (ru) u.voice = ru;
    u.onend = u.onerror = () => done();
    speechSynthesis.speak(u);
  });
}

function stopSpeaking() {
  if (playing) { try { playing.stop(); } catch (e) {} playing = null; }
  if (window.speechSynthesis) speechSynthesis.cancel();
}

// очередь «сказать вслух»: ответы и разбор фото не перебивают друг друга
const queue = [];
let busy = false;
function say(text, audio) {
  if (!text) return;
  queue.push({ text, audio });
  drain();
}
async function drain() {
  if (busy || mode === 'think') return;
  busy = true;
  while (queue.length) {
    const { text, audio } = queue.shift();
    $('say').textContent = text;
    setMode('speak');
    try {
      if (audio && ctx) await playUrl(audio);
      else await speakLocal(text);
    } catch (e) {
      await speakLocal(text);
    }
  }
  busy = false;
  await sleep(350); // хвост своего голоса из динамиков очков не должен попасть в микрофон
  setMode(talking ? 'listen' : 'off');
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------- микрофон и детектор речи ----------
let stream = null;
let rate = 48000;
const PRE = 0.45;       // сек. звука до начала речи (чтобы не съесть первый слог)
const END = 0.9;        // сек. тишины — фраза закончилась
const MIN = 0.35;       // короче — это не речь, а шум
const MAX = 25;         // дольше — отправляем как есть
let floor = 0.004;      // уровень фонового шума, подстраивается сам
let pre = [], buf = [], inSpeech = false, loud = 0, quiet = 0, spoken = 0;

async function startMic() {
  if (stream) return;
  stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  const c = audioCtx();
  rate = c.sampleRate;
  const src = c.createMediaStreamSource(stream);
  const proc = c.createScriptProcessor(4096, 1, 1);
  const mute = c.createGain();
  mute.gain.value = 0;
  src.connect(proc);
  proc.connect(mute);
  mute.connect(c.destination);
  proc.onaudioprocess = (e) => onChunk(e.inputBuffer.getChannelData(0));
}

function onChunk(data) {
  if (mode !== 'listen') { reset(); return; }
  const chunk = new Float32Array(data);
  const dur = chunk.length / rate;
  let sum = 0;
  for (let i = 0; i < chunk.length; i++) sum += chunk[i] * chunk[i];
  const rms = Math.sqrt(sum / chunk.length);
  const thr = Math.max(0.012, floor * 3.2);
  $('lvl').style.transform = 'scale(' + (1 + Math.min(rms * 6, 0.25)) + ')';
  if (!inSpeech) {
    floor = Math.max(0.002, floor * 0.95 + rms * 0.05);
    pre.push(chunk);
    while (pre.length * dur > PRE) pre.shift();
    loud = rms > thr ? loud + 1 : 0;
    if (loud >= 2) { inSpeech = true; buf = pre.slice(); pre = []; quiet = 0; spoken = 0; }
    return;
  }
  buf.push(chunk);
  spoken += dur;
  quiet = rms < thr * 0.7 ? quiet + dur : 0;
  if (quiet >= END || spoken >= MAX) {
    const pcm = buf;
    const speech = spoken - quiet;
    reset();
    if (speech >= MIN) send(pcm);
  }
}

function reset() { inSpeech = false; buf = []; pre = []; loud = 0; quiet = 0; spoken = 0; }

function wav(chunks) {
  // склеить, перевести в 16 кГц моно, упаковать в WAV
  let n = 0;
  chunks.forEach((c) => { n += c.length; });
  const all = new Float32Array(n);
  let o = 0;
  chunks.forEach((c) => { all.set(c, o); o += c.length; });
  const ratio = rate / 16000;
  const len = Math.floor(all.length / ratio);
  const out = new DataView(new ArrayBuffer(44 + len * 2));
  const str = (p, s) => { for (let i = 0; i < s.length; i++) out.setUint8(p + i, s.charCodeAt(i)); };
  str(0, 'RIFF'); out.setUint32(4, 36 + len * 2, true); str(8, 'WAVE'); str(12, 'fmt ');
  out.setUint32(16, 16, true); out.setUint16(20, 1, true); out.setUint16(22, 1, true);
  out.setUint32(24, 16000, true); out.setUint32(28, 32000, true); out.setUint16(32, 2, true);
  out.setUint16(34, 16, true); str(36, 'data'); out.setUint32(40, len * 2, true);
  for (let i = 0; i < len; i++) {
    const a = Math.floor(i * ratio), b = Math.min(all.length, Math.floor((i + 1) * ratio));
    let s = 0;
    for (let j = a; j < b; j++) s += all[j];
    const v = Math.max(-1, Math.min(1, s / Math.max(1, b - a)));
    out.setInt16(44 + i * 2, v < 0 ? v * 0x8000 : v * 0x7fff, true);
  }
  return new Blob([out], { type: 'audio/wav' });
}

async function send(pcm) {
  setMode('think');
  $('heard').textContent = '…';
  try {
    const r = await api('/api/talk', { method: 'POST', headers: { 'Content-Type': 'audio/wav' }, body: wav(pcm) });
    if (!r.heard) { $('heard').textContent = ''; setMode(talking ? 'listen' : 'off'); drain(); return; }
    $('heard').textContent = '«' + r.heard + '»';
    bubble('user', r.heard);
    if (r.say) bubble('assistant', r.say);
    setMode(talking ? 'listen' : 'off');
    say(r.say, r.audio);
  } catch (e) {
    $('heard').textContent = '';
    toast('⚠️ ' + e.message, 'error');
    setMode(talking ? 'listen' : 'off');
    drain();
  }
}

// ---------- экран не гаснет, пока идёт разговор ----------
let wake = null;
async function keepAwake() {
  try { if (navigator.wakeLock && !wake) wake = await navigator.wakeLock.request('screen'); } catch (e) {}
  if (wake) wake.addEventListener('release', () => { wake = null; });
}
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') {
    if (talking) keepAwake();
    if (ctx && ctx.state === 'suspended') ctx.resume();
    poll();
  }
});

// ---------- кнопки ----------
let safari = '';
// карманный режим: пока идёт разговор, касание в кармане его не выключит — только удержание ~1 с
let downAt = 0;
$('talk').addEventListener('pointerdown', () => { downAt = Date.now(); });
$('talk').addEventListener('click', async () => {
  audioCtx();
  unlockSpeech();
  if (talking && Date.now() - downAt < 800) {
    toast('Чтобы закончить — удерживай кнопку', 'warning');
    return;
  }
  haptic('tap');
  if (talking) {
    talking = false;
    stopSpeaking();
    queue.length = 0;
    setMode('off');
    return;
  }
  try {
    await startMic();
  } catch (e) {
    showMicHelp(e);
    return;
  }
  $('notice').hidden = true;
  talking = true;
  keepAwake();
  locate();
  setMode('listen');
  drain();
});

function showMicHelp(e) {
  const n = $('notice');
  const inTg = tg && tg.initData;
  n.hidden = false;
  n.innerHTML = '<div>' + (inTg
    ? 'Микрофон в Telegram не включился. Открой «Глаз» в Safari — там он работает.'
    : 'Нет доступа к микрофону: разреши его для этого сайта (аА → Настройки веб-сайта → Микрофон).') + '</div>';
  if (inTg && safari) {
    const b = document.createElement('button');
    b.className = 'btn primary';
    b.textContent = '🧭 Открыть в Safari';
    b.onclick = () => tg.openLink(safari);
    n.appendChild(b);
  }
  document.body.classList.add('mic-err');
  $('state').textContent = 'Нет микрофона';
  $('hint').textContent = 'Без него слушать не могу';
  haptic('error');
  if (e) console.warn(e);
}

$('stop').addEventListener('click', () => { haptic('tap'); stopSpeaking(); queue.length = 0; });
$('new').addEventListener('click', async () => {
  haptic('tap');
  try {
    await api('/api/new', { method: 'POST' });
    $('heard').textContent = ''; $('say').textContent = ''; clearChat();
    toast('🧹 Начали заново', 'success');
  } catch (e) { toast('⚠️ ' + e.message, 'error'); }
});

$('pick').addEventListener('change', async (ev) => {
  audioCtx();
  unlockSpeech();
  const files = Array.from(ev.target.files || []);
  ev.target.value = '';
  for (const f of files) {
    const video = (f.type || '').startsWith('video/');
    try {
      toast(video ? '🎬 Загружаю видео…' : '👀 Смотрю на фото…');
      await api('/api/upload', { method: 'POST', headers: { 'Content-Type': f.type || 'image/jpeg' }, body: f });
      if (video) toast('🎬 Смотрю видео…');
    } catch (e) { toast('⚠️ ' + e.message, 'error'); }
  }
});

// ---------- что видел: лента фото ----------
const thumbs = {};
async function thumb(id) {
  if (thumbs[id]) return thumbs[id];
  const r = await fetch('/api/photo/' + id, { headers: auth() });
  if (!r.ok || !(r.headers.get('Content-Type') || '').startsWith('image/')) return '';
  thumbs[id] = URL.createObjectURL(await r.blob());
  return thumbs[id];
}

function time(t) {
  const d = new Date(t * 1000);
  return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}

let photos = [];
function renderFeed(list) {
  photos = list || [];
  const feed = $('feed');
  $('seen-n').textContent = photos.length ? String(photos.length) : '';
  if (!photos.length) {
    feed.innerHTML = '<div class="empty"><span class="e">🕶</span><b>Ещё ничего</b>' +
      'Пришли фото или видео с очков в чат или нажми «Выбрать»</div>';
    return;
  }
  feed.innerHTML = photos.map((p, i) => {
    const wait = !p.text && !p.title;
    return `<button class="row tap" data-i="${i}">` +
      `<span class="thumb"><img alt="" data-id="${esc(p.id)}"${thumbs[p.id] ? ` src="${esc(thumbs[p.id])}"` : ''}>${p.video ? '<span class="vid">▶</span>' : ''}</span>` +
      '<span class="main">' +
      (wait ? '<span class="title"><span class="pill accent dot live">Смотрю…</span></span>'
        : `<span class="title">${esc(p.title || (p.video ? 'Видео' : 'Фото'))}</span>` +
          `<span class="meta two">${esc(p.text)}</span>`) +
      (p.place ? `<span class="where">📍 ${esc(p.place)}</span>` : '') +
      `</span><span class="end">${time(p.t)}</span></button>`;
  }).join('');
  feed.querySelectorAll('img[data-id]').forEach((img) => {
    thumb(img.dataset.id).then((u) => { if (u) img.src = u; });
  });
}

$('feed').addEventListener('click', (ev) => {
  const row = ev.target.closest('[data-i]');
  if (!row) return;
  const p = photos[+row.dataset.i];
  if (!p) return;
  haptic('tap');
  const sh = Kit.sheet(
    (thumbs[p.id] ? `<img class="shot" alt="" src="${esc(thumbs[p.id])}">` : '') +
    `<h2>${esc(p.title || (p.text ? (p.video ? 'Видео' : 'Фото') : 'Смотрю…'))}</h2>` +
    (p.text ? `<div class="txt">${esc(p.text)}</div>` : '<div class="txt hint">Разбор ещё идёт — скажу вслух, как только узнаю.</div>') +
    `<div class="where">${time(p.t)}${p.place ? ' · 📍 ' + esc(p.place) : ''}</div>` +
    (p.say ? '<button class="btn primary" data-act="say">🔊 Сказать вслух</button>' : '') +
    '<button class="btn" data-act="close">Закрыть</button>');
  sh.addEventListener('click', (e) => {
    const act = (e.target.closest('[data-act]') || {}).dataset;
    if (!act || !act.act) return;
    if (act.act === 'say') { audioCtx(); unlockSpeech(); say(p.say); showTab('talk'); tabs.set('talk'); }
    sh.close();
  });
});

// ---------- новые события: фото и видео из чата разбираются и звучат здесь ----------
let last = 0;
let polling = false;
async function poll() {
  if (!linked) return load();
  if (polling) return;
  polling = true;
  try {
    const r = await api('/api/events?after=' + last);
    let refresh = false;
    for (const e of r.events) {
      last = Math.max(last, e.id);
      if (e.kind === 'photo_new') { refresh = true; toast(e.video ? '🎬 Смотрю видео…' : '👀 Смотрю на фото…'); }
      if (e.kind === 'photo') {
        refresh = true;
        if (ctx) say(e.say, e.audio); else $('say').textContent = e.say;
      }
      if (e.kind === 'talk') refresh = true;
      if (e.kind === 'error') toast('⚠️ ' + e.text, 'error');
    }
    if (refresh) load();
  } catch (e) { /* сеть пропала — попробуем позже */ }
  polling = false;
}

let linked = false;  // /api/state хоть раз ответил; нет — poll() пробует снова, «Нет связи» не навсегда
async function load() {
  try {
    const s = await api('/api/state');
    if (!linked) { linked = true; setMode(mode); }  // вернуть «Готов слушать» вместо «Нет связи»
    if (!last) last = s.last;
    safari = s.safari || '';
    uiMode = s.mode || '';
    $('tochat-meta').textContent = uiMode === 'chat' ? 'режим чата уже включён' : 'бот перейдёт в режим чата: фото и голос туда, ответы там же';
    if (s.place) $('place').textContent = '📍 ' + s.place;
    renderChat(s.history);
    renderFeed(s.photos || []);
  } catch (e) {
    noLink(e.message);
  }
}

function noLink(why) {
  linked = false;
  $('state').textContent = 'Нет связи';
  $('hint').textContent = why;
  document.body.classList.add('mic-err');
  $('chat').innerHTML = '<div class="empty"><span class="e">📡</span><b>Нет связи с сервером</b>' + esc(why) + '</div>';
  $('feed').innerHTML = '<div class="empty"><span class="e">📡</span><b>Нет связи с сервером</b>' + esc(why) + '</div>';
}

// ---------- где я: GPS телефона ----------
let watching = false;
function locate() {
  if (watching || !navigator.geolocation) return;
  watching = true;
  navigator.geolocation.watchPosition((p) => {
    const first = !where;
    where = { lat: p.coords.latitude.toFixed(6), lon: p.coords.longitude.toFixed(6) };
    if (first) api('/api/where', { method: 'POST' }).then((r) => { if (r.place) $('place').textContent = '📍 ' + r.place; }).catch(() => {});
  }, () => { $('place').textContent = '📍 место неизвестно — нет доступа к GPS'; },
  { enableHighAccuracy: true, maximumAge: 30000, timeout: 20000 });
}

// ---------- старт ----------
try { document.querySelector('meta[name=theme-color]').content = getComputedStyle(document.body).backgroundColor; } catch (e) {}
if (!(tg && tg.initData) && !key) {
  noLink('Открой «Глаз» из Telegram или по ссылке из /link');
  $('state').textContent = 'Нет доступа';
} else {
  load();
  locate();
  setInterval(() => { if (document.visibilityState === 'visible') poll(); }, 2500);
}
