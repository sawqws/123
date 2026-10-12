/* Форма — приложение тренера внутри Telegram. Без сборки и библиотек. */
(() => {
  const tgw = window.Telegram && window.Telegram.WebApp;
  const $ = (s, el = document) => el.querySelector(s);
  const view = $('#view');
  const S = { data: null, tab: 'today', day: null, dayData: null, range: 30, series: {}, history: null, asking: false, eating: false, briefing: false };
  const WD = ['Воскресенье', 'Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота'];
  const WD_S = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];
  const MON = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
  const MON_S = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

  const I = {
    today: '<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/>',
    trends: '<path d="M4 19V9M10 19V5M16 19v-7M22 19H2"/>',
    food: '<path d="M7 2v9M4 2v6a3 3 0 0 0 6 0V2M7 11v11M17 22V2c-2.5 1-4 3.5-4 7v5h4"/>',
    coach: '<path d="M21 11.5a8.4 8.4 0 0 1-12.3 7.5L3 21l2-5.3A8.4 8.4 0 1 1 21 11.5z"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-6 8-6s8 2 8 6"/>',
    send: '<path d="M12 19V5M6 11l6-6 6 6"/>',
    refresh: '<path d="M20 12a8 8 0 1 1-2.4-5.7L20 8.6"/><path d="M20 3.5v5.1h-5.1"/>',
    trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    x: '<path d="M6 6l12 12M18 6 6 18"/>',
    left: '<path d="m15 18-6-6 6-6"/>',
    right: '<path d="m9 18 6-6-6-6"/>',
    chev: '<path d="m9 18 6-6-6-6"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    spark: '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/>',
    link: '<path d="M10 14a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/><path d="M14 10a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/>',
    moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z"/>',
    drop: '<path d="M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    bolt: '<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/>',
  };
  const icon = (n, cls = 'ic') => `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true">${I[n]}</svg>`;
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const num = (x, d = 0) => x == null ? '—' : Number(x).toFixed(d).replace('.', ',');
  const big = x => x == null ? '—' : Math.round(x).toLocaleString('ru-RU');
  const liters = ml => ml >= 1000 ? `${num(ml / 1000, 1).replace(/,0$/, '')} л` : `${Math.round(ml)} мл`;
  const fmtH = h => { if (h == null) return '—'; const m = Math.round(h * 60); return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}`; };
  const hHuman = h => { if (h == null) return '—'; const m = Math.round(h * 60); return `${Math.floor(m / 60)} ч ${String(m % 60).padStart(2, '0')} мин`; };
  const isoDay = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const parseDay = s => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
  const pct = (v, of) => of ? Math.max(0, Math.min(100, v / of * 100)) : 0;
  const plural = (n, a, b, c) => n % 10 === 1 && n % 100 !== 11 ? a : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? b : c;
  const H = { // отклик вибрацией (Telegram HapticFeedback)
    tap: () => { try { tgw.HapticFeedback.impactOccurred('light'); } catch (e) { /* нет */ } },
    soft: () => { try { tgw.HapticFeedback.impactOccurred('soft'); } catch (e) { /* нет */ } },
    pick: () => { try { tgw.HapticFeedback.selectionChanged(); } catch (e) { /* нет */ } },
    ok: () => { try { tgw.HapticFeedback.notificationOccurred('success'); } catch (e) { /* нет */ } },
    bad: () => { try { tgw.HapticFeedback.notificationOccurred('error'); } catch (e) { /* нет */ } },
  };
  const ZONE = {
    green: { v: 'Готов к нагрузке', c: 'var(--green)' },
    yellow: { v: 'Умеренная нагрузка', c: 'var(--yellow)' },
    red: { v: 'День восстановления', c: 'var(--red)' },
  };

  // ---------- API ----------
  async function api(path, body) {
    const opt = { headers: { 'X-Init-Data': (tgw && tgw.initData) || '', 'Content-Type': 'application/json' } };
    if (body !== undefined) { opt.method = 'POST'; opt.body = JSON.stringify(body); }
    const r = await fetch(path, opt);
    const j = await r.json().catch(() => ({ error: 'нет связи' }));
    if (!r.ok) throw new Error(j.error || 'ошибка');
    return j;
  }
  function toast(t) {
    const el = $('#toast');
    el.textContent = t; el.classList.add('on');
    clearTimeout(toast.t); toast.t = setTimeout(() => el.classList.remove('on'), 2600);
  }
  const fail = e => { H.bad(); toast(e.message || String(e)); };

  // простой Markdown от тренера -> HTML
  function md(text) {
    const lines = esc(text || '').split('\n');
    let out = '', list = false;
    for (let l of lines) {
      l = l.replace(/^#{1,6}\s+(.*)$/, '**$1**').replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<b>$1</b>');
      const li = l.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/);
      if (li) { if (!list) { out += '<ul>'; list = true; } out += `<li>${li[1]}</li>`; continue; }
      if (list) { out += '</ul>'; list = false; }
      if (l.trim()) out += `<p>${l}</p>`;
    }
    return out + (list ? '</ul>' : '');
  }
  // разбор из бота уже в HTML Telegram (b, i, code) с «• » в начале строк
  function tgHtml(h) {
    return md((h || '').replace(/<\/?(b|i|code|s)>/g, m => ({ '<b>': '**', '</b>': '**' }[m] || '')).replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&'));
  }

  // ---------- кольца, полосы, графики ----------
  function ring(p, size, stroke, color) {
    const r = (size - stroke) / 2, c = 2 * Math.PI * r, off = c * (1 - Math.max(0, Math.min(1, p)));
    return `<svg viewBox="0 0 ${size} ${size}" aria-hidden="true"><circle class="ring-bg" cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke-width="${stroke}"/>
      <circle class="ring-fg" cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke-width="${stroke}" stroke-linecap="round"
      stroke-dasharray="${c}" stroke-dashoffset="${c}" data-off="${off}" style="stroke:${color}"/></svg>`;
  }
  const meter = (p, color) => `<div class="meter"><span style="--c:${color};width:0" data-w="${p}%"></span></div>`;
  function animate() {
    requestAnimationFrame(() => requestAnimationFrame(() => {
      document.querySelectorAll('.ring-fg[data-off]').forEach(el => { el.style.strokeDashoffset = el.dataset.off; });
      document.querySelectorAll('[data-w]').forEach(el => { el.style.width = el.dataset.w; });
    }));
  }
  // ширина графика в пикселях: viewBox = реальный размер, поэтому подписи не растягиваются
  const chartW = () => Math.max(220, Math.min(view.clientWidth || 390, 620) - 64);
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
      s += `<text x="${anchor === 'start' ? 0 : anchor === 'end' ? W : x}" y="${CH + 16}" text-anchor="${anchor}">${t}</text>`;
    });
    return s;
  }
  function bars(rows, key, { max, color, ref } = {}) {
    const W = chartW(), n = rows.length, slot = W / n;
    const vals = rows.map(r => r[key]).filter(v => v != null);
    const top = max || Math.max(1, ...vals, ...(ref ? rows.map(r => r[ref] || 0) : [])) * 1.08;
    const gap = n > 60 ? 1 : n > 20 ? 2 : Math.min(10, slot * .35), bw = Math.max(1, slot - gap);
    let s = `<line class="axis" x1="0" x2="${W}" y1="${CH + .5}" y2="${CH + .5}"/>`;
    rows.forEach((r, i) => {
      const v = r[key]; if (v == null) return;
      const h = Math.max(2, v / top * CH), x = i * slot + gap / 2;
      const c = typeof color === 'function' ? color(r) : color;
      s += `<rect x="${x.toFixed(1)}" y="${(CH - h).toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" rx="${Math.min(3, bw / 2).toFixed(1)}" fill="${c}"><title>${r.day}: ${v}</title></rect>`;
    });
    if (ref) rows.forEach((r, i) => {
      const v = r[ref]; if (v == null) return;
      const y = CH - v / top * CH;
      s += `<line class="ref" x1="${(i * slot + gap / 2).toFixed(1)}" x2="${((i + 1) * slot - gap / 2).toFixed(1)}" y1="${y.toFixed(1)}" y2="${y.toFixed(1)}"/>`;
    });
    return `<svg class="chart" viewBox="0 0 ${W} ${CH + LAB}" width="${W}" height="${CH + LAB}" role="img">${s}${xLabels(rows.map(r => r.day), W, slot)}</svg>`;
  }
  function line(rows, key, color, base) {
    const W = chartW(), n = rows.length, slot = W / n;
    const pts = rows.map((r, i) => [(i + .5) * slot, r[key]]).filter(p => p[1] != null);
    if (pts.length < 2) return '<div class="empty-sm">Мало данных для графика</div>';
    const vs = pts.map(p => p[1]).concat(base != null ? [base] : []);
    let lo = Math.min(...vs), hi = Math.max(...vs); const sp = (hi - lo) || 1; lo -= sp * .15; hi += sp * .25;
    const y = v => CH - 6 - (v - lo) / (hi - lo) * (CH - 12);
    const P = pts.map(p => [p[0], y(p[1])]);
    let d = `M${P[0][0].toFixed(1)},${P[0][1].toFixed(1)}`;
    for (let i = 1; i < P.length; i++) { const [x0, y0] = P[i - 1], [x1, y1] = P[i], cx = (x0 + x1) / 2; d += ` C${cx.toFixed(1)},${y0.toFixed(1)} ${cx.toFixed(1)},${y1.toFixed(1)} ${x1.toFixed(1)},${y1.toFixed(1)}`; }
    const id = 'g' + key + Math.random().toString(36).slice(2, 6);
    let s = `<defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".22"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>`;
    s += `<line class="axis" x1="0" x2="${W}" y1="${CH + .5}" y2="${CH + .5}"/>`;
    if (base != null) {
      const yb = y(base), above = yb > 18;
      s += `<line class="norm" x1="0" x2="${W}" y1="${yb.toFixed(1)}" y2="${yb.toFixed(1)}"/><text class="norm-t" x="${W}" y="${(above ? yb - 6 : yb + 14).toFixed(1)}" text-anchor="end">норма ${num(base)}</text>`;
    }
    s += `<path d="${d} L${P[P.length - 1][0].toFixed(1)},${CH} L${P[0][0].toFixed(1)},${CH} Z" fill="url(#${id})"/>`;
    s += `<path class="stroke" d="${d}" fill="none" stroke="${color}" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round"/>`;
    const last = P[P.length - 1];
    s += `<circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="4" fill="${color}" stroke="var(--card)" stroke-width="2"/>`;
    return `<svg class="chart" viewBox="0 0 ${W} ${CH + LAB}" width="${W}" height="${CH + LAB}" role="img">${s}${xLabels(rows.map(r => r.day), W, slot)}</svg>`;
  }
  const avg = (rows, k) => { const v = rows.map(r => r[k]).filter(x => x != null); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
  const skel = (lines = 3) => Array.from({ length: lines }, (_, i) => `<div class="sk" style="width:${[92, 78, 86, 60][i % 4]}%"></div>`).join('');

  // ---------- вкладки ----------
  const TABS = [['today', 'Сегодня'], ['trends', 'Тренды'], ['food', 'Питание'], ['coach', 'Тренер']];
  function renderTabs() {
    $('#tabs').innerHTML = TABS.map(([k, t]) => `<button class="tab ${S.tab === k ? 'on' : ''}" data-tab="${k}" aria-label="${t}">${icon(k)}<span>${t}</span></button>`).join('');
  }
  function go(tab) {
    if (S.tab === tab) { window.scrollTo({ top: 0, behavior: 'smooth' }); return; }
    S.tab = tab; H.pick(); renderTabs(); render(); window.scrollTo(0, 0);
    if (tab === 'trends' && !S.series[S.range]) loadSeries(S.range);
    if (tab === 'trends' && S.progress === undefined) loadProgress();
    if (tab === 'coach' && !S.history) loadHistory();
  }
  function render(anim) {
    if (!S.data) return;
    const key = [S.tab, (S.dayData || S.data.today).day, S.range, !!S.series[S.range], !!S.history].join('|');
    if (anim === undefined) anim = key !== S.shown; // анимация — только при смене экрана, не на каждое действие
    S.shown = key;
    const html = { today: todayView, trends: trendsView, food: foodView, coach: coachView }[S.tab]();
    view.classList.toggle('still', !anim); // фоновое обновление — без повторной анимации
    view.innerHTML = `<div class="enter">${html}</div>`;
    animate();
    if (S.tab === 'coach') { const c = $('.chat'); if (c) window.scrollTo(0, document.body.scrollHeight); }
  }

  function header(eyebrow, title, extra = '') {
    const n = (S.data.user.name || '?').trim()[0] || '?';
    return `<header class="top"><div class="top-t"><div class="eyebrow">${eyebrow}</div><h1 class="title">${title}</h1></div>${extra}
      <button class="avatar" data-a="profile" aria-label="Профиль">${esc(n.toUpperCase())}</button></header>`;
  }
  function dateLine(s) {
    const d = parseDay(s), t = S.data.today.day;
    const word = s === t ? 'Сегодня' : (isoDay(new Date(parseDay(t).getTime() - 864e5)) === s ? 'Вчера' : WD[d.getDay()]);
    return `${word}, ${d.getDate()} ${MON[d.getMonth()]}`;
  }
  const cardHead = (title, right = '', ic = '') => `<div class="card-h">${ic}<h3>${title}</h3>${right}</div>`;

  // ---------- Сегодня ----------
  function onboarding() {
    const d = S.data;
    const steps = d.keys ? '' : `<section class="card"><div class="card-h"><h3>Один раз, 3 минуты</h3></div><ol class="steps">
      <li><b class="n">1</b><div>Открой <b>developer-dashboard.whoop.com</b> и войди аккаунтом WHOOP.</div></li>
      <li><b class="n">2</b><div>Создай Team, затем <b>Create App</b>. Privacy Policy: <b>sawqws.github.io/123/privacy.html</b>. Отметь все 6 scopes <b>read:*</b>.</div></li>
      <li><b class="n">3</b><div>Redirect URL — нажми, чтобы скопировать:<button class="code" data-a="copy" data-v="${esc(d.redirect)}">${esc(d.redirect || 'адрес ещё готовится…')}</button></div></li>
      <li><b class="n">4</b><div>Пришли боту: <span class="code">/whoop CLIENT_ID CLIENT_SECRET</span></div></li></ol></section>`;
    const feat = (ic, c, t, s) => `<div class="row"><span class="row-ic" style="--c:${c}">${icon(ic)}</span><div class="grow"><div class="row-t">${t}</div><div class="row-s">${s}</div></div></div>`;
    return `<section class="onb"><div class="logo">${icon('bolt')}</div><h2>Тренер по данным WHOOP</h2>
      <p>Каждое утро — план: как тренироваться, есть и спать, чтобы расти без перегруза.</p>
      ${d.keys ? `<button class="btn wide" data-a="connect">${icon('link')}Подключить WHOOP</button>` : ''}</section>
      ${steps}
      <section class="card list">
        ${feat('sun', 'var(--yellow)', 'Утренний разбор', 'Готовность и план на день')}
        ${feat('bolt', 'var(--strain)', 'После тренировки', 'Что съесть и как восстановиться')}
        ${feat('food', 'var(--food)', 'Фото еды', 'Ккал и БЖУ за секунды')}
        ${feat('moon', 'var(--sleep)', 'Сон', 'Когда лечь, чтобы выспаться')}
      </section>`;
  }

  function delta(v, base, unit, goodUp, dec = 0) {
    if (v == null || base == null) return '<span class="hint">нет нормы</span>';
    const d = v - base;
    if (Math.abs(d) < (dec ? .05 : .5)) return '<span class="hint">как обычно</span>';
    const good = goodUp ? d > 0 : d < 0;
    return `<span class="${good ? 'up' : 'down'}">${d > 0 ? '↑' : '↓'} ${num(Math.abs(d), dec)}${unit}</span><span class="hint"> к норме</span>`;
  }
  const metric = (label, color, value, unit, sub, attrs = '') =>
    `<div class="metric" ${attrs}><div class="m-k"><i style="background:${color}"></i><span>${label}</span></div>
      <div class="m-v num">${value}<small>${unit}</small></div><div class="m-d">${sub}</div></div>`;
  const sportIcon = s => /бокс|кик|единобор|мма|борьб|джиу/i.test(s) ? '🥊' : /бег|ходьб|поход/i.test(s) ? '🏃' : /силов|пауэр|функц/i.test(s) ? '🏋️' : '⚡';

  function todayView() {
    const d = S.data;
    if (!d.connected && !d.today.has_data) return header('Форма', 'Привет' + (d.user.name ? `, ${esc(d.user.name)}` : '')) + onboarding();
    const t = S.dayData || d.today, isToday = t.day === d.today.day;
    const r = t.recovery, s = t.sleep, c = t.cycle, b = t.base || {};
    const z = r ? r.zone : null, zc = z ? ZONE[z].c : 'var(--hint)';
    const nav = `<div class="day-nav"><button class="icon-btn" data-a="day" data-v="-1" aria-label="Предыдущий день">${icon('left')}</button>
      <button class="icon-btn" data-a="day" data-v="1" aria-label="Следующий день" ${isToday ? 'disabled' : ''}>${icon('right')}</button></div>`;
    let h = header(dateLine(t.day), isToday ? 'Сегодня' : 'День', nav);

    // главное: восстановление
    h += `<section class="card hero" style="--z:${zc}">
      <div class="ring-wrap">${ring(r ? r.score / 100 : 0, 176, 14, zc)}<div class="ring-in">
        <div class="ring-val num">${r ? r.score : '—'}<small>${r ? '%' : ''}</small></div><div class="ring-lbl">восстановление</div></div></div>
      ${r ? `<div class="verdict">${ZONE[z].v}</div>` : `<div class="hero-sub">WHOOP ещё не посчитал восстановление${isToday ? ' — оно появится после пробуждения' : ''}</div>`}
      ${t.band ? `<div class="hero-sub">Ориентир нагрузки <b class="num">${t.band[0]}–${t.band[1]}</b></div>` : ''}
      ${r && r.calibrating ? '<div class="hero-sub hint">WHOOP ещё калибруется</div>' : ''}</section>`;

    h += `<div class="grid2">
      ${metric('ВСР', 'var(--green)', r && r.hrv != null ? Math.round(r.hrv) : '—', 'мс', delta(r && r.hrv, b.hrv, ' мс', true))}
      ${metric('Пульс покоя', 'var(--red)', r && r.rhr != null ? r.rhr : '—', 'уд/мин', delta(r && r.rhr, b.rhr, '', false))}
      ${metric('Сон', 'var(--sleep)', s ? fmtH(s.hours) : '—', 'ч', s ? `<span class="hint">нужно ${fmtH(s.need)}</span>` : '<span class="hint">нет данных</span>', 'data-a="scroll" data-v="sleep" role="button"')}
      ${metric('Нагрузка', 'var(--strain)', c && c.strain != null ? num(c.strain, 1) : '—', 'из 21', c ? `<span class="hint">${big(c.kcal)} ккал</span>` : '<span class="hint">нет данных</span>', 'data-a="scroll" data-v="load" role="button"')}
    </div>`;

    // тренер
    const brief = t.brief;
    const right = isToday && brief && !S.briefing ? `<button class="link" data-a="brief">Обновить</button>` : '';
    h += `<section class="card coach">${cardHead('Тренер', right, `<span class="badge">${icon('spark')}</span>`)}
      ${S.briefing ? skel(4)
        : brief ? `<div class="prose">${tgHtml(brief)}</div>`
        : isToday ? `<p class="prose hint">${d.morning === 'off' ? 'Утренний разбор выключен — время в профиле.' : `Разбор придёт утром, с ${esc(d.morning || '07:00')}, как только WHOOP посчитает сон.`}</p>`
        : '<p class="prose hint">Разбора за этот день нет.</p>'}
      ${isToday ? `<div class="btns">${!brief && !S.briefing ? `<button class="btn" data-a="brief">${icon('spark')}Разобрать сейчас</button>` : ''}
        <button class="btn tinted" data-a="tab" data-v="coach">${icon('coach')}Спросить тренера</button></div>` : ''}</section>`;

    if (t.tips && t.tips.length) {
      const col = { good: 'var(--green)', warn: 'var(--yellow)', bad: 'var(--red)' };
      h += `<section class="card">${cardHead('Сигналы тела')}<div class="tips">${t.tips.map(x => `<div class="tip"><i style="background:${col[x.tone]}"></i><p>${esc(x.text)}</p></div>`).join('')}</div></section>`;
    }

    // сон
    if (s) {
      const tot = s.light + s.deep + s.rem + s.awake || 1, w = v => `${v / tot * 100}%`;
      const st = [['Глубокий', s.deep, 'var(--sleep)'], ['REM', s.rem, 'color-mix(in srgb, var(--sleep) 60%, var(--card))'], ['Лёгкий', s.light, 'color-mix(in srgb, var(--sleep) 30%, var(--card))'], ['Без сна', s.awake, 'var(--fill-2)']];
      h += `<section class="card" id="sleep">${cardHead('Сон', `<span class="side num">${esc(s.start)} → ${esc(s.end)}</span>`)}
        <div class="big-line"><span class="v num" style="color:var(--sleep)">${hHuman(s.hours)}</span><span class="s">из ${hHuman(s.need)}</span></div>
        <div class="stages">${st.map(([, v, col]) => v ? `<span style="width:${w(v)};background:${col}"></span>` : '').join('')}</div>
        <div class="legend">${st.map(([k, v, col]) => `<span><i style="background:${col}"></i>${k}<b class="num">${fmtH(v)}</b></span>`).join('')}</div>
        <div class="stats">
          <div><span>Эффективность</span><b class="num">${s.efficiency != null ? s.efficiency + '%' : '—'}</b></div>
          <div><span>Режим</span><b class="num">${s.consistency != null ? s.consistency + '%' : '—'}</b></div>
          <div><span>Дыхание</span><b class="num">${s.resp != null ? num(s.resp, 1) + '/мин' : '—'}</b></div>
          <div><span>Недосып</span><b class="num">${s.debt ? hHuman(s.debt) : 'нет'}</b></div></div>
        ${isToday && t.bedtime ? `<div class="note">${icon('moon')}<span>Отбой сегодня до <b class="num">${esc(t.bedtime.bed)}</b> — подъём в ${esc(t.bedtime.wake)}</span></div>` : ''}</section>`;
    }

    // нагрузка и тренировки
    if (c || (t.workouts && t.workouts.length)) {
      const p = v => `${pct(v, 21)}%`;
      const zc6 = ['var(--fill-2)', '#64d2ff', 'var(--green)', 'var(--yellow)', 'var(--food)', 'var(--red)'];
      h += `<section class="card" id="load">${cardHead('Нагрузка', b.strain != null ? `<span class="side">обычно <b class="num">${num(b.strain, 1)}</b></span>` : '')}
        ${c ? `<div class="big-line"><span class="v num" style="color:var(--strain)">${num(c.strain, 1)}</span><span class="s">${t.band ? `цель ${t.band[0]}–${t.band[1]}` : 'из 21'}</span></div>
        <div class="gauge">${t.band ? `<div class="band" style="left:${p(t.band[0])};width:calc(${p(t.band[1])} - ${p(t.band[0])})"></div>` : ''}<div class="fill" style="width:0" data-w="${p(c.strain || 0)}"></div></div>
        <div class="scale num"><span>0</span><span>7</span><span>14</span><span>21</span></div>` : ''}
        ${(t.workouts || []).map(w => {
          const tz = (w.zones || []).reduce((a, x) => a + x, 0) || 1;
          return `<div class="wk"><div class="wk-ic">${sportIcon(w.sport)}</div>
            <div class="grow"><div class="row-t">${esc(w.sport)}</div><div class="row-s num">${esc(w.start)} · ${w.minutes || '—'} мин${w.kcal != null ? ` · ${big(w.kcal)} ккал` : ''} · пульс ${w.avg_hr || '—'}/${w.max_hr || '—'}</div>
            ${w.zones && w.zones.length ? `<div class="zones">${w.zones.map((m, i) => m ? `<span style="width:${m / tz * 100}%;background:${zc6[i]}"></span>` : '').join('')}</div>` : ''}</div>
            <div class="wk-v num">${w.strain != null ? num(w.strain, 1) : '—'}</div></div>`;
        }).join('')}</section>`;
    }

    // питание кратко
    const n = t.nutrition, e = t.eaten;
    if (n && isToday) {
      const row = (k, v, g, txt, col) => `<div class="macro"><div class="macro-t"><span>${k}</span><span class="num">${txt}</span></div>${meter(pct(v, g), col)}</div>`;
      h += `<section class="card tap" data-a="tab" data-v="food" role="button">${cardHead('Питание', `<span class="side">${icon('chev', 'ic chev')}</span>`)}
        <div class="macros">${row('Калории', e.kcal, n.kcal, `<b>${big(e.kcal)}</b> / ${big(n.kcal)}`, 'var(--food)')}${row('Белок', e.protein, n.protein, `<b>${e.protein}</b> / ${n.protein} г`, 'var(--green)')}
        ${t.water ? row('Вода', t.water.drank, t.water.target, `<b>${num(t.water.drank / 1000, 1)}</b> / ${num(t.water.target / 1000, 1)} л`, 'var(--water)') : ''}</div></section>`;
    }
    if (isToday && d.synced) h += `<p class="foot">WHOOP обновлён в ${new Date(d.synced).toLocaleTimeString('ru', { hour: '2-digit', minute: '2-digit' })} · <button class="link" data-a="sync">Обновить</button></p>`;
    return h;
  }

  // ---------- Тренды ----------
  function trendsView() {
    let h = header(`Последние ${S.range} дней`, 'Тренды');
    h += `<div class="seg" role="tablist">${[7, 30, 90].map(n => `<button class="${S.range === n ? 'on' : ''}" data-a="range" data-v="${n}" role="tab">${n} дней</button>`).join('')}<i style="transform:translateX(${[7, 30, 90].indexOf(S.range) * 100}%)"></i></div>`;
    const rows = S.series[S.range] || (S.range === 30 ? S.data.series : null);
    if (!rows) return h + [1, 2, 3].map(() => `<section class="card"><div class="sk sk-h"></div><div class="sk sk-chart"></div></section>`).join('');
    if (!rows.some(r => r.recovery != null || r.strain != null)) return h + empty('trends', 'Данных пока нет', 'Подключи WHOOP — графики появятся после первой ночи с браслетом.');
    const zc = r => r.zone === 'green' ? 'var(--green)' : r.zone === 'yellow' ? 'var(--yellow)' : 'var(--red)';
    const card = (title, value, unit, side, chart) => `<section class="card">${cardHead(title, `<span class="side">${side}</span>`)}
      <div class="big-line"><span class="v num">${value}</span><span class="s">${unit}</span></div>${chart}</section>`;
    const pr = S.progress;
    h += `<section class="card coach">${cardHead('Разбор прогресса', pr && !S.progressing ? `<button class="link" data-a="progress">Обновить</button>` : '', `<span class="badge">${icon('spark')}</span>`)}
      ${S.progressing ? skel(3) + '<p class="hint small">Разбираю 2 месяца данных — около минуты…</p>'
        : pr ? `<p class="hint small">от ${dateLine(pr.day).toLowerCase()}</p><div class="prose">${tgHtml(pr.text)}</div>`
        : `<p class="prose hint">Тренды, сон, нагрузка, питание и план на 2 недели — по всем данным WHOOP.</p>
           <div class="btns"><button class="btn" data-a="progress">${icon('spark')}Разобрать прогресс</button></div>`}</section>`;
    const greens = rows.filter(r => r.zone === 'green').length, reds = rows.filter(r => r.zone === 'red').length;
    h += card('Восстановление', num(avg(rows, 'recovery')), '% в среднем', `<i class="dot" style="background:var(--green)"></i>${greens} <i class="dot" style="background:var(--red)"></i>${reds}`, bars(rows, 'recovery', { max: 100, color: zc }));
    h += card('ВСР', num(avg(rows, 'hrv')), 'мс', 'выше — лучше', line(rows, 'hrv', 'var(--green)', avg(rows, 'hrv')));
    h += card('Пульс покоя', num(avg(rows, 'rhr')), 'уд/мин', 'ниже — лучше', line(rows, 'rhr', 'var(--red)', avg(rows, 'rhr')));
    h += card('Сон', fmtH(avg(rows, 'sleep')), 'ч в среднем', '<i class="tick"></i>нужно', bars(rows, 'sleep', { color: 'var(--sleep)', ref: 'need' }));
    const wk = rows.reduce((a, r) => a + (r.workouts || 0), 0);
    h += card('Нагрузка', num(avg(rows, 'strain'), 1), 'в среднем', `${wk} ${plural(wk, 'тренировка', 'тренировки', 'тренировок')}`, bars(rows, 'strain', { max: 21, color: 'var(--strain)' }));
    return h;
  }
  async function loadSeries(n) {
    try { S.series[n] = (await api('/api/series', { n })).series; if (S.tab === 'trends') render(); } catch (e) { fail(e); }
  }
  async function loadProgress(make) {
    if (make) { if (S.progressing) return; S.progressing = true; H.tap(); render(); }
    try { S.progress = (await api('/api/progress', make ? { make: 1 } : {})).progress; if (make) H.ok(); } catch (e) { fail(e); }
    S.progressing = false; if (S.tab === 'trends') render();
  }
  const empty = (ic, title, text, action = '') => `<section class="empty">${icon(ic === 'trends' ? 'trends' : ic)}<h3>${title}</h3><p>${text}</p>${action}</section>`;

  // ---------- Питание ----------
  function foodView() {
    const t = S.data.today, n = t.nutrition, e = t.eaten;
    let h = header(dateLine(t.day), 'Питание');
    if (!n) {
      h += `<section class="card">${cardHead('Норма на день')}<p class="prose">Добавь вес (и лучше рост и возраст) в профиле — посчитаю калории и БЖУ под твои тренировки.</p>
        <div class="btns"><button class="btn" data-a="profile">${icon('user')}Заполнить профиль</button></div></section>`;
    } else {
      const left = n.kcal - e.kcal;
      h += `<section class="card"><div class="food-hero"><div class="food-ring">${ring(e.kcal / n.kcal, 120, 12, 'var(--food)')}
        <div class="ring-in"><div class="v num">${big(Math.abs(left))}</div><div class="s">${left >= 0 ? 'ккал осталось' : 'ккал сверх'}</div></div></div>
        <div class="macros">${[['Белок', e.protein, n.protein, 'var(--green)'], ['Жиры', e.fat, n.fat, 'var(--yellow)'], ['Углеводы', e.carbs, n.carbs, 'var(--strain)']].map(([k, v, g, c]) =>
          `<div class="macro"><div class="macro-t"><span>${k}</span><span class="num"><b>${v}</b> / ${g} г</span></div>${meter(pct(v, g), c)}</div>`).join('')}</div></div>
        <p class="hint small">Норма ${big(n.kcal)} ккал — ${esc(n.how)}.${n.teen ? ' В твоём возрасте калории не урезаем: рост важнее.' : ''}</p></section>`;
    }
    const w = t.water;
    if (w) {
      h += `<section class="card">${cardHead('Вода', `<span class="side num"><b>${liters(w.drank)}</b> из ${liters(w.target)}</span>`, `<span class="badge" style="--c:var(--water)">${icon('drop')}</span>`)}
        ${meter(pct(w.drank, w.target), 'var(--water)')}
        ${w.note ? `<p class="prose small water-note">${esc(w.note)}</p>` : ''}
        <div class="quick">${[[250, 'стакан'], [500, 'бутылка'], [1000, 'литр']].map(([ml, k]) => `<button class="q-btn" data-a="water" data-v="${ml}"><b class="num">+${ml}</b><span>${k}</span></button>`).join('')}</div>
        ${w.log.length ? `<div class="chips-wrap">${w.log.map(x => `<span class="pill num">${esc(x.time)} · ${x.ml} мл<button data-a="water-del" data-v="${x.id}" aria-label="Удалить">${icon('x')}</button></span>`).join('')}</div>` : ''}
        <p class="hint small">Норма: ${esc(w.why)}. Можно писать боту: «выпил 500 мл».</p></section>`;
    }
    h += `<section class="card">${cardHead('Что съел?', `<button class="link" data-a="meal-new">${icon('plus', 'ic sm')}Вручную</button>`)}
      <div class="ask"><textarea id="eat" rows="1" placeholder="Гречка с курицей…">${esc(S.eatDraft || '')}</textarea>
      <button class="send" data-a="eat" aria-label="Посчитать" ${S.eating ? 'disabled' : ''}>${S.eating ? '<span class="dots"><i></i><i></i><i></i></span>' : icon('send')}</button></div>
      <p class="hint small">Или пришли боту фото тарелки — посчитаю сам.</p></section>`;
    h += `<section class="card">${cardHead('Сегодня', e.count ? `<span class="side num">${big(e.kcal)} ккал</span>` : '')}${t.meals.length ? t.meals.map(m => `
      <div class="meal"><div class="time num">${esc(m.time)}</div><button class="grow meal-b" data-a="meal-edit" data-v="${m.id}"><div class="row-t">${esc(m.title)}</div>
      <div class="row-s num">Б ${Math.round(m.protein)} · Ж ${Math.round(m.fat)} · У ${Math.round(m.carbs)}</div></button><div class="k num">${big(m.kcal)}</div>
      <button class="del" data-a="meal-del" data-v="${m.id}" aria-label="Удалить">${icon('trash')}</button></div>`).join('')
      : `<div class="empty-sm">${icon('food')}<span>Пока пусто — напиши, что съел, или пришли фото боту.</span></div>`}</section>`;
    return h;
  }

  // ---------- Тренер ----------
  const SUGGEST = ['Готов ли я сегодня к спаррингу?', 'Что съесть перед тренировкой?', 'Почему упало восстановление?', 'Как улучшить сон?', 'План на неделю', 'Что съесть после тренировки?'];
  function coachView() {
    let h = header('Врач · тренер · питание', 'Тренер', S.history && S.history.length ? `<button class="icon-btn" data-a="clear" aria-label="Новый разговор">${icon('refresh')}</button>` : '');
    const hist = S.history;
    if (!hist) return h + `<div class="chat"><div class="sk sk-msg me"></div><div class="sk sk-msg"></div><div class="sk sk-msg me short"></div></div>`;
    h += '<div class="chat">';
    if (!hist.length && !S.asking) h += empty('coach', 'Спроси что угодно', 'Про тренировки, сон, питание и восстановление — отвечу по твоим данным WHOOP.');
    hist.forEach(m => { h += m.role === 'user' ? `<div class="msg me">${esc(m.text)}</div>` : `<div class="msg them prose">${md(m.text)}</div>`; });
    if (S.asking) h += `<div class="msg me">${esc(S.asking)}</div><div class="msg them"><span class="dots"><i></i><i></i><i></i></span></div>`;
    h += '</div>';
    h += `<div class="composer"><div class="chips">${SUGGEST.map(s => `<button class="chip" data-a="suggest" data-v="${esc(s)}">${esc(s)}</button>`).join('')}</div>
      <div class="ask"><textarea id="q" rows="1" placeholder="Спроси тренера…">${esc(S.qDraft || '')}</textarea>
      <button class="send" data-a="ask" aria-label="Отправить" ${S.asking ? 'disabled' : ''}>${icon('send')}</button></div></div>`;
    return h;
  }
  async function loadHistory() {
    try { S.history = (await api('/api/history', {})).history; if (S.tab === 'coach') render(); } catch (e) { fail(e); }
  }
  async function ask(q) {
    q = (q || '').trim(); if (!q || S.asking) return;
    S.asking = q; S.qDraft = ''; H.tap(); render();
    try { const r = await api('/api/ask', { text: q }); S.history = r.history; H.ok(); }
    catch (e) { fail(e); S.qDraft = q; }
    S.asking = false; if (S.tab === 'coach') render();
  }

  // ---------- окна ----------
  function sheet(html, mount) {
    const root = $('#sheet-root');
    root.innerHTML = `<div class="backdrop" data-a="close"></div><div class="sheet" role="dialog"><div class="grip"></div>${html}</div>`;
    document.body.classList.add('locked');
    if (tgw) { tgw.BackButton.show(); }
    if (mount) mount(root);
  }
  function closeSheet() {
    const root = $('#sheet-root'), sh = $('.sheet', root);
    document.body.classList.remove('locked');
    if (tgw) tgw.BackButton.hide();
    if (!sh) return;
    root.classList.add('closing');
    setTimeout(() => { root.innerHTML = ''; root.classList.remove('closing'); }, 200);
  }

  function goalsBlock() {
    const gs = S.data.goals || [];
    return `${gs.length ? `<div class="group">${gs.map(g => `<div class="row"><div class="grow"><div class="row-t wrap">${esc(g.text)}</div></div>
        <button class="del" data-a="goal-del" data-v="${g.id}" aria-label="Удалить">${icon('trash')}</button></div>`).join('')}</div>`
      : '<p class="hint small">Например: «ложиться до 23:00», «соревнования по боксу весной». Тренер привязывает к ним все советы.</p>'}
      ${gs.length < 10 ? `<div class="ask"><textarea id="goal-new" rows="1" placeholder="Новая цель…"></textarea>
        <button class="send" data-a="goal-add" aria-label="Добавить">${icon('plus')}</button></div>` : ''}`;
  }
  async function goalApi(path, body) {
    try { S.data.goals = (await api(path, body)).goals; H.ok(); const el = $('#goals'); if (el) el.innerHTML = goalsBlock(); }
    catch (e) { fail(e); }
  }

  const GOALS = [['form', 'Форма и результат'], ['mass', 'Набор мышц'], ['dry', 'Рельеф'], ['weight', 'Держать вес']];
  function profileSheet() {
    const d = S.data, p = d.profile, w = d.body || {};
    const conn = d.connected
      ? `<div class="status"><i style="background:var(--green)"></i><div class="grow"><div class="row-t">WHOOP подключён</div><div class="row-s">${d.synced ? 'обновлено ' + new Date(d.synced).toLocaleString('ru', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : ''}</div></div><button class="icon-btn" data-a="sync" aria-label="Обновить">${icon('refresh')}</button></div>`
      : `<div class="status"><i style="background:var(--red)"></i><div class="grow"><div class="row-t">WHOOP не подключён</div></div>${d.keys ? `<button class="btn sm" data-a="connect">Подключить</button>` : ''}</div>`;
    const f = (k, label, ph, type = 'text') => `<label class="field"><span>${label}</span><input name="${k}" type="${type === 'number' ? 'text' : type}" inputmode="${type === 'number' ? 'decimal' : 'text'}" placeholder="${esc(ph)}" value="${esc(p[k] != null ? p[k] : '')}"></label>`;
    sheet(`<h2>Профиль</h2>${conn}
      <div class="sec"><div class="sec-h">Мои цели</div><div id="goals">${goalsBlock()}</div></div>
      <form id="pf">
      <div class="sec"><div class="sec-h">Тело</div><div class="row3">${f('age', 'Возраст', '14', 'number')}${f('height', 'Рост, см', w.height_meter ? Math.round(w.height_meter * 100) : '170', 'number')}${f('weight', 'Вес, кг', w.weight_kilogram ? num(w.weight_kilogram, 1) : '60', 'number')}</div>
      <div class="field"><span>Пол</span><div class="opts" data-group="sex">${[['m', 'Парень'], ['f', 'Девушка']].map(([k, t]) => `<button type="button" class="chip ${p.sex === k ? 'on' : ''}" data-a="opt" data-v="${k}">${t}</button>`).join('')}</div></div>
      <div class="field"><span>Цель</span><div class="opts" data-group="goal">${GOALS.map(([k, t]) => `<button type="button" class="chip ${(p.goal || 'form') === k ? 'on' : ''}" data-a="opt" data-v="${k}">${t}</button>`).join('')}</div></div></div>
      <div class="sec"><div class="sec-h">Тренировки и еда</div>
      ${f('sport', 'Спорт', 'бокс, 8 месяцев')}
      ${f('schedule', 'Расписание тренировок', 'пн, ср, пт, сб — 2 часа вечером')}
      <label class="field"><span>Питание: что ешь и чего не ешь</span><textarea name="diet" placeholder="школьная столовая, не ем рыбу…">${esc(p.diet || '')}</textarea></label></div>
      <div class="sec"><div class="sec-h">Тренер</div>
      <label class="field"><span>Утренний разбор: время или «выкл»</span><input name="morning" type="text" placeholder="07:00" value="${esc(d.morning === 'off' ? 'выкл' : d.morning || '07:00')}"></label>
      <label class="field"><span>Важно знать тренеру</span><textarea name="notes" placeholder="травмы, соревнования, самочувствие…">${esc(p.notes || '')}</textarea></label></div>
      <button class="btn wide" type="submit">Сохранить</button></form>`, root => {
      $('#pf', root).addEventListener('submit', async ev => {
        ev.preventDefault();
        const fd = Object.fromEntries(new FormData(ev.target).entries());
        root.querySelectorAll('[data-group]').forEach(g => { const on = $('.chip.on', g); fd[g.dataset.group] = on ? on.dataset.v : ''; });
        try { const r = await api('/api/profile', fd); S.data.profile = r.profile; closeSheet(); H.ok(); toast('Сохранено'); await refresh(); }
        catch (e) { fail(e); }
      });
    });
  }
  function mealSheet(m) {
    m = m || { title: '', kcal: '', protein: '', fat: '', carbs: '' };
    const f = (k, label, type = 'number') => `<label class="field"><span>${label}</span><input name="${k}" type="text" inputmode="${type === 'number' ? 'decimal' : 'text'}" value="${esc(m[k] === '' ? '' : type === 'number' ? Math.round(m[k]) : m[k])}"></label>`;
    sheet(`<h2>${m.id ? 'Изменить' : 'Добавить еду'}</h2><form id="mf">${f('title', 'Что', 'text')}<div class="row2">${f('kcal', 'Ккал')}${f('protein', 'Белки, г')}${f('fat', 'Жиры, г')}${f('carbs', 'Углеводы, г')}</div>
      <button class="btn wide" type="submit">Сохранить</button></form>`, root => {
      $('#mf', root).addEventListener('submit', async ev => {
        ev.preventDefault();
        const fd = Object.fromEntries(new FormData(ev.target).entries());
        if (m.id) fd.id = m.id;
        try { const r = await api('/api/meal', fd); S.data.today = r.day; closeSheet(); H.ok(); render(); } catch (e) { fail(e); }
      });
    });
  }

  // ---------- действия ----------
  async function refresh() {
    try { S.data = await api('/api/all'); S.dayData = null; render(); } catch (e) { fail(e); }
  }
  async function eat() {
    const el = $('#eat'), text = el ? el.value.trim() : '';
    if (!text || S.eating) return;
    S.eating = true; S.eatDraft = text; H.tap(); render();
    try { const r = await api('/api/meal/estimate', { text }); S.data.today = r.day; S.eatDraft = ''; H.ok(); if (r.comment) toast(r.comment); }
    catch (e) { fail(e); }
    S.eating = false; render();
  }
  async function brief() {
    if (S.briefing) return;
    S.briefing = true; H.tap(); render();
    try { const r = await api('/api/brief', {}); S.data.today.brief = r.brief; H.ok(); } catch (e) { fail(e); }
    S.briefing = false; render();
  }
  async function connect() {
    H.tap();
    try { const r = await api('/api/connect', {}); if (tgw) tgw.openLink(r.url); else location.href = r.url; } catch (e) { fail(e); }
  }
  async function shiftDay(k) {
    const cur = parseDay((S.dayData || S.data.today).day);
    const nd = isoDay(new Date(cur.getTime() + k * 864e5));
    if (nd > S.data.today.day) return;
    H.pick();
    if (nd === S.data.today.day) { S.dayData = null; render(); return; }
    try { S.dayData = (await api('/api/day', { d: nd })).day; render(); } catch (e) { fail(e); }
  }

  document.addEventListener('click', async ev => {
    const tab = ev.target.closest('[data-tab]');
    if (tab) return go(tab.dataset.tab);
    const el = ev.target.closest('[data-a]');
    if (!el || el.disabled) return;
    const a = el.dataset.a, v = el.dataset.v;
    if (a === 'profile') { H.tap(); profileSheet(); }
    else if (a === 'close') closeSheet();
    else if (a === 'tab') go(v);
    else if (a === 'connect') connect();
    else if (a === 'brief') brief();
    else if (a === 'progress') loadProgress(true);
    else if (a === 'day') shiftDay(Number(v));
    else if (a === 'range') { if (S.range === Number(v)) return; S.range = Number(v); H.pick(); render(); if (!S.series[S.range]) loadSeries(S.range); }
    else if (a === 'eat') eat();
    else if (a === 'ask') { const q = $('#q'); ask(q && q.value); }
    else if (a === 'suggest') ask(v);
    else if (a === 'clear') { H.tap(); try { S.history = (await api('/api/history/clear', {})).history; render(); } catch (e) { fail(e); } }
    else if (a === 'goal-add') { const g = $('#goal-new'); if (g && g.value.trim()) goalApi('/api/goal', { text: g.value.trim() }); }
    else if (a === 'goal-del') { H.tap(); goalApi('/api/goal/delete', { id: Number(v) }); }
    else if (a === 'water') {
      H.soft();
      try { S.data.today = (await api('/api/water', { ml: Number(v) })).day; render(); toast(`+${v} мл`); } catch (e) { fail(e); }
    } else if (a === 'water-del') {
      H.tap();
      try { S.data.today = (await api('/api/water/delete', { id: Number(v) })).day; render(); } catch (e) { fail(e); }
    }
    else if (a === 'meal-new') { H.tap(); mealSheet(); }
    else if (a === 'meal-edit') { H.tap(); mealSheet(S.data.today.meals.find(m => String(m.id) === v)); }
    else if (a === 'meal-del') {
      H.tap();
      try { S.data.today = (await api('/api/meal/delete', { id: Number(v) })).day; render(); } catch (e) { fail(e); }
    } else if (a === 'opt') {
      el.parentElement.querySelectorAll('.chip').forEach(c => c.classList.toggle('on', c === el && !c.classList.contains('on')));
      H.pick();
    } else if (a === 'scroll') { const s = document.getElementById(v); if (s) { H.tap(); s.scrollIntoView({ behavior: 'smooth', block: 'start' }); } }
    else if (a === 'sync') {
      H.tap(); toast('Обновляю WHOOP…');
      try { await api('/api/sync', {}); await refresh(); H.ok(); toast('Данные обновлены'); } catch (e) { fail(e); }
    } else if (a === 'copy') {
      try { await navigator.clipboard.writeText(v); H.ok(); toast('Скопировано'); } catch (e) { toast('Выдели и скопируй адрес'); }
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
  window.addEventListener('resize', () => { if (Math.abs(window.innerWidth - rw) > 20 && S.tab === 'trends') { rw = window.innerWidth; render(); } });

  // ---------- тема Telegram ----------
  // Цвета берём из темы Telegram (themeParams); если карточки и фон совпали — фон чуть темнее, чтобы карточки читались.
  function hex(c) { const m = /^#?([0-9a-f]{6})$/i.exec(c || ''); return m ? parseInt(m[1], 16) : null; }
  function lum(c) { const v = hex(c); if (v == null) return null; return (0.2126 * (v >> 16) + 0.7152 * (v >> 8 & 255) + 0.0722 * (v & 255)) / 255; }
  function toHex(css) { // rgb(…) или color(srgb …) -> #rrggbb для setHeaderColor
    const f = (css || '').match(/[\d.]+/g);
    if (!f || f.length < 3) return null;
    const k = css.startsWith('color(') ? 255 : 1;
    return '#' + f.slice(0, 3).map(x => Math.round(Number(x) * k).toString(16).padStart(2, '0')).join('');
  }
  function theme() {
    const root = document.documentElement, tp = (tgw && tgw.themeParams) || {};
    const dark = tgw && tgw.colorScheme ? tgw.colorScheme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
    root.classList.toggle('dark', dark);
    const set = (k, v) => { if (v) root.style.setProperty(k, v); else root.style.removeProperty(k); };
    let card = tp.section_bg_color || tp.bg_color, bg = tp.secondary_bg_color;
    if (card && bg && Math.abs(lum(card) - lum(bg)) < .02) bg = tp.bg_color !== card ? tp.bg_color : null;
    if (card && bg && Math.abs(lum(card) - lum(bg)) < .02) bg = null;
    set('--card', card); set('--bg', bg || (card ? `color-mix(in srgb, ${card} 92%, ${dark ? '#000' : '#8e8e93'})` : ''));
    set('--text', tp.text_color); set('--hint', tp.hint_color || tp.subtitle_text_color);
    set('--accent', tp.button_color); set('--accent-text', tp.button_text_color);
    set('--link', tp.link_color || tp.accent_text_color); set('--danger', tp.destructive_text_color);
    const h = toHex(getComputedStyle(document.body).backgroundColor);
    try { if (h) { tgw.setHeaderColor(h); tgw.setBackgroundColor(h); if (tgw.setBottomBarColor) tgw.setBottomBarColor(h); } } catch (e) { /* старый Telegram */ }
  }
  async function start() {
    if (tgw) {
      tgw.ready(); tgw.expand();
      try { if (tgw.disableVerticalSwipes) tgw.disableVerticalSwipes(); } catch (e) { /* нет */ }
      tgw.onEvent('themeChanged', () => { theme(); render(); });
      tgw.BackButton.onClick(closeSheet);
    }
    theme();
    const p = new URLSearchParams(location.search).get('p') || '';
    const t = p.replace(/^\//, '');
    if (['trends', 'food', 'coach'].includes(t)) S.tab = t;
    renderTabs();
    try { S.data = await api('/api/all'); }
    catch (e) { view.innerHTML = `<section class="onb"><div class="logo">${icon('bolt')}</div><h2>Форма</h2><p>${esc(e.message)}</p></section>`; return; }
    render();
    if (S.tab === 'trends' && !S.series[S.range]) loadSeries(S.range);
    if (S.tab === 'trends') loadProgress();
    if (S.tab === 'coach') loadHistory();
    if (t === 'profile') profileSheet();
    // данные обновляются сами, пока приложение открыто
    setInterval(async () => {
      if (document.hidden || $('#sheet-root').innerHTML || S.asking || S.eating || S.briefing || S.dayData) return;
      if (document.activeElement && document.activeElement.tagName === 'TEXTAREA') return;
      try { S.data = await api('/api/all'); if (S.tab !== 'coach') render(false); } catch (e) { /* тихо */ }
    }, 120000);
  }
  start();
})();
