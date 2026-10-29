// Проверка сайта на всех устройствах перед выкаткой (правило Глеба 29.10): телефоны, iPad, ноутбук, мониторы.
// usage: node common/devcheck.js <url> [папка-скриншотов] [--scheme dark|light]
// На каждом размере: нет прокрутки вбок, ничего не вылезает за экран, кнопки на сенсорных экранах не меньше 36 px,
// текст не меньше 11 px, нет ошибок в консоли; скриншот каждого размера — посмотреть глазами.
// Код выхода 1, если что-то не так. Для сайта со своей логикой — ещё его тесты (например, site/kcal_test.js).
const { chromium, devices } = require('playwright');
const fs = require('fs');
const path = require('path');

const url = process.argv[2];
const dir = process.argv[3] && !process.argv[3].startsWith('--') ? process.argv[3] : '';
const scheme = process.argv.includes('--scheme') ? process.argv[process.argv.indexOf('--scheme') + 1] : 'dark';
if (!url) { console.error('usage: node common/devcheck.js <url> [папка] [--scheme dark|light]'); process.exit(2); }

const DEVICES = [
  ['Android маленький 360', { ...devices['Galaxy S9+'], viewport: { width: 360, height: 740 } }],
  ['iPhone SE', devices['iPhone SE']],
  ['iPhone 14', devices['iPhone 14']],
  ['iPhone 15 Pro Max', devices['iPhone 15 Pro Max']],
  ['iPhone альбом', { ...devices['iPhone 14 landscape'] }],
  ['iPad mini', devices['iPad Mini']],
  ['iPad Pro альбом', devices['iPad Pro 11 landscape']],
  ['Ноутбук 1366×768', { viewport: { width: 1366, height: 768 } }],
  ['Маленькое окно 1024×600', { viewport: { width: 1024, height: 600 } }],
  ['Монитор 1920×1080', { viewport: { width: 1920, height: 1080 } }],
  ['Монитор 2560×1440', { viewport: { width: 2560, height: 1440 } }],
];

(async () => {
  if (dir) fs.mkdirSync(dir, { recursive: true });
  const browser = await chromium.launch();
  const problems = [];
  for (const [name, dev] of DEVICES) {
    const ctx = await browser.newContext({ ...dev, colorScheme: scheme });
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('console', m => m.type() === 'error' && !/Failed to load resource|net::ERR/.test(m.text()) && errors.push(m.text()));
    await page.goto(url, { waitUntil: 'networkidle' }).catch(e => errors.push('не открылась: ' + e.message));
    await page.waitForTimeout(1200);
    const r = await page.evaluate(touch => {
      const W = innerWidth, H = innerHeight, out = [];
      if (document.documentElement.scrollWidth > W + 1) out.push(`прокрутка вбок: ширина ${document.documentElement.scrollWidth} > ${W}`);
      for (const el of document.querySelectorAll('body *')) {
        const cs = getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity === 0 || el.closest('[hidden]')) continue;
        const b = el.getBoundingClientRect();
        if (!b.width || !b.height) continue;
        const fixed = cs.position === 'fixed';
        if ((fixed || b.top < H) && (b.right > W + 1 || b.left < -1) && !el.closest('[aria-hidden="true"]') && cs.overflow !== 'hidden')
          out.push(`за краем: <${el.tagName.toLowerCase()} ${el.id ? '#' + el.id : el.className ? '.' + String(el.className).split(' ')[0] : ''}> ${Math.round(b.left)}…${Math.round(b.right)} из ${W}`);
        if (touch && el.matches('button, a, input, [role=button]') && b.top < H && (b.width < 36 || b.height < 36) && !el.closest('[aria-hidden="true"]'))
          out.push(`маленькая кнопка: <${el.tagName.toLowerCase()} ${el.id ? '#' + el.id : ''}${el.getAttribute('aria-label') ? ' «' + el.getAttribute('aria-label') + '»' : ''}> ${Math.round(b.width)}×${Math.round(b.height)}`);
        if (el.childNodes.length && [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()) && parseFloat(cs.fontSize) < 11)
          out.push(`мелкий текст ${cs.fontSize}: «${el.textContent.trim().slice(0, 20)}»`);
      }
      return [...new Set(out)].slice(0, 12);
    }, !!dev.hasTouch);
    const list = r.concat(errors.map(e => 'ошибка: ' + e.slice(0, 160)));
    if (dir) await page.screenshot({ path: path.join(dir, name.replace(/[^\wа-яё]+/gi, '_') + '.png') });
    console.log(`${list.length ? '✗' : '✓'} ${name} ${dev.viewport.width}×${dev.viewport.height}${list.length ? '\n   - ' + list.join('\n   - ') : ''}`);
    if (list.length) problems.push(name);
    await ctx.close();
  }
  await browser.close();
  console.log(problems.length ? `\nНЕ ПРОШЛО: ${problems.join(', ')}` : `\nВсе ${DEVICES.length} размеров в порядке`);
  process.exit(problems.length ? 1 : 0);
})();
