// Проверка сайта-зеркала «от А до Я» (Глеб 19.10): обходит страницы платформы через зеркало и ищет баги.
//   node bot/sitecheck.js <адрес зеркала> <out.json> [--orig] [--max 40]
//   SITE_TOKEN — cookie hdp_site (вход в зеркало; bot/sitecheck.py выдаёт и потом закрывает).
// На каждой странице: ошибки JS, ответы 5xx и 4xx API, оборвавшиеся запросы, битые картинки, вылезание вбок
// (компьютер, iPad, телефон), долгие запросы, есть ли кнопки бота. Панель бота: каждая вкладка открывается без ошибок.
// С --orig та же страница открывается на настоящей платформе (сессия horo/tmp/state.json): ошибки, которые есть и там,
// помечаются «есть и на платформе» — чинить их не нам.
// Ничего не меняет на платформе: все запросы, кроме чтения, перехватываются и не уходят (отметки «прочитано» и т. п.);
// страницы вопросов теста, попыток и отдельных чатов не открываются.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const PW = fs.existsSync('/opt/node-tools/node_modules/playwright') ? '/opt/node-tools/node_modules/playwright' : 'playwright';
const { chromium } = require(PW);

const args = process.argv.slice(2);
const BASE = (args[0] || '').replace(/\/$/, '');
const OUT = args[1] || 'sitecheck.json';
const ORIG = args.includes('--orig');
const MAX = Number((args[args.indexOf('--max') + 1] || '').match(/^\d+$/) ? args[args.indexOf('--max') + 1] : 40);
const TOKEN = process.env.SITE_TOKEN || '';
const STATE = path.join(__dirname, '..', 'horo', 'tmp', 'state.json');
const PLATFORM = 'https://horodigital.ru';

const VIEWS = { desktop: { width: 1366, height: 900 }, ipad: { width: 820, height: 1180 }, phone: { width: 390, height: 844 } };
const SKIP = /\/questions|\/attempt|\/logout|\/chat\/|\/login|\/print/;
const PER_KIND = { topic: 8, task: 14, default: 4 };  // похожих страниц (одна схема адреса) — не больше
// шум, который не баг: аналитика (её глушит guard.js), отменённые при уходе со страницы запросы
const NOISE = /mc\.yandex|metrika|sentry|favicon|net::ERR_ABORTED|ResizeObserver loop|Failed to load resource: net::ERR_ABORTED/i;

function proxyArgs() {
  const ca = '/root/.ccr/agent-proxy-ca.crt';
  if (!fs.existsSync(ca)) return [];
  const key = new crypto.X509Certificate(fs.readFileSync(ca)).publicKey.export({ type: 'spki', format: 'der' });
  return ['--ignore-certificate-errors-spki-list=' + crypto.createHash('sha256').update(key).digest('base64')];
}

