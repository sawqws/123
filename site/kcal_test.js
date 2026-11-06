// Проверка сайта «Калории» (docs/kcal) от А до Я в Playwright: node site/kcal_test.js [папка-для-скриншотов]
// Сервер фото подменяется (context.route), на настоящий не ходит. Дата зафиксирована: среда 21.10.2026, 12:00.
const { chromium, devices } = require('playwright');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');

const ROOT = path.join(__dirname, '..', 'docs');
const PORT = 8771;
const URL = `http://localhost:${PORT}/kcal/`;
const API = `http://localhost:${PORT}`; // на своём домене «Калории» ходят на свой же адрес (coach/SYNC.md §5)
const FORMA_API = 'https://coach.2-27-206-165.sslip.io'; // на github.io — сервер Формы
const SHOTS = process.argv[2] || '';
const NOW = new Date('2026-10-21T12:00:00');  // среда — день тренировки по умолчанию
const results = [];
let failed = 0;

function ok(cond, name, info = '') {
  results.push(`${cond ? '✓' : '✗'} ${name}${cond ? '' : '  → ' + info}`);
  if (!cond) failed++;
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function newPage(browser, opts = {}) {
  const ctx = await browser.newContext({ ...devices[opts.device || 'iPhone 14'], colorScheme: opts.scheme || 'dark', serviceWorkers: opts.sw ? 'allow' : 'block' });
  await ctx.clock.setFixedTime(opts.now || NOW);
  await ctx.route(API + '/api/kcal/shop?*', r => r.fulfill({ status: 200, contentType: 'application/json', body: '{"items":[]}' })); // магазины — пусто, на сервер не ходим
  ctx.syncs = []; // все POST /api/kcal/sync: { body, auth, t }
  await ctx.route(API + '/api/kcal/sync', r => { ctx.syncs.push({ body: JSON.parse(r.request().postData()), auth: r.request().headers().authorization, t: Date.now() });
    return opts.sync ? opts.sync(r) : r.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true,"forma":null}' }); });
  await ctx.route(API + '/api/kcal/forma', r => opts.forma ? opts.forma(r) : r.fulfill({ status: 200, contentType: 'application/json', body: 'null' }));
  ctx.states = 0; // GET /api/kcal/state — всё сохранённое на сервере
  await ctx.route(API + '/api/kcal/state', r => { ctx.states++; return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(opts.state || { days: {} }) }); });
  const page = await ctx.newPage();
  page.errors = [];
  page.on('pageerror', e => page.errors.push(e.message));
  page.on('console', m => m.type() === 'error' && !/Failed to load resource/.test(m.text()) && page.errors.push(m.text()));
  page.on('dialog', d => d.accept());
  if (opts.seed) await ctx.addInitScript(s => { if (!sessionStorage.getItem('seeded')) { localStorage.setItem('kcal.v1', s); sessionStorage.setItem('seeded', 1); } }, JSON.stringify(opts.seed));
  if (opts.init) await ctx.addInitScript(opts.init);
  await page.goto((opts.url || URL) + (opts.hash || ''));
  await sleep(700);
  return { ctx, page };
}
const state = page => page.evaluate(() => JSON.parse(localStorage.getItem('kcal.v1') || 'null'));
const text = (page, sel) => page.textContent(sel).then(t => t.replace(/\s+/g, ' ').trim());
const shot = async (page, name, full) => SHOTS && page.screenshot({ path: path.join(SHOTS, name + '.png'), fullPage: !!full });
const items = async page => (await state(page))?.days['2026-10-21']?.items || [];
const sum = (arr, k) => arr.reduce((a, i) => a + (i[k] || 0), 0);
const toastText = page => page.textContent('#toast');
const dayTypeOf = (S, d) => S.days[d].type || S.days[d].ftype;

const RECIPE_PARSE = { text: 'Сырники: пачка творога 200 г, 2 яйца, 3 ложки муки, жарил на масле, вышло 2 порции', name: 'Сырники', servings: 2, out: 0,
  method: 'жарка на масле', note: '2 яйца ≈ 110 г, 3 ложки муки ≈ 75 г', items: [ // ответ /api/kcal/recipe/parse (сервер подменён)
    { name: 'Творог 5%', g: 200, est: false, kcal: 242, p: 34, f: 10, c: 6, salt: 0.2 }, { name: 'Яйцо', g: 110, est: true, kcal: 172, p: 14, f: 12, c: 1, salt: 0.4 },
    { name: 'Мука пшеничная', g: 75, est: true, kcal: 256, p: 7.7, f: 0.8, c: 53, salt: 0 }, { name: 'Масло подсолнечное', g: 17, est: true, kcal: 153, p: 0, f: 17, c: 0, salt: 0 }] };
const rsum = page => page.$$eval('#rsum > div', els => { const c = els.map(e => e.textContent.trim()), out = []; for (let i = 6; i < c.length; i += 6) out.push(c.slice(i, i + 6)); return out; }); // итог рецепта по строкам
const digits = v => +String(v).replace(/\D/g, '');
const VOICE = { text: 'Съел тарелку гречки с котлетой, кусок хлеба и выпил стакан воды', comment: 'Гречка — обычная тарелка, около 250 г', water_ml: 250,
  fixes: ['«греча» — гречка', 'Хлеб — кусок, около 30 г'], items: [ // ответ /api/kcal/voice (сервер подменён)
  { name: 'Гречка отварная', g: 250, lo: 200, hi: 300, sure: 'mid', kcal: 275, p: 10, f: 3, c: 52, salt: 0.5 },
  { name: 'Котлета', g: 100, lo: 85, hi: 115, sure: 'high', kcal: 230, p: 15, f: 16, c: 7, salt: 1.1 },
  { name: 'Хлеб', g: 30, lo: 20, hi: 60, sure: 'low', kcal: 75, p: 2.4, f: 0.9, c: 15, salt: 0.4 }] };

async function addFood(page, q, grams, meal) {
  if (meal) await page.click(`[data-add="${meal}"] >> nth=0`, { position: { x: 30, y: 20 } }); else await page.click('#addTab');
  await sleep(550);
  await page.fill('#q', q); await sleep(120);
  await page.click('#results [data-k] >> nth=0'); await sleep(500);
  if (grams != null) await page.fill('#g', String(grams));
  await page.click('#ok'); await sleep(650);
}

