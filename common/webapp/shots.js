// Скриншоты мини-приложения без сервера и Telegram: файлы берутся из папки webapp,
// ответы API — из файла с примерами, Telegram.WebApp подменяется заглушкой.
//
//   node common/webapp/shots.js <папка webapp> <примеры.json> <куда/префикс> [--light]
//
// примеры.json:
//   { "base": "/",                              // где открыто приложение (у Главного "/app/")
//     "api": { "GET /api/state": {...},         // ответ по «МЕТОД путь» или просто «путь»
//              "/api/x": {"status": 500, "body": {"error": "…"}} },   // со своим кодом
//     "shots": [ {"name": "home"},
//                {"name": "tab2", "click": ["#tabs button:nth-child(2)"], "wait": 400} ] }
// Без ответа в примерах — {}.
const fs = require('fs');
const path = require('path');
const PW = fs.existsSync('/opt/node-tools/node_modules/playwright')
  ? '/opt/node-tools/node_modules/playwright' : 'playwright';
const { chromium } = require(PW);

const [dir, fixFile, out] = process.argv.slice(2).filter((a) => !a.startsWith('--'));
const light = process.argv.includes('--light');
if (!dir || !fixFile || !out) {
  console.error('node shots.js <webapp> <fixtures.json> <out-prefix> [--light]');
  process.exit(2);
}
const fix = JSON.parse(fs.readFileSync(fixFile, 'utf8'));
const base = fix.base || '/';
const ORIGIN = 'https://app.test';

const THEMES = {
  dark: { bg_color: '#212121', secondary_bg_color: '#0f0f0f', section_bg_color: '#1c1c1d', text_color: '#ffffff',
    hint_color: '#8d8e93', link_color: '#6ab3f3', button_color: '#3e88f7', button_text_color: '#ffffff',
    header_bg_color: '#1c1c1d', section_separator_color: '#2c2c2e', destructive_text_color: '#eb5545',
    subtitle_text_color: '#8d8e93', accent_text_color: '#6ab3f3' },
  light: { bg_color: '#ffffff', secondary_bg_color: '#efeff4', section_bg_color: '#ffffff', text_color: '#000000',
    hint_color: '#8e8e93', link_color: '#007aff', button_color: '#007aff', button_text_color: '#ffffff',
    header_bg_color: '#f7f7f7', section_separator_color: '#e0e0e3', destructive_text_color: '#ff3b30',
    subtitle_text_color: '#8e8e93', accent_text_color: '#007aff' },
};
const theme = light ? THEMES.light : THEMES.dark;

// Заглушка telegram-web-app.js: то, чем пользуются наши приложения
const STUB = `(() => {
  const tp = ${JSON.stringify(theme)};
  const root = document.documentElement.style;
  for (const [k, v] of Object.entries(tp)) root.setProperty('--tg-theme-' + k.replace(/_/g, '-'), v);
  root.setProperty('--tg-viewport-height', innerHeight + 'px');
  root.setProperty('--tg-viewport-stable-height', innerHeight + 'px');
  const noop = () => {};
  const btn = () => ({ show: noop, hide: noop, onClick: noop, offClick: noop, setText: noop, setParams: noop,
    enable: noop, disable: noop, showProgress: noop, hideProgress: noop, isVisible: false });
  window.Telegram = { WebApp: {
    initData: 'query_id=x&user=%7B%22id%22%3A1%7D&hash=x', initDataUnsafe: { user: { id: 1, first_name: 'Глеб' } },
    colorScheme: '${light ? 'light' : 'dark'}', themeParams: tp, platform: 'ios', version: '8.0',
    viewportHeight: innerHeight, viewportStableHeight: innerHeight, isExpanded: true,
    ready: noop, expand: noop, close: noop, onEvent: noop, offEvent: noop, sendData: noop,
    setHeaderColor: noop, setBackgroundColor: noop, setBottomBarColor: noop, enableClosingConfirmation: noop,
    disableVerticalSwipes: noop, requestFullscreen: noop, isVersionAtLeast: () => true,
    openLink: noop, openTelegramLink: noop, showAlert: (m, cb) => cb && cb(), showConfirm: (m, cb) => cb && cb(true),
    showPopup: (p, cb) => cb && cb('ok'), HapticFeedback: { impactOccurred: noop, notificationOccurred: noop,
      selectionChanged: noop }, MainButton: btn(), SecondaryButton: btn(), BackButton: btn(), SettingsButton: btn(),
    CloudStorage: { getItem: (k, cb) => cb(null, ''), setItem: (k, v, cb) => cb && cb(null, true) },
  } };
})();`;

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2,
    colorScheme: light ? 'light' : 'dark' });
  page.on('pageerror', (e) => console.error('ОШИБКА СТРАНИЦЫ:', e.message));
  page.on('console', (m) => { if (m.type() === 'error') console.error('console:', m.text()); });
  await page.route('**/*', async (route) => {
    const req = route.request();
    const u = new URL(req.url());
    if (u.hostname === 'telegram.org') return route.fulfill({ contentType: 'text/javascript', body: STUB });
    if (u.origin !== ORIGIN) return route.fulfill({ status: 204, body: '' });
    const p = u.pathname;
    const api = fix.api || {};
    const hit = api[`${req.method()} ${p}`] ?? api[p];
    if (hit !== undefined || /\/api\//.test(p)) {
      const wrapped = hit && typeof hit === 'object' && 'status' in hit && 'body' in hit;
      return route.fulfill({ status: wrapped ? hit.status : 200, contentType: 'application/json',
        body: JSON.stringify(wrapped ? hit.body : (hit ?? {})) });
    }
    let rel = p.startsWith(base) ? p.slice(base.length) : p.replace(/^\//, '');
    if (!rel || rel === 'index.html') rel = 'index.html';
    const file = path.join(dir, path.basename(rel));
    if (!fs.existsSync(file)) return route.fulfill({ status: 404, body: 'not found' });
    let body = fs.readFileSync(file);
    if (rel === 'index.html') body = body.toString().replace(/\{\{v\}\}/g, '1');
    const type = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml',
      '.png': 'image/png', '.json': 'application/json' }[path.extname(file)] || 'application/octet-stream';
    return route.fulfill({ contentType: type + '; charset=utf-8', body });
  });
  await page.goto(ORIGIN + base);
  await page.waitForTimeout(fix.wait || 900);
  fs.mkdirSync(path.dirname(out), { recursive: true });
  for (const s of fix.shots || [{ name: 'home' }]) {
    for (const sel of s.click || []) {
      await page.click(sel, { timeout: 3000 }).catch((e) => console.error('не нажалось', sel, e.message));
      await page.waitForTimeout(250);
    }
    if (s.eval) await page.evaluate(s.eval);
    await page.waitForTimeout(s.wait || 500);
    const file = `${out}-${s.name}${light ? '-light' : ''}.png`;
    await page.screenshot({ path: file, fullPage: s.full !== false });
    console.log(file);
  }
  await browser.close();
})();
