/* Форма — приложение тренера внутри Telegram. Без сборки и библиотек. */
(() => {
  const tgw = window.Telegram && window.Telegram.WebApp;
  const $ = (s, el = document) => el.querySelector(s);
  const view = $('#view');
  const S = { data: null, tab: 'today', day: null, dayData: null, range: 30, series: {}, history: null, asking: false, eating: false, briefing: false };
  const WD = ['Воскресенье', 'Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота'];
  const MON = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
  const MON_S = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

  const I = {
    today: '<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/>',
    trends: '<path d="M3 3v18h18"/><path d="m7 15 4-4 3 3 5-6"/>',
    food: '<path d="M12 7c-1.6-1.8-4.2-2.2-6-1-2.4 1.6-2.6 5.2-1.6 8.6C5.4 18 7.4 21 9.4 21c1 0 1.6-.5 2.6-.5s1.6.5 2.6.5c2 0 4-3 5-6.4 1-3.4.8-7-1.6-8.6-1.8-1.2-4.4-.8-6 1z"/><path d="M12 7c0-2.2 1.2-3.8 3.4-4.4"/>',
    coach: '<path d="M21 11.5a8.4 8.4 0 0 1-12.3 7.5L3 21l2-5.3A8.4 8.4 0 1 1 21 11.5z"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-6 8-6s8 2 8 6"/>',
    send: '<path d="M5 12h14M13 6l6 6-6 6"/>',
    refresh: '<path d="M20 12a8 8 0 1 1-2.4-5.7L20 8.6"/><path d="M20 3.5v5.1h-5.1"/>',
    trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    left: '<path d="m15 18-6-6 6-6"/>',
    right: '<path d="m9 18 6-6-6-6"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/>',
    link: '<path d="M10 14a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/><path d="M14 10a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/>',
  };
  const icon = (n, cls = 'ic') => `<svg class="${cls}" viewBox="0 0 24 24">${I[n]}</svg>`;
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const num = (x, d = 0) => x == null ? '—' : Number(x).toFixed(d).replace('.', ',');
  const fmtH = h => { if (h == null) return '—'; const m = Math.round(h * 60); return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}`; };
  const hHuman = h => { if (h == null) return '—'; const m = Math.round(h * 60); return `${Math.floor(m / 60)} ч ${String(m % 60).padStart(2, '0')} м`; };
  const isoDay = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const parseDay = s => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
  const haptic = (k = 'light') => { try { tgw.HapticFeedback.impactOccurred(k); } catch (e) { /* нет */ } };
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
  const fail = e => { haptic('heavy'); toast(e.message || String(e)); };

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

  // ---------- графики ----------
  function ring(pct, size, stroke, color) {
    const r = (size - stroke) / 2, c = 2 * Math.PI * r, off = c * (1 - Math.max(0, Math.min(1, pct)));
    return `<svg viewBox="0 0 ${size} ${size}"><circle class="ring-bg" cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke-width="${stroke}"/>
      <circle class="ring-fg" cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke-width="${stroke}" stroke-linecap="round"
      stroke-dasharray="${c}" stroke-dashoffset="${c}" data-off="${off}" ${color ? `style="stroke:${color};filter:none"` : ''}/></svg>`;
  }
  function animateRings() {
    requestAnimationFrame(() => requestAnimationFrame(() => {
      document.querySelectorAll('.ring-fg[data-off]').forEach(el => { el.style.strokeDashoffset = el.dataset.off; });
      document.querySelectorAll('[data-w]').forEach(el => { el.style.width = el.dataset.w; });
    }));
  }
  function xLabels(days, W, pad) {
    const n = days.length, step = Math.max(1, Math.round(n / 5));
    let s = '';
    days.forEach((d, i) => {
      if (i % step && i !== n - 1) return;
      if (i !== n - 1 && n - 1 - i < step * .6) return;
      const dt = parseDay(d), x = pad + (i + .5) * (W - pad * 2) / n;
      s += `<text x="${x}" y="128" text-anchor="middle">${n <= 7 ? ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'][dt.getDay()] : `${dt.getDate()} ${MON_S[dt.getMonth()]}`}</text>`;
    });
    return s;
  }
  function bars(rows, key, { max, color, ref } = {}) {
    const W = 320, H = 110, pad = 2, n = rows.length;
    const vals = rows.map(r => r[key]);
    const top = max || Math.max(1, ...vals.filter(v => v != null)) * 1.12;
    const bw = (W - pad * 2) / n, gap = n > 40 ? 1 : n > 14 ? 2 : 5;
    let s = `<line class="grid" x1="0" x2="${W}" y1="${H}" y2="${H}"/>`;
    rows.forEach((r, i) => {
      const v = r[key]; if (v == null) return;
      const h = Math.max(3, v / top * (H - 6)), x = pad + i * bw + gap / 2;
      const c = typeof color === 'function' ? color(r) : color;
      s += `<rect x="${x}" y="${H - h}" width="${Math.max(1, bw - gap)}" height="${h}" rx="${Math.min(4, (bw - gap) / 2)}" fill="${c}"><title>${r.day}: ${v}</title></rect>`;
    });
    if (ref) rows.forEach((r, i) => {
      const v = r[ref]; if (v == null) return;
      const y = H - v / top * (H - 6);
      s += `<line x1="${pad + i * bw + gap / 2}" x2="${pad + (i + 1) * bw - gap / 2}" y1="${y}" y2="${y}" stroke="var(--text-2)" stroke-width="2" stroke-linecap="round" opacity=".55"/>`;
    });
    return `<svg class="chart" viewBox="0 0 ${W} 132" preserveAspectRatio="none">${s}${xLabels(rows.map(r => r.day), W, pad)}</svg>`;
  }
  function line(rows, key, color, base) {
    const W = 320, H = 110, pad = 6, n = rows.length;
    const pts = rows.map((r, i) => [pad + (i + .5) * (W - pad * 2) / n, r[key]]).filter(p => p[1] != null);
    if (pts.length < 2) return '<div class="dim small">Мало данных для графика</div>';
    const vs = pts.map(p => p[1]).concat(base != null ? [base] : []);
    let lo = Math.min(...vs), hi = Math.max(...vs); const sp = (hi - lo) || 1; lo -= sp * .25; hi += sp * .2;
    const y = v => H - (v - lo) / (hi - lo) * (H - 8) - 4;
    const P = pts.map(p => [p[0], y(p[1])]);
    let d = `M${P[0][0]},${P[0][1]}`;
    for (let i = 1; i < P.length; i++) { const [x0, y0] = P[i - 1], [x1, y1] = P[i], cx = (x0 + x1) / 2; d += ` C${cx},${y0} ${cx},${y1} ${x1},${y1}`; }
    const id = 'g' + key + Math.random().toString(36).slice(2, 6);
    let s = `<defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".32"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>`;
    if (base != null) s += `<line class="grid" x1="0" x2="${W}" y1="${y(base)}" y2="${y(base)}"/><text x="${W}" y="${y(base) - 4}" text-anchor="end">норма ${num(base)}</text>`;
    s += `<path d="${d} L${P[P.length - 1][0]},${H} L${P[0][0]},${H} Z" fill="url(#${id})"/>`;
    s += `<path d="${d}" fill="none" stroke="${color}" stroke-width="2.6" stroke-linecap="round"/>`;
    const last = P[P.length - 1];
    s += `<circle cx="${last[0]}" cy="${last[1]}" r="4.5" fill="${color}" stroke="var(--surface)" stroke-width="2"/>`;
    return `<svg class="chart" viewBox="0 0 ${W} 132" preserveAspectRatio="none">${s}${xLabels(rows.map(r => r.day), W, pad)}</svg>`;
  }
  const avg = (rows, k) => { const v = rows.map(r => r[k]).filter(x => x != null); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };

  // ---------- вкладки ----------
  const TABS = [['today', 'Сегодня'], ['trends', 'Тренды'], ['food', 'Питание'], ['coach', 'Тренер']];
  function renderTabs() {
    $('#tabs').innerHTML = TABS.map(([k, t]) => `<button class="tab ${S.tab === k ? 'on' : ''}" data-tab="${k}">${icon(k)}<span>${t}</span></button>`).join('');
  }
  function go(tab) {
    if (S.tab === tab) { window.scrollTo({ top: 0, behavior: 'smooth' }); return; }
    S.tab = tab; haptic(); renderTabs(); render(); window.scrollTo(0, 0);
    if (tab === 'trends' && !S.series[S.range]) loadSeries(S.range);
    if (tab === 'coach' && !S.history) loadHistory();
  }
  function render() {
    if (!S.data) return;
    const html = { today: todayView, trends: trendsView, food: foodView, coach: coachView }[S.tab]();
    view.innerHTML = `<div class="enter">${html}</div>`;
    animateRings();
    if (S.tab === 'coach') { const c = $('.chat'); if (c) window.scrollTo(0, document.body.scrollHeight); }
  }

  function header(eyebrow, title, extra = '') {
    const n = (S.data.user.name || '?').trim()[0] || '?';
    return `<div class="top"><div class="grow"><div class="eyebrow">${eyebrow}</div><h1 class="title">${title}</h1></div>${extra}
      <button class="avatar" data-a="profile" aria-label="Профиль">${esc(n.toUpperCase())}</button></div>`;
  }
  function dateLine(s) {
    const d = parseDay(s), t = S.data.today.day;
    const word = s === t ? 'Сегодня' : (isoDay(new Date(parseDay(t).getTime() - 864e5)) === s ? 'Вчера' : WD[d.getDay()]);
    return `${word}, ${d.getDate()} ${MON[d.getMonth()]}`;
  }

  // ---------- Сегодня ----------
  function onboarding() {
    const d = S.data;
    const steps = d.keys ? '' : `<div class="card" style="text-align:left"><div class="card-h"><h3>Один раз, 3 минуты</h3></div><div class="steps">
      <div class="step"><b class="n">1</b><div>Открой <b>developer-dashboard.whoop.com</b> и войди аккаунтом WHOOP.</div></div>
      <div class="step"><b class="n">2</b><div>Создай Team, затем <b>Create App</b>. Privacy Policy: <b>sawqws.github.io/123/privacy.html</b>. Отметь все 6 scopes <b>read:*</b>.</div></div>
      <div class="step"><b class="n">3</b><div>Redirect URL:<span class="code" data-a="copy" data-v="${esc(d.redirect)}">${esc(d.redirect || 'адрес ещё готовится…')}</span></div></div>
      <div class="step"><b class="n">4</b><div>Пришли боту: <span class="code">/whoop CLIENT_ID CLIENT_SECRET</span></div></div></div></div>`;
    return `<div class="onb"><div class="logo">${icon('today')}</div><h1>Форма</h1>
      <p>Тренер, спортивный врач и нутрициолог по данным твоего WHOOP. Каждое утро — план: как тренироваться, есть и спать.</p>
      ${d.keys ? `<button class="btn wide" data-a="connect">${icon('link')}Подключить WHOOP</button>` : ''}</div>
      ${steps}
      <div class="feats">
        <div class="feat"><div class="e">☀️</div><b>Утренний разбор</b>Готовность и план на день</div>
        <div class="feat"><div class="e">🥊</div><b>После тренировки</b>Что съесть и как восстановиться</div>
        <div class="feat"><div class="e">🍽</div><b>Фото еды</b>Ккал и БЖУ за секунды</div>
        <div class="feat"><div class="e">😴</div><b>Сон</b>Когда лечь, чтобы выспаться</div>
      </div>`;
  }

  function delta(v, base, unit, goodUp, dec = 0) {
    if (v == null || base == null) return '<span>нет нормы</span>';
    const d = v - base;
    if (Math.abs(d) < (dec ? .05 : .5)) return '<span>как обычно</span>';
    const good = goodUp ? d > 0 : d < 0;
    return `<span class="${good ? 'up' : 'down'}">${d > 0 ? '↑' : '↓'} ${num(Math.abs(d), dec)}${unit}</span> <span>к норме</span>`;
  }

  function todayView() {
    const d = S.data;
    if (!d.connected && !d.today.has_data) return header('Форма', 'Привет' + (d.user.name ? `, ${esc(d.user.name)}` : '')) + onboarding();
    const t = S.dayData || d.today, isToday = t.day === d.today.day;
    const r = t.recovery, s = t.sleep, c = t.cycle, b = t.base || {};
    const z = r ? r.zone : null;
    const nav = `<div class="day-nav"><button class="icon-btn" data-a="day" data-v="-1">${icon('left')}</button>
      ${isToday ? '' : `<button class="icon-btn" data-a="day" data-v="1">${icon('right')}</button>`}</div>`;
    let h = header(dateLine(t.day), isToday ? 'Сегодня' : 'День', nav);

    h += `<div class="card hero ${z ? 'z-' + z : ''}">
      <div class="ring-wrap">${ring(r ? r.score / 100 : 0, 212, 16)}<div class="ring-in">
        <div class="ring-val num">${r ? r.score : '—'}<small>${r ? '%' : ''}</small></div><div class="ring-lbl">Восстановление</div></div></div>
      ${r ? `<div class="verdict"><i></i>${ZONE[z].v}</div>` : `<div class="hero-sub">WHOOP ещё не посчитал восстановление${isToday ? ' — оно появится после пробуждения' : ''}</div>`}
      ${t.band ? `<div class="hero-sub">Ориентир нагрузки <b>${t.band[0]}–${t.band[1]}</b></div>` : ''}
      ${r && r.calibrating ? '<div class="hero-sub dim small">WHOOP ещё калибруется</div>' : ''}</div>`;

    h += `<div class="grid2">
      <div class="metric" style="--c:var(--green)"><div class="k"><b></b>ВСР · HRV</div><div class="v num">${r && r.hrv != null ? Math.round(r.hrv) : '—'}<small>мс</small></div><div class="d">${delta(r && r.hrv, b.hrv, ' мс', true)}</div></div>
      <div class="metric" style="--c:var(--red)"><div class="k"><b></b>Пульс покоя</div><div class="v num">${r && r.rhr != null ? r.rhr : '—'}<small>уд/мин</small></div><div class="d">${delta(r && r.rhr, b.rhr, '', false)}</div></div>
      <div class="metric tap" data-a="sleep-card" style="--c:var(--sleep)"><div class="k"><b></b>Сон</div><div class="v num">${s ? fmtH(s.hours) : '—'}<small>ч</small></div><div class="d">${s ? `нужно ${fmtH(s.need)} · ${s.performance != null ? s.performance + '%' : ''}` : 'нет данных'}</div></div>
      <div class="metric" style="--c:var(--strain)"><div class="k"><b></b>Нагрузка</div><div class="v num">${c && c.strain != null ? num(c.strain, 1) : '—'}<small>/ 21</small></div><div class="d">${c ? `${c.kcal} ккал за день` : 'нет данных'}</div></div>
    </div>`;

    // тренер
    const brief = t.brief;
    h += `<div class="card coach"><div class="card-h"><span class="coach-badge">${icon('spark')}</span><h3>Тренер</h3>
      ${isToday ? `<button class="more" data-a="brief">${S.briefing ? 'думаю…' : brief ? 'Обновить' : ''}</button>` : ''}</div>
      ${S.briefing ? '<div class="skeleton" style="width:92%"></div><div class="skeleton" style="width:80%"></div><div class="skeleton" style="width:86%"></div><div class="skeleton" style="width:55%"></div>'
        : brief ? `<div class="prose">${tgHtml(brief)}</div>` : (isToday ? `<div class="prose dim">Разбор придёт утром, как только WHOOP посчитает восстановление.</div>
        <div class="row-btns"><button class="btn" data-a="brief">${icon('spark')}Разобрать сейчас</button></div>` : '<div class="prose dim">Разбора за этот день нет.</div>')}
      ${isToday ? `<div class="row-btns"><button class="btn ghost" data-a="tab" data-v="coach">${icon('coach')}Спросить тренера</button></div>` : ''}</div>`;

    if (t.tips && t.tips.length) {
      const col = { good: 'var(--green)', warn: 'var(--yellow)', bad: 'var(--red)' };
      h += `<div class="card"><div class="card-h"><h3>Сигналы тела</h3></div><div class="tips">${t.tips.map(x => `<div class="tip" style="--c:${col[x.tone]}"><i></i><div>${esc(x.text)}</div></div>`).join('')}</div></div>`;
    }

    // нагрузка
    if (c) {
      const pct = v => `${Math.min(100, v / 21 * 100)}%`;
      h += `<div class="card"><div class="card-h"><h3>Нагрузка</h3><span class="more num">${b.strain != null ? 'обычно ' + num(b.strain, 1) : ''}</span></div>
        <div class="big-line"><span class="v num" style="color:var(--strain)">${num(c.strain, 1)}</span><span class="s">${t.band ? `цель ${t.band[0]}–${t.band[1]}` : 'из 21'}</span></div>
        <div class="gauge">${t.band ? `<div class="band" style="left:${pct(t.band[0])};width:calc(${pct(t.band[1])} - ${pct(t.band[0])})"></div>` : ''}<div class="fill" style="width:0" data-w="${pct(c.strain || 0)}"></div></div>
        <div class="gauge-scale"><span>0</span><span>7</span><span>14</span><span>21</span></div></div>`;
    }

    // сон
    if (s) {
      const tot = s.light + s.deep + s.rem + s.awake || 1, w = v => `${v / tot * 100}%`;
      h += `<div class="card" id="sleep"><div class="card-h"><h3>Сон</h3><span class="more">${esc(s.start)} → ${esc(s.end)}</span></div>
        <div class="big-line"><span class="v num" style="color:var(--sleep)">${hHuman(s.hours)}</span><span class="s">из ${hHuman(s.need)}</span></div>
        <div class="stages"><span style="width:${w(s.deep)};background:#5b4bff"></span><span style="width:${w(s.rem)};background:#a78bfa"></span><span style="width:${w(s.light)};background:#c4b5fd80"></span><span style="width:${w(s.awake)};background:var(--surface-3)"></span></div>
        <div class="legend"><span><i style="background:#5b4bff"></i>Глубокий ${fmtH(s.deep)}</span><span><i style="background:#a78bfa"></i>REM ${fmtH(s.rem)}</span><span><i style="background:#c4b5fd80"></i>Лёгкий ${fmtH(s.light)}</span><span><i style="background:var(--surface-3)"></i>Без сна ${fmtH(s.awake)}</span></div>
        <div class="grid2" style="margin:14px 0 0">
          <div><div class="dim small">Эффективность</div><b class="num">${s.efficiency != null ? s.efficiency + '%' : '—'}</b></div>
          <div><div class="dim small">Режим</div><b class="num">${s.consistency != null ? s.consistency + '%' : '—'}</b></div>
          <div><div class="dim small">Дыхание</div><b class="num">${s.resp != null ? num(s.resp, 1) + ' /мин' : '—'}</b></div>
          <div><div class="dim small">Недосып</div><b class="num">${s.debt ? hHuman(s.debt) : 'нет'}</b></div></div></div>`;
    }

    // тренировки
    if (t.workouts && t.workouts.length) {
      const zc = ['#6b7280', '#3d9bff', '#22e08a', '#ffc93c', '#ff9f43', '#ff4d5e'];
      h += `<div class="card"><div class="card-h"><h3>Тренировки</h3></div>${t.workouts.map(w => {
        const tz = (w.zones || []).reduce((a, b) => a + b, 0) || 1;
        return `<div class="wk"><div class="wk-ic">${/бокс|кик|единобор|ммa|борьба/i.test(w.sport) ? '🥊' : /бег/i.test(w.sport) ? '🏃' : /силов|пауэр/i.test(w.sport) ? '🏋️' : '⚡'}</div>
          <div class="grow"><div class="t">${esc(w.sport)}</div><div class="s">${esc(w.start)} · ${w.minutes || '—'} мин · ${w.kcal != null ? w.kcal + ' ккал' : ''} · пульс ${w.avg_hr || '—'}/${w.max_hr || '—'}</div>
          ${w.zones && w.zones.length ? `<div class="zones">${w.zones.map((m, i) => m ? `<span style="width:${m / tz * 100}%;background:${zc[i]}"></span>` : '').join('')}</div>` : ''}</div>
          <div class="val num">${w.strain != null ? num(w.strain, 1) : '—'}</div></div>`;
      }).join('')}</div>`;
    }

    // питание кратко
    const n = t.nutrition, e = t.eaten;
    if (n && isToday) {
      h += `<div class="card tap" data-a="tab" data-v="food"><div class="card-h"><h3>Питание</h3><span class="more">Открыть ›</span></div>
        <div class="macro"><div class="top-l"><span>Калории</span><span><b class="num">${e.kcal}</b> / ${n.kcal}</span></div><div class="bar"><span style="--c:var(--food);width:0" data-w="${Math.min(100, e.kcal / n.kcal * 100)}%"></span></div></div>
        <div class="macro" style="margin-top:10px"><div class="top-l"><span>Белок</span><span><b class="num">${e.protein}</b> / ${n.protein} г</span></div><div class="bar"><span style="--c:var(--green);width:0" data-w="${Math.min(100, e.protein / n.protein * 100)}%"></span></div></div></div>`;
    }
    if (isToday && d.synced) h += `<div class="hint" style="text-align:center">WHOOP обновлён ${new Date(d.synced).toLocaleTimeString('ru', { hour: '2-digit', minute: '2-digit' })} · <a data-a="sync" style="color:var(--accent)">обновить</a></div>`;
    return h;
  }

  // ---------- Тренды ----------
  function trendsView() {
    let h = header('Последние ' + S.range + ' дней', 'Тренды');
    h += `<div class="seg">${[7, 30, 90].map(n => `<button class="${S.range === n ? 'on' : ''}" data-a="range" data-v="${n}">${n} дней</button>`).join('')}</div>`;
    const rows = S.series[S.range] || (S.range === 30 ? S.data.series : null);
    if (!rows) return h + '<div class="card"><div class="skeleton"></div><div class="skeleton" style="width:70%"></div></div>';
    if (!rows.some(r => r.recovery != null || r.strain != null)) return h + '<div class="card dim">Данных пока нет — подключи WHOOP.</div>';
    const zc = r => r.zone === 'green' ? 'var(--green)' : r.zone === 'yellow' ? 'var(--yellow)' : 'var(--red)';
    const card = (title, value, unit, sub, chart) => `<div class="card"><div class="card-h"><h3>${title}</h3></div>
      <div class="chart-top"><span class="v num">${value}</span><span class="s">${unit}</span><span class="s" style="margin-left:auto">${sub}</span></div>${chart}</div>`;
    const greens = rows.filter(r => r.zone === 'green').length, reds = rows.filter(r => r.zone === 'red').length;
    h += card('Восстановление', num(avg(rows, 'recovery')), '% в среднем', `🟢 ${greens} · 🔴 ${reds}`, bars(rows, 'recovery', { max: 100, color: zc }));
    h += card('ВСР · HRV', num(avg(rows, 'hrv')), 'мс', 'чем выше, тем лучше', line(rows, 'hrv', 'var(--green)', avg(rows, 'hrv')));
    h += card('Пульс покоя', num(avg(rows, 'rhr')), 'уд/мин', 'чем ниже, тем лучше', line(rows, 'rhr', 'var(--red)', avg(rows, 'rhr')));
    h += card('Сон', fmtH(avg(rows, 'sleep')), 'ч в среднем', '— нужно', bars(rows, 'sleep', { color: 'var(--sleep)', ref: 'need' }));
    const wk = rows.reduce((a, r) => a + (r.workouts || 0), 0);
    h += card('Нагрузка', num(avg(rows, 'strain'), 1), 'в среднем', `${wk} трениров${wk % 10 === 1 && wk % 100 !== 11 ? 'ка' : [2, 3, 4].includes(wk % 10) && ![12, 13, 14].includes(wk % 100) ? 'ки' : 'ок'}`, bars(rows, 'strain', { max: 21, color: 'var(--strain)' }));
    return h;
  }
  async function loadSeries(n) {
    try { S.series[n] = (await api('/api/series', { n })).series; if (S.tab === 'trends') render(); } catch (e) { fail(e); }
  }

  // ---------- Питание ----------
  function foodView() {
    const t = S.data.today, n = t.nutrition, e = t.eaten;
    let h = header(dateLine(t.day), 'Питание');
    if (!n) {
      h += `<div class="card"><div class="prose">Добавь вес (и лучше рост и возраст) в профиле — посчитаю калории и БЖУ под твои тренировки.</div>
        <div class="row-btns"><button class="btn" data-a="profile">${icon('user')}Заполнить профиль</button></div></div>`;
    } else {
      const left = n.kcal - e.kcal;
      h += `<div class="card"><div class="food-hero"><div class="food-ring">${ring(e.kcal / n.kcal, 132, 12, 'var(--food)')}
        <div class="ring-in"><div class="v num">${Math.abs(left)}</div><div class="s">${left >= 0 ? 'ккал осталось' : 'ккал сверх'}</div></div></div>
        <div class="macros">${[['Белок', e.protein, n.protein, 'var(--green)'], ['Жиры', e.fat, n.fat, 'var(--yellow)'], ['Углеводы', e.carbs, n.carbs, 'var(--strain)']].map(([k, v, g, c]) =>
          `<div class="macro"><div class="top-l"><span>${k}</span><span><b class="num">${v}</b> / ${g} г</span></div><div class="bar"><span style="--c:${c};width:0" data-w="${Math.min(100, v / g * 100)}%"></span></div></div>`).join('')}</div></div>
        <div class="hint">Норма ${n.kcal} ккал — ${esc(n.how)}. Вода: ${num(n.water / 1000, 1)} л.${n.teen ? ' В твоём возрасте не урезаем калории: рост важнее.' : ''}</div></div>`;
    }
    h += `<div class="card"><div class="card-h"><h3>Что съел?</h3><button class="more" data-a="meal-new">+ вручную</button></div>
      <div class="ask"><textarea id="eat" rows="1" placeholder="Гречка с курицей, салат…">${esc(S.eatDraft || '')}</textarea>
      <button class="send" data-a="eat" ${S.eating ? 'disabled' : ''}>${S.eating ? '<span class="dots"><i></i><i></i><i></i></span>' : icon('send')}</button></div>
      <div class="hint">Или пришли боту фото тарелки — посчитаю сам.</div></div>`;
    h += `<div class="card"><div class="card-h"><h3>Сегодня</h3><span class="more num">${e.count ? e.kcal + ' ккал' : ''}</span></div>${t.meals.length ? t.meals.map(m => `
      <div class="meal"><div class="time num">${esc(m.time)}</div><div class="grow tap" data-a="meal-edit" data-v="${m.id}"><div class="t">${esc(m.title)}</div>
      <div class="s num">Б ${Math.round(m.protein)} · Ж ${Math.round(m.fat)} · У ${Math.round(m.carbs)}</div></div><div class="k num">${Math.round(m.kcal)}</div>
      <button class="del" data-a="meal-del" data-v="${m.id}">${icon('trash')}</button></div>`).join('') : '<div class="dim">Пока пусто.</div>'}</div>`;
    return h;
  }

  // ---------- Тренер ----------
  const SUGGEST = ['Готов ли я сегодня к спаррингу?', 'Что съесть перед тренировкой?', 'Почему упало восстановление?', 'Как улучшить сон?', 'План на неделю', 'Что съесть после тренировки?'];
  function coachView() {
    let h = header('Врач · тренер · питание', 'Тренер', S.history && S.history.length ? `<button class="icon-btn" data-a="clear" aria-label="Новый разговор">${icon('refresh')}</button>` : '');
    const hist = S.history;
    if (!hist) return h + '<div class="card"><div class="skeleton"></div><div class="skeleton" style="width:60%"></div></div>';
    h += '<div class="chat">';
    if (!hist.length) h += `<div class="empty-chat"><div class="big">🥊</div><p>Спроси что угодно про тренировки, сон, питание или восстановление — отвечу по твоим данным WHOOP.</p></div>`;
    hist.forEach(m => { h += m.role === 'user' ? `<div class="msg me">${esc(m.text)}</div>` : `<div class="msg coach prose">${md(m.text)}</div>`; });
    if (S.asking) h += `<div class="msg me">${esc(S.asking)}</div><div class="msg coach"><span class="dots"><i></i><i></i><i></i></span></div>`;
    h += '</div>';
    h += `<div class="composer"><div class="chips">${SUGGEST.map(s => `<button class="chip" data-a="suggest" data-v="${esc(s)}">${esc(s)}</button>`).join('')}</div>
      <div class="ask"><textarea id="q" rows="1" placeholder="Спроси тренера…">${esc(S.qDraft || '')}</textarea>
      <button class="send" data-a="ask" ${S.asking ? 'disabled' : ''}>${icon('send')}</button></div></div>`;
    return h;
  }
  async function loadHistory() {
    try { S.history = (await api('/api/history', {})).history; if (S.tab === 'coach') render(); } catch (e) { fail(e); }
  }
  async function ask(q) {
    q = (q || '').trim(); if (!q || S.asking) return;
    S.asking = q; S.qDraft = ''; haptic(); render();
    try { const r = await api('/api/ask', { text: q }); S.history = r.history; haptic('medium'); }
    catch (e) { fail(e); S.qDraft = q; }
    S.asking = false; if (S.tab === 'coach') render();
  }

  // ---------- окна ----------
  function sheet(html, mount) {
    const root = $('#sheet-root');
    root.innerHTML = `<div class="backdrop" data-a="close"></div><div class="sheet"><div class="grip"></div>${html}</div>`;
    if (tgw) { tgw.BackButton.show(); }
    if (mount) mount(root);
  }
  function closeSheet() { $('#sheet-root').innerHTML = ''; if (tgw) tgw.BackButton.hide(); }

  const GOALS = [['form', 'Форма и результат'], ['mass', 'Набор мышц'], ['dry', 'Рельеф'], ['weight', 'Держать вес']];
  function profileSheet() {
    const d = S.data, p = d.profile, w = d.body || {};
    const conn = d.connected
      ? `<div class="status-line" style="--c:var(--green)"><i></i><div class="grow"><b>WHOOP подключён</b><div class="dim small">${d.synced ? 'обновлено ' + new Date(d.synced).toLocaleString('ru', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : ''}</div></div><button class="btn ghost" data-a="sync" style="height:36px;padding:0 12px">${icon('refresh')}</button></div>`
      : `<div class="status-line" style="--c:var(--red)"><i></i><div class="grow"><b>WHOOP не подключён</b></div>${d.keys ? `<button class="btn" data-a="connect" style="height:36px;padding:0 14px">Подключить</button>` : ''}</div>`;
    const f = (k, label, ph, type = 'text', extra = '') => `<div class="field"><label>${label}</label><input name="${k}" type="${type}" inputmode="${type === 'number' ? 'decimal' : 'text'}" placeholder="${esc(ph)}" value="${esc(p[k] != null ? p[k] : '')}" ${extra}></div>`;
    sheet(`<h2>Профиль</h2>${conn}
      <form id="pf">
      <div class="row3">${f('age', 'Возраст', '14', 'number')}${f('height', 'Рост, см', w.height_meter ? Math.round(w.height_meter * 100) : '170', 'number')}${f('weight', 'Вес, кг', w.weight_kilogram ? num(w.weight_kilogram, 1) : '60', 'number')}</div>
      <div class="field"><label>Пол</label><div class="opts" data-group="sex">${[['m', 'Парень'], ['f', 'Девушка']].map(([k, t]) => `<button type="button" class="chip ${p.sex === k ? 'on' : ''}" data-a="opt" data-v="${k}">${t}</button>`).join('')}</div></div>
      <div class="field"><label>Цель</label><div class="opts" data-group="goal">${GOALS.map(([k, t]) => `<button type="button" class="chip ${(p.goal || 'form') === k ? 'on' : ''}" data-a="opt" data-v="${k}">${t}</button>`).join('')}</div></div>
      ${f('sport', 'Спорт', 'бокс, 8 месяцев')}
      ${f('schedule', 'Расписание тренировок', 'пн, ср, пт, сб — 2 часа вечером')}
      <div class="field"><label>Питание: что ешь и чего не ешь</label><textarea name="diet" placeholder="школьная столовая, не ем рыбу…">${esc(p.diet || '')}</textarea></div>
      <div class="field"><label>Важно знать тренеру</label><textarea name="notes" placeholder="травмы, соревнования, самочувствие…">${esc(p.notes || '')}</textarea></div>
      <button class="btn wide" type="submit">Сохранить</button></form>`, root => {
      $('#pf', root).addEventListener('submit', async ev => {
        ev.preventDefault();
        const fd = Object.fromEntries(new FormData(ev.target).entries());
        root.querySelectorAll('[data-group]').forEach(g => { const on = $('.chip.on', g); fd[g.dataset.group] = on ? on.dataset.v : ''; });
        try { const r = await api('/api/profile', fd); S.data.profile = r.profile; closeSheet(); haptic('medium'); toast('Сохранено'); await refresh(); }
        catch (e) { fail(e); }
      });
    });
  }
  function mealSheet(m) {
    m = m || { title: '', kcal: '', protein: '', fat: '', carbs: '' };
    const f = (k, label, type = 'number') => `<div class="field"><label>${label}</label><input name="${k}" type="${type}" inputmode="${type === 'number' ? 'decimal' : 'text'}" value="${esc(m[k] === '' ? '' : type === 'number' ? Math.round(m[k]) : m[k])}"></div>`;
    sheet(`<h2>${m.id ? 'Изменить' : 'Добавить еду'}</h2><form id="mf">${f('title', 'Что', 'text')}<div class="row2">${f('kcal', 'Ккал')}${f('protein', 'Белки, г')}${f('fat', 'Жиры, г')}${f('carbs', 'Углеводы, г')}</div>
      <button class="btn wide" type="submit">Сохранить</button></form>`, root => {
      $('#mf', root).addEventListener('submit', async ev => {
        ev.preventDefault();
        const fd = Object.fromEntries(new FormData(ev.target).entries());
        if (m.id) fd.id = m.id;
        try { const r = await api('/api/meal', fd); S.data.today = r.day; closeSheet(); haptic('medium'); render(); } catch (e) { fail(e); }
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
    S.eating = true; S.eatDraft = text; render();
    try { const r = await api('/api/meal/estimate', { text }); S.data.today = r.day; S.eatDraft = ''; haptic('medium'); if (r.comment) toast(r.comment); }
    catch (e) { fail(e); }
    S.eating = false; render();
  }
  async function brief() {
    if (S.briefing) return;
    S.briefing = true; render();
    try { const r = await api('/api/brief', {}); S.data.today.brief = r.brief; haptic('medium'); } catch (e) { fail(e); }
    S.briefing = false; render();
  }
  async function connect() {
    try { const r = await api('/api/connect', {}); if (tgw) tgw.openLink(r.url); else location.href = r.url; } catch (e) { fail(e); }
  }
  async function shiftDay(k) {
    const cur = parseDay((S.dayData || S.data.today).day);
    const nd = isoDay(new Date(cur.getTime() + k * 864e5));
    if (nd > S.data.today.day) return;
    haptic();
    if (nd === S.data.today.day) { S.dayData = null; render(); return; }
    try { S.dayData = (await api('/api/day', { d: nd })).day; render(); } catch (e) { fail(e); }
  }

  document.addEventListener('click', async ev => {
    const tab = ev.target.closest('[data-tab]');
    if (tab) return go(tab.dataset.tab);
    const el = ev.target.closest('[data-a]');
    if (!el) return;
    const a = el.dataset.a, v = el.dataset.v;
    if (a === 'profile') profileSheet();
    else if (a === 'close') closeSheet();
    else if (a === 'tab') go(v);
    else if (a === 'connect') connect();
    else if (a === 'brief') brief();
    else if (a === 'day') shiftDay(Number(v));
    else if (a === 'range') { S.range = Number(v); haptic(); render(); if (!S.series[S.range]) loadSeries(S.range); }
    else if (a === 'eat') eat();
    else if (a === 'ask') { const q = $('#q'); ask(q && q.value); }
    else if (a === 'suggest') ask(v);
    else if (a === 'clear') { try { S.history = (await api('/api/history/clear', {})).history; render(); } catch (e) { fail(e); } }
    else if (a === 'meal-new') mealSheet();
    else if (a === 'meal-edit') mealSheet(S.data.today.meals.find(m => String(m.id) === v));
    else if (a === 'meal-del') {
      haptic('medium');
      try { S.data.today = (await api('/api/meal/delete', { id: Number(v) })).day; render(); } catch (e) { fail(e); }
    } else if (a === 'opt') {
      el.parentElement.querySelectorAll('.chip').forEach(c => c.classList.toggle('on', c === el && !c.classList.contains('on')));
      haptic();
    } else if (a === 'sleep-card') { const s = $('#sleep'); if (s) s.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
    else if (a === 'sync') {
      toast('Обновляю WHOOP…');
      try { await api('/api/sync', {}); await refresh(); toast('Данные обновлены'); } catch (e) { fail(e); }
    } else if (a === 'copy') {
      try { await navigator.clipboard.writeText(v); toast('Скопировано'); } catch (e) { toast('Выдели и скопируй адрес'); }
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
      if (ev.target.id === 'q') ask(ev.target.value); else eat();
    }
  });

  // ---------- старт ----------
  function theme() {
    const light = tgw && tgw.colorScheme === 'light';
    document.body.classList.toggle('light', !!light);
    const bg = getComputedStyle(document.body).getPropertyValue('--bg').trim();
    try { tgw.setHeaderColor(bg); tgw.setBackgroundColor(bg); tgw.setBottomBarColor && tgw.setBottomBarColor(bg); } catch (e) { /* старый Telegram */ }
  }
  async function start() {
    if (tgw) {
      tgw.ready(); tgw.expand();
      try { tgw.disableVerticalSwipes && tgw.disableVerticalSwipes(); } catch (e) { /* нет */ }
      tgw.onEvent('themeChanged', theme);
      tgw.BackButton.onClick(closeSheet);
    }
    theme();
    const p = new URLSearchParams(location.search).get('p') || '';
    const t = p.replace(/^\//, '');
    if (['trends', 'food', 'coach'].includes(t)) S.tab = t;
    renderTabs();
    try { S.data = await api('/api/all'); }
    catch (e) { view.innerHTML = `<div class="onb"><div class="logo">${icon('today')}</div><h1>Форма</h1><p>${esc(e.message)}</p></div>`; return; }
    render();
    if (S.tab === 'trends' && !S.series[S.range]) loadSeries(S.range);
    if (S.tab === 'coach') loadHistory();
    if (t === 'profile') profileSheet();
    // данные обновляются сами, пока приложение открыто
    setInterval(async () => {
      if (document.hidden || $('#sheet-root').innerHTML || S.asking || S.eating || S.briefing || S.dayData) return;
      if (document.activeElement && document.activeElement.tagName === 'TEXTAREA') return;
      try { S.data = await api('/api/all'); if (S.tab !== 'coach') render(); } catch (e) { /* тихо */ }
    }, 120000);
  }
  start();
})();
