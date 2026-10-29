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
const API = 'https://coach.2-27-206-165.sslip.io';
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
  const page = await ctx.newPage();
  page.errors = [];
  page.on('pageerror', e => page.errors.push(e.message));
  page.on('console', m => m.type() === 'error' && !/Failed to load resource/.test(m.text()) && page.errors.push(m.text()));
  page.on('dialog', d => d.accept());
  if (opts.seed) await ctx.addInitScript(s => { if (!sessionStorage.getItem('seeded')) { localStorage.setItem('kcal.v1', s); sessionStorage.setItem('seeded', 1); } }, JSON.stringify(opts.seed));
  await page.goto(URL);
  await sleep(700);
  return { ctx, page };
}
const state = page => page.evaluate(() => JSON.parse(localStorage.getItem('kcal.v1') || 'null'));
const text = (page, sel) => page.textContent(sel).then(t => t.replace(/\s+/g, ' ').trim());
const shot = async (page, name, full) => SHOTS && page.screenshot({ path: path.join(SHOTS, name + '.png'), fullPage: !!full });
const items = async page => (await state(page))?.days['2026-10-21']?.items || [];
const sum = (arr, k) => arr.reduce((a, i) => a + (i[k] || 0), 0);
const toastText = page => page.textContent('#toast');

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
      ok(/bba8ff|--s\b/.test(saltBar), 'соль 2,5 из 5 — обычный цвет', saltBar);
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
      ok((await text(page, '#water')).includes('0 из 3,4 л'), 'старая норма 3 л → расчёт 3,4 л в день тренировки', await text(page, '#water'));
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
      ok((await page.inputValue('#g')) === '300' && (await text(page, '#units')).includes('порция · 300 г'), 'пельмени: порция 250 г × 1,2 = 300 г', await page.inputValue('#g'));
      await page.click('#backBtn'); await sleep(400); await page.fill('#q', 'банан'); await sleep(300); await page.click('#results [data-k="s0"]'); await sleep(500);
      ok((await page.inputValue('#g')) === '120', 'банан — 1 шт как есть (штуки не увеличиваются)');
      await page.click('#sheetClose'); await sleep(500);
      await page.click('#gear'); await sleep(500); await page.click('#por [data-p="1"]'); await sleep(100);
      await page.fill('#w-train', '3500'); await page.press('#w-train', 'Tab'); await sleep(100);
      await page.fill('#w-rest', '100'); await page.press('#w-rest', 'Tab'); await sleep(150);
      ok((await toastText(page)).includes('От 500 до 8000'), 'норма воды 100 мл не принимается');
      await page.click('#sheetClose'); await sleep(600);
      ok((await state(page)).portion === 1 && (await state(page)).water.train === 3500 && (await state(page)).waterSet === true, 'настройки: порции как у всех, вода 3500 мл (своя)');
      await page.click('#addTab'); await sleep(500); await page.fill('#q', 'пельмени'); await sleep(300); await page.click('#results [data-k="s0"]'); await sleep(500);
      ok((await page.inputValue('#g')) === '250', '«Как у всех» — порция 250 г');
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
    /* 15. Чат с нутрициологом */
    {
      const { ctx, page } = await newPage(browser);
      let sent = null, mode = 'ok';
      await ctx.route(API + '/api/kcal/pair', r => r.fulfill({ status: 200, contentType: 'application/json', body: '{"token":"tok"}' }));
      await ctx.route(API + '/api/kcal/chat', async r => { sent = JSON.parse(r.request().postData()); await sleep(500);
        if (mode === 'err') return r.fulfill({ status: 503, contentType: 'application/json', body: '{"error":"у Claude закончился лимит, попробуй позже"}' });
        const done = { done: true, text: 'Добери белок:\n- творог 240 г\n- банан', items: [{ name: 'Творог 5%', g: 240, kcal: 290, p: 41, f: 12, c: 4, salt: 0.2 }, { name: 'Банан', g: 120, kcal: 114, p: 2, f: 1, c: 25, salt: 0 }] };
        if (!sent.stream) return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(done) });
        r.fulfill({ status: 200, contentType: 'text/event-stream', body: ['Добери ', 'белок:\n- творог 240 г\n', '- банан'].map(t => `data: ${JSON.stringify({ t })}\n\n`).join('') + `data: ${JSON.stringify(done)}\n\n` }); });
      await addFood(page, 'овсян хлоп', 100, 'breakfast');
      await page.click('[data-tab=chat]'); await sleep(600);
      ok((await text(page, '.large')) === 'Чат' && await page.isVisible('#composer') && await page.isVisible('#chatPair'), 'вкладка «Чат»: поле ввода и «Подключить»');
      await page.click('.starter >> nth=0'); await sleep(500);
      ok(await page.isVisible('#code'), 'без подключения — код');
      await page.fill('#code', '111111'); await sleep(1500);
      ok(sent && sent.stream === true, 'чат просит ответ потоком');
      ok(sent && sent.question === 'Что мне съесть сейчас?' && sent.context.left.kcal === 3048 && sent.context.meals[0].includes('Овсяные хлопья'), 'вопрос ушёл с дневником дня (осталось 3 048)', JSON.stringify(sent && sent.context.left));
      ok((await text(page, '#msgs')).includes('Добери белок') && (await page.locator('.msg.ai li').count()) === 2, 'ответ с пунктами');
      ok(!(await page.isVisible('#chatPair')), 'после подключения кнопки «Подключить» нет');
      await page.click('[data-m="1"][data-j="0"]'); await sleep(500);
      let it = await items(page);
      ok(it.length === 2 && it[1].name === 'Творог 5%' && it[1].g === 240 && Math.abs(it[1].per100.kcal - 290 / 2.4) < 1e-6, 'творог из совета — в дневник', JSON.stringify(it[1]));
      ok(await page.$eval('[data-m="1"][data-j="0"]', b => b.classList.contains('done')), 'добавленное отмечено галочкой');
      await page.click('[data-all="1"]'); await sleep(500);
      ok((await items(page)).length === 3, '«Добавить всё» — только то, чего ещё нет');
      await page.fill('#ask', 'есть рис и курица'); await page.press('#ask', 'Enter'); await sleep(200);
      ok(await page.isVisible('.typing'), 'пока думает — точки');
      await sleep(900);
      ok(sent.messages.length === 2 && sent.messages[0].role === 'user' && sent.messages[1].role === 'coach', 'история разговора уходит на сервер', JSON.stringify(sent.messages.map(m => m.role)));
      mode = 'err'; await page.fill('#ask', 'а ужин?'); await page.click('#send'); await sleep(900);
      ok((await text(page, '.chat-err')).includes('лимит'), 'ошибка сервера — понятный текст');
      mode = 'ok'; await page.click('#chatRetry'); await sleep(900);
      const st = await state(page);
      ok(!(await page.isVisible('.chat-err')) && st.chat.filter(m => m.text === 'а ужин?').length === 1 && st.chat[st.chat.length - 1].role === 'coach', '«Повторить» не дублирует вопрос');
      await page.reload(); await sleep(700); await page.click('[data-tab=chat]'); await sleep(500);
      ok((await page.locator('.msg').count()) === 6, 'переписка сохраняется', String(await page.locator('.msg').count()));
      await page.click('#clearChat'); await sleep(400);
      ok(await page.isVisible('.starter'), 'очистка переписки');
      await page.click('[data-tab=today]'); await sleep(500);
      ok(!(await page.isVisible('#composer')), 'на «Сегодня» поля чата нет');
      ok(page.errors.length === 0, 'нет ошибок в консоли (чат)', page.errors.join(' | '));
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
