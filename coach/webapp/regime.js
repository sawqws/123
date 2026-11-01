/* regime — «Режим»: распорядок дня ради лучших показателей (сон, тренировка, массаж, еда и вода, вечерний чек-лист).
   Данные — F.S.data.ext.regime (с сервера, f_regime.py: {cfg, today, week}), иначе POST /api/regime {}.
   Отметки вечера — POST /api/regime/check {id, done}, настройки — POST /api/regime/save (тело — cfg). Договор — coach/SYNC.md, п. 4. */
(window.FormaModules = window.FormaModules || []).push(F => {
  const { esc, svgi, cap } = F;
  const I = {
    clock: svgi('<circle cx="12" cy="12" r="8.6"/><path d="M12 7.2V12l3.2 2"/>'),
    hand: svgi('<path d="M7 11.5V6.8a1.6 1.6 0 0 1 3.2 0V11M10.2 10.5V5.6a1.6 1.6 0 0 1 3.2 0v5M13.4 10.6V6.6a1.6 1.6 0 0 1 3.2 0v6.6c0 4-2.5 7.3-6.3 7.3-2.4 0-4-1.2-5.2-3.2L3.4 13.7a1.5 1.5 0 0 1 2.5-1.6L7 13.6"/>'),
    sun: svgi('<circle cx="12" cy="12" r="4"/><path d="M12 2.8v2M12 19.2v2M2.8 12h2M19.2 12h2M5.5 5.5l1.4 1.4M17.1 17.1l1.4 1.4M5.5 18.5l1.4-1.4M17.1 6.9l1.4-1.4"/>'),
    list: svgi('<path d="M9 6.5h11M9 12h11M9 17.5h11"/><circle cx="4.6" cy="6.5" r="1"/><circle cx="4.6" cy="12" r="1"/><circle cx="4.6" cy="17.5" r="1"/>'),
    bell: svgi('<path d="M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 1.5h-15z"/><path d="M10 20.5a2.2 2.2 0 0 0 4 0"/>'),
    book: svgi('<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z"/><path d="M4 20.5A2.5 2.5 0 0 0 6.5 23"/>'),
  };
  const WD = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
  const INT = { full: 'Полная', light: 'Лёгкая', rest: 'Отдых' };
  const MODE = { vacation: 'Каникулы', school: 'Учёба', weekend: 'Выходной' };
  const DEF = { train_days: [], train_time: '18:00', train_minutes: 90, school_days: [0, 1, 2, 3, 4], school_start: '09:00', school_end: '16:00',
    wake: '07:00', bed: '22:00', weekend_wake: '08:00', massage_days: [], vacation: null, shift: null,
    evening: ['Душ и умыть лицо', 'Ужин: белок и углеводы', 'Телефон в сторону'], remind: 45 };

  // сервер может положить в ext.regime и {cfg, today, week}, и просто today — понимаем оба
  const pack = () => { const e = F.S.data && F.S.data.ext && F.S.data.ext.regime; return e || F.S.regime || null; };
  const today = () => { const r = pack(); return r ? (r.today || (r.day ? r : null)) : null; };
  const put = r => {
    if (!r || typeof r !== 'object') return;
    const cur = pack() || {}, next = r.today || r.week || r.cfg ? { ...cur, ...r } : r.day ? { ...cur, today: r } : cur;
    F.S.data.ext = F.S.data.ext || {};
    F.S.data.ext.regime = next;
  };
  const wdOf = s => (F.parseDay(s).getDay() + 6) % 7;  // пн = 0, как в Python
  const litres = ml => ml == null ? '—' : F.liters(ml);
  const kcal = n => n == null ? '—' : F.big(n);

  // ---------- карточка «Режим на сегодня» ----------
  function trainRow(tr) {
    if (!tr) return '';
    const on = tr.on && tr.intensity !== 'rest';
    return `<div class="rg-train"><span class="rg-int ${esc(tr.intensity || (on ? 'full' : 'rest'))}" aria-hidden="true"></span>
      <span class="rg-tm"><b>${on ? `${INT[tr.intensity] || 'Полная'} тренировка · ${tr.minutes || '—'} мин` : 'Без тренировки'}</b>
        <span class="muted">${tr.why ? esc(tr.why) : on ? `в ${esc(tr.time || '—')}` : 'день отдыха'}</span></span></div>`;
  }
  function checklist(d) {
    const list = d.checklist || [];
    if (!list.length) return '';
    const done = list.filter(x => x.done).length;
    return `<div class="rg-ev"><div class="rg-evh"><span>Вечер</span><b class="num" data-rg-cnt>${done}/${list.length}</b></div>
      ${list.map(x => `<button type="button" class="rg-ck ${x.done ? 'on' : ''}" data-a="rg-check" data-v="${esc(x.id)}" aria-pressed="${!!x.done}">
        <span class="rg-box" aria-hidden="true">${F.I.check}</span><span class="rg-ct">${esc(x.text)}</span></button>`).join('')}</div>`;
  }
  function body(d, full) {
    const tips = (d.tips || []).slice(0, full ? 3 : 2);
    const on = d.train && d.train.on && d.train.intensity !== 'rest';
    return `<div class="rg-times">
        <div><span class="tk">Отбой</span><b class="num">${esc(d.bed || '—')}</b></div>
        <div><span class="tk">Подъём</span><b class="num">${esc(d.wake || '—')}</b></div>
        <div><span class="tk">Спорт</span><b class="num">${on ? esc(d.train.time || '—') : '—'}</b></div></div>
      ${trainRow(d.train)}
      ${d.massage ? `<div class="rg-line">${I.hand}<span>Массаж сегодня</span></div>` : ''}
      <div class="rg-food num"><span><b>${kcal(d.kcal)}</b> ккал</span><span>белок <b>${d.protein == null ? '—' : d.protein}</b> г</span><span>вода <b>${litres(d.water)}</b></span></div>
      ${checklist(d)}
      ${tips.length ? `<div class="rg-tips">${tips.map(x => `<p>${esc(x)}</p>`).join('')}</div>` : ''}`;
  }

  F.slots.homeAfterMyDay.push((t, isToday) => {
    if (!isToday) return '';
    const d = today();
    if (!d) return F.S.rgFail ? '' : `<div class="sec rise">${cap('Режим на сегодня')}<section class="card">${F.skel(3)}</section></div>`;
    return `<div class="sec rise">${cap('Режим на сегодня', esc(MODE[d.mode] || ''))}
      <section class="card rg-card">${body(d)}
        <button class="rg-more" data-a="rg-open"><span>Неделя и настройки</span><span class="chev"></span></button></section></div>`;
  });
  F.slots.plus.push(row => row('rg-open', '', I.clock, 'var(--ink)', 'Режим', 'сон, тренировка, вечер'));

  // ---------- экран «Режим» ----------
  F.screens.regime = () => {
    const r = pack(), d = today();
    if (!d) {
      if (!F.S.rgLoading) load(true);
      return F.dHead('Режим', 'неделя и настройки', 'var(--ink)') + `<section class="card">${F.skel(4)}</section>`;
    }
    let h = F.dHead('Режим', `${F.dateWord(d.day)}, ${F.dateLine(d.day)}`, 'var(--ink)');
    h += `<div class="sec rise">${cap('Сегодня')}<section class="card rg-card">${body(d, true)}</section></div>`;
    const week = (r && r.week) || [];
    if (week.length) {
      h += `<div class="sec rise">${cap('Неделя')}<div class="list rg-week">${week.map(w => {
        const tr = w.train || {}, on = tr.on && tr.intensity !== 'rest', dd = F.parseDay(w.day), isT = w.day === d.day;
        return `<div class="row rg-wr ${isT ? 'on' : ''}"><span class="rg-wd"><b>${isT ? 'Сегодня' : WD[wdOf(w.day)]}</b><span class="num">${dd.getDate()} ${F.MON_S[dd.getMonth()]}</span></span>
          <span class="rg-wt"><span class="rg-int ${esc(on ? tr.intensity || 'full' : 'rest')}" aria-hidden="true"></span>${on ? `<span class="num">${esc(tr.time || '')}</span>` : 'Отдых'}${w.massage ? `<i class="rg-m" title="Массаж">${I.hand}</i>` : ''}</span>
          <span class="rg-ws num">${esc(w.bed || '—')} – ${esc(w.wake || '—')}</span></div>`;
      }).join('')}</div><p class="rg-legend"><span><i class="rg-int full"></i>полная</span><span><i class="rg-int light"></i>лёгкая</span><span><i class="rg-int rest"></i>отдых</span><span>${I.hand}массаж</span></p></div>`;
    }
    const c = (r && r.cfg) || null;
    h += `<div class="sec rise">${cap('Настройки', '<button class="link" data-a="rg-edit">Изменить</button>')}<div class="list">
      ${set(F.I.bolt, 'Тренировки', c ? days(c.train_days) + (c.train_days && c.train_days.length ? ` · ${c.train_time} · ${c.train_minutes} мин` : '') : '—')}
      ${set(I.book, 'Учёба', c ? `${days(c.school_days)} · ${c.school_start}–${c.school_end}` : '—')}
      ${set(F.I.moon, 'Сон', c ? `отбой ${c.bed} · подъём ${c.wake} · вых. ${c.weekend_wake}` : '—')}
      ${set(I.hand, 'Массаж', c ? days(c.massage_days) : '—')}
      ${c && c.vacation ? set(I.sun, 'Каникулы', `${F.dateLine(c.vacation.from)} – ${F.dateLine(c.vacation.to)} · тренировка ${c.vacation.train_time}`) : ''}
      ${c && c.shift ? set(I.clock, 'Сдвиг сна', `с ${F.dateLine(c.shift.from)} по ${c.shift.step} мин раз в ${c.shift.every} ${F.plural(c.shift.every, 'день', 'дня', 'дней')}`) : ''}
      ${set(I.bell, 'Напоминание', c ? (c.remind ? `за ${c.remind} мин до отбоя` : 'выключено') : '—')}</div></div>`;
    h += `<p class="foot">Интенсивность: восстановление ниже 34% или 3-й день тренировок подряд — лёгкая. Время сна — наш расчёт, не оценка WHOOP.</p>`;
    return h;
  };
  const days = a => !a || !a.length ? 'нет' : a.length === 7 ? 'каждый день' : a.slice().sort((x, y) => x - y).map(i => WD[i]).join(', ');
  const set = (ic, t, m) => `<button class="row tap" data-a="rg-edit"><span class="ic">${ic}</span><span class="main"><div class="title">${t}</div><div class="meta">${esc(m)}</div></span><span class="end chev"></span></button>`;

  // ---------- сервер ----------
  async function load(screen) {
    F.S.rgLoading = true;
    try { const r = await F.api('/api/regime', {}); put(r); F.S.rgFail = !today(); } catch (e) { F.S.rgFail = true; if (screen) F.fail(e); }
    F.S.rgLoading = false;
    if (F.S.screen === 'regime' || (!F.S.screen && F.S.tab === 'today')) F.render(false);
  }
  F.onData.push(d => { if (!(d.ext && d.ext.regime) && !F.S.rgLoading && !F.S.rgTried) { F.S.rgTried = true; load(false); } });

  F.actions['rg-open'] = () => F.openScreen('regime');
  F.actions['rg-edit'] = () => editSheet();
  // отметка вечера: меняем только эту строку (без перерисовки экрана), сервер запоминает
  F.actions['rg-check'] = async (el, id) => {
    const d = today(); if (!d) return;
    const it = (d.checklist || []).find(x => String(x.id) === id); if (!it) return;
    it.done = !it.done;
    F.haptic(it.done ? 'success' : 'select');
    document.querySelectorAll(`[data-a="rg-check"][data-v="${CSS.escape(id)}"]`).forEach(b => { b.classList.toggle('on', it.done); b.setAttribute('aria-pressed', it.done); });
    const n = d.checklist.filter(x => x.done).length;
    document.querySelectorAll('[data-rg-cnt]').forEach(b => { b.textContent = `${n}/${d.checklist.length}`; });
    try { const r = await F.api('/api/regime/check', { id, done: it.done }); if (r && (r.today || r.day)) { put(r); } }
    catch (e) { it.done = !it.done; F.fail(e); F.render(false); }
  };

  // ---------- лист «Настройки режима» ----------
  function editSheet() {
    const r = pack();
    F.haptic('tap');
    const c = JSON.parse(JSON.stringify({ ...DEF, ...((r && r.cfg) || {}) }));
    c.evening = (c.evening || []).slice();
    const chips = (k, arr) => `<div class="rg-days" data-days="${k}" role="group">${WD.map((w, i) => `<button type="button" class="${(arr || []).includes(i) ? 'on' : ''}" data-i="${i}" aria-pressed="${(arr || []).includes(i)}">${w}</button>`).join('')}</div>`;
    const time = (k, v, label) => `<label class="rg-f"><span>${label}</span><input type="time" data-k="${k}" value="${esc(v || '')}"></label>`;
    const date = (k, v, label) => `<label class="rg-f"><span>${label}</span><input type="date" data-k="${k}" value="${esc(v || '')}"></label>`;
    const step = (k, v, unit) => `<div class="pl-step rg-step" data-st="${k}"><button type="button" data-d="-1" aria-label="Меньше">−</button><b class="num">${v}</b><button type="button" data-d="1" aria-label="Больше">+</button><span class="muted">${unit}</span></div>`;
    const sw = (k, on, t, sub) => `<div class="rg-sw"><span class="pl-gm"><b>${t}</b><span class="muted">${sub}</span></span><button type="button" class="switch ${on ? 'on' : ''}" data-sw="${k}" aria-pressed="${!!on}" aria-label="${t}"></button></div>`;
    const html = () => `<h2>Настройки режима</h2>
      <div class="rg-g"><div class="label">Тренировки</div>${chips('train_days', c.train_days)}
        ${time('train_time', c.train_time, 'Начало')}<div class="rg-fr"><span>Длительность</span>${step('train_minutes', c.train_minutes, 'мин')}</div></div>
      <div class="rg-g"><div class="label">Учёба</div>${chips('school_days', c.school_days)}
        <div class="rg-2">${time('school_start', c.school_start, 'С')}${time('school_end', c.school_end, 'До')}</div></div>
      <div class="rg-g"><div class="label">Сон</div><div class="rg-3">${time('bed', c.bed, 'Отбой')}${time('wake', c.wake, 'Подъём')}${time('weekend_wake', c.weekend_wake, 'Подъём в выходные')}</div></div>
      <div class="rg-g"><div class="label">Массаж</div>${chips('massage_days', c.massage_days)}</div>
      <div class="rg-g">${sw('vacation', !!c.vacation, 'Каникулы', 'своё время тренировки и массажа')}
        ${c.vacation ? `<div class="rg-2">${date('vacation.from', c.vacation.from, 'С')}${date('vacation.to', c.vacation.to, 'По')}</div>
          ${time('vacation.train_time', c.vacation.train_time, 'Тренировка в каникулы')}<div class="rg-f"><span>Массаж в каникулы</span>${chips('vacation.massage_days', c.vacation.massage_days)}</div>` : ''}</div>
      <div class="rg-g">${sw('shift', !!c.shift, 'Сдвиг сна', 'постепенно вернуть сон к обычному')}
        ${c.shift ? `${date('shift.from', c.shift.from, 'Начать с')}<div class="rg-2">${time('shift.bed', c.shift.bed, 'Сейчас ложусь')}${time('shift.wake', c.shift.wake, 'Сейчас встаю')}</div>
          <div class="rg-fr"><span>Шаг</span>${step('shift.step', c.shift.step, 'мин')}</div><div class="rg-fr"><span>Раз в</span>${step('shift.every', c.shift.every, F.plural(c.shift.every, 'день', 'дня', 'дней'))}</div>` : ''}</div>
      <div class="rg-g"><div class="label">Вечер</div><div class="list rg-evl">${c.evening.map((x, i) => `<div class="row no-ic"><div class="main"><div class="title wrap">${esc(x)}</div></div>
          <button type="button" class="btn icon" data-del="${i}" aria-label="Убрать">✕</button></div>`).join('')}</div>
        ${c.evening.length < 12 ? `<div class="ask"><textarea id="rg-new" rows="1" placeholder="Новый пункт вечера…"></textarea><button type="button" class="send" data-add aria-label="Добавить">＋</button></div>` : ''}</div>
      <div class="rg-g"><div class="label">Напоминание</div><div class="rg-fr"><span>До отбоя</span>${step('remind', c.remind, c.remind ? 'мин' : 'выкл')}</div></div>
      <button class="btn primary" type="button" data-save>Сохранить</button>`;
    const el = F.sheet(html());
    const redraw = () => { const x = el.querySelector('.sheet-x'), y = el.scrollTop; el.innerHTML = html(); if (x) el.prepend(x); el.scrollTop = y; };
    const get = k => k.split('.').reduce((o, p) => o && o[p], c);
    const setv = (k, v) => { const p = k.split('.'), last = p.pop(), o = p.reduce((a, b) => a[b], c); o[last] = v; };
    const LIM = { train_minutes: [30, 240, 15], 'shift.step': [10, 60, 5], 'shift.every': [1, 7, 1], remind: [0, 120, 15] };
    const add = () => { const t = el.querySelector('#rg-new'), v = t && t.value.trim(); if (!v) return; c.evening.push(v.slice(0, 80)); F.haptic('select'); redraw(); const n = el.querySelector('#rg-new'); if (n) n.focus(); };
    el.addEventListener('click', async ev => {
      const day = ev.target.closest('[data-days] button'), st = ev.target.closest('[data-st] button'), s = ev.target.closest('[data-sw]');
      if (day) {
        const k = day.parentElement.dataset.days, i = Number(day.dataset.i), arr = (get(k) || []).slice();
        setv(k, arr.includes(i) ? arr.filter(x => x !== i) : arr.concat(i).sort((a, b) => a - b));
        day.classList.toggle('on'); day.setAttribute('aria-pressed', day.classList.contains('on')); F.haptic('select');
      } else if (st) {
        const k = st.closest('[data-st]').dataset.st, [lo, hi, s2] = LIM[k];
        setv(k, Math.max(lo, Math.min(hi, (Number(get(k)) || 0) + Number(st.dataset.d) * s2))); F.haptic('select'); redraw();
      } else if (s) {
        const k = s.dataset.sw;
        if (c[k]) c[k] = null;
        else if (k === 'vacation') c.vacation = { from: F.S.data.today.day, to: F.isoDay(new Date(F.parseDay(F.S.data.today.day).getTime() + 7 * 864e5)), train_time: '10:00', massage_days: (c.massage_days || []).slice() };
        else c.shift = { from: F.S.data.today.day, bed: '23:30', wake: '08:30', step: 30, every: 2 };
        F.haptic('select'); redraw();
      } else if (ev.target.closest('[data-del]')) { c.evening.splice(Number(ev.target.closest('[data-del]').dataset.del), 1); F.haptic('select'); redraw(); }
      else if (ev.target.closest('[data-add]')) add();
      else if (ev.target.closest('[data-save]')) {
        const btn = ev.target.closest('[data-save]');
        await F.busy(btn, async () => {
          try {
            const r2 = await F.api('/api/regime/save', c);
            if (!r2 || typeof r2 !== 'object') throw new Error('Не сохранилось — попробуй ещё раз');
            put(r2.cfg || r2.today ? r2 : { cfg: c }); if (!r2.cfg) pack().cfg = c;
            F.haptic('success'); F.toast('Режим сохранён', 'ok'); el.close(); F.render(false);
          } catch (e) { F.fail(e); }
        });
      }
    });
    el.addEventListener('change', ev => { const k = ev.target.dataset && ev.target.dataset.k; if (k) setv(k, ev.target.value || null); });
    el.addEventListener('keydown', ev => { if (ev.key === 'Enter' && ev.target.id === 'rg-new') { ev.preventDefault(); ev.stopPropagation(); add(); } });
  }
});
