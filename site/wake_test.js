// Проверка сайта «Будильник» (docs/wake) в Playwright: node site/wake_test.js [папка-для-скриншотов]
// Сервер Формы подменяется (page.route), на настоящий не ходит. Часы — page.clock, Москва; ночь 8→9 ноября 2026.
const { chromium, devices } = require('playwright');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

const ROOT = path.join(__dirname, '..', 'docs');
const PORT = +process.env.WAKE_PORT || 8791;
const PAGE = `http://localhost:${PORT}/wake/`;
const SHOTS = process.argv[2] || '';
const results = [];
let failed = 0;
function ok(cond, name, info = '') {
  results.push(`${cond ? '✓' : '✗'} ${name}${cond ? '' : '  → ' + info}`);
  if (!cond) failed++;
}
const at = s => new Date(`2026-11-0${s}+03:00`);  // at('8T21:30:00')
const FORMA = {
  now: '2026-11-08T21:30', tz: 180,
  plan: { day: '2026-11-09', wake: '06:45', bed: '22:00', mode: 'school' },
  need: 8.6, eff: 0.92, latency: 15, cycle: 94, nights: 11,
  last: { day: '2026-11-08', hours: 7.6, in_bed: 8.2, light: 3.9, deep: 1.6, rem: 2.1, cycles: 5, performance: 84, start: '23:10', end: '07:05' },
  recovery: { score: 71, hrv: 85, rhr: 58 }, armed: null,
};

async function server() {
  const p = spawn('python3', ['-m', 'http.server', String(PORT), '--bind', '127.0.0.1'], { cwd: ROOT, stdio: 'ignore' });
  for (let i = 0; i < 50; i++) {
    const up = await new Promise(res => require('http').get(PAGE, r => { r.resume(); res(r.statusCode === 200); }).on('error', () => res(false)));
    if (up) return p;
    await new Promise(r => setTimeout(r, 100));
  }
  throw new Error('сервер не поднялся');
}

async function page(browser, { token = 'tok_abcdefghijklmnop', time = at('8T21:30:00'), device = devices['iPhone 14'], reduced = false, cfg = null, api = {} } = {}) {
  const ctx = await browser.newContext({ ...device, timezoneId: 'Europe/Moscow', locale: 'ru-RU', reducedMotion: reduced ? 'reduce' : 'no-preference' });
  const pg = await ctx.newPage();
  const log = { errors: [], posts: [], gets: 0 };
  pg.on('pageerror', e => log.errors.push(String(e)));
  pg.on('console', m => { if (m.type() === 'error') log.errors.push(m.text()); });
  await pg.route('https://fonts.googleapis.com/**', r => r.fulfill({ status: 200, contentType: 'text/css', body: '' }));
  await pg.route('**/api/**', async r => {
    const u = new URL(r.request().url()), body = r.request().postData();
    if (r.request().method() === 'POST') log.posts.push({ path: u.pathname, body: body ? JSON.parse(body) : null, auth: r.request().headers().authorization });
    else log.gets++;
    const h = api[u.pathname];
    if (h) return h(r);
    if (u.pathname === '/api/wake') return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(FORMA) });
    return r.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' });
  });
  await pg.addInitScript(([tok, c]) => {
    if (sessionStorage.getItem('init')) return; sessionStorage.setItem('init', '1');  // перезагрузка — без сброса
    localStorage.clear(); if (tok) localStorage.setItem('kcal.token', tok);
    if (c) localStorage.setItem('wake.cfg', JSON.stringify(c));
  }, [token, cfg]);
  await pg.clock.install({ time });
  return { pg, ctx, log };
}
const txt = (pg, s) => pg.$eval(s, e => e.textContent.trim());
const shot = async (pg, name) => { if (SHOTS) await pg.screenshot({ path: path.join(SHOTS, name + '.png') }); };
async function motion(pg, v, n = 12) {
  await pg.evaluate(([v, n]) => {
    for (let i = 0; i < n; i++) {
      const e = new Event('devicemotion');
      Object.defineProperty(e, 'acceleration', { value: { x: v * (i % 2 ? 1 : -1), y: v * .3, z: 0 } });
      window.dispatchEvent(e);
    }
  }, [v, n]);
}

