// Самопроверка «Формы» (Глеб 03.11): обходчик мини-приложения на локальной фикстуре — каждая вкладка и экран,
// каждая кнопка [data-a] и вкладка меню; на телефоне 390×844 и ноутбуке 1366×768, в тёмной и светлой теме.
// usage: node coach/selfcheck.js <http://127.0.0.1:порт/> <папка-отчёта> [--budget секунд] [--only phone-dark]
// Ходит ТОЛЬКО на 127.0.0.1/localhost: всё остальное (WHOOP, telegram.org, шрифты, ссылки) — отменяется.
// Пишет <папка>/crawl.json: {problems: [{kind, sev, where, what, steps, shot}], notes, stats}. Код выхода 0 — отработал
// (найденное — в отчёте), 2 — не смог запуститься. Запускает coach/selfcheck.py; руками — для отладки.
//
// Что считается проблемой (sev "error" — точно сломано, "warn" — раскладка):
//   ошибка JS (pageerror, console.error), 5xx у /api, лист не открылся/не закрылся, крестик листа не виден, вне листа
//   или за экраном, «Назад» на экране не виден, кнопку закрывает другой элемент, прокрутка вбок, вылезание за экран,
//   кнопки < 36 px на сенсорном, текст < 11 px, кнопки налезают друг на друга.
// Ошибки сразу после ответа фикстуры без примера (заголовок X-Fixture: none) — не проблема приложения, а пробел
// фикстуры: идут в notes.
const { chromium, devices } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = process.argv[2];
const OUT = process.argv[3];
const arg = (k, d) => (process.argv.includes(k) ? process.argv[process.argv.indexOf(k) + 1] : d);
const BUDGET = Number(arg('--budget', 420)) * 1000;
const ONLY = arg('--only', '');
if (!BASE || !OUT) { console.error('usage: node coach/selfcheck.js <url> <папка> [--budget сек] [--only phone-dark]'); process.exit(2); }
const base = new URL(BASE);
if (!['127.0.0.1', 'localhost'].includes(base.hostname)) { console.error('только 127.0.0.1 / localhost'); process.exit(2); }
fs.mkdirSync(path.join(OUT, 'shots'), { recursive: true });

const PHONE = { ...devices['iPhone 14'], viewport: { width: 390, height: 844 } };
const LAPTOP = { viewport: { width: 1366, height: 768 } };
// размеры идут параллельно; в каждом сначала тёмная тема — все кнопки, потом светлая — все экраны и все кнопки,
// которые в тёмной открыли лист или экран (раскладку и крестики в светлой), без анимаций (быстрее)
const SIZES = [['phone', PHONE], ['laptop', LAPTOP]];
const want = c => !ONLY || ONLY.split(',').includes(c);
// нажатия, которые уводят со страницы или закрывают приложение — нажимаем, но уход не ошибка
const LEAVE_OK = new Set(['connect', 'open', 'tochat', 'wake-open']);  // wake-open — сайт «Будильник» /wake/
const t0 = Date.now();
let dead = false;  // фикстура перестала отвечать — дальше не обходим (иначе каждый экран — «не открылась»)
const late = () => dead || Date.now() - t0 > BUDGET;
const problems = [], notes = [], seen = new Set();
const stats = { configs: 0, screens: 0, buttons: 0, sheets: 0, clicks: 0, skipped_time: 0 };

// одна и та же проблема — одной строкой: раскладка — по тексту (крестик 34×34 во всех листах — одна строка, места —
// списком), ошибки — по тексту и месту; виды (phone-dark…) — списком. Скриншот — только у новой проблемы.
const LAYOUT = new Set(['small-tap', 'small-text', 'offscreen', 'overlap', 'scroll-x', 'sheet-x']);
const keyOf = p => LAYOUT.has(p.kind) ? `${p.kind}|${p.what}` : `${p.kind}|${p.where}|${p.what}`;
const known = p => problems.some(x => x.key === keyOf(p));
function add(p) {
  const key = keyOf(p);
  const old = problems.find(x => x.key === key);
  if (old) {
    if (!old.configs.includes(p.config)) old.configs.push(p.config);
    if (!old.also.includes(p.where) && old.where !== p.where && old.also.length < 6) old.also.push(p.where);
    return;
  }
  if (problems.length >= 80) return;
  problems.push({ key, ...p, configs: [p.config], also: [] });
}

