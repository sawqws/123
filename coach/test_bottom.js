// Низ экрана Формы стоит на месте (Глеб 06.11: «в диктовке опять подлетает нижняя строка — протыкай каждую панельку»).
//   node coach/test_bottom.js [папка-скриншотов]          — сам поднимает фикстуру (python3 coach/shots_fixture.py --serve 8813)
//   BOTTOM_URL=http://127.0.0.1:8790/ node coach/test_bottom.js  — против уже запущенной фикстуры
// 1) iPhone 14: все вкладки Формы, лист «＋ Добавить» и всё, что из него открывается (экраны — и их листы), во вкладке
//    «Питание» — все листы «Калорий» в рамке; в каждом — фокус в поле, уход из него, закрыть. Меню Формы (.tabbar и круг
//    «Чат») не сдвигается (прятание с клавиатурой в чате — body.kb — можно), нет прокрутки вбок, нет ошибок в консоли.
// 2) Эмуляция iOS 26, приложение с экрана «Домой» (navigator.standalone, окно 430×956): поле в «Калориях» в фокусе, окно
//    «село» до 894 — Форма не мигает; ушёл из поля / закрыл лист / кончилась запись — лечит (F.heal: слой или #app), окно
//    снова 956, меню внизу; сдвиг visualViewport.offsetTop — scrollTo; Safari и компьютер — не трогает.
//    Чего эмуляция не может: настоящий WebKit, клавиатуру и сам баг iOS — «лечение» здесь срабатывает по договору теста
//    (мигнули слой или #app — окно возвращаем сами), проверяется, КОГДА Форма лечит и что меню после этого внизу.
const { chromium, devices } = require('playwright');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

