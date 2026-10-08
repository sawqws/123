// Скриншот блока задания в тёмной теме (как на iPad у Глеба), масштаб 2x.
// usage: node horo/shot.js <ссылка на задание> <out.png>
// Тёмная тема включается только в этом браузере (localStorage), аккаунт не меняется.
const { open, close } = require('./lib');
(async () => {
  const s = await open();
  const ctx = await s.browser.newContext({ viewport: { width: 1100, height: 1400 }, deviceScaleFactor: 2, locale: 'ru-RU', storageState: await s.ctx.storageState() });
  const p = await ctx.newPage();
  await p.goto('https://horodigital.ru/student', { waitUntil: 'domcontentloaded' }); await p.waitForTimeout(5000);
  const sw = p.getByText('Тёмная тема').locator('xpath=..');
  await sw.click(); await p.waitForTimeout(1500);
  await p.goto(process.argv[2], { waitUntil: 'domcontentloaded' }); await p.waitForTimeout(9000);
  const box = p.locator('[data-testid=taskDescriptionTabs]').locator('xpath=..');
  await box.screenshot({ path: process.argv[3] });
  await close(s);
})().catch(e => { console.error(e.message); process.exit(1); });
