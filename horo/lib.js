// Общие функции для работы с horodigital.ru: браузер, вход, API.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const PW = fs.existsSync('/opt/node-tools/node_modules/playwright')
  ? '/opt/node-tools/node_modules/playwright'
  : 'playwright';
const { chromium } = require(PW);

const BASE = 'https://horodigital.ru';
const DIR = __dirname;
const TMP = path.join(DIR, 'tmp');
const STATE = path.join(TMP, 'state.json');
fs.mkdirSync(TMP, { recursive: true });

// В облачной среде Claude Code HTTPS идёт через прокси со своим CA.
// Chromium не читает его из системы, поэтому доверяем только этому CA по SPKI.
function proxyArgs() {
  const ca = '/root/.ccr/agent-proxy-ca.crt';
  if (!fs.existsSync(ca)) return [];
  const key = new crypto.X509Certificate(fs.readFileSync(ca)).publicKey.export({ type: 'spki', format: 'der' });
  const spki = crypto.createHash('sha256').update(key).digest('base64');
  return ['--ignore-certificate-errors-spki-list=' + spki];
}

async function open() {
  const exe = fs.existsSync('/opt/pw-browsers/chromium') ? '/opt/pw-browsers/chromium' : undefined;
  const browser = await chromium.launch({
    headless: true,
    executablePath: exe,
    proxy: process.env.HTTPS_PROXY ? { server: process.env.HTTPS_PROXY } : undefined,
    args: proxyArgs(),
  });
  const ctx = await browser.newContext({
    viewport: { width: 1366, height: 900 },
    locale: 'ru-RU',
    storageState: fs.existsSync(STATE) ? STATE : undefined,
  });
  const page = await ctx.newPage();
  await login(page);
  return { browser, ctx, page };
}

async function login(page) {
  await page.goto(BASE + '/student', { waitUntil: 'networkidle', timeout: 60000 });
  if (!page.url().includes('/login')) return;
  if (!process.env.HORO_LOGIN || !process.env.HORO_PASSWORD) throw new Error('Нет HORO_LOGIN / HORO_PASSWORD в окружении');
  await page.fill('input[name=login]', process.env.HORO_LOGIN);
  await page.fill('input[name=password]', process.env.HORO_PASSWORD);
  await page.click('button[type=submit]');
  await page.waitForURL(u => !u.toString().includes('/login'), { timeout: 30000 });
  await page.waitForLoadState('networkidle').catch(() => {});  // сайт иногда долго держит запросы — вход уже прошёл
}

async function close({ browser, ctx }) {
  await ctx.storageState({ path: STATE });
  await browser.close();
}

// GET к API из контекста страницы (с куками сессии).
async function api(page, url) {
  const res = await page.evaluate(async u => {
    const r = await fetch(u, { credentials: 'include' });
    return { s: r.status, t: await r.text() };
  }, url.startsWith('http') ? url : BASE + url);
  if (res.s !== 200) throw new Error(`API ${res.s}: ${url}\n${res.t.slice(0, 300)}`);
  return JSON.parse(res.t).data;
}

async function download(page, src, file) {
  const b64 = await page.evaluate(async u => {
    const r = await fetch(u, { credentials: 'include' });
    const b = new Uint8Array(await r.arrayBuffer());
    let s = '';
    for (const x of b) s += String.fromCharCode(x);
    return btoa(s);
  }, src.startsWith('http') ? src : BASE + src);
  fs.writeFileSync(file, Buffer.from(b64, 'base64'));
}

// https://horodigital.ru/student/topic/<topic>/task/<task>[/questions/...]
function parseUrl(url) {
  const m = url.match(/topic\/([0-9a-f-]{36})\/task\/([0-9a-f-]{36})/);
  if (!m) throw new Error('Не похоже на ссылку на задание: ' + url);
  return { topic: m[1], task: m[2], taskUrl: `${BASE}/student/topic/${m[1]}/task/${m[2]}` };
}

const EDU = '/api/education/v1/students/current';

async function loadTask(page, url) {
  const { topic, task, taskUrl } = parseUrl(url);
  const data = await api(page, `${EDU}/topics/${topic}/tasks/${task}`);
  const topicData = await api(page, `${EDU}/topics/${topic}/tasks?taskUuid=${task}`);
  const meta = topicData.tasks.find(t => t.uuid === task);
  const group = topicData.group.uuid;
  let attempts = [];
  if (data.type === 'test') attempts = await api(page, `${EDU}/study-groups/${group}/topics/${topic}/tasks/${task}/test-attempts?view=full`);
  return { topic, task, taskUrl, group, data, topicData, meta, attempts };
}

function htmlToText(h) {
  return (h || '')
    .replace(/<input[^>]*id="([^"]+)"[^>]*>/g, (_, id) => `[[${id.slice(0, 8)}]]`)
    .replace(/<img[\s\S]*?src="([^"]+)"[^>]*>/g, ' [img] ')
    .replace(/<br\s*\/?>|<\/p>|<\/li>/g, '\n')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&#39;|&rsquo;/g, "'")
    .replace(/&([a-z]+);/gi, (m, n) => ({ rarr: '→', ndash: '–', mdash: '—', laquo: '«', raquo: '»', aacute: 'á', eacute: 'é', iacute: 'í', oacute: 'ó', uacute: 'ú', ntilde: 'ñ', iquest: '¿', iexcl: '¡', Aacute: 'Á', Eacute: 'É' }[n] || m))
    .replace(/[ \t]+/g, ' ').replace(/\n\s+/g, '\n').trim();
}

function images(h) {
  return [...(h || '').matchAll(/src="([^"]+)"/g)].map(m => m[1]);
}

// Значок предмета для сообщений бота (по началу названия, без учёта регистра).
const SUBJ_ICONS = [
  ['алгебр', '➗'], ['геометр', '📐'], ['матем', '📐'], ['русск', '🖋'], ['литер', '📚'], ['англ', '🇬🇧'], ['испан', '🇪🇸'],
  ['немец', '🇩🇪'], ['франц', '🇫🇷'], ['китай', '🇨🇳'], ['истор', '🏛'], ['общест', '⚖️'], ['прав', '⚖️'], ['биолог', '🧬'],
  ['хим', '⚗️'], ['физик', '⚛️'], ['физич', '🏃'], ['физкул', '🏃'], ['географ', '🌍'], ['информ', '💻'], ['эконом', '💰'],
  ['музык', '🎵'], ['изо', '🎨'], ['искусс', '🎨'], ['техн', '🛠'], ['обж', '🛡'], ['астроном', '🔭'],
];
const subjIcon = name => (SUBJ_ICONS.find(([k]) => String(name).toLowerCase().startsWith(k)) || [0, '📘'])[1];

module.exports = { open, close, api, download, parseUrl, loadTask, htmlToText, images, subjIcon, BASE, TMP, EDU };
