/* Форма — приложение тренера внутри Telegram. Общий вид — kit.css/kit.js, здесь только своё. Без сборки. */
(() => {
  const { tg, esc, api, toast, haptic, sheet, busy } = Kit;
  const $ = (s, el = document) => el.querySelector(s);
  const view = $('#view');
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
  const sportIcon = s => /бокс|кик|единобор|мма|борьб|джиу/i.test(s) ? '🥊' : /бег|ходьб|поход/i.test(s) ? '🏃' : /силов|пауэр|функц/i.test(s) ? '🏋️' : '⚡';

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
  function dial(p, color, value, label, sub, act, size = 100) {
    const w = size > 100 ? 9 : 8, r = 50 - w / 2 - 1, c = 2 * Math.PI * r, off = c * (1 - Math.max(0, Math.min(1, p || 0)));
    return `<button class="dial ${size > 100 ? 'big' : ''}" data-a="detail" data-v="${act}" style="--c:${color}" aria-label="${esc(label)}">
      <span class="dial-ring"><svg viewBox="0 0 100 100" aria-hidden="true"><circle class="trk" cx="50" cy="50" r="${r}" stroke-width="${w}"/>
        <circle class="arc" cx="50" cy="50" r="${r}" stroke-width="${w}" stroke-dasharray="${c.toFixed(2)}" stroke-dashoffset="${c.toFixed(2)}" data-off="${off.toFixed(2)}"/></svg>
        <span class="dial-v">${value}</span></span>
      <span class="dial-k">${esc(label)}</span><span class="dial-s">${sub}</span></button>`;
  }
  function ring(p, color, inner, size = 120) {
    const w = 11, r = 50 - w / 2, c = 2 * Math.PI * r, off = c * (1 - Math.max(0, Math.min(1, p || 0)));
    return `<div class="kring" style="--c:${color};--size:${size}px"><svg viewBox="0 0 100 100" aria-hidden="true">
      <circle class="trk" cx="50" cy="50" r="${r}" stroke-width="${w}"/><circle class="arc" cx="50" cy="50" r="${r}" stroke-width="${w}"
      stroke-dasharray="${c.toFixed(2)}" stroke-dashoffset="${c.toFixed(2)}" data-off="${off.toFixed(2)}"/></svg><div class="kring-in">${inner}</div></div>`;
  }
  const bar = (p, color) => `<div class="bar"><i style="width:0;background:${color}" data-w="${p}%"></i></div>`;
  function animate() {
    requestAnimationFrame(() => requestAnimationFrame(() => {
      document.querySelectorAll('.arc[data-off]').forEach(el => { el.style.strokeDashoffset = el.dataset.off; });
      document.querySelectorAll('[data-w]').forEach(el => { el.style.width = el.dataset.w; });
    }));
  }

  // ---------- графики (SVG без библиотек) ----------
  const chartW = () => Math.max(200, Math.min(($('#app') || view).clientWidth || 358, 560) - 38);
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
      s += `<rect x="${x.toFixed(1)}" y="${(CH - h).toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" rx="${Math.min(3, bw / 2).toFixed(1)}" fill="${typeof color === 'function' ? color(r) : color}"><title>${r.day}: ${v}</title></rect>`;
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
    s += `<path d="${d} L${P[P.length - 1][0].toFixed(1)},${CH} L${P[0][0].toFixed(1)},${CH} Z" fill="url(#${id})"/>`;
    s += `<path d="${d}" fill="none" stroke="${color}" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round"/>`;
    const last = P[P.length - 1];
    s += `<circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="4" fill="${color}" stroke="var(--card)" stroke-width="2"/>`;
    return svg(W, s, rows);
  }
  const avg = (rows, k) => { const v = rows.map(r => r[k]).filter(x => x != null); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
  const skel = (n = 3) => Array.from({ length: n }, (_, i) => `<div class="sk line" style="width:${[92, 78, 86, 60][i % 4]}%"></div>`).join('');

  // ---------- каркас ----------
  const TABS = [{ id: 'today', icon: '⚡', label: 'Сегодня' }, { id: 'trends', icon: '📈', label: 'Тренды' },
    { id: 'food', icon: '🍽', label: 'Питание' }, { id: 'coach', icon: '💬', label: 'Тренер' }];
  let tabs;
  function go(tab) {
    if (S.tab === tab) { window.scrollTo({ top: 0, behavior: 'smooth' }); return; }
    S.tab = tab; tabs.set(tab); render(true); window.scrollTo(0, 0);
    if (tab === 'trends' && !S.series[S.range]) loadSeries(S.range);
    if (tab === 'trends' && S.progress === undefined) loadProgress();
    if (tab === 'coach' && !S.history) loadHistory();
  }
  function render(anim) {
    if (!S.data) return;
    const html = { today: todayView, trends: trendsView, food: foodView, coach: coachView }[S.tab]();
    view.removeAttribute('aria-busy');
    view.innerHTML = `<div class="${anim ? 'enter' : ''}">${html}</div>`;
    animate();
    if (S.tab === 'coach' && anim !== false) window.scrollTo(0, document.body.scrollHeight);
  }
  function hero(title, sub, end = '') {
    return `<header class="hero"><div class="logo">⚡</div><div class="titles"><h1>${title}</h1><div class="sub">${sub}</div></div>
      <div class="end">${end}<button class="btn icon tonal" data-a="profile" aria-label="Профиль">👤</button></div></header>`;
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
      return `<section class="card feature rise"><div class="feat-ic">🔌</div><h2 class="title-lg">WHOOP не подключён</h2>
        <p class="muted">Подключи браслет — каждое утро будет план: как тренироваться, есть и спать.</p>
        <button class="btn primary" data-a="connect">Подключить WHOOP</button></section>`;
    }
    return `<section class="card feature rise"><div class="feat-ic">🔌</div><h2 class="title-lg">Подключим WHOOP</h2>
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
    return `<section class="card rise">${head('💧 Вода', `<span class="num"><b>${liters(w.drank)}</b> / ${liters(w.target)}</span>`)}
      ${bar(pct(w.drank, w.target), 'var(--water)')}
      ${w.note ? `<p class="small muted">${esc(w.note)}</p>` : ''}
      <div class="btns">${q.map(([ml, k]) => `<button class="btn tonal water-b" data-a="water" data-v="${ml}"><b class="num">+${ml}</b><span>${k}</span></button>`).join('')}</div>
      ${full && w.log.length ? `<div class="logs">${w.log.map(x => `<span class="pill num">${esc(x.time)} · ${x.ml} мл<button data-a="water-del" data-v="${x.id}" aria-label="Удалить">✕</button></span>`).join('')}</div>` : ''}
      ${full ? `<p class="small muted">Норма: ${esc(w.why)}. Боту можно писать «выпил 500 мл».</p>` : ''}</section>`;
  }

  function foodMini(t) {
    const n = t.nutrition, e = t.eaten;
    const right = n ? `<span class="num"><b>${big(e.kcal)}</b> / ${big(n.kcal)} ккал</span>` : `<span class="num"><b>${big(e.kcal)}</b> ккал</span>`;
    return `<section class="card rise tap" data-a="tab" data-v="food" role="button">${head('🍽 Еда', right + '<span class="chev"></span>')}
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

  function todayView() {
    const d = S.data, t = S.dayData || d.today, isToday = t.day === d.today.day;
    const r = t.recovery, s = t.sleep, c = t.cycle, b = t.base || {};
    const synced = d.connected && d.synced ? `WHOOP ${clock(d.synced)}` : d.connected ? 'WHOOP' : 'WHOOP не подключён';
    let h = hero(dateWord(t.day), `${dateLine(t.day)} · ${synced}`);
    if (!d.connected && !d.today.has_data) {
      return h + connectCard() + waterCard(d.today.water) + foodMini(d.today) + features() + Kit.supportRow('/api/support') + chatRow();
    }
    const z = r ? r.zone : null;
    const recC = z ? C[z] : 'var(--hint)';
    const sleepP = s ? (s.performance != null ? s.performance / 100 : s.need ? s.hours / s.need : 0) : 0;
    h += `<section class="card dials-card rise">
      <div class="day-nav"><button class="btn icon" data-a="day" data-v="-1" aria-label="Предыдущий день">‹</button>
        <span>${isToday ? 'Сегодня' : esc(dateWord(t.day))}, ${dateLine(t.day)}</span>
        <button class="btn icon" data-a="day" data-v="1" aria-label="Следующий день" ${isToday ? 'disabled' : ''}>›</button></div>
      <div class="dials">
        ${dial(sleepP, 'var(--sleep)', s ? `<b class="num">${hm(s.hours)}</b>` : '<b>—</b>', 'Сон', s && s.performance != null ? `${s.performance}%` : s ? `из ${hm(s.need)}` : 'нет данных', 'sleep')}
        ${dial(r ? r.score / 100 : 0, recC, r ? `<b class="num">${r.score}<small>%</small></b>` : '<b>—</b>', 'Восстановление', r ? ZONE[z] : 'ещё не посчитано', 'rec', 130)}
        ${dial(c && c.strain != null ? c.strain / 21 : 0, 'var(--strain)', c && c.strain != null ? `<b class="num">${num(c.strain, 1)}</b>` : '<b>—</b>', 'Нагрузка', t.band ? `цель ${t.band[0]}–${t.band[1]}` : 'из 21', 'strain')}
      </div></section>`;

    // совет на день + главное действие
    const adv = advice(t);
    h += `<section class="card feature rise"><div class="label-in">✨ Совет на день</div>
      ${S.briefing ? skel(2) : adv ? `<div class="prose advice">${adv}</div>`
        : `<p class="muted">${!isToday ? 'Разбора за этот день нет.' : d.morning === 'off' ? 'Утренний разбор выключен — время в профиле.' : `Разбор придёт утром, с ${esc(d.morning || '07:00')}, как только WHOOP посчитает сон.`}</p>`}
      <div class="chips">${t.band ? `<span class="pill num">Нагрузка ${t.band[0]}–${t.band[1]}</span>` : ''}${isToday && t.bedtime ? `<span class="pill num">🌙 Отбой ${esc(t.bedtime.bed)}</span>` : ''}</div>
      ${isToday ? `<button class="btn primary" data-a="tab" data-v="coach">💬 Спросить тренера</button>
        <div class="btns">${t.brief || (t.tips || []).length ? '<button class="btn sm ghost" data-a="detail" data-v="rec">Весь разбор</button>' : ''}${!t.brief && !S.briefing ? '<button class="btn sm ghost" data-a="brief">Разобрать сейчас</button>' : ''}</div>` : ''}</section>`;

    h += `<div class="trio rise">
      <button class="stat tap" data-a="explain" data-v="hrv"><div class="k">ВСР, мс <i class="qm">?</i></div><div class="v num">${r && r.hrv != null ? Math.round(r.hrv) : '—'}</div><div class="d">${delta(r && r.hrv, b.hrv, true)}</div></button>
      <button class="stat tap" data-a="explain" data-v="rhr"><div class="k">Пульс <i class="qm">?</i></div><div class="v num">${r && r.rhr != null ? r.rhr : '—'}</div><div class="d">${delta(r && r.rhr, b.rhr, false)}</div></button>
      <button class="stat tap" data-a="explain" data-v="kcal"><div class="k">Ккал <i class="qm">?</i></div><div class="v num">${c ? big(c.kcal) : '—'}</div><div class="d"><span class="muted">траты</span></div></button></div>
      <p class="hint-line">Нажми на любую цифру или кольцо — объясню, что это значит</p>
      <button class="card summary-entry rise tap" data-a="summary" data-v="day"><span class="se-ic">📊</span><span class="se-main"><b>Сводка и советы</b>
        <span class="muted">за день, неделю, месяц и всё время с WHOOP</span></span><span class="chev"></span></button>`;

    if (isToday) h += waterCard(t.water) + foodMini(t);
    if ((t.workouts || []).length) h += `<div class="label">Тренировки</div><div class="list rise">${t.workouts.map(workoutRow).join('')}</div>`;
    if (isToday && d.connected) h += `<p class="foot">Данные WHOOP — ${d.synced ? clock(d.synced) : 'ещё не загружены'} · <button class="link" data-a="sync">Обновить</button></p>`;
    if (isToday) h += Kit.supportRow('/api/support') + chatRow();
    return h;
  }
  const workoutRow = w => `<button class="row" data-a="detail" data-v="strain"><span class="ic">${sportIcon(w.sport)}</span><div class="main"><div class="title">${esc(w.sport)}</div>
    <div class="meta num">${esc(w.start)} · ${w.minutes || '—'} мин · пульс ${w.avg_hr || '—'}/${w.max_hr || '—'}</div></div>
    <div class="end num strain-v">${w.strain != null ? num(w.strain, 1) : '—'}</div></button>`;
  // «💬 Перейти в чат» (внизу «Сегодня» и в профиле): режим «чат» — бот присылает главное меню, приложение закрывается
  function chatRow() {
    const m = S.data && S.data.mode;
    return `<div class="list mode-list"><button class="row tap" data-a="tochat"><span class="ic">💬</span><span class="main"><div class="title">Перейти в чат</div>
      <div class="meta">${m === 'chat' ? 'режим чата уже включён' : 'бот перейдёт в режим чата: всё кнопками там'}</div></span><span class="end chev"></span></button></div>`;
  }
  function features() {
    const row = (ic, t, s) => `<div class="row"><span class="ic">${ic}</span><div class="main"><div class="title">${t}</div><div class="meta">${s}</div></div></div>`;
    return `<div class="label">Что умеет Форма</div><div class="list rise">${row('☀️', 'Утренний план', 'Готовность, тренировка, отбой')}
      ${row('🥊', 'После тренировки', 'Что съесть и как восстановиться')}${row('📷', 'Фото еды', 'Ккал и БЖУ за секунды')}${row('🎥', 'Видео упражнения', 'Разбор техники')}</div>`;
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
    const norm = (v, base, unit, goodUp) => `<div class="grid2"><div class="stat"><div class="k">Сейчас</div><div class="v num">${v == null ? '—' : Math.round(v)}<small>${unit}</small></div></div>
      <div class="stat"><div class="k">Твоя норма</div><div class="v num">${base == null ? '—' : Math.round(base)}<small>${unit}</small></div><div class="d">${delta(v, base, goodUp)}</div></div></div>`;
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
    }
    sheet(h);
    animate();
  }

  // подробности по кольцам — в листе снизу
  function detail(kind) {
    const d = S.data, t = S.dayData || d.today, r = t.recovery, s = t.sleep, c = t.cycle, b = t.base || {};
    haptic('tap');
    let h = '';
    const about = ABOUT[kind] ? `<details class="about"><summary>${ABOUT[kind].t}</summary>${ABOUT[kind].p.map(x => `<p>${esc(x)}</p>`).join('')}</details>` : '';
    if (kind === 'rec') {
      h = `<h2>Восстановление ${r ? `<span class="num" style="color:${C[r.zone]}">${r.score}%</span>` : ''}</h2>${about}
        <div class="grid2"><div class="stat"><div class="k">ВСР, мс</div><div class="v num">${r && r.hrv != null ? Math.round(r.hrv) : '—'}</div><div class="d">норма ${b.hrv != null ? Math.round(b.hrv) : '—'}</div></div>
        <div class="stat"><div class="k">Пульс</div><div class="v num">${r && r.rhr != null ? r.rhr : '—'}<small>уд/мин</small></div><div class="d">норма ${b.rhr != null ? Math.round(b.rhr) : '—'}</div></div></div>
        ${r && r.calibrating ? '<div class="note"><span>⏳</span><span>WHOOP ещё калибруется — первые недели цифры неточные.</span></div>' : ''}
        ${t.brief ? `<div class="label">Разбор тренера</div><div class="card prose">${tgHtml(t.brief)}</div>` : ''}
        ${(t.tips || []).length ? `<div class="label">Сигналы тела</div><div class="card tips">${t.tips.map(x => `<div class="tip ${x.tone}"><i></i><p>${esc(x.text)}</p></div>`).join('')}</div>` : ''}
        ${t.day === d.today.day && t.brief ? '<button class="btn tonal" data-a="brief">Обновить разбор</button>' : ''}`;
    } else if (kind === 'sleep') {
      if (!s) h = '<h2>Сон</h2><div class="empty"><span class="e">😴</span>Сон за эту ночь ещё не посчитан.</div>';
      else {
        const tot = s.light + s.deep + s.rem + s.awake || 1;
        const st = [['Глубокий', s.deep, 'var(--sleep)'], ['REM', s.rem, 'color-mix(in srgb, var(--sleep) 62%, var(--card))'], ['Лёгкий', s.light, 'color-mix(in srgb, var(--sleep) 32%, var(--card))'], ['Без сна', s.awake, 'var(--field)']];
        h = `<h2>Сон <span class="num" style="color:var(--sleep)">${hHuman(s.hours)}</span></h2>${about}
          <p class="muted num">${esc(s.start)} → ${esc(s.end)} · нужно ${hHuman(s.need)}</p>
          <div class="stages">${st.map(([, v, col]) => v ? `<span style="width:${v / tot * 100}%;background:${col}"></span>` : '').join('')}</div>
          <div class="legend">${st.map(([k, v, col]) => `<span><i style="background:${col}"></i>${k}<b class="num">${hm(v)}</b></span>`).join('')}</div>
          <div class="grid2">${[['Выполнение', s.performance != null ? s.performance + '%' : '—'], ['Эффективность', s.efficiency != null ? s.efficiency + '%' : '—'], ['Режим', s.consistency != null ? s.consistency + '%' : '—'], ['Недосып', s.debt ? hm(s.debt) : 'нет']]
            .map(([k, v]) => `<div class="stat"><div class="k">${k}</div><div class="v sm num">${v}</div></div>`).join('')}</div>
          ${t.bedtime && t.day === d.today.day ? `<div class="note"><span>🌙</span><span>Отбой сегодня до <b class="num">${esc(t.bedtime.bed)}</b> — подъём в ${esc(t.bedtime.wake)}</span></div>` : ''}`;
      }
    } else {
      const p = v => `${pct(v, 21)}%`;
      h = `<h2>Нагрузка ${c && c.strain != null ? `<span class="num" style="color:var(--strain)">${num(c.strain, 1)}</span>` : ''}</h2>${about}
        <p class="muted num">${t.band ? `Ориентир на сегодня ${t.band[0]}–${t.band[1]}` : 'Шкала 0–21'}${b.strain != null ? ` · обычно ${num(b.strain, 1)}` : ''}</p>
        ${c ? `<div class="gauge">${t.band ? `<div class="band" style="left:${p(t.band[0])};width:calc(${p(t.band[1])} - ${p(t.band[0])})"></div>` : ''}<div class="fill" style="width:${p(c.strain || 0)}"></div></div>
        <div class="scale num"><span>0</span><span>7</span><span>14</span><span>21</span></div>` : ''}
        ${(t.workouts || []).length ? `<div class="label">Тренировки</div><div class="list">${t.workouts.map(workoutRow).join('')}</div>` : '<div class="empty"><span class="e">🥊</span>Тренировок за день нет.</div>'}
        ${c ? `<p class="muted small num">Траты за день ${big(c.kcal)} ккал · пульс ср. ${c.avg_hr || '—'}, макс. ${c.max_hr || '—'}</p>` : ''}`;
    }
    const el = sheet(h);
    el.addEventListener('click', ev => { const a = ev.target.closest('[data-a="brief"]'); if (a) { el.close(); brief(); } });
    animate();
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
        <p class="small muted num">🟢 ${x.green} · 🟡 ${x.yellow} · 🔴 ${x.red} ${plural(x.days, 'день', 'дня', 'дней')} · отбой в среднем ${x.bed != null ? esc(clockH(x.bed)) : '—'}${x.bed_spread != null ? ` ±${num(x.bed_spread * 60)} мин` : ''}</p>`;
    }
    h += `<div class="label">✨ Итог и советы</div>`;
    if (z.text) h += `<div class="card prose">${md(z.text)}</div><button class="btn sm ghost" data-a="sum-make" data-v="${per}">Обновить советы</button>`;
    else if (z.making) h += `<div class="card">${skel(3)}<p class="small muted">Разбираю данные — 20–40 секунд…</p></div>`;
    else h += `<button class="btn primary" data-a="sum-make" data-v="${per}">Получить советы</button>`;
    return h;
  }
  // отбой хранится в часах от полудня (23:30 -> 11,5)
  const clockH = h => { const m = Math.round((((h + 12) % 24) + 24) % 24 * 60); return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`; };
  let sumEl = null, sumPer = 'day';
  function sumHtml() {
    return `<h2>📊 Сводка и советы</h2><div class="seg" role="tablist">${PERIODS.map(([k, l]) => `<button class="${sumPer === k ? 'on' : ''}" data-a="sum-per" data-v="${k}" role="tab">${l}</button>`).join('')}</div>
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

  // ---------- Тренды ----------
  function trendsView() {
    let h = hero('Тренды', `Последние ${S.range} дней`);
    h += `<button class="card summary-entry rise tap" data-a="summary" data-v="week"><span class="se-ic">📊</span><span class="se-main"><b>Сводка и советы</b>
      <span class="muted">день · неделя · месяц · всё время</span></span><span class="chev"></span></button>`;
    h += `<div class="seg" role="tablist">${[7, 30, 90].map(n => `<button class="${S.range === n ? 'on' : ''}" data-a="range" data-v="${n}" role="tab">${n} дней</button>`).join('')}</div>`;
    const pr = S.progress;
    h += `<section class="card feature rise">${head('✨ Разбор прогресса', pr && !S.progressing ? '<button class="link" data-a="progress">Обновить</button>' : '')}
      ${S.progressing ? skel(3) + '<p class="small muted">Разбираю 2 месяца данных — около минуты…</p>'
        : pr ? `<p class="small muted">от ${dateLine(pr.day)}</p><div class="prose ${pr.text.length > 320 ? 'clamp' : ''}" id="prog">${tgHtml(pr.text)}</div>${pr.text.length > 320 ? '<button class="btn sm ghost" data-a="more">Читать целиком</button>' : ''}`
        : `<p class="muted">Тренды, сон, нагрузка, питание и план на 2 недели — по всем данным WHOOP.</p>
           <button class="btn primary" data-a="progress">Разобрать прогресс</button>`}</section>`;
    const rows = S.series[S.range] || (S.range === 30 ? S.data.series : null);
    if (!rows) return h + [1, 2, 3].map(() => '<section class="card"><div class="sk line" style="width:40%"></div><div class="sk" style="height:130px;margin-top:14px"></div></section>').join('');
    if (!rows.some(r => r.recovery != null || r.strain != null || r.sleep != null)) {
      return h + '<div class="empty"><span class="e">📈</span><b>Данных пока нет</b>Подключи WHOOP — графики появятся после первой ночи с браслетом.</div>';
    }
    const zc = r => C[r.zone] || 'var(--hint)';
    const card = (title, value, unit, side, chart) => `<section class="card rise">${head(title, side)}
      <div class="big-line"><span class="v num">${value}</span><span class="u">${unit}</span></div>${chart}</section>`;
    const greens = rows.filter(r => r.zone === 'green').length, reds = rows.filter(r => r.zone === 'red').length;
    h += card('Восстановление', num(avg(rows, 'recovery')), '% в среднем', `<span class="num"><i class="dot" style="background:var(--rec-green)"></i>${greens} <i class="dot" style="background:var(--rec-red)"></i>${reds}</span>`, bars(rows, 'recovery', { max: 100, color: zc }));
    h += card('ВСР', num(avg(rows, 'hrv')), 'мс', 'выше — лучше', line(rows, 'hrv', 'var(--rec-green)', avg(rows, 'hrv')));
    h += card('Пульс покоя', num(avg(rows, 'rhr')), 'уд/мин', 'ниже — лучше', line(rows, 'rhr', 'var(--rec-red)', avg(rows, 'rhr')));
    h += card('Сон', hm(avg(rows, 'sleep')), 'в среднем', '<i class="tick"></i>нужно', bars(rows, 'sleep', { color: 'var(--sleep)', ref: 'need' }));
    const wk = rows.reduce((a, r) => a + (r.workouts || 0), 0);
    h += card('Нагрузка', num(avg(rows, 'strain'), 1), 'в среднем', `<span class="num">${wk} ${plural(wk, 'тренировка', 'тренировки', 'тренировок')}</span>`, bars(rows, 'strain', { max: 21, color: 'var(--strain)' }));
    return h;
  }
  async function loadSeries(n) {
    try { S.series[n] = (await api('/api/series', { n })).series; if (S.tab === 'trends') render(false); } catch (e) { fail(e); }
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
      : '<div class="empty rise"><span class="e">🍽</span><b>Пока пусто</b>Сфотографируй тарелку или напиши, что съел.</div>';
    h += `<button class="btn ghost" data-a="meal-new">＋ Добавить вручную</button>`;
    h += waterCard(t.water, true);
    return h;
  }

  // ---------- Тренер ----------
  const SUGGEST = ['Готов ли я к спаррингу?', 'Что съесть перед тренировкой?', 'Почему упало восстановление?', 'Как улучшить сон?', 'План на неделю'];
  function coachView() {
    let h = hero('Тренер', 'Врач · тренер · питание', S.history && S.history.length ? '<button class="btn icon" data-a="clear" aria-label="Новый разговор">🧹</button>' : '');
    const hist = S.history;
    if (!hist) return h + '<div class="chat"><div class="sk msg-sk me"></div><div class="sk msg-sk"></div><div class="sk msg-sk me short"></div></div>';
    h += '<div class="chat">';
    if (!hist.length && !S.asking) h += '<div class="empty"><span class="e">💬</span><b>Спроси что угодно</b>Про тренировки, сон, питание и восстановление — отвечу по твоим данным WHOOP. Можно приложить скрин тренировки.</div>';
    hist.forEach(m => { h += m.role === 'user' ? `<div class="msg me">${esc(m.text)}</div>` : `<div class="msg them prose">${md(m.text)}</div>`; });
    if (S.asking) h += `<div class="msg me">${esc(S.asking)}</div><div class="msg them"><span class="dots"><i></i><i></i><i></i></span></div>`;
    h += '</div>';
    h += `<div class="composer"><div class="chips">${SUGGEST.map(s => `<button class="chip" data-a="suggest" data-v="${esc(s)}">${esc(s)}</button>`).join('')}</div>
      <div class="ask"><button class="send alt" data-a="photo" data-v="ask" aria-label="Фото" ${S.asking ? 'disabled' : ''}>📷</button>
      <textarea id="q" rows="1" placeholder="Спроси тренера…">${esc(S.qDraft || '')}</textarea>
      <button class="send" data-a="ask" aria-label="Отправить" ${S.asking ? 'disabled' : ''}>↑</button></div></div>`;
    return h;
  }
  async function loadHistory() {
    try { S.history = (await api('/api/history', {})).history; if (S.tab === 'coach') render(); } catch (e) { fail(e); }
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
  async function shiftDay(k) {
    const cur = parseDay((S.dayData || S.data.today).day);
    const nd = isoDay(new Date(cur.getTime() + k * 864e5));
    if (nd > S.data.today.day) return;
    haptic('select');
    if (nd === S.data.today.day) { S.dayData = null; render(true); return; }
    try { S.dayData = (await api('/api/day', { d: nd })).day; render(true); } catch (e) { fail(e); }
  }

  document.addEventListener('click', async ev => {
    const el = ev.target.closest('[data-a]');
    if (!el || el.disabled) return;
    const a = el.dataset.a, v = el.dataset.v;
    if (a === 'profile') profileSheet();
    else if (a === 'tab') { const t = TABS.find(x => x.id === v); if (t) { haptic('select'); go(v); } }
    else if (a === 'connect') connect();
    else if (a === 'brief') brief();
    else if (a === 'detail') detail(v);
    else if (a === 'explain') explain(v);
    else if (a === 'summary') summarySheet(v);
    else if (a === 'progress') loadProgress(true);
    else if (a === 'more') progressSheet();
    else if (a === 'day') shiftDay(Number(v));
    else if (a === 'range') { if (S.range === Number(v)) return; S.range = Number(v); haptic('select'); render(false); if (!S.series[S.range]) loadSeries(S.range); }
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
    if (['trends', 'food', 'coach'].includes(p)) S.tab = p;
    tabs = Kit.tabbar($('#tabs'), TABS, S.tab, go);
    try { S.data = await api('/api/all'); } catch (e) {
      view.removeAttribute('aria-busy');
      view.innerHTML = `${'<header class="hero"><div class="logo">⚡</div><div class="titles"><h1>Форма</h1><div class="sub">Тренер по данным WHOOP</div></div></header>'}
        <div class="card"><div class="empty"><span class="e">⚠️</span><b>Не загрузилось</b>${esc(e.message)}</div><button class="btn primary" onclick="location.reload()">Повторить</button></div>`;
      return;
    }
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
      try { S.data = await api('/api/all'); if (S.tab !== 'coach') render(false); } catch (e) { /* тихо */ }
    }, 120000);
  }
  start();
})();
