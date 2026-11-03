/* activity — как в WHOOP: экран тренировки (Activity detail), тренды 1Н/1М/6М в разборах сна, восстановления и нагрузки,
   подсказки-вопросы тренеру (WHOOP Coach). Подключение: window.FormaModules (см. начало app.js). Только клиент. */
(window.FormaModules = window.FormaModules || []).push(F => {
  const { esc, num, big, hm, plural } = F;
  const back = F.svgi('<path d="M15 5l-7 7 7 7"/>');
  const spark = F.svgi('<path d="M12 3.5l1.9 5.1 5.1 1.9-5.1 1.9L12 17.5l-1.9-5.1L5 10.5l5.1-1.9z"/><path d="M19 15.5l.8 2 2 .7-2 .8-.8 2-.8-2-2-.8 2-.7z"/>');
  const chev = F.svgi('<path d="M9 5l7 7-7 7"/>');

  // ---------- экран тренировки ----------
  // пульсовые зоны WHOOP: доля от макс. пульса
  const ZONES = [[50, 60], [60, 70], [70, 80], [80, 90], [90, 100]];
  const ZCOL = ['var(--z1)', 'var(--z2)', 'var(--z3)', 'var(--z4)', 'var(--z5)'];
  // уровни нагрузки 0–21 — те же слова, что в расшифровке «Нагрузка» (app.js, ABOUT.strain)
  const level = s => s == null ? '' : s < 10 ? 'Лёгкая нагрузка' : s < 14 ? 'Средняя нагрузка' : s < 18 ? 'Высокая нагрузка' : 'Очень высокая нагрузка';
  const nav = { prev: null };  // откуда открыли тренировку: «назад» ведёт туда же (с экрана нагрузки — на него)

  function findWorkout(id) {
    const d = F.S.data, days = [F.S.dayData, d && d.today].filter(Boolean);
    for (const t of days) {
      const w = (t.workouts || []).find(x => String(x.id) === String(id));
      if (w) return { w, t };
    }
    return null;
  }
  const endOf = (start, min) => {
    const m = /^(\d{1,2}):(\d{2})$/.exec(start || '');
    if (!m || !min) return '';
    const e = (Number(m[1]) * 60 + Number(m[2]) + min) % 1440;
    return `${String(Math.floor(e / 60)).padStart(2, '0')}:${String(e % 60).padStart(2, '0')}`;
  };
  const wHead = (title, sub) => `<header class="dhead"><button class="avatar ghost bk" data-a="actv-back" aria-label="Назад">${back}<span class="bk-t">Назад</span></button>
    <div class="titles"><h1 style="--c:var(--strain)">${title}</h1><div class="sub">${sub}</div></div></header>`;
  const tile = (k, v, unit, sub) => `<div class="tile actv-tile"><span class="tile-k">${k}</span>
    <span class="tile-v"><b class="num">${v}</b>${unit ? `<small>${unit}</small>` : ''}</span>${sub ? `<span class="actv-sub">${sub}</span>` : ''}</div>`;

  // Пульс: средний крупно, максимальный рядом, шкала от 50% до 100% макс. пульса с метками «средний» и «пик»
  function hrCard(w, maxHr) {
    const avg = w.avg_hr, mx = w.max_hr;
    if (!avg && !mx) return '';
    let scale = '';
    if (maxHr) {
      const lo = Math.round(maxHr * .5), pos = v => Math.max(0, Math.min(100, (v - lo) / (maxHr - lo) * 100)).toFixed(1);
      const ticks = ZONES.map(([z]) => `<i style="left:${((z - 50) / 50 * 100).toFixed(1)}%"></i>`).join('');
      scale = `<div class="hr-scale" aria-hidden="true"><div class="hr-track">${ticks}
          ${avg ? `<span class="hr-fill" style="width:${pos(avg)}%"></span><b class="hr-mk avg" style="left:${pos(avg)}%"></b>` : ''}
          ${mx ? `<b class="hr-mk max" style="left:${pos(mx)}%"></b>` : ''}</div>
        <div class="hr-ax num"><span>${lo}</span><span>${maxHr}</span></div></div>
        <div class="hr-legend"><span><b class="dot avg"></b>средний${avg ? ` · <b class="num">${Math.round(avg / maxHr * 100)}%</b>` : ''}</span>
          <span><b class="dot max"></b>пик${mx ? ` · <b class="num">${Math.round(mx / maxHr * 100)}%</b>` : ''}</span><span class="muted">от макс. ${maxHr}</span></div>`;
    }
    return `<section class="card hr-card rise">${F.cap('Пульс')}
      <div class="hr-nums"><div class="hr-n"><b class="num">${avg || '—'}</b><span>средний, уд/мин</span></div>
        <div class="hr-n"><b class="num">${mx || '—'}</b><span>максимальный</span></div></div>${scale}</section>`;
  }

  const ZNAME = ['лёгкая', 'выносливость', 'темп', 'тяжело', 'максимум'];
  function zonesCard(w, maxHr) {
    const z = w.zones || [];
    const tot = z.reduce((a, x) => a + (x || 0), 0);
    if (!tot) return `<section class="card rise">${F.cap('Пульсовые зоны')}<p class="small muted">WHOOP не прислал зоны пульса для этой тренировки.</p></section>`;
    const pc = m => Math.round(m / tot * 100);
    const top = Math.max(...z.slice(1).map(x => x || 0), 1);
    // общая полоса: сколько времени в каждой зоне (слева — лёгкие, справа — тяжёлые)
    const strip = [0, 1, 2, 3, 4].map(i => { const m = z[i + 1] || 0; return m ? `<i style="flex:${m};background:${ZCOL[i]}" title="Зона ${i + 1}: ${m} мин"></i>` : ''; }).join('');
    // сверху — самая тяжёлая зона, как в WHOOP
    const rows = [4, 3, 2, 1, 0].map(i => {
      const m = z[i + 1] || 0, [lo, hi] = ZONES[i];
      const bpm = maxHr ? `${Math.round(maxHr * lo / 100)}–${Math.round(maxHr * hi / 100)} уд/мин` : `${lo}–${hi}% от макс.`;
      return `<div class="actv-z${m ? '' : ' none'}"><span class="actv-zk"><b><i class="zsw" style="background:${ZCOL[i]}"></i>Зона ${i + 1} · ${ZNAME[i]}</b><small class="num">${bpm}</small></span>
        <span class="actv-zbar"><i style="width:0;background:${ZCOL[i]}" data-w="${m ? Math.max(3, m / top * 100).toFixed(1) : 0}%"></i></span>
        <span class="actv-zv num"><b>${m}</b><small>мин · ${pc(m)}%</small></span></div>`;
    }).join('');
    const z0 = z[0] || 0;
    return `<section class="card rise">${F.cap('Пульсовые зоны', `<span class="num">${tot} мин</span>`)}
      <div class="z-strip" aria-hidden="true">${strip}</div>
      ${rows}<p class="small muted">${z0 ? `Ниже зоны 1 (разминка, паузы) — ${z0} мин. ` : ''}Зоны 1–2 — лёгкая работа и выносливость, 3 — темп, 4–5 — тяжело: раунды, спурты.</p></section>`;
  }

  function shareCard(w, t, others) {
    const ds = t.cycle && t.cycle.strain != null ? t.cycle.strain : null;
    if (w.strain == null || ds == null) return '';
    const row = (k, v, col) => `<div class="hv-r"><span>${k}</span><b class="num">${num(v, 1)}</b></div>${F.bar(F.pct(v, 21), col)}`;
    // наш расчёт: шкала WHOOP не складывается (растёт всё медленнее), поэтому сравниваем тренировку с днём как есть
    const k = w.strain / ds;
    const word = k >= .85 ? 'почти вся нагрузка дня — эта тренировка' : k >= .6 ? 'основную часть нагрузки дня дала эта тренировка'
      : 'тренировка — заметная часть дня, остальное — движение за день';
    return `<section class="card rise">${F.cap('Вклад в нагрузку дня', `<b class="num" style="color:var(--strain)">${num(w.strain, 1)}</b> из ${num(ds, 1)}`)}
      <div class="hv">${row('Тренировка', w.strain, 'var(--strain)')}${row('Весь день', ds, 'color-mix(in srgb, var(--strain) 45%, transparent)')}</div>
      <p class="small muted">По шкале 0–21: ${word}${others > 1 ? ` (тренировок за день: ${others})` : ''}. Нагрузка не складывается простым сложением — чем выше, тем труднее её набрать.</p></section>`;
  }

  F.screens.workout = (t0, d, id) => {
    const f = findWorkout(id);
    if (!f) return wHead('Тренировка', '') + `<div class="empty rise"><span class="e">${F.I.run}</span><b>Тренировка не найдена</b>
      Возможно, WHOOP её ещё не прислал или день сменился. Открой её заново из «Мой день».</div>
      <button class="btn tonal" data-a="actv-back">Назад</button>`;
    const { w, t } = f;
    const body = d.body || {}, maxHr = body.max_heart_rate || null;
    const end = endOf(w.start, w.minutes);
    let h = wHead(esc(w.sport || 'Тренировка'), `${esc(F.dateWord(t.day))}, ${F.dateLine(t.day)} · <span class="num">${esc(w.start || '')}${end ? ` – ${end}` : ''}</span>`);
    if (!w.scored) h += '<div class="note rise"><span>⏳</span><span>WHOOP ещё считает эту тренировку — цифры появятся чуть позже.</span></div>';
    h += `<section class="hero-ring rise">${F.ring((w.strain || 0) / 21, 'var(--strain)', `<b class="num">${F.cnt(w.strain, 1)}</b><span>нагрузка</span>`, 210)}
      <div class="hr-sub">${w.strain != null ? `${level(w.strain)} · шкала 0–21` : 'Нагрузка ещё не посчитана'}</div></section>`;
    h += hrCard(w, maxHr);
    const tiles = [tile('Длительность', w.minutes ? hm(w.minutes / 60) : '—', w.minutes ? 'ч:мин' : ''),
      tile('Калории', w.kcal ? big(w.kcal) : '—', 'ккал')];
    if (w.distance) tiles.push(tile('Дистанция', w.distance >= 1000 ? num(w.distance / 1000, 2) : Math.round(w.distance), w.distance >= 1000 ? 'км' : 'м'));
    h += `<div class="tiles actv-tiles rise">${tiles.join('')}</div>`;
    h += zonesCard(w, maxHr);
    h += shareCard(w, t, (t.workouts || []).length);
    const when = t.day === d.today.day ? 'сегодняшней' : `${F.dateLine(t.day)}`;
    const sport = (w.sport || 'тренировки').toLowerCase();
    h += hints([`Как восстановиться после ${t.day === d.today.day ? 'этой тренировки' : `тренировки ${when}`}?`,
      'Что съесть после тренировки?',
      w.strain != null ? `${w.sport || 'Тренировка'} ${F.dateLine(t.day)}: нагрузка ${num(w.strain, 1)}, пульс ${w.avg_hr || '—'}/${w.max_hr || '—'} — это хорошо?` : `Как тренироваться на ${sport} эффективнее?`]);
    return h;
  };

  F.actions.workout = (el, v) => {
    nav.prev = F.S.screen && F.S.screen !== 'workout' ? { screen: F.S.screen, arg: F.S.screenArg, y: window.scrollY, homeY: F.S.scrollY } : null;
    F.openScreen('workout', v);
    if (nav.prev) F.S.scrollY = nav.prev.homeY;  // «назад» с экрана нагрузки вернёт на главную туда же, где была
  };
  F.actions['actv-back'] = () => {
    const p = nav.prev;
    nav.prev = null;
    if (!p) return F.closeScreen();
    F.haptic('select');
    F.S.screen = p.screen; F.S.screenArg = p.arg;
    F.nav('pop', () => { F.S.popping = true; try { F.render(false); } finally { F.S.popping = false; } window.scrollTo(0, p.y || 0); });
  };

  // ---------- подсказки тренеру (WHOOP Coach) ----------
  function hints(qs) {
    qs = qs.filter(Boolean).slice(0, 3);
    if (!qs.length) return '';
    return `<section class="card actv-coach rise">${F.cap(`<span class="actv-spark">${spark}</span>Спросить в чате`, 'по твоим данным')}
      ${qs.map(q => `<button class="actv-q" data-a="actv-ask" data-v="${esc(q)}"><span>${esc(q)}</span>${chev}</button>`).join('')}</section>`;
  }
  F.actions['actv-ask'] = async (el, q) => {
    if (!q || F.S.asking) return;
    F.haptic('tap');
    F.go('coach');
    if (!F.S.history) await F.loadHistory();  // иначе история, пришедшая позже, затрёт вопрос
    F.ask(q);
  };
  const isToday = t => t.day === F.S.data.today.day;
  function sleepHints(t) {
    const s = t.sleep || {}, b = t.bedtime, qs = [];
    const perf = s.performance != null ? s.performance : s.need ? Math.round(s.hours / s.need * 100) : null;
    if (perf != null && perf < 85) qs.push(`Почему сон только ${perf}% и как это исправить?`);
    else if (perf != null) qs.push(`Сон ${perf}% — как удержать такой режим?`);
    qs.push(b && isToday(t) ? `Как лечь сегодня до ${b.bed}?` : 'Как ложиться раньше?');
    if (s.debt) qs.push(`Как вернуть недосып ${s.debt < 1 ? Math.round(s.debt * 60) + ' мин' : F.hHuman(s.debt)}?`);
    qs.push('Как увеличить глубокий сон?');
    return qs;
  }
  function recHints(t) {
    const r = t.recovery, b = t.base || {}, qs = [];
    if (r) qs.push(`Почему восстановление ${r.score}%?`);
    qs.push(isToday(t) ? 'Сколько тренироваться сегодня?' : 'Как восстановление влияет на тренировки?');
    if (r && b.hrv != null && r.hrv != null && r.hrv < b.hrv * .9) qs.push('Почему запас сил ниже нормы?');
    qs.push(r && r.zone === 'green' ? 'Готов ли я к спаррингу?' : 'Как быстрее восстановиться?');
    return qs;
  }
  function strainHints(t) {
    const c = t.cycle || {}, qs = [];
    qs.push(isToday(t) ? 'Сколько тренироваться сегодня?' : `Нагрузка ${c.strain != null ? num(c.strain, 1) : ''} ${F.dateLine(t.day)} — это нормально?`);
    qs.push('Как восстановиться после нагрузки?');
    qs.push('Не перетренировался ли я за неделю?');
    return qs;
  }

  // ---------- тренды 1Н / 1М / 6М в разборах ----------
  const RANGES = [[7, 'Неделя'], [30, 'Месяц'], [180, 'Полгода']];
  F.S.actvRange = F.S.actvRange || { sleep: 7, rec: 7, strain: 7 };
  const pending = new Set(), asked = new Set(), failed = new Set();
  const longest = F.longest;
  const wantOf = n => Math.min(180, n * 2);  // для сравнения нужен и прошлый такой же период (сервер отдаёт до 180 дней)
  function need(n) {
    const want = wantOf(n);
    if (longest().length >= want || asked.has(want)) return;  // каждый ряд спрашиваем один раз
    asked.add(want); pending.add(want);
    F.loadSeries(want).then(() => {
      pending.delete(want);
      if (!F.S.series[want]) failed.add(want);
      ['sleep', 'rec', 'strain'].forEach(k => { if (F.S.screen === k) redraw(k); });
    });
  }
  // какие графики показывать в разборе: те же, что во «Здоровье» (F.CHARTS), с понятными заголовками и выводом
  const KINDS = { sleep: ['sleep'], rec: ['rec', 'hrv', 'rhr'], strain: ['strain'] };

  function trendCard(kind, anim = true) {
    const n = F.S.actvRange[kind] || 7;
    const all = longest();
    const seg = `<div class="seg actv-seg" role="tablist" aria-label="Период">${RANGES.map(([k, l]) => `<button class="${k === n ? 'on' : ''}" data-a="actv-range" data-v="${kind}:${k}" role="tab">${l}</button>`).join('')}</div>`;
    const span = len => len < n ? `есть только ${len} ${plural(len, 'день', 'дня', 'дней')}` : '';
    const head = len => `<section class="card actv-trend${anim ? ' rise' : ''}" id="actv-trend-${kind}">${F.cap('Как менялось', span(len))}${seg}`;
    need(n);  // прошлый период для сравнения подтянется в фоне
    const want = wantOf(n);
    // данных меньше периода, а больше сервер не дал (WHOOP носят недавно) — рисуем, что есть
    if (all.length < n && !(asked.has(want) && !pending.has(want) && !failed.has(want))) {
      if (failed.has(want)) return `${head(n)}<div class="chart-empty">График не загрузился. <button class="link" data-a="actv-retry" data-v="${kind}">Повторить</button></div></section>`;
      return `${head(n)}<div class="sk" style="height:22px;width:60%"></div><div class="sk" style="height:34px;width:46%;margin-top:10px"></div><div class="sk" style="height:${116 + 58}px;margin-top:14px"></div></section>`;
    }
    const rows = all.slice(-n), prev = all.length >= 2 * n ? all.slice(-2 * n, -n) : null;
    const allBtn = F.screens.trends ? `<button class="link tr-all" data-a="tr-open" data-v="${{ sleep: 'sleep_hours', rec: 'recovery', strain: 'strain' }[kind]}">Все показатели</button>` : '';
    return `${head(rows.length)}${KINDS[kind].map(k => F.CHARTS[k](rows, prev, n)).join('')}${allBtn}</section>`;
  }
  // перерисовать только карточку тренда, без всего экрана
  function redraw(kind) {
    const el = document.getElementById(`actv-trend-${kind}`);
    if (!el) return F.render(false);
    F.morph(el, trendCard(kind, false), true);
    F.animate();
  }
  F.actions['actv-range'] = (el, v) => {
    const [kind, n] = v.split(':');
    if (F.S.actvRange[kind] === Number(n)) return;
    F.S.actvRange[kind] = Number(n);
    F.haptic('select');
    redraw(kind);
  };
  F.actions['actv-retry'] = (el, kind) => { failed.forEach(w => asked.delete(w)); failed.clear(); redraw(kind); };

  F.slots.sleep.push(t => trendCard('sleep') + (t.sleep ? hints(sleepHints(t)) : ''));
  F.slots.rec.push(t => trendCard('rec') + (t.recovery ? hints(recHints(t)) : ''));
  F.slots.strain.push(t => trendCard('strain') + hints(strainHints(t)));
});
