// Рендер иконки и логотипов «Пульт» в PNG (Playwright + Inter). node render.js <папка>
const { chromium } = require('playwright');
const fs = require('fs'), path = require('path');
const out = process.argv[2] || '.';
const svg = fs.readFileSync(path.join(__dirname, 'icon.svg'), 'utf8');
const logo = (ink) => `<div style="display:flex;align-items:center;gap:14px;height:60px;width:300px">
  <div style="width:52px;height:52px">${svg.replace('<svg ', '<svg width="52" height="52" ')}</div>
  <div style="font:600 34px Inter;letter-spacing:-.5px;color:${ink}">Пульт</div></div>`;
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage();
  for (const s of [16, 24, 32, 48, 64, 128, 256, 512, 1024]) {
    await p.setViewportSize({ width: s, height: s });
    await p.setContent(`<body style="margin:0;background:transparent">${svg.replace('<svg ', `<svg width="${s}" height="${s}" `)}</body>`);
    await p.screenshot({ path: path.join(out, `icon-${s}.png`), omitBackground: true });
  }
  await p.setViewportSize({ width: 300, height: 60 });
  for (const [name, ink] of [['logo_dark', '#ffffff'], ['logo_light', '#111318']]) {
    await p.setContent(`<body style="margin:0;background:transparent">${logo(ink)}</body>`);
    await p.screenshot({ path: path.join(out, `${name}.png`), omitBackground: true });
  }
  await b.close();
})();
