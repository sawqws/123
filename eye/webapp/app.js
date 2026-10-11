// «Глаз»: разговор без рук через очки (Bluetooth-гарнитура iPhone) и фото с очков.
// Микрофон -> свой детектор речи (громкость) -> WAV 16 кГц -> /api/talk -> ответ голосом через AudioContext.
'use strict';

const tg = window.Telegram && Telegram.WebApp;
const $ = (id) => document.getElementById(id);

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

let toastTimer;
function toast(text) {
  const t = $('toast');
  t.textContent = text;
  t.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('on'), 3500);
}

// ---------- состояние разговора ----------
// off — выключен; listen — слушаю; think — Claude думает; speak — говорю
let mode = 'off';
const LABELS = { off: 'Нажми, чтобы говорить', listen: 'Слушаю…', think: 'Думаю…', speak: 'Говорю…' };
const ICONS = { off: '🎙', listen: '👂', think: '💭', speak: '🔊' };
let talking = false; // разговор включён (после ответа снова слушаем)

function setMode(m) {
  mode = m;
  document.body.className = m;
  $('state').textContent = LABELS[m];
  $('orb-icon').textContent = ICONS[m];
  if (m !== 'listen') $('ring').style.transform = '';
}

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
  $('ring').style.transform = 'scale(' + (1 + Math.min(rms * 6, 0.25)) + ')';
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
    setMode(talking ? 'listen' : 'off');
    say(r.say, r.audio);
  } catch (e) {
    $('heard').textContent = '';
    toast(e.message);
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
    toast('Чтобы остановить разговор — удерживай кнопку');
    return;
  }
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
  talking = true;
  keepAwake();
  locate();
  setMode('listen');
  drain();
});

function showMicHelp(e) {
  const n = $('notice');
  n.hidden = false;
  const inTg = tg && tg.initData;
  n.innerHTML = '';
  const p = document.createElement('div');
  p.textContent = inTg
    ? 'Микрофон в Telegram не включился. Открой «Глаз» в Safari — там он работает.'
    : 'Нет доступа к микрофону: разреши его для этого сайта (аА → Настройки веб-сайта → Микрофон).';
  n.appendChild(p);
  if (inTg && safari) {
    const b = document.createElement('button');
    b.className = 'btn';
    b.textContent = '🧭 Открыть в Safari';
    b.onclick = () => tg.openLink(safari);
    n.appendChild(b);
  }
  console.warn(e);
}

$('stop').addEventListener('click', () => { stopSpeaking(); queue.length = 0; });
$('new').addEventListener('click', async () => {
  try { await api('/api/new', { method: 'POST' }); $('heard').textContent = ''; $('say').textContent = ''; toast('Начали заново'); }
  catch (e) { toast(e.message); }
});

$('pick').addEventListener('change', async (ev) => {
  audioCtx();
  unlockSpeech();
  const files = Array.from(ev.target.files || []);
  ev.target.value = '';
  for (const f of files) {
    try {
      await api('/api/upload', { method: 'POST', headers: { 'Content-Type': f.type || 'image/jpeg' }, body: f });
      toast('👀 Смотрю на фото…');
    } catch (e) { toast(e.message); }
  }
});

// ---------- что видел: лента фото ----------
const thumbs = {};
async function thumb(id) {
  if (thumbs[id]) return thumbs[id];
  const r = await fetch('/api/photo/' + id, { headers: auth() });
  if (!r.ok) return '';
  thumbs[id] = URL.createObjectURL(await r.blob());
  return thumbs[id];
}

function time(t) {
  const d = new Date(t * 1000);
  return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}

async function renderFeed(photos) {
  const feed = $('feed');
  if (!photos.length) return;
  feed.innerHTML = '';
  for (const p of photos) {
    const el = document.createElement('div');
    el.className = 'item';
    const img = document.createElement('img');
    img.alt = '';
    el.appendChild(img);
    const body = document.createElement('div');
    const t = document.createElement('div');
    t.className = 't';
    t.textContent = p.title || (p.text ? 'Фото' : '👀 Смотрю…');
    const d = document.createElement('div');
    d.className = 'd';
    d.textContent = p.text || '';
    const m = document.createElement('div');
    m.className = 'm';
    m.textContent = time(p.t) + (p.place ? ' · ' + p.place : '');
    body.append(t, d, m);
    el.appendChild(body);
    if (p.say) el.onclick = () => { audioCtx(); say(p.say); };
    feed.appendChild(el);
    thumb(p.id).then((u) => { if (u) img.src = u; });
  }
}

// ---------- новые события: фото из чата разбираются и звучат здесь ----------
let last = 0;
let polling = false;
async function poll() {
  if (polling) return;
  polling = true;
  try {
    const r = await api('/api/events?after=' + last);
    let refresh = false;
    for (const e of r.events) {
      last = Math.max(last, e.id);
      if (e.kind === 'photo_new') { refresh = true; toast('👀 Смотрю на фото…'); }
      if (e.kind === 'photo') { refresh = true; if (ctx) say(e.say, e.audio); else $('say').textContent = e.say; }
      if (e.kind === 'error') toast('😕 ' + e.text);
    }
    if (refresh) load();
  } catch (e) { /* сеть пропала — попробуем позже */ }
  polling = false;
}

async function load() {
  try {
    const s = await api('/api/state');
    if (!last) last = s.last;
    safari = s.safari || '';
    if (s.place) $('place').textContent = '📍 ' + s.place;
    renderFeed(s.photos);
  } catch (e) {
    $('state').textContent = e.message;
  }
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
  }, () => { $('place').textContent = '📍 место неизвестно (нет доступа к геопозиции)'; },
  { enableHighAccuracy: true, maximumAge: 30000, timeout: 20000 });
}

// ---------- старт ----------
if (tg) {
  tg.ready();
  tg.expand();
  try { tg.disableVerticalSwipes && tg.disableVerticalSwipes(); } catch (e) {}
  try { tg.setHeaderColor('#0b0c10'); tg.setBackgroundColor('#0b0c10'); } catch (e) {}
}
if (!(tg && tg.initData) && !key) {
  $('state').textContent = 'Открой «Глаз» из Telegram или по ссылке из /link';
} else {
  load();
  locate();
  setInterval(() => { if (document.visibilityState === 'visible') poll(); }, 2500);
}
