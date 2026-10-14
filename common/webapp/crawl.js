// Протыкать мини-приложение целиком: каждая кнопка на каждом экране (до глубины --depth), без сервера и Telegram —
// как shots.js (те же примеры.json). Ловит ошибки JS, console.error, горизонтальную прокрутку, запросы к API без примера.
//
//   node common/webapp/crawl.js <папка webapp> <примеры.json> [--depth 3] [--max 200] [--json отчёт.json]
//
// Каждое нажатие — с чистой загрузки: путь до экрана повторяется заново, так листы и вкладки не мешают друг другу.
// Пустые поля перед нажатием заполняются («тест 123», числа — 5), чтобы доходить до отправки форм.
// Код выхода 1 — если нашлись ошибки JS или прокрутка вбок.
const fs = require('fs');
const { serve, chromium } = require('./shots.js');

const args = process.argv.slice(2);
const pos = args.filter((a, i) => !a.startsWith('--') && !(args[i - 1] || '').startsWith('--'));
const opt = (k, d) => { const i = args.indexOf('--' + k); return i >= 0 ? args[i + 1] : d; };
const [dir, fixFile] = pos;
if (!dir || !fixFile) {
  console.error('node crawl.js <webapp> <fixtures.json> [--depth 3] [--max 200] [--json out.json]');
  process.exit(2);
}
const fix = JSON.parse(fs.readFileSync(fixFile, 'utf8'));
const DEPTH = +opt('depth', 3);
const MAX = +opt('max', 200);

// что можно нажать: видимое, не выключенное, не внешняя ссылка
const CLICKABLES = `(() => {
  const sel = 'button, a[href], [role="button"], [data-tab], [data-p], .row.tap, .chip, label[for], summary, [onclick]';
  const seen = new Map();
  const out = [];
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    if (!r.width || !r.height || st.visibility === 'hidden' || st.pointerEvents === 'none' || el.disabled) continue;
    if (el.closest('[hidden]') || +st.opacity === 0) continue;
    if (el.tagName === 'A' && /^(https?:|tg:|mailto:)/.test(el.getAttribute('href') || '') &&
        !el.href.startsWith(location.origin)) continue;
    // под листом (sheet) открыт только он сам
    const sheet = document.querySelector('.sheet.open, .sheet:not(.sheet-backdrop) > div');
    if (sheet && !sheet.contains(el) && !el.closest('.sheet')) continue;
    const text = (el.innerText || el.getAttribute('aria-label') || el.title || el.dataset.tab || el.className || el.tagName)
      .replace(/\\s+/g, ' ').trim().slice(0, 50);
    const n = seen.get(text) || 0;
    seen.set(text, n + 1);
    out.push({ text, n });
  }
  return out;
})()`;

const SIGNATURE = `(() => {
  const h = [...document.querySelectorAll('h1, h2, .title-lg, .sheet h2, .tabbar .on, .tabs .on, .seg .on')]
    .filter((e) => e.offsetParent).map((e) => e.innerText.trim().slice(0, 30)).join('|');
  return h + '#' + location.pathname + location.search;
})()`;

const FILL = `(() => {
  for (const el of document.querySelectorAll('input, textarea')) {
    if (!el.offsetParent || el.value || el.type === 'file' || el.type === 'checkbox' || el.type === 'radio' || el.readOnly) continue;
    el.value = el.type === 'number' || el.inputMode === 'numeric' ? '5' : el.type === 'time' ? '09:00'
      : el.type === 'date' ? '2026-10-14' : 'тест 123';
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }
})()`;