// ---------- в странице ----------
// видимые кнопки: [data-a] и вкладки меню; ключ — действие, значение и подпись (по нему кнопка находится после перерисовки)
function collect(scope) {
  const root = scope === 'sheet' ? [...document.querySelectorAll('.sheet.open')].pop() : document;
  if (!root) return [];
  const out = [], keys = new Set();
  for (const el of root.querySelectorAll('[data-a], [data-tab]')) {
    if (scope !== 'sheet' && el.closest('.sheet')) continue;
    const cs = getComputedStyle(el), b = el.getBoundingClientRect();
    if (!b.width || !b.height || cs.visibility === 'hidden' || el.closest('[hidden], [aria-hidden="true"]') || el.disabled) continue;
    const label = (el.getAttribute('aria-label') || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 40);
    const key = el.dataset.tab ? `tab:${el.dataset.tab}` : `${el.dataset.a}|${el.dataset.v || ''}|${label}`;
    if (keys.has(key)) continue;
    keys.add(key);
    out.push({ key, a: el.dataset.a || 'tab', v: el.dataset.v || el.dataset.tab || '', label });
  }
  return out;
}
function find(key) {  // кнопка по ключу -> помечаем data-sc для Playwright
  document.querySelectorAll('[data-sc]').forEach(x => x.removeAttribute('data-sc'));
  const sh = [...document.querySelectorAll('.sheet.open')].pop();
  for (const el of document.querySelectorAll('[data-a], [data-tab]')) {
    if (!sh && el.closest('.sheet')) continue;
    if (sh && !sh.contains(el)) continue;
    const label = (el.getAttribute('aria-label') || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 40);
    const k = el.dataset.tab ? `tab:${el.dataset.tab}` : `${el.dataset.a}|${el.dataset.v || ''}|${label}`;
    if (k === key) { el.setAttribute('data-sc', '1'); return true; }
  }
  return false;
}
function state() {
  const F = window.F || {}, S = F.S || {};
  return { url: location.pathname + location.search, screen: S.screen || null, tab: S.tab || null,
    sheets: document.querySelectorAll('.sheet.open').length, day: S.dayData ? S.dayData.day : null };
}
// раскладка текущего вида (лист — если открыт): прокрутка вбок, за краем, мелкие кнопки и текст, налезание кнопок
function layout(touch) {
  const W = innerWidth, H = innerHeight, out = [];
  const sh = [...document.querySelectorAll('.sheet.open')].pop();
  const root = sh || document.body;
  const name = el => `<${el.tagName.toLowerCase()}${el.id ? '#' + el.id : el.classList.length ? '.' + el.classList[0] : ''}>` +
    ((el.getAttribute('aria-label') || el.textContent || '').trim() ? ` «${(el.getAttribute('aria-label') || el.textContent).replace(/\s+/g, ' ').trim().slice(0, 24)}»` : '');
  if (document.documentElement.scrollWidth > W + 1) out.push(['warn', 'scroll-x', `прокрутка вбок: ширина ${document.documentElement.scrollWidth} > ${W}`]);
  const vis = el => {
    // закрытый <details>, display:none/прозрачный предок — не видно (checkVisibility), даже если прямоугольник есть
    if (el.checkVisibility && !el.checkVisibility({ opacityProperty: true, visibilityProperty: true, contentVisibilityAuto: true })) return null;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity === 0 || el.closest('[hidden], [aria-hidden="true"]')) return null;
    const b = el.getBoundingClientRect();
    return b.width && b.height ? [b, cs] : null;
  };
  // внутри горизонтальной ленты (overflow-x auto/hidden у предка) вылезать можно — это прокрутка ленты
  const inScroller = el => { for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden' || o === 'clip') return true; } return false; };
  // видимая часть: прямоугольник, обрезанный предками с overflow (свёрнутый «Ещё показатели» — 0 px, его поля не видны)
  const clip = (el, b) => {
    let l = b.left, t = b.top, r = b.right, btm = b.bottom;
    for (let p = el.parentElement; p && p !== document.documentElement; p = p.parentElement) {
      const cs = getComputedStyle(p);
      if (cs.overflowX === 'visible' && cs.overflowY === 'visible') continue;
      const q = p.getBoundingClientRect();
      l = Math.max(l, q.left); t = Math.max(t, q.top); r = Math.min(r, q.right); btm = Math.min(btm, q.bottom);
      if (r - l < 1 || btm - t < 1) return null;
    }
    return { left: l, top: t, right: r, bottom: btm, width: r - l, height: btm - t };
  };
  const btns = [], small = {};
  for (const el of root.querySelectorAll('*')) {
    if (!sh && el.closest('.sheet, .sheet-backdrop')) continue;
    const v = vis(el);
    if (!v) continue;
    const [b, cs] = v;
    if ((b.right > W + 1 || b.left < -1) && b.top < H && b.bottom > 0 && cs.overflow !== 'hidden' && !inScroller(el) && !el.closest('#aura, svg'))
      out.push(['warn', 'offscreen', `за краем экрана: ${name(el)} ${Math.round(b.left)}…${Math.round(b.right)} из ${W}`]);
    const tap = el.matches('button, a[href], input:not([type=hidden]), select, textarea, [role=button], [data-a], [data-tab]');
    if (tap && !el.parentElement.closest('button, a[href], [data-a], [data-tab]')) { const r = clip(el, b); if (r) btns.push([el, r]); }
    if (touch && tap && el.matches('button, [role=button], [data-a], [data-tab]') && !el.parentElement.closest('button, [data-a], [data-tab]')
        && (Math.round(b.width) < 36 || Math.round(b.height) < 36) && b.top < H && !el.matches('input[type=checkbox], input[type=radio]'))
      out.push(['warn', 'small-tap', `маленькая кнопка ${Math.round(b.width)}×${Math.round(b.height)}: ${name(el)}`]);
    if ([...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()) && parseFloat(cs.fontSize) < 11 && !el.closest('svg')) {
      const k = `${el.tagName.toLowerCase()}${el.classList.length ? '.' + el.classList[0] : ''} ${cs.fontSize}`;
      (small[k] = small[k] || []).push(el.textContent.trim().slice(0, 12));
    }
  }
  for (const [k, ex] of Object.entries(small)) out.push(['warn', 'small-text', `мелкий текст <${k.split(' ')[0]}> ${k.split(' ')[1]}: «${[...new Set(ex)].slice(0, 4).join('», «')}»${ex.length > 4 ? ` и ещё ${ex.length - 4}` : ''}`]);
  // кнопки налезают друг на друга (больше 30% площади меньшей), если одна не внутри другой
  for (let i = 0; i < btns.length; i++) for (let j = i + 1; j < btns.length; j++) {
    const [e1, a] = btns[i], [e2, c] = btns[j];
    if (e1.contains(e2) || e2.contains(e1)) continue;
    const w = Math.min(a.right, c.right) - Math.max(a.left, c.left), h = Math.min(a.bottom, c.bottom) - Math.max(a.top, c.top);
    if (w <= 0 || h <= 0) continue;
    const fx = el => { for (let p = el; p; p = p.parentElement) { const s = getComputedStyle(p).position; if (s === 'fixed' || s === 'sticky') return true; } return false; };
    if (fx(e1) !== fx(e2)) continue;  // плавающее меню над прокручиваемым — не «налезание раскладки»
    // прилипший крестик (sticky) над прокрученным листом — так задумано: строки уезжают под него
    const stuck = el => getComputedStyle(el).position === 'sticky' && el.closest('.sheet') && el.closest('.sheet').scrollTop > 0;
    if (stuck(e1) || stuck(e2)) continue;
    if (w * h <= 0.3 * Math.min(a.width * a.height, c.width * c.height)) continue;
    // видно ли налезание: в середине общей части сверху одна из двух кнопок (не обрезано свёрнутым блоком и не за экраном)
    const cx = (Math.max(a.left, c.left) + Math.min(a.right, c.right)) / 2, cy = (Math.max(a.top, c.top) + Math.min(a.bottom, c.bottom)) / 2;
    if (cx < 0 || cy < 0 || cx > W || cy > H) continue;
    const top = document.elementFromPoint(cx, cy);
    if (!top || !(e1.contains(top) || e2.contains(top))) continue;
    out.push(['warn', 'overlap', `кнопки налезают: ${name(e1)} и ${name(e2)}`]);
  }
  // крестик листа
  if (sh) {
    const x = sh.querySelector('.sheet-x'), sb = sh.getBoundingClientRect();
    if (!x) out.push(['error', 'sheet-x', 'у листа нет крестика']);
    else {
      const v = vis(x);
      if (!v) out.push(['error', 'sheet-x', 'крестик листа не виден']);
      else {
        const [b] = v;
        if (b.top < -1 || b.left < -1 || b.right > W + 1 || b.bottom > H + 1) out.push(['error', 'sheet-x', `крестик за экраном: ${Math.round(b.left)},${Math.round(b.top)} (экран ${W}×${H})`]);
        else if (b.top < sb.top - 1 || b.right > sb.right + 1 || b.left < sb.left - 1) out.push(['error', 'sheet-x', 'крестик вне листа']);
      }
    }
  }
  return [...new Map(out.map(x => [x[2], x])).values()].slice(0, 15);
}
function markX() {  // крестик верхнего листа -> data-scx (у body бывают другие div после листа — :last-of-type ненадёжен)
  document.querySelectorAll('[data-scx]').forEach(x => x.removeAttribute('data-scx'));
  const s = [...document.querySelectorAll('.sheet.open')].pop(), x = s && s.querySelector('.sheet-x');
  if (x) x.setAttribute('data-scx', '1');
  return !!x;
}
function closeTo(n) {  // закрыть лишние листы сверху до n (после вложенного листа)
  const all = [...document.querySelectorAll('.sheet.open')];
  all.slice(n).reverse().forEach(x => x.close && x.close());
}
function backOk() {  // «Назад» на экране подробностей: есть, виден, внутри экрана по высоте
  const b = [...document.querySelectorAll('[data-a="back"]')].find(el => !el.closest('.sheet'));
  if (!b) return 'нет кнопки «Назад»';
  const r = b.getBoundingClientRect(), cs = getComputedStyle(b);
  if (!r.width || !r.height || cs.visibility === 'hidden' || +cs.opacity === 0) return 'кнопка «Назад» не видна';
  if (r.bottom < 0 || r.top > innerHeight || r.left < -1 || r.right > innerWidth + 1) return `кнопка «Назад» за экраном (${Math.round(r.left)},${Math.round(r.top)})`;
  return '';
}