(async () => {
  const server = spawn('python3', ['-m', 'http.server', String(PORT)], { cwd: ROOT, stdio: 'ignore' });
  await sleep(800);
  const browser = await chromium.launch();
  try {
    /* 1. Пусто, нормы по дню недели, переключатель дня */
    {
      const { ctx, page } = await newPage(browser);
      ok((await text(page, '#left')) === '3 400', 'среда — тренировка: 3 400 осталось', await text(page, '#left'));
      ok((await page.getAttribute('#seg [data-type=train]', 'aria-pressed')) === 'true', 'выбрана «Тренировка»');
      ok((await text(page, '.metrics')).includes('0 / 130 г'), 'белки 0 / 130 г');
      await shot(page, '01-empty');
      await page.click('#seg [data-type=rest]'); await sleep(1100);
      ok((await text(page, '#left')) === '2 800', 'переключение на «Отдых» → 2 800', await text(page, '#left'));
      await page.reload(); await sleep(600);
      ok((await text(page, '#left')) === '2 800', '«Отдых» сохраняется после перезагрузки');
      ok(page.errors.length === 0, 'нет ошибок в консоли (старт)', page.errors.join(' | '));
      await ctx.close();
    }
    /* 2. Добавление, порция, поиск, недавние, правка, удаление, свайп */
    {
      const { ctx, page } = await newPage(browser);
      await addFood(page, 'овсян хлоп', 100, 'breakfast');
      let it = await items(page);
      ok(it.length === 1 && Math.round(it[0].kcal) === 352 && it[0].meal === 'breakfast', 'овсянка 100 г → 352 ккал в «Завтрак»', JSON.stringify(it));
      ok((await text(page, '#left')) === '3 048', 'осталось 3 048', await text(page, '#left'));
      // порция: единица, +/−, чипы, проверка нуля, дробные граммы
      await page.click('#addTab'); await sleep(500); await page.fill('#q', 'банан'); await sleep(100); await page.click('#results [data-k] >> nth=0'); await sleep(500);
      ok((await page.inputValue('#g')) === '120', 'банан — сразу порция 1 шт = 120 г', await page.inputValue('#g'));
      await page.click('#plus'); ok((await page.inputValue('#g')) === '130', '«+» → 130');
      await page.click('#minus'); await page.click('#minus'); ok((await page.inputValue('#g')) === '110', '«−» дважды → 110');
      await page.click('#units [data-g="50"]'); ok((await page.inputValue('#g')) === '50', 'чип 50 г');
      await page.fill('#g', '0'); await page.click('#ok'); await sleep(200);
      ok((await toastText(page)).includes('Укажи граммы') && await page.isVisible('#ok'), 'ноль граммов — подсказка, лист не закрылся');
      await page.fill('#g', '7,5'); await sleep(50);
      ok((await text(page, '#res')).startsWith('7'), '7,5 г банана → 7 ккал', await text(page, '#res'));
      await page.click('#minus'); ok((await page.inputValue('#g')) === '6,5', 'меньше 10 г шаг 1 → 6,5', await page.inputValue('#g'));
      await page.fill('#g', '120'); await page.click('#ok'); await sleep(600);
      // поиск
      await page.click('#addTab'); await sleep(500);
      await page.fill('#q', 'рис сух'); await sleep(100);
      ok((await text(page, '#results .cell >> nth=0')).includes('Рис белый (сухой)'), 'поиск по словам «рис сух»');
      await page.fill('#q', 'свекла'); await sleep(100);
      ok((await text(page, '#results .cell >> nth=0')).includes('Свёкла'), 'ё = е в поиске');
      await page.fill('#q', 'zzzz'); await sleep(900);
      ok((await text(page, '#results')).includes('Такого не нашёл'), 'нет совпадений — подсказка');
      ok(!(await page.isVisible('#photoCta')), 'при поиске кнопка фото прячется');
      await page.fill('#q', ''); await sleep(100);
      ok((await text(page, '#results')).includes('Недавние'), 'появились «Недавние»');
      ok((await text(page, '#results')).includes('Популярные блюда'), 'есть «Популярные блюда»');
      await page.click('#results [data-k="r0"]'); await sleep(500);
      ok((await page.inputValue('#g')) === '120', 'недавнее открывается с прошлыми граммами', await page.inputValue('#g'));
      await page.click('#backBtn'); await sleep(500);
      ok(await page.isVisible('#q'), '«Назад» из порции → снова поиск');
      await page.click('#sheetClose'); await sleep(600);
      // правка
      it = await items(page);
      await page.click(`[data-item="${it[1].id}"]`); await sleep(500);
      await page.fill('#g', '240'); await page.click('#ok'); await sleep(600);
      it = await items(page);
      ok(it[1].g === 240 && Math.round(it[1].kcal) === 228, 'правка граммов банана → 240 г, 228 ккал', JSON.stringify(it[1]));
      // удаление и «Вернуть»
      await page.click(`[data-item="${it[1].id}"]`); await sleep(500);
      await page.click('#del'); await sleep(400);
      ok((await items(page)).length === 1, 'удаление из листа');
      await page.click('#toast button'); await sleep(400);
      ok((await items(page)).length === 2 && (await items(page))[1].name === 'Банан', '«Вернуть» вернул на место');
      // свайп влево → «Удалить»
      const box = await page.locator('.swipe .cell').first().boundingBox();
      const cdp = await ctx.newCDPSession(page);
      const tp = (type, x) => cdp.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? [] : [{ x, y: box.y + box.height / 2 }] });
      await tp('touchStart', box.x + box.width - 20);
      for (let x = box.x + box.width - 40; x > box.x + box.width - 160; x -= 20) await tp('touchMove', x);
      await tp('touchEnd'); await sleep(500);
      await shot(page, '02-swipe');
      ok(await page.locator('.swipe.sw .del').first().isVisible(), 'свайп открыл «Удалить»');
      await page.locator('.swipe .del').first().click(); await sleep(500);
      ok((await items(page)).length === 1, 'свайп-удаление');
      ok(page.errors.length === 0, 'нет ошибок в консоли (добавление)', page.errors.join(' | '));
      await ctx.close();
    }
    /* 3. Свой продукт, «Только калории», перебор нормы, соль, XSS */
    {
      const { ctx, page } = await newPage(browser);
      await page.click('#addTab'); await sleep(500); await page.click('#own'); await sleep(500);
      await page.click('#csave'); await sleep(150);
      ok((await toastText(page)).includes('Напиши название'), 'свой продукт без названия — подсказка');
      await page.fill('#cn', '<img src=x onerror="window.__x=1">Йогурт'); await page.click('#csave'); await sleep(150);
      ok((await toastText(page)).includes('Укажи калории'), 'без ккал — подсказка');
      await page.fill('#ck', '200'); await page.fill('#cp', '10'); await page.fill('#cs', '1'); await page.fill('#cu', '50');
      await page.click('#csave'); await sleep(550);
      ok((await page.inputValue('#g')) === '50', 'своя порция 50 г подставилась');
      await page.click('#ok'); await sleep(600);
      let it = await items(page);
      ok(Math.round(it[0].kcal) === 100 && Math.abs(it[0].salt - 0.5) < 1e-9 && Math.round(it[0].p) === 5, 'свой продукт: 100 ккал, Б 5, соль 0,5', JSON.stringify(it[0]));
      ok(await page.evaluate(() => window.__x === undefined), 'название не исполняется как код (XSS)');
      ok((await text(page, '#app')).includes('<img src=x'), 'название показано как текст');
      await page.click('#addTab'); await sleep(500);
      ok((await text(page, '#results')).includes('Мои продукты'), '«Мои продукты» в списке');
      await page.click('#quick'); await sleep(500);
      await page.click('#qsave'); await sleep(150);
      ok((await toastText(page)).includes('Укажи калории'), '«Только калории» без числа — подсказка');
      await page.fill('#qn', 'Столовая'); await page.fill('#qk', '700'); await page.fill('#qs', '2'); await page.click('#qsave'); await sleep(600);
      it = await items(page);
      ok(it.length === 2 && it[1].kcal === 700 && it[1].salt === 2, '«Только калории» добавлено', JSON.stringify(it[1]));
      await page.click(`[data-item="${it[1].id}"]`); await sleep(500);
      ok((await page.inputValue('#qk')) === '700', 'правка «Только калории» открывает свою форму');
      await page.fill('#qk', '650'); await page.click('#qsave'); await sleep(600);
      ok((await items(page))[1].kcal === 650, 'правка сохранена');
      const saltBar = await page.evaluate(() => getComputedStyle(document.querySelectorAll('.metric')[3]).getPropertyValue('--dot'));
      ok(/636366|--s\b/.test(saltBar), 'соль 2,5 из 5 — обычный (серый) цвет', saltBar); // монохром (Глеб 01.11)
      await page.click('#addTab'); await sleep(500); await page.click('#quick'); await sleep(500);
      await page.fill('#qk', '3000'); await page.fill('#qs', '3'); await page.click('#qsave'); await sleep(250);
      ok((await toastText(page)).includes('Норма на сегодня закрыта'), 'перебор нормы — «Норма закрыта»', await toastText(page));
      await sleep(700);
      ok(await page.$eval('#left', el => el.classList.contains('over')) && (await text(page, '.ring-in')).includes('сверх нормы'), 'перебор: «ккал сверх нормы»');
      const saltBar2 = await page.evaluate(() => getComputedStyle(document.querySelectorAll('.metric')[3]).getPropertyValue('--dot'));
      ok(/ff453a|--bad/.test(saltBar2), 'соль 5,5 из 5 — красная', saltBar2);
      await shot(page, '03-over', true);
      ok(page.errors.length === 0, 'нет ошибок в консоли (свой продукт)', page.errors.join(' | '));
      await ctx.close();
    }
    /* 4. Неделя, дни, серия, «Сегодня» */
    {
      const it = (k, id) => ({ id, meal: 'lunch', name: 'x', g: 0, kcal: k, p: 1, f: 1, c: 1, salt: 0 });
      const seed = { goals: { train: { kcal: 3400, p: 130, f: 105, c: 485 }, rest: { kcal: 2800, p: 125, f: 95, c: 360 } }, salt: 5, trainDays: [0, 1, 2, 5],
        days: { '2026-10-18': { items: [it(1000, 'a')] }, '2026-10-19': { items: [it(2000, 'b')] }, '2026-10-20': { items: [it(3400, 'c')] }, '2026-10-21': { items: [it(500, 'd')] } }, custom: [], recent: [], weights: {} };
      const { ctx, page } = await newPage(browser, { seed });
      ok((await text(page, '.subtitle')).includes('4 дня подряд'), 'серия «4 дня подряд»', await text(page, '.subtitle'));
      ok(await page.$eval('.wd[data-d="2026-10-22"]', b => b.disabled), 'будущий четверг недоступен');
      await page.click('.wd[data-d="2026-10-20"]'); await sleep(600);
      ok((await text(page, '.large')) === 'Вчера' && await page.isVisible('#todayBtn'), 'вторник: «Вчера» и кнопка «Сегодня»');
      ok((await text(page, '#left')) === '0', 'вторник: 3 400 из 3 400 → 0 осталось', await text(page, '#left'));
      await page.click('#todayBtn'); await sleep(600);
      ok((await text(page, '.large')) === 'Сегодня' && !(await page.isVisible('#todayBtn')), 'кнопка «Сегодня» возвращает');
      // свайп недели
      const wk = await page.locator('#week').boundingBox(); const cdp = await ctx.newCDPSession(page);
      const sw = async (from, to) => { await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: from, y: wk.y + 30 }] }); await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: to, y: wk.y + 30 }] }); await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }); await sleep(600); };
      await sw(wk.x + 60, wk.x + 300);
      ok((await text(page, '.large')) === '14 октября', 'свайп вправо — прошлая неделя', await text(page, '.large'));
      await sw(wk.x + 300, wk.x + 60);
      ok((await text(page, '.large')) === 'Сегодня', 'свайп влево — обратно, не в будущее');
      await sw(wk.x + 300, wk.x + 60);
      ok((await text(page, '.large')) === 'Сегодня', 'дальше сегодняшнего не уходит');
      ok(page.errors.length === 0, 'нет ошибок в консоли (неделя)', page.errors.join(' | '));
      await ctx.close();
    }
    /* 5. История и вес */
    {
      const days = {}, weights = {};
      for (let i = 1; i <= 6; i++) { const d = `2026-10-${String(21 - i).padStart(2, '0')}`; days[d] = { items: [{ id: 'h' + i, meal: 'lunch', name: 'x', g: 0, kcal: i % 2 ? 3300 : 2000, p: 0, f: 0, c: 0, salt: 4 }] }; }
      weights['2026-09-21'] = 57; weights['2026-10-21'] = 57.6;
      const seed = { goals: { train: { kcal: 3400, p: 130, f: 105, c: 485 }, rest: { kcal: 2800, p: 125, f: 95, c: 360 } }, salt: 5, trainDays: [0, 1, 2, 5], days, custom: [], recent: [], weights };
      const { ctx, page } = await newPage(browser, { seed });
      await page.click('[data-tab=history]'); await sleep(700);
      const kp = await text(page, '.kpis');
      ok(kp.includes('2 650'), 'среднее за неделю 2 650', kp);
      // дни 15..20: 20 вт(т) 3300≥3060 ✓, 19 пн(т) 2000 ✗, 18 вс(о) 3300 ✓, 17 сб(т) 2000 ✗, 16 пт(о) 3300 ✓, 15 чт(о) 2000 ✗ → 3 из 6
      ok(kp.includes('3 из 6'), '«в норме» 3 из 6', kp);
      ok(kp.includes('4 г'), 'соль в среднем 4 г', kp);
      const wsec = await page.locator('.sec', { hasText: 'Темп' }).textContent();
      ok(wsec.includes('+0,6'), 'темп веса +0,6 кг/мес', wsec);
      await page.click('#seg [data-r="30"]'); await sleep(600);
      ok((await page.locator('.chart rect').count()) === 30, 'месяц — 30 столбиков');
      await page.click('#addW'); await sleep(500);
      await page.fill('#w', '20'); await page.click('#wsave'); await sleep(150);
      ok((await toastText(page)).includes('Проверь вес'), 'вес 20 — подсказка');
      await page.fill('#w', '58,2'); await page.click('#wsave'); await sleep(600);
      ok((await state(page)).weights['2026-10-21'] === 58.2, 'вес 58,2 сохранён на сегодня');
      await shot(page, '05-history', true);
      ok(page.errors.length === 0, 'нет ошибок в консоли (история)', page.errors.join(' | '));
      await ctx.close();
    }
    /* 6. Настройки, копия, удаление */
    {
      const { ctx, page } = await newPage(browser);
      await addFood(page, 'яйцо', null, 'breakfast');
      await page.click('#gear'); await sleep(600);
      await shot(page, '06-settings');
      await page.fill('#g-train-kcal', '3500'); await page.press('#g-train-kcal', 'Tab');
      await page.fill('#salt', '4'); await page.press('#salt', 'Tab');
      await page.click('#days [data-d="2"]'); // среда — выключить
      const [dl] = await Promise.all([page.waitForEvent('download'), page.click('#exp')]);
      const dump = JSON.parse(fs.readFileSync(await dl.path(), 'utf8'));
      ok(dump.goals.train.kcal === 3500 && dump.salt === 4 && !dump.trainDays.includes(2), 'копия содержит новые настройки', JSON.stringify(dump.goals.train));
      await page.click('#sheetClose'); await sleep(600);
      ok((await page.getAttribute('#seg [data-type=rest]', 'aria-pressed')) === 'true', 'среда снята с тренировок → «Отдых»');
      ok((await text(page, '.metrics')).includes('/ 4 г'), 'лимит соли 4 г виден');
      await page.click('#gear'); await sleep(500);
      const bad = path.join(os.tmpdir(), 'kcal-bad.json'); fs.writeFileSync(bad, '{"x":1}');
      const [fc] = await Promise.all([page.waitForEvent('filechooser'), page.click('#imp')]); await fc.setFiles(bad); await sleep(300);
      ok((await toastText(page)).includes('Это не копия'), 'чужой файл не загружается');
      const good = path.join(os.tmpdir(), 'kcal-good.json'); dump.goals.rest.kcal = 2900; fs.writeFileSync(good, JSON.stringify(dump));
      const [fc2] = await Promise.all([page.waitForEvent('filechooser'), page.click('#imp')]); await fc2.setFiles(good); await sleep(600);
      ok((await state(page)).goals.rest.kcal === 2900 && (await text(page, '#left')).replace(/\s/g, '') === String(2900 - 86).replace(/\B(?=(\d{3})+$)/g, ''), 'копия загружена (отдых 2 900, яйцо на месте)', await text(page, '#left'));
      await page.click('#gear'); await sleep(500); await page.click('#wipe'); await sleep(600);
      const st = await state(page);
      ok(Object.keys(st.days).length === 0 && st.goals.rest.kcal === 2900, '«Удалить все записи» — еда удалена, нормы остались');
      ok(page.errors.length === 0, 'нет ошибок в консоли (настройки)', page.errors.join(' | '));
      await ctx.close();
    }
    /* 7. Еда по фото */
    {
      const { ctx, page } = await newPage(browser);
      let mode = 'ok', calls = 0;
      await ctx.route(API + '/api/kcal/pair', r => {
        const code = JSON.parse(r.request().postData()).code;
        return code === '111111' ? r.fulfill({ status: 200, contentType: 'application/json', body: '{"token":"tok"}' }) : r.fulfill({ status: 403, contentType: 'application/json', body: '{"error":"Неверный или старый код"}' });
      });
      await ctx.route(API + '/api/kcal/photo', async r => {
        calls++;
        if (mode === 'net') return r.abort();
        await sleep(mode === 'slow' ? 2500 : 800);
        if (r.request().headers().authorization !== 'Bearer tok') return r.fulfill({ status: 401, contentType: 'application/json', body: '{"error":"нужен вход"}' });
        if (mode === 'notfood') return r.fulfill({ status: 422, contentType: 'application/json', body: '{"error":"Не похоже на еду"}' });
        r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [{ name: 'Гречка варёная', g: 200, kcal: 220, p: 8.4, f: 2.2, c: 42, salt: 0.8 }, { name: 'Котлета', g: 100, kcal: 230, p: 15, f: 15, c: 9, salt: 1 }], comment: 'Гречка с котлетой.' }) });
      });
      const img = path.join(os.tmpdir(), 'kcal-plate.png');
      fs.writeFileSync(img, Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGP4z8DAwMDAxMDAwMDAAAANHQEDasKb6QAAAABJRU5ErkJggg==', 'base64'));
      const go = async () => { await sleep(400); await page.click('#go'); };
      const pick = async () => { const [fc] = await Promise.all([page.waitForEvent('filechooser'), page.click('#pick')]); await fc.setFiles(img); await go(); };
      await page.click('#camBtn'); await sleep(500);
      ok(await page.isVisible('#code'), 'без подключения — экран кода');
      await page.fill('#code', '123456'); await sleep(600);
      ok((await toastText(page)).includes('Неверный или старый код'), 'неверный код — понятная ошибка', await toastText(page));
      await page.fill('#code', '111111'); await sleep(700);
      ok(await page.isVisible('#pick'), 'после кода — кнопка «Снять или выбрать фото»');
      await pick(); await sleep(400);
      ok((await text(page, '.thinking')).includes('Смотрю'), 'идёт распознавание');
      await shot(page, '07-scan');
      await sleep(900);
      ok(await page.isVisible('#found'), 'найденное показано');
      await shot(page, '07-found');
      await page.click('#found [data-i="1"] [data-d="10"]'); await sleep(100);
      ok((await page.inputValue('#pg1')) === '110', '«+» у котлеты → 110 г');
      await page.click('#found [data-i="0"] [data-tog]'); await sleep(100);
      ok((await text(page, '#sum')).includes('253'), 'гречка снята — всего 253 ккал', await text(page, '#sum'));
      await page.click('#mseg [data-m="dinner"]'); await sleep(200);
      ok((await text(page, '#addAll')).includes('Ужин'), 'выбор приёма — «Ужин»');
      await page.click('#addAll'); await sleep(700);
      let it = await items(page);
      ok(it.length === 1 && it[0].name === 'Котлета' && it[0].g === 110 && Math.round(it[0].kcal) === 253 && it[0].meal === 'dinner', 'добавлена котлета 110 г в ужин', JSON.stringify(it));
      ok(Math.abs(it[0].per100.kcal - 230) < 1e-9, 'у позиции с фото есть состав на 100 г (можно править граммы)');
      // два фото + подсказка уходят на сервер; диапазон и «проверь вес»; вес руками
      let body = null; await page.route(API + '/api/kcal/photo', async r => { body = JSON.parse(r.request().postData());
        r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [{ name: 'Пельмени', g: 250, lo: 220, hi: 280, sure: 'low', kcal: 625, p: 30, f: 33, c: 55, salt: 3 }], comment: '' }) }); });
      const [fm] = await Promise.all([page.waitForEvent('filechooser'), page.click('#camBtn')]); await fm.setFiles([img, img]); await sleep(500);
      ok((await page.locator('.shots .sh img').count()) === 2 && await page.isVisible('#more'), 'выбрано 2 фото, можно добавить третье');
      const [fm2] = await Promise.all([page.waitForEvent('filechooser'), page.click('#more')]); await fm2.setFiles(img); await sleep(500);
      ok((await page.locator('.shots .sh img').count()) === 3 && !(await page.isVisible('#more')), '3 фото — больше нельзя');
      await page.click('.shots [data-rm="2"]'); await sleep(400);
      ok((await page.locator('.shots .sh img').count()) === 2, 'лишнее фото убирается');
      await page.fill('#hint', '20 пельменей'); await page.click('#go'); await sleep(900);
      ok(body && body.images.length === 1 && body.text === '20 пельменей', 'на сервер ушли 2 фото и подсказка', JSON.stringify(body && { n: body.images.length, t: body.text }));
      ok(body && body.portion === 1.5, 'с фото уходит размер порций (1,5 — большие)', JSON.stringify(body && body.portion));
      ok((await text(page, '#found')).includes('Проверь вес · по фото 220–280 г'), 'диапазон и «проверь вес» при низкой уверенности', await text(page, '#found'));
      await page.fill('#pg0', '300'); await sleep(200);
      ok((await text(page, '#sum')).includes('750 ккал'), 'вес руками 300 г → 750 ккал', await text(page, '#sum'));
      await page.click('#sheetClose'); await sleep(600); await page.unroute(API + '/api/kcal/photo');
      // не еда
      mode = 'notfood'; await page.click('#camBtn'); await sleep(300); // токен есть — сразу выбор файла
      await sleep(100);
      const [fc] = await Promise.all([page.waitForEvent('filechooser'), page.click('#camBtn')]).catch(() => [null]);
      if (fc) { await fc.setFiles(img); await go(); } await sleep(1300);
      ok((await text(page, '.thinking')).includes('Не похоже на еду') && await page.isVisible('#again'), '422 — «Не похоже на еду» и «Снять ещё раз»', await text(page, '#sheetBody'));
      // нет связи
      mode = 'net';
      const [fc3] = await Promise.all([page.waitForEvent('filechooser'), page.click('#again')]); await fc3.setFiles(img); await go(); await sleep(500);
      ok((await text(page, '.thinking')).includes('Нет связи'), 'нет сети — «Нет связи с сервером»', await text(page, '#sheetBody'));
      // отмена во время распознавания
      mode = 'slow';
      const [fc4] = await Promise.all([page.waitForEvent('filechooser'), page.click('#again')]); await fc4.setFiles(img); await go(); await sleep(400);
      await page.click('#sheetClose'); await sleep(2600);
      ok(!(await page.$eval('#sheet', s => s.classList.contains('on'))), 'отмена — после ответа сервера ничего не всплывает');
      // токен устарел → снова код
      mode = 'ok'; await page.evaluate(() => localStorage.setItem('kcal.token', 'old'));
      const [fc5] = await Promise.all([page.waitForEvent('filechooser'), page.click('#camBtn')]); await fc5.setFiles(img); await go(); await sleep(1300);
      ok(await page.isVisible('#code'), '401 — снова экран кода');
      await page.fill('#code', '111111'); await sleep(1500);
      ok(await page.isVisible('#found'), 'после нового кода распознаёт то же фото');
      ok(calls >= 5, 'запросов к серверу было ' + calls);
      ok(page.errors.length === 0, 'нет ошибок в консоли (фото)', page.errors.join(' | '));
      await ctx.close();
    }
    /* 8. Листы, меню, клавиатура */
    {
      const { ctx, page } = await newPage(browser);
      await page.click('#addTab'); await sleep(500); await page.keyboard.press('Escape'); await sleep(600);
      ok(!(await page.$eval('#sheet', s => s.classList.contains('on'))), 'Escape закрывает лист');
      await page.click('#addTab'); await sleep(450); await page.click('#sheetClose'); await sleep(80); await page.click('#addTab'); await sleep(800);
      ok(await page.$eval('#sheet', s => !s.hidden && s.classList.contains('on')), 'быстрое повторное открытие листа не теряет его');
      await page.click('#sheetClose'); await sleep(600);
      for (const q of ['банан', 'хлеб бел', 'молоко 2', 'сыр тв', 'пельм', 'борщ']) await addFood(page, q, null);
      const tw0 = await page.$eval('#tabs', e => e.offsetWidth);
      await page.mouse.wheel(0, 700); await sleep(700);
      ok(await page.evaluate(() => document.body.classList.contains('scrolled')) && (await page.$eval('#tabs', e => e.offsetWidth)) === tw0, 'прокрутка вниз — сверху полоса, меню не меняет размер (без рывков)');
      await shot(page, '08-scrolled');
      const y0 = await page.evaluate(() => scrollY);
      await page.click('[data-tab=history]'); await sleep(700); await page.click('[data-tab=today]'); await sleep(700);
      ok(Math.abs((await page.evaluate(() => scrollY)) - y0) < 4 && !(await page.$('.ghost')), 'вкладка «Сегодня» открылась там, где оставили; старая страница растаяла', `${y0} → ${await page.evaluate(() => scrollY)}`);
      await page.click('[data-tab=history]'); await sleep(700);
      ok((await text(page, '.large')) === 'История', 'переход в «Историю»');
      await page.click('[data-tab=today]'); await sleep(600);
      ok((await text(page, '.large')) === 'Сегодня', 'обратно на «Сегодня»');
      const lens = await page.evaluate(() => { const l = document.querySelector('#lens').getBoundingClientRect(), t = document.querySelector('.tab[aria-current]').getBoundingClientRect(); return Math.abs(l.left - t.left) < 2 && Math.abs(l.width - t.width) < 2; });
      ok(lens, 'линза меню стоит под выбранной вкладкой');
      ok(page.errors.length === 0, 'нет ошибок в консоли (листы)', page.errors.join(' | '));
      await ctx.close();
    }
    /* 9. Вёрстка на разных экранах, шрифты полей, старые данные */
    for (const [dev, w] of [['iPhone SE', 0], ['iPhone 15 Pro Max', 0], ['iPhone 14', 320]]) {
      const ctx = await browser.newContext({ ...devices[dev], ...(w ? { viewport: { width: w, height: 640 } } : {}), colorScheme: 'light', serviceWorkers: 'block' });
      await ctx.clock.setFixedTime(NOW);
      const page = await ctx.newPage(); page.errors = []; page.on('pageerror', e => page.errors.push(e.message));
      await page.addInitScript(() => localStorage.setItem('kcal.v1', JSON.stringify({ goals: { train: { kcal: 3400, p: 130, f: 105, c: 485 }, rest: { kcal: 2800, p: 125, f: 95, c: 360 } }, trainDays: [0, 1, 2, 5],
        days: { '2026-10-21': { items: [{ id: 'old', meal: 'lunch', name: 'Очень длинное название блюда из старой версии без соли и состава', g: 250, kcal: 1234.5, p: 50, f: 40, c: 150 }] } }, custom: [], recent: [], weights: {} })));
      await page.goto(URL); await sleep(700);
      const label = `${dev}${w ? ' ' + w + 'px' : ''}`;
      const over = async where => ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${label}: нет прокрутки вбок (${where})`, await page.evaluate(() => document.documentElement.scrollWidth + '>' + innerWidth));
      await over('сегодня');
      const body = await page.textContent('#app'); // без текста скрипта
      ok(!/NaN|undefined/.test(body), `${label}: старые данные без NaN/undefined`);
      const metr = await page.$$eval('.metric .v', els => els.every(e => e.scrollWidth <= e.parentElement.clientWidth + 2 && e.getBoundingClientRect().right <= innerWidth));
      ok(metr, `${label}: цифры БЖУ и соли влезают`);
      await shot(page, `09-${label.replace(/\s/g, '_')}`);
      await page.click('#addTab'); await sleep(600); await over('добавить');
      const bar = await page.evaluate(() => { const t = document.querySelector('.tabs').getBoundingClientRect(), f = document.querySelector('.fab').getBoundingClientRect(); return t.left >= 0 && f.right <= innerWidth && t.right <= f.left; });
      ok(bar, `${label}: меню из трёх вкладок и «+» влезает`);
      const fonts = await page.$$eval('input', els => els.filter(e => e.type !== 'file').every(e => parseFloat(getComputedStyle(e).fontSize) >= 16));
      ok(fonts, `${label}: поля ввода ≥ 16 px (iPhone не зумит)`);
      await page.click('#results [data-k] >> nth=0'); await sleep(500); await over('порция');
      await page.click('#sheetClose'); await sleep(500);
      await page.click('[data-tab=history]'); await sleep(600); await over('история');
      await page.click('[data-tab=today]'); await sleep(500); await page.click('#gear'); await sleep(600); await over('настройки');
      ok(page.errors.length === 0, `${label}: нет ошибок`, page.errors.join(' | '));
      await ctx.close();
    }
    /* 10. Полночь и офлайн */
    {
      const { ctx, page } = await newPage(browser, { now: new Date('2026-10-21T23:59:00'), sw: true });
      await addFood(page, 'банан', null);
      const vis = h => page.evaluate(h => { Object.defineProperty(document, 'hidden', { value: h, configurable: true }); document.dispatchEvent(new Event('visibilitychange')); }, h);
      await vis(true); // свернул вечером
      await ctx.clock.setFixedTime(new Date('2026-10-22T00:01:00'));
      await vis(false); // открыл после полуночи
      await sleep(600);
      ok((await text(page, '#left')) === '2 800' && (await page.getAttribute('.wd[aria-current]', 'data-d')) === '2026-10-22', 'после полуночи открылся новый день (четверг, отдых)', await text(page, '#left'));
      await page.waitForFunction(() => navigator.serviceWorker.controller, null, { timeout: 5000 }).catch(() => {});
      await ctx.setOffline(true); await page.reload(); await sleep(800);
      ok(await page.isVisible('#left'), 'без интернета открывается');
      await ctx.close();
    }
    /* 11. Находки независимой проверки (не должны вернуться) */
    {
      const it = (k, id, extra = {}) => ({ id, meal: 'lunch', name: 'Еда ' + id, g: 0, kcal: k, p: 1, f: 1, c: 1, salt: 0, ...extra });
      const seed = { goals: { train: { kcal: 3400, p: 130, f: 105, c: 485 }, rest: { kcal: 2800, p: 125, f: 95, c: 360 } }, salt: 4.5, trainDays: [0, 1, 2, 5],
        days: { '2026-10-20': { items: [it(1000, 'y1'), it(500, 'y2')] }, '2026-10-21': { items: [it(3000, 't1'), it(100, 't2')] } }, custom: [], recent: [], weights: {} };
      const { ctx, page } = await newPage(browser, { seed });
      ok((await text(page, '.metrics')).includes('/ 4,5 г'), 'лимит соли с запятой «4,5»');
      // «Вернуть» после перехода на другой день
      await page.click('[data-item="t2"]'); await sleep(500); await page.click('#qdel'); await sleep(300);
      await page.click('.wd[data-d="2026-10-20"]'); await sleep(400);
      await page.dblclick('#toast button'); await sleep(400);
      const st = await state(page);
      ok(st.days['2026-10-20'].items.map(i => i.id).join() === 'y1,y2', '«Вернуть» на другом дне не трогает этот день', JSON.stringify(st.days['2026-10-20'].items.map(i => i.id)));
      ok(st.days['2026-10-21'].items.map(i => i.id).join() === 't1,t2', '«Вернуть» вернул в свой день и один раз (двойное нажатие)', JSON.stringify(st.days['2026-10-21'].items.map(i => i.id)));
      // переключение «Тренировка/Отдых» не пишет «Норма закрыта»; прошлый день — «Норма дня закрыта»
      await page.click('#todayBtn'); await sleep(600);
      await page.evaluate(() => { document.querySelector('#toast').textContent = ''; });
      await page.click('#seg [data-type=rest]'); await sleep(700);
      ok(!(await toastText(page)).includes('Норма'), 'смена на «Отдых» не говорит «Норма закрыта»', await toastText(page));
      await page.click('#seg [data-type=train]'); await sleep(700);
      await page.click('.wd[data-d="2026-10-20"]'); await sleep(500);
      await page.click('#addTab'); await sleep(500); await page.click('#quick'); await sleep(500);
      await page.fill('#qk', '2500'); await page.click('#qsave'); await sleep(300);
      ok((await toastText(page)).includes('Норма дня закрыта'), 'на прошлом дне — «Норма дня закрыта»', await toastText(page));
      // смена расписания не меняет норму прошлых дней (вторник был тренировкой)
      await page.click('#todayBtn'); await sleep(500); await page.click('#gear'); await sleep(500);
      await page.click('#days [data-d="1"]'); await sleep(100); await page.click('#sheetClose'); await sleep(600);
      ok((await state(page)).days['2026-10-20'].type === 'train', 'снятый вторник остался тренировкой в прошлом');
      // «Назад» из порции сохраняет поиск
      await page.click('#addTab'); await sleep(500); await page.fill('#q', 'рис'); await sleep(100);
      await page.click('#results [data-k] >> nth=0'); await sleep(500); await page.click('#backBtn'); await sleep(500);
      ok((await page.inputValue('#q')) === 'рис' && (await text(page, '#results .cell >> nth=0')).toLowerCase().includes('рис'), '«Назад» из порции — поиск «рис» на месте');
      // отрицательные числа
      await page.click('#quick'); await sleep(500); await page.fill('#qk', '100'); await page.fill('#qp', '-5'); await page.click('#qsave'); await sleep(600);
      ok((await items(page)).every(i => i.p >= 0), 'отрицательные белки не принимаются');
      // фон под листом не прокручивается
      await page.click('#addTab'); await sleep(500);
      const y0 = await page.evaluate(() => document.body.getBoundingClientRect().top);
      await page.mouse.move(200, 60); await page.mouse.wheel(0, 600); await sleep(300);
      ok((await page.evaluate(() => document.body.getBoundingClientRect().top)) === y0, 'под открытым листом страница стоит');
      await page.click('#sheetClose'); await sleep(600);
      // код с пробелом, как его присылает Тренер
      await ctx.route(API + '/api/kcal/pair', r => r.fulfill({ status: JSON.parse(r.request().postData()).code === '123456' ? 200 : 403, contentType: 'application/json', body: '{"token":"tok","error":"Неверный или старый код"}' }));
      await page.click('#camBtn'); await sleep(500);
      await page.fill('#code', '123 456'); await sleep(700);
      ok(await page.isVisible('#pick'), 'код «123 456» с пробелом принимается');
      await page.click('#sheetClose'); await sleep(600);
      ok(page.errors.length === 0, 'нет ошибок в консоли (находки)', page.errors.join(' | '));
      await ctx.close();
    }
    /* 12. Испорченная копия не ломает приложение и не исполняет код */
    {
      const { ctx, page } = await newPage(browser);
      const bad = { goals: { train: { kcal: '3000' } }, days: { '2026-10-21': { items: [{ id: '"><img src=x onerror="window.__x=2">', name: 'Строки', kcal: '100', p: '5' }, { id: 'b', kcal: '200' }] }, '2026-10-20': {}, 'мусор': { items: 1 } }, weights: { '2026-10-21': '57,5', x: 1 } };
      const f = path.join(os.tmpdir(), 'kcal-broken.json'); fs.writeFileSync(f, JSON.stringify(bad));
      await page.click('#gear'); await sleep(500);
      const [fc] = await Promise.all([page.waitForEvent('filechooser'), page.click('#imp')]); await fc.setFiles(f); await sleep(700);
      ok((await text(page, '.ring-in')).includes('300 из 3 000'), 'строки-числа складываются: 300 из 3 000', await text(page, '.ring-in'));
      ok(await page.evaluate(() => window.__x === undefined), 'id из файла не исполняется (XSS)');
      await page.reload(); await sleep(700);
      ok(await page.isVisible('#gear') && await page.isVisible('#left'), 'после перезагрузки приложение живо');
      await page.click('#gear'); await sleep(500);
      const [fc2] = await Promise.all([page.waitForEvent('filechooser'), page.click('#imp')]); await fc2.setFiles(f); await sleep(600);
      ok((await toastText(page)).includes('Копия загружена'), 'тот же файл можно загрузить ещё раз');
      ok(page.errors.length === 0, 'нет ошибок в консоли (испорченная копия)', page.errors.join(' | '));
      await ctx.close();
    }
    /* 13. Блюда со всего мира и продукты из магазинов */
    {
      const { ctx, page } = await newPage(browser);
      let shopCalls = 0;
      await ctx.route(API + '/api/kcal/shop?*', r => { shopCalls++; const q = new globalThis.URL(r.request().url()).searchParams.get('q');
        return q.includes('ошибка') ? r.fulfill({ status: 502, contentType: 'application/json', body: '{"error":"x"}' })
          : r.fulfill({ status: 200, contentType: 'application/json', headers: { 'Access-Control-Allow-Origin': '*' }, body: JSON.stringify({ items: [{ name: 'Сырок глазированный ванильный', brand: 'Чудо', kcal: 421, p: 8.7, f: 28.7, c: 31.9, salt: 0.25, g: 40 }] }) }); });
      const n = JSON.parse(fs.readFileSync(path.join(ROOT, 'kcal', 'foods.json'), 'utf8')).length;
      ok(n > 5000, `в базе мира ${n} блюд`);
      await page.click('#addTab'); await sleep(500);
      for (const [q, want] of [['пад тай', 'пад-тай'], ['буррито', 'буррито'], ['суши', ''], ['лазань', 'лазань'], ['тако', 'тако']]) {
        await page.fill('#q', q); await sleep(350);
        const t = (await text(page, '#results')).toLowerCase();
        const cnt = await page.locator('#results [data-k^="s"]').count();
        ok(cnt > 0 && (!want || t.includes(want)), `поиск «${q}» находит (${cnt})`, t.slice(0, 120));
      }
      await page.fill('#q', 'курица'); await sleep(350);
      ok((await text(page, '#results')).includes('уточни запрос'), 'много совпадений — показаны первые и «уточни запрос»');
      ok((await text(page, '#results [data-k="s0"]')).startsWith('Куриная') || (await text(page, '#results [data-k="s0"]')).toLowerCase().startsWith('кур'), 'сверху — то, что начинается с запроса', await text(page, '#results [data-k="s0"]'));
      await page.fill('#q', 'сырок глаз'); await sleep(1100);
      ok((await text(page, '#results')).includes('В магазинах') && (await text(page, '#results')).includes('Чудо'), 'продукты из магазина с маркой');
      await page.click('#results [data-k="o0"]'); await sleep(500);
      ok((await page.inputValue('#g')) === '40' && (await text(page, '.portion .title')).includes('Чудо'), 'продукт из магазина: порция с упаковки 40 г');
      await page.click('#ok'); await sleep(600);
      ok(Math.round((await items(page))[0].kcal) === 168, 'добавлен сырок 40 г = 168 ккал', JSON.stringify(await items(page)));
      await page.click('#addTab'); await sleep(500); await page.fill('#q', 'ошибка сети'); await sleep(1100);
      ok(!(await text(page, '#results')).includes('Ищу в магазинах') && page.errors.length === 0, 'магазины не ответили — тихо, без ошибок');
      await page.fill('#q', 'ab'); await sleep(700);
      const before = shopCalls; await page.fill('#q', 'mol'); await page.fill('#q', 'molo'); await page.fill('#q', 'molok'); await sleep(900);
      ok(shopCalls - before === 1, 'быстрый ввод — один запрос к магазинам', String(shopCalls - before));
      ok(page.errors.length === 0, 'нет ошибок в консоли (база мира)', page.errors.join(' | '));
      await ctx.close();
    }
    /* 14а. Норма воды: старые значения по умолчанию заменяются расчётом, своя норма — остаётся */
    {
      const base = { goals: { train: { kcal: 3400, p: 130, f: 105, c: 485 }, rest: { kcal: 2800, p: 125, f: 95, c: 360 } }, trainDays: [0, 1, 2, 5], days: {}, custom: [], recent: [], weights: {} };
      let { ctx, page } = await newPage(browser, { seed: { ...base, water: { train: 3000, rest: 2500 } } });
      ok((await text(page, '#water')).includes('0 из 3,5 л'), 'старая норма 3 л → расчёт 3,5 л в день тренировки', await text(page, '#water'));
      await ctx.close();
      ({ ctx, page } = await newPage(browser, { seed: { ...base, water: { train: 4000, rest: 2000 }, waterSet: true } }));
      ok((await text(page, '#water')).includes('0 из 4 л'), 'своя норма 4 л сохраняется', await text(page, '#water'));
      await ctx.close();
    }
    /* 14. Мои порции больше обычных и вода */
    {
      const { ctx, page } = await newPage(browser);
      await page.click('#addTab'); await sleep(500); await page.fill('#q', 'пельмени'); await sleep(300);
      await page.click('#results [data-k="s0"]'); await sleep(500);
      ok((await page.inputValue('#g')) === '375' && (await text(page, '#units')).includes('порция · 375 г'), 'пельмени: порция 250 г × 1,5 = 375 г (большие по умолчанию)', await page.inputValue('#g'));
      await page.click('#backBtn'); await sleep(400); await page.fill('#q', 'банан'); await sleep(300); await page.click('#results [data-k="s0"]'); await sleep(500);
      ok((await page.inputValue('#g')) === '120', 'банан — 1 шт как есть (штуки не увеличиваются)');
      await page.click('#sheetClose'); await sleep(500);
      await page.click('#gear'); await sleep(500); await page.click('#por [data-p="1"]'); await sleep(100);
      await page.fill('#w-train', '3500'); await page.press('#w-train', 'Tab'); await sleep(100);
      await page.fill('#w-rest', '100'); await page.press('#w-rest', 'Tab'); await sleep(150);
      ok((await toastText(page)).includes('От 500 до 8000'), 'норма воды 100 мл не принимается');
      await page.click('#sheetClose'); await sleep(600);
      ok((await state(page)).portion === 1 && (await state(page)).water.train === 3500, 'настройки: порции обычные, вода 3500 мл');
      await page.click('#addTab'); await sleep(500); await page.fill('#q', 'пельмени'); await sleep(300); await page.click('#results [data-k="s0"]'); await sleep(500);
      ok((await page.inputValue('#g')) === '250', '«Обычные» — порция 250 г');
      await page.click('#sheetClose'); await sleep(500);
      // вода (среда — тренировка, норма 3,5 л)
      ok((await text(page, '#water')).includes('0 из 3,5 л'), 'вода: 0 из 3,5 л', await text(page, '#water'));
      ok(await page.$eval('[data-ml="undo"]', b => b.disabled), 'отмена недоступна, пока пусто');
      await page.click('[data-ml="250"]'); await sleep(200); await page.click('[data-ml="250"]'); await sleep(200); await page.click('[data-ml="500"]'); await sleep(300);
      ok((await text(page, '#water')).includes('1 из 3,5 л') && (await text(page, '#water')).includes('Ещё 2,5 л'), 'вода: +250 +250 +500 = 1 л', await text(page, '#water'));
      await page.click('[data-ml="undo"]'); await sleep(300);
      ok((await text(page, '#water')).includes('0,5 из 3,5 л') && (await toastText(page)).includes('Убрано 500'), 'отмена последнего: 0,5 л');
      await page.click('[data-ml="other"]'); await sleep(500); await page.fill('#ml', '5'); await page.click('#mlok'); await sleep(150);
      ok((await toastText(page)).includes('От 10 до 3000'), 'другое: 5 мл не принимается');
      await page.click('#mlc [data-v="1000"]'); await page.click('#mlok'); await sleep(600);
      for (let i = 0; i < 4; i++) { await page.click('[data-ml="500"]'); await sleep(150); }
      ok((await toastText(page)).includes('Норма воды выпита') && (await text(page, '#water')).includes('Норма выпита'), 'норма воды выпита — сообщение', await toastText(page));
      await page.reload(); await sleep(700);
      ok((await text(page, '#water')).includes('3,5 из 3,5 л'), 'вода сохраняется', await text(page, '#water'));
      await page.click('[data-tab=history]'); await sleep(700);
      ok((await text(page, '#app')).includes('Выпил норму') && (await text(page, '#app')).includes('3,5 л'), 'вода в истории');
      ok(page.errors.length === 0, 'нет ошибок в консоли (порции и вода)', page.errors.join(' | '));
      await shot(page, '14-history', true);
      await page.click('[data-tab=today]'); await sleep(600); await shot(page, '14-today', true);
      await ctx.close();
    }
    /* 15. Чата в «Калориях» нет (Глеб 03.11: «для этого есть общий чат») */
    {
      const { ctx, page } = await newPage(browser);
      ok(!(await page.$('[data-tab=chat]')) && !(await page.$('#composer')) && (await page.locator('.tab[data-tab]').count()) === 2, 'чата нет: в меню «Сегодня» и «История», поля ввода нет');
      ok(!(await page.evaluate(() => typeof renderChat !== 'undefined' || typeof sendChat !== 'undefined')), 'код чата удалён');
      ok(page.errors.length === 0, 'нет ошибок в консоли (без чата)', page.errors.join(' | '));
      await ctx.close();
      // старая переписка в памяти не мешает и не тянется дальше
      const base = { goals: { train: { kcal: 3400, p: 130, f: 105, c: 485 }, rest: { kcal: 2800, p: 125, f: 95, c: 360 } }, trainDays: [0, 1, 2, 5], days: {}, custom: [], recent: [], weights: {} };
      const r = await newPage(browser, { seed: { ...base, chat: [{ role: 'user', text: 'привет' }] } });
      await addFood(r.page, 'банан', 120, 'snack');
      ok(!('chat' in (await state(r.page))) && r.page.errors.length === 0, 'старая переписка из памяти убирается без ошибок');
      await r.ctx.close();
    }
    /* 14. Связь с Формой (coach/SYNC.md): #pair, sync с debounce, карточка, авто-тип дня, вода и еда Формы, 401, нет сети */
    const FORMA = { day: '2026-10-21', recovery: { score: 32, hrv: 85, rhr: 63 }, sleep: { hours: 7.6, performance: 84 }, strain: 2.9, train_today: false,
      workouts: [{ sport: 'boxing', start: '10:20', minutes: 98, strain: 13.4 }], water_forma: 500,
      meals_forma: [{ time: '13:10', title: 'Плов <b>', kcal: 600, p: 20, f: 25, c: 70 }],
      regime: { day: '2026-10-21', mode: 'vacation', bed: '23:45', wake: '08:30', train: { on: true, time: '10:00', minutes: 90, intensity: 'light', why: '' } } };
    const okForma = r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, forma: FORMA }) });
    {
      // без связи: ничего не уходит, карточки нет (даже если в памяти остался прошлый ответ), в настройках — «Связать с Формой»
      const { ctx, page } = await newPage(browser, { sync: okForma });
      await page.evaluate(f => localStorage.setItem('kcal.forma', f), JSON.stringify({ ...FORMA, water: 500, meals: FORMA.meals_forma })); await page.reload(); await sleep(600);
      await addFood(page, 'банан', 120, 'snack'); await page.click('#water [data-ml="250"]'); await sleep(2600);
      ok(ctx.syncs.length === 0, 'без токена sync не отправляется', String(ctx.syncs.length));
      ok(!(await page.$('#forma')) && !(await page.$('#fmeals')), 'без токена карточки Формы нет');
      await page.click('#gear'); await sleep(500);
      ok((await text(page, '#pairBtn')).includes('Связать с Формой'), 'в настройках — «Связать с Формой»', await text(page, '#pairBtn'));
      ok(page.errors.length === 0, 'нет ошибок в консоли (Форма, без связи)', page.errors.join(' | '));
      await ctx.close();
    }
    {
      // вход по ссылке #pair → токен, хэш убран, тост, сразу sync; ответ → карточка
      const { ctx, page } = await newPage(browser, { hash: '#pair=tok123456', sync: okForma, seed: { goals: { train: { kcal: 3400, p: 130, f: 105, c: 485 }, rest: { kcal: 2800, p: 125, f: 95, c: 360 } }, days: {} } });
      ok(await page.evaluate(() => localStorage.getItem('kcal.token')) === 'tok123456', '#pair → токен сохранён');
      ok(await page.evaluate(() => location.hash === '' && !location.href.includes('tok')), 'токен убран из адреса');
      ok((await toastText(page)).includes('Связано с Формой'), 'тост «Связано с Формой»', await toastText(page));
      ok(ctx.syncs.length >= 1 && ctx.syncs[0].auth === 'Bearer tok123456', 'сразу sync с токеном', JSON.stringify(ctx.syncs.map(x => x.auth)));
      const b0 = ctx.syncs[0] && ctx.syncs[0].body;
      ok(b0 && b0.goals.train.kcal === 3400 && b0.goals.rest.c === 360 && b0.salt === 5 && b0.water.train === 3500 && b0.water.rest === 2600 && typeof b0.days === 'object', 'в sync — нормы, соль, вода', JSON.stringify(b0));
      await sleep(300);
      const card = await text(page, '#forma');
      ok(['32', 'низкое', '7,6 ч', '84%', '2,9', 'Бокс', '10:20', '1 ч 38 мин', '13,4', 'Отбой 23:45', 'тренировка 10:00', 'лёгкая'].every(w => card.includes(w)), 'карточка Формы: восстановление, сон, нагрузка, тренировка, режим', card);
      ok(await page.$eval('#forma .zdot', e => getComputedStyle(e).backgroundColor) === 'rgb(255, 0, 38)', 'восстановление 32 — красная точка зоны');
      ok((await page.getAttribute('#seg [data-type=rest]', 'aria-pressed')) === 'true', 'среда, но Форма: тренировки нет → «Отдых» сам');
      ok((await text(page, '#left')) === '2 200', 'еда Формы в итогах: 2 800 − 600 = 2 200', await text(page, '#left'));
      const fm = await text(page, '#fmeals');
      ok(fm.includes('Из Формы') && fm.includes('Плов <b>') && fm.includes('600') && fm.includes('13:10'), 'секция «Из Формы» (только просмотр, имя как текст)', fm);
      ok(!(await page.$('#fmeals button')), 'еда из Формы не правится');
      ok((await text(page, '#water .sec-h')).includes('0,5 из 2,6 л'), 'вода: своя 0 + из Формы 0,5', await text(page, '#water .sec-h'));
      await shot(page, '14-forma');
      await sleep(2300); // тип дня по Форме уходит следующим sync
      let n = ctx.syncs.length, last = ctx.syncs[n - 1].body;
      ok(last.days['2026-10-21'] && last.days['2026-10-21'].type === 'rest', 'тип дня по Форме уходит в sync', JSON.stringify(last.days));
      // debounce: два нажатия подряд → один запрос через ~2 с
      await page.click('#water [data-ml="250"]'); await sleep(150); await page.click('#water [data-ml="500"]'); await sleep(1000);
      ok(ctx.syncs.length === n, 'через 1 с после сохранения ещё не отправлено (debounce)', `${ctx.syncs.length} vs ${n}`);
      await sleep(1700);
      ok(ctx.syncs.length === n + 1, 'два сохранения подряд → один sync', `${ctx.syncs.length} vs ${n + 1}`);
      last = ctx.syncs[ctx.syncs.length - 1].body.days['2026-10-21'];
      ok(last && JSON.stringify(last.water) === '[250,500]', 'в sync своя вода (без воды Формы)', JSON.stringify(last));
      ok((await text(page, '#water .sec-h')).includes('1,3 из 2,6 л'), 'общий итог воды 0,75 + 0,5 ≈ 1,3 л', await text(page, '#water .sec-h'));
      await addFood(page, 'банан', 120, 'snack'); await sleep(2400);
      last = ctx.syncs[ctx.syncs.length - 1].body.days['2026-10-21'];
      const it = last && last.items[0];
      ok(it && it.name === 'Банан' && it.meal === 'snack' && it.g === 120 && it.kcal === 114 && it.p === 1.8 && 'f' in it && 'c' in it && it.salt === 0 && !('id' in it) && !('per100' in it), 'в sync еда: name/meal/g/kcal/p/f/c/salt', JSON.stringify(it));
      ok(last.items.length === 1, 'еда из Формы обратно не уходит');
      ok((await text(page, '#left')) === '2 086', 'итог: 2 800 − 600 − 114 = 2 086', await text(page, '#left'));
      // ручной выбор дня сильнее Формы
      await page.click('#seg [data-type=train]'); await sleep(600); await page.reload(); await sleep(900);
      ok((await page.getAttribute('#seg [data-type=train]', 'aria-pressed')) === 'true', 'выбрал «Тренировка» сам — Форма не перебивает');
      // другой день — без карточки
      await page.click('.wd[data-d="2026-10-20"]'); await sleep(500);
      ok(!(await page.$('#forma')) && !(await page.$('#fmeals')), 'вчера — карточки и еды Формы нет');
      await page.click('#todayBtn'); await sleep(500);
      ok(await page.isVisible('#forma'), 'сегодня — карточка снова');
      await page.setViewportSize({ width: 1920, height: 1080 }); await sleep(400);
      await shot(page, '14-forma-1920');
      ok(page.errors.length === 0, 'нет ошибок в консоли (Форма)', page.errors.join(' | '));
      await ctx.close();
    }
    {
      // 401 → токен сброшен, «связь потеряна», карточки нет
      const { ctx, page } = await newPage(browser, { hash: '#pair=tok123456', sync: r => r.fulfill({ status: 401, contentType: 'application/json', body: '{"error":"нужен вход"}' }) });
      ok(await page.evaluate(() => localStorage.getItem('kcal.token')) === null, '401 → токен сброшен');
      ok((await toastText(page)).includes('Связь с Формой потеряна'), '401 → «Связь с Формой потеряна»', await toastText(page));
      ok(!(await page.$('#forma')), '401 → карточки нет');
      ok(page.errors.length === 0, 'нет ошибок в консоли (401)', page.errors.join(' | '));
      await ctx.close();
    }
    {
      // нет сети → тихо; при следующем сохранении — повтор
      let net = false;
      const { ctx, page } = await newPage(browser, { hash: '#pair=tok123456', sync: r => net ? okForma(r) : r.abort() });
      await sleep(1600);
      ok(!(await toastText(page)).includes('связ') && !(await page.isVisible('#toast.on')) && !(await page.$('#forma')), 'нет сети — без ошибок на экране');
      net = true; await page.click('#water [data-ml="250"]'); await sleep(2600);
      ok(ctx.syncs.length === 2 && await page.isVisible('#forma'), 'повтор при следующем сохранении → карточка', String(ctx.syncs.length));
      ok(page.errors.length === 0, 'нет ошибок в консоли (нет сети)', page.errors.join(' | '));
      await ctx.close();
    }
    /* 16. Внутри Формы (?embed=1, coach/SYNC.md §5): разделы сверху, тема Формы, без входа и своего меню, всё над меню Формы */
    {
      const { ctx, page } = await newPage(browser, { url: URL + '?embed=1&theme=light', sw: true,
        init: () => { window.__msgs = []; addEventListener('message', e => window.__msgs.push(e.data)); } });
      const bg = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
      ok(!(await page.$('#tabs')) && await page.isVisible('#eseg') && (await page.locator('#eseg [data-tab]').count()) === 2 && !(await page.$('#eseg [data-tab=chat]')), 'embed: своего нижнего меню нет, разделы сверху сегментами — «Сегодня» и «История», без «Чата»');
      ok(await page.isVisible('#addTab'), 'embed: круг «+» остаётся');
      ok(await bg() === 'rgb(255, 255, 255)', 'embed: theme=light — светлая, хотя система тёмная', await bg());
      await shot(page, '16-embed-light');
      await page.evaluate(() => window.postMessage({ type: 'forma-theme', theme: 'dark' }, location.origin)); await sleep(200);
      ok(await bg() === 'rgb(0, 0, 0)', 'embed: сообщение Формы forma-theme → тёмная', await bg());
      await page.evaluate(() => window.postMessage({ type: 'forma-theme', theme: 'red' }, location.origin)); await sleep(150);
      ok(await page.evaluate(() => document.documentElement.dataset.theme) === 'dark', 'embed: неизвестная тема не применяется');
      const H = await page.evaluate(() => innerHeight);
      const pb = await page.$eval('#app', e => parseFloat(getComputedStyle(e).paddingBottom));
      ok(pb >= 90 + 80, 'embed: снизу отступ над меню Формы (≥ 90 px)', String(pb));
      const fab = await page.locator('#addTab').boundingBox();
      ok(fab.y + fab.height <= H - 90, 'embed: «+» выше меню Формы', JSON.stringify(fab));
      ok(!(await page.textContent('body')).includes('Домой'), 'embed: без подсказок «На экран Домой»');
      ok(await page.evaluate(() => navigator.serviceWorker.getRegistrations().then(r => r.length)) === 0, 'embed: свой service worker не регистрируется');
      // лист добавления целиком виден над меню Формы
      await page.click('#addTab'); await sleep(700);
      let sh = await page.locator('#sheet').boundingBox();
      ok(sh.y >= 0 && sh.y + sh.height <= H - 90 + 1, 'embed: лист «Добавить» целиком над меню Формы', JSON.stringify(sh));
      await page.fill('#q', 'банан'); await sleep(150); await page.click('#results [data-k] >> nth=0'); await sleep(500);
      const okb = await page.locator('#ok').boundingBox();
      ok(okb && okb.y + okb.height <= H - 90, 'embed: кнопка «Добавить» в листе не под меню Формы', JSON.stringify(okb));
      await page.click('#ok'); await sleep(700);
      ok((await page.evaluate(() => window.__msgs)).some(m => m && m.type === 'kcal-changed'), 'embed: после сохранения — kcal-changed наверх', JSON.stringify(await page.evaluate(() => window.__msgs)));
      // без связи: фото — без кода, в настройках — без «Связать/Подключить»
      await page.click('#camBtn'); await sleep(500);
      ok(!(await page.isVisible('#code')) && (await toastText(page)).includes('Форм'), 'embed: фото без токена — без второго входа', await toastText(page));
      await page.click('#gear'); await sleep(600);
      ok(!(await page.$('#pairBtn')), 'embed: в настройках нет «Связать с Формой»');
      sh = await page.locator('#sheet').boundingBox();
      ok(sh.y >= 0 && sh.y + sh.height <= H - 90 + 1, 'embed: высокий лист «Настройки» над меню Формы', JSON.stringify(sh));
      await page.click('#sheetClose'); await sleep(600);
      // разделы: сегменты, линза едет, прилипают при прокрутке
      const th0 = await page.$eval('#eseg .thumb', e => e.style.transform);
      await page.click('#eseg [data-tab=history]'); await sleep(700);
      ok((await text(page, '.large')) === 'История' && (await page.getAttribute('#eseg [data-tab=history]', 'aria-pressed')) === 'true', 'embed: сегмент «История» открывает раздел');
      ok(await page.$eval('#eseg .thumb', e => e.style.transform) !== th0, 'embed: линза сегментов переехала');
      await page.click('#addW'); await sleep(600);
      sh = await page.locator('#sheet').boundingBox();
      ok(sh.y + sh.height <= H - 90, 'embed: низкий лист (вес) над меню Формы', JSON.stringify(sh));
      await page.click('#sheetClose'); await sleep(600);
      await page.evaluate(() => scrollTo(0, 0)); await sleep(400); // кнопка веса внизу — страница прокрутилась, панель уехала; вверху — снова видна
      await page.click('#eseg [data-tab=today]'); await sleep(700);
      // разделы сверху: листаю вниз — уезжают (как строка адреса Safari), вверх — возвращаются (Глеб 03.11)
      const away = () => page.$eval('#etop', e => e.classList.contains('away'));
      ok(await page.$eval('#etop', e => getComputedStyle(e).transitionProperty.includes('transform')), 'embed: панель разделов едет transform-ом (без перерисовки)');
      await page.mouse.wheel(0, 900); await sleep(500);
      let eb = await page.locator('#eseg').boundingBox();
      ok(await page.evaluate(() => scrollY) > 200 && await away() && eb.y + eb.height <= 0, 'embed: листаю вниз — разделы уезжают вверх', JSON.stringify(eb));
      await page.evaluate(() => scrollBy(0, -5)); await sleep(300);
      ok(await away(), 'embed: дрожь на 5 px вверх панель не дёргает');
      await page.mouse.wheel(0, -60); await sleep(500);
      eb = await page.locator('#eseg').boundingBox();
      ok(!(await away()) && eb.y >= 0 && eb.y < 40, 'embed: листаю вверх — разделы возвращаются', JSON.stringify(eb));
      await page.mouse.wheel(0, 300); await sleep(450);
      ok(await away(), 'embed: снова вниз — снова уезжают');
      await page.click('#addTab'); await sleep(600); await page.click('#sheetClose'); await sleep(600);
      ok(await away(), 'embed: открыл и закрыл лист — панель не прыгает');
      await page.mouse.wheel(0, -3000); await sleep(500);
      ok(!(await away()), 'embed: наверху страницы — разделы всегда видны');
      await page.click('.wd[data-d="2026-10-19"]'); await sleep(500);
      await page.click('#eseg [data-tab=today]'); await sleep(600);
      ok((await text(page, '.large')) === 'Сегодня', 'embed: ещё раз «Сегодня» — назад к сегодняшнему дню', await text(page, '.large'));
      await page.evaluate(() => window.postMessage({ type: 'forma-theme', theme: 'dark' }, location.origin)); await sleep(200);
      await shot(page, '16-embed-dark');
      await page.setViewportSize({ width: 1280, height: 800 }); await sleep(400);
      ok(await page.$eval('#app', e => parseFloat(getComputedStyle(e).paddingBottom)) < 120, 'embed ≥ 1024: без отступа под меню Формы');
      await shot(page, '16-embed-1280');
      ok(page.errors.length === 0, 'нет ошибок в консоли (embed)', page.errors.join(' | '));
      await ctx.close();
    }
    {
      // вне embed — всё как раньше: меню снизу, сегментов сверху нет, service worker есть
      const { ctx, page } = await newPage(browser, { sw: true });
      await sleep(800);
      ok(await page.isVisible('#tabs') && !(await page.$('#eseg')), 'обычный режим: нижнее меню, без верхних разделов');
      ok(await page.evaluate(() => navigator.serviceWorker.getRegistrations().then(r => r.length)) === 1, 'обычный режим: service worker регистрируется');
      ok(await page.evaluate(() => API) === `http://localhost:${PORT}`, 'на своём домене API = свой адрес (location.origin)');
      await ctx.close();
    }
    {
      // на github.io — сервер Формы
      const ctx = await browser.newContext({ ...devices['iPhone 14'], serviceWorkers: 'block' });
      await ctx.clock.setFixedTime(NOW);
      const KC = path.join(ROOT, 'kcal'), hits = [];
      await ctx.route('https://sawqws.github.io/123/kcal/**', r => { const f = new globalThis.URL(r.request().url()).pathname.replace('/123/kcal/', '') || 'index.html';
        const fp = path.join(KC, path.basename(f)); return fs.existsSync(fp) ? r.fulfill({ path: fp }) : r.fulfill({ status: 404, body: '' }); });
      await ctx.route(FORMA_API + '/api/**', r => { hits.push(new globalThis.URL(r.request().url()).pathname);
        return r.fulfill({ status: 200, contentType: 'application/json', body: r.request().url().endsWith('/state') ? '{"days":{}}' : '{"ok":true,"forma":null}' }); });
      await ctx.addInitScript(() => localStorage.setItem('kcal.token', 'tokgh'));
      const page = await ctx.newPage(); await page.goto('https://sawqws.github.io/123/kcal/'); await sleep(1500);
      ok(hits.includes('/api/kcal/state') && hits.includes('/api/kcal/sync'), 'на github.io — запросы на сервер Формы', JSON.stringify(hits));
      await ctx.close();
    }
    {
      // embed: токен протух (401) → kcal-auth наверх (Форма выдаст новый), без своего экрана связи
      const { ctx, page } = await newPage(browser, { url: URL + '?embed=1&theme=dark', init: () => { localStorage.setItem('kcal.token', 'old'); window.__msgs = []; addEventListener('message', e => window.__msgs.push(e.data)); },
        sync: r => r.fulfill({ status: 401, contentType: 'application/json', body: '{"error":"нужен вход"}' }) });
      await sleep(500);
      ok((await page.evaluate(() => window.__msgs)).some(m => m && m.type === 'kcal-auth'), 'embed: 401 → kcal-auth Форме', JSON.stringify(await page.evaluate(() => window.__msgs)));
      ok(!(await page.$('#toast button')) && !(await page.isVisible('#code')), 'embed: 401 — без «Связать» и кода');
      ok(page.errors.length === 0, 'нет ошибок в консоли (embed 401)', page.errors.join(' | '));
      await ctx.close();
    }
    {
      // ответ sync со stale (на сервере новее) → сразу забрать state
      const st = { days: { '2026-10-20': { items: [{ name: 'Новее на сервере', meal: 'lunch', g: 100, kcal: 100, p: 1, f: 1, c: 1, salt: 0 }], u: 9e12 } } };
      const { ctx, page } = await newPage(browser, { init: () => localStorage.setItem('kcal.token', 'tok'), seed: { goals: { train: { kcal: 3400, p: 130, f: 105, c: 485 }, rest: { kcal: 2800, p: 125, f: 95, c: 360 } }, fullSync: true, days: {} },
        state: { days: {} }, sync: r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, forma: null, stale: ['2026-10-20'] }) }) });
      await ctx.unroute(API + '/api/kcal/state');
      await ctx.route(API + '/api/kcal/state', r => { ctx.states++; return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(st) }); }); // теперь на сервере день новее
      await page.click('#water [data-ml="250"]'); await sleep(2600);
      const S2 = await state(page);
      ok(ctx.states >= 1 && S2.days['2026-10-20'] && S2.days['2026-10-20'].items[0].name === 'Новее на сервере', 'stale в ответе sync → день взят с сервера', JSON.stringify(S2.days['2026-10-20']));
      await ctx.close();
    }
    /* 17. Данные на любом устройстве: u у дня, слияние со state (новее побеждает), полная история кусками по 31, флаг */
    const G = { train: { kcal: 3400, p: 130, f: 105, c: 485 }, rest: { kcal: 2800, p: 125, f: 95, c: 360 } };
    const food = (name, kcal) => ({ id: 'x' + name.length + kcal, meal: 'lunch', name, g: 100, kcal, p: 1, f: 1, c: 1, salt: 0 });
    {
      const seed = { goals: G, fullSync: true, days: {
        '2026-10-20': { items: [food('Местное старое', 100)], u: 1000 }, '2026-10-19': { items: [food('Местное новое', 200)], u: 5000 } } };
      const st = { days: { '2026-10-20': { items: [{ name: 'С сервера', meal: 'dinner', g: 200, kcal: 300, p: 20, f: 10, c: 30, salt: 1 }], water: [500], type: 'rest', u: 2000 },
        '2026-10-19': { items: [{ name: 'Серверное старое', meal: 'lunch', g: 100, kcal: 1, p: 0, f: 0, c: 0, salt: 0 }], u: 3000 },
        '2026-10-18': { items: [{ name: 'Только на сервере', meal: 'snack', g: 50, kcal: 80, p: 1, f: 2, c: 3, salt: 0 }], u: 100 },
        '2024-01-01': { items: [{ name: 'Слишком старое', meal: 'snack', g: 50, kcal: 80, p: 1, f: 2, c: 3, salt: 0 }], u: 100 } },
        goals: { train: { kcal: 3000, p: 140, f: 100, c: 400 }, rest: { kcal: 2500, p: 120, f: 90, c: 300 } }, salt: 6, water: { train: 3200, rest: 2600 } };
      const { ctx, page } = await newPage(browser, { seed, state: st, init: () => localStorage.setItem('kcal.token', 'tok') });
      await sleep(600);
      let S1 = await state(page);
      ok(ctx.states === 1, 'при старте с токеном — GET /api/kcal/state', String(ctx.states));
      ok(S1.days['2026-10-20'].items[0].name === 'С сервера' && S1.days['2026-10-20'].u === 2000, 'слияние: на сервере новее — берём серверный день', JSON.stringify(S1.days['2026-10-20']));
      ok(S1.days['2026-10-19'].items[0].name === 'Местное новое' && S1.days['2026-10-19'].u === 5000, 'слияние: старый серверный день не затирает новый свой');
      ok(S1.days['2026-10-18'] && S1.days['2026-10-18'].items[0].name === 'Только на сервере', 'слияние: дня нет у себя — добавлен');
      ok(!S1.days['2024-01-01'], 'слияние: дни старше 400 дней не берём');
      const it = S1.days['2026-10-20'].items[0];
      ok(it.per100 && Math.round(it.per100.kcal) === 150 && JSON.stringify(S1.days['2026-10-20'].water) === '[500]' && dayTypeOf(S1, '2026-10-20') === 'rest', 'серверный день: состав на 100 г, вода, тип', JSON.stringify(S1.days['2026-10-20']));
      ok(S1.goals.train.kcal === 3000 && S1.salt === 6 && S1.water.rest === 2600, 'нормы, соль и вода — с сервера (свои были по умолчанию)', JSON.stringify([S1.goals.train, S1.salt, S1.water]));
      ok((await text(page, '#left')) === '3 000', 'новая норма сразу на экране', await text(page, '#left'));
      const b0 = ctx.syncs[0] && ctx.syncs[0].body;
      ok(b0 && b0.days['2026-10-19'].u === 5000 && b0.days['2026-10-20'].u === 2000 && b0.days['2026-10-20'].items[0].name === 'С сервера', 'sync после слияния: дни с u', JSON.stringify(b0 && Object.keys(b0.days)));
      // правка дня → u = время правки, другие дни не трогаются
      await addFood(page, 'банан', 120, 'snack'); await sleep(1200);
      S1 = await state(page);
      ok(S1.days['2026-10-21'].u === NOW.getTime() && S1.days['2026-10-19'].u === 5000 && S1.days['2026-10-20'].u === 2000, 'u ставится только изменённому дню', JSON.stringify([S1.days['2026-10-21'].u, S1.days['2026-10-19'].u]));
      await sleep(1500);
      const bl = ctx.syncs[ctx.syncs.length - 1].body;
      ok(bl.days['2026-10-21'] && bl.days['2026-10-21'].u === NOW.getTime(), 'sync шлёт u изменённого дня');
      // тип дня от Формы — не правка (u не меняется)
      await page.evaluate(() => { S.days['2026-10-18'].ftype = 'train'; save(); });
      ok((await state(page)).days['2026-10-18'].u === 100, 'тип дня по Форме не меняет u');
      // старый день (> 14 дней) поправлен — уходит обычным sync
      await page.evaluate(() => { S.days['2026-09-01'] = { items: [{ id: 'old1', meal: 'lunch', name: 'Давнее', g: 0, kcal: 500, p: 0, f: 0, c: 0, salt: 0 }] }; save(); });
      await sleep(2600);
      const bo = ctx.syncs[ctx.syncs.length - 1].body;
      ok(bo.days['2026-09-01'] && bo.days['2026-09-01'].items[0].name === 'Давнее', 'поправленный давний день уходит в sync', JSON.stringify(Object.keys(bo.days)));
      // «Удалить все записи» при связи — дни пустые с новым u (удаление дойдёт до сервера)
      await page.click('#gear'); await sleep(500); await page.click('#wipe'); await sleep(600);
      S1 = await state(page);
      ok(S1.days['2026-10-19'] && !S1.days['2026-10-19'].items.length && S1.days['2026-10-19'].u > NOW.getTime(), 'удаление при связи — пустые дни с новым u', JSON.stringify(S1.days['2026-10-19']));
      ok(page.errors.length === 0, 'нет ошибок в консоли (слияние)', page.errors.join(' | '));
      await ctx.close();
    }
    {
      // полная история: первый раз (нет флага) — кусками по 31 дню, дни старше 400 — нет; ошибка куска — без флага
      const days = {};
      for (let i = 0; i < 70; i++) { const d = new Date(2026, 9, 21 - i); days[`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`] = { items: [food('Е' + i, 100 + i)] }; }
      days['2025-01-01'] = { items: [food('Давно', 100)] };
      let n = 0, fail = true;
      const { ctx, page } = await newPage(browser, { seed: { goals: G, days }, init: () => localStorage.setItem('kcal.token', 'tok'),
        sync: r => (++n === 2 && fail) ? r.fulfill({ status: 503, body: '' }) : r.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true,"forma":null}' }) });
      await sleep(800);
      ok(ctx.syncs.length === 2 && !(await state(page)).fullSync, 'кусок не принят — флага нет, остальное не шлём', String(ctx.syncs.length));
      fail = false; ctx.syncs.length = 0;
      await page.reload(); await sleep(1200);
      const sent = ctx.syncs.map(x => Object.keys(x.body.days));
      const all = [].concat(...sent);
      ok(sent.length === 3 && sent.every(k => k.length <= 31) && sent[0].length === 31, 'вся история — кусками по 31 дню', JSON.stringify(sent.map(k => k.length)));
      ok(all.length === 70 && new Set(all).size === 70 && !all.includes('2025-01-01'), 'ушли все 70 дней, старше 400 — нет', String(all.length));
      ok(ctx.syncs.every(x => x.body.goals && x.body.salt === 5 && x.auth === 'Bearer tok'), 'в каждом куске — нормы и токен');
      ok((await state(page)).fullSync === true, 'после полной отправки — флаг S.fullSync');
      ctx.syncs.length = 0;
      await page.reload(); await sleep(1200);
      ok(ctx.syncs.length === 1 && Object.keys(ctx.syncs[0].body.days).length <= 14, 'с флагом — обычный sync (14 дней)', JSON.stringify(ctx.syncs.map(x => Object.keys(x.body.days).length)));
      // новая связка (другой токен) — история уходит заново
      ctx.syncs.length = 0;
      await page.evaluate(() => { location.hash = '#pair=newtok123'; }); await sleep(1500);
      ok(ctx.syncs.length === 3 && ctx.syncs[0].auth === 'Bearer newtok123', 'новая связка — вся история заново', String(ctx.syncs.length));
      ok(page.errors.length === 0, 'нет ошибок в консоли (полная история)', page.errors.join(' | '));
      await ctx.close();
    }
    /* 18. Быстрый завтрак, размер порций, новые блюда (Глеб 03.11) */
    {
      const { ctx, page } = await newPage(browser);
      ok(await page.locator('.sec [data-qb]').count() === 4, 'в «Завтраке» — 4 быстрые кнопки');
      const qh = await page.$$eval('.sec [data-qb]', bs => bs.map(b => b.getBoundingClientRect().height));
      ok(qh.every(h => h >= 36), 'быстрые кнопки ≥ 36 px', JSON.stringify(qh));
      await shot(page, '18-quick-before');
      await page.click('.sec [data-qb="nordic"]'); await sleep(650);
      let its = await items(page);
      ok(its.length === 2 && its[0].name === 'Хлопья гречневые Nordic на молоке' && its[1].name === 'Авокадо' && its.every(i => i.meal === 'breakfast'),
        'одно нажатие → хлопья Nordic + авокадо отдельной строкой', JSON.stringify(its.map(i => i.name)));
      ok(Math.round(sum(its, 'kcal')) === 720 && its[0].g === 350 && its[1].g === 145, 'хлопья 490 ккал (350 г) + авокадо 230 ккал (145 г)', JSON.stringify(its.map(i => [i.g, i.kcal])));
      ok(its[0].per100 && Math.round(its[0].per100.kcal) === 140 && its[1].unit && its[1].unit.g === 145, 'у позиций есть состав на 100 г и порция — граммы можно править');
      ok((await toastText(page)).includes('Хлопья Nordic + авокадо') && (await toastText(page)).includes('Отменить'), 'тост: «Хлопья Nordic + авокадо» и «Отменить»', await toastText(page));
      ok((await page.getAttribute('.sec [data-qb="nordic"]', 'aria-pressed')) === 'true' && (await page.getAttribute('.sec [data-qb="avo"]', 'aria-pressed')) === 'true', 'кнопки отмечены «уже добавлено»');
      await shot(page, '18-quick-after');
      await page.click('.sec [data-qb="nordic"]'); await sleep(450);
      ok((await items(page)).length === 2 && (await toastText(page)).includes('уже в завтраке'), 'повтор — не дублирует молча: «уже в завтраке»', await toastText(page));
      await page.click('#toast button'); await sleep(650);
      its = await items(page);
      ok(its.length === 3 && its.filter(i => i.name === 'Авокадо').length === 1, '«Ещё раз» — вторая тарелка хлопьев, авокадо не дублируется', JSON.stringify(its.map(i => i.name)));
      await page.click('.sec [data-qb="rice"]'); await sleep(650);
      its = await items(page);
      ok(its.length === 4 && its[3].name === 'Каша рисовая на молоке' && its[3].g === 400 && Math.round(its[3].kcal) === 396, 'рисовая каша 400 г = 396 ккал, без второго авокадо', JSON.stringify(its[3]));
      await page.click('#toast button'); await sleep(500);
      ok((await items(page)).length === 3, '«Отменить» убирает добавленное');
      await page.click('.sec [data-qb="avo"]'); await sleep(450);
      ok((await items(page)).length === 3 && (await toastText(page)).includes('Авокадо — уже в завтраке'), 'отдельная «Авокадо» — тоже без дубля');
      // в листе «Добавить»
      await page.click('[data-add="breakfast"] >> nth=0', { position: { x: 30, y: 20 } }); await sleep(650);
      ok(await page.isVisible('#qbAdd [data-qb="millet"]'), 'лист «Добавить» для завтрака — сверху быстрые кнопки');
      await shot(page, '18-add-breakfast');
      await page.click('#mseg [data-m="lunch"]'); await sleep(300);
      ok(!(await page.isVisible('#qbAdd')), 'для обеда — их нет');
      await page.click('#mseg [data-m="breakfast"]'); await sleep(300);
      await page.fill('#q', 'каш'); await sleep(250);
      ok(!(await page.isVisible('#qbAdd')), 'при поиске — спрятаны');
      await page.fill('#q', ''); await sleep(250);
      await page.click('#qbAdd [data-qb="millet"]'); await sleep(750);
      its = await items(page);
      ok(its.some(i => i.name === 'Каша пшённая на молоке' && Math.round(i.kcal) === 460 && i.meal === 'breakfast') && await page.isHidden('#sheet'), 'пшённая из листа — 460 ккал, лист закрылся');
      // другой день — кнопки снова свободны
      await page.click('#week [data-d="2026-10-20"]'); await sleep(600);
      ok((await page.getAttribute('.sec [data-qb="nordic"]', 'aria-pressed')) === 'false', 'на другой день кнопки не отмечены');
      await page.click('#todayBtn'); await sleep(600);
      // разделы и новые блюда
      await page.click('#addTab'); await sleep(900);
      const lt = await text(page, '#results');
      ok(['Завтраки', 'Супы', 'Вторые блюда', 'Салаты', 'Фастфуд, пицца, роллы', 'Крупы и хлеб', 'Молочное', 'Спортпит'].every(g => lt.includes(g)), 'база по разделам: завтраки, супы, вторые, салаты, фастфуд, крупы, молочное, спортпит');
      const nd = await page.locator('#results [data-k^="d"]').count(), nb = await page.locator('#results [data-k^="b"]').count();
      ok(nd >= 130 && nb >= 130, `встроенных блюд ${nd}, продуктов ${nb}`);
      for (const [q, want] of [['гречка с курицей', 'Гречка с курицей'], ['поке', 'Поке с'], ['протеиновый коктейль', 'Протеиновый коктейль'], ['хлопья nordic', 'Хлопья гречневые Nordic'],
        ['паста с лососем', 'Паста с лососем'], ['шуба', ''], ['сельдь под', 'Сельдь под шубой'], ['гейнер', 'Гейнер'], ['ряженка', 'Ряженка'], ['цезарь с кре', 'Салат Цезарь с креветками']]) {
        await page.fill('#q', q); await sleep(300);
        const first = await page.locator('#results [data-k="s0"]').count() ? await text(page, '#results [data-k="s0"] .n') : '';
        if (want) ok(first.startsWith(want), `новое блюдо ищется: «${q}» → ${first}`, first);
      }
      await page.fill('#q', 'поке с лососем'); await sleep(350);
      const pk = await page.$$eval('#results [data-k^="s"]', cs => cs.filter(c => !c.querySelector('.m').textContent.includes('Кофемания')).map(c => c.querySelector('.n').textContent.replace(/\s*≈$/, '').trim())); // «Поке» Кофемании — своё блюдо ресторана
      ok(pk.filter(n => n === 'Поке с лососем').length === 1, 'одинаковое блюдо из мировой базы не дублируется', JSON.stringify(pk));
      await page.fill('#q', 'плов с курицей'); await sleep(300); await page.click('#results [data-k="s0"]'); await sleep(500);
      ok((await page.inputValue('#g')) === '525', 'плов с курицей: порция 350 г × 1,5 = 525 г', await page.inputValue('#g'));
      await page.click('#sheetClose'); await sleep(500);
      // размер порций в настройках
      await page.click('#gear'); await sleep(500);
      ok(await page.locator('#por [data-p]').count() === 3 && (await page.getAttribute('#por [data-p="1.5"]', 'aria-pressed')) === 'true', '«Размер порций»: обычные / большие / очень большие, по умолчанию — большие');
      ok((await text(page, '#por')).includes('Очень большие') && (await text(page, '#por')).includes('×2'), 'подписи ×1, ×1,5, ×2');
      await shot(page, '18-settings');
      await page.click('#por [data-p="2"]'); await sleep(150); await page.click('#sheetClose'); await sleep(500);
      ok((await state(page)).portion === 2, 'выбрано ×2 — сохранилось');
      await page.click('#addTab'); await sleep(500); await page.fill('#q', 'пельмени'); await sleep(300); await page.click('#results [data-k="s0"]'); await sleep(500);
      ok((await page.inputValue('#g')) === '500', '«Очень большие» — пельмени 500 г', await page.inputValue('#g'));
      await page.click('#sheetClose'); await sleep(500);
      ok(page.errors.length === 0, 'нет ошибок в консоли (быстрый завтрак, порции)', page.errors.join(' | '));
      await ctx.close();
    }
    /* 18а. Размер порций: старые значения → ближайший из трёх; с сервера — если свой по умолчанию; уходит в sync */
    {
      const base = { goals: { train: { kcal: 3400, p: 130, f: 105, c: 485 }, rest: { kcal: 2800, p: 125, f: 95, c: 360 } }, trainDays: [0, 1, 2, 5], days: {}, custom: [], recent: [], weights: {} };
      let { ctx, page } = await newPage(browser, { seed: { ...base, portion: 1.2 } });
      await page.click('#gear'); await sleep(500);
      ok((await page.getAttribute('#por [data-p="1.5"]', 'aria-pressed')) === 'true', 'старые «+20 %» → «Большие ×1,5»');
      await page.click('#sheetClose'); await sleep(500);
      await page.click('#addTab'); await sleep(500); await page.fill('#q', 'пельмени'); await sleep(300); await page.click('#results [data-k="s0"]'); await sleep(500);
      ok((await page.inputValue('#g')) === '375', 'и порции считаются ×1,5', await page.inputValue('#g'));
      await ctx.close();
      ({ ctx, page } = await newPage(browser, { seed: { ...base, portion: 1 } }));
      ok((await state(page)).portion === 1, '«Как у всех» остаётся «Обычные»');
      await ctx.close();
      ({ ctx, page } = await newPage(browser, { seed: { ...base, fullSync: true }, state: { days: {}, portion: 2 }, init: () => localStorage.setItem('kcal.token', 'tok') }));
      await sleep(900);
      ok((await state(page)).portion === 2, 'размер порций с сервера (свой был по умолчанию)');
      ok(ctx.syncs.length && ctx.syncs[ctx.syncs.length - 1].body.portion === 2, 'размер порций уходит в sync', JSON.stringify(ctx.syncs.map(x => x.body.portion)));
      await ctx.close();
      ({ ctx, page } = await newPage(browser, { seed: { ...base, portion: 1, fullSync: true }, state: { days: {}, portion: 2 }, init: () => localStorage.setItem('kcal.token', 'tok') }));
      await sleep(900);
      ok((await state(page)).portion === 1, 'свой выбор не перебивается сервером');
      await ctx.close();
    }
    /* 19. «Часто» и закрепы (Глеб 03.11): частота × свежесть за 60 дней, приём пищи, обычная порция, закреп первым */
    {
      const dd = n => { const x = new Date(2026, 9, 21 - n); return `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, '0')}-${String(x.getDate()).padStart(2, '0')}`; };
      const it = (name, g, meal, k100, id) => ({ id, meal, name, g, kcal: k100 * g / 100, p: g / 20, f: g / 40, c: g / 8, salt: 0.1, per100: { kcal: k100, p: 5, f: 2.5, c: 12.5, salt: 0.05 }, unit: null });
      const days = {}, put = (n, x) => { (days[dd(n)] = days[dd(n)] || { items: [] }).items.push(x); };
      [1, 2, 3, 4].forEach(n => put(n, it('Банан', 120, 'snack', 89, 'bn' + n)));            // 4 раза, свежее — перекус
      [1, 2].forEach(n => put(n, it('Творог 5%', 200, 'breakfast', 121, 'tv' + n))); put(3, it('Творог 5%', 150, 'breakfast', 121, 'tv3')); // 3 раза, обычно 200 г — завтрак
      [45, 46, 47, 48, 49].forEach(n => put(n, it('Гречка варёная', 250, 'lunch', 110, 'gr' + n))); // 5 раз, но давно — обед
      put(2, it('Чай с сахаром', 250, 'snack', 30, 'ch'));                                      // один раз — не «часто»
      [61, 62, 63, 64, 65, 66].forEach(n => put(n, it('Рис варёный', 200, 'lunch', 116, 'rs' + n))); // старше 60 дней — не считается
      const base = { goals: { train: { kcal: 3400, p: 130, f: 105, c: 485 }, rest: { kcal: 2800, p: 125, f: 95, c: 360 } }, trainDays: [0, 1, 2, 5], days, custom: [], recent: [], weights: {} };
      const { ctx, page } = await newPage(browser, { seed: base });
      const favNames = () => page.$$eval('#fav [data-fk]', els => els.map(e => e.querySelector('.n').textContent.trim()));
      await page.click('#addTab'); await sleep(700); // 12:00 — «Обед»
      let fv = await favNames();
      ok(fv.length === 3 && !fv.includes('Чай с сахаром') && !fv.includes('Рис варёный'), '«Часто»: только ≥ 2 раз и за 60 дней', fv.join(', '));
      ok(fv[0] === 'Банан' && fv[1] === 'Творог 5%' && fv[2] === 'Гречка варёная', '«Часто» в обеде: свежее и чаще — выше давнего (банан, творог, гречка)', fv.join(', '));
      await page.click('#mseg [data-m=breakfast]'); await sleep(600);
      fv = await favNames();
      ok(fv[0] === 'Творог 5%', '«Часто» в завтраке: первым то, что ем на завтрак', fv.join(', '));
      ok((await text(page, '#fav [data-fk] >> nth=0')).includes('200 г'), 'порция — та, что ставлю обычно (200 г, а не 150)', await text(page, '#fav [data-fk] >> nth=0'));
      // закреп удержанием (правой кнопкой / долгим нажатием) — первым, с булавкой; сохраняется
      await page.dispatchEvent('#fav [data-fk="гречка вареная"]', 'contextmenu'); await sleep(700);
      fv = await favNames();
      ok(fv[0] === 'Гречка варёная' && await page.$('#fav [data-fk="гречка вареная"] .pinm'), 'закреп — первым и с булавкой', fv.join(', '));
      ok((await state(page)).pins.length === 1 && (await state(page)).pins[0].g === 250, 'закреп сохранён с обычной порцией', JSON.stringify((await state(page)).pins));
      ok((await toastText(page)).includes('Закреплено'), 'тост «Закреплено наверху»');
      // «+» — сразу в дневник с обычной порцией; лист открыт, кнопка — галочка; «Отменить» убирает
      await page.click('#fav [data-fk="творог 5%"] [data-fa]'); await sleep(500);
      let its = await items(page);
      ok(its.length === 1 && its[0].name === 'Творог 5%' && its[0].g === 200 && its[0].meal === 'breakfast' && Math.round(its[0].kcal) === 242, '«+» — творог 200 г в завтрак одним нажатием', JSON.stringify(its));
      ok(await page.isVisible('#sheet.on') && await page.$eval('#fav [data-fk="творог 5%"] [data-fa]', b => b.classList.contains('done')), 'лист не закрылся, «+» стал галочкой');
      await page.click('#toast button'); await sleep(400);
      ok((await items(page)).length === 0, '«Отменить» — убрано');
      // закреп кнопкой в листе порции
      await page.click('#fav [data-fk="банан"] [data-fo]'); await sleep(600);
      ok((await page.inputValue('#g')) === '120', 'нажатие на строку — лист порции с обычными граммами', await page.inputValue('#g'));
      await page.click('#pinB'); await sleep(300);
      ok((await page.getAttribute('#pinB', 'aria-pressed')) === 'true' && (await state(page)).pins.length === 2, 'кнопка «Закрепить наверху» в листе порции');
      await page.click('#backBtn'); await sleep(700);
      fv = await favNames();
      ok(fv[0] === 'Гречка варёная' && fv[1] === 'Банан' && fv[2] === 'Творог 5%', 'закреплённые — первыми, по порядку закрепления', fv.join(', '));
      // открепить
      await sleep(400); await page.dispatchEvent('#fav [data-fk="гречка вареная"]', 'contextmenu'); await sleep(700);
      fv = await favNames();
      ok(fv[0] === 'Банан' && (await state(page)).pins.length === 1 && !(await page.$('#fav [data-fk="гречка вареная"] .pinm')), 'повторное удержание — откреплено', fv.join(', '));
      // закрепить блюдо из базы (не из «Часто»)
      await sleep(400); await page.dispatchEvent('#results [data-k="d0"]', 'contextmenu'); await sleep(700);
      fv = await favNames();
      ok(fv[1] === 'Каша овсяная на молоке', 'удержание блюда из базы — тоже закреп', fv.join(', '));
      await page.click('#sheetClose'); await sleep(600);
      // капсулы «Часто» в приёмах пищи на «Сегодня»
      ok(await page.locator('.sec [data-fq="творог 5%"]').count() === 1 && await page.locator('.sec [data-fq="банан"][data-meal="snack"]').count() === 1, 'на «Сегодня»: творог — в завтраке, банан — в перекусе');
      ok(await page.locator('[data-fq][data-meal="lunch"]').count() === 1 && await page.locator('[data-fq="банан"][data-meal="lunch"]').count() === 0, 'в обеде — только то, что ем в обед');
      await page.click('.sec [data-fq="банан"]'); await sleep(700);
      its = await items(page);
      ok(its.length === 1 && its[0].name === 'Банан' && its[0].meal === 'snack' && its[0].g === 120, 'капсула — банан 120 г в перекус', JSON.stringify(its));
      ok(await page.$eval('.sec [data-fq="банан"]', b => b.classList.contains('on')), 'капсула отмечена галочкой');
      await page.click('.sec [data-fq="банан"]'); await sleep(400);
      ok((await items(page)).length === 1 && (await toastText(page)).includes('Уже'), 'повтор — «Уже в «Перекус»», без дубля', await toastText(page));
      await page.reload(); await sleep(700);
      ok((await state(page)).pins.length === 2, 'закрепы сохраняются после перезагрузки');
      // не больше 6 закрепов
      await page.evaluate(() => { const s = JSON.parse(localStorage.getItem('kcal.v1')); s.pins = Array.from({ length: 6 }, (_, i) => ({ name: 'Блюдо ' + i, g: 100, kcal: 100, p: 1, f: 1, c: 1, salt: 0 })); localStorage.setItem('kcal.v1', JSON.stringify(s)); });
      await page.reload(); await sleep(700);
      await page.click('#addTab'); await sleep(700);
      await page.dispatchEvent('#results [data-k="d1"]', 'contextmenu'); await sleep(500);
      ok((await state(page)).pins.length === 6 && (await toastText(page)).includes('до 6'), 'больше 6 закрепов нельзя — понятный тост', await toastText(page));
      await shot(page, '19-fav');
      ok(page.errors.length === 0, 'нет ошибок в консоли («Часто»)', page.errors.join(' | '));
      await ctx.close();
    }
    /* 20. «Кофемания» — меню ресторана с весом и КБЖУ на порцию (coffeemania.ru, 03.11.2026) */
    {
      const { ctx, page } = await newPage(browser);
      const cm = await page.evaluate(() => ({ n: CMF.length, secs: CM.filter(r => !Array.isArray(r)).length, bad: CMF.filter(f => !(f.kcal > 0 && f.unit && f.unit.g > 0 && f.p >= 0 && f.f >= 0 && f.c >= 0) || f.unit.scale).length,
        syr: CMF.find(f => /^Сырники/.test(f.name)) }));
      ok(cm.n >= 150 && cm.secs >= 8 && cm.bad === 0, `в базе «Кофемания»: ${cm.n} блюд в ${cm.secs} разделах, у всех вес и КБЖУ, порции не умножаются`, JSON.stringify(cm).slice(0, 200));
      await page.click('#addTab'); await sleep(700);
      ok(await page.isVisible('#cmOpen') && (await text(page, '#cmOpen')).includes(String(cm.n)), 'в «Добавить» — ряд «Кофемания» в «Рестораны»');
      for (const q of ['кофемания', 'Coffeemania', 'кофеман']) {
        await page.fill('#q', q); await sleep(400);
        const rs = await page.$$eval('#results [data-k] .m', els => els.map(e => e.textContent));
        ok(await page.isVisible('#cmOpen') && rs.length >= 30 && rs.every(t => t.includes('Кофемания')), `поиск «${q}» находит раздел и блюда Кофемании`, rs.length + ' ' + rs.slice(0, 2).join(' | '));
      }
      await page.fill('#q', 'сырники кофемания'); await sleep(400);
      ok((await text(page, '#results [data-k] >> nth=0')).includes('Сырники') && (await text(page, '#results [data-k] >> nth=0')).includes('Кофемания'), '«сырники кофемания» — сырники из Кофемании', await text(page, '#results [data-k] >> nth=0'));
      await page.fill('#q', ''); await sleep(300);
      await page.click('#cmOpen'); await sleep(700);
      ok((await text(page, '#sheetTitle')) === 'Кофемания' && (await page.locator('#cmList .group-t').count()) === cm.secs && (await page.locator('#cmList [data-k]').count()) === cm.n, 'лист «Кофемания» — все блюда по разделам меню');
      await shot(page, '20-coffeemania');
      // блюдо — порция как в меню, калории как в меню
      const f0 = await page.evaluate(() => { const f = CMF[0]; return { name: f.name, g: f.unit.g, kcal: f.kcal * f.unit.g / 100 }; });
      await page.click('#cmList [data-k="c0"]'); await sleep(600);
      ok((await page.inputValue('#g')) === String(f0.g), 'порция — вес из меню (без ×1,5)', await page.inputValue('#g') + ' / ' + f0.g);
      await page.click('#ok'); await sleep(700);
      const its = await items(page);
      ok(its.length === 1 && its[0].name === f0.name && Math.abs(its[0].kcal - f0.kcal) < 0.6, 'добавлено с калориями как в меню', JSON.stringify(its[0]));
      ok(page.errors.length === 0, 'нет ошибок в консоли (Кофемания)', page.errors.join(' | '));
      await ctx.close();
    }
    /* 18б. Быстрый завтрак внутри Формы (?embed=1) */
    {
      const { ctx, page } = await newPage(browser, { url: URL + '?embed=1&theme=dark' });
      ok(await page.locator('.sec [data-qb]').count() === 4, 'внутри Формы — быстрые кнопки есть');
      await page.click('.sec [data-qb="millet"]'); await sleep(650);
      ok((await items(page)).length === 2, 'внутри Формы — одно нажатие → каша + авокадо');
      await shot(page, '18-embed');
      ok(page.errors.length === 0, 'нет ошибок в консоли (embed, быстрый завтрак)', page.errors.join(' | '));
      await ctx.close();
    }
    /* 21. «Мои рецепты» (Глеб 03.11): текст → предпросмотр (сервер подменён) → правка → сохранить → поиск, «Мои рецепты»,
       одно нажатие, «Часто», правка пересчитывает (записанное не меняется), удаление с подтверждением, sync с u */
    {
      const parses = [];
      const { ctx, page } = await newPage(browser, { init: () => localStorage.setItem('kcal.token', 'tokrec') });
      await ctx.route(API + '/api/kcal/recipe/parse', r => { parses.push({ body: JSON.parse(r.request().postData()), auth: r.request().headers().authorization });
        return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(RECIPE_PARSE) }); });
      await page.click('#addTab'); await sleep(550);
      ok(await page.isVisible('#recipeCta') && (await text(page, '#recipeCta')).includes('Мой рецепт'), 'в «Добавить» — кнопка «Мой рецепт»');
      ok((await text(page, '#recs')) === '', 'рецептов нет — блока «Мои рецепты» нет');
      await page.fill('#q', 'сыр'); await sleep(150);
      ok(!(await page.isVisible('#recipeCta')), 'при поиске кнопка рецепта прячется');
      await page.fill('#q', ''); await sleep(150);
      await page.click('#recipeCta'); await sleep(650);
      ok((await text(page, '#sheetTitle')) === 'Мой рецепт' && await page.isVisible('#mic') && await page.isVisible('#recText'), 'лист «Мой рецепт»: микрофон и поле для текста');
      ok((await text(page, '#recSt')).includes('Нажми и говори'), 'подсказка у микрофона', await text(page, '#recSt'));
      const micBox = await page.locator('#mic').boundingBox();
      ok(micBox.width >= 88 && micBox.height >= 88, 'кнопка микрофона большая', JSON.stringify(micBox));
      await shot(page, '21-recipe-new');
      await page.click('#recGo'); await sleep(200);
      ok(parses.length === 0 && (await toastText(page)).includes('Напиши'), 'пустой текст — подсказка, на сервер не уходит');
      await page.fill('#recText', RECIPE_PARSE.text); await page.click('#recGo'); await sleep(800);
      ok(parses.length === 1 && parses[0].body.text === RECIPE_PARSE.text && parses[0].auth === 'Bearer tokrec' && !parses[0].body.audio, 'текст → /api/kcal/recipe/parse с токеном', JSON.stringify(parses[0]));
      ok((await text(page, '#sheetTitle')) === 'Новый рецепт' && (await page.inputValue('#rn')) === 'Сырники', 'предпросмотр: название', await text(page, '#sheetTitle'));
      ok((await page.locator('#ings .ing').count()) === 4 && (await page.locator('#ings .est').count()) === 3, 'предпросмотр: 4 продукта, оценённые граммы — с «≈»');
      ok((await text(page, '.rec-said')).includes('пачка творога'), 'видно, что сказал');
      let rows = await rsum(page);
      ok(rows.length === 3 && rows[0][0] === 'Всё блюдо402 г' && digits(rows[0][1]) === 823 && rows[2][0] === 'Порция201 г' && digits(rows[2][1]) === 412, 'итог: всё блюдо 402 г — 823 ккал, порция 201 г — 412', JSON.stringify(rows));
      ok(digits(rows[1][1]) === 205 && rows[1][5] === '0,1', 'на 100 г готового — 205 ккал, соль 0,1', JSON.stringify(rows[1]));
      ok(((await state(page)).recipes || []).length === 0 && (await items(page)).length === 0, 'предпросмотр ничего не сохраняет');
      await shot(page, '21-recipe-preview');
      // правка граммов, удаление строки, порции, вес готового — пересчёт сразу
      await page.fill('#ig0', '300'); await sleep(120);
      rows = await rsum(page);
      ok(digits(rows[0][1]) === 944 && (await page.locator('#ings [data-i="0"] .est').count()) === 0, 'творог 200 → 300 г: 944 ккал', JSON.stringify(rows[0]));
      await page.click('#ings [data-i="1"] [data-d="1"]'); await sleep(120);
      ok((await page.inputValue('#ig1')) === '120' && digits((await rsum(page))[0][1]) === 960, 'яйцо +10 г кнопкой → 120 г, 960 ккал', await page.inputValue('#ig1'));
      await page.click('#ings [data-x="3"]'); await sleep(150);
      rows = await rsum(page);
      ok((await page.locator('#ings .ing').count()) === 3 && digits(rows[0][1]) === 807, 'масло убрано: 807 ккал', JSON.stringify(rows[0]));
      await page.click('[data-sv="1"]'); await sleep(100);
      ok((await text(page, '#rsv')) === '3' && (await rsum(page))[2][0] === 'Порция165 г', '3 порции → порция 165 г', JSON.stringify(await rsum(page)));
      await page.fill('#rout', '450'); await sleep(100);
      rows = await rsum(page);
      ok(rows[0][0] === 'Всё блюдо450 г' && rows[2][0] === 'Порция150 г' && digits(rows[1][1]) === 179, 'вес готового 450 г: на 100 г — 179 ккал, порция 150 г', JSON.stringify(rows));
      // свой продукт через поиск по базе
      await page.fill('#rn', 'Сырники Глеба');
      await page.click('#ingAdd'); await sleep(600);
      ok((await text(page, '#sheetTitle')) === 'Продукт', '«Добавить продукт» — поиск по базе');
      await page.fill('#iq', 'сметана'); await sleep(400);
      const sm = await text(page, '#ires [data-p] >> nth=0');
      await page.click('#ires [data-p] >> nth=0'); await sleep(650);
      ok((await text(page, '#sheetTitle')) === 'Новый рецепт' && (await page.locator('#ings .ing').count()) === 4 && (await page.inputValue('#rn')) === 'Сырники Глеба'
        && (await page.inputValue('#rout')) === '450' && (await text(page, '#rsv')) === '3', 'продукт добавлен, правки не потерялись', sm);
      // своё — название и КБЖУ
      await page.click('#ingAdd'); await sleep(600);
      await page.fill('#inN', 'Изюм'); await page.fill('#inG', '30'); await page.fill('#inK', '300'); await page.click('#inOk'); await sleep(650);
      ok((await page.locator('#ings .ing').count()) === 5 && (await text(page, '#ings [data-i="4"]')).includes('Изюм') && (await page.inputValue('#ig4')) === '30', 'своё: изюм 30 г');
      await page.click('#ings [data-x="3"]'); await sleep(120); // сметану обратно
      // сохранить
      await page.click('#rsave'); await sleep(800);
      let S1 = await state(page), R = S1.recipes[0];
      ok(S1.recipes.length === 1 && R.name === 'Сырники Глеба' && R.servings === 3 && R.out === 450 && R.ings.length === 4 && R.u > 0 && R.text === RECIPE_PARSE.text,
        'рецепт сохранён на устройстве', JSON.stringify(R));
      ok(R.ings[0].g === 300 && R.ings[0].est === false && R.ings[2].est === true && R.ings[3].name === 'Изюм' && Math.round(R.ings[3].kcal) === 90 && !('k' in R.ings[0]), 'продукты с граммами и «оценкой»', JSON.stringify(R.ings));
      ok((await text(page, '#sheetTitle')) === 'Добавить' && (await text(page, '#recs')).includes('Сырники Глеба') && (await toastText(page)).includes('в моих рецептах'), 'после сохранения — в «Мои рецепты» наверху «Добавить»');
      await shot(page, '21-recipe-in-add');
      await sleep(2300);
      const sent = ctx.syncs.map(x => x.body.recipes).filter(Boolean).pop();
      ok(sent && sent.length === 1 && sent[0].id === R.id && sent[0].u === R.u && sent[0].ings.length === 4 && !('k' in sent[0].ings[0]), 'рецепт ушёл на сервер в sync (с u)', JSON.stringify(ctx.syncs.map(x => x.body.recipes)));
      ok((await state(page)).recU === R.u, 'recU — принятое сервером u', (await state(page)).recU);
      // поиск: как обычное блюдо, порция — одна порция
      await page.fill('#q', 'сырники гл'); await sleep(300);
      const r0 = await text(page, '#results [data-k] >> nth=0');
      ok(r0.includes('Сырники Глеба') && r0.includes('Мой рецепт') && r0.includes('порция 150 г'), 'рецепт находится поиском', r0);
      await page.click('#results [data-k] >> nth=0'); await sleep(550);
      ok((await page.inputValue('#g')) === '150' && (await text(page, '#units')).includes('всё блюдо · 450 г') && await page.isVisible('#recOpen'), 'порция рецепта: 150 г, чип «всё блюдо», кнопка «Рецепт»', await text(page, '#units'));
      const perServ = await page.evaluate(() => { const f = recFood(recLive()[0]); return f.kcal * 1.5; });
      await page.click('#ok'); await sleep(700);
      let its = await items(page);
      ok(its.length === 1 && its[0].name === 'Сырники Глеба' && its[0].g === 150 && Math.abs(its[0].kcal - perServ) < 0.01 && its[0].per100 && its[0].unit.g === 150, 'записан в дневник: 150 г', JSON.stringify(its[0]));
      // одно нажатие из «Мои рецепты»
      await page.click('#addTab'); await sleep(550);
      ok((await text(page, '#recs [data-ro="0"]')).includes('150 г'), 'в «Мои рецепты» — привычная порция 150 г', await text(page, '#recs'));
      await page.click('#recs [data-ra="0"]'); await sleep(400);
      its = await items(page);
      ok(its.length === 2 && its[1].g === 150 && await page.isVisible('#q') && (await toastText(page)).includes('Сырники Глеба'), '«+» — в дневник в одно нажатие, лист не закрылся', JSON.stringify(its.map(i => i.g)));
      await page.click('#sheetClose'); await sleep(600);
      // «Часто»: добавлен 2 раза — там, а в «Мои рецепты» не дублируется
      await page.click('#addTab'); await sleep(600);
      ok((await text(page, '#fav')).includes('Сырники Глеба') && !(await page.locator('#recs [data-ro]').count()) && (await text(page, '#recAll')).includes('1'), 'рецепт в «Часто», в «Мои рецепты» — «Все · 1»', await text(page, '#recs'));
      // правка: граммы → пересчёт, записанное не меняется
      const before = (await items(page)).map(i => i.kcal);
      await page.click('#recAll'); await sleep(600);
      ok((await text(page, '#sheetTitle')) === 'Мои рецепты' && (await page.locator('#recList [data-rid]').count()) === 1 && await page.isVisible('#recNew'), 'лист «Мои рецепты»: список и «Новый рецепт»');
      await shot(page, '21-recipes');
      await page.click('#recList [data-rid] >> nth=0'); await sleep(600);
      ok((await text(page, '#sheetTitle')) === 'Рецепт' && (await page.inputValue('#ig0')) === '300' && await page.isVisible('#rdel'), 'рецепт открывается для правки');
      await page.fill('#ig0', '200'); await page.fill('#rn', 'Сырники'); await page.click('#rsave'); await sleep(700);
      S1 = await state(page);
      const R2 = S1.recipes[0];
      ok(R2.id === R.id && R2.name === 'Сырники' && R2.ings[0].g === 200 && Math.round(R2.ings[0].kcal) === 242 && R2.u > R.u, 'правка: творог 200 г, новое имя, u больше', JSON.stringify(R2));
      ok(JSON.stringify((await items(page)).map(i => i.kcal)) === JSON.stringify(before) && (await items(page))[0].name === 'Сырники Глеба', 'записанное в дневник не изменилось');
      // удаление — два нажатия, «Вернуть»
      await page.click('#recList [data-rid] >> nth=0'); await sleep(600);
      await page.click('#rdel'); await sleep(150);
      ok((await text(page, '#rdel')).includes('ещё раз') && !(await state(page)).recipes[0].del, 'первое нажатие «Удалить» — только подтверждение');
      await page.click('#rdel'); await sleep(500);
      S1 = await state(page);
      ok(S1.recipes.length === 1 && S1.recipes[0].del === true && S1.recipes[0].u > R2.u && (await text(page, '#sheetTitle')) === 'Мои рецепты' && (await toastText(page)).includes('Рецепт удалён'), 'удалён: «надгробие» с новым u', JSON.stringify(S1.recipes));
      await page.click('#toast button'); await sleep(400);
      S1 = await state(page);
      ok(!S1.recipes[0].del && S1.recipes[0].name === 'Сырники' && (await page.locator('#recList [data-rid]').count()) === 1, '«Вернуть» — рецепт снова есть');
      await page.click('#recList [data-rid] >> nth=0'); await sleep(600); await page.click('#rdel'); await page.click('#rdel'); await sleep(500);
      await page.click('#sheetClose'); await sleep(600);
      await page.click('#addTab'); await sleep(550); await page.fill('#q', 'сырники'); await sleep(300);
      ok(!(await text(page, '#results')).includes('Мой рецепт') && (await items(page)).length === 2, 'удалённый не ищется, дневник цел');
      await sleep(2300);
      const last = ctx.syncs.map(x => x.body.recipes).filter(Boolean).pop();
      ok(last && last.some(x => x.id === R.id && x.del === true), 'удаление ушло на сервер', JSON.stringify(last));
      ok(page.errors.length === 0, 'нет ошибок в консоли (рецепты)', page.errors.join(' | '));
      await ctx.close();
    }
    /* 21а. Рецепты на любом устройстве: слияние со state по u, удалённое на другом — удаляется, rstale → state заново */
    {
      const ing = (name, g, kcal) => ({ name, g, kcal, p: 1, f: 1, c: 1, salt: 0, est: false });
      const seed = { goals: {}, days: {}, fullSync: true, recipes: [
        { id: 'rA', u: 1000, name: 'Плов', servings: 4, out: 1600, ings: [ing('Рис', 400, 1400), ing('Курица', 600, 1200)] },
        { id: 'rB', u: 5000, name: 'Омлет', servings: 1, out: 0, ings: [ing('Яйцо', 165, 257)] },
        { id: 'rC', u: 1000, name: 'Блины', servings: 0, out: 0, ings: [ing('Мука', 200, 680)] }] };
      const st = { days: {}, recipes: [
        { id: 'rA', u: 2000, name: 'Плов с курицей', servings: 4, out: 1600, ings: [ing('Рис', 400, 1400), ing('Курица', 600, 1200)] },
        { id: 'rB', u: 4000, name: 'Старый омлет', servings: 1, out: 0, ings: [ing('Яйцо', 110, 172)] },
        { id: 'rC', u: 3000, del: true }, { id: 'rD', u: 3000, name: 'Гречка с грибами', servings: 2, out: 0, ings: [ing('Гречка', 150, 500)] },
        { id: 'rE', u: 3000, del: true }, { id: '<x>', u: 1, name: '<img src=x onerror=alert(1)>', ings: [ing('Яд', 1, 1)] }] };
      const { ctx, page } = await newPage(browser, { seed, state: st, init: () => localStorage.setItem('kcal.token', 'tokm'),
        sync: r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, forma: null, stale: [], rstale: ['rB'] }) }) });
      await sleep(900);
      const S2 = await state(page), by = id => S2.recipes.find(x => x.id === id);
      ok(by('rA').name === 'Плов с курицей' && by('rA').u === 2000, 'сервер новее — его версия');
      ok(by('rB').name === 'Омлет' && by('rB').u === 5000, 'своя новее — остаётся своя');
      ok(by('rC').del === true && by('rD').name === 'Гречка с грибами' && !by('rE'), 'удалённое на другом устройстве — удалено, нового — добавлено, чужое «надгробие» — не нужно', JSON.stringify(S2.recipes.map(x => x.id + ':' + (x.del ? 'del' : x.name))));
      ok(by('x') && by('x').name.startsWith('<img') && S2.recipes.length === 5, 'id без спецсимволов', JSON.stringify(S2.recipes.map(x => x.id)));
      await page.click('#addTab'); await sleep(550);
      ok((await page.locator('#recs [data-ro]').count()) === 3 && !(await page.evaluate(() => document.querySelector('#recs img'))), 'в «Мои рецепты» — 3 из 4, текст экранирован');
      await sleep(2000);
      ok(ctx.states >= 2, 'rstale в ответе sync → снова state', ctx.states);
      ok(page.errors.length === 0, 'нет ошибок в консоли (слияние рецептов)', page.errors.join(' | '));
      await ctx.close();
    }
    /* 21б. Голос: подставной MediaRecorder (audio/mp4 как в Safari), нажать-начать/нажать-стоп, удержание, отказ, «Слушаю…» → «Считаю…» */
    {
      const parses = []; let mode = 'ok';
      const { ctx, page } = await newPage(browser, { init: () => {
        localStorage.setItem('kcal.token', 'tokv');
        window.__mic = { calls: 0, deny: false, stopped: 0 };
        navigator.mediaDevices.getUserMedia = async () => { __mic.calls++; if (__mic.deny) { const e = new Error('нет'); e.name = 'NotAllowedError'; throw e; }
          return { getTracks: () => [{ stop() { __mic.stopped++; } }] }; };
        window.MediaRecorder = class { constructor(s, o) { this.mimeType = (o && o.mimeType) || ''; this.state = 'inactive'; __mic.opts = o; }
          static isTypeSupported(t) { return t === 'audio/mp4'; }
          start() { this.state = 'recording'; }
          stop() { this.state = 'inactive'; setTimeout(() => { this.ondataavailable && this.ondataavailable({ data: new Blob([new Uint8Array(3000).fill(7)], { type: 'audio/mp4' }) }); this.onstop && this.onstop(); }, 20); } };
      } });
      await ctx.route(API + '/api/kcal/recipe/parse', async r => {
        parses.push(JSON.parse(r.request().postData()));
        if (mode === 'slow') await sleep(3300);
        if (mode === 'novoice') return r.fulfill({ status: 422, contentType: 'application/json', body: JSON.stringify({ error: 'Голос не распознан — напиши текстом', voice: false }) });
        return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(RECIPE_PARSE) });
      });
      await page.click('#addTab'); await sleep(550); await page.click('#recipeCta'); await sleep(650);
      await page.click('#mic'); await sleep(1300);
      ok((await text(page, '#recSt')).startsWith('Слушаю · 0:01') && await page.evaluate(() => document.querySelector('#mic').classList.contains('on')) && await page.isVisible('#recCancel'),
        'нажал — идёт запись, таймер', await text(page, '#recSt'));
      ok(await page.evaluate(() => __mic.opts && __mic.opts.mimeType === 'audio/mp4'), 'формат — audio/mp4 (как в Safari)', JSON.stringify(await page.evaluate(() => __mic.opts)));
      await shot(page, '21-recipe-listening');
      await page.click('#mic'); await sleep(900);
      ok(parses.length === 1 && parses[0].audio.startsWith('data:audio/mp4;base64,') && parses[0].mime === 'audio/mp4' && !parses[0].text, 'нажал ещё раз — запись ушла на сервер', JSON.stringify(parses[0]).slice(0, 120));
      ok(await page.evaluate(() => __mic.stopped) === 1, 'микрофон выключен после записи');
      ok((await text(page, '#sheetTitle')) === 'Новый рецепт' && (await page.locator('#ings .ing').count()) === 4, 'голос → предпросмотр');
      // удержание: держал ~1 с и отпустил — стоп
      await page.click('#backBtn'); await sleep(650);
      ok((await page.inputValue('#recText')) === RECIPE_PARSE.text, '«Назад» — расшифровка в поле, можно поправить');
      mode = 'slow';
      const mb = await page.locator('#mic').boundingBox();
      await page.mouse.move(mb.x + mb.width / 2, mb.y + mb.height / 2); await page.mouse.down(); await sleep(1300); await page.mouse.up(); await sleep(500);
      ok(parses.length === 2 && parses[1].audio, 'удержание: отпустил — запись ушла');
      ok((await text(page, '#recSt')).startsWith('Слушаю…') && await page.evaluate(() => document.querySelector('#mic').disabled), 'пока ждём сервер — «Слушаю…»', await text(page, '#recSt'));
      await sleep(2300);
      ok((await text(page, '#recSt')).startsWith('Считаю…'), 'потом — «Считаю…»', await text(page, '#recSt'));
      await sleep(1200);
      ok((await text(page, '#sheetTitle')) === 'Новый рецепт', 'дождались — предпросмотр');
      // голос не распознан → понятная строка, без всплывающих окон
      mode = 'novoice';
      await page.click('#backBtn'); await sleep(650);
      await page.click('#mic'); await sleep(1200); await page.click('#mic'); await sleep(700);
      ok((await text(page, '#recSt')).includes('Голос не распознан') && (await text(page, '#recSt')).includes('текстом') && (await text(page, '#sheetTitle')) === 'Мой рецепт', 'голос не распознан — «напиши текстом»', await text(page, '#recSt'));
      // отказ в доступе к микрофону
      await page.evaluate(() => { __mic.deny = true; });
      await page.click('#mic'); await sleep(400);
      ok((await text(page, '#recSt')).includes('Нет доступа к микрофону') && (await text(page, '#recSt')).includes('текстом') && !(await page.evaluate(() => document.querySelector('#mic').classList.contains('on'))), 'отказ — понятный текст, можно писать', await text(page, '#recSt'));
      // слишком коротко; «Отменить запись»; закрыл лист во время записи — микрофон выключен
      await page.evaluate(() => { __mic.deny = false; });
      const n0 = parses.length;
      await page.click('#mic'); await sleep(300); await page.click('#mic'); await sleep(400);
      ok(parses.length === n0 && (await text(page, '#recSt')).includes('Слишком коротко'), 'меньше секунды — не отправляем', await text(page, '#recSt'));
      await page.click('#mic'); await sleep(1200); await page.click('#recCancel'); await sleep(400);
      ok(parses.length === n0 && (await text(page, '#recSt')).includes('Нажми и говори'), '«Отменить запись» — ничего не ушло');
      const st0 = await page.evaluate(() => __mic.stopped);
      await page.click('#mic'); await sleep(800); await page.keyboard.press('Escape'); await sleep(600);
      ok(await page.evaluate(() => __mic.stopped) === st0 + 1 && parses.length === n0 && await page.isHidden('#sheet'), 'закрыл лист во время записи — микрофон выключен, ничего не ушло');
      ok(page.errors.length === 0, 'нет ошибок в консоли (голос)', page.errors.join(' | '));
      await ctx.close();
    }
    /* 21в. Без MediaRecorder — только текст; внутри Формы (?embed=1) — кнопка есть, рецепт сохраняется */
    {
      const { ctx, page } = await newPage(browser, { init: () => { localStorage.setItem('kcal.token', 'tokn'); delete window.MediaRecorder; } });
      await page.click('#addTab'); await sleep(550); await page.click('#recipeCta'); await sleep(650);
      ok(!(await page.isVisible('#mic')) && (await text(page, '#recSt')).includes('Запись голоса в этом браузере не работает') && await page.isVisible('#recText'), 'нет записи в браузере — только текст');
      await ctx.close();
    }
    {
      const { ctx, page } = await newPage(browser, { url: URL + '?embed=1&theme=light', init: () => localStorage.setItem('kcal.token', 'tokemb') });
      await ctx.route(API + '/api/kcal/recipe/parse', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(RECIPE_PARSE) }));
      await page.click('#addTab'); await sleep(550);
      ok(await page.isVisible('#recipeCta'), 'внутри Формы — «Мой рецепт» есть');
      await page.click('#recipeCta'); await sleep(650); await page.fill('#recText', 'сырники'); await page.click('#recGo'); await sleep(700);
      await shot(page, '21-recipe-embed-light');
      await page.click('#rlog'); await sleep(650);
      ok((await text(page, '#sheetTitle')) === 'Порция' && (await page.inputValue('#g')) === '201', '«Сохранить и записать» → лист порции с одной порцией', await page.inputValue('#g'));
      await page.click('#ok'); await sleep(700);
      const S3 = await state(page);
      ok(S3.recipes.length === 1 && (await items(page)).length === 1 && (await items(page))[0].g === 201, 'внутри Формы: рецепт сохранён и записан');
      ok(page.errors.length === 0, 'нет ошибок в консоли (рецепт внутри Формы)', page.errors.join(' | '));
      await ctx.close();
    }
    /* 22. «Надиктовать» (Глеб 05.11): голос (подставной MediaRecorder) или текст → /api/kcal/voice (сервер подменён) → предпросмотр
       как у фото + вода → правка граммов, ×, снятие воды, приём пищи → «Записать» (еда и вода), «Отменить»; только вода; 422, сеть, 401 */
    {
      const voices = []; let mode = 'ok';
      const { ctx, page } = await newPage(browser, { init: () => {
        localStorage.setItem('kcal.token', 'tokvc');
        window.__mic = { calls: 0, stopped: 0 };
        navigator.mediaDevices.getUserMedia = async () => { __mic.calls++; return { getTracks: () => [{ stop() { __mic.stopped++; } }] }; };
        window.MediaRecorder = class { constructor(s, o) { this.mimeType = (o && o.mimeType) || ''; this.state = 'inactive'; }
          static isTypeSupported(t) { return t === 'audio/mp4'; }
          start() { this.state = 'recording'; }
          stop() { this.state = 'inactive'; setTimeout(() => { this.ondataavailable && this.ondataavailable({ data: new Blob([new Uint8Array(3000).fill(7)], { type: 'audio/mp4' }) }); this.onstop && this.onstop(); }, 20); } };
      } });
      await ctx.route(API + '/api/kcal/voice', async r => {
        voices.push({ body: JSON.parse(r.request().postData()), auth: r.request().headers().authorization });
        if (mode === 'slow') await sleep(3300);
        if (mode === 'novoice') return r.fulfill({ status: 422, contentType: 'application/json', body: JSON.stringify({ error: 'Голос не распознан — скажи ещё раз или напиши текстом', voice: false }) });
        if (mode === 'nofood') return r.fulfill({ status: 422, contentType: 'application/json', body: JSON.stringify({ error: 'Не услышал еды — скажи, что съел и сколько' }) });
        if (mode === 'net') return r.abort('failed');
        if (mode === '401') return r.fulfill({ status: 401, contentType: 'application/json', body: '{"error":"нужен вход"}' });
        if (mode === 'water') return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ text: 'Выпил пол-литра воды', items: [], water_ml: 500, comment: '' }) });
        if (voices[voices.length - 1].body.prev) return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ text: 'хлеба не было, котлет две', water_ml: 250, comment: '',
          fixes: ['Учёл: хлеба не было', 'Котлет две — 200 г'], items: [VOICE.items[0], { ...VOICE.items[1], g: 200, kcal: 460, p: 30, f: 32, c: 14, salt: 2.2, sure: 'mid' }] }) });
        return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(VOICE) });
      });
      await page.click('#addTab'); await sleep(550);
      const vb = await page.locator('#voiceCta').boundingBox();
      ok(await page.isVisible('#voiceCta') && (await text(page, '#voiceCta b')) === 'Надиктовать' && vb.height >= 36 && vb.width >= 100, 'в «Добавить» — кнопка «Надиктовать» (≥ 36 px)', JSON.stringify(vb));
      ok(await page.isVisible('#recipeCta') && Math.abs((await page.locator('#recipeCta').boundingBox()).y - vb.y) < 1, '«Мой рецепт» — рядом, в одном ряду');
      await shot(page, '22-voice-add');
      await page.fill('#q', 'сыр'); await sleep(150);
      ok(!(await page.isVisible('#voiceCta')), 'при поиске «Надиктовать» прячется');
      await page.fill('#q', ''); await sleep(150);
      await page.click('#voiceCta'); await sleep(650);
      ok((await text(page, '#sheetTitle')) === 'Надиктовать' && await page.isVisible('#mic') && await page.isVisible('#recText') && await page.isVisible('#backBtn'), 'лист «Надиктовать»: микрофон, поле текста, «Назад»');
      ok((await text(page, '#recSt')).includes('Нажми и говори'), 'подсказка у микрофона', await text(page, '#recSt'));
      await shot(page, '22-voice-new');
      await page.click('#recGo'); await sleep(200);
      ok(voices.length === 0 && (await toastText(page)).includes('Напиши'), 'пустой текст — подсказка, на сервер не уходит');
      // голос: нажал — запись — нажал → «Слушаю…» → «Считаю…» → предпросмотр
      await page.click('#mic'); await sleep(1300);
      ok((await text(page, '#recSt')).startsWith('Слушаю · 0:01') && await page.evaluate(() => document.querySelector('#mic').classList.contains('on')) && await page.isVisible('#recCancel'), 'нажал — идёт запись, таймер', await text(page, '#recSt'));
      await shot(page, '22-voice-listening');
      mode = 'slow';
      await page.click('#mic'); await sleep(350);
      ok((await text(page, '#recSt')).startsWith('Слушаю…') && await page.evaluate(() => __mic.stopped) === 1, 'отпустил — «Слушаю…», микрофон выключен', await text(page, '#recSt'));
      await sleep(2500);
      ok((await text(page, '#recSt')).startsWith('Считаю…'), 'потом — «Считаю…»', await text(page, '#recSt'));
      await sleep(1100);
      ok(voices.length === 1 && voices[0].body.audio.startsWith('data:audio/mp4;base64,') && voices[0].body.mime === 'audio/mp4' && voices[0].auth === 'Bearer tokvc' && !voices[0].body.text,
        'запись ушла на /api/kcal/voice с токеном', JSON.stringify(voices[0]).slice(0, 140));
      ok((await text(page, '#sheetTitle')) === 'Еда голосом' && (await page.locator('#found .vi').count()) === 3, 'предпросмотр: 3 позиции', await text(page, '#sheetTitle'));
      ok((await text(page, '.rec-said')).includes('тарелку гречки') && (await text(page, '.vnote')).includes('обычная тарелка'), '«Ты сказал» и комментарий', await text(page, '.rec-said'));
      ok((await text(page, '.vfix-t')) === 'Понял так' && (await page.$$eval('.vfix-i', e => e.map(x => x.textContent))).join('|') === '«греча» — гречка|Хлеб — кусок, около 30 г'
        && await page.isVisible('#vfixb') && (await text(page, '#vfixb')) === 'Поправить', '«Понял так» — что понял не дословно, и «Поправить»', await text(page, '.vfix'));
      ok((await page.inputValue('#pg0')) === '250' && (await text(page, '#pm0')).includes('275 ккал') && (await text(page, '#found [data-i="0"] .range')) === '≈ 200–300 г'
        && (await page.locator('#found [data-i="0"] .est').count()) === 1 && (await page.locator('#found [data-i="1"] .est').count()) === 0, 'граммы, ккал, «≈» и диапазон — как у фото', await text(page, '#found [data-i="0"]'));
      ok((await text(page, '#found [data-i="2"] .range')).includes('Проверь вес'), 'неуверенно — «Проверь вес»', await text(page, '#found [data-i="2"]'));
      ok((await text(page, '#vsum')).includes('Всего') && (await text(page, '#vsum')).includes('580'), 'итог: 580 ккал', await text(page, '#vsum'));
      ok((await text(page, '#vw')).includes('Вода +250 мл') && (await page.getAttribute('#vwt', 'aria-checked')) === 'true' && (await text(page, '#vwm')).includes('станет 0,3 из 3,5 л'), 'вода +250 мл — включена', await text(page, '#vw'));
      ok((await text(page, '#vlog')) === 'Записать в «Обед» и воду' && (await page.getAttribute('#mseg [data-m="lunch"]', 'aria-pressed')) === 'true', 'приём пищи — текущий (обед)', await text(page, '#vlog'));
      ok((await items(page)).length === 0 && !((await state(page)).days['2026-10-21'] || {}).water, 'предпросмотр ничего не записывает');
      const small = await page.$$eval('#sheetBody button, #sheetBody input', els => els.filter(e => e.offsetParent && (e.getBoundingClientRect().width < 36 || e.getBoundingClientRect().height < 36)).map(e => e.id || e.getAttribute('aria-label')));
      ok(small.length === 0, 'кнопки предпросмотра ≥ 36 px', JSON.stringify(small));
      await shot(page, '22-voice-preview');
      // правка граммов: −/+ и числом; ×; вода — снять
      await page.click('#found [data-i="0"] [data-d="10"]'); await sleep(120);
      ok((await page.inputValue('#pg0')) === '260' && (await text(page, '#pm0')).includes('286 ккал') && (await page.locator('#found [data-i="0"] .est, #found [data-i="0"] .range').count()) === 0, '+10 г кнопкой → 260 г, 286 ккал, «≈» снят', await text(page, '#pm0'));
      await page.fill('#pg1', '150'); await sleep(120);
      ok((await text(page, '#pm1')).includes('345 ккал') && (await text(page, '#vsum')).includes('706'), 'котлета числом 150 г → 345 ккал, итог 706', await text(page, '#vsum'));
      await page.click('#found [data-x="2"]'); await sleep(450);
      ok((await page.locator('#found .vi').count()) === 2 && (await text(page, '#vsum')).includes('631'), '× — хлеб убран, итог 631', await text(page, '#vsum'));
      await page.click('#vwt'); await sleep(150);
      ok((await page.getAttribute('#vwt', 'aria-checked')) === 'false' && (await text(page, '#vlog')) === 'Записать в «Обед»' && (await text(page, '#vwm')) === 'не записывать', 'переключатель — вода снята');
      await page.click('#vw .t'); await sleep(100);
      ok((await page.getAttribute('#vwt', 'aria-checked')) === 'true', 'нажатие по строке — вода снова включена');
      await page.click('#vwt'); await sleep(100);
      await page.click('#mseg [data-m="dinner"]'); await sleep(200);
      ok((await text(page, '#vlog')) === 'Записать в «Ужин»', 'выбран «Ужин»');
      await page.click('#vlog'); await sleep(700);
      let its = await items(page), S1 = await state(page);
      ok(its.length === 2 && its.every(i => i.meal === 'dinner') && its[0].name === 'Гречка отварная' && its[0].g === 260 && Math.round(its[0].kcal) === 286 && its[1].g === 150 && Math.round(its[1].kcal) === 345
        && Math.abs(its[0].per100.kcal - 110) < 1e-9, '«Записать» — еда в «Ужин» обычными записями (с составом на 100 г)', JSON.stringify(its));
      ok(!(S1.days['2026-10-21'].water || []).length, 'снятая вода не записана', JSON.stringify(S1.days['2026-10-21'].water));
      ok((await toastText(page)).includes('2 блюда · 631 ккал') && (await text(page, '#toast button')) === 'Отменить', 'тост «2 блюда · 631 ккал» с «Отменить»', await toastText(page));
      await page.click('#toast button'); await sleep(400);
      ok((await items(page)).length === 0, '«Отменить» — записи убраны');
      // текстом: Enter — отправить; еда и вода записываются вместе
      mode = 'ok';
      await page.click('#addTab'); await sleep(550); await page.click('#voiceCta'); await sleep(650);
      await page.fill('#recText', 'гречка с котлетой, хлеб и стакан воды'); await page.press('#recText', 'Enter'); await sleep(700);
      ok(voices.length === 2 && voices[1].body.text === 'гречка с котлетой, хлеб и стакан воды' && !voices[1].body.audio && (await text(page, '#sheetTitle')) === 'Еда голосом', 'текст + Enter → /api/kcal/voice {text} → предпросмотр', JSON.stringify(voices[1] && voices[1].body));
      await page.click('#backBtn'); await sleep(650);
      ok((await text(page, '#sheetTitle')) === 'Надиктовать' && (await page.inputValue('#recText')) === VOICE.text, '«Назад» — сказанное в поле, можно поправить', await page.inputValue('#recText'));
      await page.click('#recGo'); await sleep(700);
      await page.click('#vlog'); await sleep(700);
      its = await items(page); S1 = await state(page);
      ok(its.length === 3 && its.every(i => i.meal === 'lunch') && JSON.stringify(S1.days['2026-10-21'].water) === '[250]', '«Записать» — 3 позиции в «Обед» и вода 250 мл', JSON.stringify({ n: its.length, w: S1.days['2026-10-21'].water }));
      ok((await toastText(page)) === '3 блюда · 580 ккал · +250 млОтменить', 'тост «3 блюда · 580 ккал · +250 мл» — еда и вода', await toastText(page));
      ok((await text(page, '#water')).includes('0,3 из 3,5 л'), 'вода на «Сегодня» обновилась', await text(page, '#water'));
      // «Поправить» (Глеб 06.11: «умная, как ты»): к разбору — что не так → prev (как на экране) + поправка → весь список заново
      await page.click('#addTab'); await sleep(550); await page.click('#voiceCta'); await sleep(650);
      await page.fill('#recText', 'гречка с котлетой, хлеб и стакан воды'); await page.press('#recText', 'Enter'); await sleep(700);
      await page.click('#found [data-i="0"] [data-d="10"]'); await sleep(120);
      await page.click('#vwt'); await sleep(120); await page.click('#vwt'); await sleep(120); // вода снята и снова включена
      await page.click('#mseg [data-m="dinner"]'); await sleep(200);
      await page.click('#vfixb'); await sleep(650);
      ok((await text(page, '#sheetTitle')) === 'Поправить' && await page.isVisible('#mic') && await page.isVisible('#recText') && (await text(page, '#recGo')) === 'Пересчитать', 'лист «Поправить»: микрофон, поле, «Пересчитать»');
      await shot(page, '22-voice-fix');
      await page.click('#backBtn'); await sleep(650);
      ok((await text(page, '#sheetTitle')) === 'Еда голосом' && (await page.inputValue('#pg0')) === '260' && (await page.getAttribute('#mseg [data-m="dinner"]', 'aria-pressed')) === 'true'
        && (await page.locator('#found .vi').count()) === 3, '«Назад» из «Поправить» — предпросмотр как был (260 г, «Ужин»)', await page.inputValue('#pg0'));
      const nv = voices.length;
      await page.click('#vfixb'); await sleep(650);
      await page.fill('#recText', 'хлеба не было, котлет две'); await page.press('#recText', 'Enter'); await sleep(700);
      const pv = voices[nv] && voices[nv].body;
      ok(pv && pv.text === 'хлеба не было, котлет две' && pv.prev && pv.prev.text === VOICE.text && pv.prev.water_ml === 250 && pv.prev.items.length === 3
        && pv.prev.items[0].g === 260 && pv.prev.items[0].kcal === 286, 'ушло: поправка + разбор как на экране (гречка 260 г, вода 250)', JSON.stringify(pv).slice(0, 200));
      ok((await text(page, '#sheetTitle')) === 'Еда голосом' && (await page.locator('#found .vi').count()) === 2 && (await page.inputValue('#pg1')) === '200'
        && (await text(page, '.vsaid')).includes('Поправил«хлеба не было, котлет две»') && (await text(page, '.vfix')).includes('Учёл: хлеба не было')
        && (await page.getAttribute('#mseg [data-m="dinner"]', 'aria-pressed')) === 'true', 'новый список: 2 позиции, котлеты 200 г, «Поправил …», приём пищи сохранился', await text(page, '.vsaid'));
      await shot(page, '22-voice-fixed');
      await page.click('#vlog'); await sleep(700);
      its = await items(page);
      ok(its.length === 5 && its.slice(3).every(i => i.meal === 'dinner') && its[4].g === 200 && Math.round(its[4].kcal) === 460, '«Записать» после поправки — 2 позиции в «Ужин»', JSON.stringify(its.slice(3)));
      await page.click('#toast button'); await sleep(400);
      // только вода
      mode = 'water';
      await page.click('#addTab'); await sleep(550); await page.click('#voiceCta'); await sleep(650);
      await page.fill('#recText', 'выпил пол-литра воды'); await page.click('#recGo'); await sleep(700);
      ok(await page.isHidden('#vfood') && await page.isHidden('#mseg') && (await text(page, '#vlog')) === 'Записать воду · 500 мл', 'только вода — без еды и приёма пищи', await text(page, '#vlog'));
      await shot(page, '22-voice-water');
      await page.click('#vlog'); await sleep(700);
      ok(JSON.stringify((await state(page)).days['2026-10-21'].water) === '[250,500]' && (await items(page)).length === 3 && (await toastText(page)).startsWith('Вода +500 мл'), 'записана только вода', JSON.stringify((await state(page)).days['2026-10-21'].water));
      // ошибки — строкой в листе
      await page.click('#addTab'); await sleep(550); await page.click('#voiceCta'); await sleep(650);
      mode = 'novoice';
      await page.click('#mic'); await sleep(1200); await page.click('#mic'); await sleep(700);
      ok((await text(page, '#recSt')).includes('Не расслышал') && (await text(page, '#recSt')).includes('текстом') && (await text(page, '#sheetTitle')) === 'Надиктовать', '422 voice:false — «Не расслышал — напиши текстом»', await text(page, '#recSt'));
      mode = 'nofood';
      await page.fill('#recText', 'привет'); await page.press('#recText', 'Enter'); await sleep(600);
      ok((await text(page, '#recSt')).includes('Не услышал еды') && !(await page.evaluate(() => document.querySelector('#recText').disabled)), '422 без еды — понятная строка, можно поправить', await text(page, '#recSt'));
      mode = 'net';
      await page.click('#recGo'); await sleep(600);
      ok((await text(page, '#recSt')).includes('Нет связи с сервером') && !(await page.evaluate(() => document.querySelector('#mic').disabled)), 'нет сети — «Нет связи с сервером»', await text(page, '#recSt'));
      const st0 = await page.evaluate(() => __mic.stopped), n0 = voices.length;
      await page.click('#mic'); await sleep(800); await page.keyboard.press('Escape'); await sleep(600);
      ok(await page.evaluate(() => __mic.stopped) === st0 + 1 && voices.length === n0 && await page.isHidden('#sheet'), 'Esc во время записи — лист закрыт, микрофон выключен, ничего не ушло');
      mode = '401';
      await page.click('#addTab'); await sleep(550); await page.click('#voiceCta'); await sleep(650);
      await page.fill('#recText', 'банан'); await page.click('#recGo'); await sleep(700);
      ok(await page.evaluate(() => localStorage.getItem('kcal.token')) === null && await page.isVisible('#code') && (await text(page, '.portion .title')) === 'Еда голосом'
        && (await toastText(page)).includes('Связь с Формой потеряна'), '401 — «Связь с Формой потеряна» и экран кода', await toastText(page));
      ok(page.errors.length === 0, 'нет ошибок в консоли (надиктовать)', page.errors.join(' | '));
      await ctx.close();
    }
    /* 22а. Без связи с Формой — «Надиктовать» просит связать; узкий iPhone SE — всё влезает; внутри Формы (?embed=1) — записывает */
    {
      const { ctx, page } = await newPage(browser);
      await page.click('#addTab'); await sleep(550); await page.click('#voiceCta'); await sleep(650);
      ok(await page.isVisible('#code') && (await text(page, '.portion .title')) === 'Еда голосом' && (await text(page, '.lead')).includes('Голос разбирает'), 'без связи — экран кода «Еда голосом»');
      await ctx.close();
    }
    {
      const { ctx, page } = await newPage(browser, { device: 'iPhone SE', init: () => localStorage.setItem('kcal.token', 'tokse') });
      await ctx.route(API + '/api/kcal/voice', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(VOICE) }));
      const wide = () => page.evaluate(() => { const b = document.querySelector('#sheetBody'); return document.documentElement.scrollWidth <= innerWidth + 1 && b.scrollWidth <= b.clientWidth + 1; });
      await page.click('#addTab'); await sleep(550);
      ok(await wide() && (await page.$eval('#voiceCta b', b => b.scrollWidth <= b.clientWidth + 1)), 'iPhone SE: «Надиктовать» влезает целиком, без прокрутки вбок');
      await shot(page, '22-voice-add-se');
      await page.click('#voiceCta'); await sleep(650); await page.fill('#recText', 'гречка'); await page.click('#recGo'); await sleep(700);
      ok(await wide() && (await page.locator('#found .vi').count()) === 3, 'iPhone SE: предпросмотр без прокрутки вбок');
      await shot(page, '22-voice-preview-se');
      ok(page.errors.length === 0, 'нет ошибок в консоли (надиктовать, SE)', page.errors.join(' | '));
      await ctx.close();
    }
    {
      const { ctx, page } = await newPage(browser, { url: URL + '?embed=1&theme=light', init: () => localStorage.setItem('kcal.token', 'tokemb') });
      await ctx.route(API + '/api/kcal/voice', r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(VOICE) }));
      await page.click('#addTab'); await sleep(550);
      ok(await page.isVisible('#voiceCta'), 'внутри Формы — «Надиктовать» есть');
      await page.click('#voiceCta'); await sleep(650);
      await shot(page, '22-voice-new-embed-light');
      await page.fill('#recText', 'гречка с котлетой'); await page.click('#recGo'); await sleep(700);
      await shot(page, '22-voice-preview-embed-light');
      await page.click('#vlog'); await sleep(700);
      ok((await items(page)).length === 3 && JSON.stringify((await state(page)).days['2026-10-21'].water) === '[250]', 'внутри Формы: еда и вода записаны');
      ok(page.errors.length === 0, 'нет ошибок в консоли (надиктовать внутри Формы)', page.errors.join(' | '));
      await ctx.close();
    }
  } catch (e) {
    ok(false, 'тест упал', e.stack.split('\n').slice(0, 3).join(' '));
  }
  await browser.close();
  server.kill();
  console.log(results.join('\n'));
  console.log(failed ? `\nНЕ ПРОШЛО: ${failed} из ${results.length}` : `\nВсё прошло: ${results.length} проверок`);
  process.exit(failed ? 1 : 0);
})();