(async () => {
  if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });
  const srv = await server();
  const browser = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
  try {
    /* 1. Ядро */
    {
      const { pg, ctx, log } = await page(browser, { token: '' });
      await pg.goto(PAGE);
      const r = await pg.evaluate(() => {
        const { Core } = window.__wake, MIN = 60000, T = s => new Date(`2026-11-0${s}+03:00`).getTime(), H = Core.hm;
        const out = {};
        let p = Core.plan({ onset: T('8T21:20:00'), wakeAt: T('9T06:45:00'), win: 30, cycle: 90 });
        out.p1 = [p.onCycle, H(p.pred), p.k, H(p.from)];
        p = Core.plan({ onset: T('8T22:15:00'), wakeAt: T('9T06:45:00'), win: 20, cycle: 90 });
        out.p2 = [p.onCycle, H(p.pred), p.k];
        const b = Core.beds({ wakeAt: T('9T06:45:00'), win: 20, cycle: 94, latency: 15, need: 8.5 });
        out.b = b.filter(x => x.k >= 5 && x.k <= 6).map(x => [x.k, H(x.bed), x.enough]);
        out.next = [H(Core.nextAt(T('8T21:30:00'), '06:45')), new Date(Core.nextAt(T('8T21:30:00'), '06:45')).getDate(),
          new Date(Core.nextAt(T('9T05:00:00'), '06:45')).getDate(), new Date(Core.nextAt(T('9T06:45:00'), '06:45')).getDate()];
        // ночь с датчиком: тихо до 06:30, движение до окна не будит, в окне — будит
        const n = new Core.Night({ start: T('8T22:00:00'), wakeAt: T('9T06:45:00'), win: 20, cycle: 90, latency: 15 });
        for (let t = T('8T22:00:30'); t <= T('9T06:00:00'); t += 30000) n.add({ t, m: t === T('9T03:00:00') ? 2 : 0.2, s: null });
        out.onset = H(n.onset);
        out.before = n.decide(T('9T06:00:10'));
        for (let t = T('9T06:00:30'); t <= T('9T06:30:00'); t += 30000) n.add({ t, m: 0.2, s: null });
        out.quiet = n.decide(T('9T06:30:10'));
        n.add({ t: T('9T06:30:30'), m: 1.6, s: null });
        out.move = n.decide(T('9T06:30:31'));
        out.dead = n.decide(T('9T06:45:00'));
        const sm = n.summary(T('9T06:30:31'), 'move');
        out.sum = [sm.onset, sm.woke, sm.minutes, sm.moves, sm.light + sm.deep + sm.awake];
        // две средние подряд (0,8 + 0,7) — тоже лёгкий сон
        const n2 = new Core.Night({ start: T('8T22:00:00'), wakeAt: T('9T06:45:00'), win: 20, cycle: 90 });
        n2.add({ t: T('9T06:30:00'), m: 0.8, s: null }); n2.add({ t: T('9T06:30:30'), m: 0.7, s: null });
        out.two = n2.decide(T('9T06:30:31'));
        // без датчиков: будит в конце цикла в окне (уснул ~22:05 по задержке 15 мин, 6 × 85 = 06:35)
        const n3 = new Core.Night({ start: T('8T21:50:00'), wakeAt: T('9T06:45:00'), win: 20, cycle: 85, latency: 15 });
        for (let t = T('8T21:50:30'); t <= T('9T06:34:00'); t += 30000) n3.add({ t, m: null, s: null });
        out.nos = [H(n3.plan().pred), n3.decide(T('9T06:34:59')), n3.decide(T('9T06:35:00'))];
        // с датчиком, тихо, конец цикла в окне: через 3 минуты после него
        const n4 = new Core.Night({ start: T('8T21:50:00'), wakeAt: T('9T06:45:00'), win: 20, cycle: 85, latency: 15 });
        n4.add({ t: T('9T06:36:30'), m: 0.1, s: null }); n4.add({ t: T('9T06:37:00'), m: 0.1, s: null });
        out.cyc = [n4.decide(T('9T06:37:10')), n4.decide(T('9T06:38:00'))];
        // только звук
        const n5 = new Core.Night({ start: T('8T22:00:00'), wakeAt: T('9T06:45:00'), win: 20, cycle: 90 });
        n5.add({ t: T('9T06:30:00'), m: null, s: 1.4 });
        out.snd = n5.decide(T('9T06:30:01'));
        // «ещё 5 минут» — без окна, только в срок
        const n6 = new Core.Night({ start: T('9T06:31:00'), wakeAt: T('9T06:36:00'), win: 0, cycle: 90, latency: 0, snooze: true });
        n6.add({ t: T('9T06:32:00'), m: 3, s: null });
        out.snz = [n6.decide(T('9T06:32:01')), n6.decide(T('9T06:36:00'))];
        out.ms = [Core.motionScore(0.05, Array(40).fill(0.01)), Core.motionScore(0.05, Array(40).fill(0.05)), Core.motionScore(0.2, [])];
        out.fmt = [Core.dur(564), Core.toHM(-5), Core.toHM(1445), Core.mins('24:00'), Core.mins('6:05')];
        return out;
      });
      ok(JSON.stringify(r.p1) === JSON.stringify([true, '06:20', 6, '06:15']), 'план: конец 6-го цикла в окне → 06:20', JSON.stringify(r.p1));
      ok(r.p2[0] === false && r.p2[1] === '06:45', 'план: цикл мимо окна → крайнее время', JSON.stringify(r.p2));
      ok(JSON.stringify(r.b) === JSON.stringify([[5, '22:35', false], [6, '21:01', true]]), 'когда лечь: 6 циклов по 94 мин → 21:01, по норме', JSON.stringify(r.b));
      ok(JSON.stringify(r.next) === JSON.stringify(['06:45', 9, 9, 10]), 'ближайший подъём', JSON.stringify(r.next));
      ok(r.onset === '22:05', 'засыпание — первые 15 мин тишины, не раньше 5 мин', r.onset);
      ok(r.before === null && r.quiet === null, 'до окна и в тишине — не будит', `${r.before} ${r.quiet}`);
      ok(r.move === 'move' && r.dead === 'deadline', 'движение в окне → будит; крайний срок → будит', `${r.move} ${r.dead}`);
      ok(r.sum[0] === '22:05' && r.sum[1] === '06:30' && r.sum[2] === 506 && r.sum[3] === 2 && Math.abs(r.sum[4] - 510) <= 2, 'итог ночи', JSON.stringify(r.sum));
      ok(r.two === 'move', 'два средних движения подряд — тоже будит', r.two);
      ok(JSON.stringify(r.nos) === JSON.stringify(['06:35', null, 'cycle']), 'без датчиков — в конце цикла', JSON.stringify(r.nos));
      ok(JSON.stringify(r.cyc) === JSON.stringify([null, 'cycle']), 'с датчиком в тишине — через 3 мин после конца цикла', JSON.stringify(r.cyc));
      ok(r.snd === 'sound', 'только микрофон — будит по звуку', r.snd);
      ok(JSON.stringify(r.snz) === JSON.stringify([null, 'deadline']), '«ещё 5 минут» — только по сроку', JSON.stringify(r.snz));
      ok(r.ms[0] >= 1 && r.ms[1] < 1 && r.ms[2] < 1, 'порог движения от тихого уровня', JSON.stringify(r.ms));
      ok(JSON.stringify(r.fmt) === JSON.stringify(['9:24', '23:55', '00:05', null, 365]), 'формат времени', JSON.stringify(r.fmt));
      ok(!log.errors.length, 'без ошибок в консоли (ядро)', log.errors.join(' | '));
      await ctx.close();
    }

    /* 2. Без Формы: экран, связать по коду */
    {
      const api = { '/api/kcal/pair': r => r.fulfill({ status: 200, contentType: 'application/json', body: '{"token":"tok_paired_1234567890"}' }) };
      const { pg, ctx, log } = await page(browser, { token: '', api });
      await pg.goto(PAGE);
      ok(await txt(pg, '#win') === '06:40–07:00', 'без Формы: 07:00, окно 20 мин', await txt(pg, '#win'));
      ok((await txt(pg, '#forma')).includes('Связать с Формой'), 'кнопка «Связать с Формой»');
      ok((await txt(pg, '#sky figcaption')).includes('средний'), 'цикл средний', await txt(pg, '#sky figcaption'));
      ok((await txt(pg, '#goHint')).includes('обычный будильник на 07:00'), 'подсказка про запасной будильник', await txt(pg, '#goHint'));
      ok(log.gets === 0, 'без токена — к серверу не ходит', String(log.gets));
      ok(await pg.evaluate(() => document.documentElement.scrollWidth <= innerWidth && innerWidth === 390), 'нет прокрутки вбок (iPhone 14)', String(await pg.evaluate(() => [document.documentElement.scrollWidth, innerWidth])));
      await shot(pg, '01-main-nopair');
      await pg.click('#pairBtn');
      await pg.waitForSelector('.sheet.on');
      await pg.fill('#code', '123456');
      await pg.click('#pairGo');
      await pg.waitForFunction(() => document.querySelector('#forma').textContent.includes('Из Формы'));
      ok(await pg.evaluate(() => localStorage.getItem('kcal.token')) === 'tok_paired_1234567890', 'код → токен сохранён');
      ok(await txt(pg, '#win') === '06:25–06:45', 'после связи — подъём по режиму 06:45', await txt(pg, '#win'));
      const pair = log.posts.find(p => p.path === '/api/kcal/pair');
      ok(pair && pair.body.code === '123456', 'код ушёл на /api/kcal/pair');
      ok(!log.errors.length, 'без ошибок в консоли (связь)', log.errors.join(' | '));
      await ctx.close();
    }

    /* 3. #pair= из Формы */
    {
      const { pg, ctx } = await page(browser, { token: '' });
      await pg.goto(PAGE + '#pair=tok_from_forma_123456');
      await pg.waitForFunction(() => document.querySelector('#forma').textContent.includes('Из Формы'));
      ok(await pg.evaluate(() => localStorage.getItem('kcal.token')) === 'tok_from_forma_123456' && !(await pg.evaluate(() => location.hash)), '#pair= → токен, адрес чистый');
      await ctx.close();
    }

    /* 4. С Формой: данные, настройки, ночь, движение в окне, утро */
    {
      const { pg, ctx, log } = await page(browser);
      await pg.goto(PAGE);
      await pg.waitForFunction(() => document.querySelector('#forma').textContent.includes('Из Формы'));
      ok(await txt(pg, '#win') === '06:25–06:45', 'по режиму: 06:25–06:45', await txt(pg, '#win'));
      ok((await txt(pg, '#wakeSrc')) === 'не позже · по режиму', 'подпись «по режиму»', await txt(pg, '#wakeSrc'));
      ok((await txt(pg, '#sky figcaption')).includes('Цикл 94 мин — по твоему WHOOP за 11 ночей'), 'цикл из WHOOP', await txt(pg, '#sky figcaption'));
      const f = await txt(pg, '#forma');
      ok(f.includes('7:36') && f.includes('5') && f.includes('71%') && f.includes('Нужно сна: 8:36'), 'карточка Формы: сон, циклы, восстановление, норма', f);
      ok((await txt(pg, '#heroSub')) === 'Уснёшь сейчас — сна до 9:00. Разбужу, как шевельнёшься в окне, или в 06:45', '21:30 — «Уснёшь сейчас — сна до 9:00»', await txt(pg, '#heroSub'));
      ok((await pg.$$('.bed')).length === 0, 'отбой по норме прошёл — меньше нормы не предлагаем', await txt(pg, '#beds'));
      await shot(pg, '02-main-forma');
      await pg.click('#wPlus');
      ok(await txt(pg, '#wakeTv') === '06:50' && (await txt(pg, '#wakeSrc')).includes('вернуть 06:45'), '«+» → 06:50, своё на эту ночь', await txt(pg, '#wakeSrc'));
      await pg.click('#resetWake');
      ok(await txt(pg, '#wakeTv') === '06:45', '«вернуть» → по режиму', await txt(pg, '#wakeTv'));
      await pg.click('#winSeg button[data-v="30"]');
      ok(await txt(pg, '#win') === '06:15–06:45', 'окно 30 мин', await txt(pg, '#win'));
      await pg.click('#winSeg button[data-v="20"]');
      await pg.click('#sndSeg button[data-v="chime"]');
      ok(await pg.evaluate(() => JSON.parse(localStorage.getItem('wake.cfg')).snd) === 'chime', 'мелодия сохраняется');
      await pg.click('#preview');
      await pg.waitForFunction(() => !window.__wake.AU.paused, null, { timeout: 15000 });
      ok(true, '«Послушать» играет');
      await pg.click('#preview');
      await pg.waitForFunction(() => window.__wake.sounds.chime, null, { timeout: 20000 });
      // Спать: сначала «Перед сном»
      await pg.click('#go');
      await pg.waitForSelector('.sheet.on');
      ok((await txt(pg, '#shBody')).includes('Тренер напишет в Telegram в 06:45'), 'лист «Перед сном» про страховку');
      await shot(pg, '03-before-sleep');
      await pg.click('#howGo');
      await pg.waitForSelector('#night:not([hidden])');
      ok(await txt(pg, '#nline') === 'Разбужу 06:25–06:45', 'ночной экран', await txt(pg, '#nline'));
      await pg.waitForTimeout(300);
      const arm = log.posts.find(p => p.path === '/api/wake/arm');
      ok(arm && arm.body.at === '2026-11-09T06:46' && arm.body.from === '06:25' && arm.auth === 'Bearer tok_abcdefghijklmnop', 'страховка в Telegram: arm 06:46', JSON.stringify(arm));
      await pg.waitForFunction(() => document.querySelector('#nstatT').textContent.length > 0, null, { timeout: 5000 });
      await motion(pg, .004); await pg.clock.fastForward(30000);
      ok((await txt(pg, '#nstatT')).includes('движени'), 'датчик движения работает', await txt(pg, '#nstatT'));
      await shot(pg, '04-night');
      await pg.clock.fastForward(12000);
      ok(await pg.$eval('#night', e => e.classList.contains('dim')), 'через 12 с экран тускнеет');
      // ночь проходит: до окна — тихо
      await pg.clock.fastForward('08:40:00');  // 06:12
      ok(!(await pg.$eval('#alarm', e => e.hidden)) === false, 'до окна не звонит');
      await motion(pg, .004); await pg.clock.fastForward(30000);
      await motion(pg, .5); await pg.clock.fastForward(30000);  // 06:13 — движение до окна
      ok(await pg.$eval('#alarm', e => e.hidden), 'движение до окна не будит');
      await pg.clock.fastForward('00:14:00');  // 06:27 — в окне
      await motion(pg, .004); await pg.clock.fastForward(30000);
      ok(await pg.$eval('#alarm', e => e.hidden), 'в окне, тихо — спит дальше');
      await motion(pg, .5); await pg.clock.fastForward(30000);
      await pg.waitForSelector('#alarm:not([hidden])', { timeout: 3000 });
      ok((await txt(pg, '#awhy')).includes('шевельнулся'), 'рассвет: «Ты шевельнулся — сон лёгкий»', await txt(pg, '#awhy'));
      await pg.waitForFunction(() => !window.__wake.AU.paused, null, { timeout: 5000 }).then(() => ok(true, 'мелодия играет'), () => ok(false, 'мелодия играет'));
      const done = log.posts.find(p => p.path === '/api/wake/done');
      ok(done && done.body.reason === 'move' && /^06:2[6-8]$/.test(done.body.woke) && done.body.window === 20, 'итог ночи ушёл в Форму', JSON.stringify(done && done.body));
      await pg.click('#upBtn');
      ok(await pg.evaluate(() => window.__wake.AU.paused), '«Я встал» — тишина');
      await pg.waitForSelector('.sheet.on');
      ok((await txt(pg, '#shBody')).includes('Доброе утро') && (await txt(pg, '#shBody')).includes('по движению'), 'утро: итог ночи', await txt(pg, '#shBody'));
      await shot(pg, '06-morning');
      await pg.keyboard.press('Escape');
      await pg.waitForFunction(() => document.querySelector('#sheet').hidden);
      ok(true, 'Esc закрывает лист');
      await pg.click('#histBtn');
      await pg.waitForSelector('.hist button');
      ok((await pg.$$('.hist button')).length === 1, 'история: одна ночь');
      await pg.click('.hist button');
      await pg.waitForFunction(() => document.querySelector('#shT').textContent.startsWith('Ночь'));
      ok(true, 'ночь из истории открывается');
      ok(!log.errors.length, 'без ошибок в консоли (ночь)', log.errors.join(' | '));
      await ctx.close();
    }

    /* 5. Крайний срок, «ещё 5 минут», выключить, перезапуск ночью */
    {
      const { pg, ctx, log } = await page(browser, { cfg: { wake: '06:45', src: 'forma', day: '2026-11-09', win: 20, snd: 'dawn', mic: false, how: true }, reduced: true });
      await pg.goto(PAGE);
      await pg.waitForFunction(() => document.querySelector('#forma').textContent.includes('Из Формы'));
      await pg.click('#go');
      await pg.waitForSelector('#night:not([hidden])');
      ok(await pg.$eval('.sheet', e => e.hidden), 'второй раз — без листа «Перед сном»');
      await pg.clock.fastForward('09:15:30');  // 06:45:30, датчики молчат (стол)
      await pg.waitForSelector('#alarm:not([hidden])', { timeout: 3000 });
      ok((await txt(pg, '#awhy')).includes('крайнее'), 'крайний срок', await txt(pg, '#awhy'));
      await shot(pg, '05-alarm');
      await pg.click('#snoozeBtn');
      await pg.waitForSelector('#night:not([hidden])');
      ok((await txt(pg, '#nline')).startsWith('Разбужу в 06:5'), '«Ещё 5 минут»', await txt(pg, '#nline'));
      await pg.clock.fastForward('00:05:01');
      await pg.waitForSelector('#alarm:not([hidden])', { timeout: 3000 });
      ok((await txt(pg, '#awhy')).includes('Пять минут'), 'снова будит через 5 минут', await txt(pg, '#awhy'));
      await pg.click('#upBtn');
      await pg.waitForSelector('.sheet.on');
      const h = await pg.evaluate(() => JSON.parse(localStorage.getItem('wake.nights')));
      ok(h.length === 1 && h[0].reason === 'deadline', 'в истории одна ночь, причина — крайний срок', JSON.stringify(h.map(x => x.reason)));
      await pg.keyboard.press('Escape');
      // выключить ночью
      await pg.clock.fastForward('14:00:00');  // вечер 9-го
      await pg.click('#go');
      await pg.waitForSelector('#night:not([hidden])');
      await pg.click('#nOff');
      await pg.waitForSelector('.sheet.on');
      await pg.click('#offYes');
      await pg.waitForSelector('#night', { state: 'hidden' });
      ok(log.posts.some(p => p.path === '/api/wake/done' && p.body.cancel), 'выключить → страховка снята');
      // перезапуск страницы ночью
      await pg.click('#go');
      await pg.waitForSelector('#night:not([hidden])');
      await pg.reload();
      await pg.waitForSelector('#night:not([hidden])');
      ok(!(await pg.$eval('#resume', e => e.hidden)), 'после перезапуска — «Продолжить ночь»');
      await pg.click('#resume');
      await pg.waitForSelector('#resume', { state: 'hidden' });
      ok(true, 'ночь продолжается');
      ok(!log.errors.length, 'без ошибок в консоли (срок)', log.errors.join(' | '));
      await ctx.close();
    }

    /* 6. Компьютер: две колонки, клавиатура */
    {
      const { pg, ctx, log } = await page(browser, { device: { viewport: { width: 1440, height: 900 } }, time: at('8T19:00:00') });
      await pg.goto(PAGE);
      await pg.waitForFunction(() => document.querySelector('#forma').textContent.includes('Из Формы'));
      ok((await txt(pg, '#heroSub')).startsWith('Ляг в 21:01 — 6 циклов, сна 9:24'), '19:00 — «Ляг в 21:01 — 6 циклов»', await txt(pg, '#heroSub'));
      ok((await pg.$$('.bed')).length === 2 && (await txt(pg, '.bed')).startsWith('21:01'), '«когда лечь»: 21:01 (6, по норме) и 22:35 (5)', await txt(pg, '#beds'));
      const [a, b] = await pg.evaluate(() => [document.querySelector('.hero').getBoundingClientRect().left, document.querySelector('.side').getBoundingClientRect().left]);
      ok(b > a + 300, 'широкий экран — две колонки', `${a} ${b}`);
      await pg.focus('#wakeIn'); await pg.keyboard.press('ArrowUp');
      ok(await txt(pg, '#wakeTv') === '06:50', 'стрелка вверх — +5 минут', await txt(pg, '#wakeTv'));
      await shot(pg, '07-desktop');
      ok(!log.errors.length, 'без ошибок в консоли (компьютер)', log.errors.join(' | '));
      await ctx.close();
    }
  } catch (e) {
    ok(false, 'проверка упала', e.stack);
  } finally {
    await browser.close();
    srv.kill();
  }
  console.log(results.join('\n'));
  console.log(`\n${results.length - failed}/${results.length} проверок пройдено`);
  process.exit(failed ? 1 : 0);
})();