// ---------- обход одного вида ----------
async function crawlConfig(browser, cname, dev, scheme, only) {  // only — ключи кнопок для светлой темы (null — все)
  const openers = new Set();
  const ctx = await browser.newContext({ ...dev, colorScheme: scheme, serviceWorkers: 'block', reducedMotion: only ? 'reduce' : 'no-preference' });
  await ctx.route('**/*', r => {
    const u = new URL(r.request().url());
    return ['127.0.0.1', 'localhost'].includes(u.hostname) && u.port === base.port ? r.continue() : r.abort();
  });
  // красные тосты (Kit.toast(…, 'err')) — так приложение показывает пойманные ошибки: их тоже собираем
  await ctx.addInitScript(() => {
    window.__scToasts = [];
    new MutationObserver(() => {
      const t = document.getElementById('toast');
      if (t && t.classList.contains('show') && t.classList.contains('err') && t.textContent !== window.__scLast) window.__scToasts.push(window.__scLast = t.textContent);
      if (t && !t.classList.contains('show')) window.__scLast = '';
    }).observe(document, { subtree: true, childList: true, attributes: true, attributeFilter: ['class'] });
  });
  let page = null;
  ctx.on('page', p => { if (page && p !== page) p.close().catch(() => {}); });  // window.open — закрыть сразу
  page = await ctx.newPage();
  const touch = !!dev.hasTouch;
  let errs = [], gap = 0, left = null, inflight = 0;
  page.on('request', () => inflight++);
  page.on('requestfinished', () => inflight--);
  page.on('requestfailed', () => inflight--);
  page.on('pageerror', e => errs.push({ t: 'js', m: e.message }));
  page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource|net::ERR|ERR_FAILED|status of 4\d\d/.test(m.text())) errs.push({ t: 'console', m: m.text() }); });
  page.on('response', r => {
    const u = new URL(r.url());
    if (!u.pathname.startsWith('/api/')) return;
    if (r.status() >= 500) errs.push({ t: 'api', m: `${r.request().method()} ${u.pathname} → ${r.status()}` });
    if (r.headers()['x-fixture'] === 'none') gap = Date.now();
  });
  page.on('dialog', d => { notes.push(`${cname}: всплыло окно браузера «${d.message().slice(0, 60)}»`); d.dismiss().catch(() => {}); });
  page.on('framenavigated', f => { if (f === page.mainFrame()) { const u = new URL(f.url()); if (u.host !== base.host || !['/', '/index.html'].includes(u.pathname)) left = f.url(); } });

  const go = async q => {
    left = null;
    inflight = 0;
    await page.goto(new URL(q, BASE).href, { waitUntil: 'load', timeout: 15000 }).catch(e => {
      if (/ERR_CONNECTION_REFUSED|ERR_CONNECTION_RESET/.test(e.message)) {
        if (!dead) add({ kind: 'fixture', sev: 'note', config: cname, where: q, what: 'фикстура перестала отвечать — обход прерван', steps: [] });
        dead = true;
      } else errs.push({ t: 'load', m: 'не открылась: ' + e.message.split('\n')[0] });
    });
    await settle();
  };
  const settle = async (ms = only ? 120 : 260) => {  // запросы закончились и заглушки загрузки ушли, потом — время на анимацию
    const end = Date.now() + 3000;
    await page.waitForTimeout(120);
    while (inflight > 0 && Date.now() < end) await page.waitForTimeout(60);
    await page.waitForFunction(() => !document.querySelector('[aria-busy="true"]'), null, { timeout: Math.max(100, end - Date.now()) }).catch(() => {});
    // переход экрана (View Transition, html[data-nav]) и появление листа закончились — иначе под нагрузкой проверка
    // попадала в середину: «Назад» ещё не отрисован (экран чёрный), лист-окно ещё прозрачный, крестик сжат анимацией
    await page.waitForFunction(() => !document.documentElement.dataset.nav && !document.getAnimations().some(a => a.playState === 'running'
      && Number.isFinite(a.effect && a.effect.getComputedTiming().endTime)), null, { timeout: Math.max(100, end - Date.now()) }).catch(() => {});
    await page.waitForTimeout(ms);
  };
  const shot = async (tag) => {
    const f = path.join(OUT, 'shots', `${cname}-${tag}`.replace(/[^\wа-яё.-]+/gi, '_').slice(0, 90) + '.png');
    await page.screenshot({ path: f }).catch(() => {});
    return f;
  };
  const flush = async (where, steps) => {  // ошибки, накопившиеся с прошлого раза
    for (const m of await page.evaluate(() => (window.__scToasts || []).splice(0)).catch(() => [])) errs.push({ t: 'toast', m: 'красная плашка: ' + m });
    const list = errs; errs = [];
    if (!list.length) return;
    const fixture = gap && Date.now() - gap < 4000;
    for (const e of list.slice(0, 4)) {
      if (fixture && e.t !== 'api') { notes.push(`${cname} ${where}: после ответа фикстуры без примера — ${e.m.slice(0, 140)}`); continue; }
      const p = { kind: e.t === 'api' ? 'api-5xx' : e.t === 'toast' ? 'toast-error' : 'js-error', sev: 'error', config: cname, where, what: e.m.slice(0, 220), steps };
      add({ ...p, shot: known(p) ? '' : await shot(where) });
    }
    gap = 0;
  };
  const check = async (where, steps) => {
    for (const [sev, kind, what] of await page.evaluate(layout, touch).catch(() => [])) {
      const p = { kind, sev, config: cname, where, what, steps };
      add({ ...p, shot: known(p) ? '' : await shot(where) });
    }
  };

  // экраны: вкладки, свои экраны (sleep/rec/strain) и экраны модулей F.screens, листы profile/summary
  await go('./');
  await flush('загрузка', ['открыть приложение']);
  const mods = await page.evaluate(() => Object.keys((window.F && window.F.screens) || {})).catch(() => []);
  const SCREENS = ['', 'trends', 'food', 'coach', 'sleep', 'rec', 'strain', ...mods.filter(m => m !== 'workout'), 'profile', 'summary'];
  stats.configs++;
  for (const p of SCREENS) {
    if (late()) { stats.skipped_time++; continue; }
    const q = p ? `./?p=${p}` : './';
    const where = p ? `?p=${p}` : 'Главная';
    await go(q);
    stats.screens++;
    const open = [`открыть ${q} (${cname})`];
    await flush(where, open);
    if (dead) break;
    if (['sleep', 'rec', 'strain', ...mods].includes(p)) {
      const bad = await page.evaluate(backOk).catch(() => '');
      if (bad) add({ kind: 'back', sev: 'error', config: cname, where, what: bad, steps: open, shot: await shot(where) });
    }
    await check(where, open);
    if (cname === 'phone-dark' || cname === 'laptop-dark') await shot(where);
    const home = await page.evaluate(state);
    const scope = home.sheets ? 'sheet' : 'page';
    const btns = await page.evaluate(collect, scope);
    for (const b of btns) {
      if (late()) { stats.skipped_time++; break; }
      const k = `${cname}|${b.key}`;
      if (seen.has(k) || (only && !only.has(b.key))) continue;
      seen.add(k);
      // вернуть экран, если прошлое нажатие его поменяло
      let now = await page.evaluate(state).catch(() => null);
      if (now && !left && now.sheets > home.sheets) {
        await page.evaluate(() => [...document.querySelectorAll('.sheet.open')].reverse().forEach(x => x.close && x.close())).catch(() => {});
        await page.waitForTimeout(450);
        now = await page.evaluate(state).catch(() => null);
      }
      if (!now || left || JSON.stringify(now) !== JSON.stringify(home)) await go(q);
      if (!(await page.evaluate(find, b.key).catch(() => false))) continue;  // кнопки больше нет (например, меню после перерисовки)
      stats.buttons++;
      const name = `«${b.label || b.a}» (${b.a}${b.v ? '=' + b.v : ''})`;
      const steps = [...open, `нажать ${name}`];
      const before = await page.evaluate(state);
      try {
        await page.click('[data-sc]', { timeout: 2500 });
        stats.clicks++;
      } catch (e) {
        const m = e.message.split('\n').find(x => /intercepts pointer events/.test(x));
        if (m) add({ kind: 'covered', sev: 'error', config: cname, where, what: `кнопку ${name} закрывает другой элемент: ${m.replace(/.*?(<[^>]{0,80}>).*/, '$1')}`, steps, shot: await shot(where + '-covered') });
        else if (/not visible|outside of the viewport/.test(e.message)) add({ kind: 'covered', sev: 'warn', config: cname, where, what: `кнопка ${name} не нажимается: не видна или за экраном`, steps, shot: await shot(where) });
        continue;
      }
      await settle(450);
      if (left) {
        if (!LEAVE_OK.has(b.a)) notes.push(`${cname} ${where}: ${name} уводит на ${left}`);
        await flush(where, steps);
        continue;
      }
      await flush(where, steps);
      const after = await page.evaluate(state).catch(() => null);
      if (!after) continue;
      if (after.sheets > before.sheets || (after.screen && after.screen !== before.screen)) openers.add(b.key);
      if (after.sheets > before.sheets) {  // открылся лист: раскладка, крестик, закрыть крестиком
        stats.sheets++;
        const sw = `${where} → ${b.label || b.a}`;
        await check(sw, steps);
        if (cname === 'phone-dark' || cname === 'laptop-dark') await shot(sw);
        // кнопки внутри листа — один уровень, только в первом виде каждого размера (листы одинаковы во всех темах)
        if (!only && !late()) await crawlSheet(sw, steps);
        await page.evaluate(closeTo, after.sheets).catch(() => {});  // вложенные листы из обхода листа — закрыть
        await page.waitForTimeout(400);
        if (await page.evaluate(markX).catch(() => false)) {
          await page.click('[data-scx]', { timeout: 2000 }).catch(e => add({ kind: 'sheet-x', sev: 'error', config: cname, where: sw, what: 'крестик не нажимается: ' + e.message.split('\n')[0].slice(0, 120), steps }));
          const n = await page.waitForFunction(k => document.querySelectorAll('.sheet.open').length < k, after.sheets, { timeout: 1500 })
            .then(() => 0).catch(() => after.sheets);
          if (n >= after.sheets) add({ kind: 'sheet-close', sev: 'error', config: cname, where: sw, what: 'лист не закрылся по крестику', steps: [...steps, 'нажать ✕'], shot: await shot(sw + '-notclosed') });
          await flush(sw, [...steps, 'нажать ✕']);
        }
      } else if (after.screen && after.screen !== before.screen) {  // открылся экран подробностей: «Назад» на месте
        const bad = await page.evaluate(backOk).catch(() => '');
        if (bad) add({ kind: 'back', sev: 'error', config: cname, where: `${where} → ${after.screen}`, what: bad, steps, shot: await shot(after.screen) });
        await check(`${where} → ${after.screen}`, steps);
      }
    }
  }

  async function crawlSheet(sw, steps) {
    const inner = await page.evaluate(collect, 'sheet').catch(() => []);
    const top = await page.evaluate(state);
    for (const b of inner.slice(0, 12)) {
      if (late()) return;
      const k = `${cname}|sheet|${b.key}`;
      if (seen.has(k)) continue;
      seen.add(k);
      const now = await page.evaluate(state).catch(() => null);
      if (!now || left || now.sheets < top.sheets) return;  // лист закрылся/ушли — дальше этот лист не обходим
      if (!(await page.evaluate(find, b.key).catch(() => false))) continue;
      stats.buttons++;
      const name = `«${b.label || b.a}» (${b.a}${b.v ? '=' + b.v : ''})`;
      const st = [...steps, `в листе нажать ${name}`];
      try { await page.click('[data-sc]', { timeout: 2000 }); stats.clicks++; } catch (e) {
        const m = e.message.split('\n').find(x => /intercepts pointer events/.test(x));
        if (m) add({ kind: 'covered', sev: 'error', config: cname, where: sw, what: `кнопку ${name} в листе закрывает другой элемент: ${m.replace(/.*?(<[^>]{0,80}>).*/, '$1')}`, steps: st, shot: await shot(sw + '-covered') });
        continue;
      }
      await settle(400);
      await flush(sw, st);
      const s2 = await page.evaluate(state).catch(() => null);
      if (s2 && s2.sheets > top.sheets) {  // лист из листа: проверить крестик и закрыть его
        await check(`${sw} → ${b.label || b.a}`, st);
        if (await page.evaluate(markX).catch(() => false)) await page.click('[data-scx]', { timeout: 1500 }).catch(() => {});
        await page.waitForTimeout(400);
        await page.evaluate(closeTo, top.sheets).catch(() => {});
      }
    }
  }
  await ctx.close();
  return openers;
}

