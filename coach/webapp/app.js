/* Форма — приложение тренера внутри Telegram. Общий вид — kit.css/kit.js, здесь только своё. Без сборки. */
(() => {
  const { tg, esc, api, toast, haptic, busy } = Kit;
  // лист снизу, как в iOS: тянется пальцем вниз и закрывается, если отпустить ниже трети или смахнуть
  function sheet(html, onClose) {
    const el = Kit.sheet(html, onClose), back = el.previousElementSibling;
    let y0 = null, t0 = 0, dy = 0, drag = false;
    el.addEventListener('touchstart', e => {
      if (e.touches.length !== 1 || e.target.closest('input, textarea, select, .seg, .chips, .rg-days') || matchMedia('(min-width: 1024px)').matches) return;  // на широком лист — окно по центру
      y0 = e.touches[0].clientY; t0 = performance.now(); dy = 0; drag = false;
    }, { passive: true });
    el.addEventListener('touchmove', e => {
      if (y0 == null) return;
      const d = e.touches[0].clientY - y0;
      if (!drag) { if (d > 6 && el.scrollTop <= 0) { drag = true; el.classList.add('dragging'); if (back) back.style.transition = 'none'; } else if (Math.abs(d) > 6) { y0 = null; return; } else return; }
      e.preventDefault();
      dy = Math.max(0, d) + Math.min(0, d) * .15;  // вверх — с сопротивлением, как резинка
      el.style.transform = `translateY(${dy}px)`;
      if (back) back.style.opacity = String(Math.max(0, 1 - dy / (el.offsetHeight || 600)));
    }, { passive: false });
    const end = () => {
      if (y0 == null) return;
      y0 = null;
      if (!drag) return;
      const v = dy / Math.max(1, performance.now() - t0);
      el.classList.remove('dragging');
      if (back) { back.style.transition = ''; back.style.opacity = ''; }
      el.style.transform = '';
      if (dy > el.offsetHeight / 3 || v > .5) { haptic('select'); el.close(); }
    };
    el.addEventListener('touchend', end); el.addEventListener('touchcancel', end);
    // клавиатура (iPhone, Telegram): окно не сжимается, а видимая часть уезжает — лист встаёт над клавиатурой и не выше
    // видимой части, иначе его верх с крестиком уходит за экран (Глеб 03.11: «крестики улетели вверх»)
    const vv = window.visualViewport;
    if (vv) {
      const fit = () => {
        if (!el.isConnected) { vv.removeEventListener('resize', fit); vv.removeEventListener('scroll', fit); return; }
        const kb = Math.max(0, Math.round(innerHeight - vv.height - vv.offsetTop)), lim = Math.round(vv.height - 12);
        if (matchMedia('(min-width: 1024px)').matches || (kb < 2 && lim >= innerHeight * .88)) { el.style.bottom = ''; el.style.maxHeight = ''; return; }
        el.style.bottom = `${kb}px`; el.style.maxHeight = `${Math.min(lim, Math.round(innerHeight * .88))}px`;
      };
      // первая проверка — после первого кадра: синхронный замер при открытии заставлял браузер пересчитать всю страницу
      // (57 мс при CPU ×4 на «Здоровье» с графиками) — лист дёргался на старте
      vv.addEventListener('resize', fit); vv.addEventListener('scroll', fit); requestAnimationFrame(() => requestAnimationFrame(fit));
    }
    return el;
  }
  const $ = (s, el = document) => el.querySelector(s);
  // Плавающая «🛠» создаётся из body[data-support], но пока атрибут стоит на body, kit.js считает «поддержкой» любой тап по странице
  // (closest('[data-support]') находит body) — создаём кнопку сами и снимаем атрибут.
  delete document.body.dataset.support;  // плавающей «🛠» нет (у WHOOP её нет, она перекрывала экран) — «Поддержка» внизу «Сегодня» и в «Ещё»
  const view = $('#view');
  // Модули (journal.js, plan.js, …) подключаются ДО app.js и кладут функцию в window.FormaModules; app.js вызывает
  // каждую с объектом F перед стартом. Что модуль может: F.screens[вид] — экран «подробностей» (открыть: F.openScreen(вид, арг)),
  // F.actions[data-a] — обработчик кнопки, F.views[вкладка] — заменить вкладку, F.slots.<место> — куски HTML, F.onData — после загрузки.
  const F = window.F = { screens: {}, actions: {}, views: {}, onData: [],
    slots: { homeTop: [], homeAfterDials: [], homeAfterOutlook: [], homeAfterMyDay: [], homeJournal: [], homeAfterDashboard: [],
      healthTop: [], healthAfterMonitor: [], healthBottom: [], sleep: [], rec: [], strain: [], coachTop: [], plus: [] } };
  const slot = (name, ...args) => F.slots[name].map(fn => { try { return fn(...args) || ''; } catch (e) { console.error(name, e); return ''; } }).join('');
  const S = { data: null, tab: 'today', dayData: null, range: 30, series: {}, history: null, progress: undefined,
    asking: false, briefing: false, progressing: false, photo: false };

  const WD = ['Воскресенье', 'Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота'];
  const WD_S = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];
  const MON = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
  const MON_S = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
  const C = { green: 'var(--rec-green)', yellow: 'var(--rec-yellow)', red: 'var(--rec-red)' };
  const ZONE = { green: 'Готов к нагрузке', yellow: 'Умеренная нагрузка', red: 'День восстановления' };

  const num = (x, d = 0) => x == null ? '—' : Number(x).toFixed(d).replace('.', ',');
  const big = x => x == null ? '—' : Math.round(x).toLocaleString('ru-RU');
  const liters = ml => ml >= 1000 ? `${num(ml / 1000, 1).replace(/,0$/, '')} л` : `${Math.round(ml)} мл`;
  const hm = h => { if (h == null) return '—'; const m = Math.round(h * 60); return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}`; };
  const hHuman = h => { if (h == null) return '—'; const m = Math.round(h * 60); return `${Math.floor(m / 60)} ч ${String(m % 60).padStart(2, '0')} мин`; };
  const isoDay = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const parseDay = s => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
  const pct = (v, of) => of ? Math.max(0, Math.min(100, v / of * 100)) : 0;
  const plural = (n, a, b, c) => n % 10 === 1 && n % 100 !== 11 ? a : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? b : c;
  const fail = e => { haptic('error'); toast(e.message || String(e), 'err'); };
  const clock = iso => new Date(iso).toLocaleTimeString('ru', { hour: '2-digit', minute: '2-digit' });
  // линейные иконки (SVG, 24x24, штрих 1.8), без эмодзи
  const svgi = (d, cls = '') => `<svg class="ico ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
  const I = {
    today: svgi('<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4.2"/>'),
    trends: svgi('<path d="M3 17l5.5-6 4 4L21 6"/><path d="M15.5 6H21v5.5"/>'),
    food: svgi('<path d="M7 3v7a2.2 2.2 0 0 0 2.2 2.2V21M11.4 3v7a2.2 2.2 0 0 1-2.2 2.2"/><path d="M17.5 21V3c-2.3 1.4-3.5 4-3.5 7 0 2.6 1.3 3.8 3.5 4"/>'),
    coach: svgi('<path d="M4 5.5h16v11H9.5L5 20.5v-4H4z"/>'),
    user: svgi('<circle cx="12" cy="8" r="3.8"/><path d="M4.5 20.5c0-3.8 3.4-6 7.5-6s7.5 2.2 7.5 6"/>'),
    back: svgi('<path d="M15 5l-7 7 7 7"/>'),
    next: svgi('<path d="M9 5l7 7-7 7"/>'),
    stats: svgi('<path d="M5 20V11M12 20V4M19 20v-6"/>'),
    moon: svgi('<path d="M20 14.2A8.2 8.2 0 1 1 9.8 4a6.6 6.6 0 0 0 10.2 10.2z"/>'),
    drop: svgi('<path d="M12 3.2s6 6.4 6 10.8a6 6 0 0 1-12 0c0-4.4 6-10.8 6-10.8z"/>'),
    bolt: svgi('<path d="M13 2.5L5 13.5h6l-1 8 8-11h-6z"/>'),
    run: svgi('<circle cx="14.5" cy="4.5" r="1.8"/><path d="M8.5 21l3-6.5-3-2.5 3-5 4 3 3.5 1M11.5 14.5l4 2 1 4.5"/>'),
    weight: svgi('<path d="M6.5 7.5v9M3.5 10v4M17.5 7.5v9M20.5 10v4M6.5 12h11"/>'),
    plug: svgi('<path d="M9 3v5M15 3v5M6 8h12v3a6 6 0 0 1-12 0zM12 17v4"/>'),
    help: svgi('<circle cx="12" cy="12" r="9"/><path d="M9.5 9.3a2.5 2.5 0 1 1 3.4 2.3c-.6.3-.9.8-.9 1.4M12 16.6v.1"/>'),
    home: svgi('<path d="M4 10.5L12 4l8 6.5V20h-5.5v-5.5h-5V20H4z"/>'),
    health: svgi('<path d="M12 20s-7.5-4.6-7.5-10.2A4.3 4.3 0 0 1 12 7.2a4.3 4.3 0 0 1 7.5 2.6C19.5 15.4 12 20 12 20z"/><path d="M6.5 12h3l1.5-2.5 2 5 1.5-2.5h3"/>'),
    plus: svgi('<path d="M12 5v14M5 12h14"/>'),
    check: svgi('<path d="M5 12.5l4.5 4.5L19 7.5"/>'),
    flame: svgi('<path d="M12 21c-3.6 0-6-2.4-6-5.6 0-3.4 2.6-5 3.4-8.4 1.6 1.2 2.4 2.8 2.4 4.4 1-.6 1.6-1.8 1.6-3.2 2.4 1.8 2.6 4.6 2.6 7.2 0 3.2-2.4 5.6-4 5.6z"/>'),
    cam: svgi('<path d="M4 8h3.5L9 5.5h6L16.5 8H20v11H4z"/><circle cx="12" cy="13" r="3.4"/>'),
    arrow: svgi('<path d="M4 12h15M13 6l6 6-6 6"/>'),
    clock: svgi('<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>'),
    bed: svgi('<path d="M3 18V7M3 14h18v4M21 14v-2.5A2.5 2.5 0 0 0 18.5 9H11v5"/><circle cx="7" cy="11" r="1.6"/>'),
    sparkle: svgi('<path d="M12 3.5l1.8 5 5 1.8-5 1.8-1.8 5-1.8-5-5-1.8 5-1.8z"/><path d="M18.5 15.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/>'),
    hrv: svgi('<path d="M2.5 13h4l2-5 3 10 2.5-7 1.5 2h6"/>'),
    rhr: svgi('<path d="M12 19.5s-7.5-4.4-7.5-9.7A4 4 0 0 1 12 7.2a4 4 0 0 1 6.9 1.4"/><path d="M18.5 12.5v7M15.8 17l2.7 2.7 2.7-2.7"/>'),
    lungs: svgi('<path d="M12 4v7.5M12 10l-2.5 2M12 10l2.5 2"/><path d="M8.5 8C6 8.8 4 12.8 4 16.8c0 1.6 1.2 2.6 2.6 2.1 2-.7 3.4-2.6 3.4-5.1V10"/><path d="M15.5 8c2.5.8 4.5 4.8 4.5 8.8 0 1.6-1.2 2.6-2.6 2.1-2-.7-3.4-2.6-3.4-5.1V10"/>'),
    zones: svgi('<path d="M12 20s-8-4.8-8-10.5A4.5 4.5 0 0 1 12 7a4.5 4.5 0 0 1 8 2.5C20 15.2 12 20 12 20z"/><path d="M7.5 12.5h2.5l1-2 2 4 1-2h2.5"/>'),
    sync: svgi('<path d="M20 11a8 8 0 0 0-14.6-4.4M4 13a8 8 0 0 0 14.6 4.4"/><path d="M5 3v4h4M19 21v-4h-4"/>'),
  };
  const sportIcon = s => /бокс|кик|единобор|мма|борьб|джиу/i.test(s) ? I.bolt : /бег|ходьб|поход|вело|плав/i.test(s) ? I.run : /силов|пауэр|функц|кроссфит/i.test(s) ? I.weight : I.bolt;

  // простой Markdown от тренера -> HTML
  function md(text) {
    const lines = esc(text || '').split('\n');
    let out = '', list = false;
    for (let l of lines) {
      l = l.replace(/^#{1,6}\s+(.*)$/, '**$1**').replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<b>$1</b>')
        .replace(/(\d) (г|мл|мин|ккал|л|ч|мс|кг|см)(?![а-яё])/g, '$1&nbsp;$2')
        .replace(/(\d)–(\d)/g, '$1\u2060–\u2060$2')
        // \u00ab\u0418\u0441\u0442\u043e\u0447\u043d\u0438\u043a\u0438:\u00bb \u043f\u043e\u0441\u043b\u0435 \u043f\u043e\u0438\u0441\u043a\u0430 \u0442\u0440\u0435\u043d\u0435\u0440\u0430 \u2014 [\u0441\u0430\u0439\u0442](https://\u2026) \u2192 \u0441\u0441\u044b\u043b\u043a\u0430 (\u043e\u0442\u043a\u0440\u044b\u0432\u0430\u0435\u0442\u0441\u044f \u0447\u0435\u0440\u0435\u0437 Telegram, data-a="open")
        .replace(/\[([^\]]{1,80})\]\((https?:\/\/[^\s()]+)\)/g, '<a class="src" data-a="open" data-v="$2" role="link" tabindex="0">$1</a>');
      const li = l.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/);
      if (li) { if (!list) { out += '<ul>'; list = true; } out += `<li>${li[1]}</li>`; continue; }
      if (list) { out += '</ul>'; list = false; }
      if (l.trim()) out += `<p>${l}</p>`;
    }
    return out + (list ? '</ul>' : '');
  }
  // разбор из бота уже в HTML Telegram (b, i, code) с «• » в начале строк
  const plainOf = h => (h || '').replace(/<\/?(b|i|code|s)>/g, m => ({ '<b>': '**', '</b>': '**' }[m] || ''))
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
  const tgHtml = h => md(plainOf(h));
  // совет на день: первые 1–2 строки разбора без маркеров
  function advice(t) {
    const lines = plainOf(t.brief || '').split('\n').map(x => x.replace(/^\s*(?:[-•]|\*(?!\*))\s*/, '').trim()).filter(Boolean);
    if (lines.length) return md(lines.slice(0, 2).join('\n'));
    if (t.tips && t.tips.length) return `<p>${esc(t.tips[0].text)}</p>`;
    return '';
  }

  // ---------- кольца ----------
  // счётчик: цифра набегает от 0 при загрузке (animate)
  const cnt = (v, d = 0) => v == null ? '—' : `<span class="cnt" data-count="${v}" data-d="${d}">${num(v, d)}</span>`;
  // band — целевой отрезок нагрузки [от, до] (0..1) светлой дугой под основной
  function dial(p, color, value, label, sub, act, size = 100, band = null) {
    const w = 6.5, r = 50 - w / 2 - 1, c = 2 * Math.PI * r, off = c * (1 - Math.max(0, Math.min(1, p || 0)));
    const bd = band ? `<circle class="band" cx="50" cy="50" r="${r}" stroke-width="${w}" stroke-dasharray="${((band[1] - band[0]) * c).toFixed(2)} ${c.toFixed(2)}" stroke-dashoffset="${(-band[0] * c).toFixed(2)}"/>` : '';
    return `<button class="dial ${size > 100 ? 'big' : ''}" data-a="detail" data-v="${act}" style="--c:${color}" aria-label="${esc(label)}">
      <span class="dial-ring"><svg viewBox="0 0 100 100" aria-hidden="true"><circle class="trk" cx="50" cy="50" r="${r}" stroke-width="${w}"/>${bd}
        <circle class="arc" cx="50" cy="50" r="${r}" stroke-width="${w}" stroke-dasharray="${c.toFixed(2)}" stroke-dashoffset="${c.toFixed(2)}" data-off="${off.toFixed(2)}"/></svg>
        <span class="dial-v">${value}</span></span>
      <span class="dial-k">${esc(label)}<i class="chev-s">${I.next}</i></span>${sub ? `<span class="dial-s">${sub}</span>` : ''}</button>`;
  }
  function ring(p, color, inner, size = 120, band = null) {
    const w = size > 150 ? 8 : 11, r = 50 - w / 2, c = 2 * Math.PI * r, off = c * (1 - Math.max(0, Math.min(1, p || 0)));
    const bd = band ? `<circle class="band" cx="50" cy="50" r="${r}" stroke-width="${w}" stroke-dasharray="${((band[1] - band[0]) * c).toFixed(2)} ${c.toFixed(2)}" stroke-dashoffset="${(-band[0] * c).toFixed(2)}"/>` : '';
    return `<div class="kring ${size > 150 ? 'xl' : ''}" style="--c:${color};--size:${size}px"><svg viewBox="0 0 100 100" aria-hidden="true">
      <circle class="trk" cx="50" cy="50" r="${r}" stroke-width="${w}"/>${bd}<circle class="arc" cx="50" cy="50" r="${r}" stroke-width="${w}"
      stroke-dasharray="${c.toFixed(2)}" stroke-dashoffset="${c.toFixed(2)}" data-off="${off.toFixed(2)}"/></svg><div class="kring-in">${inner}</div></div>`;
  }
  const bar = (p, color) => `<div class="bar"><i style="width:0;background:${color}" data-w="${p}%"></i></div>`;
  // Анимации: только transform/opacity/stroke (120 Гц без рывков). Карточки ниже экрана оживают, когда до них докрутили (reveal).
  const REDUCED = matchMedia('(prefers-reduced-motion: reduce)');
  const outside = el => !!el.closest('.later');
  // instant — тихое обновление (данные раз в 2 минуты, вода, еда): всё сразу на месте, без повторной анимации
  function animate(root = document, instant = false) {
    requestAnimationFrame(() => requestAnimationFrame(() => {
      root.querySelectorAll('.arc[data-off]').forEach(el => { if (!outside(el)) { el.style.strokeDashoffset = el.dataset.off; delete el.dataset.off; } });
      root.querySelectorAll('[data-w]').forEach(el => { if (!outside(el)) { el.style.width = el.dataset.w; el.classList.add('w-in'); delete el.dataset.w; } });
      root.querySelectorAll('.cnt[data-count]').forEach(el => {
        if (outside(el)) return;
        if (instant) { delete el.dataset.count; return; }
        const to = Number(el.dataset.count), d = Number(el.dataset.d || 0), t0 = performance.now(), dur = 1300;
        delete el.dataset.count;
        if (REDUCED.matches || !isFinite(to)) return;
        const tick = now => { const k = Math.min(1, (now - t0) / dur), e = k === 1 ? 1 : 1 - Math.pow(2, -10 * k); el.textContent = num(to * e, d); if (k < 1) requestAnimationFrame(tick); };
        requestAnimationFrame(tick);
      });
    }));
  }
  // Глеб 05.11: «быстро листаешь — приложение как будто перезапускается, всё дёргается, улетает-залетает»: раньше карточки
  // ниже экрана были спрятаны и въезжали по одной, когда до них долистаешь. Теперь всё на месте сразу; появление с задержкой —
  // только у первых карточек при открытии экрана (ниже экрана оно проходит незаметно, пока туда не долистал)
  function stagger() {
    let i = 0;
    view.querySelectorAll('.rise').forEach(el => el.style.setProperty('--i', Math.min(i++, 10)));
  }
  // переходы между экранами как в iOS: View Transitions (iOS 18+, Chrome), иначе — въезд нового экрана
  function nav(kind, fn, plain) {
    const root = document.documentElement;
    if (REDUCED.matches) { fn(); return; }
    if (plain) { S.navKind = kind; try { fn(); } finally { S.navKind = null; } return; }
    // переход ещё идёт (двойной тап): ждём, пока он применит своё состояние, и только потом наше — иначе старое перезапишет новое
    if (S.vt) { S.vt.updateCallbackDone.catch(() => {}).finally(() => { S.navKind = kind; try { fn(); } finally { S.navKind = null; } }); return; }
    if (document.startViewTransition) {
      root.dataset.nav = kind;
      try {
        const vt = S.vt = document.startViewTransition(() => { S.inVT = true; try { fn(); } finally { S.inVT = false; } });
        vt.finished.catch(() => {}).finally(() => { if (S.vt === vt) { delete root.dataset.nav; S.vt = null; } });
        return;
      } catch (e) { delete root.dataset.nav; S.vt = null; }
    }
    S.navKind = kind; try { fn(); } finally { S.navKind = null; }
  }

  // ---------- графики (SVG без библиотек) ----------
  // Понятность (Глеб 02.11: «графики очень непонятные»): даты под столбиками, значения — только у последнего дня и максимума
  // и никогда не поверх столбиков (над самым высоким под подписью), уровни (низкое/среднее/высокое) — словами справа, касание
  // или наведение показывает день и значение. Нормы на графиках нет (Глеб 03.11: «зачем норма? я могу и больше, и меньше»).
  // Сон — риска «сколько было нужно в ту ночь» у каждого столбика. 6 месяцев — по неделям (≈26 столбиков, среднее недели).
  // Столбики ≤ 24 px со скруглённым верхом, линии 2 px, сетка — тонкие сплошные.
  const chartW = () => {  // карточка: padding 20 + 20; на широком экране — ширина колонки
    const w = ($('#app') || view).clientWidth || 358, cols = WIDE3.matches ? 3 : WIDE.matches ? 2 : 1;
    return Math.max(200, Math.min((w - (cols - 1) * 24) / cols, 800) - 40);
  };
  const TP = 22, CH = 116, GUT = 64;  // поле над графиком (подписи значений), высота графика, колонка уровней справа
  const f1 = v => Math.round(v * 10) / 10;
  const hS = h => { const m = Math.round(h * 60); return `${Math.floor(m / 60)}ч ${m % 60}м`; };  // коротко для подписей: 7ч 12м
  const dayTip = s => { const d = parseDay(s), t = S.data && S.data.today && S.data.today.day; return `${s === t ? 'Сегодня' : WD[d.getDay()]}, ${d.getDate()} ${MON[d.getMonth()]}`; };
  // даты под графиком: неделя — день недели и число, месяц — числа (месяц — у первой и при смене), 6 месяцев — месяцы
  // (weekly — столбик = неделя: месяц под первой неделей, где он начался)
  function xLabels(days, PW, slot, weekly) {
    const n = days.length, y1 = TP + CH + 16;
    let s = '';
    const put = (i, a, b, at) => {
      let x = at != null ? at : (i + .5) * slot, anchor = at != null ? 'start' : 'middle';
      if (anchor === 'middle' && x < 16) { x = 0; anchor = 'start'; } else if (anchor === 'middle' && x > PW - 16) { x = PW; anchor = 'end'; }
      s += `<text class="xl" x="${f1(x)}" y="${y1}" text-anchor="${anchor}">${a}</text>`;
      if (b) s += `<text class="xl2" x="${f1(x)}" y="${y1 + 14}" text-anchor="${anchor}">${b}</text>`;
    };
    if (weekly) {
      let lastM = -1, lastX = -99;
      days.forEach((d, i) => {
        const mid = new Date(parseDay(d).getTime() + 3 * 864e5), m = mid.getMonth(), x = (i + .5) * slot;  // четверг недели
        if (m === lastM) return;
        lastM = m;
        if (x - lastX < 30) return;
        put(i, MON_S[m]); lastX = x;
      });
      return s;
    }
    if (n <= 7) {
      days.forEach((d, i) => { const dt = parseDay(d); put(i, WD_S[dt.getDay()], `${dt.getDate()}${i === 0 || dt.getDate() === 1 ? ' ' + MON_S[dt.getMonth()] : ''}`); });
      return s;
    }
    if (n <= 45) {
      const k = Math.max(1, Math.ceil(40 / slot)), idx = [];
      for (let i = n - 1; i >= 0; i -= k) idx.unshift(i);
      let lastM = -1;
      idx.forEach(i => { const dt = parseDay(days[i]), m = dt.getMonth(); put(i, `${dt.getDate()}${m !== lastM ? ' ' + MON_S[m] : ''}`); lastM = m; });
      return s;
    }
    let lastX = -99;
    days.forEach((d, i) => {
      const dt = parseDay(d);
      if (dt.getDate() !== 1 && i !== 0) return;
      const x = i * slot;
      if (x - lastX < 34 || x > PW - 24) return;
      put(i, MON_S[dt.getMonth()], '', x); lastX = x;
    });
    return s;
  }
  // подписи значений по важности; та, что налезает на уже поставленную, не рисуется (значение есть в подсказке)
  function labels(cands, PW, taken = []) {
    let s = '';
    cands.forEach(c => {
      if (!c) return;
      const w = c.t.length * 6.3 + 4;
      let x = c.x, anchor = c.anchor || 'middle';
      if (anchor === 'middle' && x - w / 2 < 0) { x = 0; anchor = 'start'; }
      if (anchor === 'middle' && x + w / 2 > PW) { x = PW; anchor = 'end'; }
      if (anchor === 'end' && x - w < 0) { x = 0; anchor = 'start'; }
      const x0 = anchor === 'end' ? x - w : anchor === 'middle' ? x - w / 2 : x;
      if (c.tops) {  // столбики: строка встаёт над самым высоким из тех, что под ней (+ 4 px воздуха)
        const i0 = Math.max(0, Math.floor((x0 - 2) / c.slot)), i1 = Math.min(c.tops.length - 1, Math.floor((x0 + w + 2) / c.slot));
        let hi = TP + CH; for (let i = i0; i <= i1; i++) hi = Math.min(hi, c.tops[i]);
        c.y = hi - 5;
        if (c.y - 10 < 0) return;  // выше некуда — значение есть в подсказке
      }
      const b = [x0, c.y - 11, x0 + w, c.y + 3];
      if (taken.some(o => b[0] < o[2] && b[2] > o[0] && b[1] < o[3] && b[3] > o[1])) return;
      taken.push(b);
      s += `<text class="${c.cls || 'vl'}" x="${f1(x)}" y="${f1(c.y)}" text-anchor="${anchor}">${esc(c.t)}</text>`;
    });
    return s;
  }
  // tips[i] = [день, значение, пояснение, y точки, цвет точки] — для плавающей подсказки
  const svgWrap = (W, H, s, days, PW, slot, tips, kind, label, weekly) => `<svg class="chart ${kind}" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" tabindex="0"
    aria-label="${esc(label || 'График')}. Коснись столбика или точки — покажу день и значение" data-tip="${esc(JSON.stringify(tips))}" data-slot="${f1(slot)}" data-pw="${f1(PW)}">${s}${xLabels(days, PW, slot, weekly)}</svg>`;
  const xbOf = n => n <= 7 ? 36 : 22;
  // столбик: скруглённый верх 4 px, ровное основание
  function rbar(x, y, w, base) {
    const h = base - y; if (h <= 0) return '';
    const r = Math.min(4, w / 2, h);
    return `M${f1(x)},${base}V${f1(y + r)}Q${f1(x)},${f1(y)} ${f1(x + r)},${f1(y)}H${f1(x + w - r)}Q${f1(x + w)},${f1(y)} ${f1(x + w)},${f1(y + r)}V${base}Z`;
  }
  // полоса «обычно у тебя» [от, до]: заливка под данными и пунктирные края
  const bandRect = (b, y, PW) => `<rect class="ch-band" x="0" y="${f1(y(b[1]))}" width="${PW}" height="${f1(Math.max(1, y(b[0]) - y(b[1])))}"/>`;
  const bandEdges = (b, y, PW) => b.map(v => `<line class="ch-band-e" x1="0" x2="${PW}" y1="${f1(y(v))}" y2="${f1(y(v))}"/>`).join('');
  // o: max, color (цвет или функция строки), bands [[от, до, слово]], need — ключ «сколько было нужно» (риска у каждого
  // столбика), fmt (подпись значения с единицей), tip (строка -> [значение, пояснение, цвет точки]), label (для диктора),
  // band [от, до] — полоса, w — своя ширина. Строка может нести tipDay (подпись дня в подсказке) и wk (столбик = неделя).
  function bars(rows, key, o = {}) {
    const W = o.w || chartW(), G = o.bands ? GUT : 0, PW = W - G, n = rows.length, slot = PW / n, H = TP + CH + xbOf(n);
    const vals = rows.map(r => r[key]).filter(v => v != null), weekly = rows.some(r => r.wk);
    if (!vals.length) return '<div class="chart-empty">Нет данных за этот период</div>';
    const needs = o.need ? rows.map(r => r[o.need]).filter(v => v != null) : [];
    const top = o.max || Math.max(1, ...vals, ...needs, o.band ? o.band[1] : 0) * 1.1, base = TP + CH;
    const y = v => base - Math.max(0, Math.min(v, top)) / top * CH;
    const bw = weekly ? Math.max(3, Math.min(18, slot * .64)) : n > 60 ? Math.max(1, slot - 1) : n > 20 ? Math.max(2, slot - 2) : Math.min(24, slot * .6);
    const fmt = o.fmt || (v => num(v)), col = r => typeof o.color === 'function' ? o.color(r) : o.color || 'var(--ink)';
    let s = '';
    if (o.bands) o.bands.forEach(([a, b, word]) => {
      if (a > 0) s += `<line class="grid" x1="0" x2="${PW}" y1="${f1(y(a))}" y2="${f1(y(a))}"/>`;
      s += `<text class="band-t" x="${PW + 10}" y="${f1((y(a) + y(b)) / 2 + 4)}">${word}</text>`;
    });
    if (o.band) s += bandRect(o.band, y, PW);
    s += `<line class="axis" x1="0" x2="${PW}" y1="${base + .5}" y2="${base + .5}"/>`;
    const hw = Math.min(slot, Math.max(bw + 10, 16));
    s += `<rect class="ch-hb" x="${f1((slot - hw) / 2)}" y="${TP - 6}" width="${f1(hw)}" height="${CH + 6}" rx="${Math.min(8, hw / 2)}"/>`;
    const tips = [], tops = [];  // tops[i] — верх столбика (и риски) i: подписи не лезут на него
    let iMax = -1, iLast = -1, ticks = '';
    rows.forEach((r, i) => {
      const v = r[key], x = i * slot + (slot - bw) / 2, dn = r.tipDay || dayTip(r.day);
      const nd = o.need && r[o.need] != null ? r[o.need] : null;
      if (v == null) { tips.push([dn, 'нет данных', '', base]); tops.push(nd != null ? y(nd) : base); return; }
      if (iMax < 0 || v > rows[iMax][key]) iMax = i;
      iLast = i;
      s += `<path class="gb" style="--i:${Math.round(i / n * 24)}" d="${rbar(x, Math.min(y(v), base - 2), bw, base)}" fill="${col(r)}"/>`;
      // сон: риска «сколько было нужно в эту ночь» — короткая черта поперёк столбика на высоте потребности
      // (подложка цвета карточки + тонкая черта — видно и поверх столбика, и над ним; в ч/б столбик того же цвета, что черта)
      if (nd != null) { const yy = f1(y(nd)), a = f1(x), b = f1(x + bw); ticks += `<line class="need-bg" x1="${a}" x2="${b}" y1="${yy}" y2="${yy}"/><line class="need-tk" x1="${a}" x2="${b}" y1="${yy}" y2="${yy}"/>`; }
      tops.push(Math.min(y(v), nd != null ? y(nd) - 1 : base));
      const tp = o.tip ? o.tip(r) : [fmt(v), ''];
      tips.push([dn, tp[0], tp[1] || '', y(v), tp[2] || '']);
    });
    s += ticks;
    if (o.band) s += bandEdges(o.band, y, PW);  // края полосы — поверх столбиков, иначе их не видно
    // подписи: последний день (жирно) и максимум — над самым высоким столбиком под подписью, без наложений
    const taken = [], lab = labels([iLast >= 0 ? { i: iLast, cls: 'vl last' } : null, iMax >= 0 && iMax !== iLast ? { i: iMax, cls: 'vl' } : null]
      .filter(Boolean).map(c => ({ x: (c.i + .5) * slot, t: fmt(rows[c.i][key]), cls: c.cls, tops, slot })), PW, taken);
    return svgWrap(W, H, s + lab, rows.map(r => r.day), PW, slot, tips, 'bars', o.label, weekly);
  }
  // плавная линия без «выбросов» за точки (монотонная кривая): ни одна дуга не выше максимума и не ниже минимума
  function smooth(P) {
    const n = P.length;
    if (n < 3) return 'M' + P.map(p => `${f1(p[0])},${f1(p[1])}`).join('L');
    const dx = [], m = [], t = [];
    for (let i = 0; i < n - 1; i++) { dx[i] = P[i + 1][0] - P[i][0]; m[i] = (P[i + 1][1] - P[i][1]) / dx[i]; }
    t[0] = m[0]; t[n - 1] = m[n - 2];
    for (let i = 1; i < n - 1; i++) t[i] = m[i - 1] * m[i] <= 0 ? 0 : (m[i - 1] + m[i]) / 2;
    for (let i = 0; i < n - 1; i++) {
      if (m[i] === 0) { t[i] = 0; t[i + 1] = 0; continue; }
      const a = t[i] / m[i], b = t[i + 1] / m[i], h = a * a + b * b;
      if (h > 9) { const k = 3 / Math.sqrt(h); t[i] = k * a * m[i]; t[i + 1] = k * b * m[i]; }
    }
    let d = `M${f1(P[0][0])},${f1(P[0][1])}`;
    for (let i = 0; i < n - 1; i++) {
      const h = dx[i] / 3;
      d += ` C${f1(P[i][0] + h)},${f1(P[i][1] + t[i] * h)} ${f1(P[i + 1][0] - h)},${f1(P[i + 1][1] - t[i + 1] * h)} ${f1(P[i + 1][0])},${f1(P[i + 1][1])}`;
    }
    return d;
  }
  // base — больше не рисуется (нормы на графиках нет, Глеб 03.11), оставлен для совместимости вызовов;
  // o.fmt — подпись с единицей («92 мс»), o.label — для диктора
  function line(rows, key, color, base, o = {}) {
    const W = o.w || chartW(), PW = W, n = rows.length, slot = PW / n, H = TP + CH + xbOf(n), weekly = rows.some(r => r.wk);
    const pts = rows.map((r, i) => [(i + .5) * slot, r[key], i]).filter(p => p[1] != null);
    if (pts.length < 2) return '<div class="chart-empty">Мало данных для графика</div>';
    const fmt = o.fmt || (v => num(v));
    const vs = pts.map(p => p[1]).concat(o.band || []);
    let lo = Math.min(...vs), hi = Math.max(...vs); const sp = (hi - lo) || 1; lo -= sp * .06; hi += sp * .06;
    const y = v => TP + 4 + (hi - v) / (hi - lo) * (CH - 24);  // снизу место под подпись минимума
    const P = pts.map(p => [p[0], y(p[1])]), d = smooth(P), bottom = TP + CH;
    let s = o.band ? bandRect(o.band, y, PW) + bandEdges(o.band, y, PW) : '';
    s += `<line class="axis" x1="0" x2="${PW}" y1="${bottom + .5}" y2="${bottom + .5}"/>`;
    s += `<g class="ch-hl"><line class="ch-x" x1="${f1(slot / 2)}" x2="${f1(slot / 2)}" y1="${TP - 6}" y2="${bottom}"/></g>`;
    s += `<path class="fillin" d="${d} L${f1(P[P.length - 1][0])},${bottom} L${f1(P[0][0])},${bottom} Z" fill="${color}"/>`;
    s += `<path class="draw" pathLength="1" d="${d}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>`;
    let iMax = 0, iMin = 0;
    pts.forEach((p, k) => { if (p[1] > pts[iMax][1]) iMax = k; if (p[1] < pts[iMin][1]) iMin = k; });
    const L = pts.length - 1, taken = [];
    const above = k => ({ x: pts[k][0], y: P[k][1] - 9, t: fmt(pts[k][1]) }), below = k => ({ x: pts[k][0], y: P[k][1] + 17, t: fmt(pts[k][1]) });
    const lastLab = { ...(L === iMin && L !== iMax ? below(L) : above(L)), anchor: 'end', x: pts[L][0] + 4, cls: 'vl last' };
    let lab = labels([lastLab], PW, taken);
    // точки у последнего дня, максимума и минимума — в кольце цвета карточки
    const dot = (k, r) => `<circle class="${k === L ? 'dotin' : 'mm'}" cx="${f1(P[k][0])}" cy="${f1(P[k][1])}" r="${r}" fill="${color}"/>`;
    const mm = labels([iMax !== L ? { ...above(iMax), cls: 'vl' } : null], PW, taken);  // 1–2 подписи: последний день и максимум
    lab += mm;
    const dots = (mm && iMax !== L ? dot(iMax, 3) : '') + dot(L, 4);
    s += `<circle class="ch-pt" cx="0" cy="0" r="5" fill="${color}"/>`;
    const tips = rows.map(r => r[key] == null ? [r.tipDay || dayTip(r.day), 'нет данных', '', bottom]
      : [r.tipDay || dayTip(r.day), fmt(r[key]), o.tip ? o.tip(r) : '', y(r[key])]);
    return svgWrap(W, H, s + dots + lab, rows.map(r => r.day), PW, slot, tips, 'lines', o.label, weekly);
  }

  // плавающая подсказка: наведение мышью, касание пальцем (ведёшь — едет за пальцем), стрелки с клавиатуры;
  // второе касание того же дня, касание мимо или Esc — скрыть. Одна на всё приложение, текст — через textContent.
  const tip = document.createElement('div');
  tip.className = 'ch-tip'; tip.setAttribute('role', 'status'); tip.setAttribute('aria-live', 'polite');
  tip.innerHTML = '<b></b><span class="ct-d"></span><span class="ct-x"><i></i><em></em></span>';
  document.body.append(tip);
  let tipAt = null;  // { svg, i }
  const tipData = svg => {  // разбор один раз на версию графика (тихое обновление меняет data-tip у того же узла)
    if (svg._tk !== svg.dataset.tip) { try { svg._tips = JSON.parse(svg.dataset.tip); } catch (e) { svg._tips = []; } svg._tk = svg.dataset.tip; }
    return svg._tips;
  };
  function tipIdx(svg, clientX) {
    const r = svg.getBoundingClientRect(), k = r.width / Number(svg.viewBox.baseVal.width || r.width);
    const x = (clientX - r.left) / k, n = tipData(svg).length;
    return Math.max(0, Math.min(n - 1, Math.floor(x / Number(svg.dataset.slot))));
  }
  function tipPlace() {
    if (!tipAt) return;
    const { svg, i } = tipAt, d = tipData(svg)[i];
    if (!d || !svg.isConnected) return tipHide();
    const r = svg.getBoundingClientRect(), k = r.width / Number(svg.viewBox.baseVal.width || r.width), slot = Number(svg.dataset.slot);
    const cx = r.left + (i + .5) * slot * k, cy = r.top + Math.max(TP - 6, d[3] - 8) * k;
    const w = tip.offsetWidth, h = tip.offsetHeight;
    const x = Math.max(8, Math.min(innerWidth - w - 8, cx - w / 2)), yy = cy - h - 10 < 8 ? cy + 26 : cy - h - 10;
    tip.style.transform = `translate(${Math.round(x)}px, ${Math.round(yy)}px)`;
  }
  function tipShow(svg, i) {
    const d = tipData(svg)[i]; if (!d) return;
    if (tipAt && tipAt.svg !== svg) tipClear();
    tipAt = { svg, i };
    tip.querySelector('b').textContent = d[1];
    tip.querySelector('.ct-d').textContent = d[0];
    const x = tip.querySelector('.ct-x'); x.hidden = !d[2];
    x.querySelector('em').textContent = d[2] || '';
    const dot = x.querySelector('i'); dot.hidden = !d[4]; dot.style.background = d[4] || '';
    const slot = Number(svg.dataset.slot);
    const hb = svg.querySelector('.ch-hb, .ch-hl'), pt = svg.querySelector('.ch-pt');
    if (hb) { hb.style.transform = `translateX(${f1(i * slot)}px)`; hb.classList.add('on'); }
    if (pt) { pt.style.transform = `translate(${f1((i + .5) * slot)}px, ${f1(d[3])}px)`; pt.classList.toggle('on', d[1] !== 'нет данных'); }
    svg.classList.add('tipping');
    const fresh = !tip.classList.contains('on');
    if (fresh) tip.style.transition = 'opacity .15s';  // первое появление — сразу на месте, дальше едет за пальцем
    tip.classList.add('on');
    tipPlace();
    if (fresh) requestAnimationFrame(() => { tip.style.transition = ''; });
  }
  function tipClear() {
    if (!tipAt) return;
    const { svg } = tipAt;
    svg.classList.remove('tipping');
    svg.querySelectorAll('.ch-hb, .ch-hl, .ch-pt').forEach(e => e.classList.remove('on'));
  }
  function tipHide() { tipClear(); tipAt = null; tip.classList.remove('on'); }
  const chartOf = e => e.target && e.target.closest ? e.target.closest('svg.chart[data-tip]') : null;
  document.addEventListener('pointermove', e => {
    const svg = chartOf(e);
    if (e.pointerType === 'mouse') { if (svg) tipShow(svg, tipIdx(svg, e.clientX)); else if (tipAt && !tipAt.pinned) tipHide(); return; }
    if (svg && e.buttons && tipAt && tipAt.svg === svg) tipShow(svg, tipIdx(svg, e.clientX));  // палец ведёт по графику
  }, { passive: true });
  document.addEventListener('click', e => {
    const svg = chartOf(e);
    if (!svg) { if (tipAt && !e.target.closest('.ch-tip')) tipHide(); return; }
    const i = tipIdx(svg, e.clientX);
    if (tipAt && tipAt.fresh) { tipAt.fresh = false; tipAt.pinned = true; haptic('select'); return; }  // это касание и открыло подсказку
    if (tipAt && tipAt.svg === svg && tipAt.i === i && (tipAt.pinned || e.pointerType !== 'mouse')) { tipHide(); return; }
    tipShow(svg, i); tipAt.pinned = true; haptic('select');
  });
  // палец: подсказка — сразу при касании (и едет, если вести); второе касание того же дня её скрывает (в click)
  document.addEventListener('pointerdown', e => {
    const svg = chartOf(e); if (!svg || e.pointerType === 'mouse') return;
    const i = tipIdx(svg, e.clientX);
    if (tipAt && tipAt.svg === svg && tipAt.i === i) return;
    tipShow(svg, i); tipAt.fresh = true;
  }, { passive: true });
  document.addEventListener('pointercancel', () => { if (tipAt && tipAt.fresh) tipHide(); });  // палец начал прокрутку страницы — подсказка не нужна
  document.addEventListener('focusin', e => { const svg = chartOf(e); if (svg && svg.matches(':focus-visible')) tipShow(svg, tipData(svg).length - 1); });
  document.addEventListener('focusout', e => { if (tipAt && e.target === tipAt.svg) tipHide(); });
  window.addEventListener('keydown', e => {
    if (!tipAt) return;
    if (e.key === 'Escape') { e.preventDefault(); e.stopImmediatePropagation(); tipHide(); return; }
    if ((e.key === 'ArrowLeft' || e.key === 'ArrowRight') && document.activeElement === tipAt.svg) {
      e.preventDefault(); e.stopImmediatePropagation();
      const n = tipData(tipAt.svg).length;
      tipShow(tipAt.svg, Math.max(0, Math.min(n - 1, tipAt.i + (e.key === 'ArrowLeft' ? -1 : 1)))); tipAt.pinned = true;
    }
  }, true);
  addEventListener('scroll', () => { if (tipAt) requestAnimationFrame(tipPlace); }, { passive: true, capture: true });
  addEventListener('resize', () => tipHide());

  // ---------- карточки графиков: заголовок простыми словами и одна строка вывода с цифрой ----------
  const PER = { 7: 'неделю', 14: '2 недели', 30: 'месяц', 180: '6 месяцев' };
  const PREV = { 7: 'за прошлую неделю', 14: 'за прошлые 2 недели', 30: 'за прошлый месяц', 180: 'за прошлые 6 месяцев' };
  // 6 месяцев — по неделям (Глеб 03.11: «малюсенькие тоненькие палочки сбивают»): столбик = среднее недели (пн–вс)
  const WK = ['recovery', 'sleep', 'need', 'perf', 'strain', 'hrv', 'rhr'];
  function weekly(rows) {
    if (!rows || rows.length <= 60) return rows;
    const out = [];
    let cur = null;
    rows.forEach(r => {
      const d = parseDay(r.day), mon = isoDay(new Date(d.getTime() - ((d.getDay() + 6) % 7) * 864e5));
      if (!cur || cur.day !== mon) { cur = { day: mon, wk: true, n: [], rows: [] }; out.push(cur); }
      cur.rows.push(r);
    });
    return out.map(w => {
      const o = { day: w.day, wk: true, workouts: w.rows.reduce((a, r) => a + (r.workouts || 0), 0) };
      WK.forEach(k => { const xs = w.rows.map(r => r[k]).filter(v => v != null); o[k] = xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null; });
      o.zone = o.recovery == null ? null : o.recovery >= 67 ? 'green' : o.recovery >= 34 ? 'yellow' : 'red';
      const a = parseDay(w.rows[0].day), b = parseDay(w.rows[w.rows.length - 1].day);
      o.tipDay = `неделя ${a.getDate()}${a.getMonth() !== b.getMonth() ? ' ' + MON_S[a.getMonth()] : ''}–${b.getDate()} ${MON_S[b.getMonth()]}`;
      return o;
    });
  }
  const filledN = (rows, k) => rows.filter(r => r[k] != null).length;
  // «В среднем 71% — ▲ на 6% лучше, чем за прошлые 2 недели»; нет прошлого периода — без сравнения (нормы нет, Глеб 03.11);
  // per — длина периода в днях (для 6 месяцев строки уже по неделям)
  function chartLead(rows, prev, key, { fmt, diff, goodUp = null, word = 'в среднем', eps = .5, per } = {}) {
    const a = avg(rows, key), n = rows.length, P = per || n;
    if (a == null) return '<p class="ch-lead"><b>—</b> нет данных за этот период</p>';
    const half = Math.max(3, Math.round(n / 2));
    let ref = null, vsWord = '', same = 'как обычно';
    if (prev && prev.length >= n && filledN(prev, key) >= half && filledN(rows, key) >= half) { ref = avg(prev, key); vsWord = `, чем ${PREV[P] || 'в прошлый раз'}`; same = `так же, как ${PREV[P] || 'в прошлый раз'}`; }
    let cmp = '';
    if (ref != null) {
      const x = a - ref, df = diff || fmt;
      if (Math.abs(x) < eps) cmp = `<span class="ch-cmp flat">${same}</span>`;
      else {
        const up = x > 0, good = goodUp == null ? null : goodUp === up;
        const w = goodUp === true ? (up ? 'лучше' : 'хуже') : goodUp === false ? (up ? 'выше' : 'ниже') : (up ? 'больше' : 'меньше');
        const tail = goodUp === false ? (good ? ' — хорошо' : ' — хуже') : '';
        cmp = `<span class="ch-cmp ${good == null ? 'flat' : good ? 'up' : 'down'}"><i>${up ? '▲' : '▼'}</i>на ${df(Math.abs(x))} ${w}${vsWord}${tail}</span>`;
      }
    }
    return `<p class="ch-lead"><span class="ch-v"><b class="num">${fmt(a)}</b> ${word}</span>${cmp}</p>`;
  }
  const chBlock = (title, lead, chart, foot = '') => `<div class="ch-block"><h3 class="ch-t">${title}</h3>${lead}<div class="chart-box">${chart}</div>${foot ? `<div class="ch-key">${foot}</div>` : ''}</div>`;
  const chartCard = (title, lead, chart, foot = '') => `<section class="card chart-card rise">${chBlock(title, lead, chart, foot)}</section>`;
  const REC_BANDS = [[0, 34, 'низкое'], [34, 67, 'среднее'], [67, 100, 'высокое']];
  const STRAIN_LV = [[0, 10, 'лёгкая'], [10, 14, 'средняя'], [14, 18, 'высокая'], [18, 21, 'макс.']];
  const strainWord = v => v < 10 ? 'лёгкая' : v < 14 ? 'средняя' : v < 18 ? 'высокая' : 'максимальная';
  const recWord = z => ({ green: 'высокое', yellow: 'среднее', red: 'низкое' }[z] || '');
  const zoneDot = z => z ? `var(--zone-${z}, ${C[z]})` : '';
  const kdot = z => `<i class="kdot" style="background:${zoneDot(z)}"></i>`;
  const baseNow = () => (S.data && S.data.today && S.data.today.base) || {};
  // одни и те же графики — во «Здоровье» и в разборах сна, восстановления и нагрузки
  const CHARTS = {
    rec(rows0, prev0, n) {
      const rows = weekly(rows0), prev = weekly(prev0), wk = rows !== rows0;
      const g = rows0.filter(r => r.zone === 'green').length, y = rows0.filter(r => r.zone === 'yellow').length, rd = rows0.filter(r => r.zone === 'red').length;
      const dn = k => `<b class="num">${k}</b> ${plural(k, 'день', 'дня', 'дней')}`;
      return chBlock(`Восстановление за ${PER[n]}`,
        chartLead(rows, prev, 'recovery', { fmt: v => `${Math.round(v)}%`, goodUp: true, per: n }),
        bars(rows, 'recovery', { max: 100, color: r => C[r.zone] || 'var(--hint)', bands: REC_BANDS, fmt: v => `${Math.round(v)}%`, label: `Восстановление за ${PER[n]}`,
          tip: r => [`${Math.round(r.recovery)}%`, `${recWord(r.zone)} восстановление${wk ? ' в среднем' : ''}`, zoneDot(r.zone)] }),
        `<span>${kdot('green')}высокое ${dn(g)}</span><span>${kdot('yellow')}среднее ${dn(y)}</span><span>${kdot('red')}низкое ${dn(rd)}</span>${wk ? '<span>столбик — неделя</span>' : ''}`);
    },
    sleep(rows0, prev0, n) {
      const rows = weekly(rows0), prev = weekly(prev0), wk = rows !== rows0, pf = avg(rows0, 'perf');
      return chBlock(`Сон за ${PER[n]}`,
        chartLead(rows, prev, 'sleep', { fmt: hHuman, diff: v => `${Math.round(v * 60)} мин`, goodUp: true, eps: 5 / 60, per: n }),
        bars(rows, 'sleep', { color: 'var(--sleep)', fmt: hS, need: 'need', label: `Сон за ${PER[n]}`,
          tip: r => [hHuman(r.sleep), r.need ? `нужно было ${r.wk ? 'в среднем ' : ''}${hHuman(r.need)}` : ''] }),
        `<span><i class="ktick"></i>${wk ? 'сколько было нужно в среднем за неделю' : 'сколько было нужно в эту ночь'}</span>${pf != null ? `<span>поспал от нужного в среднем <b class="num">${Math.round(pf)}%</b></span>` : ''}`);
    },
    strain(rows0, prev0, n) {
      const rows = weekly(rows0), prev = weekly(prev0), wk = rows !== rows0;
      const all = rows0.reduce((a, r) => a + (r.workouts || 0), 0);
      return chBlock(`Нагрузка за ${PER[n]}`,
        chartLead(rows, prev, 'strain', { fmt: v => `${num(v, 1)} из 21`, diff: v => `${num(v, 1)} ${Number.isInteger(Math.round(v * 10) / 10) ? plural(Math.round(v), 'балл', 'балла', 'баллов') : 'балла'}`, eps: .05, per: n }),
        bars(rows, 'strain', { max: 21, color: 'var(--strain)', bands: STRAIN_LV, fmt: v => num(v, 1), label: `Нагрузка за ${PER[n]}`,
          tip: r => [`${num(r.strain, 1)} из 21`, `${strainWord(r.strain)} нагрузка${wk ? ' в среднем' : ''}${r.workouts ? ` · ${r.workouts} ${plural(r.workouts, 'тренировка', 'тренировки', 'тренировок')}` : ''}`] }),
        `<span><b class="num">${all}</b> ${plural(all, 'тренировка', 'тренировки', 'тренировок')} за ${PER[n]}</span><span>шкала нагрузки — от 0 до 21</span>${wk ? '<span>столбик — неделя</span>' : ''}`);
    },
    hrv(rows0, prev0, n) {
      const rows = weekly(rows0), prev = weekly(prev0), fmt = v => `${Math.round(v)} мс`;
      return chBlock(`Гибкость пульса за ${PER[n]}`, chartLead(rows, prev, 'hrv', { fmt, goodUp: true, per: n }),
        line(rows, 'hrv', 'var(--rec-blue)', null, { fmt, label: `Гибкость пульса за ${PER[n]}` }), `<span>Отдых организма по пульсу во сне. Выше — лучше${rows !== rows0 ? ' · точка — неделя' : ''}</span>`);
    },
    rhr(rows0, prev0, n) {
      const rows = weekly(rows0), prev = weekly(prev0), fmt = v => `${Math.round(v)} уд/мин`;
      return chBlock(`Пульс покоя за ${PER[n]}`, chartLead(rows, prev, 'rhr', { fmt, goodUp: false, per: n }),
        line(rows, 'rhr', 'var(--rec-blue)', null, { fmt, label: `Пульс покоя за ${PER[n]}` }), `<span>Ниже — лучше: сердцу легче, ты отдохнул${rows !== rows0 ? ' · точка — неделя' : ''}</span>`);
    },
  };
  // самый длинный загруженный ряд (все кончаются сегодняшним днём) и прошлый такой же период для сравнения
  function longest() {
    let best = (S.data && S.data.series) || [];
    Object.values(S.series || {}).forEach(r => { if (r && r.length > best.length) best = r; });
    return best;
  }
  const prevAsked = new Set();
  function prevOf(n) {
    const all = longest(), want = Math.min(180, n * 2);
    if (all.length < want && !prevAsked.has(want)) { prevAsked.add(want); loadSeries(want, true); }
    return all.length >= 2 * n ? all.slice(-2 * n, -n) : null;
  }
  const avg = (rows, k) => { const v = rows.map(r => r[k]).filter(x => x != null); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
  const retryCard = (title, act) => `<section class="card"><div class="empty"><span class="e">⚠️</span><b>${title}</b>Проверь интернет и попробуй ещё раз.</div>
    <button class="btn tonal" data-a="${act}">Повторить</button></section>`;
  const skel = (n = 3) => Array.from({ length: n }, (_, i) => `<div class="sk line" style="width:${[92, 78, 86, 60][i % 4]}%"></div>`).join('');

  // ---------- каркас ----------
  // нижнее меню (Глеб 03.11: «Добавить» — четвёртой, на третье — «Питание»): Главная · Здоровье · Питание · «＋» Добавить
  // и круг «Чат» справа (универсальный собеседник: тренер, врач-консультант, психолог, учёба). id вкладки чата — 'coach'.
  const TABS = [{ id: 'today', icon: I.home, label: 'Главная' }, { id: 'trends', icon: I.health, label: 'Здоровье' },
    { id: 'food', icon: I.food, label: 'Питание' }, { id: 'plus', icon: I.plus, label: 'Добавить' }, { id: 'coach', icon: I.coach, label: 'Чат' }];
  let tabs;
  // широкий экран (ноутбук, монитор, iPad боком): меню — колонкой слева, карточки — в 2–3 колонки (layout.css)
  const WIDE = matchMedia('(min-width: 1024px)'), WIDE3 = matchMedia('(min-width: 1600px)');
  const wide = () => WIDE.matches;
  // подложка активной вкладки переезжает пружиной (меню перерисовывается целиком — помним, откуда ехать)
  function tabInd() {
    const bar = $('#tabs'); if (!bar) return;
    const side = wide();
    const on = bar.querySelector(side ? 'button.on:not([data-tab="plus"])' : 'button.on:not([data-tab="coach"]):not([data-tab="plus"])');
    let ind = bar.querySelector('.tab-ind');
    const place = () => { ind.style.transform = `translate(${S.indX}px,${S.indY}px)`; ind.style.width = S.indW + 'px'; ind.style.height = side ? S.indH + 'px' : ''; };
    if (!ind) {
      ind = document.createElement('i'); ind.className = 'tab-ind'; bar.append(ind);
      if (S.indX != null && S.indSide === side) { ind.style.transition = 'none'; place(); ind.style.opacity = S.indO; void ind.offsetWidth; ind.style.transition = ''; }
    }
    if (!on) { ind.style.opacity = 0; S.indO = 0; return; }
    if (S.indSide !== side) { ind.style.transition = 'none'; requestAnimationFrame(() => { ind.style.transition = ''; }); }
    S.indSide = side; S.indX = on.offsetLeft; S.indY = side ? on.offsetTop : 0; S.indW = on.offsetWidth; S.indH = on.offsetHeight; S.indO = 1;
    place(); ind.style.opacity = 1;
  }
  function go(tab) {
    if (tab === 'plus') { tabs.set(S.tab); plusSheet(); return; }
    if (S.tab === tab && !S.screen) { window.scrollTo({ top: 0, behavior: 'smooth' }); return; }
    const from = S.tab;
    if (tab === 'coach' && F.coFresh) F.coFresh();  // 30 мин тишины — новый разговор
    S.tab = tab; S.screen = null; tabs.set(tab);
    // «Питание» — живая рамка «Калорий» (kcal.js): без снимка всей страницы с рамкой, она сама проявляется (kcal.css)
    nav('tab', () => { render(true); window.scrollTo(0, 0); }, from === 'food' || tab === 'food');
    if (tab === 'trends' && !S.series[S.range]) loadSeries(S.range);
    if (tab === 'trends' && S.progress === undefined) loadProgress();
    if (tab === 'coach' && !S.history) loadHistory();
  }
  // Тихое обновление без мигания (Глеб 02.11: «проверь все панели на дёргания»): вместо замены innerHTML
  // меняем только то, что отличается. Узлы, стили колец/полос (выставлены JS) и поля ввода остаются на месте.
  function morphNode(a, b) {
    if (a.nodeType !== b.nodeType || a.nodeName !== b.nodeName) { a.replaceWith(b); return; }
    if (a.nodeType === 3 || a.nodeType === 8) { if (a.nodeValue !== b.nodeValue) a.nodeValue = b.nodeValue; return; }
    if (a.nodeType !== 1) return;
    for (const { name } of [...a.attributes]) {
      if (b.hasAttribute(name)) continue;
      // стиль, выставленный JS для анимации (кольца, ширина и высота полос) — не сбрасываем; остальной (ширины заглушек) — снять
      if (name === 'style' && (b.hasAttribute('data-off') || b.hasAttribute('data-w') || b.hasAttribute('data-h') || a.classList.contains('arc') || a.classList.contains('w-in'))) continue;
      a.removeAttribute(name);
    }
    for (const { name, value } of [...b.attributes]) {
      if (a.getAttribute(name) === value) continue;
      if (name === 'data-off' && a.style.strokeDashoffset === value) continue;  // кольцо уже на месте — не запускать заново
      if (name === 'data-w' && a.style.width === value) continue;
      if (name === 'style' && a.style.strokeDashoffset && b.style && !b.style.strokeDashoffset) { a.setAttribute(name, value); a.style.strokeDashoffset = a.style.strokeDashoffset; continue; }
      a.setAttribute(name, value);
    }
    if ((a.nodeName === 'INPUT' || a.nodeName === 'TEXTAREA' || a.nodeName === 'SELECT') && document.activeElement === a) return;  // печатает — не трогаем
    if (a.nodeName === 'INPUT' && a.type !== 'file' && a.value !== b.value && !a.matches(':focus')) a.value = b.value;
    morphKids(a, b);
  }
  function morphKids(a, b) {
    const ac = [...a.childNodes].filter(n => !(n.nodeType === 1 && n.classList.contains('sheet-x'))), bc = [...b.childNodes];
    for (let i = 0; i < bc.length; i++) { if (i < ac.length) morphNode(ac[i], bc[i]); else a.appendChild(bc[i]); }
    for (let i = bc.length; i < ac.length; i++) ac[i].remove();
  }
  // morph(el, html) — поменять содержимое el; morph(el, html, true) — сам el (вместо outerHTML)
  function morph(el, html, outer = false) {
    if (!el) return el;
    const t = document.createElement('template'); t.innerHTML = html;
    if (outer) { const n = t.content.firstElementChild; if (!n) { el.remove(); return null; } morphNode(el, n); return el.isConnected ? el : n; }
    const box = el.cloneNode(false); box.append(t.content); morphKids(el, box); return el;
  }
  function render(anim) {
    if (!S.data) return;
    const html = S.screen ? screenView(S.screen) : (F.views[S.tab] || { today: todayView, trends: trendsView, food: () => '', coach: coachView }[S.tab])();
    view.removeAttribute('aria-busy');
    tipHide();  // подсказка графика не висит над новым экраном
    const rz =(S.dayData || S.data.today || {}).recovery;  // цвет восстановления — в живой фон
    document.documentElement.style.setProperty('--zc', rz ? C[rz.zone] : 'var(--rec-blue)');
    // «назад» возвращает прежний экран как был — без повторного набегания цифр и колец
    const moving = (anim || S.navKind || S.inVT) && !S.popping;
    const cls = [S.inVT ? 'enter vt' : S.navKind ? `enter nav-${S.navKind}` : anim ? 'enter' : '', moving ? '' : 'calm'].join(' ').trim();
    const cur = view.firstElementChild;
    if (!moving && cur && cur.parentNode === view && view.childElementCount === 1) { morph(cur, `<div class="${cls}">${html}</div>`, true); }  // тихо: только отличия
    else view.innerHTML = `<div class="${cls}">${html}</div>`;
    document.body.classList.toggle('tab-coach', S.tab === 'coach' && !S.screen);
    document.body.classList.toggle('tab-food', S.tab === 'food' && !S.screen);  // рамка «Калорий» (kcal.js) видна только на своей вкладке
    if (moving) stagger();
    animate(document, !moving);
    if (F.navSync) F.navSync();
    if (F.coLayout) F.coLayout(S.coForce);  // чат тренера: своя прокрутка сообщений (more.js), страница стоит
    S.coForce = false;
  }
  const initial = () => { const n = ((S.data && S.data.user && S.data.user.name) || '').trim(); return n ? esc(n[0].toUpperCase()) : I.user; };
  const avatar = () => `<button class="avatar" data-a="profile" aria-label="Профиль">${initial()}</button>`;
  function hero(title, sub, end = '') {
    return `<header class="hero">${avatar()}<div class="titles"><h1>${title}</h1><div class="sub">${sub}</div></div>
      <div class="end">${end}</div></header>`;
  }
  function dateWord(s) {
    const d = parseDay(s), t = S.data.today.day;
    return s === t ? 'Сегодня' : isoDay(new Date(parseDay(t).getTime() - 864e5)) === s ? 'Вчера' : WD[d.getDay()];
  }
  const dateLine = s => { const d = parseDay(s); return `${d.getDate()} ${MON[d.getMonth()]}`; };
  const head = (title, right = '') => `<div class="head"><h2>${title}</h2>${right ? `<div class="head-r">${right}</div>` : ''}</div>`;

  // ---------- «На сегодня»: сон · вода · еда ----------
  function todayPlan(t) {
    const d = S.data, rg = d.ext && d.ext.regime, rt = rg && rg.saved && rg.today, bt = t.bedtime || {};
    const rows = [];
    const row = (ic, hc, title, meta, attrs, end = '<span class="chev"></span>') => `<${attrs ? 'button' : 'div'} class="row${attrs ? ' tap' : ''} tp-row" ${attrs || ''}>
        <span class="ic" style="--hc:${hc}">${ic}</span><span class="main"><div class="title">${title}</div>${meta ? `<div class="meta">${meta}</div>` : ''}</span>
        <span class="end">${end}</span></${attrs ? 'button' : 'div'}>`;
    const bed = rt ? rt.bed : bt.bed, wake = rt ? rt.wake : bt.wake;
    if (bed) rows.push(row(I.moon, 'var(--sleep)', `Лечь в <b class="num">${esc(bed)}</b>`,
      [wake ? `подъём ${esc(wake)}` : '', bt.need ? `сон ${hm(bt.need)}` : ''].filter(Boolean).join(' · '),
      rt ? 'data-a="rg-open"' : 'data-a="detail" data-v="sleep"'));
    const w = t.water;
    if (w && w.target) {
      const left = Math.max(0, w.target - w.drank);
      rows.push(left > 0
        ? row(I.drop, 'var(--water)', `Выпить ещё <b class="num">${liters(left)}</b>`, esc(w.short || `выпито ${liters(w.drank)} из ${liters(w.target)}`), '',
          '<button class="pill tp-add num" data-a="water" data-v="250" aria-label="Вода +250 мл">+250</button>')
        : row(I.drop, 'var(--water)', 'Вода — норма есть', `выпито ${liters(w.drank)}`, ''));
    }
    const g = foodGoals(t), e = t.eaten || {};
    if (g && g.kcal) {
      const kl = Math.round(g.kcal - (e.kcal || 0)), pl = Math.round((g.protein || 0) - (e.protein || 0));
      rows.push(kl > 50
        ? row(I.food, 'var(--food)', `Съесть ещё <b class="num">${big(kl)}</b> ккал`, g.protein ? (pl > 0 ? `белок — ещё ${pl} г` : 'белок — норма есть') : '', 'data-a="tab" data-v="food"')
        : row(I.food, 'var(--food)', 'Еда — норма есть', g.protein && pl > 0 ? `белок — ещё ${pl} г` : `съедено ${big(e.kcal)} ккал`, 'data-a="tab" data-v="food"'));
    }
    const links = `<div class="olinks">${t.brief || (t.tips || []).length ? '<button class="link" data-a="detail" data-v="rec">Весь разбор</button>' : !S.briefing ? '<button class="link" data-a="brief">Разобрать сейчас</button>' : ''}
        <button class="coach-link" data-a="tab" data-v="coach">${I.coach}Спросить в чате</button></div>`;
    return `<section class="card outlook tplan rise"><h3 class="ot">На сегодня</h3>
      ${rows.length ? `<div class="list plain tp-list">${rows.join('')}</div>` : '<p class="muted">Данных на сегодня пока нет — они придут с WHOOP.</p>'}${links}</section>`;
  }

  // ---------- Сегодня ----------
  function connectCard() {
    const d = S.data;
    if (d.keys) {
      return `<section class="card feature rise"><div class="feat-ic">${I.plug}</div><h2 class="title-lg">WHOOP не подключён</h2>
        <p class="muted">Подключи браслет — каждое утро будет план: как тренироваться, есть и спать.</p>
        <button class="btn primary" data-a="connect">Подключить WHOOP</button></section>`;
    }
    return `<section class="card feature rise"><div class="feat-ic">${I.plug}</div><h2 class="title-lg">Подключим WHOOP</h2>
      <p class="muted">Один раз, 3 минуты — дальше всё само.</p>
      <ol class="steps">
        <li>Открой <b>developer-dashboard.whoop.com</b> и войди аккаунтом WHOOP.</li>
        <li>Создай Team, затем <b>Create App</b>. Privacy Policy: <b>sawqws.github.io/123/privacy.html</b>, отметь все 6 <b>read:*</b>.</li>
        <li>Redirect URL — нажми, чтобы скопировать:<button class="code" data-a="copy" data-v="${esc(d.redirect)}">${esc(d.redirect || 'адрес ещё готовится…')}</button></li>
        <li>Пришли боту: <span class="code">/whoop CLIENT_ID CLIENT_SECRET</span></li></ol>
      <button class="btn primary" data-a="open" data-v="https://developer-dashboard.whoop.com/apps/create">Открыть WHOOP Developer</button></section>`;
  }

  function waterCard(w, full) {
    if (!w) return '';
    // Глеб 03.11: «больше градаций — кружки у меня больше 250» — ряд листается вбок, на широком экране — в 2 строки
    const q = [[150, 'глоток'], [200, 'стакан'], [250, 'стакан'], [300, 'кружка'], [350, 'кружка'], [400, 'кружка'],
      [500, 'бутылка'], [750, 'бутылка'], ...(full ? [[1000, 'литр']] : [])];
    return `<section class="card rise">${head(`<span class="hico" style="--hc:var(--water)">${I.drop}</span>Вода`, `<span class="num"><b>${liters(w.drank)}</b> / ${liters(w.target)}</span>`)}
      ${bar(pct(w.drank, w.target), 'var(--water)')}
      ${w.note ? `<p class="small muted">${esc(w.note)}</p>` : ''}
      <div class="btns wq">${q.map(([ml, k]) => `<button class="btn tonal water-b" data-a="water" data-v="${ml}" aria-label="Вода +${ml} мл"><b class="num">+${ml}</b><span>${k}</span></button>`).join('')}</div>
      ${full && w.log.length ? `<div class="logs">${w.log.map(x => x.src === 'kcal'
        ? `<span class="pill num kc" title="Записано в «Калориях»">${esc(x.time)} · ${x.ml} мл<i>Калории</i></span>`
        : `<span class="pill num">${esc(x.time)} · ${x.ml} мл<button data-a="water-del" data-v="${x.id}" aria-label="Удалить">✕</button></span>`).join('')}</div>` : ''}
      ${full ? `<p class="small muted">Норма: ${esc(w.why)}. Боту можно писать «выпил 500 мл».</p>` : ''}</section>`;
  }

  // цели дня: из «Калорий» (если связаны), иначе — наш расчёт по тратам
  function foodGoals(t) {
    const k = t.kcal && t.kcal.goals, n = t.nutrition;
    if (k && k.kcal) return { kcal: k.kcal, protein: k.protein, fat: k.fat, carbs: k.carbs, salt: k.salt, water: k.water, src: 'kcal' };
    return n ? { kcal: n.kcal, protein: n.protein, fat: n.fat, carbs: n.carbs, salt: null, water: n.water, src: 'forma' } : null;
  }
  function foodMini(t) {
    const n = foodGoals(t), e = t.eaten;
    const right = n ? `<span class="num"><b>${big(e.kcal)}</b> / ${big(n.kcal)} ккал</span>` : `<span class="num"><b>${big(e.kcal)}</b> ккал</span>`;
    return `<section class="card rise tap" data-a="tab" data-v="food" role="button">${head(`<span class="hico" style="--hc:var(--food)">${I.food}</span>Питание`, right + '<span class="chev"></span>')}
      ${n ? bar(pct(e.kcal, n.kcal), 'var(--food)') : ''}
      <div class="macros num">${n ? `<span>Б <b>${e.protein}</b>/${n.protein}</span><span>Ж <b>${e.fat}</b>/${n.fat}</span><span>У <b>${e.carbs}</b>/${n.carbs} г</span>`
        : `<span>${e.count ? `${e.count} ${plural(e.count, 'приём', 'приёма', 'приёмов')}` : 'Пока ничего — пришли фото еды'}</span>`}${e.salt != null ? `<span>соль <b>${num(e.salt, 1)}</b>${n && n.salt ? `/${num(n.salt, 0)}` : ''} г</span>` : ''}</div>
      ${t.kcal ? `<p class="kc-from">из «Калорий»${t.kcal.updated ? ` · ${clock(t.kcal.updated)}` : ''}</p>` : ''}</section>`;
  }

  function delta(v, base, goodUp, unit = '') {
    if (v == null || base == null) return '<span class="muted">нет нормы</span>';
    const d = v - base;
    if (Math.abs(d) < .5) return '<span class="muted">как обычно</span>';
    const good = goodUp ? d > 0 : d < 0;
    return `<span class="${good ? 'up' : 'down'}">${d > 0 ? '↑' : '↓'} ${num(Math.abs(d))}${unit}</span>`;
  }
  // стрелка к норме за 30 дней: зелёная — хорошо, красная — плохо, серая — ни то ни другое
  function vs(v, b, goodUp, d = 0, unit = '') {
    if (v == null || b == null) return '';
    const x = v - b;
    if (Math.abs(x) < (d ? .05 : .5)) return '<span class="vs flat">= норма</span>';
    const cls = goodUp == null ? 'flat' : (goodUp ? x > 0 : x < 0) ? 'up' : 'down';
    return `<span class="vs ${cls}"><i>${x > 0 ? '▲' : '▼'}</i>${num(Math.abs(x), d)}${unit}</span>`;
  }
  // серия: сколько дней подряд браслет на руке (есть сон, восстановление или нагрузка), как «Streak» в WHOOP
  function streak() {
    if (S.data.streak && S.data.streak.n != null) return { n: S.data.streak.n, full: false, since: S.data.streak.since };  // сервер считает по всей истории
    const ser = S.data.series || [], has = r => r.recovery != null || r.sleep != null || r.strain != null;
    let i = ser.length - 1, n = 0;
    if (i >= 0 && !has(ser[i])) i--;  // сегодня WHOOP мог ещё не прислать — серия не рвётся
    for (; i >= 0 && has(ser[i]); i--) n++;
    return { n, full: n > 0 && n >= ser.length - 1 };
  }
  const cap = (title, right = '') => `<div class="cap"><h3>${title}</h3>${right ? `<span class="cap-r">${right}</span>` : ''}</div>`;

  // «Strain Target»: шкала 0–21 с подписанными уровнями, цель на сегодня (светлый отрезок) и где ты сейчас (метка с числом)
  const STRAIN_BANDS = [[0, 10, 'Лёгкая'], [10, 14, 'Средняя'], [14, 18, 'Высокая'], [18, 21, 'Макс.']];
  function strainScale(t, sv) {
    const band = t.band, p = v => `${pct(v, 21).toFixed(1)}%`;
    const edge = v => pct(v, 21) < 14 ? ' l' : pct(v, 21) > 86 ? ' r' : '';  // у краёв подпись не вылезает за шкалу
    const mid = band ? (band[0] + band[1]) / 2 : null, near = band && sv != null && Math.abs(mid - sv) < 5.5;
    const inBand = band && sv != null && sv >= band[0] && sv <= band[1];
    const now = sv != null ? `<span class="s21-l now${edge(sv)}" style="left:${p(sv)}">сейчас <b class="num">${num(sv, 1)}</b>${near && inBand ? ' · в цели' : ''}</span>` : '';
    const goal = band && !(near && sv != null) ? `<span class="s21-l goal${edge(mid)}" style="left:${p(mid)}">цель <b class="num">${band[0]}–${band[1]}</b></span>` : '';
    return `<div class="s21" role="img" aria-label="Нагрузка ${sv != null ? num(sv, 1) : 'нет'} из 21${band ? `, цель ${band[0]}–${band[1]}` : ''}">
      <div class="s21-top">${goal}${now}</div>
      <div class="scale21">${[10, 14, 18].map(v => `<i class="div21" style="left:${p(v)}"></i>`).join('')}
        ${band ? `<span class="band21" style="left:${p(band[0])};width:calc(${p(band[1])} - ${p(band[0])})"></span>` : ''}
        ${sv != null ? `<span class="mark21" style="left:${p(sv)}"></span>` : ''}</div>
      <div class="s21-ax num">${[0, 10, 14, 18, 21].map(v => `<span style="left:${p(v)}">${v}</span>`).join('')}</div>
      <div class="ticks">${STRAIN_BANDS.map(([a, b, k]) => `<span style="flex:${b - a}">${k}</span>`).join('')}</div></div>`;
  }
  // карточка «Цель нагрузки»: одна строка вывода — сколько ещё можно или что уже хватит
  function strainTarget(t, sv) {
    const band = t.band;
    let lead = '';
    if (sv == null) lead = '<span class="ch-v"><b class="num">—</b> нагрузка ещё не посчитана</span>';
    else if (!band) lead = `<span class="ch-v"><b class="num">${num(sv, 1)}</b> из 21 сейчас</span>`;
    else if (sv < band[0]) lead = `<span class="ch-v"><b class="num">${num(sv, 1)}</b> из 21 сейчас</span><span class="ch-cmp flat">до цели ещё ${num(band[0] - sv, 1)} — можно тренироваться</span>`;
    else if (sv <= band[1]) lead = `<span class="ch-v"><b class="num">${num(sv, 1)}</b> из 21 — ты в цели</span><span class="ch-cmp up"><i>▲</i>хорошая нагрузка на сегодня</span>`;
    else lead = `<span class="ch-v"><b class="num">${num(sv, 1)}</b> из 21 — выше цели</span><span class="ch-cmp down"><i>▲</i>на ${num(sv - band[1], 1)} больше — на сегодня хватит</span>`;
    return `<section class="card rise"><h3 class="ch-t">Цель нагрузки на ${t.day === S.data.today.day ? 'сегодня' : 'этот день'}</h3><p class="ch-lead">${lead}</p>${strainScale(t, sv)}
      ${band ? '<p class="small muted">Цель зависит от восстановления: чем лучше отдохнул, тем больше можно.</p>' : ''}</section>`;
  }
  // «Tonight's Sleep»: сколько нужно и во сколько лечь
  function sleepPlanner(t) {
    // планер сна из «Моего плана» (режим Пик/Норма/Минимум) главнее простого расчёта insights.bedtime
    const pl = (S.data.ext && S.data.ext.plan && S.data.ext.plan.planner) || null, b = t.bedtime;
    if (!pl && !b) return '';
    const need = pl ? pl.need : b.need, bed = pl ? pl.bed : b.bed, wake = pl ? pl.wake : b.wake;
    const mode = pl ? ((pl.options || []).find(o => o.mode === pl.mode) || {}).name : '';
    return `<section class="card rise tap" data-a="${F.screens.plan ? 'plan-open' : 'detail'}" data-v="sleep" role="button">${cap('Сон сегодня', `${mode ? esc(mode) : ''}<span class="chev"></span>`)}
      <div class="tonight"><div><span class="tk">Нужно сна</span><b class="num" style="color:var(--teal)">${hm(need)}</b></div>
        <div><span class="tk">Лечь в</span><b class="num">${esc(bed)}</b></div>
        <div><span class="tk">Подъём${!pl && b.weekend ? ' · вых.' : ''}</span><b class="num">${esc(wake)}</b></div></div></section>`;
  }
  // «My Day»: сон и тренировки за день по порядку
  function myDay(t) {
    const s = t.sleep, rows = [];
    if (s) rows.push(`<button class="act" data-a="detail" data-v="sleep"><span class="act-ic" style="--c:var(--sleep)">${I.moon}</span>
      <span class="act-m"><b>Сон</b><span class="num">${esc(s.start)} – ${esc(s.end)}</span></span>
      <span class="act-v num" style="--c:var(--sleep)">${hm(s.hours)}</span></button>`);
    (t.workouts || []).forEach(w => rows.push(`<button class="act" data-a="workout" data-v="${esc(w.id)}"><span class="act-ic" style="--c:var(--strain)">${sportIcon(w.sport)}</span>
      <span class="act-m"><b>${esc(w.sport)}</b><span class="num">${esc(w.start)} · ${w.minutes || '—'} мин${w.kcal ? ` · ${big(w.kcal)} ккал` : ''}</span></span>
      <span class="act-v num" style="--c:var(--strain)">${w.strain != null ? num(w.strain, 1) : '—'}</span></button>`));
    return `<div class="sec rise"><div class="cap"><h3>Мой день</h3><button class="addsq" data-a="tab" data-v="plus" aria-label="Добавить">${I.plus}</button></div>
      <button class="outrow" data-a="${document.documentElement.classList.contains('wh') ? 'outlook' : 'detail'}" data-v="rec"><span>${t.day === S.data.today.day ? 'Прогноз на день' : 'Разбор дня'}</span>${I.next}</button>
      <div class="card acts"><div class="acts-h"><b>Активности</b><span class="num">${rows.length || ''}</span></div>${rows.length ? rows.join('') : '<p class="muted small">Сна и тренировок за этот день пока нет — WHOOP пришлёт их после записи.</p>'}</div></div>`;
  }
  // «My Dashboard»: плитки показателей со стрелкой к норме за 30 дней
  // HRV — «Гибкость пульса» (Глеб 04.11: «красивее, понятнее и по-русски»; старые названия — в words.py) — у названия везде короткая подпись, что это
  const HRV_WHAT = 'отдых организма по пульсу во сне', what = s => `<small class="what">${s}</small>`;
  function tile({ k, v, d = 0, unit = '', base, goodUp, ex, sub, hint }) {
    return `<button class="tile" data-a="explain" data-v="${ex}"><span class="tile-k">${k}</span>
      <span class="tile-v"><b class="num">${v == null ? '—' : cnt(v, d)}</b><small>${unit}</small></span>
      <span class="tile-d">${vs(v, base, goodUp, d, unit && unit !== '%' ? ' ' + unit : unit) || `<span class="vs flat">${sub || (base != null ? `норма ${num(base, d)}` : 'нет нормы')}</span>`}</span>${hint ? what(hint) : ''}</button>`;
  }
  function dashboard(t) {
    const r = t.recovery || {}, s = t.sleep || {}, c = t.cycle || {}, b = t.base || {};
    return `<div class="sec rise">${cap('Моя панель', 'к норме за 30 дней')}<div class="tiles">
      ${tile({ k: 'Гибкость пульса', v: r.hrv, unit: 'мс', base: b.hrv, goodUp: true, ex: 'hrv', hint: HRV_WHAT })}
      ${tile({ k: 'Пульс покоя', v: r.rhr, unit: 'уд/мин', base: b.rhr, goodUp: false, ex: 'rhr' })}
      ${tile({ k: 'Дыхание во сне', v: s.resp, d: 1, unit: 'в мин', base: b.resp, goodUp: null, ex: 'resp' })}
      ${tile({ k: 'Кислород в крови', v: r.spo2, d: 1, unit: '%', goodUp: true, ex: 'spo2', sub: 'норма 95–100%' })}
      ${tile({ k: 'Температура кожи', v: r.skin_temp, d: 1, unit: '°C', base: b.skin_temp, goodUp: null, ex: 'temp' })}
      ${tile({ k: 'Калории', v: c.kcal, unit: 'ккал', base: b.kcal, goodUp: null, ex: 'kcal' })}</div></div>`;
  }

  function todayView() {
    const d = S.data, t = S.dayData || d.today, isToday = t.day === d.today.day;
    const r = t.recovery, s = t.sleep, c = t.cycle;
    if (!d.connected && !d.today.has_data) {
      return hero('Форма', 'Тренер по данным WHOOP') + connectCard() + waterCard(d.today.water) + foodMini(d.today) + features() + Kit.supportRow('/api/support') + chatRow();
    }
    const synced = d.connected && d.synced ? `Данные WHOOP — ${clock(d.synced)}` : d.connected ? 'Данные WHOOP ещё не загружены' : '';
    const z = r ? r.zone : null;
    const recC = z ? C[z] : 'var(--hint)';
    const sleepP = s ? (s.performance != null ? s.performance / 100 : s.need ? s.hours / s.need : 0) : 0;
    const sleepV = s ? (s.performance != null ? s.performance : s.need ? Math.round(s.hours / s.need * 100) : null) : null;
    const st = streak(), dd = parseDay(t.day);
    // шапка как в WHOOP: аватар с серией дней · дата в капсуле · справа — когда пришли данные (заряда браслета API не даёт)
    let h = `<header class="wbar"><button class="mepill" data-a="profile" aria-label="Профиль"><span class="avatar">${initial()}</span>
        ${st.n ? `<span class="fl" title="Серия: дней подряд с браслетом">${I.flame}<b class="num">${st.n}${st.full ? '+' : ''}</b></span>` : ''}</button>
      <div class="datecap"><button class="pg" data-a="day" data-v="-1" aria-label="Предыдущий день">${I.back}</button>
        <span class="dc-t">${isToday ? 'Сегодня' : `${WD_S[dd.getDay()]}, ${dd.getDate()} ${MON_S[dd.getMonth()]}`}</span>
        <button class="pg" data-a="day" data-v="1" aria-label="Следующий день" ${isToday ? 'disabled' : ''}>${I.next}</button></div>
      <button class="syncpill" data-a="sync" aria-label="Обновить данные WHOOP">${I.sync}<span class="num">${d.synced ? clock(d.synced) : '—'}</span></button></header>
      <div class="wordmark">ФОРМА</div>`;
    h += slot('homeTop', t, isToday);
    h += `<section class="dials rise" data-k="${esc(t.day)}-${sleepV}-${r ? r.score : ''}-${c ? c.strain : ''}">
        ${dial(sleepP, 'var(--sleep)', sleepV != null ? `<b class="num">${cnt(sleepV)}<small>%</small></b>` : '<b>—</b>', 'Сон', '', 'sleep')}
        ${dial(r ? r.score / 100 : 0, recC, r ? `<b class="num">${cnt(r.score)}<small>%</small></b>` : '<b>—</b>', 'Восстановление', '', 'rec')}
        ${dial(c && c.strain != null ? c.strain / 21 : 0, 'var(--strain)', c && c.strain != null ? `<b class="num">${cnt(c.strain, 1)}</b>` : '<b>—</b>', 'Нагрузка', '', 'strain')}
      </section>`;
    h += slot('homeAfterDials', t, isToday);

    // «На сегодня» (Глеб 05.11: прежняя карточка с разбором — «максимально непонятная, много текста, зачем мне эти данные»):
    // три дела дня с цифрами — когда лечь, сколько ещё воды, сколько ещё еды; строка ведёт к своему экрану. Без оценки дня
    // и советов по тренировке, цифры колец не повторяет. Прошлые дни — как было: начало разбора дня
    if (isToday) h += todayPlan(t);
    else {
      const adv = advice(t);
      h += `<section class="card outlook rise"><h3 class="ot">Разбор дня</h3>${adv ? `<div class="prose advice">${adv}</div>` : '<p class="muted">Разбора за этот день нет.</p>'}</section>`;
    }

    S.outlookHtml = h.slice(h.lastIndexOf('<section class="card outlook'));  // вид WHOOP: совет дня — листом из строки «Прогноз на день»
    const ms = monStat(t), msOk = ms.all && ms.ok === ms.all, sv = c && c.strain != null ? c.strain : null;
    h += `<div class="mons rise"><button class="mon" data-a="tab" data-v="trends"><span class="mon-k">Монитор здоровья${I.next}</span>
        <span class="mon-s"><i class="mon-b" style="--c:${!ms.all ? 'var(--hint)' : msOk ? 'var(--teal)' : ms.ok >= ms.all - 1 ? C.yellow : C.red}">${msOk ? I.check : '!'}</i>
        <span><b style="color:${!ms.all ? 'var(--hint)' : msOk ? 'var(--teal)' : ms.ok >= ms.all - 1 ? C.yellow : C.red}">${!ms.all ? 'Нет данных' : msOk ? 'В норме' : 'Обрати внимание'}</b>${ms.all ? `${ms.ok}/${ms.all} показателей` : 'нужна неделя'}</span></span></button>
      <button class="mon" data-a="detail" data-v="strain"><span class="mon-k">Цель нагрузки${I.next}</span>
        <span class="mon-s"><i class="mon-b num" style="--c:var(--strain)">${sv != null ? num(sv, 1) : '—'}</i>
        <span><b style="color:var(--strain)">${t.band ? `${t.band[0]}–${t.band[1]}` : 'Нет цели'}</b>${t.band && sv != null ? (sv < t.band[0] ? 'можно ещё' : sv <= t.band[1] ? 'в цели' : 'выше цели') : 'по восстановлению'}</span></span></button></div>`;
    h += slot('homeAfterOutlook', t, isToday);
    h += myDay(t);
    h += slot('homeAfterMyDay', t, isToday);
    if (isToday) h += sleepPlanner(t);
    if (isToday) h += `<div class="sec rise">${cap('Мой дневник')}${slot('homeJournal', t, isToday)}<div class="pair">${waterCard(t.water)}${foodMini(t)}</div></div>`;
    h += dashboard(t);
    h += slot('homeAfterDashboard', t, isToday);
    h += `<button class="card summary-entry rise tap" data-a="summary" data-v="week"><span class="se-ic">${I.stats}</span><span class="se-main"><b>Оценка и советы</b>
      <span class="muted">день · неделя · месяц · всё время</span></span><span class="chev"></span></button>`;
    if (isToday && d.connected) h += `<p class="foot">${synced} · <button class="link" data-a="sync">Обновить</button></p>`;
    if (isToday) h += Kit.supportRow('/api/support') + chatRow();
    return h;
  }
  // «💬 Перейти в чат» (внизу «Сегодня» и в профиле): режим «чат» — бот присылает главное меню, приложение закрывается
  function chatRow() {
    const m = S.data && S.data.mode;
    return `<div class="list mode-list"><button class="row tap" data-a="tochat"><span class="ic">${I.coach}</span><span class="main"><div class="title">Перейти в чат</div>
      <div class="meta">${m === 'chat' ? 'режим чата уже включён' : 'бот перейдёт в режим чата: всё кнопками там'}</div></span><span class="end chev"></span></button></div>`;
  }
  function features() {
    const row = (ic, t, s) => `<div class="row"><span class="ic">${ic}</span><div class="main"><div class="title">${t}</div><div class="meta">${s}</div></div></div>`;
    return `<div class="label">Что умеет Форма</div><div class="list rise">${row(I.today, 'Утренний план', 'Готовность, тренировка, отбой')}
      ${row(I.bolt, 'После тренировки', 'Что съесть и как восстановиться')}${row(I.food, 'Фото еды', 'Ккал и БЖУ за секунды')}${row(I.run, 'Видео упражнения', 'Разбор техники')}</div>`;
  }

  // ---------- расшифровки: что значит цифра и как она у тебя ----------
  const ABOUT = {
    rec: { t: 'Что это', p: ['Восстановление — насколько организм отдохнул и готов к нагрузке сегодня. WHOOP считает его ночью по гибкости пульса, пульсу в покое, дыханию и сну.',
      '🟢 67–100% — можно тренироваться в полную силу, 🟡 34–66% — умеренно, 🔴 0–33% — лучше лёгкий день и отдых.'] },
    sleep: { t: 'Что это', p: ['Сон — сколько ты спал и сколько тебе было нужно (с учётом вчерашней нагрузки и недосыпа). «Выполнение» — сколько процентов от нужного ты поспал: 85% и выше — хорошо.',
      'Глубокий сон восстанавливает мышцы, REM — голову и память. Режим — насколько ровно ты ложишься и встаёшь каждый день.'] },
    strain: { t: 'Что это', p: ['Нагрузка — сколько за день потрудились сердце и мышцы, по шкале от 0 до 21. Считается по пульсу: тренировки, ходьба, всё движение.',
      '0–9 — лёгкий день, 10–13 — средний, 14–17 — тяжёлый, 18–21 — очень тяжёлый. Ориентир на сегодня зависит от восстановления.'] },
  };
  // «i» в шапке разбора: что это за показатель
  function aboutSheet(kind) {
    const A = ABOUT[kind]; if (!A) return;
    haptic('tap');
    sheet(`<h2>${{ sleep: 'Сон', rec: 'Восстановление', strain: 'Нагрузка' }[kind]}</h2>${A.p.map(x => `<p>${esc(x)}</p>`).join('')}`);
  }
  function explainHrv(v, b) {
    if (v == null) return 'Сегодня гибкость пульса ещё не посчитана — это делается ночью.';
    if (b == null) return `Сейчас ${Math.round(v)} мс. Норма появится, когда наберётся хотя бы неделя данных.`;
    const p = (v - b) / b * 100;
    if (p >= 10) return `Выше твоей нормы на ${Math.round(p)}% — нервная система хорошо отдохнула, организм готов к нагрузке. 💪`;
    if (p > -10) return 'В пределах твоей нормы — обычный день, можно тренироваться как запланировано.';
    if (p > -20) return `Ниже нормы на ${Math.round(-p)}% — организм немного устал (нагрузка, недосып, стресс). Тренируйся полегче.`;
    return `Сильно ниже нормы (на ${Math.round(-p)}%) — организм перегружен или начинается болезнь. Сегодня лучше отдых и сон.`;
  }
  function explainRhr(v, b) {
    if (v == null) return 'Пульс покоя ещё не посчитан — WHOOP меряет его во сне.';
    if (b == null) return `Сейчас ${v} уд/мин. Норма появится, когда наберётся хотя бы неделя данных.`;
    const d = v - b;
    if (d <= -3) return `Ниже твоей нормы на ${Math.round(-d)} — сердцу легче, ты хорошо восстановился. 👍`;
    if (d < 3) return 'Как обычно — всё в порядке.';
    if (d < 6) return `Выше нормы на ${Math.round(d)} — организм ещё не восстановился (поздняя тренировка, недосып, мало воды).`;
    return `Заметно выше нормы (+${Math.round(d)}) — так бывает при перегрузке или начале простуды. Сегодня полегче и больше сна.`;
  }
  function explain(kind) {
    const d = S.data, t = S.dayData || d.today, r = t.recovery || {}, c = t.cycle || {}, b = t.base || {}, n = t.nutrition;
    haptic('tap');
    const box = (title, body) => `<div class="label">${title}</div><div class="card prose">${body}</div>`;
    const norm = (v, base, unit, goodUp, dec = 0) => `<div class="grid2"><div class="stat"><div class="k">Сейчас</div><div class="v num">${v == null ? '—' : num(v, dec)}<small>${unit}</small></div></div>
      <div class="stat"><div class="k">Твоя норма</div><div class="v num">${base == null ? '—' : num(base, dec)}<small>${unit}</small></div><div class="d">${base == null ? '' : goodUp == null ? (v == null ? '' : `${v - base > 0 ? '↑' : '↓'} ${num(Math.abs(v - base), dec)}`) : delta(v, base, goodUp)}</div></div></div>`;
    let h = '';
    if (kind === 'hrv') {
      h = `<h2>Гибкость пульса</h2><p class="what-lead">Насколько отдохнул организм — по пульсу во сне.</p>${norm(r.hrv, b.hrv, ' мс', true)}
        <div class="note"><span>${r.hrv != null && b.hrv != null && r.hrv >= b.hrv ? '🟢' : '🟡'}</span><span>${esc(explainHrv(r.hrv, b.hrv))}</span></div>
        ${box('Простыми словами', '<p>Это то, насколько организм успел отдохнуть за ночь. Браслет понимает это по пульсу, пока ты спишь.</p><p><b>Норма</b> — твой обычный уровень за последние 30 дней.</p><p><b>Выше нормы — лучше</b>: ты отдохнул, можно нагружаться. <b>Ниже</b> — организм устал, день лучше полегче.</p>')}
        ${box('Подробнее', '<p>Сердце бьётся не как метроном: промежутки между ударами чуть-чуть разные. Чем сильнее они «гуляют», тем лучше отдохнула нервная система. WHOOP меряет это ночью (в самом WHOOP это называется HRV).</p><p><b>Что за число в мс:</b> на сколько тысячных секунды в среднем меняется промежуток между ударами сердца ночью. Само число у всех своё — смотри на стрелку: ▲ больше твоей нормы — организм отдохнул, ▼ меньше — устал.</p><p><b>Выше — лучше:</b> нервная система отдохнула. <b>Ниже обычного</b> — усталость, стресс, недосып, болезнь, поздняя еда.</p><p>Сравнивай только со своей нормой — у всех она своя, чужие цифры не важны. Стрелка ↑/↓ — разница с твоей нормой за 30 дней.</p>')}
        ${box('Как поднять', '<ul><li>Спать 8–9 ч и ложиться в одно время</li><li>После тяжёлого дня — лёгкий</li><li>Пить воду в течение дня</li><li>Не есть плотно за 2–3 ч до сна, вечером без экрана</li></ul>')}`;
    } else if (kind === 'rhr') {
      h = `<h2>Пульс покоя</h2>${norm(r.rhr, b.rhr, ' уд/мин', false)}
        <div class="note"><span>${r.rhr != null && b.rhr != null && r.rhr <= b.rhr ? '🟢' : '🟡'}</span><span>${esc(explainRhr(r.rhr, b.rhr))}</span></div>
        ${box('Что это', '<p>Сколько раз в минуту бьётся сердце, когда ты глубоко спишь. Чем ты тренированнее и лучше отдохнул, тем он ниже: сердцу легче качать кровь.</p><p><b>Ниже — лучше.</b> Если он выше обычного на 3–5 и больше — организм не восстановился или начинается болезнь. Стрелка — разница с твоей нормой за 30 дней.</p>')}
        ${box('Как снизить', '<ul><li>Хороший сон без поздних тренировок</li><li>Спокойное кардио 2–3 раза в неделю (лёгкий бег, вело)</li><li>Вода, меньше сладких напитков и кофеина вечером</li></ul>')}`;
    } else if (kind === 'kcal') {
      const eaten = t.eaten ? t.eaten.kcal : null;
      h = `<h2>Ккал — траты за день</h2><div class="grid2"><div class="stat"><div class="k">Потратил</div><div class="v num">${big(c.kcal)}</div><div class="d">${b.kcal != null ? `обычно ${big(b.kcal)}` : ''}</div></div>
        <div class="stat"><div class="k">Съел</div><div class="v num">${big(eaten)}</div><div class="d">${n ? `норма ${big(n.kcal)}` : ''}</div></div></div>
        ${box('Что это', '<p>Сколько энергии тело потратило за день: на дыхание, мозг, учёбу, ходьбу и тренировки. Даже во сне тратится примерно 1200–1600 ккал.</p><p>Чтобы расти и тренироваться, есть нужно примерно столько же, в тяжёлые дни — больше. Если долго есть меньше трат — падают силы, восстановление и рост мышц.</p>')}
        ${box('Что делать', `<ul><li>Сравни с «Съел» — недобор больше 300–400 ккал лучше закрыть едой</li><li>Белок в каждом приёме пищи (мясо, рыба, творог, яйца)</li><li>Записывай еду фото во вкладке «Питание»</li></ul>`)}`;
    } else if (kind === 'resp') {
      const s = t.sleep || {};
      h = `<h2>Частота дыхания</h2>${norm(s.resp, b.resp, ' в мин', null, 1)}
        ${box('Что это', '<p>Сколько вдохов в минуту ты делаешь во сне. У здорового человека она очень стабильна — обычно 12–18.</p><p>Заметный рост (на 1–2 и больше) бывает при простуде, жаре в комнате или сильной усталости. Важна не сама цифра, а разница с твоей нормой.</p>')}
        ${box('Что делать', '<ul><li>Проветривай комнату, спи в прохладе (18–20 °C)</li><li>Если дыхание выше нормы вместе с пульсом — сегодня без тяжёлой тренировки</li></ul>')}`;
    } else if (kind === 'spo2') {
      h = `<h2>Кислород в крови</h2>${norm(r.spo2, null, ' %', true, 1)}
        ${box('Что это', '<p>Какая часть крови несёт кислород. Норма — 95–100%. WHOOP меряет её ночью.</p><p>Если несколько ночей подряд ниже 94% — стоит показаться врачу, особенно при храпе или усталости с утра.</p>')}
        ${box('Что делать', '<ul><li>Спи на боку, с проветренной комнатой</li><li>Не ложись сразу после плотной еды</li></ul>')}`;
    } else if (kind === 'temp') {
      h = `<h2>Температура кожи</h2>${norm(r.skin_temp, b.skin_temp, ' °C', null, 1)}
        ${box('Что это', '<p>Температура кожи запястья ночью. Это не температура тела: она ниже, и важна только разница с твоей нормой.</p><p>Рост на 0,5–1 °C и больше на фоне плохого восстановления — частый ранний признак простуды.</p>')}
        ${box('Что делать', '<ul><li>При скачке температуры — больше сна и воды, тренировка полегче</li><li>Следи за самочувствием: горло, насморк</li></ul>')}`;
    }
    sheet(h);
    animate();
  }

  // ---------- экраны подробностей: сон, восстановление, нагрузка ----------
  function openScreen(kind, arg) {
    haptic('tap');
    S.scrollY = window.scrollY;
    S.screen = kind; S.screenArg = arg;
    nav('push', () => { render(true); window.scrollTo(0, 0); });
  }
  function closeScreen(swiped) {  // swiped — экран уже уехал пальцем: прежний просто въезжает, без второго перехода
    haptic('select');
    S.screen = null;
    nav('pop', () => { S.popping = true; try { render(false); } finally { S.popping = false; } window.scrollTo(0, S.scrollY || 0); }, swiped === true);
  }
  const dHead = (title, sub, color) => `<header class="dhead"><button class="avatar ghost bk" data-a="back" aria-label="Назад">${I.back}<span class="bk-t">Назад</span></button>
    <div class="titles"><h1 style="--c:${color}">${title}</h1><div class="sub">${sub}</div></div></header>`;
  const lastRows = n => (S.data.series || []).slice(-n);
  const dNoData = (title, text) => `<div class="empty"><span class="e">${I.moon}</span><b>${title}</b>${text}</div>`;
  const metric = (k, v, sub) => `<div class="stat"><div class="k">${k}</div><div class="v num">${v}</div>${sub ? `<div class="d">${sub}</div>` : ''}</div>`;
  const sub0 = t => `${dateWord(t.day)}, ${dateLine(t.day)}`;

  function screenView(kind) {
    const d = S.data, t = S.dayData || d.today;
    const own = { sleep: sleepScreen, rec: recScreen, strain: strainScreen }[kind];
    if (own) return own(t, d) + slot(kind, t, d);
    return F.screens[kind] ? F.screens[kind](t, d, S.screenArg) : dHead('Нет экрана', '', 'var(--ink)');
  }

  // ---------- разборы как в WHOOP: дата по центру и «i», большое кольцо, «что влияет» с уголком к кольцу, вывод в рамке ----------
  const dTop = (t, kind) => `<header class="wtop" data-t="${{ sleep: 'Сон', rec: 'Восстановление', strain: 'Нагрузка' }[kind] || ''}"><button class="wbtn bk" data-a="back" aria-label="Назад">${I.back}<span class="bk-t">Назад</span></button>
    <b class="wtop-t">${esc(dateWord(t.day))}</b><button class="wbtn info" data-a="about" data-v="${kind}" aria-label="Что это">i</button></header>`;
  function bigRing(p, color, value, label, band) {
    const w = 7, r = 50 - w / 2, c = 2 * Math.PI * r, off = c * (1 - Math.max(0, Math.min(1, p || 0)));
    let bd = '';
    if (band) {  // цель нагрузки — светлый отрезок дорожки и белая засечка посередине, как Strain Target
      const mid = (band[0] + band[1]) / 2 * 2 * Math.PI, x = k => (50 + k * Math.cos(mid)).toFixed(2), y = k => (50 + k * Math.sin(mid)).toFixed(2);
      bd = `<circle class="wband" cx="50" cy="50" r="${r}" stroke-width="${w}" stroke-dasharray="${((band[1] - band[0]) * c).toFixed(2)} ${c.toFixed(2)}" stroke-dashoffset="${(-band[0] * c).toFixed(2)}"/>
        <line class="wtick" x1="${x(r - w / 2)}" y1="${y(r - w / 2)}" x2="${x(r + w / 2)}" y2="${y(r + w / 2)}"/>`;
    }
    return `<section class="wring rise" style="--c:${color}"><div class="wring-c"><svg viewBox="0 0 100 100" aria-hidden="true">
      <circle class="wtrk" cx="50" cy="50" r="${r}" stroke-width="${w}"/>${bd}
      <circle class="arc" cx="50" cy="50" r="${r}" stroke-width="${w}" stroke-dasharray="${c.toFixed(2)}" stroke-dashoffset="${c.toFixed(2)}" data-off="${off.toFixed(2)}"/></svg>
      <div class="wring-in"><i class="wmark">ФОРМА</i><b class="num">${value}</b><span>${label}</span></div></div></section>`;
  }
  // треугольник к норме за 30 дней: форма — куда сдвинулось, цвет — хорошо (бирюза) / плохо (оранжевый) / ни то ни другое (серый)
  function tri(v, b, goodUp, d = 0) {
    if (v == null || b == null) return '<i class="tri dot"></i>';
    const x = v - b;
    if (Math.abs(x) < (d ? .05 : .5)) return '<i class="tri dot"></i>';
    const col = goodUp == null ? 'flat' : (goodUp ? x > 0 : x < 0) ? 'good' : 'bad';
    return `<i class="tri ${x > 0 ? 'up' : 'down'} ${col}"></i>`;
  }
  const crow = (ic, k, v, ind, ex, hint) => `<button class="crow" ${ex ? `data-a="explain" data-v="${ex}"` : 'type="button" tabindex="-1"'}><span class="cic">${ic}</span>
    <span class="ck">${k}${hint ? what(hint) : ''}</span>${ind.startsWith('<span class="seg3') ? ind + `<span class="cv num">${v}</span>` : `<span class="cv num">${v}</span>${ind}`}</button>`;
  const vsFoot = '<div class="cfoot"><i class="tri up good"></i><i class="tri down bad"></i><b>Сегодня</b>&nbsp;против 30 дней</div>';
  // три деления «плохо / достаточно / оптимально»; границы — наш расчёт (WHOOP свои не публикует)
  const lvl3 = (v, a, b) => v == null ? -1 : v < a ? 0 : v < b ? 1 : 2;
  const seg3 = lvl => `<span class="seg3">${[0, 1, 2].map(i => `<i class="${i === lvl ? ['poor', 'ok', 'best'][i] : ''}"></i>`).join('')}</span>`;
  // ключ — те же три деления в миниатюре: уровень видно по тому, какое деление горит (в ч/б виде цвет не различим)
  const segFoot = `<div class="cfoot seg3-key">${['Плохо', 'Достаточно', 'Оптимально'].map((w, l) => `<span>${seg3(l).replace('seg3', 'seg3 mini')}${w}</span>`).join('')}</div>`;
  const insight = (text, link, q) => `<section class="winsight rise"><p>${text}</p><button class="wlink" data-a="ask-go" data-v="${esc(q)}">${link}${I.arrow}</button></section>`;
  const cmpWord = (v, b, tol, up, down, same) => v == null || b == null ? null : v - b > tol ? up : b - v > tol ? down : same;

  // фазы сна: какую долю ночи занял каждый этап (подробной по минутам кривой WHOOP API не отдаёт — только итоги по фазам)
  const STAGES = [['awake', 'Не спал', 'var(--st-awake)'], ['rem', 'Быстрый сон', 'var(--st-rem)'], ['light', 'Лёгкий сон', 'var(--st-light)'], ['deep', 'Глубокий сон', 'var(--st-deep)']];
  function sleepScreen(t) {
    const s = t.sleep, b = t.base || {};
    let h = dTop(t, 'sleep');
    if (!s) return h + dNoData('Сон ещё не посчитан', 'WHOOP присылает его после пробуждения.');
    const perf = s.performance != null ? s.performance : s.need ? Math.round(s.hours / s.need * 100) : null;
    const hvn = s.need ? Math.round(s.hours / s.need * 100) : null;
    const inBed = (s.hours + s.awake) || 1;
    const rest = (s.deep || 0) + (s.rem || 0), restP = s.hours ? Math.round(rest / s.hours * 100) : null;
    h += bigRing((perf || 0) / 100, 'var(--sleep)', `${cnt(perf)}<small>%</small>`, 'Выполнение<br>сна');
    const L = { hvn: lvl3(hvn, 70, 85), cons: lvl3(s.consistency, 60, 80), eff: lvl3(s.efficiency, 85, 90), rest: lvl3(restP, 35, 45) };
    h += `<section class="wcon rise">${crow(I.moon, 'Часы и потребность', hvn != null ? hvn + '%' : '—', seg3(L.hvn))}
      ${crow(I.clock, 'Режим сна', s.consistency != null ? s.consistency + '%' : '—', seg3(L.cons))}
      ${crow(I.bed, 'Эффективность', s.efficiency != null ? Math.round(s.efficiency) + '%' : '—', seg3(L.eff))}
      ${crow(I.sparkle, 'Глубокий + быстрый', restP != null ? restP + '%' : '—', seg3(L.rest))}${segFoot}</section>`;
    const lv = perf == null ? -1 : perf >= 85 ? 2 : perf >= 70 ? 1 : 0;
    const weak = [['режим сна', L.cons], ['часы сна', L.hvn], ['эффективность', L.eff], ['глубокий сон и REM', L.rest]].filter(x => x[1] === 0 || x[1] === 1).sort((a, c) => a[1] - c[1])[0];
    h += insight(`${['Сна не хватило', 'Сон достаточный, но есть куда расти', 'Сон оптимальный — организм хорошо восстановился'][lv] || 'Сон посчитан'}${weak && lv < 2 ? ` — подтянуть стоит ${weak[0]}` : ''}.`,
      'Разобрать мой сон', `Разбери мой сон за ${dateLine(t.day)}: что улучшить?`);
    const mx = Math.max(s.hours, s.need, 1);
    const short = s.need ? s.need - s.hours : null;
    h += `<section class="card rise"><h3 class="ch-t">Сколько спал и сколько нужно</h3>
      <p class="ch-lead"><span class="ch-v"><b class="num">${hHuman(s.hours)}</b> сна</span>${short == null ? '' : short > 5 / 60
        ? `<span class="ch-cmp down"><i>▼</i>на ${Math.round(short * 60)} мин меньше, чем нужно</span>` : '<span class="ch-cmp up"><i>▲</i>выспался — хватило</span>'}</p>
      <div class="hv"><div class="hv-r"><span>Спал · ${esc(s.start)}–${esc(s.end)}</span><b class="num">${hHuman(s.hours)}</b></div>${bar(pct(s.hours, mx), 'var(--sleep)')}
        <div class="hv-r"><span>Нужно</span><b class="num">${hHuman(s.need)}</b></div>${bar(pct(s.need, mx), 'var(--st-awake)')}
        ${b.sleep != null ? `<div class="hv-r"><span>Обычно</span><b class="num">${hHuman(b.sleep)}</b></div>${bar(pct(b.sleep, mx), 'color-mix(in srgb, var(--sleep) 45%, transparent)')}` : ''}</div></section>`;
    h += `<section class="card rise"><h3 class="ch-t">Из чего состоял сон</h3>
      <p class="ch-lead"><span class="ch-v"><b class="num">${hHuman(rest)}</b> восстанавливающего сна</span><span class="ch-cmp flat">глубокий + быстрый · ${restP != null ? restP + '% сна' : ''} · в постели ${hHuman(inBed)}</span></p>
      <div class="stagebar">${STAGES.map(([k, , col]) => s[k] ? `<span style="flex:${s[k]};background:${col}"></span>` : '').join('')}</div>
      <div class="steps-chart">${STAGES.map(([k, name, col], i) => {
        const v = s[k] || 0, p = v / inBed * 100;
        return `<div class="stp"><span class="stn"><i style="background:${col}"></i>${name}</span>
          <span class="stbar"><span style="width:0;background:${col}" data-w="${Math.max(1.5, p).toFixed(1)}%"></span></span>
          <span class="stv num"><b>${hS(v)}</b><small>${p.toFixed(0)}%</small></span></div>`;
      }).join('')}</div><p class="small muted">Глубокий сон чинит мышцы, быстрый — голову и память. Чем их больше, тем лучше восстановление.</p></section>`;
    h += `<div class="grid2 metrics rise">${metric('Дыхание во сне', s.resp != null ? `${num(s.resp, 1)}<small>вдохов/мин</small>` : '—', b.resp != null ? `обычно ${num(b.resp, 1)} вдохов в минуту` : '')}
      ${metric('Просыпался', s.disturbances != null ? `${s.disturbances}<small>${plural(s.disturbances, 'раз', 'раза', 'раз')}</small>` : '—', s.cycles != null ? `${s.cycles} ${plural(s.cycles, 'цикл', 'цикла', 'циклов')} сна` : '')}</div>`;
    return h;
  }

  function recScreen(t) {
    const r = t.recovery, b = t.base || {}, d = S.data, s = t.sleep || {};
    let h = dTop(t, 'rec');
    if (!r) return h + dNoData('Ещё не посчитано', 'WHOOP считает восстановление ночью, по готовности сна.');
    h += bigRing(r.score / 100, C[r.zone], `${cnt(r.score)}<small>%</small>`, 'Восстановление');
    if (r.calibrating) h += '<div class="note rise"><span>⏳</span><span>WHOOP ещё калибруется — первые недели цифры неточные.</span></div>';
    const perfs = (d.series || []).filter(x => x.day < t.day).map(x => x.perf).filter(x => x != null), pb = perfs.length >= 5 ? perfs.reduce((a, x) => a + x, 0) / perfs.length : null;
    h += `<section class="wcon rise">${crow(I.hrv, 'Гибкость пульса', r.hrv != null ? `${Math.round(r.hrv)}<small>мс</small>` : '—', tri(r.hrv, b.hrv, true), 'hrv', HRV_WHAT)}
      ${crow(I.rhr, 'Пульс покоя', r.rhr != null ? `${r.rhr}<small>уд/мин</small>` : '—', tri(r.rhr, b.rhr, false), 'rhr')}
      ${crow(I.lungs, 'Дыхание во сне', s.resp != null ? `${num(s.resp, 1)}<small>в мин</small>` : '—', tri(s.resp, b.resp, null, 1), 'resp')}
      ${crow(I.moon, 'Сон от нужного', s.performance != null ? s.performance + '%' : '—', tri(s.performance, pb, true))}${vsFoot}</section>`;
    const parts = [cmpWord(r.hrv, b.hrv, (b.hrv || 0) * .07, 'Гибкость пульса выше обычного', 'Гибкость пульса ниже обычного', 'Гибкость пульса как обычно'),
      cmpWord(r.rhr, b.rhr, 2, 'пульс покоя выше', 'пульс покоя ниже', 'пульс покоя обычный')].filter(Boolean);
    h += insight(`${parts.length ? parts.join(', ') + ' — ' : ''}${{ green: 'организм готов к нагрузке сегодня', yellow: 'сегодня лучше умеренная нагрузка', red: 'сегодня день восстановления' }[r.zone]}.`.replace(/^./, x => x.toUpperCase()),
      'Разобрать восстановление', `Почему у меня восстановление ${r.score}% и что с этим делать сегодня?`);
    if (t.brief) h += `<div class="label rise">Разбор тренера</div><div class="card prose rise">${tgHtml(t.brief)}</div>`;
    if ((t.tips || []).length) h += `<div class="label rise">Сигналы тела</div><div class="card tips rise">${t.tips.map(x => `<div class="tip ${x.tone}"><i></i><p>${esc(x.text)}</p></div>`).join('')}</div>`;
    if (t.day === d.today.day) h += `<button class="btn tonal rise" data-a="brief">${t.brief ? 'Обновить разбор' : 'Разобрать сейчас'}</button>`;
    return h;
  }

  // зоны пульса: 1 — разминка … 5 — максимум
  const ZCOL = ['var(--z1)', 'var(--z2)', 'var(--z3)', 'var(--z4)', 'var(--z5)'];
  function strainScreen(t) {
    const c = t.cycle, b = t.base || {}, ws = t.workouts || [];
    let h = dTop(t, 'strain');
    const sv = c && c.strain != null ? c.strain : null;
    const band = t.band ? [t.band[0] / 21, t.band[1] / 21] : null;
    h += bigRing((sv || 0) / 21, 'var(--strain)', cnt(sv, 1), 'Нагрузка дня', band);
    // зоны — только по тренировкам (поминутного пульса за весь день WHOOP API не отдаёт)
    const zs = [1, 2, 3, 4, 5].map(i => ws.reduce((a, w) => a + ((w.zones || [])[i] || 0), 0));
    const z13 = zs[0] + zs[1] + zs[2], z45 = zs[3] + zs[4], mins = ws.reduce((a, w) => a + (w.minutes || 0), 0);
    h += `<section class="wcon rise">${crow(I.zones, 'Пульс: зоны 1–3 · лёгкие', ws.length ? hS(z13 / 60) : '—', '<i class="tri dot"></i>')}
      ${crow(I.zones, 'Пульс: зоны 4–5 · тяжёлые', ws.length ? hS(z45 / 60) : '—', '<i class="tri dot"></i>')}
      ${crow(I.bolt, 'Калории', c && c.kcal != null ? `${big(c.kcal)}<small>ккал</small>` : '—', tri(c && c.kcal, b.kcal, null), 'kcal')}
      ${crow(I.run, 'Тренировки', ws.length ? `${ws.length} · ${hS(mins / 60)}` : '0', '<i class="tri dot"></i>')}${vsFoot}</section>`;
    const word = sv == null ? '' : sv < 10 ? 'лёгкая: организм почти не напрягался' : sv < 14 ? 'средняя: обычная активная нагрузка' : sv < 18 ? 'высокая: сердце и мышцы хорошо поработали' : 'максимальная: на пределе сил';
    h += insight(sv == null ? 'Нагрузка ещё не посчитана.' : `Нагрузка ${num(sv, 1)} — ${word}.${t.band ? ` Цель на сегодня по восстановлению — ${t.band[0]}–${t.band[1]}.` : ''}`,
      'Разобрать нагрузку', 'Сколько и как мне тренироваться сегодня по нагрузке и восстановлению?');
    if (t.band || sv != null) h += strainTarget(t, sv);
    h += `<div class="grid2 metrics rise">${metric('Пульс средний', c && c.avg_hr ? c.avg_hr : '—', 'уд/мин за день')}${metric('Пульс макс.', c && c.max_hr ? c.max_hr : '—', 'уд/мин за день')}</div>`;
    if (ws.length) {
      h += '<div class="label rise">Тренировки</div>';
      h += ws.map(w => {
        const tot = (w.zones || []).slice(1).reduce((a, x) => a + x, 0);
        return `<section class="card wk rise tap" data-a="workout" data-v="${esc(w.id)}" role="button"><div class="wk-h"><div><div class="wk-n">${esc(w.sport)}</div><div class="meta num">${esc(w.start)} · ${w.minutes || '—'} мин · пульс ${w.avg_hr || '—'}/${w.max_hr || '—'}${w.kcal ? ` · ${big(w.kcal)} ккал` : ''}</div></div>
          <div class="wk-s num">${w.strain != null ? num(w.strain, 1) : '—'}</div></div>
          ${tot ? `<div class="zbar">${w.zones.slice(1).map((m, i) => m ? `<span style="flex:${m};background:${ZCOL[i]}"></span>` : '').join('')}</div>
          <div class="zleg num">${w.zones.slice(1).map((m, i) => `<span><i style="background:${ZCOL[i]}"></i>Зона ${i + 1}<b>${m}<small> мин</small></b></span>`).join('')}</div>` : ''}</section>`;
      }).join('');
    } else h += '<div class="empty rise"><span class="e">' + I.run + '</span>Тренировок за день нет.</div>';
    return h;
  }

  // ---------- сводка и советы за период ----------
  const PERIODS = [['day', 'День'], ['week', 'Неделя'], ['month', 'Месяц'], ['all', 'Всё время']];
  S.sum = {};
  function cmp(v, p, goodUp, unit = '', d = 0) {
    if (v == null || p == null) return '';
    const x = v - p;
    if (Math.abs(x) < (d ? .05 : .5)) return '<span class="muted">как было</span>';
    const cls = goodUp == null ? 'muted' : (goodUp ? x > 0 : x < 0) ? 'up' : 'down';  // null — ни хорошо, ни плохо
    return `<span class="${cls}">${x > 0 ? '↑' : '↓'} ${num(Math.abs(x), d)}${unit}</span>`;
  }
  function sumBody(per) {
    const z = S.sum[per];
    if (!z || z.loading && !z.summary) return skel(4);
    if (z.error && !z.summary) return `<div class="empty"><span class="e">⚠️</span>${esc(z.error)}</div>`;
    const s = z.summary, x = s.stats, p = s.prev || {};
    const fmtD = iso => { const d = parseDay(iso); return `${d.getDate()} ${MON_S[d.getMonth()]}`; };
    const range = per === 'day' ? dateLine(s.until) : `${fmtD(s.since)} — ${fmtD(s.until)} · ${s.span} ${plural(s.span, 'день', 'дня', 'дней')}`;
    const tile = (k, v, c, hint) => `<div class="stat"><div class="k">${k}${hint ? what(hint) : ''}</div><div class="v sm num">${v}</div><div class="d">${c || ''}</div></div>`;
    const dd = s.day || {};
    const recV = per === 'day' ? (dd.recovery ? dd.recovery.score : null) : x.recovery;
    const hrvV = per === 'day' ? (dd.recovery ? dd.recovery.hrv : null) : x.hrv;
    const rhrV = per === 'day' ? (dd.recovery ? dd.recovery.rhr : null) : x.rhr;
    const slV = per === 'day' ? (dd.sleep ? dd.sleep.hours : null) : x.sleep;
    const stV = per === 'day' ? (dd.cycle ? dd.cycle.strain : null) : x.strain;
    let h = `<p class="small muted">${range} · сравнение: ${esc(s.prev_label)}</p>
      <div class="grid2">${tile('Восстановление', recV != null ? Math.round(recV) + '%' : '—', cmp(recV, p.recovery, true, '%'))}
      ${tile('Гибкость пульса', hrvV != null ? Math.round(hrvV) + ' мс' : '—', cmp(hrvV, p.hrv, true, ' мс'), HRV_WHAT)}
      ${tile('Пульс покоя', rhrV != null ? Math.round(rhrV) + ' уд/мин' : '—', cmp(rhrV, p.rhr, false, ' уд/мин'))}
      ${tile('Сон', hHuman(slV), cmp(slV != null ? slV * 60 : null, p.sleep != null ? p.sleep * 60 : null, true, ' мин'))}
      ${tile('Нагрузка', stV != null ? num(stV, 1) + ' из 21' : '—', cmp(stV, p.strain, null, '', 1))}
      ${per === 'day' ? tile('Тренировки', (dd.workouts || []).length, '') : tile('Тренировки', x.workouts, `<span class="muted">всего ${x.minutes} мин</span>`)}</div>`;
    if (per !== 'day' && x.days) {
      const zb = (n, c) => n ? `<span style="flex:${n};background:${c}"></span>` : '';
      const dn = k => `<b class="num">${k}</b> ${plural(k, 'день', 'дня', 'дней')}`;
      h += `<div class="label">Восстановление по дням</div><div class="card sum-z"><p class="ch-lead"><span class="ch-v"><b class="num">${x.green}</b> из ${x.days} ${plural(x.days, 'дня', 'дней', 'дней')} — высокое</span></p>
        <div class="zones" role="img" aria-label="Высокое ${x.green}, среднее ${x.yellow}, низкое ${x.red} из ${x.days}">${zb(x.green, C.green)}${zb(x.yellow, C.yellow)}${zb(x.red, C.red)}</div>
        <div class="ch-key"><span>${kdot('green')}высокое ${dn(x.green)}</span><span>${kdot('yellow')}среднее ${dn(x.yellow)}</span><span>${kdot('red')}низкое ${dn(x.red)}</span></div>
        <p class="small muted num">Отбой в среднем ${x.bed != null ? esc(clockH(x.bed)) : '—'}${x.bed_spread != null ? `, разброс ±${num(x.bed_spread * 60)} мин` : ''}</p></div>`;
    }
    h += `<div class="label">Итог и советы</div>`;
    if (z.making && z.live) h += `<div class="card prose sum-live">${md(z.live)}</div>`;
    else if (z.making) h += `<div class="card sum-live">${skel(3)}<p class="small muted">Разбираю данные — пара секунд…</p></div>`;
    else if (z.text) h += `<div class="card prose">${md(z.text)}</div><button class="btn sm ghost" data-a="sum-make" data-v="${per}">Обновить советы</button>`;
    else h += `<button class="btn primary" data-a="sum-make" data-v="${per}">Получить советы</button>`;
    return h;
  }
  // отбой хранится в часах от полудня (23:30 -> 11,5)
  const clockH = h => { const m = Math.round((((h + 12) % 24) + 24) % 24 * 60); return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`; };
  let sumEl = null, sumPer = 'day';
  function sumHtml() {
    return `<h2>Сводка и советы</h2><div class="seg" role="tablist">${PERIODS.map(([k, l]) => `<button class="${sumPer === k ? 'on' : ''}" data-a="sum-per" data-v="${k}" role="tab">${l}</button>`).join('')}</div>
      <div id="sum-body">${sumBody(sumPer)}</div>`;
  }
  function sumRender() { const b = sumEl && sumEl.querySelector('#sum-body'); if (b) { morph(b, sumBody(sumPer)); animate(); } }
  // «Итог и советы» приходят по кусочкам: дописываем только карточку текста раз в кадр — лист не перерисовывается
  async function sumStream(per, z) {
    const headers = { 'X-Init-Data': (tg && tg.initData) || '', 'Content-Type': 'application/json' };
    if (!headers['X-Init-Data']) headers['X-Site'] = '1';
    const r = await fetch('/api/summary/stream', { method: 'POST', headers, body: JSON.stringify({ period: per }) });
    if (!r.ok || !r.body) { let m = ''; try { m = (await r.json()).error; } catch (e) { /* не JSON */ } throw new Error(m || 'Не получилось — попробуй ещё раз'); }
    const rd = r.body.getReader(), dec = new TextDecoder();
    let buf = '', live = '', queued = false, done = null;
    const paint = () => { queued = false; if (per !== sumPer || !sumEl) return; const c = sumEl.querySelector('.sum-live'); if (!c) return sumRender(); c.classList.add('prose'); c.innerHTML = md(live); };
    for (;;) {
      const { value, done: end } = await rd.read();
      if (end) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const line = buf.slice(0, i).replace(/^data: ?/, ''); buf = buf.slice(i + 2);
        let ev; try { ev = JSON.parse(line); } catch (e) { continue; }
        if (ev.error) throw new Error(ev.error);
        if (ev.t) { live += ev.t; z.live = live; if (!queued) { queued = true; requestAnimationFrame(paint); } }
        if (ev.done) done = ev.text;
      }
    }
    if (done == null) throw new Error('Ответ оборвался — попробуй ещё раз');
    return done;
  }
  async function sumLoad(per, make) {
    const z = S.sum[per] = S.sum[per] || {};
    if (make) { if (z.making) return; z.making = true; z.live = ''; } else z.loading = true;
    if (per === sumPer) sumRender();
    try {
      if (make) { z.text = await sumStream(per, z); z.error = null; haptic('success'); }
      else {
        const r = await api('/api/summary', { period: per });
        Object.assign(z, { summary: r.summary, text: r.text, error: null });
        if (!r.text && (r.summary.stats.days || (r.summary.day && r.summary.day.cycle))) { z.loading = false; return sumLoad(per, true); }  // советы — сразу, без лишней кнопки
      }
    } catch (e) { if (make) fail(e); else z.error = e.message || String(e); }
    z.loading = false; z.making = false; z.live = '';
    if (per === sumPer) sumRender();
  }
  function summarySheet(per) {
    haptic('tap');
    sumPer = per || 'day';
    sumEl = sheet(sumHtml());
    sumEl.classList.add('sum-sheet');  // постоянная высота: при загрузке и смене периода лист не прыгает
    sumEl.addEventListener('click', ev => {
      const b = ev.target.closest('[data-a]');
      if (!b) return;
      ev.stopPropagation();
      if (b.dataset.a === 'sum-per') {
        sumPer = b.dataset.v; haptic('select');
        sumEl.querySelectorAll('[data-a="sum-per"]').forEach(x => x.classList.toggle('on', x.dataset.v === sumPer));
        sumRender();
        if (!S.sum[sumPer]) sumLoad(sumPer);
      } else if (b.dataset.a === 'sum-make') sumLoad(b.dataset.v, true);
    });
    if (!S.sum[sumPer] || !S.sum[sumPer].summary) sumLoad(sumPer); else sumRender();
  }

  // ---------- Здоровье (Health): монитор здоровья, оценки, тренды ----------
  // «Health Monitor»: каждый показатель против твоего обычного уровня. Точных границ WHOOP не публикует —
  // здесь наши: «в норме» — в пределах tol от нормы за 30 дней, «заметно» — до 2×tol, дальше — «сильно».
  const MONITOR = [
    { k: 'Гибкость пульса', what: HRV_WHAT, ex: 'hrv', unit: 'мс', get: t => (t.recovery || {}).hrv, base: b => b.hrv, tol: b => b * .15 },
    { k: 'Пульс покоя', ex: 'rhr', unit: 'уд/мин', get: t => (t.recovery || {}).rhr, base: b => b.rhr, tol: () => 3 },
    { k: 'Дыхание во сне', ex: 'resp', unit: 'в мин', d: 1, get: t => (t.sleep || {}).resp, base: b => b.resp, tol: () => 1 },
    { k: 'Кислород в крови', ex: 'spo2', unit: '%', d: 1, get: t => (t.recovery || {}).spo2, base: () => 97.5, tol: () => 2.5, range: '95–100' },
    { k: 'Температура кожи', ex: 'temp', unit: '°C', d: 1, get: t => (t.recovery || {}).skin_temp, base: b => b.skin_temp, tol: () => .5 },
  ];
  function monStat(t) {
    const b = t.base || {};
    let ok = 0, all = 0;
    MONITOR.forEach(m => { const v = m.get(t), base = m.base(b); if (v != null && base != null) { all++; if (Math.abs(v - base) / m.tol(base) <= 1) ok++; } });
    return { ok, all };
  }
  function monitorCard(t) {
    const b = t.base || {};
    let ok = 0, all = 0;
    const rows = MONITOR.map(m => {
      const v = m.get(t), base = m.base(b) != null ? m.base(b) : null;
      let col = 'var(--hint)', word = 'нет данных', range = '';
      if (base != null) { const tl = m.tol(base); range = m.range || `${num(base - tl, m.d || 0)}–${num(base + tl, m.d || 0)}`; }
      if (v != null && base != null) {
        all++;
        const x = Math.abs(v - base) / m.tol(base);
        if (x <= 1) { ok++; col = C.green; word = 'в норме'; } else if (x <= 2) { col = C.yellow; word = 'заметно'; } else { col = C.red; word = 'сильно'; }
      } else if (v != null) word = 'нет нормы';
      return `<button class="mrow" data-a="explain" data-v="${m.ex}"><i class="mdot" style="background:${col}"></i>
        <span class="mk">${m.k}${m.what ? what(m.what) : ''}<small>${range ? `обычно ${range}` : word}</small></span>
        <span class="mv"><b class="num">${v == null ? '—' : num(v, m.d || 0)}</b><small>${m.unit}</small><em style="color:${col}">${word}</em></span></button>`;
    }).join('');
    return `<section class="card monitor rise">${cap('Монитор здоровья', all ? `<b class="num" style="color:${ok === all ? C.green : ok >= all - 1 ? C.yellow : C.red}">${ok}/${all}</b> в норме` : '')}${rows}</section>`;
  }
  const RANGES = [[7, 'Неделя'], [30, 'Месяц'], [180, '6 месяцев']];
  function trendsView() {
    const t = S.data.today;
    let h = hero('Здоровье', `${dateWord(t.day)}, ${dateLine(t.day)}`);
    h += slot('healthTop', t);
    h += monitorCard(t);
    h += slot('healthAfterMonitor', t);
    h += progCard();
    h += `<div class="sec rise">${cap('Тренды', F.screens.trends ? '<button class="link" data-a="tr-open">Все показатели</button>' : '')}<div class="seg" role="tablist">${RANGES.map(([n, l]) => `<button class="${S.range === n ? 'on' : ''}" data-a="range" data-v="${n}" role="tab">${l}</button>`).join('')}</div></div>`;
    const rows = S.series[S.range] || (S.range === 30 ? S.data.series : null);
    if (!rows && S.seriesErr === S.range) return h + retryCard('Графики не загрузились', 'series-retry');
    if (!rows) return h + [1, 2, 3].map(() => '<section class="card"><div class="sk line" style="width:40%"></div><div class="sk" style="height:130px;margin-top:14px"></div></section>').join('');
    if (!rows.some(r => r.recovery != null || r.strain != null || r.sleep != null)) {
      return h + `<div class="empty"><span class="e">${I.trends}</span><b>Данных пока нет</b>Подключи WHOOP — графики появятся после первой ночи с браслетом.</div>`;
    }
    // каждый график: заголовок «… за неделю», строка вывода (среднее и сравнение с прошлым таким же периодом), подсказка по касанию
    const n = S.range, prev = prevOf(n), card = html => `<section class="card chart-card rise">${html}</section>`;
    ['rec', 'sleep', 'strain', 'hrv', 'rhr'].forEach(k => { h += card(CHARTS[k](rows, prev, n)); });
    h += slot('healthBottom', t, rows);
    return h;
  }
  async function loadSeries(n, quiet) {  // quiet — прошлый период для сравнения: без ошибки на экране
    if (!quiet) S.seriesErr = null;
    try { S.series[n] = (await api('/api/series', { n })).series; } catch (e) { if (quiet) return; fail(e); S.seriesErr = n; }
    if (S.tab === 'trends') render(false);
  }
  // ---------- Разбор прогресса (Глеб 03.11: «должен быть намного легче… пробаганный и неплавный») ----------
  // Карточка во «Здоровье» — только крупный итог и кнопка; разбор — в листе постоянной высоты: «Итог» крупно, блоки целей —
  // свёрнутыми строками. «Обновить» сразу открывает лист и пишет разбор потоком (/api/progress/stream). Карточка и лист
  // меняются точечно (morph своего узла), вкладка с графиками не перерисовывается.
  S.pg = { live: '', making: false, open: new Set() };
  const pgPlain = pr => pr ? plainOf(pr.text) : '';
  // Markdown разбора → [{title, lines}], первый — «Итог» (или текст до первого заголовка)
  function progBlocks(text) {
    const out = [];
    let cur = null;
    (text || '').split('\n').forEach(raw => {
      const l = raw.trim();
      const hd = l.match(/^(?:#{1,6}\s*)?\*\*([^*]{1,80})\*\*:?$/) || l.match(/^#{1,6}\s+(.{1,80})$/);
      if (hd) { cur = { title: hd[1].replace(/:$/, '').trim(), lines: [] }; out.push(cur); return; }
      if (!l) return;
      if (!cur) { cur = { title: 'Итог', lines: [] }; out.push(cur); }
      cur.lines.push(l);
    });
    return out;
  }
  const pgLead = b => b ? b.lines.map(x => x.replace(/^\s*(?:[-*•]|\d+[.)])\s+/, '')).join(' ').replace(/\*\*(.+?)\*\*/g, '$1') : '';
  const pgPeek = b => { const l = (b.lines.find(x => /^Сейчас:/i.test(x)) || b.lines[0] || '').replace(/^\s*(?:[-*•])\s+/, '').replace(/\*\*(.+?)\*\*/g, '$1'); return l; };
  function progCard() {
    const pr = S.progress, z = S.pg, bl = pr ? progBlocks(pgPlain(pr)) : [];
    const sum = bl[0], rest = bl.slice(1);
    let body;
    if (S.progress === undefined && !z.making) body = `<div class="pg-c-sum">${skel(2)}</div><div class="pg-c-row"><span class="small muted">&nbsp;</span></div>`;
    else if (!pr && !z.making) body = `<p class="pg-c-sum muted">Посмотрю все данные — WHOOP, сон, вес, еду и воду, привычки — и по каждой цели скажу, как быстрее к ней прийти.</p>
      <div class="pg-c-row"><button class="btn primary" data-a="progress">Разобрать прогресс</button></div>`;
    else {
      const lead = z.making ? (pgLead(sum) || 'Смотрю все твои данные…') : pgLead(sum);  // пока пишется новый — прежний итог
      body = `<p class="pg-c-sum">${esc(lead)}</p>
        <div class="pg-c-row"><span class="small muted">${z.making ? 'Обновляю разбор…' : `${dateLine(pr.day)}${rest.length ? ` · ${rest.length} ${plural(rest.length, 'блок', 'блока', 'блоков')}` : ''}`}</span>
        <button class="btn sm tonal" data-a="pg-open">${z.making ? 'Смотреть' : 'Открыть'}</button></div>`;
    }
    // «Обновить» в шапке есть всегда (прячется, а не убирается) — высота карточки не прыгает
    const can = pr && !z.making;
    return `<section class="card pg-card rise" id="pg-card">${cap('Разбор прогресса', `<button class="link${can ? '' : ' pg-hide'}" data-a="progress"${can ? '' : ' tabindex="-1" aria-hidden="true"'}>Обновить</button>`)}${body}</section>`;
  }
  const pgCardSync = () => { const c = $('#pg-card'); if (c) morph(c, progCard(), true); };
  function progBody() {
    const z = S.pg, text = z.making ? z.live : pgPlain(S.progress);
    if (z.err && !z.making && !S.progress) return `<div class="empty"><span class="e">⚠️</span>${esc(z.err)}</div><button class="btn primary" data-a="progress">Попробовать ещё раз</button>`;
    const bl = progBlocks(text);
    if (!bl.length) return `<div class="pg-sum">${skel(3)}</div><p class="small muted pg-wait">Смотрю все твои данные — первые строки через пару секунд…</p>`;
    const [sum, ...rest] = bl;
    let h = `<div class="pg-sum">${md(sum.lines.join('\n'))}</div>`;
    h += rest.map((b, i) => {
      const writing = z.making && i === rest.length - 1;
      const open = z.open.has(b.title) ? ' open' : '';
      return `<details class="pg-b${writing ? ' writing' : ''}" data-k="${esc(b.title)}"${open}><summary><span class="pg-bt"><b>${esc(b.title)}</b>
        <small>${writing ? 'пишу…' : esc(pgPeek(b))}</small></span><i class="pg-chev"></i></summary><div class="prose">${md(b.lines.join('\n'))}</div></details>`;
    }).join('');
    if (!z.making) h += `<div class="pg-foot"><button class="btn sm ghost" data-a="progress">Обновить разбор</button>
      <button class="btn sm tonal" data-a="ask-go" data-v="Посмотри мой разбор прогресса: с чего начать прямо сегодня?">${I.coach}Спросить тренера</button></div>`;
    return h;
  }
  let pgEl = null, pgQueued = false;
  function pgPaint() {
    pgQueued = false;
    pgCardSync();
    const b = pgEl && pgEl.isConnected && pgEl.querySelector('#pg-body');
    if (b) morph(b, progBody());
  }
  const pgSoon = () => { if (!pgQueued) { pgQueued = true; requestAnimationFrame(pgPaint); } };
  function progressSheet() {
    if (pgEl && pgEl.isConnected) return pgEl;
    const pr = S.progress;
    pgEl = sheet(`<h2>Разбор прогресса</h2><p class="small muted pg-meta">${pr && !S.pg.making ? `от ${dateLine(pr.day)} · ` : ''}по всем данным и твоим целям</p><div id="pg-body">${progBody()}</div>`, () => { pgEl = null; });
    pgEl.classList.add('pg-sheet');  // постоянная высота: пока пишется разбор, лист не растёт и не прыгает
    pgEl.addEventListener('toggle', e => { const d = e.target; if (d.matches && d.matches('details.pg-b')) { d.open ? S.pg.open.add(d.dataset.k) : S.pg.open.delete(d.dataset.k); if (d.open) haptic('select'); } }, true);
    pgEl.addEventListener('click', ev => { if (ev.target.closest('[data-a="ask-go"]')) pgEl.close(); });
    return pgEl;
  }
  async function progStream() {
    const headers = { 'X-Init-Data': (tg && tg.initData) || '', 'Content-Type': 'application/json' };
    if (!headers['X-Init-Data']) headers['X-Site'] = '1';
    const r = await fetch('/api/progress/stream', { method: 'POST', headers, body: '{}' });
    if (!r.ok || !r.body) { let m = ''; try { m = (await r.json()).error; } catch (e) { /* не JSON */ } throw new Error(m || 'Не получилось — попробуй ещё раз'); }
    const rd = r.body.getReader(), dec = new TextDecoder();
    let buf = '', done = null;
    for (;;) {
      const { value, done: end } = await rd.read();
      if (end) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const line = buf.slice(0, i).replace(/^data: ?/, ''); buf = buf.slice(i + 2);
        let ev; try { ev = JSON.parse(line); } catch (e) { continue; }
        if (ev.error) throw new Error(ev.error);
        if (ev.t) { S.pg.live += ev.t; pgSoon(); }
        if (ev.done) done = ev.progress || { day: S.data.today.day, text: ev.text };
      }
    }
    if (!done) throw new Error('Ответ оборвался — попробуй ещё раз');
    return done;
  }
  async function makeProgress() {
    const z = S.pg;
    if (z.making) { progressSheet(); return; }
    haptic('tap');
    Object.assign(z, { making: true, live: '', err: null });
    z.open.clear();
    progressSheet();
    pgPaint();
    try { S.progress = await progStream(); haptic('success'); } catch (e) { z.err = e.message || String(e); if (S.progress) fail(e); }
    z.making = false; z.live = '';
    pgPaint();
    const m = pgEl && pgEl.querySelector('.pg-meta'); if (m && S.progress) m.textContent = `от ${dateLine(S.progress.day)} · по всем данным и твоим целям`;
  }
  async function loadProgress() {
    if (S.pg.loading) return;
    S.pg.loading = true;
    try { S.progress = (await api('/api/progress', {})).progress; } catch (e) { if (S.progress === undefined) S.progress = null; }
    S.pg.loading = false;
    if (!S.pg.making) pgCardSync();  // только своя карточка — графики вкладки не трогаем
  }

  // ---------- Питание — встроенные «Калории»: kcal.js (F.views.food) ----------

  // ---------- Тренер ----------
  const SUGGEST = ['Готов ли я к спаррингу?', 'Что съесть перед тренировкой?', 'Почему упало восстановление?', 'Как улучшить сон?', 'План на неделю'];
  function coachView() {
    let h = hero('Чат', 'Тренер · врач · психолог · учёба', S.history && S.history.length ? '<button class="avatar ghost" data-a="clear" aria-label="Новый разговор">✕</button>' : '');
    const hist = S.history;
    if (!hist && S.historyErr) return h + retryCard('Разговор не загрузился', 'history-retry');
    if (!hist) return h + '<div class="chat"><div class="sk msg-sk me"></div><div class="sk msg-sk"></div><div class="sk msg-sk me short"></div></div>';
    h += '<div class="chat">';
    if (!hist.length && !S.asking) h += '<div class="empty"><span class="e">💬</span><b>Спроси что угодно</b>Про тренировки, сон, питание и восстановление — отвечу по твоим данным WHOOP. Можно приложить скрин тренировки.</div>';
    hist.forEach(m => { h += m.role === 'user' ? `<div class="msg me">${esc(m.text)}</div>` : `<div class="msg them prose">${md(m.text)}</div>${F.actChips ? F.actChips(m) : ''}`; });
    if (S.asking) h += `<div class="msg me">${esc(S.asking)}</div><div class="msg them"><span class="dots"><i></i><i></i><i></i></span></div>`;
    h += '</div>';
    h += `<div class="composer"><div class="chips">${SUGGEST.map(s => `<button class="chip" data-a="suggest" data-v="${esc(s)}">${esc(s)}</button>`).join('')}</div>
      <div class="ask"><button class="send alt" data-a="photo" data-v="ask" aria-label="Фото" ${S.asking ? 'disabled' : ''}>${I.cam}</button>
      <textarea id="q" rows="1" placeholder="Спроси о чём угодно…">${esc(S.qDraft || '')}</textarea>
      <button class="send" data-a="ask" aria-label="Отправить" ${S.asking ? 'disabled' : ''}>↑</button></div></div>`;
    return h;
  }
  async function loadHistory() {
    S.historyErr = false;
    try { S.history = (await api('/api/history', {})).history; if (F.coFresh) F.coFresh(); } catch (e) { S.historyErr = true; }  // без всплывающей ошибки: в чате — «Повторить»
    if (S.tab === 'coach') render();
  }
  // поле ввода очищаем сами: тихое обновление (morph) не трогает поле, в котором стоит курсор
  const clearQ = v => { const el = $('#q'); if (el) { el.value = v || ''; el.style.height = ''; } };
  // ответ тренера приходит по кусочкам: дописываем только последний пузырь раз в кадр, экран не перерисовывается
  async function askStream(q) {
    const headers = { 'X-Init-Data': (tg && tg.initData) || '', 'Content-Type': 'application/json' };
    if (!headers['X-Init-Data']) headers['X-Site'] = '1';
    const r = await fetch('/api/ask/stream', { method: 'POST', headers, body: JSON.stringify({ text: q }) });
    if (!r.ok || !r.body) { let m = ''; try { m = (await r.json()).error; } catch (e) { /* не JSON */ } throw new Error(m || 'Чат не ответил — попробуй ещё раз'); }
    const rd = r.body.getReader(), dec = new TextDecoder();
    let buf = '', live = '', queued = false, done = null;
    const paint = () => { queued = false; if (!S.asking) return;  /* ответ уже готов и отрисован целиком (с «✅ Записал») — не затирать */ const b = [...document.querySelectorAll('.msg.them')].pop(); if (!b || S.tab !== 'coach') return;
      const sc = b.closest('.co-scroll');  // докручиваем, только если он и так внизу (S.coBottom — more.js)
      b.classList.add('prose'); b.innerHTML = md(live); if (sc && S.coBottom !== false) sc.scrollTop = sc.scrollHeight; };
    for (;;) {
      const { value, done: end } = await rd.read();
      if (end) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const line = buf.slice(0, i).replace(/^data: ?/, ''); buf = buf.slice(i + 2);
        let ev; try { ev = JSON.parse(line); } catch (e) { continue; }
        if (ev.error) throw new Error(ev.error);
        if (ev.s && !live) { const b = [...document.querySelectorAll('.msg.them')].pop(); if (b && S.tab === 'coach') b.innerHTML = `<span class="dots"><i></i><i></i><i></i></span> <span class="ask-note">${esc(ev.s)}</span>`; }
        if (ev.t) { live += ev.t; S.askLive = live; if (!queued) { queued = true; requestAnimationFrame(paint); } }
        if (ev.done) done = ev;
      }
    }
    if (!done) throw new Error('Ответ оборвался — попробуй ещё раз');
    if (F.onActs) F.onActs(done);  // тренер что-то записал — чипы «Отменить» под ответом (assist.js)
    return done.history;
  }
  async function ask(q) {
    q = (q || '').trim(); if (!q || S.asking) return;
    S.asking = q; S.askLive = ''; S.qDraft = ''; S.coTips = false; S.coForce = true; clearQ(); haptic('tap'); render();
    try { S.history = await askStream(q); haptic('success'); }
    catch (e) { fail(e); S.qDraft = q; }
    S.asking = false; S.askLive = '';
    if (S.tab === 'coach') { render(); if (S.qDraft) clearQ(S.qDraft); }
  }

  // ---------- фото ----------
  async function shrink(file) {
    const url = URL.createObjectURL(file);
    try {
      const img = await new Promise((ok, bad) => { const i = new Image(); i.onload = () => ok(i); i.onerror = bad; i.src = url; });
      const k = Math.min(1, 1280 / Math.max(img.naturalWidth, img.naturalHeight));
      const cv = document.createElement('canvas');
      cv.width = Math.round(img.naturalWidth * k); cv.height = Math.round(img.naturalHeight * k);
      cv.getContext('2d').drawImage(img, 0, 0, cv.width, cv.height);
      return cv.toDataURL('image/jpeg', .85);
    } catch (e) {
      return new Promise((ok, bad) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = bad; r.readAsDataURL(file); });
    } finally { URL.revokeObjectURL(url); }
  }
  function pickPhoto() {  // фото к вопросу тренеру (еда по фото — в «Калориях», вкладка «Питание»)
    const inp = $('#pick');
    inp.value = '';
    inp.onchange = async () => {
      const f = inp.files && inp.files[0];
      if (!f) return;
      haptic('tap');
      const text = ($('#q') || {}).value || '';
      S.asking = '📷 ' + (text.trim() || 'Фото'); S.qDraft = ''; S.coForce = true; clearQ(); render();
      try { const r = await api('/api/photo', { image: await shrink(f), mode: 'ask', text: text.trim() }); S.history = r.history; haptic('success'); }
      catch (e) { fail(e); }
      S.asking = false; if (S.tab === 'coach') render();
    };
    inp.click();
  }

  // ---------- профиль и еда (листы) ----------
  function goalsBlock() {
    const gs = S.data.goals || [];
    return `${gs.length ? `<div class="list plain">${gs.map(g => `<div class="row no-ic"><div class="main"><div class="title wrap">${esc(g.text)}</div></div>
        <button class="btn icon" data-a="goal-del" data-v="${g.id}" aria-label="Удалить">✕</button></div>`).join('')}</div>`
      : '<p class="small muted">Например: «ложиться до 23:00», «соревнования по боксу весной». Тренер привязывает к ним все советы.</p>'}
      ${gs.length < 10 ? `<div class="ask"><textarea id="goal-new" rows="1" placeholder="Новая цель…"></textarea><button class="send" data-a="goal-add" aria-label="Добавить">＋</button></div>` : ''}`;
  }
  async function goalApi(path, body) {
    try { S.data.goals = (await api(path, body)).goals; haptic('success'); const el = $('#goals'); if (el) morph(el, goalsBlock()); } catch (e) { fail(e); }
  }
  const GOALS = [['form', 'Форма'], ['mass', 'Мышцы'], ['dry', 'Рельеф'], ['weight', 'Вес']];
  function profileSheet() {
    const d = S.data, p = d.profile, w = d.body || {};
    haptic('tap');
    const conn = d.connected
      ? `<div class="list"><div class="row"><span class="ic">✅</span><div class="main"><div class="title">WHOOP подключён</div><div class="meta">${d.synced ? 'обновлено ' + new Date(d.synced).toLocaleString('ru', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : ''}</div></div><button class="btn sm tonal" data-a="sync">Обновить</button></div></div>`
      : `<div class="list"><div class="row"><span class="ic">🔌</span><div class="main"><div class="title">WHOOP не подключён</div></div>${d.keys ? '<button class="btn sm primary" data-a="connect">Подключить</button>' : ''}</div></div>`;
    const f = (k, label, ph, type = 'text') => `<label class="field"><span>${label}</span><input name="${k}" type="text" inputmode="${type === 'number' ? 'decimal' : 'text'}" placeholder="${esc(ph)}" value="${esc(p[k] != null ? p[k] : '')}"></label>`;
    const el = sheet(`<h2>Профиль</h2>${conn}
      <div class="label">Мои цели</div><div id="goals">${goalsBlock()}</div>
      <form id="pf"><div class="label">Тело</div><div class="row3">${f('age', 'Возраст', '14', 'number')}${f('height', 'Рост, см', w.height_meter ? Math.round(w.height_meter * 100) : '170', 'number')}${f('weight', 'Вес, кг', w.weight_kilogram ? num(w.weight_kilogram, 1) : '60', 'number')}</div>
      <div class="field"><span>Пол</span><div class="seg" data-group="sex">${[['m', 'Парень'], ['f', 'Девушка']].map(([k, t]) => `<button type="button" class="${p.sex === k ? 'on' : ''}" data-v="${k}">${t}</button>`).join('')}</div></div>
      <div class="field"><span>Цель</span><div class="seg" data-group="goal">${GOALS.map(([k, t]) => `<button type="button" class="${(p.goal || 'form') === k ? 'on' : ''}" data-v="${k}">${t}</button>`).join('')}</div></div>
      <div class="label">Тренировки и еда</div>
      ${f('sport', 'Спорт', 'бокс, 8 месяцев')}${f('schedule', 'Расписание тренировок', 'пн, ср, пт — 2 часа вечером')}
      <label class="field"><span>Что ешь и чего не ешь</span><textarea name="diet" rows="2" placeholder="школьная столовая, не ем рыбу…">${esc(p.diet || '')}</textarea></label>
      <div class="label">Тренер</div>
      <label class="field"><span>Утренний разбор: время или «выкл»</span><input name="morning" type="text" placeholder="07:00" value="${esc(d.morning === 'off' ? 'выкл' : d.morning || '07:00')}"></label>
      <label class="field"><span>Важно знать тренеру</span><textarea name="notes" rows="2" placeholder="травмы, соревнования, самочувствие…">${esc(p.notes || '')}</textarea></label>
      <button class="btn primary" type="submit">Сохранить</button></form>${Kit.supportRow('/api/support')}${chatRow()}`);
    el.addEventListener('click', ev => {
      const s = ev.target.closest('[data-group] button');
      if (s) { const was = s.classList.contains('on'); s.parentElement.querySelectorAll('button').forEach(b => b.classList.remove('on')); if (!was || s.parentElement.dataset.group === 'goal') s.classList.add('on'); haptic('select'); }
    });
    $('#pf', el).addEventListener('submit', async ev => {
      ev.preventDefault();
      const fd = Object.fromEntries(new FormData(ev.target).entries());
      el.querySelectorAll('[data-group]').forEach(g => { const on = $('button.on', g); fd[g.dataset.group] = on ? on.dataset.v : ''; });
      await busy(ev.submitter || $('#pf button[type=submit]', el), async () => {
        try { const r = await api('/api/profile', fd); S.data.profile = r.profile; el.close(); haptic('success'); toast('Сохранено', 'ok'); await refresh(); } catch (e) { fail(e); }
      });
    });
  }
  // серия ношения (огонёк в шапке): число должно совпадать с WHOOP — «Сверить с WHOOP» ставит его вручную (POST /api/streak {n})
  function streakBody() {
    const st = streak(), a = S.data.streak && S.data.streak.anchored;
    const sd = st.since ? parseDay(st.since) : st.n ? new Date(parseDay(S.data.today.day).getTime() - (st.n - 1) * 864e5) : null;
    return `<div class="grid2"><div class="stat"><div class="k">Дней подряд</div><div class="v num">${st.n}${st.full ? '+' : ''}</div><div class="d">${a ? 'сверено с WHOOP' : 'по данным браслета'}</div></div>
      <div class="stat"><div class="k">Началась</div><div class="v sm num">${sd ? `${sd.getDate()} ${MON_S[sd.getMonth()]}` : '—'}</div><div class="d">${sd ? sd.getFullYear() : ''}</div></div></div>`;
  }
  function streakSheet() {
    haptic('tap');
    const el = sheet(`<h2>Серия</h2><p class="what-lead">Сколько дней подряд браслет на руке. Пропустил день — серия начинается заново.</p>
      <div class="sk-body">${streakBody()}</div>
      <div class="label">Сверить с WHOOP</div>
      <form class="card sk-form"><p class="small muted">В WHOOP другое число? Впиши его — дальше серия пойдёт от него.</p>
        <div class="sk-row"><input name="n" type="text" inputmode="numeric" pattern="[0-9]*" maxlength="4" autocomplete="off" aria-label="Дней подряд в WHOOP" placeholder="${streak().n}">
        <button class="btn primary" type="submit">Сохранить</button></div></form>`);
    $('.sk-form', el).addEventListener('submit', async ev => {
      ev.preventDefault();
      const inp = ev.target.elements.n, raw = inp.value.trim(), n = Number(raw);
      if (!/^\d{1,4}$/.test(raw) || n > 3650) { haptic('error'); toast('Число от 0 до 3650', 'err'); inp.focus(); return; }
      await busy(ev.submitter || $('.sk-form button', el), async () => {
        try {
          const r = await api('/api/streak', { n });
          S.data.streak = r.streak || { n, anchored: true };
          inp.value = ''; inp.placeholder = String(S.data.streak.n); inp.blur();
          morph($('.sk-body', el), streakBody());
          render(false);  // тихо: огонёк в шапке и «Ещё» — только отличия
          haptic('success'); toast('Серия обновлена', 'ok');
        } catch (e) { fail(e); }
      });
    });
  }

  // «＋» по центру меню (как в WHOOP): быстрые действия
  function plusSheet() {
    haptic('tap');
    const d = S.data, row = (a, v, ic, col, t, m) => `<button class="row tap" data-a="${a}" data-v="${v}"><span class="ic" style="color:${col}">${ic}</span>
      <span class="main"><div class="title">${t}</div><div class="meta">${m}</div></span><span class="end chev"></span></button>`;
    const el = sheet(`<h2>Добавить</h2><div class="list">
      ${row('tab', 'food', I.food, 'var(--food)', 'Еда', 'фото, поиск, БЖУ и соль')}
      ${row('water', '250', I.drop, 'var(--water)', 'Вода +250 мл', 'стакан')}
      ${row('water', '500', I.drop, 'var(--water)', 'Вода +500 мл', 'бутылка')}
      ${row('tab', 'coach', I.coach, 'var(--teal)', 'Спросить в чате', 'о чём угодно — видит твои данные')}
      ${row('summary', 'week', I.stats, 'var(--rec-blue)', 'Оценка и советы', 'день · неделя · месяц')}
      ${slot('plus', row)}
      ${d.connected ? row('sync', '', I.sync, 'var(--hint)', 'Обновить данные WHOOP', d.synced ? `были в ${clock(d.synced)}` : 'загрузить сейчас') : ''}</div>`);
    // действие выполняет общий обработчик кликов (он ниже по всплытию); лист закрываем сразу — иначе он спрячет «Назад» следующего листа
    el.addEventListener('click', ev => { if (ev.target.closest('[data-a]')) el.close(); });
  }

  // ---------- действия ----------
  async function refresh() {
    try { S.data = await api('/api/all'); S.dayData = null; render(false); } catch (e) { fail(e); }
  }
  async function brief() {
    if (S.briefing) return;
    S.briefing = true; haptic('tap'); render(false);
    try { const r = await api('/api/brief', {}); S.data.today.brief = r.brief; haptic('success'); } catch (e) { fail(e); }
    S.briefing = false; render(false);
  }
  async function connect() {
    haptic('tap');
    try { const r = await api('/api/connect', {}); if (tg) tg.openLink(r.url); else location.href = r.url; } catch (e) { fail(e); }
  }
  async function shiftTo(nd) {
    if (nd > S.data.today.day || nd === (S.dayData || S.data.today).day) return;
    haptic('select');
    const dir = nd > (S.dayData || S.data.today).day ? 'next' : 'prev';
    if (nd === S.data.today.day) { S.dayData = null; nav(dir, () => render(true)); return; }
    try { S.dayData = (await api('/api/day', { d: nd })).day; nav(dir, () => render(true)); } catch (e) { fail(e); }
  }
  const shiftDay = k => shiftTo(isoDay(new Date(parseDay((S.dayData || S.data.today).day).getTime() + k * 864e5)));

  document.addEventListener('click', async ev => {
    const el = ev.target.closest('[data-a]');
    if (!el || el.disabled) return;
    const a = el.dataset.a, v = el.dataset.v;
    if (F.actions[a]) return F.actions[a](el, v, ev);  // модуль может заменить и встроенное действие
    if (a === 'profile') profileSheet();
    else if (a === 'workout') openScreen('strain');
    else if (a === 'plan-open') openScreen('plan');
    else if (a === 'about') aboutSheet(v);
    else if (a === 'streak') streakSheet();
    else if (a === 'ask-go') { go('coach'); ask(v); }  // без модуля activity.js — общий экран нагрузки
    else if (a === 'tab') { const t = TABS.find(x => x.id === v); if (t) { haptic('select'); go(v); } }
    else if (a === 'connect') connect();
    else if (a === 'brief') brief();
    else if (a === 'detail') openScreen(v);
    else if (a === 'back') closeScreen();
    else if (a === 'goto') shiftTo(v);
    else if (a === 'explain') explain(v);
    else if (a === 'summary') summarySheet(v);
    else if (a === 'progress') makeProgress();
    else if (a === 'pg-open') { haptic('tap'); progressSheet(); }
    else if (a === 'day') shiftDay(Number(v));
    else if (a === 'range') { if (S.range === Number(v)) return; S.range = Number(v); haptic('select'); render(false); if (!S.series[S.range]) loadSeries(S.range); }
    else if (a === 'series-retry') { S.seriesErr = null; render(false); loadSeries(S.range); }
    else if (a === 'history-retry') { S.historyErr = false; render(false); loadHistory(); }
    else if (a === 'photo') pickPhoto(v);
    else if (a === 'ask') { const q = $('#q'); ask(q && q.value); }
    else if (a === 'suggest') ask(v);
    else if (a === 'clear') { haptic('tap'); try { S.history = (await api('/api/history/clear', {})).history; render(); } catch (e) { fail(e); } }
    else if (a === 'goal-add') { const g = $('#goal-new'); if (g && g.value.trim()) goalApi('/api/goal', { text: g.value.trim() }); }
    else if (a === 'goal-del') { haptic('tap'); goalApi('/api/goal/delete', { id: Number(v) }); }
    else if (a === 'water') {
      haptic('tap');
      try { S.data.today = (await api('/api/water', { ml: Number(v) })).day; render(false); toast(`💧 +${v} мл`, 'ok'); } catch (e) { fail(e); }
    } else if (a === 'water-del') {
      haptic('tap');
      try { S.data.today = (await api('/api/water/delete', { id: Number(v) })).day; render(false); } catch (e) { fail(e); }
    } else if (a === 'sync') {
      await busy(el, async () => {
        try { await api('/api/sync', {}); await refresh(); haptic('success'); toast('Данные обновлены', 'ok'); } catch (e) { fail(e); }
      });
    } else if (a === 'copy') {
      try { await navigator.clipboard.writeText(v); haptic('success'); toast('Скопировано', 'ok'); } catch (e) { toast('Выдели и скопируй адрес'); }
    } else if (a === 'open') { if (tg) tg.openLink(v); else window.open(v, '_blank'); }
    else if (a === 'tochat') {  // режим «💬 чат»: бот запомнит и пришлёт главное меню в чат, приложение закрывается
      try { await busy(el, () => api('/api/mode', { mode: 'chat' })); } catch (e) { fail(e); return; }
      S.data.mode = 'chat';
      if (tg && tg.close) tg.close(); else { toast('Режим чата включён — главное меню в чате', 'ok'); render(false); }
    }
  });
  document.addEventListener('input', ev => {
    if (ev.target.tagName === 'TEXTAREA' && ev.target.closest('.ask')) {
      ev.target.style.height = 'auto'; ev.target.style.height = Math.min(140, ev.target.scrollHeight) + 'px';
      if (ev.target.id === 'q') S.qDraft = ev.target.value;
    }
  });
  document.addEventListener('keydown', ev => {
    const typing = ev.target.closest && ev.target.closest('input, textarea, select, [contenteditable]');
    if (!typing && !ev.metaKey && !ev.ctrlKey && !ev.altKey && !document.querySelector('.sheet')) {
      if (ev.key === 'Escape' && S.screen) { ev.preventDefault(); closeScreen(); return; }
      if ((ev.key === 'ArrowLeft' || ev.key === 'ArrowRight') && !S.screen && S.tab === 'today') { ev.preventDefault(); shiftDay(ev.key === 'ArrowLeft' ? -1 : 1); return; }
    }
    if (ev.key === 'Enter' && !ev.shiftKey && ev.target.tagName === 'TEXTAREA' && ev.target.closest('.ask')) {
      ev.preventDefault();
      if (ev.target.id === 'q') ask(ev.target.value);
      else if (ev.target.id === 'goal-new') { if (ev.target.value.trim()) goalApi('/api/goal', { text: ev.target.value.trim() }); }
    }
  });
  // шапка становится стеклянной, когда под ней поехало содержимое; большой заголовок уехал — маленький появляется в полосе (как iOS)
  const navbar = document.createElement('header');
  navbar.id = 'navbar'; navbar.innerHTML = `<button type="button" class="bk" data-a="back" aria-label="Назад">${I.back}<span class="bk-t">Назад</span></button><b></b><span></span><div class="mini"></div>`;
  document.body.append(navbar);
  let scTick = false;
  function navSync() {
    scTick = false;
    document.body.classList.toggle('scrolled', window.scrollY > 6);
    // главная: кольца уехали вверх — в шапке маленькие «◯ СОН ◯ ВОССТАНОВЛЕНИЕ ◯ НАГРУЗКА» (как у WHOOP)
    const dials = !S.screen && S.tab === 'today' && view.querySelector('.dials');
    navbar.classList.toggle('rings', !!dials);
    if (dials) {
      navbar.querySelector('button').hidden = true;  // на главной «Назад» не нужен
      const mini = navbar.querySelector('.mini');
      if (!mini.dataset.k || mini.dataset.k !== dials.dataset.k) {
        mini.dataset.k = dials.dataset.k || '';
        mini.innerHTML = [...dials.querySelectorAll('.dial')].map(d => {
          const arc = d.querySelector('.arc'), r = arc.getAttribute('r'), dash = arc.getAttribute('stroke-dasharray'), off = arc.dataset.off;
          return `<button class="mini-r" data-a="detail" data-v="${d.dataset.v}" style="--c:${d.style.getPropertyValue('--c')}"><svg viewBox="0 0 100 100" aria-hidden="true">
            <circle cx="50" cy="50" r="${r}" fill="none" stroke="rgba(255,255,255,.14)" stroke-width="10"/><circle cx="50" cy="50" r="${r}" fill="none" stroke="var(--c)" stroke-width="10" stroke-dasharray="${dash}" stroke-dashoffset="${off}"/></svg>${esc(d.getAttribute('aria-label') || '')}</button>`;
        }).join('');
      }
      navbar.classList.toggle('show', dials.getBoundingClientRect().bottom < 60);
      return;
    }
    // экраны «Сон / Восстановление / Нагрузка» (шапка .wtop): в ч/б виде шапка не липнет — «Назад» после прокрутки даёт полоса
    const h1 = view.querySelector('.hero h1, .dhead h1') || (document.documentElement.classList.contains('mono') && view.querySelector('.wtop[data-t]'));
    if (!h1) { navbar.classList.remove('show'); return; }
    navbar.querySelector('b').textContent = (h1.dataset.t || h1.textContent).trim();
    navbar.querySelector('button').hidden = !S.screen;
    navbar.classList.toggle('show', h1.getBoundingClientRect().bottom < 52);
  }
  addEventListener('scroll', () => { if (!scTick) { scTick = true; requestAnimationFrame(navSync); } }, { passive: true });
  F.navSync = () => requestAnimationFrame(navSync);
  // свайп от левого края — «назад», как в iOS: экран едет за пальцем, отпустил дальше трети или смахнул — закрылся
  (() => {
    let x0 = null, y0 = 0, t0 = 0, dx = 0, on = false;
    addEventListener('touchstart', e => {
      if (!S.screen || e.touches.length !== 1 || document.querySelector('.sheet') || e.touches[0].clientX > 26) return;
      x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; t0 = performance.now(); dx = 0; on = false;
    }, { passive: true });
    addEventListener('touchmove', e => {
      if (x0 == null) return;
      const mx = e.touches[0].clientX - x0, my = e.touches[0].clientY - y0;
      if (!on) { if (mx > 8 && Math.abs(mx) > Math.abs(my) * 1.4) { on = true; view.classList.add('swiping'); } else if (Math.abs(my) > 10) { x0 = null; return; } else return; }
      e.preventDefault();
      dx = Math.max(0, mx);
      view.style.transform = `translate3d(${dx}px,0,0)`;
      view.style.boxShadow = '-20px 0 40px rgba(0,0,0,.45)';
    }, { passive: false });
    const end = () => {
      if (x0 == null) return;
      x0 = null;
      if (!on) return;
      const v = dx / Math.max(1, performance.now() - t0), w = view.offsetWidth || innerWidth;
      const reset = () => { view.classList.remove('swipe-end'); view.style.transform = ''; view.style.boxShadow = ''; };
      view.classList.remove('swiping'); view.classList.add('swipe-end');
      if (dx > w / 3 || v > .45) { view.style.transform = `translate3d(${w}px,0,0)`; setTimeout(() => { reset(); closeScreen(true); }, 300); }
      else { view.style.transform = 'translate3d(0,0,0)'; setTimeout(reset, 380); }
    };
    addEventListener('touchend', end); addEventListener('touchcancel', end);
  })();
  let rw = window.innerWidth;
  window.addEventListener('resize', () => { if (Math.abs(window.innerWidth - rw) > 20 && (S.tab === 'trends' || S.screen)) { rw = window.innerWidth; render(false); } });
  [WIDE, WIDE3].forEach(m => m.addEventListener && m.addEventListener('change', () => { tabInd(); render(false); }));

  function theme() {
    const root = document.documentElement;
    if (!root.classList.contains('mono')) { if (Kit.scheme) Kit.scheme(); return; }
    const mq = matchMedia('(prefers-color-scheme: light)');
    const light = tg && tg.colorScheme ? tg.colorScheme === 'light' : mq.matches;
    root.classList.toggle('lt', light);
    const m = document.querySelector('meta[name="color-scheme"]'); if (m) m.content = light ? 'light' : 'dark';
    const tc = document.querySelector('meta[name="theme-color"]'); if (tc) tc.content = light ? '#ffffff' : '#000000';
    if (Kit.scheme) Kit.scheme();  // шапка Telegram — в цвет фона
  }
  theme();
  try { matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => { if (!tg) { theme(); render(false); } }); } catch (e) { /* старый браузер */ }
  async function start() {
    const p = (new URLSearchParams(location.search).get('p') || '').replace(/^\//, '');
    if (['trends', 'health', 'food', 'coach'].includes(p)) S.tab = p === 'health' ? 'trends' : p;
    tabs = Kit.tabbar($('#tabs'), TABS, S.tab, go);
    const setTab = tabs.set; tabs.set = t => { setTab(t); tabInd(); };
    tabInd(); addEventListener('resize', tabInd);
    try { S.data = await api('/api/all'); } catch (e) {
      view.removeAttribute('aria-busy');
      view.innerHTML = `${'<header class="hero"><div class="titles"><h1>Форма</h1><div class="sub">Тренер по данным WHOOP</div></div></header>'}
        <div class="card"><div class="empty"><span class="e">⚠️</span><b>Не загрузилось</b>${esc(e.message)}</div><button class="btn primary" onclick="location.reload()">Повторить</button></div>`;
      return;
    }
    if (['sleep', 'rec', 'strain'].includes(p) || F.screens[p]) S.screen = p;
    F.onData.forEach(fn => { try { fn(S.data); } catch (e) { console.error(e); } });
    render(true);
    if (S.tab === 'trends') { if (!S.series[S.range]) loadSeries(S.range); loadProgress(); }
    if (S.tab === 'coach') loadHistory();
    if (p === 'profile') profileSheet();
    if (p === 'summary') summarySheet('week');
    if (tg && tg.onEvent) tg.onEvent('themeChanged', () => { theme(); render(false); });
    // данные обновляются сами, пока приложение открыто (Глеб 04.11: «в WHOOP уже есть, а Форма не подтянула» — сервер
    // получил их по вебхуку сразу, а экран ждал до 2 мин и не обновлялся при возврате в приложение):
    // каждые 30 с и сразу при возврате на экран; перерисовка — только если данные правда поменялись
    let lastPull = Date.now(), pulling = false, lastSig = JSON.stringify(S.data), later = 0;
    // пока листает или держит палец — ничего не перерисовываем и не перезагружаем (Глеб 05.11: «быстро листаешь — дёргается»)
    let lastActive = 0;
    const active = () => { lastActive = Date.now(); };
    ['touchstart', 'touchmove', 'wheel', 'pointerdown'].forEach(e => addEventListener(e, active, { passive: true, capture: true }));
    addEventListener('scroll', active, { passive: true, capture: true });
    F.idle = () => Date.now() - lastActive;
    // новая выкатка, а приложение открыто со старым кодом (Глеб 04.11: iPhone держит приложение с экрана «Домой» в памяти
    // часами — после переименования всё ещё было старое название): сервер шлёт версию файлов, другая — перезагрузка при
    // возврате в приложение (или сразу, если ничего не открыто и не набирается)
    const myV = ((document.querySelector('script[src*="app.js?v="]') || {}).src || '').split('v=')[1] || '';
    let stale = false;
    const reload = () => { try { sessionStorage.setItem('forma-reload', String(Date.now())); } catch (e) { /* приватный режим */ } location.reload(); };
    const recent = () => { try { return Date.now() - Number(sessionStorage.getItem('forma-reload') || 0) < 60000; } catch (e) { return false; } };
    const pull = async (resume) => {
      if (stale && resume === true && !recent()) return reload();
      if (pulling || document.hidden || document.querySelector('.sheet') || S.asking || S.briefing || S.photo || S.dayData) return;
      if (resume !== true && F.idle() < 2500) { clearTimeout(later); later = setTimeout(() => pull(false), 2600); return; }
      if (document.activeElement && ['TEXTAREA', 'INPUT'].includes(document.activeElement.tagName)) return;
      pulling = true; lastPull = Date.now();
      try {
        const d = await api('/api/all');
        if (d && d.v && myV && d.v !== myV && !recent()) {
          stale = true;
          // новая версия: при возврате в приложение — сразу; иначе — только если минуту ничего не трогал (не посреди чтения)
          if (resume === true || (!S.screen && S.tab !== 'coach' && F.idle() > 60000)) return reload();
        }
        const sig = JSON.stringify(d);
        if (sig === lastSig) return;
        lastSig = sig; S.data = d;
        F.onData.forEach(fn => { try { fn(S.data); } catch (e) { console.error(e); } });
        if (S.tab !== 'coach' && !S.screen) render(false);
      } catch (e) { /* тихо */ } finally { pulling = false; }
    };
    F.pull = pull;
    setInterval(() => pull(false), 30000);
    const back = () => { if (!document.hidden && (stale || Date.now() - lastPull > 10000)) pull(true); };
    document.addEventListener('visibilitychange', back);
    addEventListener('pageshow', back);
    addEventListener('focus', back);
  }
  // всё, чем пользуются модули
  Object.assign(F, { morph, S, I, C, ZONE, WD, WD_S, MON, MON_S, tg, esc, api, toast, haptic, sheet, busy, $, num, big, hm, hHuman, liters, pct, plural, fail, clock,
    isoDay, parseDay, dateWord, dateLine, md, tgHtml, cnt, dial, ring, bar, bars, line, avg, animate, render, go, openScreen, closeScreen,
    dHead, cap, head, hero, chartCard, chBlock, chartLead, CHARTS, PER, prevOf, longest, metric, skel, retryCard, vs, sportIcon, strainScale, explain, ask, loadHistory, loadSeries,
    summarySheet, profileSheet, streakSheet, refresh, shiftTo, svgi, lastRows, sub0, nav, theme, wide });
  // вид WHOOP: «Прогноз на день» открывает совет дня листом (карточки над «Моим днём» у WHOOP нет)
  if (!F.actions.outlook) F.actions.outlook = () => { if (S.outlookHtml) sheet(`<h2>Прогноз на день</h2>${S.outlookHtml}`); else openScreen('rec'); };
  (window.FormaModules || []).forEach(m => { try { m(F); } catch (e) { console.error('модуль', e); } });
  start();
})();
