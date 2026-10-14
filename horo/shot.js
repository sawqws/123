// Скриншот задания в тёмной теме (как на iPad у Глеба), масштаб 2x — только карточка с условием.
// usage: node horo/shot.js <ссылка на задание> <out.png>
// Окно высокое (3200 px): иначе поле ответа внизу экрана закрывает последние примеры.
// Рядом пишет <out>.json — где на скрине строки условия (пиксели картинки), по ним horo/sheet.py
// пишет ответ справа от каждого номера: {width, height, rects: [{x0, y0, x1, y1, t}]}, t — текст или «[img]».
// Тёмная тема включается только в этом браузере (localStorage), аккаунт не меняется.
const fs = require('fs');
const { open, close } = require('./lib');
const SCALE = 2;
(async () => {
  const s = await open();
  const ctx = await s.browser.newContext({ viewport: { width: 1100, height: 3200 }, deviceScaleFactor: SCALE, locale: 'ru-RU', storageState: await s.ctx.storageState() });
  const p = await ctx.newPage();
  await p.goto('https://horodigital.ru/student', { waitUntil: 'domcontentloaded' }); await p.waitForTimeout(5000);
  const sw = p.getByText('Тёмная тема').locator('xpath=..');
  await sw.click(); await p.waitForTimeout(1500);
  await p.goto(process.argv[2], { waitUntil: 'domcontentloaded' }); await p.waitForTimeout(9000);
  const card = p.locator('[data-testid=taskDescriptionTabs]');
  await card.screenshot({ path: process.argv[3] });
  const lay = await card.evaluate((el, k) => {
    const base = el.getBoundingClientRect();
    const rects = [];
    const add = (r, t) => {
      if (r.width > 1 && r.height > 1) rects.push({ x0: Math.round((r.left - base.left) * k), y0: Math.round((r.top - base.top) * k), x1: Math.round((r.right - base.left) * k), y1: Math.round((r.bottom - base.top) * k), t });
    };
    const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    for (let n = w.currentNode; n; n = w.nextNode()) {
      if (n.nodeType === 3 && n.textContent.trim()) {
        const r = document.createRange(); r.selectNodeContents(n);
        for (const q of r.getClientRects()) add(q, n.textContent.trim().slice(0, 200));
      } else if (n.nodeType === 1 && n.tagName === 'IMG') add(n.getBoundingClientRect(), '[img]');
    }
    return { width: Math.round(base.width * k), height: Math.round(base.height * k), rects };
  }, SCALE);
  fs.writeFileSync(process.argv[3].replace(/\.png$/i, '') + '.json', JSON.stringify(lay));
  await close(s);
})().catch(e => { console.error(e.message); process.exit(1); });
