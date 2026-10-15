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
  // стрелка к прошлому периоду: зелёная — к лучшему, красная — к худшему, серая — без оценки
  function arrow(v, p, goodUp, d = 0, unit = '') {
    if (v == null || p == null) return '<span class="vs flat">нет сравнения</span>';
    const x = v - p;
    if (Math.abs(x) < (d ? .05 : .5)) return '<span class="vs flat">как раньше</span>';
    const cls = goodUp == null ? 'flat' : (goodUp ? x > 0 : x < 0) ? 'up' : 'down';
    return `<span class="vs ${cls}"><i>${x > 0 ? '▲' : '▼'}</i>${num(Math.abs(x), d)}${unit}</span>`;
  }
  const arrowH = (v, p) => {  // для часов сна: разница в минутах
    if (v == null || p == null) return '<span class="vs flat">нет сравнения</span>';
    const m = Math.round((v - p) * 60);
    if (Math.abs(m) < 3) return '<span class="vs flat">как раньше</span>';
    return `<span class="vs ${m > 0 ? 'up' : 'down'}"><i>${m > 0 ? '▲' : '▼'}</i>${Math.abs(m)} мин</span>`;
  };
  const tile = (k, v, unit, d) => `<div class="tile as-tile"><span class="tile-k">${k}</span><span class="tile-v"><b>${v}</b><small>${unit}</small></span><span class="tile-d">${d}</span></div>`;
  // столбики с подписями (HTML, без библиотек): rows [{label, sub, v, color, title}]
  function cols(rows, max, fmt) {
    const top = max || Math.max(1, ...rows.map(r => r.v || 0)) * 1.1;
    return `<div class="as-cols">${rows.map(r => `<div class="as-col" title="${esc(r.title || '')}">
      <span class="as-cv num">${r.v == null ? '—' : fmt(r.v)}</span>
      <span class="as-ct"><i style="height:0;background:${r.color}" data-h="${r.v == null ? 0 : Math.max(3, F.pct(r.v, top))}%"></i></span>
      <span class="as-cl">${r.label}</span>${r.sub ? `<span class="as-cs num">${r.sub}</span>` : ''}</div>`).join('')}</div>`;
  }
  // столбики растут как полоски в app.js (data-w), но по высоте
  const grow = () => requestAnimationFrame(() => requestAnimationFrame(() =>
    document.querySelectorAll('.as-ct i[data-h]').forEach(el => { el.style.height = el.dataset.h; })));

  // «Состояние тренированности»: шкала недогруз — оптимально — перегрузка и где неделя (отношение нагрузки 7/28)
  function stateCard(s, title) {
    const col = STATE_C[s.key] || 'var(--hint)';
    const lo = .5, hi = 1.6, p = r => Math.max(0, Math.min(100, (r - lo) / (hi - lo) * 100));
    const mark = s.ratio != null ? `<span class="as-mark" style="left:${p(s.ratio)}%"><b class="num">×${num(s.ratio, 2)}</b></span>` : '';
    return `<section class="card rise">${F.cap(title || 'Состояние тренированности')}<div class="as-word" style="color:${col}">${esc(s.word)}</div>
      <div class="as-gauge"><span style="flex:${p(.8)};background:var(--rec-blue)"></span><span style="flex:${p(1.3) - p(.8)};background:var(--teal)"></span><span style="flex:${100 - p(1.3)};background:var(--rec-red)"></span>${mark}</div>
      <div class="as-gl"><span>Недогруз</span><span>Оптимально</span><span>Перегрузка</span></div>
      <p class="small">${esc(s.text)}</p>
      <p class="small muted">Наш расчёт: средняя нагрузка за 7 дней к средней за 4 недели и восстановление недели.</p></section>`;
  }
  function sleepCard(s) {
    const col = SLEEP_C[s.key] || 'var(--hint)';
    if (!s.sleep) return `<section class="card rise">${F.cap('Статус сна')}<div class="as-word" style="color:${col}">${esc(s.word)}</div><p class="small muted">${esc(s.text)}</p></section>`;
    const row = (k, v, sub, p, c) => `<div class="as-sr"><div class="as-srt"><span>${k}</span><span class="num"><b>${v}</b>${sub ? ` ${sub}` : ''}</span></div>${p != null ? F.bar(p, c) : ''}</div>`;
    return `<section class="card rise">${F.cap('Статус сна')}<div class="as-word" style="color:${col}">${esc(s.word)}</div>
      ${row('Сон против потребности', hm(s.sleep), s.need ? `из ${hm(s.need)} · ${s.pct}%` : '', s.pct, 'var(--sleep)')}
      ${row('Режим (WHOOP)', s.consistency != null ? `${num(s.consistency)}%` : '—', s.bed ? `· отбой ~${s.bed}${s.spread != null ? ` ±${Math.round(s.spread * 60)} мин` : ''}` : '', s.consistency, 'var(--st-rem)')}
      ${row('Глубокий + REM', hm(s.restor), s.restor_pct != null ? `· ${s.restor_pct}% сна` : '', s.restor_pct != null ? Math.min(100, s.restor_pct / 60 * 100) : null, 'var(--st-deep)')}
      <p class="small">${esc(s.text)}</p></section>`;
  }
  function daysCard(a) {
    const c = a.cur;
    return `<section class="card rise">${F.cap('Дни', `<span class="num"><i class="dot" style="background:${F.C.green}"></i>${c.green}<i class="dot" style="background:${F.C.yellow}"></i>${c.yellow}<i class="dot" style="background:${F.C.red}"></i>${c.red}</span>`)}
      <div class="as-days">${a.days.map(r => `<button class="as-day" data-a="as-goto" data-v="${r.day}" aria-label="${dl(r.day)}">
        <span class="as-dc num" style="--c:${zc(r.zone)}">${r.recovery == null ? '—' : r.recovery}</span>
        <span class="as-dw">${F.WD_S[F.parseDay(r.day).getDay()]}</span><span class="as-dd num">${F.parseDay(r.day).getDate()}</span></button>`).join('')}</div>
      <p class="small muted">Восстановление по дням, %. Нажми на день — откроется его сводка.</p></section>`;
  }
  function zonesCard(a, what) {
    const c = a.cur.zones || [], p = a.prev.zones || [];
    const tc = c.reduce((x, y) => x + y, 0), tp = p.reduce((x, y) => x + y, 0);
    if (!tc && !tp) return `<section class="card rise">${F.cap('Пульсовые зоны')}<p class="small muted">Тренировок с пульсом за этот период нет.</p></section>`;
    const max = Math.max(1, ...c, ...p);
    return `<section class="card rise">${F.cap('Пульсовые зоны', `<span class="num"><b>${tc}</b> мин</span>`)}
      ${c.map((m, i) => `<div class="as-z"><span class="as-zk"><i style="background:${ZCOL[i]}"></i>Z${i + 1}</span>
        <span class="as-zb"><span class="bar"><i style="width:0;background:${ZCOL[i]}" data-w="${F.pct(m, max)}%"></i></span><span class="bar as-zp"><i style="width:0" data-w="${F.pct(p[i] || 0, max)}%"></i></span></span>
        <span class="as-zv num"><b>${m}</b><small>${p[i] || 0}</small></span></div>`).join('')}
      <p class="small muted">Минуты по тренировкам: яркая полоска — ${what}, серая — ${a.period === 'week' ? 'прошлая неделя' : 'прошлый месяц'} (${tp} мин). Z4–Z5 — тяжёлая работа, Z1–Z2 — лёгкая.</p></section>`;
  }
  function weekdayCard(a, title) {
    const rows = a.by_weekday.map(r => ({ label: r.label, v: r.strain, color: 'var(--strain)', sub: a.period === 'week' && r.day ? String(F.parseDay(r.day).getDate()) : '' }));
    return `<section class="card rise">${F.cap(title, `<span class="num"><b style="color:var(--strain)">${num(a.cur.strain, 1)}</b> ср.</span>`)}
      ${cols(rows, 21, v => num(v, 1))}</section>`;
  }
  function compareCard(a) {
    const c = a.cur, p = a.prev.days ? a.prev : {};
    return `<div class="sec rise">${F.cap(`Против ${a.period === 'week' ? 'прошлой недели' : 'прошлого месяца'}`, esc(a.prev_label))}<div class="tiles as-cmp">
      ${tile('Восстановление', num(c.recovery), '%', arrow(c.recovery, p.recovery, true, 0, '%'))}
      ${tile('ВСР', num(c.hrv), 'мс', arrow(c.hrv, p.hrv, true, 0, ' мс'))}
      ${tile('Пульс покоя', num(c.rhr), 'уд/мин', arrow(c.rhr, p.rhr, false))}
      ${tile('Сон', hm(c.sleep), '', arrowH(c.sleep, p.sleep))}
      ${tile('Нагрузка', num(c.strain, 1), 'в день', arrow(c.strain, p.strain, null, 1))}
      ${tile('Тренировки', c.workouts, `· ${c.minutes} мин`, arrow(c.workouts, p.workouts, null))}
    </div>${a.prev.days && a.prev.days < a.span * .7 ? `<p class="small muted">За прошлый период есть данные только за ${a.prev.days} ${plural(a.prev.days, 'день', 'дня', 'дней')} — сравнение примерное.</p>` : ''}</div>`;
  }
  function recCard(a) {
    const c = a.cur, p = a.prev;
    const zb = (x, col) => x ? `<span style="flex:${x};background:${col}"></span>` : '';
    const line = (x, k) => (x.green + x.yellow + x.red) ? `<div class="as-rl"><span class="small muted">${k}</span><div class="as-zr">${zb(x.green, F.C.green)}${zb(x.yellow, F.C.yellow)}${zb(x.red, F.C.red)}</div>
      <span class="num small">${x.green}·${x.yellow}·${x.red}</span></div>` : '';
    return `<section class="card rise">${F.cap('Восстановление', arrow(c.recovery, p.days ? p.recovery : null, true, 0, '%'))}
      <div class="big-line"><span class="v num" style="color:${zc(c.recovery >= 67 ? 'green' : c.recovery >= 34 ? 'yellow' : 'red')}">${num(c.recovery)}</span><span class="u">% в среднем</span></div>
      ${line(c, 'этот')}${p.days ? line(p, 'прошлый') : ''}
      <p class="small muted">Дни по зонам: зелёные · жёлтые · красные.${c.best ? ` Лучший день — ${dl(c.best.day)} (${c.best.recovery}%), худший — ${dl(c.worst.day)} (${c.worst.recovery}%).` : ''}</p></section>`;
  }
  // сон и потребность по неделям месяца: столбик сна (тёмная часть — глубокий+REM), черта — сколько было нужно
  function weeksSleepCard(a) {
    const top = Math.max(1, ...a.weeks.map(w => Math.max(w.sleep || 0, w.need || 0))) * 1.08;
    return `<section class="card rise">${F.cap('Сон и потребность', '<i class="tick"></i>нужно')}
      <div class="as-cols as-ws">${a.weeks.map(w => `<div class="as-col">
        <span class="as-cv num">${hm(w.sleep)}</span>
        <span class="as-ct">${w.need ? `<em class="as-need" style="bottom:${F.pct(w.need, top)}%"></em>` : ''}
          <i style="height:0;background:var(--sleep)" data-h="${w.sleep ? F.pct(w.sleep, top) : 0}%"><s style="height:${w.sleep && w.restor ? F.pct(w.restor, w.sleep) : 0}%"></s></i></span>
        <span class="as-cl">${esc(w.label)}</span><span class="as-cs num">${w.need ? `из ${hm(w.need)}` : ''}</span></div>`).join('')}</div>
      <p class="small muted">Тёмная часть столбика — восстанавливающий сон (глубокий + REM): в среднем ${hm(a.cur.restor)} за ночь${a.prev.restor ? `, месяцем раньше ${hm(a.prev.restor)}` : ''}.</p></section>`;
  }
  function trendCard(a, key, title, unit, goodWord) {
    const avg = a.cur[key], pv = a.prev.days ? a.prev[key] : null;
    return `<section class="card rise">${F.cap(title, goodWord)}
      <div class="big-line"><span class="v num">${num(avg)}</span><span class="u">${unit}</span>${arrow(avg, pv, key === 'hrv', 0, '')}</div>
      <div class="chart-box">${F.line(a.days, key, 'var(--rec-blue)', avg)}</div></section>`;
  }

  // ---------- экраны ----------
  F.screens.assess = (t, d, arg) => {
    const per = arg === 'month' ? 'month' : 'week';
    const a = M.as[per];
    if (!a && !M.loading[per] && !M.err[per]) loadAs(per);
    let h = F.dHead(per === 'week' ? 'Оценка недели' : 'Оценка месяца',
      a ? `${a.label} · полные дни против ${a.prev_label}` : (per === 'week' ? 'последние 7 полных дней' : 'последние 30 полных дней'), 'var(--teal)');
    h += `<div class="seg as-seg" role="tablist">${[['week', 'Неделя'], ['month', 'Месяц']].map(([v, l]) => `<button class="${per === v ? 'on' : ''}" data-a="as-per" data-v="${v}" role="tab">${l}</button>`).join('')}</div>`;
    if (!a && M.err[per]) return h + F.retryCard('Оценка не загрузилась', 'as-retry');
    if (!a) return h + [1, 2, 3].map(() => `<section class="card">${F.skel(3)}</section>`).join('');
    if (!a.enough) return h + `<div class="empty"><span class="e">${IC.cal}</span><b>Мало данных</b>Нужно хотя бы 3 дня с браслетом за этот период — тогда соберу оценку.</div>`;
    const tips = `<button class="btn primary as-tips" data-a="summary" data-v="${per}">Советы тренера</button>`;
    if (per === 'week') {
      h += stateCard(a.state) + sleepCard(a.sleep) + daysCard(a) + weekdayCard(a, 'Нагрузка по дням') + zonesCard(a, 'эта неделя') + compareCard(a) + tips;
    } else {
      h += recCard(a) + sleepCard(a.sleep) + weeksSleepCard(a) + zonesCard(a, 'этот месяц') + weekdayCard(a, 'Нагрузка: пн–вс')
        + trendCard(a, 'hrv', 'ВСР', 'мс', 'выше — лучше') + trendCard(a, 'rhr', 'Пульс покоя', 'уд/мин', 'ниже — лучше')
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
      <button class="tile as-entry" data-a="as-open" data-v="month"><span class="as-ei">${IC.month}</span><span class="tile-k">Оценка месяца</span><span class="as-ev"><b>30 дней</b><small>сон, зоны, ВСР — против прошлых 30</small></span></button>
      <button class="tile as-entry as-cal" data-a="as-open" data-v="calendar"><span class="as-ei">${IC.cal}</span><span class="tile-k">Календарь</span><span class="as-ev"><b>По дням</b><small>восстановление за полгода</small></span></button>
    </div></div>`;
  });
  F.slots.plus.push(row => row('as-open', 'calendar', IC.cal, 'var(--rec-green)', 'Календарь', 'восстановление по дням, полгода'));
});
