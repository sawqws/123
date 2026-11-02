/* skin — «Кожа»: мой уход утром и вечером, процедуры по расписанию, заметки о коже, отметки на сегодня, серия,
   дневник кожи 1–5 и график за 30 дней. Карточка на главной, экран ?p=skin, пункт в «＋», вечерний уход — в чек-листе
   «Режима» (F.slots.rgEvening; одинаковые пункты режима прячет F.rgHide).
   Данные — F.S.sn (полный ответ /api/skin) или F.S.data.ext.skin (кратко из /api/all). Сервер — coach/f_skin.py.
   Отметка и оценка меняют только свою строку (без перерисовки экрана); запросы — по очереди. */
(window.FormaModules = window.FormaModules || []).push(F => {
  const { esc, svgi, cap } = F;
  const I = {
    face: svgi('<circle cx="12" cy="12" r="8.6"/><path d="M9 10.2h.01M15 10.2h.01M9.3 14.6c1.5 1.2 3.9 1.2 5.4 0"/>'),
    sun: svgi('<circle cx="12" cy="12" r="4"/><path d="M12 2.8v2M12 19.2v2M2.8 12h2M19.2 12h2M5.5 5.5l1.4 1.4M17.1 17.1l1.4 1.4M5.5 18.5l1.4-1.4M17.1 6.9l1.4-1.4"/>'),
    jar: svgi('<path d="M8.5 3.5h7v3l1.8 2.6V19a1.5 1.5 0 0 1-1.5 1.5H8.2A1.5 1.5 0 0 1 6.7 19V9.1l1.8-2.6z"/><path d="M6.7 13h10.6"/>'),
    list: svgi('<path d="M9 6.5h11M9 12h11M9 17.5h11"/><circle cx="4.6" cy="6.5" r="1"/><circle cx="4.6" cy="12" r="1"/><circle cx="4.6" cy="17.5" r="1"/>'),
    edit: svgi('<path d="M4 20l1-4.5L15.5 5a2.1 2.1 0 0 1 3 3L8 18.5z"/><path d="M13.5 7l3 3"/>'),
    note: svgi('<path d="M6 3.5h9l3.5 3.5v13.5H6z"/><path d="M14.5 3.5V7.5h4M9 12h6M9 15.5h4"/>'),
    x: svgi('<path d="M7 7l10 10M17 7L7 17"/>'),
  };
  const WD = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
  const WORD = { 1: 'плохо', 2: 'так себе', 3: 'нормально', 4: 'хорошо', 5: 'отлично' };
  const PART = { am: 'Утро', pm: 'Вечер', extra: 'Процедуры' };
  const HOME_MAX = 4;

  // ---------- данные ----------
  const ext = () => F.S.data && F.S.data.ext && F.S.data.ext.skin;
  const data = () => F.S.sn || ext() || null;
  const today = () => { const d = data(); return d && d.today; };
  function put(r) {
    if (!r || !r.today) return;
    F.S.sn = { ...(F.S.sn || {}), ...r };
    if (F.S.data) {
      F.S.data.ext = F.S.data.ext || {};
      F.S.data.ext.skin = { has: r.has, today: r.today, streak: r.streak, rated: r.stats ? r.stats.rated : 0,
        notes: r.notes ? Object.values(r.notes).some(Boolean) : false };
    }
  }
  let queue = Promise.resolve(), pending = 0;
  function send(path, body, apply = true) {
    pending++;
    const p = queue.then(() => F.api(path, body));
    queue = p.catch(() => {});
    return p.then(r => { pending--; if (apply && !pending) put(r); return r; }, e => { pending--; throw e; });
  }
  async function load(screen) {
    if (F.S.snLoading) return;
    F.S.snLoading = true;
    try { put(await F.api('/api/skin', {})); F.S.snFail = F.S.snErr = false; } catch (e) { F.S.snFail = !data(); if (screen) { F.S.snErr = true; F.fail(e); } }
    F.S.snLoading = false;
    if (F.S.screen === 'skin' || (!F.S.screen && F.S.tab === 'today')) F.render(false);
  }
  F.onData.push(d => {
    const e = d.ext && d.ext.skin;
    if (e && e.today && F.S.sn && !pending) F.S.sn = { ...F.S.sn, today: e.today, streak: e.streak, has: e.has };
    if (!e && !F.S.snTried) { F.S.snTried = true; load(false); }
  });
  const vib = kind => {
    F.haptic(kind);
    const tgh = F.tg && F.tg.HapticFeedback && F.tg.initData;
    if (!tgh && navigator.vibrate) { try { navigator.vibrate(kind === 'success' ? [10, 70, 18] : 8); } catch (e) { /* нет вибрации */ } }
  };
  const nowPart = () => new Date().getHours() < 15 ? 'am' : 'pm';
  const cnt = (t, p) => `${t[p + '_done']}/${t[p + '_total']}`;
  const partDone = (t, p) => t[p + '_total'] > 0 && t[p + '_done'] >= t[p + '_total'];
  // заглушка загрузки — другим тегом (article): morph заменит её целиком, и ширины строк-заглушек (style="width:92%")
  // не прилипнут к настоящим блокам (morph не снимает style, которого нет в новой разметке)
  const days = n => `${n} ${F.plural(n, 'день', 'дня', 'дней')}`;
  const skel = n => `<article class="card">${F.skel(n)}</article>`;

  // ---------- строка шага: круглая галочка, отметка одним нажатием ----------
  function step(x, meta) {
    const m = meta != null ? meta : x.product || '';
    return `<button type="button" class="sn-ck${x.done ? ' on' : ''}" data-a="sn-mark" data-v="${esc(x.id)}" data-sn-id="${esc(x.id)}" aria-pressed="${!!x.done}">
      <span class="sn-box" aria-hidden="true">${F.I.check}</span>
      <span class="sn-ct"><b>${esc(x.title)}</b>${m ? `<small>${esc(m)}</small>` : ''}</span></button>`;
  }
  const extraMeta = x => [x.product, x.sched, x.days && x.days.length ? '' : `${x.week_done} из ${x.week_need} за неделю`].filter(Boolean).join(' · ');
  function partBtn(t, p) {
    const ok = partDone(t, p), none = !t[p + '_total'];
    return `<button type="button" class="sn-part${ok ? ' ok' : ''}${none ? ' none' : ''}" data-a="sn-open" data-sn-part="${p}">
      <span class="sn-pi">${p === 'am' ? I.sun : F.I.moon}</span>
      <span class="sn-pt"><b>${PART[p]}</b><small class="num" data-sn-cnt="${p}">${none ? 'нет шагов' : cnt(t, p)}</small></span>
      <span class="sn-pm" aria-hidden="true">${F.I.check}</span></button>`;
  }
  function rateRow(t, big) {
    const r = t.rating;
    return `<div class="sn-rate${big ? ' big' : ''}" data-sn-rate>
      <div class="sn-rh"><span>${big ? 'Оценка за день' : 'Кожа сегодня'}</span><b data-sn-word>${r ? `${r} · ${WORD[r]}` : 'не оценил'}</b></div>
      <div class="sn-rb" role="group" aria-label="Оценка кожи от 1 до 5">${[1, 2, 3, 4, 5].map(n =>
        `<button type="button" class="num${r === n ? ' on' : ''}" data-a="sn-rate" data-v="${n}" aria-pressed="${r === n}" aria-label="${n} — ${WORD[n]}">${n}</button>`).join('')}</div>
      ${big ? '<div class="sn-rl" aria-hidden="true"><span>плохо</span><span>отлично</span></div>' : ''}</div>`;
  }

  // ---------- карточка на главной ----------
  F.slots.homeAfterMyDay.push((t0, isToday) => {
    if (!isToday) return '';
    const d = data(), t = d && d.today;
    if (!t) return F.S.snFail ? '' : `<div class="sec rise">${cap('Кожа')}${skel(2)}</div>`;
    if (!d.has) {
      return `<div class="sec rise">${cap('Кожа')}<section class="card sn-card sn-empty">
        <span class="sn-ei">${I.face}</span><p><b>Расскажи, какой у тебя уход</b>Утро, вечер, маски — буду отмечать шаги, а тренер подскажет, что улучшить.</p>
        <div class="sn-btns"><button class="btn primary" data-a="sn-import">Заполнить списком</button><button class="btn" data-a="sn-open">Подробнее</button></div></section></div>`;
    }
    // сейчас утро — утренние шаги; утро уже сделано (или шагов нет) — вечерние
    const np = nowPart(), other = np === 'am' ? 'pm' : 'am';
    const p = t[np + '_total'] && !(partDone(t, np) && t[other + '_total'] && !partDone(t, other) && np === 'am') ? np : other;
    const list = (t[p] || []).slice(0, HOME_MAX), more = (t[p] || []).length - HOME_MAX;
    const due = (t.extra || []).filter(x => !x.done);
    return `<div class="sec rise">${cap('Кожа', `<button class="link" data-a="sn-open" aria-label="Кожа: ${d.streak >= 2 ? `серия ${days(d.streak)}` : 'все шаги и дневник'}">${d.streak >= 2 ? `<span class="sn-fl num">${F.I.flame}${d.streak}</span>` : 'Все'}</button>`)}
      <section class="card sn-card"><div class="sn-parts">${partBtn(t, 'am')}${partBtn(t, 'pm')}</div>
        ${list.length ? `<div class="sn-list"><div class="sn-lh">${p === 'am' ? 'Утро' : 'Уход на вечер'}</div>${list.map(x => step(x)).join('')}</div>` : ''}
        ${more > 0 || due.length ? `<button class="sn-more" data-a="sn-open"><span>${due.length ? `Сегодня ещё: ${esc(due.map(x => x.title.toLowerCase()).join(', '))}` : `Ещё ${more}`}</span><span class="chev"></span></button>` : ''}
        ${rateRow(t)}</section></div>`;
  });
  F.slots.plus.push(row => row('sn-open', '', I.face, 'var(--ink)', 'Кожа', 'уход утром и вечером, оценка'));

  // ---------- вечерний чек-лист «Режима»: уход берём отсюда, не дублируем ----------
  F.slots.rgEvening = F.slots.rgEvening || [];
  F.slots.rgEvening.push(rd => {
    const t = today();
    if (!t || !(t.pm || []).length || (rd && rd.day && rd.day !== t.day)) return '';
    return `<div class="sn-rg"><div class="rg-evh"><span>Уход за кожей</span><b class="num" data-sn-cnt="pm">${cnt(t, 'pm')}</b></div>
      ${t.pm.map(x => `<button type="button" class="rg-ck ${x.done ? 'on' : ''}" data-a="sn-mark" data-v="${esc(x.id)}" data-sn-id="${esc(x.id)}" aria-pressed="${!!x.done}">
        <span class="rg-box" aria-hidden="true">${F.I.check}</span><span class="rg-ct">${esc(x.title)}</span></button>`).join('')}</div>`;
  });
  const norm = s => String(s || '').toLowerCase().replace(/ё/g, 'е').replace(/[^а-яa-z ]/g, '').split(/\s+/).filter(Boolean);
  const same = (a, b) => { const x = norm(a), y = norm(b); return !!x.length && !!y.length && (x.join() === y.join() || (x[0] === y[0] && x[0].length >= 4)); };
  F.rgHide = text => { const t = today(); return !!(t && (t.pm || []).some(x => same(text, x.title))); };

  // ---------- экран «Кожа» ----------
  F.screens.skin = () => {
    const d = F.S.sn, t = today();
    const title = F.dHead('Кожа', t ? `${F.dateWord(t.day)}, ${F.dateLine(t.day)}` : 'уход и дневник', 'var(--ink)');
    if (!d || !d.routine) {
      if (F.S.snErr) return title + `<section class="card sn-empty"><p>Не загрузилось.</p><button class="btn primary" data-a="sn-retry">Повторить</button></section>`;
      load(true);
      return title + skel(5);
    }
    const s = d.stats || {}, r = d.routine;
    let h = title;
    if (d.has) {
      h += `<section class="card sn-card sn-hero rise"><div class="sn-parts">${partBtn(t, 'am')}${partBtn(t, 'pm')}</div>
        <div class="sn-stats"><div><b class="num" data-sn-streak>${d.streak}</b><span>${F.plural(d.streak, 'день', 'дня', 'дней')} подряд</span></div>
          <div><b class="num">${d.best}</b><span>лучшая серия</span></div>
          <div><b class="num">${s.avg7 == null ? '—' : F.num(s.avg7, 1).replace(/,0$/, '')}</b><span>кожа за 7 дней</span></div></div></section>`;
      ['am', 'pm'].forEach(p => {
        if (!(t[p] || []).length) return;
        h += `<div class="sec rise">${cap(PART[p], `<span class="num" data-sn-cnt="${p}">${cnt(t, p)}</span>`)}<section class="card sn-card"><div class="sn-list">${t[p].map(x => step(x)).join('')}</div></section></div>`;
      });
      const ex = (t.extra || []).concat(t.later || []);
      if (ex.length) {
        h += `<div class="sec rise">${cap('Процедуры', '<span>по расписанию</span>')}<section class="card sn-card"><div class="sn-list">
          ${(t.extra || []).map(x => step(x, `сегодня · ${extraMeta(x)}`)).join('')}
          ${(t.later || []).map(x => `<div class="sn-later">${step(x, extraMeta(x))}</div>`).join('')}</div></section></div>`;
      }
    } else {
      h += `<section class="card sn-card sn-empty sn-hero rise"><span class="sn-ei">${I.face}</span>
        <p><b>Расскажи, какой у тебя уход</b>Напиши списком, что делаешь утром и вечером и какие маски бывают, — я разложу по шагам. Каждый день отмечай одним нажатием, а тренер будет видеть уход и советовать.</p>
        <div class="sn-btns"><button class="btn primary" data-a="sn-import">Заполнить списком</button><button class="btn" data-a="sn-edit">По шагам</button></div></section>`;
    }
    h += `<div class="sec rise">${cap('Как кожа сегодня')}<section class="card sn-card">${rateRow(t, true)}
      <input class="sn-note" type="text" maxlength="200" enterkeyhint="done" value="${esc(t.note || '')}" placeholder="Заметка: что заметил (по желанию)" aria-label="Заметка о коже за сегодня"></section></div>`;
    h += `<div class="sec rise">${cap('30 дней', s.rated ? `<span class="num">${s.rated} ${F.plural(s.rated, 'оценка', 'оценки', 'оценок')}</span>` : '')}<section class="card sn-card">
      <div data-sn-chart>${chart(d.diary || [])}</div><p class="sn-sum" data-sn-sum>${esc(s.summary || '')}</p></section></div>`;
    const notes = d.notes || {}, nt = [notes.type, notes.react, notes.avoid].filter(Boolean).join(' · ');
    h += `<div class="sec rise">${cap('Мой уход')}<div class="list">
      <button class="row tap" data-a="sn-edit"><span class="ic">${I.edit}</span><span class="main"><div class="title">${d.has ? 'Изменить уход' : 'Добавить по шагам'}</div><div class="meta">${d.has ? `утро ${r.am.length} · вечер ${r.pm.length} · процедуры ${r.extra.length}` : 'утро, вечер, маски по дням'}</div></span><span class="end chev"></span></button>
      <button class="row tap" data-a="sn-import"><span class="ic">${I.list}</span><span class="main"><div class="title">Заполнить списком</div><div class="meta">каждая строка — шаг</div></span><span class="end chev"></span></button>
      <button class="row tap" data-a="sn-notes"><span class="ic">${I.note}</span><span class="main"><div class="title">Заметки о коже</div><div class="meta">${esc(nt || 'тип кожи, на что реагирует, что не подходит')}</div></span><span class="end chev"></span></button>
      <button class="row tap" data-a="ask-go" data-v="Посмотри мой уход за кожей и оценки кожи: что улучшить?"><span class="ic">${F.I.coach}</span><span class="main"><div class="title">Спросить тренера</div><div class="meta">видит твой уход и оценки</div></span><span class="end chev"></span></button></div></div>`;
    h += '<p class="foot">Сыпь, сильные или болезненные прыщи, зуд, отёк, пятна, которые меняются, — скажи родителям и сходи к дерматологу. Новое средство сначала пробуй на маленьком участке.</p>';
    return h;
  };
  function chart(diary) {
    const n = diary.length, mid = diary[Math.floor(n / 2)];
    const dl = x => { const dd = F.parseDay(x.day); return `${dd.getDate()} ${F.MON_S[dd.getMonth()]}`; };
    return `<div class="sn-chart" role="img" aria-label="Оценки кожи за 30 дней">
      <div class="sn-ax" aria-hidden="true"><span>5</span><span>3</span><span>1</span></div>
      <div class="sn-bars">${diary.map((x, i) => `<span class="sn-bar${i === n - 1 ? ' now' : ''}${x.ok ? ' ok' : ''}" title="${esc(dl(x))}: ${x.r ? `${x.r} — ${WORD[x.r]}` : 'нет оценки'}">
        <i style="--h:${x.r ? x.r / 5 : 0}"${x.r ? '' : ' class="none"'}></i></span>`).join('')}</div></div>
      ${n ? `<div class="sn-xl" aria-hidden="true"><span>${esc(dl(diary[0]))}</span><span>${esc(dl(mid))}</span><span>сегодня</span></div>` : ''}
      <p class="sn-leg"><span><i class="sn-lb"></i>оценка</span><span><i class="sn-lo"></i>уход сделан</span></p>`;
  }

  // ---------- действия ----------
  F.actions['sn-open'] = () => { if (F.S.screen === 'skin') return; vib('tap'); F.openScreen('skin'); };
  F.actions['sn-retry'] = () => { F.S.snErr = false; F.render(false); };
  F.actions['sn-import'] = () => importSheet();
  F.actions['sn-edit'] = () => { if (!(F.S.sn && F.S.sn.routine)) { load(true).then(() => F.S.sn && F.S.sn.routine && editSheet()); return; } editSheet(); };
  F.actions['sn-notes'] = () => notesSheet();

  function findStep(id) {
    const t = today(); if (!t) return null;
    for (const p of ['am', 'pm', 'extra', 'later']) { const x = (t[p] || []).find(s => s.id === id); if (x) return [t, p, x]; }
    return null;
  }
  // точечно: строки шага, счётчики частей, «готово» у частей
  function patch(t, pop) {
    t.am_done = (t.am || []).filter(x => x.done).length; t.pm_done = (t.pm || []).filter(x => x.done).length;
    document.querySelectorAll('[data-sn-cnt]').forEach(el => { const p = el.dataset.snCnt; if (t[p + '_total']) el.textContent = cnt(t, p); });
    document.querySelectorAll('[data-sn-part]').forEach(el => el.classList.toggle('ok', partDone(t, el.dataset.snPart)));
    ['am', 'pm', 'extra', 'later'].forEach(p => (t[p] || []).forEach(x => document.querySelectorAll(`[data-sn-id="${CSS.escape(x.id)}"]`).forEach(b => {
      const was = b.classList.contains('on');
      b.classList.toggle('on', !!x.done); b.setAttribute('aria-pressed', !!x.done);
      if (pop && was !== !!x.done) { b.classList.remove('pop'); void b.offsetWidth; b.classList.add('pop'); }
    })));
    const st = document.querySelector('[data-sn-streak]'), d = data();
    if (st && d) st.textContent = d.streak;
  }
  F.actions['sn-mark'] = async (el, id) => {
    const f = findStep(id); if (!f) return;
    const [t, , x] = f;
    x.done = !x.done;
    vib(x.done ? 'select' : 'tap');
    patch(t, true);
    if ((x.done && t.am_total && t.am_done === t.am_total && (t.am || []).some(s => s.id === id))
      || (x.done && t.pm_total && t.pm_done === t.pm_total && (t.pm || []).some(s => s.id === id))) setTimeout(() => vib('success'), 140);
    try { await send('/api/skin/mark', { id, done: x.done }); const tt = today(); if (tt) patch(tt, false); }
    catch (e) { F.fail(e); await load(false); F.render(false); }
  };
  F.actions['sn-rate'] = async (el, v) => {
    const t = today(); if (!t) return;
    const n = Number(v);
    if (t.rating === n) return;
    t.rating = n; vib('select');
    document.querySelectorAll('[data-sn-rate]').forEach(box => {
      box.querySelectorAll('[data-a="sn-rate"]').forEach(b => { const on = Number(b.dataset.v) === n; b.classList.toggle('on', on); b.setAttribute('aria-pressed', on); });
      box.querySelector('[data-sn-word]').textContent = `${n} · ${WORD[n]}`;
    });
    try { await send('/api/skin/rate', { rating: n }); afterRate(); }
    catch (e) { F.fail(e); await load(false); F.render(false); }
  };
  // ответ сервера: график и вывод — тихо, только эти куски
  function afterRate() {
    const d = F.S.sn; if (!d || !d.diary) return;
    document.querySelectorAll('[data-sn-chart]').forEach(el => F.morph(el, chart(d.diary)));
    document.querySelectorAll('[data-sn-sum]').forEach(el => { el.textContent = (d.stats && d.stats.summary) || ''; });
  }
  async function saveNote(input) {
    const t = today(); if (!t) return;
    const note = input.value.trim().replace(/\s+/g, ' ').slice(0, 200);
    if (note === (t.note || '')) return;
    t.note = note;
    try { await send('/api/skin/rate', { note }); vib('select'); F.toast(note ? 'Заметка сохранена' : 'Заметка убрана', 'ok'); } catch (e) { F.fail(e); }
  }
  document.addEventListener('keydown', ev => {
    if (ev.key === 'Enter' && ev.target.classList && ev.target.classList.contains('sn-note')) { ev.preventDefault(); ev.stopPropagation(); ev.target.blur(); }
  }, true);
  document.addEventListener('focusout', ev => { if (ev.target.classList && ev.target.classList.contains('sn-note')) saveNote(ev.target); });

  // ---------- лист «Мой уход»: утро, вечер, процедуры ----------
  function editSheet() {
    vib('tap');
    const src = (F.S.sn && F.S.sn.routine) || { am: [], pm: [], extra: [] };
    const R = JSON.parse(JSON.stringify({ am: src.am, pm: src.pm, extra: src.extra.map(x => ({ id: x.id, title: x.title, product: x.product, days: x.days || [], weekly: x.weekly || 1 })) }));
    if (!R.am.length && !R.pm.length && !R.extra.length) { R.am.push({ title: '', product: '' }); R.pm.push({ title: '', product: '' }); }
    const lim = p => p === 'extra' ? 12 : 12;
    const row = (p, x, i) => `<div class="sn-er" data-p="${p}" data-i="${i}">
        <div class="sn-ef"><input type="text" maxlength="80" data-f="title" value="${esc(x.title)}" placeholder="${p === 'extra' ? 'Маска, скраб…' : 'Шаг: умыться, крем…'}" aria-label="${PART[p]}: шаг ${i + 1}">
          <input type="text" maxlength="80" data-f="product" value="${esc(x.product || '')}" placeholder="Средство — по желанию" aria-label="Средство"></div>
        <button type="button" class="btn icon sn-rm" data-rm="${p}|${i}" aria-label="Убрать">${I.x}</button>
        ${p === 'extra' ? `<div class="sn-eday"><div class="sn-days" role="group">${WD.map((w, k) => `<button type="button" class="${x.days.includes(k) ? 'on' : ''}" data-day="${i}|${k}" aria-pressed="${x.days.includes(k)}">${w}</button>`).join('')}</div>
          ${x.days.length ? `<span class="sn-ewk muted">по этим дням</span>` : `<div class="sn-ewk"><span>или раз в неделю</span><div class="pl-step"><button type="button" data-wk="${i}|-1" aria-label="Реже">−</button><b class="num">${x.weekly}</b><button type="button" data-wk="${i}|1" aria-label="Чаще">+</button></div></div>`}</div>` : ''}</div>`;
    const html = () => `<h2>Мой уход</h2>${['am', 'pm', 'extra'].map(p => `<div class="sn-eg"><div class="label">${PART[p]}${p === 'extra' ? ' · по дням или N раз в неделю' : ''}</div>
        ${R[p].map((x, i) => row(p, x, i)).join('')}
        ${R[p].length < lim(p) ? `<button type="button" class="btn sn-add" data-add="${p}">${F.I.plus}<span>${p === 'extra' ? 'Процедура' : 'Шаг'}</span></button>` : ''}</div>`).join('')}
      <button class="btn primary" type="button" data-save>Сохранить</button>`;
    const el = F.sheet('<div class="sn-ed"></div>');
    const box = el.querySelector('.sn-ed');
    box.innerHTML = html();
    const redraw = () => F.morph(box, html());
    el.addEventListener('input', ev => {
      const f = ev.target.dataset.f, r = ev.target.closest('.sn-er'); if (!f || !r) return;
      R[r.dataset.p][Number(r.dataset.i)][f] = ev.target.value;
    });
    el.addEventListener('click', async ev => {
      const add = ev.target.closest('[data-add]'), rm = ev.target.closest('[data-rm]'), dy = ev.target.closest('[data-day]'), wk = ev.target.closest('[data-wk]');
      if (add) {
        const p = add.dataset.add; R[p].push(p === 'extra' ? { title: '', product: '', days: [], weekly: 1 } : { title: '', product: '' });
        vib('select'); redraw();
        const inp = box.querySelectorAll(`.sn-er[data-p="${p}"] [data-f="title"]`); if (inp.length) inp[inp.length - 1].focus();
      } else if (rm) { const [p, i] = rm.dataset.rm.split('|'); R[p].splice(Number(i), 1); vib('tap'); redraw(); }
      else if (dy) {
        const [i, k] = dy.dataset.day.split('|').map(Number), x = R.extra[i];
        x.days = x.days.includes(k) ? x.days.filter(z => z !== k) : x.days.concat(k).sort((a, b) => a - b);
        if (x.days.length === 7) { x.days = x.days.filter(z => z !== k); F.toast('Каждый день — это шаг утра или вечера', 'err'); return; }
        vib('select'); redraw();
      } else if (wk) { const [i, k] = wk.dataset.wk.split('|').map(Number); R.extra[i].weekly = Math.max(1, Math.min(6, R.extra[i].weekly + k)); vib('select'); redraw(); }
      else if (ev.target.closest('[data-save]')) {
        const clean = p => R[p].filter(x => x.title.trim()).map(x => {
          const o = { title: x.title.trim(), product: (x.product || '').trim() }; if (x.id) o.id = x.id;
          if (p === 'extra') { if (x.days.length) o.days = x.days; else o.weekly = x.weekly; }
          return o;
        });
        await F.busy(ev.target.closest('[data-save]'), async () => {
          try {
            await send('/api/skin/save', { routine: { am: clean('am'), pm: clean('pm'), extra: clean('extra') } });
            vib('success'); F.toast('Уход сохранён', 'ok'); el.close(); F.render(false);
          } catch (e) { F.fail(e); }
        });
      }
    });
    el.addEventListener('keydown', ev => { if (ev.key === 'Enter' && ev.target.tagName === 'INPUT') { ev.preventDefault(); const b = el.querySelector('[data-save]'); if (b) b.click(); } });
  }

  // ---------- лист «Заметки о коже» ----------
  function notesSheet() {
    vib('tap');
    const n = (F.S.sn && F.S.sn.notes) || {};
    const f = (k, t, ph) => `<label class="sn-f"><span>${t}</span><textarea rows="2" maxlength="300" data-k="${k}" placeholder="${ph}">${esc(n[k] || '')}</textarea></label>`;
    const el = F.sheet(`<h2>Заметки о коже</h2><p class="small muted">Тренер учитывает их в советах.</p>
      ${f('type', 'Тип кожи и особенности', 'Например: жирная в Т-зоне, сухая на щеках')}
      ${f('react', 'На что реагирует', 'Например: краснеет от горячей воды')}
      ${f('avoid', 'Что не подходит', 'Например: спиртовые лосьоны')}
      <button class="btn primary" type="button" data-save>Сохранить</button>`);
    el.addEventListener('click', async ev => {
      const b = ev.target.closest('[data-save]'); if (!b) return;
      const notes = {}; el.querySelectorAll('[data-k]').forEach(t => { notes[t.dataset.k] = t.value.trim(); });
      await F.busy(b, async () => {
        try { await send('/api/skin/save', { notes }); vib('success'); F.toast('Сохранено', 'ok'); el.close(); F.render(false); } catch (e) { F.fail(e); }
      });
    });
  }

  // ---------- «Заполнить списком» ----------
  function importSheet() {
    vib('tap');
    let text = F.S.snDraft || '', prev = null;
    const drop = new Set();
    const keys = () => ['am', 'pm', 'extra'].flatMap(p => (prev[p] || []).filter(x => !drop.has(x.key)));
    const nw = { type: 'Тип кожи', react: 'Реагирует на', avoid: 'Не подходит' };
    const html = () => prev ? `<h2>Проверь уход</h2>
        ${['am', 'pm', 'extra'].map(p => { const xs = (prev[p] || []).filter(x => !drop.has(x.key)); return xs.length ? `<div class="label">${PART[p]}</div><div class="list sn-prev">${xs.map(x => `<div class="row"><span class="ic">${p === 'am' ? I.sun : p === 'pm' ? F.I.moon : I.jar}</span>
          <span class="main"><div class="title wrap">${esc(x.title)}</div>${x.product || x.sched ? `<div class="meta">${esc([x.product, x.sched].filter(Boolean).join(' · '))}</div>` : ''}</span>
          <button type="button" class="btn icon sn-rm" data-drop="${esc(x.key)}" aria-label="Убрать">${I.x}</button></div>`).join('')}</div>` : ''; }).join('')}
        ${Object.keys(prev.notes || {}).length ? `<div class="label">Заметки</div><div class="list sn-prev">${Object.entries(prev.notes).map(([k, v]) => `<div class="row"><span class="ic">${I.note}</span><span class="main"><div class="title wrap">${esc(v)}</div><div class="meta">${nw[k] || ''}</div></span></div>`).join('')}</div>` : ''}
        ${!keys().length && !Object.keys(prev.notes || {}).length ? '<p class="muted">Нечего добавлять.</p>' : ''}
        ${prev.dup.length ? `<p class="small muted">Уже есть: ${esc(prev.dup.join(', '))}</p>` : ''}
        ${prev.over.length ? `<p class="small muted">Не влезло (максимум 12 в части): ${esc(prev.over.join(', '))}</p>` : ''}
        ${prev.skipped.length ? `<p class="small muted">Не понял строки: ${esc(prev.skipped.join(', '))}</p>` : ''}
        ${keys().length || Object.keys(prev.notes || {}).length ? `<button class="btn primary" type="button" data-go>Добавить${keys().length ? ` ${keys().length} ${F.plural(keys().length, 'шаг', 'шага', 'шагов')}` : ' заметки'}</button>` : ''}
        <button class="btn" type="button" data-back>Изменить текст</button>`
      : `<h2>Заполнить списком</h2>
        <p class="small muted sn-help">Каждая строка — шаг. Понимаю «утром», «вечером», «на ночь», «утром и вечером», заголовки «Утро:» и «Вечер:» (шаги через запятую), дни («вт, сб»), «2 раза в неделю». Средство — после тире или в скобках. «Тип кожи: …», «Реагирует на …», «Не подходит: …» — в заметки.</p>
        <textarea id="sn-text" rows="8" maxlength="4000" placeholder="Утро: умыться гелем, крем&#10;Вечер: умыться, крем на ночь&#10;Маска 2 раза в неделю: вт, сб&#10;Скраб раз в неделю&#10;Тип кожи: комбинированная">${esc(text)}</textarea>
        <button class="btn primary" type="button" data-parse>Разобрать</button>`;
    const el = F.sheet('<div class="sn-ed"></div>');
    const box = el.querySelector('.sn-ed');
    const redraw = () => { box.innerHTML = html(); el.scrollTop = 0; };
    redraw();
    if (matchMedia('(pointer: fine)').matches) setTimeout(() => { const t = el.querySelector('#sn-text'); if (t) t.focus(); }, 350);
    el.addEventListener('input', ev => { if (ev.target.id === 'sn-text') { text = ev.target.value; F.S.snDraft = text; } });
    el.addEventListener('click', async ev => {
      if (ev.target.closest('[data-parse]')) {
        if (!text.trim()) { F.toast('Напиши уход — каждый шаг с новой строки', 'err'); return; }
        await F.busy(ev.target.closest('[data-parse]'), async () => {
          try {
            const r = await F.api('/api/skin/import', { text, preview: true });
            if (!r || !Array.isArray(r.am)) throw new Error('Не разобралось — попробуй ещё раз');
            prev = { dup: [], over: [], skipped: [], notes: {}, ...r }; drop.clear(); vib('select'); redraw();
          } catch (e) { F.fail(e); }
        });
      } else if (ev.target.closest('[data-drop]')) {
        const b = ev.target.closest('[data-drop]'), r = b.closest('.row');
        drop.add(b.dataset.drop); vib('select');
        if (r) { r.classList.add('out'); setTimeout(redraw, 200); } else redraw();
      } else if (ev.target.closest('[data-back]')) { prev = null; vib('select'); redraw(); }
      else if (ev.target.closest('[data-go]')) {
        await F.busy(ev.target.closest('[data-go]'), async () => {
          try {
            const r = await send('/api/skin/import', { text, drop: [...drop] });
            const a = r.added || {}, n = (a.am || 0) + (a.pm || 0) + (a.extra || 0);
            F.S.snDraft = ''; vib('success'); F.toast(n ? `Добавлено шагов: ${n}` : 'Заметки сохранены', 'ok'); el.close();
            if (F.S.screen !== 'skin') F.openScreen('skin'); else F.render(false);
          } catch (e) { F.fail(e); }
        });
      }
    });
  }
});