(async () => {
  let browser;
  try { browser = await chromium.launch(); } catch (e) { console.error('браузер не запустился:', e.message); process.exit(2); }
  const fell = (c, e) => { add({ kind: 'crawler', sev: 'note', config: c, where: '-', what: 'обходчик упал: ' + e.message.split('\n')[0], steps: [] }); return null; };
  await Promise.all(SIZES.map(async ([size, dev]) => {
    let openers = null;
    if (want(`${size}-dark`)) openers = await crawlConfig(browser, `${size}-dark`, dev, 'dark', null).catch(e => fell(`${size}-dark`, e));
    if (want(`${size}-light`)) await crawlConfig(browser, `${size}-light`, dev, 'light', openers || new Set()).catch(e => fell(`${size}-light`, e));
  }));
  await browser.close();
  stats.seconds = Math.round((Date.now() - t0) / 1000);
  const res = { problems: problems.map(({ key, ...p }) => p), notes: [...new Set(notes)].slice(0, 60), stats };
  fs.writeFileSync(path.join(OUT, 'crawl.json'), JSON.stringify(res, null, 1));
  const n = res.problems.filter(p => p.sev !== 'note').length;
  console.log(`экранов ${stats.screens}, кнопок ${stats.buttons}, листов ${stats.sheets}, ${stats.seconds} с — ${n ? 'проблем: ' + n : 'всё чисто'}`);
  for (const p of res.problems) console.log(`- [${p.sev}] ${p.where}${p.also.length ? ` (+${p.also.length})` : ''}: ${p.what} (${p.configs.join(', ')})`);
  process.exit(0);
})();
