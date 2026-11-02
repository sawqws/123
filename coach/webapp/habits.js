/* habits — «Привычки и цели»: карточка на главной (кольцо дня, отметки в одно касание), экран «Привычки» (сегодня по
   времени дня, цели на день, неделя-сетка, серии), лист привычки и «Добавить списком».
   Данные — F.S.hb (полный ответ /api/habits) или F.S.data.ext.habits (кратко из /api/all). Сервер — coach/f_habits.py.
   Отметка меняет только свою строку и кольцо (без перерисовки экрана); запросы — по очереди (сервер читает и пишет день). */
(window.FormaModules = window.FormaModules || []).push(F => {
  const { esc, svgi, cap } = F;
  const IC = {
    check: svgi('<path d="M5 12.5l4.5 4.5L19 7.5"/>'),
    water: svgi('<path d="M12 3.2s6 6.4 6 10.8a6 6 0 0 1-12 0c0-4.4 6-10.8 6-10.8z"/>'),
    book: svgi('<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z"/><path d="M4 20.5A2.5 2.5 0 0 0 6.5 21H20"/>'),
    walk: svgi('<path d="M8 3.5c1.7 0 2.5 1.8 2.5 4s-.8 4.5-2.5 4.5S5.5 9.7 5.5 7.5 6.3 3.5 8 3.5zM6 14.5h4l-.3 2.3a2 2 0 0 1-3.4 0z"/><path d="M16 7.5c1.7 0 2.5 1.8 2.5 4s-.8 4.5-2.5 4.5-2.5-2.3-2.5-4.5.8-4 2.5-4zM14 18.5h4l-.3 1.6a2 2 0 0 1-3.4 0z"/>'),
    run: F.I.run,
    sleep: F.I.bed,
    food: svgi('<path d="M12 7.5c-1.5-1.2-5.5-1.6-6.5 2.5-.9 3.8 1.5 9.5 4.3 9.5 1 0 1.4-.5 2.2-.5s1.2.5 2.2.5c2.8 0 5.2-5.7 4.3-9.5-1-4.1-5-3.7-6.5-2.5z"/><path d="M12 7.5c0-2 1-3.5 3-4"/>'),
    pill: svgi('<path d="M10.6 4.9a4.2 4.2 0 0 1 6 6l-6.7 6.7a4.2 4.2 0 0 1-6-6z"/><path d="M7.2 8.3l6 6"/>'),
    mind: svgi('<circle cx="12" cy="5.5" r="2.2"/><path d="M4 19.5c2.5-1 5-1.4 8-1.4s5.5.4 8 1.4M6.5 15.5L10 10.5h4l3.5 5M10 10.5l-.4 7.3M14 10.5l.4 7.3"/>'),
    teeth: svgi('<path d="M7.5 4C5.2 4 4 5.8 4 8.2c0 3 1.6 4.3 2 7.3.3 2.6.9 4.5 2.1 4.5 1.6 0 1.4-5 3.9-5s2.3 5 3.9 5c1.2 0 1.8-1.9 2.1-4.5.4-3 2-4.3 2-7.3C20 5.8 18.8 4 16.5 4c-1.9 0-2.6 1-4.5 1S9.4 4 7.5 4z"/>'),
    phone: svgi('<rect x="7" y="3" width="10" height="18" rx="2.5"/><path d="M11 17.5h2"/>'),
    sun: svgi('<circle cx="12" cy="12" r="4"/><path d="M12 2.8v2M12 19.2v2M2.8 12h2M19.2 12h2M5.5 5.5l1.4 1.4M17.1 17.1l1.4 1.4M5.5 18.5l1.4-1.4M17.1 6.9l1.4-1.4"/>'),
    dumbbell: F.I.weight,
    pen: svgi('<path d="M4 20l1-4.5L15.5 5a2.1 2.1 0 0 1 3 3L8 18.5z"/><path d="M13.5 7l3 3"/>'),
    heart: svgi('<path d="M12 20s-7.5-4.6-7.5-10.2A4.3 4.3 0 0 1 12 7.2a4.3 4.3 0 0 1 7.5 2.6C19.5 15.4 12 20 12 20z"/>'),
    lang: svgi('<path d="M4 5.5h16v11H9.5L5 20.5v-4H4z"/><path d="M9 13.5l3-6 3 6M10 11.5h4"/>'),
    music: svgi('<path d="M9 18V5.5l10-2V16"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>'),
    leaf: svgi('<path d="M5 19c0-8 5-13 14-14 0 9-5 14-13 14z"/><path d="M5 19l7-7"/>'),
    moon: F.I.moon,
    clean: F.I.sparkle,
  };
  const ICONS = Object.keys(IC);
  const I = {
    list: svgi('<path d="M9 6.5h11M9 12h11M9 17.5h11"/><circle cx="4.6" cy="6.5" r="1"/><circle cx="4.6" cy="12" r="1"/><circle cx="4.6" cy="17.5" r="1"/>'),
    target: svgi('<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r=".8"/>'),
    move: svgi('<path d="M4 12h13M12 6l6 6-6 6"/><path d="M20.5 5v14"/>'),
    pause: svgi('<circle cx="12" cy="12" r="8.5"/><path d="M10 9v6M14 9v6"/>'),
    box: svgi('<path d="M4 7.5h16v3H4zM5.5 10.5V19h13v-8.5M10 14h4"/>'),
    x: svgi('<path d="M7 7l10 10M17 7L7 17"/>'),
  };
  const SLOTS = [['утро', 'Утро'], ['день', 'День'], ['вечер', 'Вечер'], ['', 'В течение дня']];
  const WD = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
  const TW = { утро: 'утром', день: 'днём', вечер: 'вечером' };
  const HOME_MAX = 6;

  // ---------- данные ----------
  const ext = () => F.S.data && F.S.data.ext && F.S.data.ext.habits;
  const data = () => F.S.hb || ext() || null;
  const today = () => { const d = data(); return d && d.today; };
  function put(r) {
    if (!r || !r.today) return;
    F.S.hb = { ...(F.S.hb || {}), ...r };
    if (F.S.data) {
      F.S.data.ext = F.S.data.ext || {};
      const e = F.S.data.ext.habits || {};
      F.S.data.ext.habits = { ...e, today: r.today, week: r.week || e.week, has: r.habits ? r.habits.length > 0 : e.has };
    }
  }
  // запросы по очереди: отметки подряд не теряются; ответ применяем, только когда очередь пуста (иначе он устарел)
  let queue = Promise.resolve(), pending = 0;
  function send(path, body, apply = true) {
    pending++;
    const p = queue.then(() => F.api(path, body));
    queue = p.catch(() => {});
    return p.then(r => { pending--; if (apply && !pending) put(r); return r; }, e => { pending--; throw e; });
  }
  async function load(screen) {
    if (F.S.hbLoading) return;
    F.S.hbLoading = true;
    try { put(await F.api('/api/habits', {})); F.S.hbFail = F.S.hbErr = false; } catch (e) { F.S.hbFail = !data(); if (screen) { F.S.hbErr = true; F.fail(e); } }
    F.S.hbLoading = false;
    if (F.S.screen === 'habits' || (!F.S.screen && F.S.tab === 'today')) F.render(false);
  }
  // тихое обновление раз в 2 минуты (/api/all) — берём свежее «сегодня», если сами ничего не отправляем
  F.onData.push(d => {
    const e = d.ext && d.ext.habits;
    if (e && e.today && F.S.hb && !pending) F.S.hb = { ...F.S.hb, today: e.today, week: e.week || F.S.hb.week };
    if (!e && !F.S.hbTried) { F.S.hbTried = true; load(false); }
  });

  // отклик: Telegram — HapticFeedback, сайт на телефоне — короткая вибрация
  const vib = kind => {
    F.haptic(kind);
    const tgh = F.tg && F.tg.HapticFeedback && F.tg.initData;
    if (!tgh && navigator.vibrate) { try { navigator.vibrate(kind === 'success' ? [10, 70, 18] : kind === 'error' ? [30, 40, 30] : 8); } catch (e) { /* нет вибрации */ } }
  };

  // ---------- подписи ----------
  const fmt = n => F.big(n);
  const unit = it => it.unit ? ` ${esc(it.unit)}` : '';
  function sched(h) {
    if (h.weekly) return `${h.weekly} ${F.plural(h.weekly, 'раз', 'раза', 'раз')} в неделю`;
    const d = h.days || [];
    if (d.length === 7) return 'каждый день';
    if (d.join() === '0,1,2,3,4') return 'по будням';
    if (d.join() === '5,6') return 'по выходным';
    return d.map(i => WD[i].toLowerCase()).join(', ');
  }
  const when = t => !t ? '' : TW[t] || `в ${t}`;
  function meta(it) {
    const out = [];
    if (it.skip) out.push('пропуск — серия не прервётся');
    else if (it.kind === 'count') out.push(`${fmt(it.value)} из ${fmt(it.target)}${unit(it)}`);
    if (!it.skip && it.weekly) out.push(it.week_done >= it.week_need && !it.done ? 'норма недели ✓' : `${it.week_done} из ${it.week_need} за неделю`);
    if (it.time && /\d/.test(it.time)) out.push(it.time);
    return out.join(' · ');
  }
  const streak = (n, u) => n >= 2 ? `<span class="hb-st num" title="Серия">${F.I.flame}${n}${u === 'week' ? '<small>нед</small>' : ''}</span>` : '';

  // ---------- строка привычки ----------
  const pOf = it => it.kind === 'count' ? Math.min(100, Math.round((it.value || 0) / (it.target || 1) * 100)) : it.done ? 100 : 0;
  function row(it) {
    const p = pOf(it), m = meta(it);
    return `<div class="hb-row${it.done ? ' on' : ''}${it.skip ? ' skip' : ''}${it.kind === 'count' ? ' cnt' : ''}" data-hid="${esc(it.id)}">
      <button type="button" class="hb-ck" data-a="hb-tap" data-v="${esc(it.id)}" aria-pressed="${!!it.done}" aria-label="${esc(it.title)}: ${it.done ? 'выполнено' : 'отметить'}">
        <svg class="hb-c" viewBox="0 0 36 36" aria-hidden="true"><circle class="hb-trk" cx="18" cy="18" r="16"/><circle class="hb-arc" cx="18" cy="18" r="16" pathLength="100" style="stroke-dashoffset:${100 - p}"/></svg>
        <i class="hb-fill" aria-hidden="true"></i>
        <svg class="hb-tick" viewBox="0 0 24 24" aria-hidden="true"><path d="M6.5 12.5l3.8 3.8L17.5 8.8" pathLength="1"/></svg></button>
      <button type="button" class="hb-main" data-a="hb-item" data-v="${esc(it.id)}">
        <span class="hb-t"><i class="hb-ic">${IC[it.icon] || IC.check}</i><span class="hb-tt">${esc(it.title)}</span></span>
        <span class="hb-m" ${m ? '' : 'hidden'}>${esc(m)}</span></button>
      ${streak(it.streak, it.streak_unit)}
      ${it.kind === 'count' ? `<button type="button" class="hb-plus num" data-a="hb-plus" data-v="${esc(it.id)}" aria-label="Добавить ${fmt(it.step)}">+${fmt(it.step)}</button>` : ''}
    </div>`;
  }
  // обновить строку на месте: класс, дуга, подпись — переходы CSS дают плавность
  function patchRow(it, pop) {
    document.querySelectorAll(`.hb-row[data-hid="${CSS.escape(it.id)}"]`).forEach(el => {
      el.classList.toggle('on', !!it.done);
      el.classList.toggle('skip', !!it.skip);
      const b = el.querySelector('.hb-ck');
      b.setAttribute('aria-pressed', !!it.done);
      b.setAttribute('aria-label', `${it.title}: ${it.done ? 'выполнено' : 'отметить'}`);
      el.querySelector('.hb-arc').style.strokeDashoffset = 100 - pOf(it);
      const m = el.querySelector('.hb-m'), t = meta(it);
      m.textContent = t; m.hidden = !t;
      const st = el.querySelector('.hb-st'), html = streak(it.streak, it.streak_unit);
      if (st && !html) st.remove();
      else if (html) { if (st) st.outerHTML = html; else el.querySelector('.hb-main').insertAdjacentHTML('afterend', html); }
      if (pop) { b.classList.remove('pop'); void b.offsetWidth; b.classList.add('pop'); }
    });
  }

  // ---------- кольцо дня ----------
  function sum(t, week) {
    const closed = t.total > 0 && t.done >= t.total, p = t.total ? Math.round(t.done / t.total * 100) : 0;
    return `<div class="hb-sum${closed ? ' closed' : ''}" data-hb-sum>
      <div class="hb-ring"><svg viewBox="0 0 100 100" aria-hidden="true"><circle class="hb-rtrk" cx="50" cy="50" r="44"/>
        <circle class="hb-rarc" cx="50" cy="50" r="44" pathLength="100" style="stroke-dashoffset:${100 - p}"/></svg>
        <div class="hb-rin"><b class="num" data-hb-done>${t.done}</b><span>из <span class="num" data-hb-total>${t.total}</span></span></div>
        <svg class="hb-rok" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 12.5l4 4L18 8.5" pathLength="1"/></svg></div>
      <div class="hb-sum-t"><b data-hb-head>${esc(head(t))}</b><span class="hb-sub" data-hb-sub>${esc(sub(t))}</span>
        ${week && week.length ? `<div class="hb-wk" aria-label="Неделя">${week.map((w, i) => `<span class="hb-wd${i === week.length - 1 ? ' now' : ''}"><i style="--h:${w.pct == null ? 0 : w.pct / 100}"${w.pct == null ? ' class="none"' : ''}></i><small>${WD[w.wd][0]}</small></span>`).join('')}</div>` : ''}</div>
    </div>`;
  }
  const head = t => !t.total ? 'Ничего на сегодня' : t.done >= t.total ? 'День закрыт' : `${t.done} из ${t.total} выполнено`;
  function sub(t) {
    if (!t.total) return 'Добавь привычку или цель на день';
    if (t.done >= t.total) return 'Всё сделано — так держать';
    const left = t.total - t.done;
    return `Осталось ${left} ${F.plural(left, 'дело', 'дела', 'дел')}`;
  }
  // пересчёт «N из M» по строкам (сразу, до ответа сервера)
  function recount(t) {
    const its = t.items || [], gs = t.goals || [];
    t.habits_done = its.filter(x => x.due && x.done).length; t.habits_total = its.filter(x => x.due).length;
    t.goals_done = gs.filter(g => g.done).length; t.goals_total = gs.length;
    t.done = t.habits_done + t.goals_done; t.total = t.habits_total + t.goals_total;
  }
  function patchSum(celebrate) {
    const t = today(); if (!t) return;
    const closed = t.total > 0 && t.done >= t.total, p = t.total ? Math.round(t.done / t.total * 100) : 0;
    document.querySelectorAll('[data-hb-sum]').forEach(el => {
      const was = el.classList.contains('closed');
      el.classList.toggle('closed', closed);
      el.querySelector('.hb-rarc').style.strokeDashoffset = 100 - p;
      el.querySelector('[data-hb-done]').textContent = t.done;
      el.querySelector('[data-hb-total]').textContent = t.total;
      el.querySelector('[data-hb-head]').textContent = head(t);
      el.querySelector('[data-hb-sub]').textContent = sub(t);
      const bar = el.querySelector('.hb-wd.now i');
      if (bar) { bar.style.setProperty('--h', p / 100); bar.classList.toggle('none', !t.total); }
      if (closed && !was && celebrate) { el.classList.remove('closing'); void el.offsetWidth; el.classList.add('closing'); setTimeout(() => el.classList.remove('closing'), 1600); }
    });
    if (closed && celebrate && !F.S.hbClosedToast) { F.S.hbClosedToast = true; setTimeout(() => vib('success'), 120); }
    if (!closed) F.S.hbClosedToast = false;
    document.querySelectorAll('[data-hb-gmove]').forEach(b => { b.hidden = !(t.goals || []).some(g => !g.done); });
  }

  // ---------- цели на день ----------
  const goalRow = g => `<div class="hb-row hb-goal${g.done ? ' on' : ''}" data-gid="${esc(g.id)}">
      <button type="button" class="hb-ck" data-a="hb-goal" data-v="${esc(g.id)}" aria-pressed="${!!g.done}" aria-label="${esc(g.text)}">
        <svg class="hb-c" viewBox="0 0 36 36" aria-hidden="true"><circle class="hb-trk" cx="18" cy="18" r="16"/></svg><i class="hb-fill" aria-hidden="true"></i>
        <svg class="hb-tick" viewBox="0 0 24 24" aria-hidden="true"><path d="M6.5 12.5l3.8 3.8L17.5 8.8" pathLength="1"/></svg></button>
      <span class="hb-main"><span class="hb-t"><span class="hb-tt">${esc(g.text)}</span></span></span>
      <button type="button" class="hb-del" data-a="hb-gdel" data-v="${esc(g.id)}" aria-label="Убрать цель">${I.x}</button></div>`;
  const goalsList = (gs, max) => (max ? gs.slice(0, max) : gs).map(goalRow).join('');
  const goalInput = ph => `<div class="hb-gadd"><span class="hb-gplus" aria-hidden="true">${F.I.plus}</span>
      <input class="hb-gin" type="text" maxlength="80" enterkeyhint="done" placeholder="${ph}" aria-label="Новая цель на сегодня"></div>`;
  function redrawGoals() {
    const t = today(); if (!t) return;
    document.querySelectorAll('[data-hb-goals]').forEach(el => { el.innerHTML = goalsList(t.goals || [], Number(el.dataset.hbGoals) || 0); });
    patchSum(true);
  }

  // ---------- карточка на главной ----------
  F.slots.homeAfterMyDay.push((t0, isToday) => {
    if (!isToday) return '';
    const d = data(), t = d && d.today;
    if (!t) return F.S.hbFail ? '' : `<div class="sec rise">${cap('Привычки и цели')}<section class="card">${F.skel(3)}</section></div>`;
    const its = t.items || [], gs = t.goals || [];
    if (!its.length && !gs.length && !(d.has || (d.habits || []).length)) {
      return `<div class="sec rise">${cap('Привычки и цели')}<section class="card hb-card hb-empty">
        <p>Отмечай привычки и цели на день — кольцо покажет, сколько уже сделано.</p>
        <div class="hb-btns"><button class="btn primary" data-a="hb-import">Добавить списком</button><button class="btn" data-a="hb-new">Одну привычку</button></div></section></div>`;
    }
    const more = its.length - HOME_MAX;
    return `<div class="sec rise">${cap('Привычки и цели', '<button class="link" data-a="hb-open">Все</button>')}
      <section class="card hb-card">${sum(t, d.week)}
        ${its.length ? `<div class="hb-list">${its.slice(0, HOME_MAX).map(row).join('')}</div>` : ''}
        <div class="hb-list" data-hb-goals="4">${goalsList(gs, 4)}</div>
        ${goalInput('Цель на сегодня…')}
        <button class="rg-more hb-more" data-a="hb-open"><span>${more > 0 ? `Ещё ${more} · неделя и серии` : 'Неделя, серии и настройки'}</span><span class="chev"></span></button></section></div>`;
  });
  F.slots.plus.push(row => row('hb-open', '', IC.check, 'var(--ink)', 'Привычки и цели', 'отметить, добавить списком'));

  // ---------- экран «Привычки» ----------
  F.screens.habits = () => {
    const d = F.S.hb;
    const title = F.dHead('Привычки', today() ? `${F.dateWord(today().day)}, ${F.dateLine(today().day)}` : 'и цели на день', 'var(--ink)');
    if (!d || !d.habits) {
      if (F.S.hbErr) return title + `<section class="card hb-empty"><p>Не загрузилось.</p><button class="btn primary" data-a="hb-retry">Повторить</button></section>`;
      load(true);
      return title + `<section class="card">${F.skel(5)}</section>`;
    }
    const t = d.today, s = d.stats || {}, its = t.items || [];
    let h = title;
    h += `<section class="card hb-card hb-hero rise">${sum(t, d.week)}
      <div class="hb-stats"><div><b class="num">${s.streak_now || 0}</b><span>серия сейчас</span></div>
        <div><b class="num">${s.streak_best || 0}</b><span>лучшая серия</span></div>
        <div><b class="num">${s.week_pct == null ? '—' : s.week_pct + '%'}</b><span>за 7 дней</span></div></div></section>`;
    if (!d.habits.length) {
      h += `<div class="sec rise">${cap('Привычки')}<section class="card hb-empty"><p>Пока пусто. Вставь свой список — каждая строка станет привычкой: «Пить 8 стаканов воды», «Английский 3 раза в неделю», «Бег пн ср пт в 07:00».</p>
        <div class="hb-btns"><button class="btn primary" data-a="hb-import">Добавить списком</button><button class="btn" data-a="hb-new">Одну привычку</button></div></section></div>`;
    }
    SLOTS.forEach(([k, name]) => {
      const part = its.filter(x => (x.slot || '') === k);
      if (!part.length) return;
      const dn = part.filter(x => x.due && x.done).length, tt = part.filter(x => x.due).length;
      h += `<div class="sec rise">${cap(name, tt ? `<span class="num">${dn} / ${tt}</span>` : '')}<section class="card hb-card"><div class="hb-list">${part.map(row).join('')}</div></section></div>`;
    });
    const gs = t.goals || [];
    h += `<div class="sec rise">${cap('Цели на день', `<button class="link" data-a="hb-move" data-hb-gmove ${gs.some(g => !g.done) ? '' : 'hidden'}>На завтра</button>`)}
      <section class="card hb-card"><div class="hb-list" data-hb-goals="0">${goalsList(gs)}</div>${goalInput('Добавить цель и нажать Enter')}</section></div>`;
    const act = d.habits.filter(x => !x.archived);
    if (act.length) {
      const days = (d.week || []).map(w => w.day);
      h += `<div class="sec rise">${cap('Неделя', '<span>7 дней</span>')}<section class="card hb-card hb-grid-card">
        <div class="hb-grid hb-gh" aria-hidden="true"><span class="hb-g0"></span>${(d.week || []).map((w, i) => `<span class="${i === 6 ? 'now' : ''}">${WD[w.wd]}</span>`).join('')}</div>
        ${act.map(x => `<div class="hb-grid" data-hb-grid="${esc(x.id)}">${gridRow(x, days)}</div>`).join('')}
        <p class="hb-legend"><span><i class="hb-dot d"></i>сделано</span><span><i class="hb-dot p"></i>частично</span><span><i class="hb-dot s"></i>пропуск</span><span><i class="hb-dot m"></i>нет</span></p></section></div>`;
    }
    h += `<div class="sec rise">${cap('Управление')}<div class="list">
      ${act.length < 40 ? `<button class="row tap" data-a="hb-new"><span class="ic">${F.I.plus}</span><span class="main"><div class="title">Новая привычка</div><div class="meta">отметка или количество, дни, время</div></span><span class="end chev"></span></button>` : ''}
      <button class="row tap" data-a="hb-import"><span class="ic">${I.list}</span><span class="main"><div class="title">Добавить списком</div><div class="meta">каждая строка — привычка</div></span><span class="end chev"></span></button>
      <button class="row tap" data-a="hb-skipday"><span class="ic">${I.pause}</span><span class="main"><div class="title">${s.skip_day ? 'Вернуть сегодняшний день' : 'Пропустить сегодня'}</div><div class="meta">${s.skip_day ? 'день пропущен — серии сохранены' : 'болею, в дороге — серии не прервутся'}</div></span></button>
      ${d.habits.some(x => x.archived) ? `<button class="row tap" data-a="hb-arch"><span class="ic">${I.box}</span><span class="main"><div class="title">Архив</div><div class="meta">${d.habits.filter(x => x.archived).length} в архиве</div></span><span class="end chev"></span></button>` : ''}</div></div>`;
    h += `<p class="foot">Отметить можно сегодня и до 7 дней назад — нажми на точку в «Неделе». Пропуск не рвёт серию. «N раз в неделю» — серия в неделях.</p>`;
    return h;
  };
  function gridRow(x, days) {
    const g = x.grid || '', name = { d: 'сделано', p: 'частично', s: 'пропуск', m: 'не сделано', o: 'не по плану', f: '' };
    return `<button type="button" class="hb-gt" data-a="hb-item" data-v="${esc(x.id)}"><i class="hb-ic">${IC[x.icon] || IC.check}</i><span>${esc(x.title)}</span>${x.streak >= 2 ? `<b class="hb-gs num">${F.I.flame}${x.streak}${x.weekly ? ' нед' : ''}</b>` : ''}</button>
      ${days.map((day, i) => { const c = g[i] || 'f'; return c === 'f' ? '<span class="hb-dot f" aria-hidden="true"></span>'
        : `<button type="button" class="hb-dot ${c}" data-a="hb-dot" data-v="${esc(x.id)}|${day}" aria-label="${WD[(F.parseDay(day).getDay() + 6) % 7]}: ${name[c]}"></button>`; }).join('')}`;
  }

  // ---------- действия ----------
  F.actions['hb-open'] = () => { vib('tap'); F.openScreen('habits'); };
  F.actions['hb-new'] = () => editSheet(null);
  F.actions['hb-retry'] = () => { F.S.hbErr = false; F.render(false); };
  F.actions['hb-import'] = () => importSheet();
  F.actions['hb-item'] = (el, id) => editSheet(id);
  const item = id => { const t = today(); return t && (t.items || []).find(x => x.id === id); };
  async function markItem(it, body, celebrate = true) {
    recount(today()); patchRow(it, true); patchSum(celebrate);
    try { await send('/api/habits/mark', { id: it.id, ...body }); afterServer(); }
    catch (e) { F.fail(e); await load(false); F.render(false); }
  }
  // ответ сервера: серии, «норма недели», сетка — тихо, без перерисовки экрана
  function afterServer() {
    const t = today(); if (!t) return;
    (t.items || []).forEach(x => patchRow(x));
    patchSum(false);
    const d = F.S.hb;
    if (d && d.habits) {
      const days = (d.week || []).map(w => w.day);
      d.habits.forEach(x => document.querySelectorAll(`[data-hb-grid="${CSS.escape(x.id)}"]`).forEach(el => { el.innerHTML = gridRow(x, days); }));
    }
  }
  F.actions['hb-tap'] = (el, id) => {
    const it = item(id); if (!it) return;
    const on = !it.done;
    it.done = on; it.skip = false;
    const was = it.value || 0;
    it.value = on ? Math.max(it.target, it.kind === 'count' ? it.target : 1) : 0;
    if (it.weekly) { it.week_done = Math.max(0, (it.week_done || 0) + (on ? 1 : -1)); if (on) it.due = true; }
    vib(on ? 'select' : 'tap');
    markItem(it, it.kind === 'count' ? { value: it.value } : { done: on });
    if (!on && it.kind === 'count' && was) F.toast('Сброшено до 0', 'ok');
  };
  F.actions['hb-plus'] = (el, id) => {
    const it = item(id); if (!it) return;
    const was = it.done;
    it.value = Math.min((it.value || 0) + (it.step || 1), it.target * 10);
    it.done = it.value >= it.target; it.skip = false;
    if (it.weekly && it.done && !was) { it.week_done = (it.week_done || 0) + 1; it.due = true; }
    vib(it.done && !was ? 'success' : 'select');
    markItem(it, { value: it.value });
  };
  F.actions['hb-goal'] = async (el, id) => {
    const t = today(), g = t && (t.goals || []).find(x => x.id === id); if (!g) return;
    g.done = !g.done;
    vib(g.done ? 'select' : 'tap');
    document.querySelectorAll(`.hb-goal[data-gid="${CSS.escape(id)}"]`).forEach(r => {
      r.classList.toggle('on', g.done); const b = r.querySelector('.hb-ck'); b.setAttribute('aria-pressed', g.done);
      b.classList.remove('pop'); void b.offsetWidth; b.classList.add('pop');
    });
    recount(t); patchSum(true);
    try { await send('/api/habits/goal', { op: 'done', id, done: g.done }); patchSum(false); } catch (e) { F.fail(e); await load(false); F.render(false); }
  };
  F.actions['hb-gdel'] = async (el, id) => {
    const t = today(); if (!t) return;
    const r = el.closest('.hb-goal');
    vib('tap');
    t.goals = (t.goals || []).filter(g => g.id !== id); recount(t);
    if (r) { r.classList.add('out'); setTimeout(redrawGoals, 220); } else redrawGoals();
    try { await send('/api/habits/goal', { op: 'del', id }); } catch (e) { F.fail(e); await load(false); F.render(false); }
  };
  F.actions['hb-move'] = async el => {
    vib('tap');
    try { const r = await send('/api/habits/goal', { op: 'move' }); redrawGoals(); vib('success'); F.toast(`На завтра: ${r.moved} ${F.plural(r.moved, 'цель', 'цели', 'целей')}`, 'ok'); }
    catch (e) { F.fail(e); }
  };
  async function addGoal(input) {
    const text = input.value.trim().replace(/\s+/g, ' ').slice(0, 80), t = today();
    if (!text || !t) return;
    if ((t.goals || []).length >= 30) { F.toast('Не больше 30 целей на день', 'err'); return; }
    input.value = '';
    t.goals = (t.goals || []).concat({ id: `tmp${Date.now()}`, text, done: false }); recount(t);
    redrawGoals(); vib('select');
    document.querySelectorAll('[data-hb-goals]').forEach(el => { const last = el.lastElementChild; if (last) last.classList.add('in'); });
    try { await send('/api/habits/goal', { op: 'add', text }); redrawGoals(); } catch (e) { F.fail(e); await load(false); F.render(false); }
  }
  document.addEventListener('keydown', ev => {
    if (ev.key === 'Enter' && ev.target.classList && ev.target.classList.contains('hb-gin')) { ev.preventDefault(); ev.stopPropagation(); addGoal(ev.target); }
  }, true);
  F.actions['hb-dot'] = async (el, v) => {
    const [id, day] = v.split('|'), d = F.S.hb, x = d && (d.habits || []).find(h => h.id === id);
    if (!x) return;
    const on = !el.classList.contains('d');
    el.className = `hb-dot ${on ? 'd' : 'm'} pop`;
    vib(on ? 'select' : 'tap');
    try { await send('/api/habits/mark', { id, day, done: on }); afterServer(); } catch (e) { F.fail(e); await load(false); F.render(false); }
  };
  F.actions['hb-skipday'] = async () => {
    const s = (F.S.hb && F.S.hb.stats) || {};
    vib('tap');
    try { await send('/api/habits/mark', { skip: !s.skip_day }); F.render(false); F.toast(s.skip_day ? 'День вернули' : 'Сегодня — пропуск, серии сохранены', 'ok'); } catch (e) { F.fail(e); }
  };
  F.actions['hb-arch'] = () => {
    const d = F.S.hb; if (!d) return;
    vib('tap');
    const arch = d.habits.filter(x => x.archived);
    const el = F.sheet(`<h2>Архив</h2><div class="list">${arch.map(x => `<div class="row"><span class="ic">${IC[x.icon] || IC.check}</span><span class="main"><div class="title wrap">${esc(x.title)}</div><div class="meta">${esc(sched(x))}</div></span>
      <button type="button" class="btn hb-sm" data-unarch="${esc(x.id)}">Вернуть</button></div>`).join('')}</div>`);
    el.addEventListener('click', async ev => {
      const b = ev.target.closest('[data-unarch]'); if (!b) return;
      await F.busy(b, async () => {
        try { await send('/api/habits/save', { habit: { id: b.dataset.unarch, archived: false } }); vib('success'); el.close(); F.render(false); } catch (e) { F.fail(e); }
      });
    });
  };

  // ---------- лист привычки: создать / изменить ----------
  function editSheet(id) {
    if (id && !(F.S.hb && F.S.hb.habits)) { load(true).then(() => { if (F.S.hb && F.S.hb.habits) editSheet(id); }); return; }  // с главной: нужны дни и архив
    const d = F.S.hb || {}, src = id ? (d.habits || []).find(x => x.id === id) : null;
    if (id && !src) return;
    vib('tap');
    const it = id ? item(id) : null;
    const h = src ? JSON.parse(JSON.stringify({ title: src.title, icon: src.icon, kind: src.kind, target: src.target, unit: src.unit || '',
      days: src.days || [0, 1, 2, 3, 4, 5, 6], weekly: src.weekly || null, time: src.time || null }))
      : { title: '', icon: 'check', kind: 'check', target: 8, unit: '', days: [0, 1, 2, 3, 4, 5, 6], weekly: null, time: null };
    let mode = h.weekly ? 'weekly' : h.days.length === 7 ? 'all' : 'days';
    let tmode = !h.time ? '' : ['утро', 'день', 'вечер'].includes(h.time) ? h.time : 'at';
    let sure = false, val = it ? it.value || 0 : 0;
    const seg = (k, opts, cur) => `<div class="seg hb-seg" data-seg="${k}" role="group">${opts.map(([v, t]) => `<button type="button" class="${v === cur ? 'on' : ''}" data-v="${v}" aria-pressed="${v === cur}">${t}</button>`).join('')}</div>`;
    const html = () => `<h2>${id ? 'Привычка' : 'Новая привычка'}</h2>
      <label class="hb-f"><span>Название</span><input id="hb-title" type="text" maxlength="80" value="${esc(h.title)}" placeholder="Например, «Читать 20 минут»"></label>
      <div class="hb-g"><div class="label">Значок</div><div class="hb-icons" role="group">${ICONS.map(k => `<button type="button" class="${k === h.icon ? 'on' : ''}" data-icon="${k}" aria-label="${k}" aria-pressed="${k === h.icon}">${IC[k]}</button>`).join('')}</div></div>
      <div class="hb-g"><div class="label">Как отмечать</div>${seg('kind', [['check', 'Галочкой'], ['count', 'Количеством']], h.kind)}
        ${h.kind === 'count' ? `<div class="hb-2"><label class="hb-f"><span>Цель в день</span><input id="hb-target" type="number" inputmode="numeric" min="1" max="100000" value="${h.target}"></label>
          <label class="hb-f"><span>Единица</span><input id="hb-unit" type="text" maxlength="12" value="${esc(h.unit)}" placeholder="стаканов, мин, шагов"></label></div>` : ''}</div>
      <div class="hb-g"><div class="label">Когда</div>${seg('mode', [['all', 'Каждый день'], ['days', 'По дням'], ['weekly', 'N в неделю']], mode)}
        ${mode === 'days' ? `<div class="hb-days" role="group">${WD.map((w, i) => `<button type="button" class="${h.days.includes(i) ? 'on' : ''}" data-day="${i}" aria-pressed="${h.days.includes(i)}">${w}</button>`).join('')}</div>` : ''}
        ${mode === 'weekly' ? `<div class="hb-fr"><span>Раз в неделю</span><div class="pl-step hb-step"><button type="button" data-wk="-1" aria-label="Меньше">−</button><b class="num">${h.weekly || 3}</b><button type="button" data-wk="1" aria-label="Больше">+</button></div></div>` : ''}</div>
      <div class="hb-g"><div class="label">Время дня</div>${seg('time', [['', 'Любое'], ['утро', 'Утро'], ['день', 'День'], ['вечер', 'Вечер'], ['at', 'Своё']], tmode)}
        ${tmode === 'at' ? `<label class="hb-f"><span>Время</span><input id="hb-time" type="time" value="${esc(/\d/.test(h.time || '') ? h.time : '07:00')}"></label>` : ''}</div>
      ${it && it.kind === 'count' && src.kind === 'count' ? `<div class="hb-g"><div class="label">Сегодня</div><div class="hb-fr"><span>${esc(src.unit || 'Сделано')}</span>
        <div class="pl-step hb-step"><button type="button" data-val="-1" aria-label="Меньше">−</button><b class="num" data-valv>${fmt(val)}</b><button type="button" data-val="1" aria-label="Больше">+</button></div></div></div>` : ''}
      ${it ? `<div class="hb-g"><div class="rg-sw hb-sw"><span><b>Пропустить сегодня</b><span class="muted">серия не прервётся</span></span><button type="button" class="switch ${it.skip ? 'on' : ''}" data-skip aria-pressed="${!!it.skip}" aria-label="Пропустить сегодня"></button></div></div>` : ''}
      <button class="btn primary" type="button" data-save>${id ? 'Сохранить' : 'Добавить'}</button>
      ${id ? `<div class="hb-btns hb-danger"><button class="btn" type="button" data-archive>${src.archived ? 'Вернуть из архива' : 'В архив'}</button>
        <button class="btn danger" type="button" data-del>${sure ? 'Точно удалить?' : 'Удалить'}</button></div>` : ''}`;
    const el = F.sheet(html());
    const read = () => {
      const q = s => el.querySelector(s);
      if (q('#hb-title')) h.title = q('#hb-title').value;
      if (q('#hb-target')) h.target = Number(q('#hb-target').value) || 1;
      if (q('#hb-unit')) h.unit = q('#hb-unit').value;
      if (q('#hb-time')) h.time = q('#hb-time').value || null;
    };
    const redraw = () => { read(); const x = el.querySelector('.sheet-x'), y = el.scrollTop; el.innerHTML = html(); if (x) el.prepend(x); el.scrollTop = y; };
    const body = () => {
      read();
      const out = { title: h.title.trim(), icon: h.icon, kind: h.kind, time: tmode === 'at' ? h.time || '07:00' : tmode || null };
      if (h.kind === 'count') { out.target = h.target; out.unit = h.unit.trim(); }
      if (mode === 'weekly') out.weekly = h.weekly || 3;
      else { out.weekly = null; out.days = mode === 'all' ? [0, 1, 2, 3, 4, 5, 6] : h.days; }
      return out;
    };
    if (!id) setTimeout(() => { const t = el.querySelector('#hb-title'); if (t && matchMedia('(pointer: fine)').matches) t.focus(); }, 350);
    el.addEventListener('click', async ev => {
      const s = ev.target.closest('[data-seg] button'), ic = ev.target.closest('[data-icon]'), dy = ev.target.closest('[data-day]');
      const wk = ev.target.closest('[data-wk]'), vv = ev.target.closest('[data-val]');
      if (s) {
        const k = s.parentElement.dataset.seg, v = s.dataset.v;
        if (k === 'kind') h.kind = v; else if (k === 'mode') { mode = v; if (v === 'weekly' && !h.weekly) h.weekly = 3; } else tmode = v;
        vib('select'); redraw();
      } else if (ic) {
        h.icon = ic.dataset.icon; vib('select');
        el.querySelectorAll('[data-icon]').forEach(b => { b.classList.toggle('on', b === ic); b.setAttribute('aria-pressed', b === ic); });
      } else if (dy) {
        const i = Number(dy.dataset.day);
        h.days = h.days.includes(i) ? h.days.filter(x => x !== i) : h.days.concat(i).sort((a, b) => a - b);
        dy.classList.toggle('on'); dy.setAttribute('aria-pressed', dy.classList.contains('on')); vib('select');
      } else if (wk) {
        h.weekly = Math.max(1, Math.min(6, (h.weekly || 3) + Number(wk.dataset.wk))); vib('select'); redraw();
      } else if (vv) {
        val = Math.max(0, Math.min(src.target * 10, val + Number(vv.dataset.val) * (it.step || 1))); vib('select');
        el.querySelector('[data-valv]').textContent = fmt(val);
        it.value = val; it.done = val >= it.target; it.skip = false;
        markItem(it, { value: val });
      } else if (ev.target.closest('[data-skip]')) {
        const sw = ev.target.closest('[data-skip]'), on = !it.skip;
        sw.classList.toggle('on', on); sw.setAttribute('aria-pressed', on); vib('select');
        it.skip = on; if (on) { it.value = 0; it.done = false; }
        markItem(it, { skip: on }, false);
      } else if (ev.target.closest('[data-save]')) {
        const b = body();
        if (!b.title) { F.toast('Напиши название', 'err'); el.querySelector('#hb-title').focus(); return; }
        if (mode === 'days' && !h.days.length) { F.toast('Выбери хотя бы один день', 'err'); return; }
        await F.busy(ev.target.closest('[data-save]'), async () => {
          try {
            await send('/api/habits/save', { habit: id ? { id, ...b } : b });
            vib('success'); F.toast(id ? 'Сохранено' : 'Привычка добавлена', 'ok'); el.close(); F.render(false);
          } catch (e) { F.fail(e); }
        });
      } else if (ev.target.closest('[data-archive]')) {
        await F.busy(ev.target.closest('[data-archive]'), async () => {
          try { await send('/api/habits/save', { habit: { id, archived: !src.archived } }); vib('success'); el.close(); F.render(false); } catch (e) { F.fail(e); }
        });
      } else if (ev.target.closest('[data-del]')) {
        if (!sure) { sure = true; vib('warning'); ev.target.closest('[data-del]').textContent = 'Точно удалить?'; return; }
        await F.busy(ev.target.closest('[data-del]'), async () => {
          try { await send('/api/habits/save', { delete: id }); vib('success'); F.toast('Привычка удалена', 'ok'); el.close(); F.render(false); } catch (e) { F.fail(e); }
        });
      }
    });
    el.addEventListener('keydown', ev => { if (ev.key === 'Enter' && ev.target.tagName === 'INPUT') { ev.preventDefault(); const b = el.querySelector('[data-save]'); if (b) b.click(); } });
  }

  // ---------- «Добавить списком» ----------
  function importSheet() {
    vib('tap');
    let text = F.S.hbDraft || '', prev = null;
    const line = h => `${sched(h)}${h.time ? ` · ${when(h.time)}` : ''}${h.kind === 'count' ? ` · цель ${fmt(h.target)}${h.unit ? ' ' + h.unit : ''}` : ''}`;
    const html = () => prev ? `<h2>Проверь список</h2>
        ${prev.habits.length ? `<div class="list hb-prev">${prev.habits.map((h, i) => `<div class="row"><span class="ic">${IC[h.icon] || IC.check}</span>
          <span class="main"><div class="title wrap">${esc(h.title)}</div><div class="meta">${esc(line(h))}</div></span>
          <button type="button" class="btn icon hb-sm" data-drop="${i}" aria-label="Убрать">${I.x}</button></div>`).join('')}</div>` : '<p class="muted">Нечего добавлять.</p>'}
        ${prev.dup.length ? `<p class="small muted">Уже есть: ${esc(prev.dup.join(', '))}</p>` : ''}
        ${prev.over.length ? `<p class="small muted">Не влезло (максимум 40): ${esc(prev.over.join(', '))}</p>` : ''}
        ${prev.skipped.length ? `<p class="small muted">Пропущены пустые строки: ${prev.skipped.length}</p>` : ''}
        ${prev.habits.length ? `<button class="btn primary" type="button" data-go>Добавить ${prev.habits.length} ${F.plural(prev.habits.length, 'привычку', 'привычки', 'привычек')}</button>` : ''}
        <button class="btn" type="button" data-back>Изменить текст</button>`
      : `<h2>Добавить списком</h2>
        <p class="small muted hb-help">Каждая строка — привычка. Понимаю «каждый день», «3 раза в неделю», дни («пн ср пт», «по будням»), количество («8 стаканов», «10000 шагов», «20 мин») и время («утром», «вечером», «в 22:00»).</p>
        <textarea id="hb-text" rows="8" maxlength="8000" placeholder="Пить 8 стаканов воды&#10;Зарядка утром&#10;Английский 3 раза в неделю&#10;Бег пн ср пт в 07:00&#10;Читать 20 мин перед сном">${esc(text)}</textarea>
        <button class="btn primary" type="button" data-parse>Разобрать</button>`;
    const el = F.sheet(html());
    const redraw = () => { const x = el.querySelector('.sheet-x'); el.innerHTML = html(); if (x) el.prepend(x); el.scrollTop = 0; };
    el.addEventListener('input', ev => { if (ev.target.id === 'hb-text') { text = ev.target.value; F.S.hbDraft = text; } });
    el.addEventListener('click', async ev => {
      if (ev.target.closest('[data-parse]')) {
        if (!text.trim()) { F.toast('Вставь список — каждая привычка с новой строки', 'err'); return; }
        await F.busy(ev.target.closest('[data-parse]'), async () => {
          try {
            const r = await F.api('/api/habits/import', { text, preview: true });
            if (!r || !Array.isArray(r.habits)) throw new Error('Не разобралось — попробуй ещё раз');
            prev = { dup: [], over: [], skipped: [], ...r }; vib('select'); redraw();
          } catch (e) { F.fail(e); }
        });
      } else if (ev.target.closest('[data-drop]')) {
        const i = Number(ev.target.closest('[data-drop]').dataset.drop);
        prev.habits.splice(i, 1); vib('select'); redraw();
      } else if (ev.target.closest('[data-back]')) { prev = null; vib('select'); redraw(); }
      else if (ev.target.closest('[data-go]')) {
        await F.busy(ev.target.closest('[data-go]'), async () => {
          try {
            const r = await send('/api/habits/import', { text: prev.habits.map(h => h.line).join('\n') });
            const n = (r.created || []).length;
            F.S.hbDraft = ''; vib('success'); F.toast(`Добавлено: ${n}`, 'ok'); el.close();
            if (F.S.screen !== 'habits') F.openScreen('habits'); else F.render(false);
          } catch (e) { F.fail(e); }
        });
      }
    });
  }
});