const SHOTS = process.argv[2] || '';
const PORT = 8813;
const BASE = process.env.BOTTOM_URL || `http://127.0.0.1:${PORT}/`;
const results = [];
let failed = 0;
const ok = (cond, name, info = '') => { results.push(`${cond ? '✓' : '✗'} ${name}${cond ? '' : '  → ' + info}`); if (!cond) failed++; return cond; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const FIELD = 'input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=button]):not([type=submit]):not([type=range]):not([type=file]):not([disabled]), textarea:not([disabled]), [contenteditable]:not([contenteditable="false"])';

async function fixture() {
  if (process.env.BOTTOM_URL) return null;
  const srv = spawn('python3', [path.join(__dirname, 'shots_fixture.py'), '--serve', String(PORT)], { stdio: ['ignore', 'pipe', 'inherit'] });
  for (let i = 0; i < 100; i++) {
    try { if ((await fetch(BASE)).ok) return srv; } catch (e) { /* ещё не встал */ }
    await sleep(200);
  }
  srv.kill(); throw new Error('фикстура не поднялась');
}

// в Форме: модуль-шпион (F — для экранов), сообщения «Калорий» (kcal-ui); в эмуляции — подмена окна iOS
function spy(emu) {
  if (window.top !== window) return;
  (window.FormaModules = window.FormaModules || []).push(F => { window.__F = F; });
  window.__ui = [];
  addEventListener('message', e => { if (e.data && e.data.type === 'kcal-ui') window.__ui.push(e.data.what); });
  if (!emu) return;
  if (emu.standalone) Object.defineProperty(navigator, 'standalone', { configurable: true, get: () => true });
  // окно «садится» по-настоящему (setViewportSize из теста); мигнули слой/#app (как ждёт __mode) — окно сразу снова 956:
  // innerHeight — подменой (F.heal смотрит через 2 кадра), раскладка — __grow() из теста
  const ih = Object.getOwnPropertyDescriptor(window, 'innerHeight');
  window.__ih = 0; window.__mode = 'layer'; window.__flips = [];
  Object.defineProperty(window, 'innerHeight', { configurable: true, get() { return window.__ih || ih.get.call(window); } });
  // мигание видно по атрибуту style: flip ставит display: none и сразу снимает — во второй записи oldValue с «display: none»
  // (запись приходит микрозадачей — раньше, чем F.heal через 2 кадра проверит окно)
  new MutationObserver(recs => recs.forEach(r => {
    const old = r.oldValue || '';
    if (!/display:\s*none/.test(old)) return;
    const who = r.target.id === 'app' ? 'app' : /100lvh/.test(old) ? 'layer' : '';
    if (!who) return;
    window.__flips.push(who);
    if (window.__low && window.__mode === who) { window.__low = false; window.__ih = window.__full; window.__grow && window.__grow(); }
  })).observe(document, { subtree: true, attributes: true, attributeFilter: ['style'], attributeOldValue: true });
  // iOS 26: visualViewport.offsetTop после клавиатуры не возвращается в 0; scrollTo его сбрасывает (договор теста)
  if (window.visualViewport) Object.defineProperty(visualViewport, 'offsetTop', { configurable: true, get: () => window.__vt || 0 });
  const st = window.scrollTo;
  window.scrollTo = function () { window.__vt = 0; return st.apply(this, arguments); };
}

async function bars(page) {
  return page.evaluate(() => {
    const r = s => { const e = document.querySelector(s); if (!e) return null; const b = e.getBoundingClientRect(); return [b.top, b.bottom, b.left, b.right].map(v => Math.round(v * 2) / 2); };
    const bar = document.querySelector('.tabbar'), cs = bar && getComputedStyle(bar);
    const frame = document.querySelector('.kp-frame');
    let fx = 0;
    try { const fd = frame && frame.contentDocument; if (fd && document.body.classList.contains('tab-food')) fx = fd.documentElement.scrollWidth - fd.documentElement.clientWidth; } catch (e) { /* чужая */ }
    return { bar: r('.tabbar'), chat: r('.tabbar button[data-tab="coach"]'), hidden: !cs || cs.visibility === 'hidden' || +cs.opacity < .05 || cs.display === 'none',
      kb: document.body.classList.contains('kb'), sx: document.documentElement.scrollWidth - innerWidth, fx, sy: Math.round(scrollY) };
  });
}
const sameBox = (a, b) => !!a && !!b && a.every((v, i) => Math.abs(v - b[i]) <= 1);
async function check(page, base, name, opts = {}) {
  const b = await bars(page);
  const moved = !(b.kb && opts.kbOk) && (!sameBox(b.bar, base.bar) || !sameBox(b.chat, base.chat));
  const hid = b.hidden && !(b.kb && opts.kbOk);
  ok(!moved && !hid && b.sx <= 1 && b.fx <= 1, name, `меню ${JSON.stringify(b.bar)}/${JSON.stringify(b.chat)} (было ${JSON.stringify(base.bar)}/${JSON.stringify(base.chat)})${hid ? ' спрятано' : ''}${b.sx > 1 ? ' вбок ' + b.sx : ''}${b.fx > 1 ? ' вбок в «Калориях» ' + b.fx : ''}`);
  return b;
}
async function focusBlur(page, ctx, base, name, root, opts = {}) {  // ctx — page или frame «Калорий»
  const f = await ctx.evaluateHandle(([sel, rootSel]) => {
    const r = document.querySelectorAll(rootSel); const box = r[r.length - 1];
    return box ? [...box.querySelectorAll(sel)].find(e => { const b = e.getBoundingClientRect(); return b.width > 0 && b.height > 0; }) || null : null;
  }, [FIELD, root]);
  const el = f.asElement();
  if (!el) return false;
  await el.evaluate(e => e.focus());
  await sleep(250);
  await check(page, base, `${name}: поле в фокусе`, opts);
  await el.evaluate(e => e.blur());
  await sleep(400);
  await check(page, base, `${name}: ушёл из поля`, opts);
  return true;
}
const shot = async (page, name) => { if (SHOTS) await page.screenshot({ path: path.join(SHOTS, name + '.png') }); };

async function newPage(browser, ctxOpts, emu) {
  const ctx = await browser.newContext({ colorScheme: 'dark', ...ctxOpts });
  await ctx.addInitScript(spy, emu || null);
  const page = await ctx.newPage();
  page.errors = [];
  page.on('pageerror', e => page.errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') page.errors.push(m.text()); });
  page.on('dialog', d => d.dismiss());
  page.kb = [];
  await page.route('**/api/kb', r => { try { page.kb.push(JSON.parse(r.request().postData() || '{}')); } catch (e) { /* не JSON */ } return r.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' }); });
  await page.goto(BASE);
  await page.waitForSelector('.tabbar button[data-tab="food"]');
  await page.waitForFunction(() => window.__F && window.__F.S && window.__F.S.data);
  await sleep(900);
  return page;
}
const tab = async (page, id) => { await page.click(`.tabbar button[data-tab="${id}"]`); await sleep(800); };
async function kcalFrame(page) {
  await tab(page, 'food');
  await page.waitForSelector('#kcal-pane.ready', { timeout: 10000 });
  const fr = page.frames().find(f => f.url().includes('/kcal/'));
  await fr.waitForFunction(() => typeof openAdd === 'function' && document.querySelector('#addTab'));
  await sleep(400);
  return fr;
}
const closeAll = async page => {
  for (let i = 0; i < 6; i++) {
    const open = await page.evaluate(() => { document.activeElement && document.activeElement.blur && document.activeElement.blur(); return !!document.querySelector('.sheet') || !!(window.__F && window.__F.S.screen); });
    if (!open) return;
    await page.keyboard.press('Escape'); await sleep(450);
  }
};

// ---------- 1) все панели, iPhone 14 ----------
async function panels(browser) {
  const page = await newPage(browser, { ...devices['iPhone 14'] });
  const base = await bars(page);
  ok(base.bar && base.bar[1] > 600, 'меню Формы внизу экрана', JSON.stringify(base));
  for (const t of ['trends', 'food', 'coach', 'today']) {
    await tab(page, t);
    if (t === 'food') await page.waitForSelector('#kcal-pane.ready', { timeout: 10000 });
    await check(page, base, `вкладка ${t}`);
    if (t === 'coach') {
      await focusBlur(page, page, base, 'чат', 'body', { kbOk: true });
      await shot(page, 'chat');
    }
  }
  // «＋ Добавить» и всё, что из него открывается; у экранов — их листы (кнопки [data-a], открывшие .sheet)
  await tab(page, 'plus');
  await check(page, base, 'лист «＋ Добавить»');
  await focusBlur(page, page, base, 'лист «＋ Добавить»', '.sheet');
  await shot(page, 'plus');
  const rows = await page.$$eval('.sheet [data-a]', els => els.map(e => [e.dataset.a, e.dataset.v || '', e.textContent.trim().replace(/\s+/g, ' ').slice(0, 24)]));
  await closeAll(page);
  const seen = new Set();
  for (const [a, v, label] of rows) {
    await tab(page, 'today'); await closeAll(page);
    await tab(page, 'plus');
    const row = await page.$(`.sheet [data-a="${a}"]${v ? `[data-v="${v}"]` : ''}`);
    if (!row) { ok(false, `«＋» → ${label}`, 'строки нет'); continue; }
    await row.click(); await sleep(900);
    await check(page, base, `«＋» → ${label}`);
    const sheet = await page.$('.sheet');
    if (sheet) await focusBlur(page, page, base, `«＋» → ${label}`, '.sheet', { kbOk: true });
    else await focusBlur(page, page, base, `«＋» → ${label}`, '#view', { kbOk: true });
    const screen = await page.evaluate(() => window.__F.S.screen);
    if (screen && !sheet) {  // экран: его кнопки, которые открывают лист
      const acts = await page.$$eval('#view [data-a]', els => els.map((e, i) => [i, e.dataset.a, e.dataset.v || '']).filter(([, a]) => !/^(back|tab|open|range|tr-|chart|day|seg|ch-)/.test(a)));
      for (const [i, sa, sv] of acts) {
        const key = screen + ":" + sa;  // одно действие — один раз на экран (строки привычек, замеров — одинаковые листы)
        if (seen.has(key)) continue;
        seen.add(key);
        const btn = (await page.$$('#view [data-a]'))[i];
        if (!btn || !(await btn.isVisible())) continue;
        await btn.click({ timeout: 2000 }).catch(() => {});
        await sleep(700);
        if (!(await page.$('.sheet'))) { if ((await page.evaluate(() => window.__F.S.screen)) !== screen) { await closeAll(page); await page.evaluate(s => window.__F.openScreen(s), screen); await sleep(700); } continue; }
        await check(page, base, `${screen} → лист «${sa}»`);
        await focusBlur(page, page, base, `${screen} → лист «${sa}»`, '.sheet', { kbOk: true });
        await page.keyboard.press('Escape'); await sleep(450);
        await check(page, base, `${screen} → лист «${sa}» закрыт`);
        if ((await page.evaluate(() => window.__F.S.screen)) !== screen) { await closeAll(page); await page.evaluate(s => window.__F.openScreen(s), screen); await sleep(700); }
      }
    }
    await closeAll(page);
    await check(page, base, `«＋» → ${label}: закрыто`);
  }
  await tab(page, 'today');

  // «Питание» = «Калории» в рамке: все листы
  const fr = await kcalFrame(page);
  const kAdd = async () => (await fr.$('#addTab')).boundingBox();
  const add0 = await kAdd();
  const meal = () => fr.evaluate(() => mealNow());
  const SHEETS = [
    ['Добавить', () => fr.click('#addTab')],
    ['Добавить → поиск → Порция', async () => {
      await fr.click('#addTab'); await sleep(600);
      const q = await fr.$('#sheetBody input[type=search], #sheetBody input'); await q.fill('гречка'); await sleep(900);
      const b = await fr.$('#results [data-k]'); if (b) await b.click();
    }],
    ['Надиктовать', async () => { await fr.click('#addTab'); await sleep(600); await fr.click('#voiceCta'); }],
    ['Мой рецепт', async () => { await fr.click('#addTab'); await sleep(600); await fr.click('#recipeCta'); }],
    ['Мои рецепты', async () => fr.evaluate(m => openRecipes(m), await meal())],
    ['Рецепт', async () => fr.evaluate(m => showRecipe(JSON.parse(JSON.stringify(S.recipes.find(r => !r.del))), { meal: m }), await meal())],
    ['Свой продукт', async () => fr.evaluate(m => openCustom(m), await meal())],
    ['Только калории', async () => fr.evaluate(m => openQuick(m), await meal())],
    ['Кофемания', async () => fr.evaluate(m => openCM(m), await meal())],
    ['Продукт (в рецепт)', () => fr.evaluate(() => openIngPick(() => {}, null))],
    ['Вода', () => fr.evaluate(() => openWater())],
    ['Вес сегодня', () => fr.evaluate(() => openWeight())],
    ['Настройки', () => fr.click('#gear')],
    ['Еда по фото', async () => fr.evaluate(m => readySheet(m), await meal())],
    ['Еда голосом', async () => fr.evaluate(m => showSpoken({ text: 'каша 300 грамм и стакан воды', water: 250, comment: '',
      items: [{ name: 'Каша овсяная', g: 300, lo: 250, hi: 350, sure: 'mid', kcal: 270, p: 9, f: 5, c: 45, salt: .6 }] }, m), await meal())],
    ['Фото: найдено', async () => fr.evaluate(m => showFound('', [{ name: 'Гречка', g: 200, lo: 180, hi: 220, sure: 'high', kcal: 220, p: 8, f: 2, c: 44, salt: .4 }], 'Пример', m), await meal())],
  ];
  let uiSeen = 0;
  for (const [name, open] of SHEETS) {
    await page.evaluate(() => { window.__ui = []; });
    try { await open(); } catch (e) { ok(false, `«Калории» → ${name}`, 'не открылся: ' + e.message.split('\n')[0]); continue; }
    await sleep(700);
    const on = await fr.evaluate(() => document.querySelector('#sheet').classList.contains('on'));
    if (!ok(on, `«Калории» → ${name}: лист открыт`)) continue;
    await check(page, base, `«Калории» → ${name}`);
    const add1 = await kAdd();
    const had = await focusBlur(page, fr, base, `«Калории» → ${name}`, '#sheetBody');
    if (name === 'Надиктовать') await shot(page, 'kcal-voice');
    await fr.evaluate(() => closeSheet()); await sleep(650);
    await check(page, base, `«Калории» → ${name}: закрыт`);
    const add2 = await kAdd();
    ok(sameBox([add0.y, add0.x], [add2.y, add2.x]), `«Калории» → ${name}: круг «+» на месте`, JSON.stringify([add0, add1, add2]));
    const ui = await page.evaluate(() => window.__ui);
    ok(ui.includes('sheet-open') && ui.includes('sheet-close') && (!had || (ui.includes('focus') && ui.includes('blur'))), `«Калории» → ${name}: Форма слышит лист и поле`, ui.join(','));
    uiSeen += ui.length;
  }
  ok(uiSeen > 0, 'сообщения kcal-ui доходят до Формы');
  await tab(page, 'today');
  await check(page, base, 'снова «Главная»');
  ok(page.errors.length === 0, 'iPhone 14: нет ошибок в консоли', page.errors.slice(0, 5).join(' | '));
  await page.context().close();
}

// ---------- 2) эмуляция iOS 26: окно «садится» после клавиатуры в «Калориях» ----------
const EMU = { ...devices['iPhone 14'], viewport: { width: 430, height: 956 }, screen: { width: 430, height: 956 }, permissions: ['microphone'] };
async function emuPage(browser, opts = {}) {
  const page = await newPage(browser, opts.ctx || EMU, { standalone: opts.standalone !== false });
  await page.exposeFunction('__grow', async () => {  // «WebKit пересчитал окно»: раскладка снова 956, подмена снята
    await page.setViewportSize({ width: 430, height: 956 });
    await page.evaluate(() => { window.__ih = 0; }).catch(() => {});
  });
  await page.evaluate(() => { window.__full = innerHeight; });
  return page;
}
const sink = async page => {  // окно «село» на 62 px (как замер 04.11: 956 → 894)
  await page.evaluate(() => { window.__low = true; });
  await page.setViewportSize({ width: 430, height: 894 });
};
async function voiceSheet(page) {
  const fr = await kcalFrame(page);
  await fr.click('#addTab'); await sleep(600);
  await fr.click('#voiceCta'); await sleep(800);
  return fr;
}
const flips = page => page.evaluate(() => window.__flips.slice());
const heals = page => page.kb.filter(k => k.when === 'heal').map(k => k.heal);

async function emulation(browser) {
  // слой лечит: пока поле «Калорий» в фокусе — не мигаем, ушёл из поля — лечим, меню снова внизу
  for (const mode of ['layer', 'app']) {
    const page = await emuPage(browser);
    await page.evaluate(m => { window.__mode = m; }, mode);
    const base = await bars(page);
    const fr = await voiceSheet(page);
    await page.evaluate(() => { window.__flips = []; });
    await fr.focus('#recText'); await sleep(300);
    ok(await page.evaluate(() => document.activeElement && document.activeElement.tagName === 'IFRAME'), `${mode}: фокус в рамке «Калорий»`);
    await sink(page); await sleep(1500);
    const sunk = await bars(page);
    ok(sunk.bar[1] < base.bar[1] - 50, `${mode}: эмуляция — меню «подлетело» на 62 px`, `${base.bar[1]} → ${sunk.bar[1]}`);
    ok((await flips(page)).length === 0, `${mode}: поле в фокусе — Форма не мигает`, (await flips(page)).join(','));
    await shot(page, `emu-${mode}-sunk`);
    await fr.evaluate(() => document.activeElement.blur()); await sleep(1800);
    const f = await flips(page);
    ok(f.includes(mode), `${mode}: ушёл из поля — Форма лечит (${mode === 'layer' ? 'слой' : '#app'})`, f.join(',') || 'не мигала');
    await check(page, base, `${mode}: меню вернулось вниз`);
    await sleep(1200);
    const h = heals(page);
    ok(h.some(x => x.startsWith('питание: 894→956') && x.includes(mode === 'layer' ? '(слой)' : '(#app)')), `${mode}: замер heal «питание: 894→956»`, JSON.stringify(h));
    ok(page.kb.some(k => k.when === 'kcal-blur' && k.tab === 'food'), `${mode}: замер kcal-blur`, page.kb.map(k => k.when).join(','));
    ok(page.errors.length === 0, `${mode}: нет ошибок в консоли`, page.errors.slice(0, 3).join(' | '));
    await page.context().close();
  }

  // закрыл лист кнопкой (поле в фокусе) — тоже лечит
  {
    const page = await emuPage(browser);
    const base = await bars(page);
    const fr = await voiceSheet(page);
    await page.evaluate(() => { window.__flips = []; });
    await fr.focus('#recText'); await sleep(200);
    await sink(page); await sleep(900);
    await fr.click('#sheetClose'); await sleep(1800);
    ok((await flips(page)).length > 0, 'закрыл лист — Форма лечит', 'не мигала');
    await check(page, base, 'закрыл лист — меню внизу');
    await page.context().close();
  }

  // запись голоса: окно «село» во время записи, две попытки не помогли (стоп) — кончилась запись (mic-stop) — лечит снова
  {
    const page = await emuPage(browser);
    const base = await bars(page);
    const fr = await voiceSheet(page);
    await page.evaluate(() => { window.__mode = 'none'; window.__flips = []; window.__ui = []; });
    await fr.click('#mic'); await sleep(900);
    const rec = await fr.evaluate(() => document.querySelector('#mic').classList.contains('on'));
    await sink(page); await sleep(1200);  // resize → первая попытка (не помогла)
    await page.evaluate(() => window.__F.heal('проверка')); await sleep(1000);  // вторая — тоже нет: Форма ждёт смены высоты
    const tried = await flips(page);
    ok(tried.includes('layer') && tried.includes('app'), 'запись: окно «село» — Форма пробовала (слой, #app)', tried.join(','));
    ok(await page.evaluate(() => window.__F.heal('проверка')) === false, 'запись: после двух неудач — не мигает зря');
    await page.evaluate(() => { window.__mode = 'layer'; window.__flips = []; });
    if (rec) { await fr.click('#mic'); await sleep(1800); }
    const ui = await page.evaluate(() => window.__ui);
    if (ok(rec, 'запись: микрофон включился (подставной)', 'не включился — проверка mic-stop пропущена')) {
      ok(ui.includes('mic-stop'), 'запись кончилась — Форма слышит mic-stop', ui.join(','));
      ok((await flips(page)).includes('layer'), 'запись кончилась — Форма лечит (после двух неудач)', (await flips(page)).join(',') || 'не мигала');
      await check(page, base, 'запись кончилась — меню внизу');
    }
    await page.context().close();
  }

  // сдвиг visualViewport.offsetTop (iOS 26 не вернул в 0) — scrollTo, без мигания
  {
    const page = await emuPage(browser);
    const fr = await voiceSheet(page);
    await page.evaluate(() => { window.__flips = []; });
    await fr.focus('#recText'); await sleep(200);
    await page.evaluate(() => { window.__vt = 120; });
    await fr.evaluate(() => document.activeElement.blur()); await sleep(1800);
    ok(await page.evaluate(() => !window.__vt), 'сдвиг: Форма вызвала scrollTo');
    ok((await flips(page)).length === 0, 'сдвиг: высота в порядке — не мигает', (await flips(page)).join(','));
    ok(heals(page).some(x => x.startsWith('питание: сдвиг 120→0 (scrollTo)')), 'сдвиг: замер heal «сдвиг 120→0»', JSON.stringify(heals(page)));
    await page.context().close();
  }

  // Safari (не с экрана «Домой») и компьютер — не трогаем
  for (const [name, opts] of [['Safari', { standalone: false }],
    ['компьютер', { standalone: false, ctx: { viewport: { width: 430, height: 956 }, screen: { width: 430, height: 956 } } }]]) {
    const page = await emuPage(browser, opts);
    const fr = await voiceSheet(page);
    await page.evaluate(() => { window.__flips = []; });
    await fr.focus('#recText'); await sleep(200);
    await sink(page); await sleep(600);
    await fr.evaluate(() => document.activeElement.blur()); await sleep(1600);
    ok((await flips(page)).length === 0, `${name}: не лечит`, (await flips(page)).join(','));
    await page.context().close();
  }
}

(async () => {
  if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });
  const srv = await fixture();
  const browser = await chromium.launch({ args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'] });
  try {
    if (process.env.BOTTOM_ONLY !== 'emu') await panels(browser);
    if (process.env.BOTTOM_ONLY !== 'panels') await emulation(browser);
  } catch (e) {
    ok(false, 'проверка дошла до конца', e.stack);
  } finally {
    await browser.close();
    if (srv) srv.kill();
  }
  console.log(results.join('\n'));
  console.log(`\n${results.length - failed} из ${results.length} ✓${failed ? `, ${failed} ✗` : ''}`);
  process.exit(failed ? 1 : 0);
})();