const kindOf = p => (/\/task\//.test(p) ? 'task' : /\/topic\//.test(p) ? 'topic' : 'default');
const schemaOf = p => p.replace(/[0-9a-f]{8}-[0-9a-f-]{27,}/g, ':id').replace(/\/\d+(?=\/|$)/g, '/:n');

// только чтение: всё, что меняет платформу, не уходит (страница получает «ок» и живёт дальше)
async function readOnly(page) {
  await page.route('**/*', route => {
    const r = route.request();
    if (!['GET', 'HEAD', 'OPTIONS'].includes(r.method()) && /\/api\//.test(r.url()) && !/\/api\/(site\/(exams|wait|times|draft\/files|theory|running)|state|me|prep\/state)(\?|$)/.test(r.url())) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: '{"success":true,"data":null}' });
    }
    if (/updateReadPosition=true/.test(r.url())) return route.continue({ url: r.url().replace('updateReadPosition=true', 'updateReadPosition=false') });
    return route.continue();
  });
}

function watch(page, sink) {
  page.on('pageerror', e => sink.push({ kind: 'js', text: String(e.message || e).slice(0, 300) }));
  page.on('console', m => { if (m.type() === 'error' && !NOISE.test(m.text())) sink.push({ kind: 'console', text: m.text().slice(0, 300) }); });
  page.on('requestfailed', r => {
    const f = (r.failure() || {}).errorText || '';
    if (!NOISE.test(r.url() + ' ' + f)) sink.push({ kind: 'failed', text: `${r.method()} ${short(r.url())} — ${f}` });
  });
  page.on('response', async r => {
    const u = r.url(), s = r.status();
    if (NOISE.test(u)) return;
    if (s >= 500 || (s >= 400 && /\/api\//.test(u) && s !== 401)) sink.push({ kind: 'http', text: `${s} ${r.request().method()} ${short(u)}` });
    const t = r.request().timing();
    if (t && t.responseEnd > 8000) sink.push({ kind: 'slow', text: `${Math.round(t.responseEnd / 1000)} с ${short(u)}` });
  });
}
const short = u => u.replace(/^https?:\/\/[^/]+/, '').slice(0, 140);

async function visit(page, url, sink) {
  const t0 = Date.now();
  let status = 0;
  try {
    const r = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 });
    status = r ? r.status() : 0;
    await page.waitForLoadState('networkidle', { timeout: 20000 }).catch(() => {});
    await page.waitForTimeout(1500);
    // 23.10: страница задания под нагрузкой рисуется 10–20 с (заготовка без текста) — «пустая» была ложной тревогой
    await page.waitForFunction(() => (document.querySelector('main, #root, #app') || document.body).innerText.trim().length >= 20,
      null, { timeout: 15000 }).catch(() => {});
  } catch (e) { sink.push({ kind: 'nav', text: String(e.message).split('\n')[0].slice(0, 200) }); }
  const ms = Date.now() - t0;
  const info = await page.evaluate(() => {
    const vw = innerWidth;
    const broken = [...document.images].filter(i => i.complete && i.currentSrc && i.naturalWidth === 0 && i.offsetWidth > 0
      && !i.closest('[data-hdpx], .hdpx-vv')).map(i => i.currentSrc.replace(location.origin, '').slice(0, 120));
    const wide = document.documentElement.scrollWidth > vw + 2;
    const culprit = wide ? [...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > vw + 2 && !e.closest('.hdpx-vv'))
      .slice(0, 3).map(e => e.tagName.toLowerCase() + (e.className && typeof e.className === 'string' ? '.' + e.className.split(' ')[0] : '')) : [];
    const hrefs = [...document.querySelectorAll('a[href]')].map(a => a.getAttribute('href')).filter(h => h && h.startsWith('/student'));
    const empty = (document.querySelector('main, #root, #app') || document.body).innerText.trim().length < 20;
    return { broken, wide, culprit, hrefs, empty, bot: !!document.querySelector('.hdpx-tab, #hdpx-on'), title: document.title, url: location.pathname };
  }).catch(e => ({ broken: [], wide: false, culprit: [], hrefs: [], empty: true, bot: false, err: e.message }));
  if (info.url && /\/login|\/site\/?$/.test(info.url)) sink.push({ kind: 'login', text: 'выкинуло на вход: ' + info.url });
  if (status >= 400) sink.push({ kind: 'http', text: `страница ${status}` });
  for (const b of info.broken) sink.push({ kind: 'img', text: 'картинка не загрузилась: ' + b });
  if (info.wide) sink.push({ kind: 'wide', text: 'страница вылезает вбок: ' + info.culprit.join(', ') });
  if (info.empty) sink.push({ kind: 'empty', text: 'страница пустая' });
  return { ms, status, ...info };
}

async function panel(page, sink) {
  // панель бота: открыть, пройти все вкладки, закрыть — без ошибок и пустых экранов
  const tab = await page.$('.hdpx-tab');
  if (!tab) { sink.push({ kind: 'bot', text: 'нет кнопки бота 🤖' }); return; }
  await tab.click().catch(() => {});
  await page.waitForTimeout(800);
  const tabs = await page.$$eval('.hdpx-tabbtn', a => a.map(x => x.dataset.v));
  for (const v of tabs) {
    await page.click(`.hdpx-tabbtn[data-v="${v}"]`).catch(() => {});
    await page.waitForTimeout(1800);
    const txt = await page.$eval('.hdpx-dr-body', e => e.innerText.trim()).catch(() => '');
    if (!txt) sink.push({ kind: 'bot', text: `вкладка «${v}» пустая` });
    if (/Ошибка сервера|Ошибка 5\d\d/.test(txt)) sink.push({ kind: 'bot', text: `вкладка «${v}»: ${txt.split('\n').find(l => /Ошибка/.test(l))}` });
  }
  await page.click('.hdpx-drawer [data-a="close"]').catch(() => {});
}

(async () => {
  const exe = fs.existsSync('/opt/pw-browsers/chromium') ? '/opt/pw-browsers/chromium' : undefined;
  const browser = await chromium.launch({ headless: true, executablePath: exe, args: proxyArgs(),
    proxy: process.env.HTTPS_PROXY ? { server: process.env.HTTPS_PROXY } : undefined });
  const host = new URL(BASE).hostname;
  const mk = async view => {
    const ctx = await browser.newContext({ viewport: VIEWS[view], ignoreHTTPSErrors: true, locale: 'ru-RU',
      isMobile: view === 'phone', hasTouch: view !== 'desktop' });
    await ctx.addCookies([{ name: 'hdp_site', value: TOKEN, domain: host, path: '/', secure: true, httpOnly: true }]);
    const page = await ctx.newPage();
    await readOnly(page);
    return { ctx, page };
  };
  const pages = [], t0 = Date.now();
  const { page } = await mk('desktop');
  const queue = ['/student'], seen = new Set(), perSchema = {};
  let sink = [];
  watch(page, { push: x => sink.push(x) });
  while (queue.length && pages.length < MAX) {
    const p = queue.shift().split('#')[0].split('?')[0].replace(/\/$/, '') || '/student';
    if (seen.has(p) || SKIP.test(p)) continue;
    const sc = schemaOf(p);
    perSchema[sc] = (perSchema[sc] || 0) + 1;
    if (perSchema[sc] > (PER_KIND[kindOf(p)] || PER_KIND.default)) continue;
    seen.add(p);
    sink = [];
    const info = await visit(page, BASE + p, sink);
    if (pages.length === 0) await panel(page, sink);  // панель бота — один раз на главной
    if (kindOf(p) === 'task' && !pages.some(x => x.kind === 'task')) {
      if (!(await page.$('.hdpx-actionbar'))) sink.push({ kind: 'bot', text: 'на странице задания нет кнопок бота внизу' });
      await panel(page, sink);
    }
    pages.push({ path: p, kind: kindOf(p), ms: info.ms, title: info.title, issues: sink });
    for (const h of info.hrefs) if (!seen.has(h.split('?')[0])) queue.push(h);
  }
  // те же главные страницы — на iPad и телефоне (вёрстка, ошибки)
  for (const view of ['ipad', 'phone']) {
    const { ctx, page: pv } = await mk(view);
    for (const pg of pages.slice(0, 6)) {
      const s = [];
      watch(pv, { push: x => s.push(x) });
      await visit(pv, BASE + pg.path, s);
      for (const x of s) pg.issues.push({ ...x, view });
      pv.removeAllListeners('pageerror'); pv.removeAllListeners('console'); pv.removeAllListeners('requestfailed'); pv.removeAllListeners('response');
    }
    await ctx.close();
  }
  // сверка с настоящей платформой: что есть и там — не наше
  if (ORIG && fs.existsSync(STATE)) {
    const ctx = await browser.newContext({ viewport: VIEWS.desktop, storageState: STATE, locale: 'ru-RU' });
    const po = await ctx.newPage();
    await readOnly(po);
    for (const pg of pages.filter(x => x.issues.some(i => !i.view))) {
      const s = [];
      watch(po, { push: x => s.push(x) });
      await visit(po, PLATFORM + pg.path, s);
      const theirs = new Set(s.map(x => x.kind + '|' + x.text.replace(/\d+ с /, '')));
      for (const i of pg.issues) if (!i.view && theirs.has(i.kind + '|' + i.text.replace(/\d+ с /, ''))) i.platform = true;
      po.removeAllListeners('pageerror'); po.removeAllListeners('console'); po.removeAllListeners('requestfailed'); po.removeAllListeners('response');
    }
    await ctx.close();
  }
  await browser.close();
  // свести: одинаковые ошибки на разных страницах — одной строкой
  const all = {};
  for (const pg of pages) for (const i of pg.issues) {
    const k = i.kind + '|' + i.text;
    (all[k] = all[k] || { kind: i.kind, text: i.text, platform: !!i.platform, views: new Set(), pages: [] });
    all[k].views.add(i.view || 'desktop');
    if (all[k].pages.length < 5 && !all[k].pages.includes(pg.path)) all[k].pages.push(pg.path);
  }
  const issues = Object.values(all).map(x => ({ ...x, views: [...x.views] }))
    .sort((a, b) => (a.platform - b.platform) || (a.kind === 'slow') - (b.kind === 'slow'));
  const slowest = [...pages].sort((a, b) => b.ms - a.ms).slice(0, 3).map(p => ({ path: p.path, ms: p.ms }));
  const report = { at: Math.round(Date.now() / 1000), base: BASE, secs: Math.round((Date.now() - t0) / 1000), pages: pages.length,
    avg_ms: Math.round(pages.reduce((a, p) => a + p.ms, 0) / Math.max(1, pages.length)), slowest, issues,
    list: pages.map(p => ({ path: p.path, ms: p.ms, n: p.issues.length })) };
  fs.writeFileSync(OUT, JSON.stringify(report, null, 1));
  const ours = issues.filter(i => !i.platform && i.kind !== 'slow');
  console.log(`СТРАНИЦ: ${pages.length} · ОШИБОК: ${ours.length} (+${issues.length - ours.length} не наших/медленных) · ${report.secs} с`);
  process.exit(0);  // отчёт записан — не ждём, пока Chromium на сервере закроется (там он иногда падает при выходе)
})().catch(e => { console.error('sitecheck:', e.message); process.exit(1); });
