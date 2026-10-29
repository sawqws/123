// Плавность сайта перед выкаткой (правило Глеба 29.10): процессор замедлен в 4 раза (как слабый телефон),
// считаем подвисания (долгие задачи > 50 мс) и медленные кадры (> 34 мс) при загрузке и прокрутке.
// usage: node common/perfcheck.js <url>      Код выхода 1 — если блокировка > 200 мс или медленных кадров > 15.
// Сценарии конкретного сайта (открыть окно, поиск, добавить) — в его тестах.
const { chromium, devices } = require('playwright');
const url = process.argv[2];
if (!url) { console.error('usage: node common/perfcheck.js <url>'); process.exit(2); }

(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ ...devices['iPhone 14'], serviceWorkers: 'block' });
  const p = await ctx.newPage(), cdp = await ctx.newCDPSession(p);
  await p.addInitScript(() => {
    window.__lt = 0; new PerformanceObserver(l => l.getEntries().forEach(e => { window.__lt += e.duration - 50; })).observe({ entryTypes: ['longtask'] });
    window.__frames = []; let last = performance.now(); const tick = t => { window.__frames.push(t - last); last = t; requestAnimationFrame(tick); }; requestAnimationFrame(tick);
  });
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
  const res = {};
  const run = async (name, fn) => { await p.evaluate(() => { window.__lt = 0; window.__frames = []; }); await fn(); await p.waitForTimeout(500);
    res[name] = await p.evaluate(() => ({ блокировка_мс: Math.round(window.__lt), медленных_кадров: window.__frames.filter(f => f > 34).length })); };
  await run('загрузка', async () => { await p.goto(url); await p.waitForTimeout(2000); });
  await run('прокрутка', async () => { for (let i = 0; i < 15; i++) { await p.mouse.wheel(0, 150); await p.waitForTimeout(40); } for (let i = 0; i < 15; i++) { await p.mouse.wheel(0, -150); await p.waitForTimeout(40); } });
  const tbt = res['прокрутка'].блокировка_мс, slow = res['прокрутка'].медленных_кадров;
  console.log(JSON.stringify(res, null, 1));
  const bad = tbt > 200 || slow > 15;
  console.log(bad ? 'НЕ ПЛАВНО — разберись до выкатки' : 'Плавно');
  await b.close();
  process.exit(bad ? 1 : 0);
})();