async function locate(page, c) {
  const all = await page.evaluateHandle(`(() => {
    const sel = 'button, a[href], [role="button"], [data-tab], [data-p], .row.tap, .chip, label[for], summary, [onclick]';
    return [...document.querySelectorAll(sel)].filter((el) => {
      const r = el.getBoundingClientRect();
      return r.width && r.height && !el.closest('[hidden]');
    });
  })()`);
  const els = await all.getProperties();
  let n = 0;
  for (const h of els.values()) {
    const el = h.asElement();
    if (!el) continue;
    const t = await el.evaluate((e) => (e.innerText || e.getAttribute('aria-label') || e.title || e.dataset.tab || e.className || e.tagName)
      .replace(/\s+/g, ' ').trim().slice(0, 50));
    if (t === c.text && n++ === c.n) return el;
  }
  return null;
}

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1 });
  const report = [];
  const tried = new Set();
  const seenScreens = new Set();
  const queue = [[]];  // пути: список нажатий от загрузки
  let clicks = 0;

  async function fresh(path, log) {
    const page = await ctx.newPage();
    page.on('pageerror', (e) => log.errors.push('JS: ' + e.message));
    page.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) log.errors.push('console: ' + m.text()); });
    page.on('filechooser', () => log.notes.push('открылся выбор файла'));
    page.on('dialog', (d) => d.dismiss().catch(() => {}));
    const url = await serve(page, dir, fix, true, (m, p, has) => log.api.push(`${m} ${p}${has ? '' : ' (нет примера)'}`));
    await page.goto(url);
    await page.waitForTimeout(fix.wait || 700);
    for (const c of path) {
      const el = await locate(page, c);
      if (!el) { log.notes.push(`путь сломался на «${c.text}»`); return page; }
      await page.evaluate(FILL);
      await el.click({ timeout: 1500 }).catch(() => el.evaluate((e) => e.click()).catch(() => {}));
      await page.waitForTimeout(350);
    }
    return page;
  }

  while (queue.length && clicks < MAX) {
    const path = queue.shift();
    const probe = { errors: [], api: [], notes: [] };
    const page = await fresh(path, probe);
    const screen = await page.evaluate(SIGNATURE);
    const items = await page.evaluate(CLICKABLES);
    await page.close();
    if (path.length === 0 && probe.errors.length) report.push({ path: '(загрузка)', click: '', ...probe });
    for (const c of items) {
      const key = screen + '→' + c.text + '#' + c.n;
      if (tried.has(key) || clicks >= MAX) continue;
      tried.add(key);
      clicks++;
      const log = { errors: [], api: [], notes: [] };
      const p = await fresh(path, log);
      log.errors.length = 0; log.api.length = 0;  // считаем только то, что после нажатия
      const el = await locate(p, c);
      if (!el) { await p.close(); continue; }
      await p.evaluate(FILL);
      // перекрыто (плавающая кнопка, вкладки) — жмём напрямую, как палец по видимой части
      await el.click({ timeout: 1500 }).catch(() => el.evaluate((e) => e.click()).catch((e) => log.notes.push('не нажалось: ' + e.message.split('\n')[0])));
      await p.waitForTimeout(600);
      const over = await p.evaluate(() => document.documentElement.scrollWidth - innerWidth);
      if (over > 1) log.errors.push(`прокрутка вбок на ${over}px`);
      const after = await p.evaluate(SIGNATURE).catch(() => '');
      await p.close();
      const rec = { path: path.map((x) => x.text).join(' › ') || '(главный)', click: c.text, ...log };
      if (log.errors.length || log.notes.length) report.push(rec);
      else report.push({ path: rec.path, click: c.text, api: log.api, ok: true });
      if (after && !seenScreens.has(after) && path.length + 1 < DEPTH) {
        seenScreens.add(after);
        queue.push([...path, c]);
      }
    }
  }
  await browser.close();
  const bad = report.filter((r) => r.errors && r.errors.length);
  for (const r of report) {
    if (r.ok) continue;
    console.log(`• ${r.path} › «${r.click}»`);
    for (const e of r.errors || []) console.log('   ❌ ' + e);
    for (const n of r.notes || []) console.log('   · ' + n);
  }
  const noFix = [...new Set(report.flatMap((r) => r.api || []).filter((a) => a.includes('нет примера')))];
  if (noFix.length) console.log('Запросы без примера (ответ {}):\n  ' + noFix.join('\n  '));
  console.log(`Нажатий: ${clicks}, экранов: ${seenScreens.size + 1}, с ошибками: ${bad.length}`);
  if (opt('json')) fs.writeFileSync(opt('json'), JSON.stringify(report, null, 1));
  process.exit(bad.length ? 1 : 0);
})();
