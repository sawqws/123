// Запасной способ открыть поиск Авито — настоящим Chromium (если простой запрос Авито не пустил).
// node avito/search.js <url> → HTML страницы в stdout. Код выхода 2 — Авито показал блокировку.
const fs = require('fs');

const PW = fs.existsSync('/opt/node-tools/node_modules/playwright')
  ? '/opt/node-tools/node_modules/playwright'
  : 'playwright';
const { chromium } = require(PW);

(async () => {
  const url = process.argv[2];
  if (!url) { console.error('node search.js <url>'); process.exit(1); }
  const exe = fs.existsSync('/opt/pw-browsers/chromium') ? '/opt/pw-browsers/chromium' : undefined;
  const browser = await chromium.launch({ headless: true, executablePath: exe });
  try {
    const ctx = await browser.newContext({
      locale: 'ru-RU',
      viewport: { width: 1366, height: 900 },
      userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) '
        + 'Chrome/131.0.0.0 Safari/537.36',
    });
    const page = await ctx.newPage();
    const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 });
    await page.waitForSelector('[data-marker="item"]', { timeout: 15000 }).catch(() => {});
    const html = await page.content();
    process.stdout.write(html);
    const blocked = (resp && [403, 429].includes(resp.status())) || /Доступ ограничен|firewall-title/.test(html);
    process.exitCode = blocked ? 2 : 0;
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error(String(e)); process.exit(1); });
