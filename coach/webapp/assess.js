/* assess — оценки недели и месяца (как Weekly / Monthly Performance Assessment WHOOP) и календарь восстановления.
   Сервер: coach/f_assess.py (/api/assess?period=week|month, /api/calendar?month=YYYY-MM). Подключение: window.FormaModules (см. начало app.js). */
(window.FormaModules = window.FormaModules || []).push(F => {
  const { esc, num, hm, plural } = F;
  const IC = {
    cal: F.svgi('<rect x="3.5" y="5" width="17" height="15.5" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/>'),
    week: F.svgi('<path d="M4 19V9M9 19V5M14 19v-7M19 19V8"/>'),
    month: F.svgi('<path d="M3 17l5-5 4 3 8-8"/><path d="M15 7h5v5"/>'),
    prev: F.svgi('<path d="M15 5l-7 7 7 7"/>'),
    next: F.svgi('<path d="M9 5l7 7-7 7"/>'),
  };
  // цвета состояний (наш расчёт на сервере: f_assess.py)
  const STATE_C = { optimal: 'var(--teal)', over: 'var(--rec-red)', restore: 'var(--rec-blue)' };
  const SLEEP_C = { good: 'var(--teal)', ok: 'var(--rec-yellow)', bad: 'var(--food)' };
  const ZCOL = ['var(--z1)', 'var(--z2)', 'var(--z3)', 'var(--z4)', 'var(--z5)'];
  const M = { as: {}, err: {}, loading: {}, cal: {}, calErr: null, calMonth: null };
  const zc = z => F.C[z] || 'rgba(255,255,255,.14)';
  const dl = s => { const d = F.parseDay(s); return `${d.getDate()} ${F.MON_S[d.getMonth()]}`; };

  // ---------- загрузка ----------
  async function loadAs(per) {
    if (M.loading[per]) return;
    M.loading[per] = true; M.err[per] = null;
    try { M.as[per] = await F.api(`/api/assess?period=${per}`); } catch (e) { M.err[per] = e.message || String(e); }
    M.loading[per] = false;
    if (F.S.screen === 'assess') F.render(false);
  }
  // месяц календаря: из кэша сразу, иначе загрузить (экран сам перерисуется, когда придёт)
  async function loadCal(month) {
    M.calMonth = month; M.calErr = null;
    if (M.cal[month] || M.loading.cal === month) return;
    M.loading.cal = month;
    try { M.cal[month] = await F.api(`/api/calendar?month=${encodeURIComponent(month)}`); } catch (e) { if (M.calMonth === month) M.calErr = e.message || String(e); }
    if (M.loading.cal === month) M.loading.cal = null;
    if (F.S.screen === 'calendar' && M.calMonth === month) F.render(false);
  }

  // ---------- мелкие части ----------
  // Правила понятности (жалоба 02.11 «графики непонятные»): у каждого графика — заголовок простым вопросом, под ним
  // одна строка вывода с цифрой; никаких сокращений (Z1, REM, ВСР, Strain); подписи прямо у столбиков и полос;
  // сравнение — «сейчас / раньше» прямо у полос или «было → стало» словами; ни одного числа без единицы.
  const NB = ' ';
  // длительность словами: 1 ч 48 мин / 45 мин / 8 ч — для фраз
  const dur = h => {
    if (h == null) return '—';
    const m = Math.round(Math.abs(h) * 60), H = Math.floor(m / 60), mm = m % 60;
    return H ? (mm ? `${H}${NB}ч ${mm}${NB}мин` : `${H}${NB}ч`) : `${mm}${NB}мин`;
  };
  // коротко для подписей на столбиках: 7 ч 05 / 45 мин
  const durS = h => {
    if (h == null) return '—';
    const m = Math.round(Math.abs(h) * 60), H = Math.floor(m / 60), mm = m % 60;
    return H ? (mm ? `${H}${NB}ч${NB}${String(mm).padStart(2, '0')}` : `${H}${NB}ч`) : `${mm}${NB}мин`;
  };
  const days = n => `${n}${NB}${plural(n, 'день', 'дня', 'дней')}`;
  // разница с прошлым периодом: ▲ +30 мин / ▼ −2 / «как раньше»; good — что лучше (true — больше, false — меньше, null — без оценки)
  function chg(v, p, { unit = '', d = 0, good = null, eps } = {}) {
    if (v == null || p == null) return '<span class="vs flat as-vs">не с чем сравнить</span>';
    const x = v - p;
    if (Math.abs(x) < (eps != null ? eps : d ? .05 : .5)) return '<span class="vs flat as-vs">как раньше</span>';
    const cls = good == null ? 'flat' : (good ? x > 0 : x < 0) ? 'up' : 'down';
    return `<span class="vs ${cls} as-vs"><i>${x > 0 ? '▲' : '▼'}</i>${x > 0 ? '+' : '−'}${num(Math.abs(x), d)}${unit}</span>`;
  }
  // «лучше / хуже, чем раньше» — словами, для строки вывода
  const better = (v, p, goodUp) => v == null || p == null || Math.abs(v - p) < .5 ? 'как раньше' : (goodUp ? v > p : v < p) ? 'лучше, чем раньше' : 'хуже, чем раньше';
  // плитка сравнения: значение, разница и «было …»
  const tile = (k, v, unit, d, was) => `<div class="tile as-tile"><span class="tile-k">${k}</span><span class="tile-v"><b>${v}</b><small>${unit}</small></span>
    <span class="tile-d as-td">${d}${was != null ? `<span class="as-was">было ${was}</span>` : ''}</span></div>`;
  // строка вывода под заголовком карточки: крупно — главный итог, мельче — пояснение
  const lead = (t, sub) => `<p class="as-lead">${t}</p>${sub ? `<p class="as-sub">${sub}</p>` : ''}`;
  const periodWas = a => a.period === 'week' ? 'прошлая неделя' : 'прошлые 30 дней';

  // полосы «сейчас / раньше» с подписями прямо у полос: rows [{name, dot, cur, prev, fmt, delta}]
  function pairs(a, rows) {
    const max = Math.max(1, ...rows.map(r => Math.max(r.cur || 0, r.prev || 0)));
    const w = v => v ? Math.max(1.5, v / max * 100).toFixed(1) : 0;
    return `<div class="as-pairs as-anim">${rows.map(r => `<div class="as-pg" role="img" aria-label="${esc(r.name)}: сейчас ${r.fmt(r.cur || 0)}, раньше ${r.fmt(r.prev || 0)}">
      <div class="as-ph"><span class="as-pn">${r.dot ? `<i class="as-dot" style="background:${r.dot}"></i>` : ''}${r.name}</span>${r.delta}</div>
      <div class="as-pr now" title="Сейчас: ${r.fmt(r.cur || 0)}"><span class="as-pk">сейчас</span><span class="as-pt"><i style="width:${w(r.cur)}%"></i></span><b class="as-pv num">${r.fmt(r.cur || 0)}</b></div>
      <div class="as-pr was" title="Раньше: ${r.fmt(r.prev || 0)}"><span class="as-pk">раньше</span><span class="as-pt"><i style="width:${w(r.prev)}%"></i></span><span class="as-pv num">${r.fmt(r.prev || 0)}</span></div>
    </div>`).join('')}<p class="as-note">Раньше — ${periodWas(a)}, ${esc(a.prev_label)}.</p></div>`;
  }
  // полоса 0–100 с подписью справа (сон, режим, доля глубокого сна)
  const hbar = (p, label) => `<div class="as-hb as-anim"><span class="as-pt"><i style="width:${Math.max(0, Math.min(100, p || 0)).toFixed(1)}%"></i></span><span class="as-pv num">${label}</span></div>`;
  // столбики и полосы растут через transform, когда карточка уже на экране (без рывков; reduced-motion — сразу)
  const grow = () => requestAnimationFrame(() => requestAnimationFrame(() =>
    document.querySelectorAll('.as-anim:not(.as-go)').forEach(el => el.classList.add('as-go'))));
  // «×1,05» из текста сервера — понятнее процентами: «105%»
  const ratioPct = t => String(t || '').replace(/×(\d+),(\d+)/g, (m, a, b) => `${Math.round(parseFloat(`${a}.${b}`) * 100)}%`);

  // «Нагрузка: мало, норма или много?» — шкала и где эта неделя (нагрузка недели к обычной за 4 недели)
  function stateCard(s) {
    const col = STATE_C[s.key] || 'var(--hint)';
    const lo = .5, hi = 1.6, p = r => Math.max(0, Math.min(100, (r - lo) / (hi - lo) * 100));
    const mp = s.ratio != null ? p(s.ratio) : 0;
    const mark = s.ratio != null ? `<span class="as-mark${mp < 18 ? ' l' : mp > 82 ? ' r' : ''}" style="left:${mp.toFixed(1)}%"><b class="num">ты здесь · ${Math.round(s.ratio * 100)}%</b></span>` : '';
    const seg = (w, cls, t) => `<span class="as-gs ${cls}" style="flex:${w.toFixed(1)}"><i></i><em>${t}</em></span>`;
    return `<section class="card rise">${F.cap('Нагрузка: мало, норма или много?')}<div class="as-word" style="color:${col}">${esc(s.word)}</div>
      ${s.ratio != null ? `<div class="as-gauge" role="img" aria-label="Нагрузка недели — ${Math.round(s.ratio * 100)}% от обычной: ${esc(s.word)}">
        ${seg(p(.8), 'lo', 'мало')}${seg(p(1.3) - p(.8), 'ok', 'норма')}${seg(100 - p(1.3), 'hi', 'много')}${mark}</div>` : ''}
      <p class="small">${esc(ratioPct(s.text))}</p>
      <p class="as-note">Как считаем: нагрузка за 7 дней против твоей обычной за 4 недели (100% — как обычно) и восстановление.</p></section>`;
  }
  function sleepCard(s) {
    const col = SLEEP_C[s.key] || 'var(--hint)';
    if (!s.sleep) return `<section class="card rise">${F.cap('Как ты спишь')}<div class="as-word" style="color:${col}">${esc(s.word)}</div><p class="small muted">${esc(s.text)}</p></section>`;
    const row = (k, v, bar, note) => `<div class="as-sr"><div class="as-srt"><span>${k}</span><span class="num"><b>${v}</b></span></div>${bar || ''}${note ? `<p class="as-note">${note}</p>` : ''}</div>`;
    const short = s.need ? s.need - s.sleep : null;
    const spread = s.spread != null ? Math.round(s.spread * 60) : null;
    return `<section class="card rise">${F.cap('Как ты спишь')}<div class="as-word" style="color:${col}">${esc(s.word)}</div>
      ${row('Сколько спал за ночь', dur(s.sleep), s.need ? hbar(s.pct, `${s.pct}% нормы`) : '',
        s.need ? `Нужно ${dur(s.need)}. ${short > 1 / 6 ? `Не хватает ${dur(short)} за ночь.` : 'Сна хватает.'}` : '')}
      ${row('Ложишься в одно время?', s.bed ? `около ${s.bed}` : '—', s.consistency != null ? hbar(s.consistency, `ровно на ${num(s.consistency)}%`) : '',
        spread != null ? (s.spread <= .75 ? `Отбой гуляет на ±${spread}${NB}мин — режим ровный, это помогает восстановлению.` : `Отбой гуляет на ±${spread}${NB}мин — организму легче, когда режим ровный.`) : '')}
      ${s.restor ? row('Глубокий и быстрый сон', dur(s.restor), s.restor_pct != null ? hbar(s.restor_pct, `${s.restor_pct}% сна`) : '',
        'Самая полезная часть сна: восстанавливает мышцы и голову.') : ''}</section>`;
  }
  function daysCard(a) {
    const c = a.cur, rows = a.days.filter(r => r.recovery != null);
    const best = rows.length ? rows.reduce((x, y) => y.recovery > x.recovery ? y : x) : null;
    const worst = rows.length ? rows.reduce((x, y) => y.recovery < x.recovery ? y : x) : null;
    const wd = r => WD_IN[(F.parseDay(r.day).getDay() + 6) % 7];
    return `<section class="card rise">${F.cap('Восстановление по дням')}
      ${best ? lead(`Лучше всего — ${wd(best)}: ${best.recovery}%`, best !== worst ? `Хуже всего — ${wd(worst)}: ${worst.recovery}%. В среднем — ${num(c.recovery)}%.` : '') : ''}
      <p class="as-legd"><span><i class="as-dot" style="background:var(--zone-green, ${F.C.green})"></i>зелёных — ${days(c.green)}</span><span><i class="as-dot" style="background:var(--zone-yellow, ${F.C.yellow})"></i>жёлтых — ${days(c.yellow)}</span><span><i class="as-dot" style="background:var(--zone-red, ${F.C.red})"></i>красных — ${days(c.red)}</span></p>
      <div class="as-days">${a.days.map(r => `<button class="as-day" data-a="as-goto" data-v="${r.day}" aria-label="${dl(r.day)}${r.recovery != null ? `, восстановление ${r.recovery}%` : ', нет данных'}">
        <span class="as-dc num" style="--c:${zc(r.zone)}">${r.recovery == null ? '—' : `${r.recovery}%`}</span>
        ${r.zone ? `<i class="as-dot" style="background:var(--zone-${r.zone}, ${zc(r.zone)})"></i>` : '<i class="as-dot"></i>'}<span class="as-dw">${F.WD_S[F.parseDay(r.day).getDay()]}</span><span class="as-dd num">${F.parseDay(r.day).getDate()}</span></button>`).join('')}</div>
      <p class="as-note">Цифра — восстановление за день, точка — его зона. Нажми на день — откроется его сводка.</p></section>`;
  }
  const ZN = ['Зона 1 · лёгкая', 'Зона 2 · умеренная', 'Зона 3 · средняя', 'Зона 4 · тяжёлая', 'Зона 5 · максимум'];
  function zonesCard(a) {
    const c = a.cur.zones || [], p = a.prev.zones || [];
    const tc = c.reduce((x, y) => x + y, 0), tp = p.reduce((x, y) => x + y, 0);
    const title = 'Сколько минут в каждой зоне пульса';
    if (!tc && !tp) return `<section class="card rise">${F.cap(title)}<p class="small muted">Тренировок с пульсом за этот период нет.</p></section>`;
    const hc = (c[3] || 0) + (c[4] || 0), hp = (p[3] || 0) + (p[4] || 0), dh = hc - hp;
    const head = !tp ? `Тяжёлых минут (зоны 4–5) — ${hc}`
      : Math.abs(dh) < 5 ? `Тяжёлых минут (зоны 4–5) — как раньше: ${hc}`
      : `Тяжёлых минут стало ${dh > 0 ? 'больше' : 'меньше'} на ${Math.abs(dh)}`;
    const sub = `Всего на тренировках ${tc}${NB}мин${tp ? `, раньше ${tp}${NB}мин` : ''}. Зоны 4–5 — тяжёлая работа, 1–2 — лёгкая.`;
    const mins = v => `${v}${NB}мин`;
    return `<section class="card rise">${F.cap(title)}${lead(head, sub)}
      ${pairs(a, ZN.map((name, i) => ({ name, cur: c[i] || 0, prev: p[i] || 0, fmt: mins, delta: tp ? chg(c[i] || 0, p[i] || 0, { unit: `${NB}мин` }) : '' })))}</section>`;
  }
  const WD_FULL = ['понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье'];
  const WD_IN = ['в понедельник', 'во вторник', 'в среду', 'в четверг', 'в пятницу', 'в субботу', 'в воскресенье'];
  // «В какие дни нагрузка больше»: столбики пн…вс, самый тяжёлый день выделен, значения на столбиках
  function weekdayCard(a) {
    const rows = a.by_weekday.filter(r => r.strain != null);
    if (!rows.length) return `<section class="card rise">${F.cap('В какие дни нагрузка больше')}<p class="small muted">Нагрузки за этот период нет.</p></section>`;
    const top = rows.reduce((x, y) => y.strain > x.strain ? y : x), low = rows.reduce((x, y) => y.strain < x.strain ? y : x);
    const sub = `${low !== top ? `Легче всего — ${WD_IN[low.wd]}: ${num(low.strain, 1)}. ` : ''}${a.period === 'week' ? 'Нагрузка за день' : 'Средняя нагрузка дня за 30 дней'} по шкале от 0 до 21.`;
    return `<section class="card rise">${F.cap('В какие дни нагрузка больше')}${lead(`Тяжелее всего — ${WD_IN[top.wd]}: ${num(top.strain, 1)} из 21`, sub)}
      <div class="as-wk as-anim">${a.by_weekday.map(r => {
        const on = r === top, h = r.strain != null ? Math.max(2, r.strain / 21 * 100).toFixed(1) : 0;
        const dd = a.period === 'week' && r.day ? String(F.parseDay(r.day).getDate()) : '';
        return `<div class="as-wc${on ? ' top' : ''}" role="img" aria-label="${WD_FULL[r.wd]}: нагрузка ${r.strain == null ? 'нет данных' : num(r.strain, 1)}" title="${WD_FULL[r.wd]}: ${r.strain == null ? 'нет данных' : `нагрузка ${num(r.strain, 1)} из 21`}">
          <span class="as-wt"><i style="height:${h}%"></i><b class="as-wv num" style="bottom:${h}%">${r.strain == null ? '—' : num(r.strain, 1)}</b></span>
          <span class="as-wl">${r.label}</span>${dd ? `<span class="as-wd num">${dd}</span>` : ''}</div>`;
      }).join('')}</div></section>`;
  }
  function compareCard(a) {
    const c = a.cur, p = a.prev.days ? a.prev : {}, has = !!a.prev.days;
    const w = (v, u = '') => has && v != null ? `${v}${u}` : null;
    return `<div class="sec rise">${F.cap('Было → стало')}<div class="tiles as-cmp">
      ${tile('Восстановление', num(c.recovery), '%', chg(c.recovery, p.recovery, { unit: '%', good: true }), w(num(p.recovery), '%'))}
      ${tile('Отдых нервов', num(c.hrv), 'мс', chg(c.hrv, p.hrv, { unit: `${NB}мс`, good: true }), w(num(p.hrv), `${NB}мс`))}
      ${tile('Пульс покоя', num(c.rhr), 'уд/мин', chg(c.rhr, p.rhr, { good: false }), w(num(p.rhr)))}
      ${tile('Сон за ночь', durS(c.sleep), '', chg(c.sleep != null ? Math.round(c.sleep * 60) : null, p.sleep != null ? Math.round(p.sleep * 60) : null, { unit: `${NB}мин`, good: true, eps: 3 }), w(durS(p.sleep)))}
      ${tile('Нагрузка в день', num(c.strain, 1), 'из 21', chg(c.strain, p.strain, { d: 1 }), w(num(p.strain, 1)))}
      ${tile('Тренировки', c.workouts, `· ${c.minutes}${NB}мин`, chg(c.workouts, p.workouts), w(p.workouts))}
    </div><p class="as-note">${a.period === 'week' ? 'Прошлая неделя' : 'Прошлые 30 дней'} (${esc(a.prev_label)}) → ${a.period === 'week' ? 'эта' : 'последние 30'} (${esc(a.label)}). Зелёная стрелка — к лучшему, красная — к худшему.</p>
    ${a.prev.days && a.prev.days < a.span * .7 ? `<p class="small muted">За прошлый период есть данные только за ${days(a.prev.days)} — сравнение примерное.</p>` : ''}</div>`;
  }
  // «Сколько было хороших дней»: зелёные / жёлтые / красные — сейчас и раньше
  function recCard(a) {
    const c = a.cur, p = a.prev.days ? a.prev : null;
    const dot = k => `var(--zone-${k}, ${F.C[k]})`;
    const rows = [['green', 'Зелёные · полон сил', true], ['yellow', 'Жёлтые · средне', null], ['red', 'Красные · нужен отдых', false]]
      .map(([k, name, good]) => ({ name, dot: dot(k), cur: c[k] || 0, prev: p ? p[k] || 0 : 0, fmt: days, delta: p ? chg(c[k] || 0, p[k] || 0, { good }) : '' }));
    return `<section class="card rise">${F.cap('Сколько было хороших дней')}
      ${lead(`Восстановление в среднем ${num(c.recovery)}%${p ? ` — ${better(c.recovery, p.recovery, true)} (${num(p.recovery)}%)` : ''}`,
        c.best ? `Лучший день — ${dl(c.best.day)} (${c.best.recovery}%), хуже всего — ${dl(c.worst.day)} (${c.worst.recovery}%).` : '')}
      ${pairs(a, rows)}</section>`;
  }
  // «Сколько спал и сколько нужно» по неделям месяца: столбик — сон (внизу — глубокий и быстрый),
  // пунктирная рамка — сколько не хватило до нормы, черта с подписью — сколько нужно
  function weeksSleepCard(a) {
    if (!Array.isArray(a.weeks) || !a.weeks.length) return '';  // мало данных за месяц — карточку не рисуем (раньше экран падал)
    const W = a.weeks.filter(w => w.sleep);
    if (!W.length) return '';
    const top = Math.max(...W.map(w => Math.max(w.sleep, w.need || 0)));
    const P = v => Math.max(0, v / top * 100).toFixed(1);
    const c = a.cur, short = c.need && c.sleep ? c.need - c.sleep : null;
    const head = short == null ? `В среднем ${dur(c.sleep)} за ночь`
      : short > 1 / 6 ? `Спишь на ${dur(short)} меньше нужного`
      : short < -1 / 6 ? `Спишь на ${dur(-short)} больше нужного` : 'Сна хватает — спишь сколько нужно';
    const sub = `За ночь в среднем ${dur(c.sleep)}${c.need ? `, нужно ${dur(c.need)}` : ''}.${c.restor ? ` Глубокий и быстрый сон — ${dur(c.restor)}${a.prev.restor ? `, раньше ${dur(a.prev.restor)}` : ''}.` : ''}`;
    const PH = 150;  // высота поля столбиков, px — для решения, влезает ли подпись внутрь
    const col = w => {
      if (!w.sleep) return `<div class="as-sc none"><div class="as-sp"></div><span class="as-cl">${esc(w.label)}</span><span class="as-cs">нет данных</span></div>`;
      const need = w.need || 0, lack = need - w.sleep, hi = Math.max(w.sleep, need);
      const rh = w.restor ? w.restor / top * PH : 0;
      return `<div class="as-sc" role="img" title="${esc(w.label)}: спал ${dur(w.sleep)}${need ? `, нужно ${dur(need)}` : ''}${w.restor ? `, глубокий и быстрый ${dur(w.restor)}` : ''}"
        aria-label="${esc(w.label)}: спал ${dur(w.sleep)}${need ? `, нужно ${dur(need)}` : ''}${w.restor ? `, из них глубокий и быстрый ${dur(w.restor)}` : ''}">
        <div class="as-sp">
          ${need ? `<span class="as-nt" style="bottom:${P(hi)}%">нужно<b class="num">${durS(need)}</b></span>` : ''}
          ${lack > 0 ? `<span class="as-gp" style="bottom:${P(w.sleep)}%;height:${P(lack)}%"></span>` : ''}
          <i class="as-f lt" style="height:${P(w.sleep)}%"></i>
          ${w.restor ? `<i class="as-f dp" style="height:${P(w.restor)}%">${rh >= 20 ? `<b class="num">${durS(w.restor)}</b>` : ''}</i>` : ''}
          ${need ? `<span class="as-nl" style="bottom:${P(need)}%"></span>` : ''}
        </div>
        <span class="as-cl">${esc(w.label)}</span><b class="as-cv num">спал ${durS(w.sleep)}</b>
        ${need ? (lack > 1 / 12 ? `<span class="as-cs">не хватило<b class="num">${durS(lack)}</b></span>` : '<span class="as-cs ok">хватило ✓</span>') : ''}</div>`;
    };
    return `<section class="card rise">${F.cap('Сколько спал и сколько нужно')}${lead(head, sub)}
      <div class="as-leg"><span><i class="as-sw dp"></i>глубокий и быстрый</span><span><i class="as-sw lt"></i>остальной сон</span><span><i class="as-sw gp"></i>не хватило</span><span><i class="as-sw nl"></i>нужно</span></div>
      <div class="as-ws as-anim">${a.weeks.map(col).join('')}</div>
      <p class="as-note">Столбик — сколько спал за ночь в среднем за неделю, черта — сколько нужно.</p></section>`;
  }
  function trendCard(a, key, title, unit, goodUp, about) {
    const avg = a.cur[key], pv = a.prev.days ? a.prev[key] : null;
    return `<section class="card rise">${F.cap(title, goodUp ? 'выше — лучше' : 'ниже — лучше')}
      <div class="big-line"><span class="v num">${num(avg)}</span><span class="u">${unit} в среднем</span>${chg(avg, pv, { unit: ` ${unit}`, good: goodUp })}</div>
      <p class="as-sub">${about}${pv != null ? ` Раньше — ${num(pv)}${NB}${unit}: сейчас ${better(avg, pv, goodUp)}.` : ''}</p>
      <div class="chart-box">${F.line(a.days, key, 'var(--rec-blue)', avg)}</div></section>`;
  }

  // ---------- экраны ----------
  F.screens.assess = (t, d, arg) => {
    const per = arg === 'month' ? 'month' : 'week';
    const a = M.as[per];
    if (!a && !M.loading[per] && !M.err[per]) loadAs(per);
    let h = F.dHead(per === 'week' ? 'Оценка недели' : 'Оценка месяца',
      a ? `${a.label} · сравнение с ${a.prev_label}` : (per === 'week' ? 'последние 7 полных дней' : 'последние 30 полных дней'), 'var(--teal)');
    h += `<div class="seg as-seg" role="tablist">${[['week', 'Неделя'], ['month', 'Месяц']].map(([v, l]) => `<button class="${per === v ? 'on' : ''}" data-a="as-per" data-v="${v}" role="tab">${l}</button>`).join('')}</div>`;
    if (!a && M.err[per]) return h + F.retryCard('Оценка не загрузилась', 'as-retry');
    if (!a) return h + [1, 2, 3].map(() => `<section class="card">${F.skel(3)}</section>`).join('');
    if (!a.enough) return h + `<div class="empty"><span class="e">${IC.cal}</span><b>Мало данных</b>Нужно хотя бы 3 дня с браслетом за этот период — тогда соберу оценку.</div>`;
    const tips = `<button class="btn primary as-tips" data-a="summary" data-v="${per}">Советы тренера</button>`;
    if (per === 'week') {
      h += stateCard(a.state) + sleepCard(a.sleep) + daysCard(a) + weekdayCard(a) + zonesCard(a) + compareCard(a) + tips;
    } else {
      h += recCard(a) + sleepCard(a.sleep) + weeksSleepCard(a) + zonesCard(a) + weekdayCard(a)
        + trendCard(a, 'hrv', 'Отдых нервов', 'мс', true, 'Чем выше, тем лучше отдохнула нервная система.')
        + trendCard(a, 'rhr', 'Пульс покоя', 'уд/мин', false, 'Чем ниже, тем легче сердцу.')
        + compareCard(a) + tips;
    }
    grow();
    return h;
  };

  F.screens.calendar = () => {
    const month = M.calMonth || (F.S.data.today.day || '').slice(0, 7);
    if (!M.cal[month] && !M.calErr) loadCal(month);
    const c = M.cal[month];
    let h = F.dHead('Календарь', 'восстановление по дням', 'var(--rec-green)');
    const title = c ? c.title : '';
    h += `<div class="as-nav"><button class="avatar ghost" data-a="as-cal" data-v="${c && c.prev ? c.prev : ''}" ${c && c.prev ? '' : 'disabled'} aria-label="Прошлый месяц">${IC.prev}</button>
      <b>${esc(title) || '&nbsp;'}</b><button class="avatar ghost" data-a="as-cal" data-v="${c && c.next ? c.next : ''}" ${c && c.next ? '' : 'disabled'} aria-label="Следующий месяц">${IC.next}</button></div>`;
    if (!c && M.calErr) return h + F.retryCard('Календарь не загрузился', 'as-cal-retry');
    if (!c) return h + `<section class="card"><div class="sk" style="height:300px"></div></section>`;
    const head = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс'].map(w => `<span class="as-cw">${w}</span>`).join('');
    const cells = Array.from({ length: c.offset }, () => '<span></span>').join('') + c.days.map(r => {
      const dd = F.parseDay(r.day).getDate(), off = r.future || r.old;
      const has = r.recovery != null;
      return `<button class="as-cd${r.today ? ' today' : ''}${has ? ' has' : ''}" ${off ? 'disabled' : `data-a="as-goto" data-v="${r.day}"`} aria-label="${dl(r.day)}${has ? `, ${r.recovery}%` : ''}">
        <span class="as-cc num" style="--c:${has ? zc(r.zone) : 'transparent'}">${dd}</span><span class="as-cp num">${has ? r.recovery + '%' : ''}</span></button>`;
    }).join('');
    const s = c.stats;
    h += `<section class="card rise as-calc"><div class="as-grid">${head}${cells}</div></section>`;
    h += `<section class="card rise">${F.cap('Итог месяца', s.days ? `<span class="num"><i class="dot" style="background:${F.C.green}"></i>${s.green}<i class="dot" style="background:${F.C.yellow}"></i>${s.yellow}<i class="dot" style="background:${F.C.red}"></i>${s.red}</span>` : '')}
      ${s.days ? `<div class="tiles as-cmp">${tile('Восстановление', num(s.recovery), '%', '<span class="vs flat">в среднем</span>')}${tile('Сон', hm(s.sleep), '', '<span class="vs flat">в среднем</span>')}
        ${tile('Нагрузка', num(s.strain, 1), 'в день', '<span class="vs flat">в среднем</span>')}${tile('Тренировки', s.workouts, 'шт', `<span class="vs flat">${s.days} ${plural(s.days, 'день', 'дня', 'дней')} с данными</span>`)}</div>`
        : '<p class="small muted">За этот месяц данных WHOOP нет.</p>'}
      <p class="small muted">Цвет кружка — зона восстановления. Нажми на день — откроется его сводка на главной.</p></section>`;
    return h;
  };

  // ---------- действия ----------
  F.actions['as-open'] = (el, v) => { F.haptic('tap'); F.openScreen(v === 'calendar' ? 'calendar' : 'assess', v); };
  F.actions['as-per'] = (el, v) => { if (F.S.screenArg === v) return; F.haptic('select'); F.S.screenArg = v; F.render(false); };
  F.actions['as-retry'] = () => { const per = F.S.screenArg === 'month' ? 'month' : 'week'; M.err[per] = null; loadAs(per); F.render(false); };
  F.actions['as-cal'] = (el, v) => { if (!v) return; F.haptic('select'); loadCal(v); F.render(false); };
  F.actions['as-cal-retry'] = () => { M.calErr = null; F.render(false); };
  F.actions['as-goto'] = (el, v) => { F.go('today'); F.shiftTo(v); };
  // данные обновились (раз в 2 минуты или «Обновить») — оценки и календарь пересчитаются при следующем открытии
  F.onData.push(() => { if (F.S.screen !== 'assess') { M.as = {}; M.err = {}; } if (F.S.screen !== 'calendar') { M.cal = {}; M.calErr = null; M.calMonth = null; } });

  // ---------- входы: «Здоровье» и «＋» ----------
  F.slots.healthAfterMonitor.push(() => {
    const w = (((F.S.data || {}).ext || {}).assess || {}).week;
    const dots = w && w.zones ? `<span class="as-dots">${w.zones.map(z => `<i style="background:${zc(z)}"></i>`).join('')}</span>` : '';
    const st = w && w.enough ? `<b style="color:${STATE_C[w.state.key] || 'var(--hint)'}">${esc(w.state.word)}</b><small>сон: ${esc((w.sleep.word || '').toLowerCase())}</small>` : '<b class="muted">Неделя</b><small>за 7 полных дней</small>';
    return `<div class="sec rise">${F.cap('Оценки')}<div class="as-entries">
      <button class="tile as-entry" data-a="as-open" data-v="week"><span class="as-ei">${IC.week}</span><span class="tile-k">Оценка недели${w ? `<br><span class="as-el">${esc(w.label)}</span>` : ''}</span><span class="as-ev">${st}</span>${dots}</button>
      <button class="tile as-entry" data-a="as-open" data-v="month"><span class="as-ei">${IC.month}</span><span class="tile-k">Оценка месяца</span><span class="as-ev"><b>30 дней</b><small>сон, пульс, отдых нервов — против прошлых 30 дней</small></span></button>
      <button class="tile as-entry as-cal" data-a="as-open" data-v="calendar"><span class="as-ei">${IC.cal}</span><span class="tile-k">Календарь</span><span class="as-ev"><b>По дням</b><small>восстановление за полгода</small></span></button>
    </div></div>`;
  });
  F.slots.plus.push(row => row('as-open', 'calendar', IC.cal, 'var(--rec-green)', 'Календарь', 'восстановление по дням, полгода'));
});
