/* Форма — приложение тренера внутри Telegram. Общий вид — kit.css/kit.js, здесь только своё. Без сборки. */
(() => {
  const { tg, esc, api, toast, haptic, sheet, busy } = Kit;
  const $ = (s, el = document) => el.querySelector(s);
  // Плавающая «🛠» создаётся из body[data-support], но пока атрибут стоит на body, kit.js считает «поддержкой» любой тап по странице
  // (closest('[data-support]') находит body) — создаём кнопку сами и снимаем атрибут.
  Kit.supportFab(); delete document.body.dataset.support;
  const view = $('#view');
  // Модули (journal.js, plan.js, …) подключаются ДО app.js и кладут функцию в window.FormaModules; app.js вызывает
  // каждую с объектом F перед стартом. Что модуль может: F.screens[вид] — экран «подробностей» (открыть: F.openScreen(вид, арг)),
  // F.actions[data-a] — обработчик кнопки, F.views[вкладка] — заменить вкладку, F.slots.<место> — куски HTML, F.onData — после загрузки.
  const F = window.F = { screens: {}, actions: {}, views: {}, onData: [],
    slots: { homeTop: [], homeAfterOutlook: [], homeAfterMyDay: [], homeJournal: [], homeAfterDashboard: [],
      healthTop: [], healthAfterMonitor: [], healthBottom: [], sleep: [], rec: [], strain: [], coachTop: [], plus: [] } };
  const slot = (name, ...args) => F.slots[name].map(fn => { try { return fn(...args) || ''; } catch (e) { console.error(name, e); return ''; } }).join('');
  const S = { data: null, tab: 'today', dayData: null, range: 30, series: {}, history: null, progress: undefined,
    asking: false, eating: false, briefing: false, progressing: false, photo: false };

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
        .replace(/(\d)–(\d)/g, '$1\u2060–\u2060$2');
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
  // карточки появляются по мере прокрутки: всё, что ниже экрана, ждёт своей очереди
  // в автоснимках (shots.js, navigator.webdriver) страница снимается целиком — показываем всё сразу; #motion — проверка анимаций
  const revealer = 'IntersectionObserver' in window && !(navigator.webdriver && !location.hash.includes('motion')) ? new IntersectionObserver(es => es.forEach(e => {
    if (!e.isIntersecting) return;
    revealer.unobserve(e.target);
    e.target.classList.remove('later'); e.target.classList.add('shown');
    animate(e.target);
  }), { rootMargin: '0px 0px -6% 0px' }) : null;
  function stagger() {
    let i = 0;
    view.querySelectorAll('.rise').forEach(el => {
      if (revealer && !REDUCED.matches && el.getBoundingClientRect().top > innerHeight + 10) { el.classList.add('later'); revealer.observe(el); }
      else el.style.setProperty('--i', Math.min(i++, 10));
    });
  }
  // переходы между экранами как в iOS: View Transitions (iOS 18+, Chrome), иначе — въезд нового экрана
  function nav(kind, fn) {
    const root = document.documentElement;
    if (REDUCED.matches) { fn(); return; }
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
  const chartW = () => Math.max(200, Math.min(($('#app') || view).clientWidth || 358, 800) - 40);  // карточка: padding 20 + 20
  const CH = 112, LAB = 22;
  function xLabels(days, W, slot) {
    const n = days.length, fits = Math.max(2, Math.floor(W / (n <= 7 ? 30 : 58)));
    const k = Math.min(n, fits), idx = new Set();
    for (let i = 0; i < k; i++) idx.add(Math.round(i * (n - 1) / Math.max(1, k - 1)));
    let s = '';
    idx.forEach(i => {
      const dt = parseDay(days[i]), x = (i + .5) * slot;
      const t = n <= 7 ? WD_S[dt.getDay()] : `${dt.getDate()} ${MON_S[dt.getMonth()]}`;
      const anchor = n > 7 && i === 0 ? 'start' : n > 7 && i === n - 1 ? 'end' : 'middle';
      s += `<text x="${anchor === 'start' ? 0 : anchor === 'end' ? W : x.toFixed(1)}" y="${CH + 16}" text-anchor="${anchor}">${t}</text>`;
    });
    return s;
  }
  const svg = (W, s, rows) => `<svg class="chart" viewBox="0 0 ${W} ${CH + LAB}" width="${W}" height="${CH + LAB}" role="img">${s}${xLabels(rows.map(r => r.day), W, W / rows.length)}</svg>`;
  function bars(rows, key, { max, color, ref } = {}) {
    const W = chartW(), n = rows.length, slot = W / n;
    const vals = rows.map(r => r[key]).filter(v => v != null);
    if (!vals.length) return '<div class="chart-empty">Нет данных за этот период</div>';
    const top = max || Math.max(1, ...vals, ...(ref ? rows.map(r => r[ref] || 0) : [])) * 1.08;
    const gap = n > 60 ? 1 : n > 20 ? 2 : Math.min(10, slot * .35), bw = Math.max(1, slot - gap);
    let s = `<line class="axis" x1="0" x2="${W}" y1="${CH + .5}" y2="${CH + .5}"/>`;
    rows.forEach((r, i) => {
      const v = r[key]; if (v == null) return;
      const h = Math.max(2, v / top * CH), x = i * slot + gap / 2;
      s += `<rect class="gb" style="--i:${Math.round(i / n * 24)}" x="${x.toFixed(1)}" y="${(CH - h).toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" rx="${Math.min(3, bw / 2).toFixed(1)}" fill="${typeof color === 'function' ? color(r) : color}"><title>${r.day}: ${v}</title></rect>`;
    });
    if (ref) rows.forEach((r, i) => {
      const v = r[ref]; if (v == null) return;
      const y = CH - v / top * CH;
      s += `<line class="ref" x1="${(i * slot + gap / 2).toFixed(1)}" x2="${((i + 1) * slot - gap / 2).toFixed(1)}" y1="${y.toFixed(1)}" y2="${y.toFixed(1)}"/>`;
    });
    return svg(W, s, rows);
  }
  function line(rows, key, color, base) {
    const W = chartW(), n = rows.length, slot = W / n;
    const pts = rows.map((r, i) => [(i + .5) * slot, r[key]]).filter(p => p[1] != null);
    if (pts.length < 2) return '<div class="chart-empty">Мало данных для графика</div>';
    const vs = pts.map(p => p[1]).concat(base != null ? [base] : []);
    let lo = Math.min(...vs), hi = Math.max(...vs); const sp = (hi - lo) || 1; lo -= sp * .15; hi += sp * .25;
    const y = v => CH - 6 - (v - lo) / (hi - lo) * (CH - 12);
    const P = pts.map(p => [p[0], y(p[1])]);
    let d = `M${P[0][0].toFixed(1)},${P[0][1].toFixed(1)}`;
    for (let i = 1; i < P.length; i++) { const [x0, y0] = P[i - 1], [x1, y1] = P[i], cx = (x0 + x1) / 2; d += ` C${cx.toFixed(1)},${y0.toFixed(1)} ${cx.toFixed(1)},${y1.toFixed(1)} ${x1.toFixed(1)},${y1.toFixed(1)}`; }
    const id = 'g' + key + Math.random().toString(36).slice(2, 6);
    let s = `<defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".25"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>`;
    s += `<line class="axis" x1="0" x2="${W}" y1="${CH + .5}" y2="${CH + .5}"/>`;
    if (base != null) {
      const yb = y(base);
      s += `<line class="norm" x1="0" x2="${W}" y1="${yb.toFixed(1)}" y2="${yb.toFixed(1)}"/><text class="norm-t" x="${W}" y="${(yb > 18 ? yb - 6 : yb + 14).toFixed(1)}" text-anchor="end">норма ${num(base)}</text>`;
    }
    s += `<path class="fillin" d="${d} L${P[P.length - 1][0].toFixed(1)},${CH} L${P[0][0].toFixed(1)},${CH} Z" fill="url(#${id})"/>`;
    s += `<path class="draw" pathLength="1" d="${d}" fill="none" stroke="${color}" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round"/>`;
    const last = P[P.length - 1];
    s += `<circle class="dotin" cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="4" fill="${color}" stroke="#1a2227" stroke-width="2"/>`;
    return svg(W, s, rows);
  }
  const avg = (rows, k) => { const v = rows.map(r => r[k]).filter(x => x != null); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
  const retryCard = (title, act) => `<section class="card"><div class="empty"><span class="e">⚠️</span><b>${title}</b>Проверь интернет и попробуй ещё раз.</div>
    <button class="btn tonal" data-a="${act}">Повторить</button></section>`;
  const skel = (n = 3) => Array.from({ length: n }, (_, i) => `<div class="sk line" style="width:${[92, 78, 86, 60][i % 4]}%"></div>`).join('');

  // ---------- каркас ----------
  // нижнее меню как в WHOOP: Главная · Здоровье · «＋» по центру (быстрые действия) · Питание · Тренер (Coach — в правом углу)
  const TABS = [{ id: 'today', icon: I.home, label: 'Главная' }, { id: 'trends', icon: I.health, label: 'Здоровье' },
    { id: 'plus', icon: I.plus, label: 'Добавить' }, { id: 'food', icon: I.food, label: 'Питание' }, { id: 'coach', icon: I.coach, label: 'Тренер' }];
  let tabs;
  // подложка активной вкладки переезжает пружиной (меню перерисовывается целиком — помним, откуда ехать)
  function tabInd() {
    const bar = $('#tabs'); if (!bar) return;
    const on = bar.querySelector('button.on:not([data-tab="coach"]):not([data-tab="plus"])');
    let ind = bar.querySelector('.tab-ind');
    if (!ind) {
      ind = document.createElement('i'); ind.className = 'tab-ind'; bar.append(ind);
      if (S.indX != null) { ind.style.transition = 'none'; ind.style.transform = `translateX(${S.indX}px)`; ind.style.width = S.indW + 'px'; ind.style.opacity = S.indO; void ind.offsetWidth; ind.style.transition = ''; }
    }
    if (!on) { ind.style.opacity = 0; S.indO = 0; return; }
    S.indX = on.offsetLeft; S.indW = on.offsetWidth; S.indO = 1;
    ind.style.transform = `translateX(${S.indX}px)`; ind.style.width = S.indW + 'px'; ind.style.opacity = 1;
  }
  function go(tab) {
    if (tab === 'plus') { tabs.set(S.tab); plusSheet(); return; }
    if (S.tab === tab && !S.screen) { window.scrollTo({ top: 0, behavior: 'smooth' }); return; }
    S.tab = tab; S.screen = null; tabs.set(tab);
    nav('tab', () => { render(true); window.scrollTo(0, 0); });
    if (tab === 'trends' && !S.series[S.range]) loadSeries(S.range);
    if (tab === 'trends' && S.progress === undefined) loadProgress();
    if (tab === 'coach' && !S.history) loadHistory();
  }
  function render(anim) {
    if (!S.data) return;
    const html = S.screen ? screenView(S.screen) : (F.views[S.tab] || { today: todayView, trends: trendsView, food: foodView, coach: coachView }[S.tab])();
    view.removeAttribute('aria-busy');
    if (revealer) revealer.disconnect();
    // «назад» возвращает прежний экран как был — без повторного набегания цифр и колец
    const moving = (anim || S.navKind || S.inVT) && !S.popping;
    const cls = [S.inVT ? 'enter vt' : S.navKind ? `enter nav-${S.navKind}` : anim ? 'enter' : '', moving ? '' : 'calm'].join(' ').trim();
    view.innerHTML = `<div class="${cls}">${html}</div>`;
    if (moving) stagger();
    animate(document, !moving);
    if (S.tab === 'coach' && anim !== false) window.scrollTo(0, document.body.scrollHeight);
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
    const q = full ? [[250, 'стакан'], [500, 'бутылка'], [1000, 'литр']] : [[250, 'стакан'], [500, 'бутылка']];
    return `<section class="card rise">${head(`<span class="hico" style="--hc:var(--water)">${I.drop}</span>Вода`, `<span class="num"><b>${liters(w.drank)}</b> / ${liters(w.target)}</span>`)}
      ${bar(pct(w.drank, w.target), 'var(--water)')}
      ${w.note ? `<p class="small muted">${esc(w.note)}</p>` : ''}
      <div class="btns">${q.map(([ml, k]) => `<button class="btn tonal water-b" data-a="water" data-v="${ml}"><b class="num">+${ml}</b><span>${k}</span></button>`).join('')}</div>
      ${full && w.log.length ? `<div class="logs">${w.log.map(x => `<span class="pill num">${esc(x.time)} · ${x.ml} мл<button data-a="water-del" data-v="${x.id}" aria-label="Удалить">✕</button></span>`).join('')}</div>` : ''}
      ${full ? `<p class="small muted">Норма: ${esc(w.why)}. Боту можно писать «выпил 500 мл».</p>` : ''}</section>`;
  }

  function foodMini(t) {
    const n = t.nutrition, e = t.eaten;
    const right = n ? `<span class="num"><b>${big(e.kcal)}</b> / ${big(n.kcal)} ккал</span>` : `<span class="num"><b>${big(e.kcal)}</b> ккал</span>`;
    return `<section class="card rise tap" data-a="tab" data-v="food" role="button">${head(`<span class="hico" style="--hc:var(--food)">${I.food}</span>Питание`, right + '<span class="chev"></span>')}
      ${n ? bar(pct(e.kcal, n.kcal), 'var(--food)') : ''}
      <div class="macros num">${n ? `<span>Б <b>${e.protein}</b>/${n.protein}</span><span>Ж <b>${e.fat}</b>/${n.fat}</span><span>У <b>${e.carbs}</b>/${n.carbs} г</span>`
        : `<span>${e.count ? `${e.count} ${plural(e.count, 'приём', 'приёма', 'приёмов')}` : 'Пока ничего — пришли фото еды'}</span>`}</div></section>`;
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
    const ser = S.data.series || [], has = r => r.recovery != null || r.sleep != null || r.strain != null;
    let i = ser.length - 1, n = 0;
    if (i >= 0 && !has(ser[i])) i--;  // сегодня WHOOP мог ещё не прислать — серия не рвётся
    for (; i >= 0 && has(ser[i]); i--) n++;
    return { n, full: n > 0 && n >= ser.length - 1 };
  }
  const cap = (title, right = '') => `<div class="cap"><h3>${title}</h3>${right ? `<span class="cap-r">${right}</span>` : ''}</div>`;

  // «Strain Target»: шкала 0–21, целевой отрезок по восстановлению и где ты сейчас
  const STRAIN_BANDS = [[0, 10, 'Лёгкая'], [10, 14, 'Средняя'], [14, 18, 'Высокая'], [18, 21, 'Макс']];
  function strainScale(t, sv) {
    const band = t.band, p = v => `${pct(v, 21)}%`;
    return `<div class="scale21"><span class="band21" style="left:${band ? p(band[0]) : 0};width:${band ? `calc(${p(band[1])} - ${p(band[0])})` : 0}"></span>
        ${sv != null ? `<span class="mark21" style="left:${p(sv)}"><b class="num">${num(sv, 1)}</b></span>` : ''}</div>
      <div class="ticks num">${STRAIN_BANDS.map(([a, b, k]) => `<span style="flex:${b - a}">${k}</span>`).join('')}</div>`;
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
      <button class="outrow" data-a="detail" data-v="rec"><span>${t.day === S.data.today.day ? 'Прогноз на день' : 'Разбор дня'}</span>${I.next}</button>
      <div class="card acts"><div class="acts-h"><b>Активности</b><span class="num">${rows.length || ''}</span></div>${rows.length ? rows.join('') : '<p class="muted small">Сна и тренировок за этот день пока нет — WHOOP пришлёт их после записи.</p>'}</div></div>`;
  }
  // «My Dashboard»: плитки показателей со стрелкой к норме за 30 дней
  function tile({ k, v, d = 0, unit = '', base, goodUp, ex, sub }) {
    return `<button class="tile" data-a="explain" data-v="${ex}"><span class="tile-k">${k}</span>
      <span class="tile-v"><b class="num">${v == null ? '—' : cnt(v, d)}</b><small>${unit}</small></span>
      <span class="tile-d">${vs(v, base, goodUp, d) || `<span class="vs flat">${sub || (base != null ? `норма ${num(base, d)}` : 'нет нормы')}</span>`}</span></button>`;
  }
  function dashboard(t) {
    const r = t.recovery || {}, s = t.sleep || {}, c = t.cycle || {}, b = t.base || {};
    return `<div class="sec rise">${cap('Моя панель', 'к норме за 30 дней')}<div class="tiles">
      ${tile({ k: 'ВСР', v: r.hrv, unit: 'мс', base: b.hrv, goodUp: true, ex: 'hrv' })}
      ${tile({ k: 'Пульс покоя', v: r.rhr, unit: 'уд/мин', base: b.rhr, goodUp: false, ex: 'rhr' })}
      ${tile({ k: 'Дыхание', v: s.resp, d: 1, unit: 'в мин', base: b.resp, goodUp: null, ex: 'resp' })}
      ${tile({ k: 'SpO₂', v: r.spo2, d: 1, unit: '%', goodUp: true, ex: 'spo2', sub: 'норма 95–100%' })}
      ${tile({ k: 'Темп. кожи', v: r.skin_temp, d: 1, unit: '°C', base: b.skin_temp, goodUp: null, ex: 'temp' })}
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
        ${st.n ? `<span class="fl">${I.flame}<b class="num">${st.n}${st.full ? '+' : ''}</b></span>` : ''}</button>
      <div class="datecap"><button class="pg" data-a="day" data-v="-1" aria-label="Предыдущий день">${I.back}</button>
        <span class="dc-t">${isToday ? 'Сегодня' : `${WD_S[dd.getDay()]}, ${dd.getDate()} ${MON_S[dd.getMonth()]}`}</span>
        <button class="pg" data-a="day" data-v="1" aria-label="Следующий день" ${isToday ? 'disabled' : ''}>${I.next}</button></div>
      <button class="syncpill" data-a="sync" aria-label="Обновить данные WHOOP">${I.sync}<span class="num">${d.synced ? clock(d.synced) : '—'}</span></button></header>
      <div class="wordmark">ФОРМА</div>`;
    h += slot('homeTop', t, isToday);
    h += `<section class="dials rise">
        ${dial(sleepP, 'var(--sleep)', sleepV != null ? `<b class="num">${cnt(sleepV)}<small>%</small></b>` : '<b>—</b>', 'Сон', '', 'sleep')}
        ${dial(r ? r.score / 100 : 0, recC, r ? `<b class="num">${cnt(r.score)}<small>%</small></b>` : '<b>—</b>', 'Восстановление', '', 'rec')}
        ${dial(c && c.strain != null ? c.strain / 21 : 0, 'var(--strain)', c && c.strain != null ? `<b class="num">${cnt(c.strain, 1)}</b>` : '<b>—</b>', 'Нагрузка', '', 'strain')}
      </section>`;

    // «Daily Outlook»: совет на день + «Узнать у тренера»
    const adv = advice(t);
    h += `<section class="card outlook rise"><h3 class="ot">${r ? `<i class="zdot" style="background:${recC}"></i>${ZONE[z]}` : isToday ? 'Прогноз на день' : 'Разбор дня'}</h3>
      ${S.briefing ? skel(2) : adv ? `<div class="prose advice">${adv}</div>`
        : `<p class="muted">${!isToday ? 'Разбора за этот день нет.' : d.morning === 'off' ? 'Утренний разбор выключен — время в профиле.' : `Разбор придёт утром, с ${esc(d.morning || '07:00')}, как только WHOOP посчитает сон.`}</p>`}
      ${isToday ? `<div class="olinks">${t.brief || (t.tips || []).length ? '<button class="link" data-a="detail" data-v="rec">Весь разбор</button>' : ''}${!t.brief && !S.briefing ? '<button class="link" data-a="brief">Разобрать сейчас</button>' : ''}
        <button class="coach-link" data-a="tab" data-v="coach">${I.coach}Узнать у тренера</button></div>` : ''}</section>`;

    const ms = monStat(t), msOk = ms.all && ms.ok === ms.all, sv = c && c.strain != null ? c.strain : null;
    h += `<div class="mons rise"><button class="mon" data-a="tab" data-v="trends"><span class="mon-k">Монитор здоровья${I.next}</span>
        <span class="mon-s"><i class="mon-b" style="--c:${!ms.all ? 'var(--hint)' : msOk ? 'var(--teal)' : ms.ok >= ms.all - 1 ? C.yellow : C.red}">${msOk ? I.check : '!'}</i>
        <span><b style="color:${!ms.all ? 'var(--hint)' : msOk ? 'var(--teal)' : ms.ok >= ms.all - 1 ? C.yellow : C.red}">${!ms.all ? 'Нет данных' : msOk ? 'В норме' : 'Есть отклонения'}</b>${ms.all ? `${ms.ok}/${ms.all} показателей` : 'нужна неделя'}</span></span></button>
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
    rec: { t: 'Что это', p: ['Восстановление — насколько организм отдохнул и готов к нагрузке сегодня. WHOOP считает его ночью по ВСР, пульсу покоя, дыханию и сну.',
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
    if (v == null) return 'Сегодня ВСР ещё не посчитана — это делается ночью.';
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
      h = `<h2>ВСР — вариабельность пульса</h2>${norm(r.hrv, b.hrv, ' мс', true)}
        <div class="note"><span>${r.hrv != null && b.hrv != null && r.hrv >= b.hrv ? '🟢' : '🟡'}</span><span>${esc(explainHrv(r.hrv, b.hrv))}</span></div>
        ${box('Что это', '<p>Сердце бьётся не как метроном: промежутки между ударами чуть-чуть разные. ВСР показывает, насколько они разные, — WHOOP меряет её ночью.</p><p><b>Выше — лучше:</b> нервная система отдохнула. <b>Ниже обычного</b> — усталость, стресс, недосып, болезнь, поздняя еда.</p><p>Сравнивай только со своей нормой — у всех она своя, чужие цифры не важны. Стрелка ↑/↓ — разница с твоей нормой за 30 дней.</p>')}
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
        ${box('Что это', '<p>Сколько вдохов в минуту ты делаешь во сне. У здорового человека она очень стабильна — обычно 12–18.</p><p>Заметный рост (на 1–2 и больше) бывает при простуде, жаре в комнате или сильной усталости. Важна не сама цифра, а отклонение от твоей нормы.</p>')}
        ${box('Что делать', '<ul><li>Проветривай комнату, спи в прохладе (18–20 °C)</li><li>Если дыхание выше нормы вместе с пульсом — сегодня без тяжёлой тренировки</li></ul>')}`;
    } else if (kind === 'spo2') {
      h = `<h2>SpO₂ — кислород в крови</h2>${norm(r.spo2, null, ' %', true, 1)}
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
  function closeScreen() {
    haptic('select');
    S.screen = null;
    nav('pop', () => { S.popping = true; try { render(false); } finally { S.popping = false; } window.scrollTo(0, S.scrollY || 0); });
  }
  const dHead = (title, sub, color) => `<header class="dhead"><button class="avatar ghost" data-a="back" aria-label="Назад">${I.back}</button>
    <div class="titles"><h1 style="--c:${color}">${title}</h1><div class="sub">${sub}</div></div></header>`;
  const lastRows = n => (S.data.series || []).slice(-n);
  const dNoData = (title, text) => `<div class="empty"><span class="e">${I.moon}</span><b>${title}</b>${text}</div>`;
  const metric = (k, v, sub) => `<div class="stat"><div class="k">${k}</div><div class="v num">${v}</div>${sub ? `<div class="d">${sub}</div>` : ''}</div>`;
  const chartCard = (title, side, chart) => `<section class="card rise">${head(title, side)}<div class="chart-box">${chart}</div></section>`;
  const sub0 = t => `${dateWord(t.day)}, ${dateLine(t.day)}`;

  function screenView(kind) {
    const d = S.data, t = S.dayData || d.today;
    const own = { sleep: sleepScreen, rec: recScreen, strain: strainScreen }[kind];
    if (own) return own(t, d) + slot(kind, t, d);
    return F.screens[kind] ? F.screens[kind](t, d, S.screenArg) : dHead('Нет экрана', '', 'var(--ink)');
  }

  // ---------- разборы как в WHOOP: дата по центру и «i», большое кольцо, «что влияет» с уголком к кольцу, вывод в рамке ----------
  const dTop = (t, kind) => `<header class="wtop"><button class="wbtn" data-a="back" aria-label="Назад">${I.back}</button>
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
      <div class="wring-in"><b class="num">${value}</b><span>${label}</span></div></div></section>`;
  }
  // треугольник к норме за 30 дней: форма — куда сдвинулось, цвет — хорошо (бирюза) / плохо (оранжевый) / ни то ни другое (серый)
  function tri(v, b, goodUp, d = 0) {
    if (v == null || b == null) return '<i class="tri dot"></i>';
    const x = v - b;
    if (Math.abs(x) < (d ? .05 : .5)) return '<i class="tri dot"></i>';
    const col = goodUp == null ? 'flat' : (goodUp ? x > 0 : x < 0) ? 'good' : 'bad';
    return `<i class="tri ${x > 0 ? 'up' : 'down'} ${col}"></i>`;
  }
  const crow = (ic, k, v, ind, ex) => `<button class="crow" ${ex ? `data-a="explain" data-v="${ex}"` : 'type="button" tabindex="-1"'}><span class="cic">${ic}</span>
    <span class="ck">${k}</span>${ind.startsWith('<span class="seg3') ? ind + `<span class="cv num">${v}</span>` : `<span class="cv num">${v}</span>${ind}`}</button>`;
  const vsFoot = '<div class="cfoot"><i class="tri up good"></i><i class="tri down bad"></i><b>Сегодня</b>&nbsp;против 30 дней</div>';
  // три деления «плохо / достаточно / оптимально»; границы — наш расчёт (WHOOP свои не публикует)
  const lvl3 = (v, a, b) => v == null ? -1 : v < a ? 0 : v < b ? 1 : 2;
  const seg3 = lvl => `<span class="seg3">${[0, 1, 2].map(i => `<i class="${i === lvl ? ['poor', 'ok', 'best'][i] : ''}"></i>`).join('')}</span>`;
  const segFoot = '<div class="cfoot"><i class="lg poor"></i>Плохо<i class="lg ok"></i>Достаточно<i class="lg best"></i>Оптимально</div>';
  const insight = (text, link, q) => `<section class="winsight rise"><p>${text}</p><button class="wlink" data-a="ask-go" data-v="${esc(q)}">${link}${I.arrow}</button></section>`;
  const cmpWord = (v, b, tol, up, down, same) => v == null || b == null ? null : v - b > tol ? up : b - v > tol ? down : same;

  // фазы сна: какую долю ночи занял каждый этап (подробной по минутам кривой WHOOP API не отдаёт — только итоги по фазам)
  const STAGES = [['awake', 'Бодрствование', 'var(--st-awake)'], ['rem', 'REM', 'var(--st-rem)'], ['light', 'Лёгкий', 'var(--st-light)'], ['deep', 'Глубокий (SWS)', 'var(--st-deep)']];
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
      ${crow(I.sparkle, 'Глубокий + REM', restP != null ? restP + '%' : '—', seg3(L.rest))}${segFoot}</section>`;
    const lv = perf == null ? -1 : perf >= 85 ? 2 : perf >= 70 ? 1 : 0;
    const weak = [['режим сна', L.cons], ['часы сна', L.hvn], ['эффективность', L.eff], ['глубокий сон и REM', L.rest]].filter(x => x[1] === 0 || x[1] === 1).sort((a, c) => a[1] - c[1])[0];
    h += insight(`${['Сна не хватило', 'Сон достаточный, но есть куда расти', 'Сон оптимальный — организм хорошо восстановился'][lv] || 'Сон посчитан'}${weak && lv < 2 ? ` — подтянуть стоит ${weak[0]}` : ''}.`,
      'Разобрать мой сон', `Разбери мой сон за ${dateLine(t.day)}: что улучшить?`);
    const mx = Math.max(s.hours, s.need, 1);
    h += `<section class="card rise">${cap('Часы и потребность', `<span class="num">${s.debt ? 'недосып ' + hm(s.debt) : 'недосыпа нет'}</span>`)}
      <div class="hv"><div class="hv-r"><span>Спал · ${esc(s.start)}–${esc(s.end)}</span><b class="num">${hHuman(s.hours)}</b></div>${bar(pct(s.hours, mx), 'var(--sleep)')}
        <div class="hv-r"><span>Нужно</span><b class="num">${hHuman(s.need)}</b></div>${bar(pct(s.need, mx), 'var(--st-awake)')}
        ${b.sleep != null ? `<div class="hv-r"><span>Обычно</span><b class="num">${hHuman(b.sleep)}</b></div>${bar(pct(b.sleep, mx), 'color-mix(in srgb, var(--sleep) 45%, transparent)')}` : ''}</div></section>`;
    h += `<section class="card rise">${cap('Фазы сна', `<span class="num">в постели ${hm(inBed)}</span>`)}
      <div class="stagebar">${STAGES.map(([k, , col]) => s[k] ? `<span style="flex:${s[k]};background:${col}"></span>` : '').join('')}</div>
      <div class="steps-chart">${STAGES.map(([k, name, col], i) => {
        const v = s[k] || 0, p = v / inBed * 100;
        return `<div class="stp"><span class="stn"><i style="background:${col}"></i>${name}</span>
          <span class="stbar"><span style="margin-left:${(i * 3).toFixed(0)}%;width:0;background:${col}" data-w="${Math.max(1.5, p).toFixed(1)}%"></span></span>
          <span class="stv num"><b>${hm(v)}</b><small>${p.toFixed(0)}%</small></span></div>`;
      }).join('')}</div><p class="small muted">Восстанавливающий сон — глубокий ${hm(s.deep)} + REM ${hm(s.rem)} = ${hm(rest)}.</p></section>`;
    h += `<div class="grid2 metrics rise">${metric('Дыхание', s.resp != null ? num(s.resp, 1) : '—', b.resp != null ? `обычно ${num(b.resp, 1)} в мин` : 'в минуту')}
      ${metric('Пробуждения', s.disturbances != null ? s.disturbances : '—', s.cycles != null ? `${s.cycles} ${plural(s.cycles, 'цикл', 'цикла', 'циклов')} сна` : '')}</div>`;
    h += chartCard('Сон за 14 дней', `<span class="num"><i class="tick"></i>нужно</span>`, bars(lastRows(14), 'sleep', { color: 'var(--sleep)', ref: 'need' }));
    return h;
  }

  function recScreen(t) {
    const r = t.recovery, b = t.base || {}, d = S.data, s = t.sleep || {};
    let h = dTop(t, 'rec');
    if (!r) return h + dNoData('Ещё не посчитано', 'WHOOP считает восстановление ночью, по готовности сна.');
    h += bigRing(r.score / 100, C[r.zone], `${cnt(r.score)}<small>%</small>`, 'Восстановление');
    if (r.calibrating) h += '<div class="note rise"><span>⏳</span><span>WHOOP ещё калибруется — первые недели цифры неточные.</span></div>';
    const perfs = (d.series || []).filter(x => x.day < t.day).map(x => x.perf).filter(x => x != null), pb = perfs.length >= 5 ? perfs.reduce((a, x) => a + x, 0) / perfs.length : null;
    h += `<section class="wcon rise">${crow(I.hrv, 'Вариабельность пульса', r.hrv != null ? Math.round(r.hrv) : '—', tri(r.hrv, b.hrv, true), 'hrv')}
      ${crow(I.rhr, 'Пульс покоя', r.rhr != null ? r.rhr : '—', tri(r.rhr, b.rhr, false), 'rhr')}
      ${crow(I.lungs, 'Частота дыхания', s.resp != null ? num(s.resp, 1) : '—', tri(s.resp, b.resp, null, 1), 'resp')}
      ${crow(I.moon, 'Выполнение сна', s.performance != null ? s.performance + '%' : '—', tri(s.performance, pb, true))}${vsFoot}</section>`;
    const parts = [cmpWord(r.hrv, b.hrv, (b.hrv || 0) * .07, 'ВСР выше обычного', 'ВСР ниже обычного', 'ВСР как обычно'),
      cmpWord(r.rhr, b.rhr, 2, 'пульс покоя выше', 'пульс покоя ниже', 'пульс покоя обычный')].filter(Boolean);
    h += insight(`${parts.length ? parts.join(', ') + ' — ' : ''}${{ green: 'организм готов к нагрузке сегодня', yellow: 'сегодня лучше умеренная нагрузка', red: 'сегодня день восстановления' }[r.zone]}.`.replace(/^./, x => x.toUpperCase()),
      'Разобрать восстановление', `Почему у меня восстановление ${r.score}% и что с этим делать сегодня?`);
    const rows = lastRows(14);
    h += chartCard('Восстановление, 14 дней', b.recovery != null ? `<span class="num">обычно ${num(b.recovery)}%</span>` : '', bars(rows, 'recovery', { max: 100, color: r2 => C[r2.zone] || 'var(--hint)' }));
    h += chartCard('ВСР, 14 дней', '<span>выше — лучше</span>', line(rows, 'hrv', 'var(--rec-blue)', b.hrv));
    h += chartCard('Пульс покоя, 14 дней', '<span>ниже — лучше</span>', line(rows, 'rhr', 'var(--rec-blue)', b.rhr));
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
    h += `<section class="wcon rise">${crow(I.zones, 'Пульсовые зоны 1–3', ws.length ? hm(z13 / 60) : '—', '<i class="tri dot"></i>')}
      ${crow(I.zones, 'Пульсовые зоны 4–5', ws.length ? hm(z45 / 60) : '—', '<i class="tri dot"></i>')}
      ${crow(I.bolt, 'Калории', c && c.kcal != null ? big(c.kcal) : '—', tri(c && c.kcal, b.kcal, null), 'kcal')}
      ${crow(I.run, 'Тренировки', ws.length ? `${ws.length} · ${hm(mins / 60)}` : '0', '<i class="tri dot"></i>')}${vsFoot}</section>`;
    const word = sv == null ? '' : sv < 10 ? 'лёгкая: организм почти не напрягался' : sv < 14 ? 'средняя: обычная активная нагрузка' : sv < 18 ? 'высокая: сердце и мышцы хорошо поработали' : 'максимальная: на пределе сил';
    h += insight(sv == null ? 'Нагрузка ещё не посчитана.' : `Нагрузка ${num(sv, 1)} — ${word}.${t.band ? ` Цель на сегодня по восстановлению — ${t.band[0]}–${t.band[1]}.` : ''}`,
      'Разобрать нагрузку', 'Сколько и как мне тренироваться сегодня по нагрузке и восстановлению?');
    if (t.band || sv != null) h += `<section class="card rise">${cap('Цель нагрузки', t.band ? `<b class="num" style="color:var(--strain)">${t.band[0]}–${t.band[1]}</b>` : '')}${strainScale(t, sv)}</section>`;
    h += `<div class="grid2 metrics rise">${metric('Пульс средний', c && c.avg_hr ? c.avg_hr : '—', 'уд/мин за день')}${metric('Пульс макс.', c && c.max_hr ? c.max_hr : '—', 'уд/мин за день')}</div>`;
    if (ws.length) {
      h += '<div class="label rise">Тренировки</div>';
      h += ws.map(w => {
        const tot = (w.zones || []).slice(1).reduce((a, x) => a + x, 0);
        return `<section class="card wk rise tap" data-a="workout" data-v="${esc(w.id)}" role="button"><div class="wk-h"><div><div class="wk-n">${esc(w.sport)}</div><div class="meta num">${esc(w.start)} · ${w.minutes || '—'} мин · пульс ${w.avg_hr || '—'}/${w.max_hr || '—'}${w.kcal ? ` · ${big(w.kcal)} ккал` : ''}</div></div>
          <div class="wk-s num">${w.strain != null ? num(w.strain, 1) : '—'}</div></div>
          ${tot ? `<div class="zbar">${w.zones.slice(1).map((m, i) => m ? `<span style="flex:${m};background:${ZCOL[i]}"></span>` : '').join('')}</div>
          <div class="zleg num">${w.zones.slice(1).map((m, i) => `<span><i style="background:${ZCOL[i]}"></i>Z${i + 1}<b>${m}</b></span>`).join('')}</div>` : ''}</section>`;
      }).join('');
    } else h += '<div class="empty rise"><span class="e">' + I.run + '</span>Тренировок за день нет.</div>';
    h += chartCard('Нагрузка, 14 дней', b.strain != null ? `<span class="num">обычно ${num(b.strain, 1)}</span>` : '', bars(lastRows(14), 'strain', { max: 21, color: 'var(--strain)' }));
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
    const tile = (k, v, c) => `<div class="stat"><div class="k">${k}</div><div class="v sm num">${v}</div><div class="d">${c || ''}</div></div>`;
    const dd = s.day || {};
    const recV = per === 'day' ? (dd.recovery ? dd.recovery.score : null) : x.recovery;
    const hrvV = per === 'day' ? (dd.recovery ? dd.recovery.hrv : null) : x.hrv;
    const rhrV = per === 'day' ? (dd.recovery ? dd.recovery.rhr : null) : x.rhr;
    const slV = per === 'day' ? (dd.sleep ? dd.sleep.hours : null) : x.sleep;
    const stV = per === 'day' ? (dd.cycle ? dd.cycle.strain : null) : x.strain;
    let h = `<p class="small muted">${range} · сравнение: ${esc(s.prev_label)}</p>
      <div class="grid2">${tile('Восстановление', recV != null ? Math.round(recV) + '%' : '—', cmp(recV, p.recovery, true, '%'))}
      ${tile('ВСР', hrvV != null ? Math.round(hrvV) + ' мс' : '—', cmp(hrvV, p.hrv, true))}
      ${tile('Пульс покоя', rhrV != null ? Math.round(rhrV) : '—', cmp(rhrV, p.rhr, false))}
      ${tile('Сон', hm(slV), cmp(slV != null ? slV * 60 : null, p.sleep != null ? p.sleep * 60 : null, true, ' мин'))}
      ${tile('Нагрузка', stV != null ? num(stV, 1) : '—', cmp(stV, p.strain, null, '', 1))}
      ${per === 'day' ? tile('Тренировки', (dd.workouts || []).length, '') : tile('Тренировки', x.workouts, `<span class="muted">${x.minutes} мин</span>`)}</div>`;
    if (per !== 'day' && x.days) {
      const zb = (n, c) => n ? `<span style="flex:${n};background:${c}"></span>` : '';
      h += `<div class="zones">${zb(x.green, C.green)}${zb(x.yellow, C.yellow)}${zb(x.red, C.red)}</div>
        <p class="small muted num">🟢 ${x.green} · 🟡 ${x.yellow} · 🔴 ${x.red} из ${x.days} дн. · отбой в среднем ${x.bed != null ? esc(clockH(x.bed)) : '—'}${x.bed_spread != null ? ` ±${num(x.bed_spread * 60)} мин` : ''}</p>`;
    }
    h += `<div class="label">Итог и советы</div>`;
    if (z.text) h += `<div class="card prose">${md(z.text)}</div><button class="btn sm ghost" data-a="sum-make" data-v="${per}">Обновить советы</button>`;
    else if (z.making) h += `<div class="card">${skel(3)}<p class="small muted">Разбираю данные — 20–40 секунд…</p></div>`;
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
  function sumRender() { const b = sumEl && sumEl.querySelector('#sum-body'); if (b) { b.innerHTML = sumBody(sumPer); animate(); } }
  async function sumLoad(per, make) {
    const z = S.sum[per] = S.sum[per] || {};
    if (make) { if (z.making) return; z.making = true; } else z.loading = true;
    if (per === sumPer) sumRender();
    try {
      const r = await api('/api/summary', make ? { period: per, make: 1 } : { period: per });
      Object.assign(z, { summary: r.summary, text: r.text, error: null });
      if (make) haptic('success');
      else if (!r.text && (r.summary.stats.days || (r.summary.day && r.summary.day.cycle))) { z.loading = false; return sumLoad(per, true); }  // советы — сразу, без лишней кнопки
    } catch (e) { z.error = e.message || String(e); if (make) fail(e); }
    z.loading = false; z.making = false;
    if (per === sumPer) sumRender();
  }
  function summarySheet(per) {
    haptic('tap');
    sumPer = per || 'day';
    sumEl = sheet(sumHtml());
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
    { k: 'ВСР', ex: 'hrv', unit: 'мс', get: t => (t.recovery || {}).hrv, base: b => b.hrv, tol: b => b * .15 },
    { k: 'Пульс покоя', ex: 'rhr', unit: 'уд/мин', get: t => (t.recovery || {}).rhr, base: b => b.rhr, tol: () => 3 },
    { k: 'Дыхание', ex: 'resp', unit: 'в мин', d: 1, get: t => (t.sleep || {}).resp, base: b => b.resp, tol: () => 1 },
    { k: 'SpO₂', ex: 'spo2', unit: '%', d: 1, get: t => (t.recovery || {}).spo2, base: () => 97.5, tol: () => 2.5, range: '95–100' },
    { k: 'Темп. кожи', ex: 'temp', unit: '°C', d: 1, get: t => (t.recovery || {}).skin_temp, base: b => b.skin_temp, tol: () => .5 },
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
        <span class="mk">${m.k}<small>${range ? `обычно ${range}` : word}</small></span>
        <span class="mv"><b class="num">${v == null ? '—' : num(v, m.d || 0)}</b><small>${m.unit}</small><em style="color:${col}">${word}</em></span></button>`;
    }).join('');
    return `<section class="card monitor rise">${cap('Монитор здоровья', all ? `<b class="num" style="color:${ok === all ? C.green : ok >= all - 1 ? C.yellow : C.red}">${ok}/${all}</b> в норме` : '')}${rows}</section>`;
  }
  const RANGES = [[7, '1Н'], [30, '1М'], [180, '6М']];
  function trendsView() {
    const t = S.data.today;
    let h = hero('Здоровье', `${dateWord(t.day)}, ${dateLine(t.day)}`);
    h += slot('healthTop', t);
    h += monitorCard(t);
    h += slot('healthAfterMonitor', t);
    const pr = S.progress;
    h += `<section class="card rise">${cap('Разбор прогресса', pr && !S.progressing ? '<button class="link" data-a="progress">Обновить</button>' : '')}
      ${S.progressing ? skel(3) + '<p class="small muted">Разбираю 2 месяца данных — около минуты…</p>'
        : pr ? `<p class="small muted">от ${dateLine(pr.day)}</p><div class="prose ${pr.text.length > 320 ? 'clamp' : ''}" id="prog">${tgHtml(pr.text)}</div>${pr.text.length > 320 ? '<button class="link" data-a="more">Читать целиком</button>' : ''}`
        : `<p class="muted small">Тренды, сон, нагрузка, питание и план на 2 недели — по всем данным WHOOP.</p>
           <button class="btn primary" data-a="progress">Разобрать прогресс</button>`}</section>`;
    h += `<div class="sec rise">${cap('Тренды')}<div class="seg" role="tablist">${RANGES.map(([n, l]) => `<button class="${S.range === n ? 'on' : ''}" data-a="range" data-v="${n}" role="tab">${l}</button>`).join('')}</div></div>`;
    const rows = S.series[S.range] || (S.range === 30 ? S.data.series : null);
    if (!rows && S.seriesErr === S.range) return h + retryCard('Графики не загрузились', 'series-retry');
    if (!rows) return h + [1, 2, 3].map(() => '<section class="card"><div class="sk line" style="width:40%"></div><div class="sk" style="height:130px;margin-top:14px"></div></section>').join('');
    if (!rows.some(r => r.recovery != null || r.strain != null || r.sleep != null)) {
      return h + `<div class="empty"><span class="e">${I.trends}</span><b>Данных пока нет</b>Подключи WHOOP — графики появятся после первой ночи с браслетом.</div>`;
    }
    const zc = r => C[r.zone] || 'var(--hint)';
    const card = (title, value, unit, side, chart, color) => `<section class="card rise">${cap(title, side)}
      <div class="big-line"><span class="v num" style="color:${color || 'var(--ink)'}">${value}</span><span class="u">${unit}</span></div>${chart}</section>`;
    const greens = rows.filter(r => r.zone === 'green').length, yellows = rows.filter(r => r.zone === 'yellow').length, reds = rows.filter(r => r.zone === 'red').length;
    h += card('Восстановление', num(avg(rows, 'recovery')), '% в среднем', `<span class="num"><i class="dot" style="background:${C.green}"></i>${greens}<i class="dot" style="background:${C.yellow}"></i>${yellows}<i class="dot" style="background:${C.red}"></i>${reds}</span>`, bars(rows, 'recovery', { max: 100, color: zc }));
    h += card('Сон', hm(avg(rows, 'sleep')), 'в среднем', '<i class="tick"></i>нужно', bars(rows, 'sleep', { color: 'var(--sleep)', ref: 'need' }), 'var(--sleep)');
    const wk = rows.reduce((a, r) => a + (r.workouts || 0), 0);
    h += card('Нагрузка', num(avg(rows, 'strain'), 1), 'в среднем', `<span class="num">${wk} ${plural(wk, 'тренировка', 'тренировки', 'тренировок')}</span>`, bars(rows, 'strain', { max: 21, color: 'var(--strain)' }), 'var(--strain)');
    h += card('ВСР', num(avg(rows, 'hrv')), 'мс', 'выше — лучше', line(rows, 'hrv', 'var(--rec-blue)', avg(rows, 'hrv')));
    h += card('Пульс покоя', num(avg(rows, 'rhr')), 'уд/мин', 'ниже — лучше', line(rows, 'rhr', 'var(--rec-blue)', avg(rows, 'rhr')));
    h += slot('healthBottom', t, rows);
    return h;
  }
  async function loadSeries(n) {
    S.seriesErr = null;
    try { S.series[n] = (await api('/api/series', { n })).series; } catch (e) { fail(e); S.seriesErr = n; }
    if (S.tab === 'trends') render(false);
  }
  async function loadProgress(make) {
    if (make) { if (S.progressing) return; S.progressing = true; haptic('tap'); render(false); }
    try { S.progress = (await api('/api/progress', make ? { make: 1 } : {})).progress; if (make) haptic('success'); } catch (e) { fail(e); }
    S.progressing = false; if (S.tab === 'trends') render(false);
  }

  // ---------- Питание ----------
  function foodView() {
    const t = S.data.today, n = t.nutrition, e = t.eaten;
    let h = hero('Питание', `Сегодня, ${dateLine(t.day)}`);
    const left = n ? n.kcal - e.kcal : 0;
    h += `<section class="card feature rise"><div class="food-top">
      ${n ? ring(e.kcal / n.kcal, 'var(--food)', `<b class="num">${big(Math.abs(left))}</b><span>${left >= 0 ? 'ккал осталось' : 'ккал сверх'}</span>`, 118)
        : ring(0, 'var(--food)', `<b class="num">${big(e.kcal)}</b><span>ккал</span>`, 118)}
      <div class="macro-list">${n ? [['Белок', e.protein, n.protein, 'var(--rec-green)'], ['Жиры', e.fat, n.fat, 'var(--rec-yellow)'], ['Углеводы', e.carbs, n.carbs, 'var(--strain)']].map(([k, v, g, col]) =>
        `<div class="macro"><div class="mt"><span>${k}</span><span class="num"><b>${v}</b>/${g} г</span></div>${bar(pct(v, g), col)}</div>`).join('')
        : '<p class="small muted">Добавь вес в профиле — посчитаю норму калорий и БЖУ.</p><button class="btn sm tonal" data-a="profile">Заполнить профиль</button>'}</div></div>
      <button class="btn primary" data-a="photo" data-v="food" ${S.photo ? 'disabled' : ''}>${S.photo === 'food' ? 'Считаю калории…' : '📷 Сфотографировать еду'}</button>
      <div class="ask"><textarea id="eat" rows="1" placeholder="Что съел?">${esc(S.eatDraft || '')}</textarea>
        <button class="send" data-a="eat" aria-label="Посчитать" ${S.eating ? 'disabled' : ''}>${S.eating ? '<span class="dots"><i></i><i></i><i></i></span>' : '↑'}</button></div>
      ${n ? `<p class="small muted">Норма ${big(n.kcal)} ккал — ${esc(n.how)}.${n.teen ? ' В твоём возрасте калории не урезаем: рост важнее.' : ''}</p>` : ''}</section>`;
    h += `<div class="label">Приёмы пищи${e.count ? `<span class="num">${big(e.kcal)} ккал</span>` : ''}</div>`;
    h += t.meals.length ? `<div class="list rise">${t.meals.map(m => `<button class="row" data-a="meal-edit" data-v="${m.id}">
        <span class="ic time num">${esc(m.time)}</span><div class="main"><div class="title">${esc(m.title)}</div>
        <div class="meta num">Б ${Math.round(m.protein)} · Ж ${Math.round(m.fat)} · У ${Math.round(m.carbs)}</div></div><div class="end num"><b>${big(m.kcal)}</b></div></button>`).join('')}</div>`
      : `<div class="empty rise"><span class="e">${I.food}</span><b>Пока пусто</b>Сфотографируй тарелку или напиши, что съел.</div>`;
    h += `<button class="btn ghost" data-a="meal-new">＋ Добавить вручную</button>`;
    h += waterCard(t.water, true);
    return h;
  }

  // ---------- Тренер ----------
  const SUGGEST = ['Готов ли я к спаррингу?', 'Что съесть перед тренировкой?', 'Почему упало восстановление?', 'Как улучшить сон?', 'План на неделю'];
  function coachView() {
    let h = hero('Тренер', 'Врач · тренер · питание', S.history && S.history.length ? '<button class="avatar ghost" data-a="clear" aria-label="Новый разговор">✕</button>' : '');
    const hist = S.history;
    if (!hist && S.historyErr) return h + retryCard('Разговор не загрузился', 'history-retry');
    if (!hist) return h + '<div class="chat"><div class="sk msg-sk me"></div><div class="sk msg-sk"></div><div class="sk msg-sk me short"></div></div>';
    h += '<div class="chat">';
    if (!hist.length && !S.asking) h += '<div class="empty"><span class="e">💬</span><b>Спроси что угодно</b>Про тренировки, сон, питание и восстановление — отвечу по твоим данным WHOOP. Можно приложить скрин тренировки.</div>';
    hist.forEach(m => { h += m.role === 'user' ? `<div class="msg me">${esc(m.text)}</div>` : `<div class="msg them prose">${md(m.text)}</div>`; });
    if (S.asking) h += `<div class="msg me">${esc(S.asking)}</div><div class="msg them"><span class="dots"><i></i><i></i><i></i></span></div>`;
    h += '</div>';
    h += `<div class="composer"><div class="chips">${SUGGEST.map(s => `<button class="chip" data-a="suggest" data-v="${esc(s)}">${esc(s)}</button>`).join('')}</div>
      <div class="ask"><button class="send alt" data-a="photo" data-v="ask" aria-label="Фото" ${S.asking ? 'disabled' : ''}>${I.cam}</button>
      <textarea id="q" rows="1" placeholder="Спроси тренера…">${esc(S.qDraft || '')}</textarea>
      <button class="send" data-a="ask" aria-label="Отправить" ${S.asking ? 'disabled' : ''}>↑</button></div></div>`;
    return h;
  }
  async function loadHistory() {
    S.historyErr = false;
    try { S.history = (await api('/api/history', {})).history; } catch (e) { fail(e); S.historyErr = true; }
    if (S.tab === 'coach') render();
  }
  async function ask(q) {
    q = (q || '').trim(); if (!q || S.asking) return;
    S.asking = q; S.qDraft = ''; haptic('tap'); render();
    try { const r = await api('/api/ask', { text: q }); S.history = r.history; haptic('success'); } catch (e) { fail(e); S.qDraft = q; }
    S.asking = false; if (S.tab === 'coach') render();
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
  function pickPhoto(mode) {
    const inp = $('#pick');
    inp.value = '';
    inp.onchange = async () => {
      const f = inp.files && inp.files[0];
      if (!f) return;
      haptic('tap');
      if (mode === 'ask') {
        const text = ($('#q') || {}).value || '';
        S.asking = '📷 ' + (text.trim() || 'Фото'); S.qDraft = ''; render();
        try { const r = await api('/api/photo', { image: await shrink(f), mode: 'ask', text: text.trim() }); S.history = r.history; haptic('success'); }
        catch (e) { fail(e); }
        S.asking = false; if (S.tab === 'coach') render();
        return;
      }
      S.photo = 'food'; render(false);
      try {
        const r = await api('/api/photo', { image: await shrink(f), mode: 'food' });
        S.data.today = r.day; haptic('success'); toast(`${r.meal.title} · ${big(r.meal.kcal)} ккал`, 'ok');
      } catch (e) { fail(e); }
      S.photo = false; render(false);
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
    try { S.data.goals = (await api(path, body)).goals; haptic('success'); const el = $('#goals'); if (el) el.innerHTML = goalsBlock(); } catch (e) { fail(e); }
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
  function mealSheet(m) {
    m = m || { title: '', kcal: '', protein: '', fat: '', carbs: '' };
    haptic('tap');
    const f = (k, label, type = 'number') => `<label class="field"><span>${label}</span><input name="${k}" type="text" inputmode="${type === 'number' ? 'decimal' : 'text'}" value="${esc(m[k] === '' ? '' : type === 'number' ? Math.round(m[k]) : m[k])}"></label>`;
    const el = sheet(`<h2>${m.id ? 'Изменить' : 'Добавить еду'}</h2><form id="mf">${f('title', 'Что', 'text')}<div class="grid2">${f('kcal', 'Ккал')}${f('protein', 'Белки, г')}${f('fat', 'Жиры, г')}${f('carbs', 'Углеводы, г')}</div>
      <button class="btn primary" type="submit">Сохранить</button>${m.id ? '<button class="btn danger" type="button" data-del="1">Удалить</button>' : ''}</form>`);
    $('#mf', el).addEventListener('submit', async ev => {
      ev.preventDefault();
      const fd = Object.fromEntries(new FormData(ev.target).entries());
      if (m.id) fd.id = m.id;
      try { const r = await api('/api/meal', fd); S.data.today = r.day; el.close(); haptic('success'); render(false); } catch (e) { fail(e); }
    });
    const del = $('[data-del]', el);
    if (del) del.addEventListener('click', async () => {
      try { S.data.today = (await api('/api/meal/delete', { id: m.id })).day; el.close(); haptic('success'); render(false); } catch (e) { fail(e); }
    });
  }
  function progressSheet() {
    if (!S.progress) return;
    sheet(`<h2>Разбор прогресса</h2><p class="small muted">от ${dateLine(S.progress.day)}</p><div class="prose">${tgHtml(S.progress.text)}</div>`);
  }

  // «＋» по центру меню (как в WHOOP): быстрые действия
  function plusSheet() {
    haptic('tap');
    const d = S.data, row = (a, v, ic, col, t, m) => `<button class="row tap" data-a="${a}" data-v="${v}"><span class="ic" style="color:${col}">${ic}</span>
      <span class="main"><div class="title">${t}</div><div class="meta">${m}</div></span><span class="end chev"></span></button>`;
    const el = sheet(`<h2>Добавить</h2><div class="list">
      ${row('photo', 'food', I.cam, 'var(--food)', 'Еда по фото', 'посчитаю ккал и БЖУ')}
      ${row('meal-new', '', I.food, 'var(--food)', 'Еда вручную', 'название и цифры')}
      ${row('water', '250', I.drop, 'var(--water)', 'Вода +250 мл', 'стакан')}
      ${row('water', '500', I.drop, 'var(--water)', 'Вода +500 мл', 'бутылка')}
      ${row('tab', 'coach', I.coach, 'var(--teal)', 'Спросить тренера', 'по твоим данным WHOOP')}
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
  async function eat() {
    const el = $('#eat'), text = el ? el.value.trim() : '';
    if (!text || S.eating) return;
    S.eating = true; S.eatDraft = text; haptic('tap'); render(false);
    try { const r = await api('/api/meal/estimate', { text }); S.data.today = r.day; S.eatDraft = ''; haptic('success'); toast(`${r.meal.title} · ${big(r.meal.kcal)} ккал`, 'ok'); }
    catch (e) { fail(e); }
    S.eating = false; render(false);
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
    else if (a === 'ask-go') { go('coach'); ask(v); }  // без модуля activity.js — общий экран нагрузки
    else if (a === 'tab') { const t = TABS.find(x => x.id === v); if (t) { haptic('select'); go(v); } }
    else if (a === 'connect') connect();
    else if (a === 'brief') brief();
    else if (a === 'detail') openScreen(v);
    else if (a === 'back') closeScreen();
    else if (a === 'goto') shiftTo(v);
    else if (a === 'explain') explain(v);
    else if (a === 'summary') summarySheet(v);
    else if (a === 'progress') loadProgress(true);
    else if (a === 'more') progressSheet();
    else if (a === 'day') shiftDay(Number(v));
    else if (a === 'range') { if (S.range === Number(v)) return; S.range = Number(v); haptic('select'); render(false); if (!S.series[S.range]) loadSeries(S.range); }
    else if (a === 'series-retry') { S.seriesErr = null; render(false); loadSeries(S.range); }
    else if (a === 'history-retry') { S.historyErr = false; render(false); loadHistory(); }
    else if (a === 'eat') eat();
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
    } else if (a === 'meal-new') mealSheet();
    else if (a === 'meal-edit') mealSheet(S.data.today.meals.find(m => String(m.id) === v));
    else if (a === 'sync') {
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
      if (ev.target.id === 'eat') S.eatDraft = ev.target.value;
    }
  });
  document.addEventListener('keydown', ev => {
    if (ev.key === 'Enter' && !ev.shiftKey && ev.target.tagName === 'TEXTAREA' && ev.target.closest('.ask')) {
      ev.preventDefault();
      if (ev.target.id === 'q') ask(ev.target.value);
      else if (ev.target.id === 'goal-new') { if (ev.target.value.trim()) goalApi('/api/goal', { text: ev.target.value.trim() }); }
      else eat();
    }
  });
  let rw = window.innerWidth;
  window.addEventListener('resize', () => { if (Math.abs(window.innerWidth - rw) > 20 && S.tab === 'trends') { rw = window.innerWidth; render(false); } });

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
    if (tg && tg.onEvent) tg.onEvent('themeChanged', () => render(false));
    // данные обновляются сами, пока приложение открыто
    setInterval(async () => {
      if (document.hidden || document.querySelector('.sheet') || S.asking || S.eating || S.briefing || S.photo || S.dayData) return;
      if (document.activeElement && ['TEXTAREA', 'INPUT'].includes(document.activeElement.tagName)) return;
      try { S.data = await api('/api/all'); F.onData.forEach(fn => { try { fn(S.data); } catch (e) { console.error(e); } }); if (S.tab !== 'coach' && !S.screen) render(false); } catch (e) { /* тихо */ }
    }, 120000);
  }
  // всё, чем пользуются модули
  Object.assign(F, { S, I, C, ZONE, WD, WD_S, MON, MON_S, tg, esc, api, toast, haptic, sheet, busy, $, num, big, hm, hHuman, liters, pct, plural, fail, clock,
    isoDay, parseDay, dateWord, dateLine, md, tgHtml, cnt, dial, ring, bar, bars, line, avg, animate, render, go, openScreen, closeScreen,
    dHead, cap, head, hero, chartCard, metric, skel, retryCard, vs, sportIcon, strainScale, explain, ask, loadHistory, loadSeries,
    summarySheet, profileSheet, refresh, shiftTo, svgi, lastRows, sub0, nav });
  (window.FormaModules || []).forEach(m => { try { m(F); } catch (e) { console.error('модуль', e); } });
  start();
})();
