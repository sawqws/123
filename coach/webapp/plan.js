/* plan — «Мой план» как WHOOP My Plan + Sleep Planner: цели недели пн–вс с точками дней и планер сна.
   Данные — F.S.data.ext.plan (с сервера, f_plan.py), правки — POST /api/plan/save. Подключение: window.FormaModules (см. app.js). */
(window.FormaModules = window.FormaModules || []).push(F => {
  const { esc, svgi, cap } = F;
  const ICON = svgi('<circle cx="12" cy="12" r="8.6"/><circle cx="12" cy="12" r="4.6"/><circle cx="12" cy="12" r="1" fill="currentColor"/>');
  const CLOCK = svgi('<circle cx="12" cy="12" r="8.6"/><path d="M12 7.2V12l3.2 2"/>');
  const GAUGE = svgi('<path d="M4.2 16.5a8.6 8.6 0 1 1 15.6 0"/><path d="M12 13.5l4-4.5"/>');
  const LOOK = {  // иконка и цвет цели
    sleep: [F.I.moon, 'var(--sleep)'], workouts: [F.I.bolt, 'var(--strain)'], strain: [GAUGE, 'var(--strain)'],
    bedtime: [CLOCK, 'var(--rec-blue)'], water: [F.I.drop, 'var(--water)'], protein: [F.I.food, 'var(--food)'] };
  const SHORT = { strain: 'Нагрузка', bedtime: 'Отбой' };  // на главной места мало
  const WDL = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс'];
  const STATE = { done: 'выполнено', miss: 'нет', today: 'сегодня', future: 'впереди', nodata: 'нет данных' };
  const plan = () => F.S.data && F.S.data.ext && F.S.data.ext.plan;
  const h2 = h => { const m = Math.round(h * 60); return `${Math.floor(m / 60)} ч${m % 60 ? ` ${String(m % 60).padStart(2, '0')} мин` : ''}`; };

  // счётчик цели: сон — среднее за неделю, остальное — «3/4»
  const counter = g => g.value !== null && g.value !== undefined
    ? (g.avg == null ? '—' : `${g.kind === 'pct' ? Math.round(g.avg) : F.num(g.avg, 1)}<small>${g.kind === 'pct' ? '%' : ' ч'}</small>`)
    : `${g.count}<small>/${g.need}</small>`;
  const dots = (g, labels) => `<span class="pl-dots" style="--c:${LOOK[g.id][1]}">${g.dots.map((s, i) =>
    `<span class="pl-d"><i class="pl-${s}" title="${WDL[i]}: ${STATE[s]}"></i>${labels ? `<em class="${s === 'today' || (plan() && plan().week[i] === plan().today) ? 'on' : ''}">${WDL[i]}</em>` : ''}</span>`).join('')}</span>`;
  const statusText = g => {
    if (g.status === 'nodata') return g.id === 'water' ? 'Записей воды за неделю нет — отмечай в «Мой дневник»' : 'Еды за неделю не записано — добавь через «＋»';
    if (g.status === 'done') return g.value != null ? 'Цель держится' : 'Цель недели выполнена';
    if (g.value != null) return g.status === 'behind' ? 'Пока ниже цели — подтянет ранний отбой' : 'Ночей с данными пока нет';
    const d = `${g.left} ${F.plural(g.left, 'день', 'дня', 'дней')}`;
    return g.status === 'ok' ? `Осталось ${g.id === 'workouts' ? `${g.left} ${F.plural(g.left, 'тренировка', 'тренировки', 'тренировок')}` : d} — успеваешь`
      : `Осталось ${g.id === 'workouts' ? g.left : d} — на этой неделе уже не успеть`;
  };

  // ---------- главная: карточка «Мой план» после «Мой день» ----------
  F.slots.homeAfterMyDay.push((t, isToday) => {
    const p = plan();
    if (!isToday || !p) return '';
    const on = p.goals.filter(g => g.on);
    const rows = on.map(g => `<div class="pl-row"><span class="pl-t">${esc(SHORT[g.id] || g.title)}</span>${dots(g)}
      <b class="pl-c num ${g.status}">${counter(g)}</b></div>`).join('');
    return `<div class="sec rise">${cap('Мой план', `<b class="num" style="color:var(--teal)">${p.met}</b>из ${p.total} целей недели`)}
      <button class="card plan-mini tap" data-a="plan-open" aria-label="Открыть «Мой план»">
        ${rows || '<p class="muted small">Целей недели нет — включи их в «Изменить цели».</p>'}
        <span class="pl-more">Цели, точки недели и планер сна<span class="chev"></span></span>
      </button></div>`;
  });

  // ---------- «＋»: строка «Мой план» ----------
  F.slots.plus.push(row => row('plan-open', '', ICON, 'var(--teal)', 'Мой план', 'цели недели и во сколько лечь'));

  // ---------- экран «Мой план» ----------
  F.screens.plan = () => {
    const p = plan();
    if (!p) {
      if (!F.S.planLoading) load();
      return F.dHead('Мой план', 'цели недели и сон', 'var(--teal)') + `<section class="card">${F.skel(4)}</section>`;
    }
    const a = F.parseDay(p.from), b = F.parseDay(p.to);
    const range = a.getMonth() === b.getMonth() ? `${a.getDate()}–${b.getDate()} ${F.MON[b.getMonth()]}` : `${F.dateLine(p.from)} – ${F.dateLine(p.to)}`;
    const on = p.goals.filter(g => g.on);
    const leftDays = 7 - p.week.indexOf(p.today);
    let h = F.dHead('Мой план', `Неделя ${range}`, 'var(--teal)');
    h += `<section class="card pl-hero rise">${F.ring(p.total ? p.met / p.total : 0, 'var(--teal)', `<b class="num">${F.cnt(p.met)}<small>/${p.total}</small></b>`, 96)}
      <div class="pl-hero-t"><b>${p.met === p.total && p.total ? 'Все цели недели выполнены' : `${p.met} из ${p.total} ${F.plural(p.total, 'цели', 'целей', 'целей')} выполнено`}</b>
        <span class="muted small">${leftDays > 1 ? `До конца недели ${leftDays} ${F.plural(leftDays, 'день', 'дня', 'дней')} вместе с сегодня` : 'Сегодня последний день недели'}</span></div></section>`;
    h += `<div class="sec rise">${cap('Цели недели', '<button class="link" data-a="plan-edit">Изменить цели</button>')}`;
    h += on.length ? on.map(goalCard).join('') : `<section class="card"><p class="muted small">Все цели выключены.</p><button class="btn tonal" data-a="plan-edit">Выбрать цели</button></section>`;
    h += `</div>`;
    h += sleepPlanner(p.planner);
    h += `<div class="sec rise">${cap('Мои цели', '<button class="link" data-a="profile">В профиле</button>')}
      <div class="list">${p.texts.length ? p.texts.map(x => `<div class="row"><span class="ic" style="color:var(--teal)">${ICON}</span><span class="main"><div class="title wrap">${esc(x.text)}</div></span></div>`).join('')
        : `<button class="row tap" data-a="profile"><span class="ic" style="color:var(--teal)">${F.I.plus}</span><span class="main"><div class="title">Добавить свою цель</div><div class="meta">например «ложиться до 23:00» — тренер видит их в каждом ответе</div></span><span class="end chev"></span></button>`}</div></div>`;
    h += `<p class="foot pl-foot">Точка дня у сна и отбоя — ночь, после которой начался этот день. Цели, нормы и время отбоя — наш расчёт по данным WHOOP, а не оценка WHOOP.</p>`;
    return h;
  };

  function goalCard(g) {
    const [ic, col] = LOOK[g.id];
    return `<section class="card pl-goal" style="--c:${col}"><div class="pl-gh"><span class="pl-ic">${ic}</span>
        <span class="pl-gm"><b>${esc(g.title)}</b><span class="muted">${esc(g.target)}</span></span>
        <span class="pl-gc num ${g.status}">${counter(g)}</span></div>
      ${dots(g, true)}
      <p class="small pl-st ${g.status}">${statusText(g)}${g.note && g.status !== 'nodata' ? ` <span class="muted">· ${esc(g.note)}</span>` : ''}</p></section>`;
  }

  // планер сна как WHOOP Sleep Planner: три режима от потребности сна и время подъёма → во сколько лечь
  function sleepPlanner(pl) {
    const sel = pl.options.find(o => o.mode === pl.mode);
    return `<div class="sec rise">${cap('Планер сна', 'сегодня')}<section class="card pl-sleep">
      <div class="pl-bed"><span class="tk">Лечь в</span><b class="num">${esc(pl.bed)}</b>
        <span class="muted small">сон ${h2(sel.hours)} + ${pl.fall} мин заснуть · подъём ${esc(pl.wake)}</span></div>
      <div class="pl-opts" role="radiogroup" aria-label="Сколько спать">${pl.options.map(o => `<button class="pl-opt ${o.mode === pl.mode ? 'on' : ''}" role="radio" aria-checked="${o.mode === pl.mode}" data-a="plan-mode" data-v="${o.mode}">
          <span class="pl-on"></span><span class="pl-om"><b>${esc(o.name)}</b><span>${o.pct}% · ${h2(o.hours)}</span></span><b class="num">${esc(o.bed)}</b></button>`).join('')}</div>
      <label class="pl-wake"><span class="pl-wk"><b>Подъём</b><span class="muted">${pl.wake_auto ? 'как обычно по WHOOP' : 'своё время'}</span></span>
        <input id="plan-wake" type="time" value="${esc(pl.wake)}" aria-label="Время подъёма"></label>
      ${!pl.wake_auto ? `<button class="link pl-reset" data-a="plan-wake-auto">Вернуть обычный подъём${pl.usual_wake ? ` (${esc(pl.usual_wake)})` : ''}</button>` : ''}
      <p class="small muted">Нужно сна: <b class="num">${h2(pl.need)}</b> — ${esc(pl.need_from)}. «Пик» — вся потребность, «Норма» — 85%, «Минимум» — 70%.</p>
    </section></div>`;
  }

  // ---------- сервер ----------
  async function load() {
    F.S.planLoading = true;
    try { const r = await F.api('/api/plan'); if (r && r.goals) { F.S.data.ext = F.S.data.ext || {}; F.S.data.ext.plan = r; } } catch (e) { F.fail(e); }
    F.S.planLoading = false;
    if (F.S.screen === 'plan') F.render(false);
  }
  async function saveP(body, quiet) {
    try {
      const r = await F.api('/api/plan/save', body);
      if (!r || !r.goals) throw new Error('Не сохранилось — попробуй ещё раз');
      F.S.data.ext.plan = r;
      F.haptic('success');
      if (!quiet) F.toast('План сохранён', 'ok');
      F.render(false);
      return true;
    } catch (e) { F.fail(e); return false; }
  }

  F.actions['plan-open'] = () => F.openScreen('plan');
  F.actions['plan-mode'] = (el, v) => {
    const p = plan();
    if (!p || p.planner.mode === v) return;
    F.haptic('select');
    // сразу показываем выбор, сервер пересчитает и запомнит
    const o = p.planner.options.find(x => x.mode === v);
    Object.assign(p.planner, { mode: v, bed: o.bed, hours: o.hours });
    F.render(false);
    saveP({ sleep_mode: v }, true).then(ok => { if (!ok) load(); });  // не сохранилось — вернуть как на сервере
  };
  F.actions['plan-wake-auto'] = () => saveP({ wake: null }, true);
  F.actions['plan-edit'] = () => editSheet();
  document.addEventListener('change', ev => {
    if (ev.target.id === 'plan-wake' && ev.target.value) saveP({ wake: ev.target.value }, true);
  });

  // ---------- лист «Изменить цели» ----------
  const days = n => F.plural(n, 'день', 'дня', 'дней');
  const STEP = { workouts: [1, 14, 1], strain: [1, 7, 1], bedtime: [1, 7, 1], water: [1, 7, 1], protein: [1, 7, 1] };
  function editSheet() {
    const p = plan();
    if (!p) return;
    F.haptic('tap');
    const G = Object.fromEntries(p.goals.map(g => [g.id, g]));
    // что правим: настройки с сервера (у тренировок без своего числа — то, что посчитано по профилю/истории)
    const cfg = JSON.parse(JSON.stringify(p.config));
    cfg.workouts.n = cfg.workouts.n || G.workouts.need;
    const fmt = (k, v) => k === 'sleep' ? (cfg.sleep.kind === 'pct' ? `${v}%` : `${F.num(v, v % 1 ? 1 : 0)} ч`) : v;
    const stepper = (k, v, unit) => `<div class="pl-step" data-k="${k}"><button type="button" data-d="-1" aria-label="Меньше">−</button>
      <b class="num">${fmt(k, v)}</b><button type="button" data-d="1" aria-label="Больше">+</button>${unit ? `<span class="muted">${unit}</span>` : ''}</div>`;
    const block = (k, title, sub, body) => `<div class="pl-eg ${cfg[k].on ? '' : 'off'}" data-g="${k}" style="--c:${LOOK[k][1]}">
      <div class="pl-egh"><span class="pl-ic">${LOOK[k][0]}</span><span class="pl-gm"><b>${title}</b><span class="muted">${sub}</span></span>
        <button type="button" class="switch ${cfg[k].on ? 'on' : ''}" data-sw="${k}" aria-pressed="${cfg[k].on}" aria-label="${title}"></button></div>
      <div class="pl-egb">${body}</div></div>`;
    const html = () => `<h2>Цели недели</h2><p class="small muted pl-lead">Выбери, что считать, и сколько раз за неделю — с понедельника по воскресенье.</p>
      ${block('sleep', 'Сон', 'в среднем за ночь', `<div class="seg" data-kind>${[['pct', 'Выполнение, %'], ['hours', 'Часы']].map(([k, t]) => `<button type="button" class="${cfg.sleep.kind === k ? 'on' : ''}" data-v="${k}">${t}</button>`).join('')}</div>${stepper('sleep', cfg.sleep.value, 'не меньше')}`)}
      ${block('workouts', 'Тренировки', `в неделю${G.workouts.auto ? ` · обычно ${G.workouts.auto}` : ''}`, stepper('workouts', cfg.workouts.n, F.plural(cfg.workouts.n, 'тренировка', 'тренировки', 'тренировок')))}
      ${block('strain', 'Нагрузка в цели', 'дней, когда нагрузка попала в цель дня', stepper('strain', cfg.strain.n, days(cfg.strain.n)))}
      ${block('bedtime', 'Режим отбоя', 'лечь ±30 мин к своему времени', `${stepper('bedtime', cfg.bedtime.n, days(cfg.bedtime.n))}
        <label class="pl-wake pl-bt"><span class="pl-wk"><b>Отбой в</b><span class="muted">пусто — по планеру сна (${esc(p.planner.bed)})</span></span>
          <input type="time" data-bt value="${esc(cfg.bedtime.time || '')}" aria-label="Цель отбоя"></label>`)}
      ${block('water', 'Вода', 'дней с выполненной нормой', stepper('water', cfg.water.n, days(cfg.water.n)))}
      ${block('protein', 'Белок', 'дней, когда белка по норме (по записанной еде)', stepper('protein', cfg.protein.n, days(cfg.protein.n)))}
      <button class="btn primary" type="button" data-save>Сохранить</button>`;
    const el = F.sheet(html());
    const redraw = () => { const x = el.querySelector('.sheet-x'), y = el.scrollTop; el.innerHTML = html(); if (x) el.prepend(x); el.scrollTop = y; };
    el.addEventListener('click', async ev => {
      const sw = ev.target.closest('[data-sw]'), st = ev.target.closest('.pl-step button'), kd = ev.target.closest('[data-kind] button');
      if (sw) { const k = sw.dataset.sw; cfg[k].on = !cfg[k].on; F.haptic('select'); redraw(); }
      else if (kd) {
        if (cfg.sleep.kind === kd.dataset.v) return;
        cfg.sleep.kind = kd.dataset.v; cfg.sleep.value = kd.dataset.v === 'pct' ? 85 : 8.5; F.haptic('select'); redraw();
      } else if (st) {
        const k = st.closest('.pl-step').dataset.k, d = Number(st.dataset.d);
        if (k === 'sleep') {
          const [lo, hi, s] = cfg.sleep.kind === 'pct' ? [50, 100, 5] : [5, 12, .5];
          cfg.sleep.value = Math.max(lo, Math.min(hi, Math.round((cfg.sleep.value + d * s) * 10) / 10));
        } else { const [lo, hi, s] = STEP[k]; cfg[k].n = Math.max(lo, Math.min(hi, cfg[k].n + d * s)); }
        F.haptic('select'); redraw();
      } else if (ev.target.closest('[data-save]')) {
        const bt = el.querySelector('[data-bt]');
        const goals = JSON.parse(JSON.stringify(cfg));
        goals.bedtime.time = bt && bt.value ? bt.value : null;
        await F.busy(ev.target.closest('[data-save]'), async () => { if (await saveP({ goals })) el.close(); });
      }
    });
    el.addEventListener('change', ev => { if (ev.target.matches('[data-bt]')) cfg.bedtime.time = ev.target.value; });
  }
});
