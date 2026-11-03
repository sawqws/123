/* skin — «Кожа»: мой уход утром и вечером, процедуры по расписанию, заметки о коже, отметки на сегодня, серия,
   разбор фото лба (Claude, на сервере в фоне). Карточка на главной, экран ?p=skin, пункт в «＋», вечерний уход —
   в чек-листе «Режима» (F.slots.rgEvening; одинаковые пункты режима прячет F.rgHide).
   Данные — F.S.sn (полный ответ /api/skin) или F.S.data.ext.skin (кратко из /api/all). Сервер — coach/f_skin.py.
   Отметка меняет только свою строку (без перерисовки экрана); запросы — по очереди.
   Ручной оценки 1–5 нет (Глеб 03.11: «убери оценку — ты по моей фотке разбирай и говори, что да как»): после фото —
   строка «Разбираю фото…» (тихий опрос раз в 3 с, до 60 с), потом «Последний разбор: Всё нормально · 03.11» или
   «Есть что поправить · 03.11»; полный текст — листом по нажатию, сам ничего не открывается. На экране — лента разборов.
   Фото лба (раз в 3 дня вечером после умывания): строка «Фото лба» (карточка и «Процедуры»), фронтальная камера,
   сжатие ≤ 1280 px; снимок крупно не показывается — миниатюра улетает в значок с числом снимков (угол карточки
   или экрана); лист «Фото лба» — сетка размытых миниатюр, нажатие — открыть одно, удалить. */
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
    cam: svgi('<path d="M3.8 8.6A1.6 1.6 0 0 1 5.4 7h2.3l1.6-2.2h5.4L16.3 7h2.3a1.6 1.6 0 0 1 1.6 1.6v9a1.6 1.6 0 0 1-1.6 1.6H5.4a1.6 1.6 0 0 1-1.6-1.6z"/><circle cx="12" cy="12.8" r="3.5"/>'),
    left: svgi('<path d="M14.5 5.5L8 12l6.5 6.5"/>'),
    right: svgi('<path d="M9.5 5.5L16 12l-6.5 6.5"/>'),
    hide: svgi('<path d="M3.5 12s3-6 8.5-6c1.6 0 3 .5 4.2 1.2M20.5 12s-3 6-8.5 6c-1.6 0-3-.5-4.2-1.2"/><path d="M4.5 19.5l15-15"/>'),
  };
  const WD = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
  const AIW = { ok: 'Всё нормально', attention: 'Есть что поправить', none: 'Не удалось разобрать', wait: 'Разбираю фото…' };
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
      const old = F.S.data.ext.skin;
      F.S.data.ext.skin = { has: r.has, today: r.today, streak: r.streak,
        ai: r.ai ? { last: r.ai.last } : (old && old.ai) || null,
        notes: r.notes ? Object.values(r.notes).some(Boolean) : false };
    }
  }
  const lastAi = () => { const d = data(); return (d && d.ai && d.ai.last) || null; };
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
    try { put(await F.api('/api/skin', {})); F.S.snFail = F.S.snErr = false; setTimeout(watch, 0); } catch (e) { F.S.snFail = !data(); if (screen) { F.S.snErr = true; F.fail(e); } }
    F.S.snLoading = false;
    if (F.S.screen === 'skin' || (!F.S.screen && F.S.tab === 'today')) F.render(false);
  }
  F.onData.push(d => {
    const e = d.ext && d.ext.skin;
    if (e && e.today && F.S.sn && !pending) {
      const a = e.ai && e.ai.last, b = F.S.sn.ai && F.S.sn.ai.last;
      F.S.sn = { ...F.S.sn, today: e.today, streak: e.streak, has: e.has };
      // разбор поменялся (готов на сервере, пока приложение было свёрнуто) — ленту и сводку подтянем тихо
      if (a && (!b || a.day !== b.day || a.status !== b.status)) { F.S.sn.ai = { ...(F.S.sn.ai || {}), last: a }; if (a.status !== 'wait') load(false); }
    }
    if (!e && !F.S.snTried) { F.S.snTried = true; load(false); }
    setTimeout(watch, 0);
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
  // ---------- разбор фото лба: строка «Последний разбор», лента, лист с текстом ----------
  const dd = s => { const d = F.parseDay(s); return `${String(d.getDate()).padStart(2, '0')}.${String(d.getMonth() + 1).padStart(2, '0')}`; };
  // статус точкой без цвета: ○ всё нормально, ● есть что поправить, пунктир — не вышло, мигает — разбираю
  const dot = st => `<span class="sn-ad ${esc(st)}" aria-hidden="true"></span>`;
  const short = x => {
    const lines = String(x.text || '').split('\n').map(s => s.replace(/^\s*[-•*]\s*/, '').replace(/\*\*/g, '').trim()).filter(Boolean);
    if (x.status === 'wait') return 'обычно до минуты';
    if (x.status === 'ok') return lines[1] || 'идём дальше';
    return lines[0] || '';
  };
  function aiRow(a) {
    if (!a) return '';
    const wait = a.status === 'wait';
    return `<button type="button" class="sn-ai${wait ? ' wait' : ''}" data-a="sn-ai" data-v="${esc(a.day)}"${wait ? ' aria-busy="true"' : ''}
      aria-label="Последний разбор кожи: ${esc(AIW[a.status] || '')}, ${esc(dd(a.day))}">${dot(a.status)}
      <span class="sn-ct"><small>Последний разбор</small><b>${wait ? AIW.wait : `${esc(AIW[a.status] || '')} <span class="sn-fd num">· ${esc(dd(a.day))}</span>`}</b></span>
      <span class="chev" aria-hidden="true"></span></button>`;
  }
  function feed(d) {
    const xs = (d.ai && d.ai.days) || [], s = d.stats || {};
    const list = xs.length ? `<div class="sn-feed">${xs.map(x => `<button type="button" class="sn-fr" data-a="sn-ai" data-v="${esc(x.day)}">${dot(x.status)}
        <span class="sn-ct"><b>${esc(AIW[x.status] || '')} <span class="sn-fd num">· ${esc(dd(x.day))}</span></b><small>${esc(short(x))}</small></span>
        <span class="chev" aria-hidden="true"></span></button>`).join('')}</div>` : '';
    return `${list}<p class="sn-sum">${esc(s.summary || '')}</p>`;
  }
  const aiStat = s => s && s.total ? `<b class="num">${s.ok}/${s.total}</b><span>всё нормально</span>` : '<b class="num">—</b><span>разборов пока нет</span>';

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
    return `<div class="sec rise">${cap('Кожа', `${badge(t)}<button class="link" data-a="sn-open" aria-label="Кожа: ${d.streak >= 2 ? `серия ${days(d.streak)}` : 'все шаги и дневник'}">${d.streak >= 2 ? `<span class="sn-fl num">${F.I.flame}${d.streak}</span>` : 'Все'}</button>`)}
      <section class="card sn-card"><div class="sn-parts">${partBtn(t, 'am')}${partBtn(t, 'pm')}</div>
        ${list.length ? `<div class="sn-list"><div class="sn-lh">${p === 'am' ? 'Утро' : 'Уход на вечер'}</div>${list.map(x => step(x)).join('')}</div>` : ''}
        ${more > 0 || due.length ? `<button class="sn-more" data-a="sn-open"><span>${due.length ? `Сегодня ещё: ${esc(due.map(x => x.title.toLowerCase()).join(', '))}` : `Ещё ${more}`}</span><span class="chev"></span></button>` : ''}
        <div class="sn-shotbox" data-sn-shotbox>${shotRow(t)}</div>
        <div class="sn-aibox" data-sn-ai>${aiRow(lastAi())}</div></section></div>`;
  });
  F.slots.plus.push(row => row('sn-open', '', I.face, 'var(--ink)', 'Кожа', 'уход и разбор по фото'));

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
          <div data-sn-aistat>${aiStat(s)}</div></div></section>`;
      ['am', 'pm'].forEach(p => {
        if (!(t[p] || []).length) return;
        h += `<div class="sec rise">${cap(PART[p], `<span class="num" data-sn-cnt="${p}">${cnt(t, p)}</span>`)}<section class="card sn-card"><div class="sn-list">${t[p].map(x => step(x)).join('')}</div></section></div>`;
      });
      // процедуры по расписанию; «Фото лба» (раз в 3 дня) — первой строкой: в свой день — как отметка дня, в другие — тише
      h += `<div class="sec rise">${cap('Процедуры', badge(t))}<section class="card sn-card"><div class="sn-list">
          <div class="sn-shotbox" data-sn-shotbox>${shotRow(t)}</div>
          ${(t.extra || []).map(x => step(x, `сегодня · ${extraMeta(x)}`)).join('')}
          ${(t.later || []).map(x => `<div class="sn-later">${step(x, extraMeta(x))}</div>`).join('')}</div></section></div>`;
    } else {
      h += `<section class="card sn-card sn-empty sn-hero rise"><span class="sn-ei">${I.face}</span>
        <p><b>Расскажи, какой у тебя уход</b>Напиши списком, что делаешь утром и вечером и какие маски бывают, — я разложу по шагам. Каждый день отмечай одним нажатием, а тренер будет видеть уход и советовать.</p>
        <div class="sn-btns"><button class="btn primary" data-a="sn-import">Заполнить списком</button><button class="btn" data-a="sn-edit">По шагам</button></div></section>`;
      h += `<div class="sec rise">${cap('Фото лба', badge(t))}<section class="card sn-card"><div class="sn-list"><div class="sn-shotbox" data-sn-shotbox>${shotRow(t)}</div></div></section></div>`;
    }
    h += `<div class="sec rise">${cap('Разборы по фото')}<section class="card sn-card" data-sn-feed>${feed(d)}</section></div>`;
    const notes = d.notes || {}, nt = [notes.type, notes.react, notes.avoid].filter(Boolean).join(' · ');
    h += `<div class="sec rise">${cap('Мой уход')}<div class="list">
      <button class="row tap" data-a="sn-edit"><span class="ic">${I.edit}</span><span class="main"><div class="title">${d.has ? 'Изменить уход' : 'Добавить по шагам'}</div><div class="meta">${d.has ? `утро ${r.am.length} · вечер ${r.pm.length} · процедуры ${r.extra.length}` : 'утро, вечер, маски по дням'}</div></span><span class="end chev"></span></button>
      <button class="row tap" data-a="sn-import"><span class="ic">${I.list}</span><span class="main"><div class="title">Заполнить списком</div><div class="meta">каждая строка — шаг</div></span><span class="end chev"></span></button>
      <button class="row tap" data-a="sn-notes"><span class="ic">${I.note}</span><span class="main"><div class="title">Заметки о коже</div><div class="meta">${esc(nt || 'тип кожи, на что реагирует, что не подходит')}</div></span><span class="end chev"></span></button>
      <button class="row tap" data-a="ask-go" data-v="Посмотри мой уход за кожей и последние разборы фото: что улучшить?"><span class="ic">${F.I.coach}</span><span class="main"><div class="title">Спросить тренера</div><div class="meta">видит твой уход и разборы фото</div></span><span class="end chev"></span></button></div></div>`;
    h += '<p class="foot">Сыпь, сильные или болезненные прыщи, зуд, отёк, пятна, которые меняются, — скажи родителям и сходи к дерматологу. Новое средство сначала пробуй на маленьком участке.</p>';
    return h;
  };
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
  // ---------- разбор фото: тихий опрос, точечное обновление, лист ----------
  let polling = false, aiOpen = null;  // aiOpen — открытый лист разбора {el, day, draw}
  function patchAi() {
    const d = data(); if (!d) return;
    document.querySelectorAll('[data-sn-ai]').forEach(el => F.morph(el, aiRow(lastAi())));
    if (F.S.sn && F.S.sn.ai) {
      document.querySelectorAll('[data-sn-feed]').forEach(el => F.morph(el, feed(F.S.sn)));
      document.querySelectorAll('[data-sn-aistat]').forEach(el => F.morph(el, aiStat(F.S.sn.stats)));
    }
    if (aiOpen && aiOpen.el.isConnected) aiOpen.draw();
  }
  // идёт разбор — раз в 3 с спрашиваем сервер (до 60 с), меняем только строки разбора
  function watch() {
    const a = lastAi();
    if (polling || !a || a.status !== 'wait') return;
    polling = true;
    const t0 = Date.now();
    const tick = async () => {
      try { const r = await F.api('/api/skin', {}); if (!pending) put(r); } catch (e) { /* сеть — попробуем ещё раз */ }
      const b = lastAi();
      if (b && b.status === 'wait' && Date.now() - t0 < 60000) { setTimeout(tick, 3000); return; }
      polling = false;
      patchAi();
      if (b && b.status !== 'wait') { vib('select'); document.querySelectorAll('.sn-ai').forEach(el => { el.classList.remove('new'); void el.offsetWidth; el.classList.add('new'); }); }
    };
    setTimeout(tick, 3000);
  }
  const findAi = day => {
    const xs = (F.S.sn && F.S.sn.ai && F.S.sn.ai.days) || [], l = lastAi();
    return xs.find(x => x.day === day) || (l && l.day === day ? l : null);
  };
  function aiSheet(day) {
    vib('tap');
    const el = F.sheet('<div class="sn-as" data-sn-as></div>', () => { if (aiOpen && aiOpen.el === el) aiOpen = null; });
    const box = el.querySelector('[data-sn-as]');
    const q = x => `Разбор кожи по фото от ${dd(x.day)}: ${short(x)}. Что ещё сделать в уходе?`;
    const html = () => {
      const x = findAi(day);
      if (!x) return '<h2>Разбор кожи</h2><p class="muted">Разбора за этот день нет.</p>';
      const meta = [F.dateLine(x.day), x.time, x.compare ? `по сравнению с прошлым фото — ${x.compare}` : ''].filter(Boolean).join(' · ');
      const body = x.status === 'wait' ? '<p class="sn-aw">Смотрю фото — обычно до минуты. Можно закрыть, разбор появится сам.</p>'
        : `<div class="prose sn-ap">${F.md(x.text || '')}</div>`;
      const btns = x.status === 'wait' ? ''
        : x.status === 'none' ? (x.retake ? '<button class="btn primary" type="button" data-shoot>Переснять</button>' : '<button class="btn primary" type="button" data-re>Повторить</button>')
          : `<button class="btn" type="button" data-a="ask-go" data-v="${esc(q(x))}">${F.I.coach}<span>Спросить тренера</span></button><button class="btn" type="button" data-re>Разобрать заново</button>`;
      const th = thumbs.get(x.day);  // миниатюра уже в памяти — сразу в разметке (morph не снимет её при обновлении)
      return `<div class="sn-ah"><span class="sn-ath"><img alt="" data-day="${esc(x.day)}"${th ? ` src="${th}" class="in"` : ''}></span>
          <div class="sn-at"><h2>Разбор кожи</h2><div class="sn-ak">${dot(x.status)}<b>${esc(AIW[x.status] || '')}</b></div><p class="small muted">${esc(meta)}</p></div></div>
        ${body}${btns ? `<div class="sn-abtns">${btns}</div>` : ''}
        ${x.status === 'attention' ? '<p class="foot sn-af">Это не диагноз. Сильные или болезненные прыщи, гной, отёк — покажи дерматологу и скажи родителям.</p>' : ''}`;
    };
    const thumb = async () => {
      const img = box.querySelector('.sn-ath img'); if (!img || img.classList.contains('in')) return;
      try { const src = await getImg(img.dataset.day, true); if (src && img.isConnected) { img.onload = () => img.classList.add('in'); img.src = src; } }
      catch (e) { img.closest('.sn-ath').classList.add('none'); }
    };
    const draw = () => { F.morph(box, html()); thumb(); };
    box.innerHTML = html(); thumb();
    aiOpen = { el, day, draw };
    el.addEventListener('click', async ev => {
      if (ev.target.closest('[data-a="ask-go"]')) { el.close(); return; }
      if (ev.target.closest('[data-shoot]')) { el.close(); F.actions['sn-photo'](); return; }
      const re = ev.target.closest('[data-re]'); if (!re) return;
      await F.busy(re, async () => {
        try { await send('/api/skin/analyze', { day }); vib('select'); draw(); patchAi(); watch(); }
        catch (e) { F.fail(e); }
      });
    });
  }
  F.actions['sn-ai'] = (el, day) => { if (day) aiSheet(day); };

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

  // ---------- фото лба: раз в 3 дня вечером после умывания ----------
  // Снимок не показываем крупно: миниатюра улетает в значок с числом снимков. Открыть — только по нажатию на значок.
  const photo = t => (t && t.photo) || { due: false, done: false, count: 0, next: null, every: 3 };
  const evening = () => new Date().getHours() >= 17;
  const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  const dayShort = s => { const d = F.parseDay(s); return `${d.getDate()} ${F.MON_S[d.getMonth()]}`; };
  function nextWord(t, ph) {
    if (!ph.next || !t || !t.day) return `раз в ${ph.every || 3} дня`;
    const n = Math.round((F.parseDay(ph.next) - F.parseDay(t.day)) / 864e5);
    const wd = WD[(F.parseDay(ph.next).getDay() + 6) % 7].toLowerCase();
    return `следующее ${n === 1 ? 'завтра' : n === 2 ? 'послезавтра' : `в ${wd}`} · раз в ${ph.every || 3} дня`;
  }
  function shotRow(t) {
    const ph = photo(t), ev = evening(), todo = ph.due && !ph.done;
    const sub = ph.done ? 'сегодня готово · можно переснять' : todo ? (ev ? 'сейчас, после умывания' : 'вечером, после умывания') : nextWord(t, ph);
    return `<button type="button" class="sn-shot${todo ? ' due' : ''}${todo && ev ? ' hot' : ''}${ph.done ? ' on' : ''}${!ph.due ? ' quiet' : ''}" data-a="sn-photo" data-sn-shot
      aria-label="Фото лба: ${esc(sub)}"><span class="sn-sb" aria-hidden="true">${ph.done ? F.I.check : I.cam}</span>
      <span class="sn-ct"><b>Фото лба</b><small>${esc(sub)}</small></span>${todo ? '<span class="sn-go" aria-hidden="true">Снять</span>' : ''}</button>`;
  }
  const badge = t => { const n = photo(t).count || 0; return `<button type="button" class="sn-ph${n ? '' : ' zero'}" data-a="sn-photos" data-sn-ph
      aria-label="Фото лба: ${n} ${F.plural(n, 'снимок', 'снимка', 'снимков')}"${n ? '' : ' tabindex="-1"'}>${I.cam}<b class="num" data-sn-phn>${n}</b></button>`; };
  const inView = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth; };
  // только счётчики и строки «Фото лба» — без перерисовки экрана
  function patchPhoto() {
    const t = today(); if (!t) return;
    const n = photo(t).count || 0;
    document.querySelectorAll('[data-sn-ph]').forEach(b => {
      b.classList.toggle('zero', !n); b.querySelector('[data-sn-phn]').textContent = n;
      b.setAttribute('aria-label', `Фото лба: ${n} ${F.plural(n, 'снимок', 'снимка', 'снимков')}`);
      if (n) b.removeAttribute('tabindex'); else b.setAttribute('tabindex', '-1');
    });
    document.querySelectorAll('[data-sn-shotbox]').forEach(el => F.morph(el, shotRow(t)));
  }

  F.actions['sn-photo'] = () => {
    vib('tap');
    let inp = document.getElementById('sn-cam');
    if (!inp) {
      inp = Object.assign(document.createElement('input'), { type: 'file', accept: 'image/*', id: 'sn-cam', hidden: true });
      inp.setAttribute('capture', 'user');  // фронтальная камера — лоб снимаешь сам
      inp.setAttribute('aria-hidden', 'true');
      document.body.append(inp);
    }
    inp.value = '';
    inp.onchange = () => { const f = inp.files && inp.files[0]; inp.value = ''; if (f) gotPhoto(f); };
    inp.click();
  };
  F.actions['sn-photos'] = () => photosSheet();

  // сжатие, как shrink в app.js: ≤ side px, JPEG; не влезло в лимит — тише качество, потом меньше сторона
  async function jpeg(file, side, max) {
    const url = URL.createObjectURL(file);
    try {
      const img = await new Promise((ok, bad) => { const i = new Image(); i.onload = () => ok(i); i.onerror = bad; i.src = url; });
      let k = Math.min(1, side / Math.max(img.naturalWidth, img.naturalHeight)), q = .82, out = '';
      const cv = document.createElement('canvas');
      for (let i = 0; i < 6; i++) {
        cv.width = Math.max(1, Math.round(img.naturalWidth * k)); cv.height = Math.max(1, Math.round(img.naturalHeight * k));
        cv.getContext('2d').drawImage(img, 0, 0, cv.width, cv.height);
        out = cv.toDataURL('image/jpeg', q);
        if (out.length <= max) return out;
        if (q > .62) q -= .1; else k *= .8;
      }
      if (out.length <= max) return out;
      throw new Error('big');
    } finally { URL.revokeObjectURL(url); }
  }
  let saving = false;
  async function gotPhoto(file) {
    if (saving) return;
    const t = today(); if (!t) return;
    saving = true;
    let full, thumb;
    try { [full, thumb] = await Promise.all([jpeg(file, 1280, 840000), jpeg(file, 240, 60000)]); }
    catch (e) { saving = false; vib('error'); F.toast('Не получилось прочитать фото — попробуй ещё раз', 'err'); return; }
    const ph = photo(t), was = { ...ph };
    t.photo = { ...ph, count: (ph.count || 0) + (ph.done ? 0 : 1), done: true, due: true, last: t.day };
    const req = send('/api/skin/photo', { image: full, thumb });
    const landed = fly(thumb, t.photo.count);
    try {
      const r = await req;
      thumbs.delete(t.day);
      if (r && r.photos) F.S.snPhotos = r.photos;
      await landed;
      vib('success'); F.toast('Фото сохранено — разбираю', 'ok');
      patchAi(); watch();
    } catch (e) {
      await landed;
      const tt = today(); if (tt) tt.photo = was;
      vib('error'); F.fail(e);
    }
    saving = false;
    patchPhoto();
  }
  // миниатюра улетает в значок (в углу карточки) или в уголок экрана; крупно снимок не показываем
  function fly(src, n) {
    const done = () => { patchPhoto(); document.querySelectorAll('[data-sn-ph]').forEach(b => { if (inView(b)) { b.classList.remove('bump'); void b.offsetWidth; b.classList.add('bump'); } }); };
    if (reduced()) { done(); return Promise.resolve(); }
    const from = [...document.querySelectorAll('[data-sn-shot] .sn-sb')].find(inView);
    let to = [...document.querySelectorAll('[data-sn-ph]')].find(b => inView(b) && b.getBoundingClientRect().top > 0), corner = null;
    if (!to) {  // значка не видно (прокрутил) — временный значок в правом верхнем углу экрана
      corner = document.createElement('div');
      corner.className = 'sn-ph sn-corner'; corner.setAttribute('aria-hidden', 'true');
      corner.innerHTML = `${I.cam}<b class="num">${n - 1}</b>`;
      document.body.append(corner);
      requestAnimationFrame(() => corner.classList.add('show'));
      to = corner;
    }
    const a = from ? from.getBoundingClientRect() : { left: innerWidth / 2 - 14, top: innerHeight * .62, width: 28, height: 28 };
    const b = to.getBoundingClientRect(), bw = to.offsetWidth || b.width;  // скрытый (0 снимков) значок сжат transform — берём настоящий размер
    const S = 64, x0 = a.left + a.width / 2 - S / 2, y0 = a.top + a.height / 2 - S / 2;
    const dx = b.left + b.width / 2 - S / 2 - x0, dy = b.top + b.height / 2 - S / 2 - y0, k = bw / S;
    const el = document.createElement('div');
    el.className = 'sn-fly'; el.setAttribute('aria-hidden', 'true');
    el.style.cssText = `left:${x0}px;top:${y0}px;background-image:url("${src}")`;
    document.body.append(el);
    const anim = el.animate([
      { transform: 'translate(0,0) scale(.3)', opacity: 0 },
      { transform: 'translate(0,-28px) scale(1)', opacity: 1, offset: .26 },
      { transform: `translate(${dx * .55}px,${Math.min(dy * .55, -28 + dy * .3)}px) scale(${(1 + k) / 2})`, opacity: 1, offset: .62 },
      { transform: `translate(${dx}px,${dy}px) scale(${k})`, opacity: .9, offset: .94 },
      { transform: `translate(${dx}px,${dy}px) scale(${k * .6})`, opacity: 0 }],
    { duration: 780, easing: 'cubic-bezier(.32,.72,0,1)', fill: 'forwards' });
    return anim.finished.catch(() => {}).then(() => {
      el.remove(); done();
      if (corner) {
        corner.querySelector('b').textContent = n;
        corner.classList.remove('bump'); void corner.offsetWidth; corner.classList.add('bump');
        setTimeout(() => { corner.classList.remove('show'); setTimeout(() => corner.remove(), 400); }, 1300);
      }
    });
  }

  // ---------- лист «Фото лба»: сетка размытых миниатюр по датам, нажатие — одно фото, удалить ----------
  const thumbs = new Map();  // день → data:URL миниатюры (только в памяти, пока открыто приложение)
  async function getImg(day, thumb) {
    if (thumb && thumbs.has(day)) return thumbs.get(day);
    const r = await F.api('/api/skin/photo/get', thumb ? { day, thumb: true } : { day });
    if (thumb && r && r.image) thumbs.set(day, r.image);
    return r && r.image;
  }
  function photosSheet() {
    vib('tap');
    let list = F.S.snPhotos || null, cur = -1, sure = 0;
    const keys = ev => {  // ПК: ← → — соседние снимки, Backspace — к сетке (Esc закрывает лист сам)
      if (cur < 0 || ev.target.closest && ev.target.closest('input, textarea')) return;
      if (ev.key === 'ArrowLeft') { ev.preventDefault(); go(-1); } else if (ev.key === 'ArrowRight') { ev.preventDefault(); go(1); }
      else if (ev.key === 'Backspace') { ev.preventDefault(); cur = -1; sure = 0; show(); }
    };
    document.addEventListener('keydown', keys);
    const el = F.sheet('<div class="sn-phs" data-sn-phs></div>', () => { document.removeEventListener('keydown', keys); token++; });
    const box = el.querySelector('[data-sn-phs]');
    const grid = () => !list ? `<h2>Фото лба</h2><div class="sn-grid">${'<span class="sn-tile sk"><span class="sn-tw"></span></span>'.repeat(6)}</div>`
      : `<h2>Фото лба</h2><p class="small muted sn-phh">${list.length ? `${list.length} ${F.plural(list.length, 'снимок', 'снимка', 'снимков')} · видишь только ты, тренер смотрит их только для разбора. Нажми, чтобы открыть.` : 'Пока нет снимков. Раз в 3 дня вечером после умывания — так видно, как меняется кожа.'}</p>
        ${list.length ? `<div class="sn-grid">${list.map((p, i) => `<button type="button" class="sn-tile" data-i="${i}" aria-label="Фото ${esc(dayShort(p.day))}">
          <span class="sn-tw"><img alt="" data-day="${esc(p.day)}"><span class="sn-eye" aria-hidden="true">${I.hide}</span></span><small>${esc(dayShort(p.day))}</small></button>`).join('')}</div>`
        : `<button class="btn primary sn-first" type="button" data-shoot>${I.cam}<span>Сделать фото</span></button>`}`;
    const view = () => { const p = list[cur]; return `<div class="sn-view">
        <div class="sn-vbar"><button type="button" class="sn-vb" data-back aria-label="Все фото">${I.left}<span>Все</span></button>
          <b>${esc(F.dateLine(p.day))}</b><span class="sn-vn num">${cur + 1} из ${list.length}</span></div>
        <div class="sn-vimg"><img alt="Фото лба ${esc(dayShort(p.day))}"></div>
        <div class="sn-vnav"><button type="button" class="btn icon" data-step="-1" aria-label="Новее"${cur > 0 ? '' : ' disabled'}>${I.left}</button>
          <button type="button" class="btn sn-del${sure ? ' sure' : ''}" data-del>${sure ? 'Точно удалить?' : 'Удалить'}</button>
          <button type="button" class="btn icon" data-step="1" aria-label="Старее"${cur < list.length - 1 ? '' : ' disabled'}>${I.right}</button></div></div>`; };
    let token = 0;
    async function fill() {  // миниатюры по 3 за раз, новые сверху
      const my = ++token;
      const imgs = [...box.querySelectorAll('img[data-day]')];
      let i = 0;
      const one = async () => {
        while (i < imgs.length && my === token) {
          const img = imgs[i++];
          try { const src = await getImg(img.dataset.day, true); if (my !== token || !src) continue; img.onload = () => img.classList.add('in'); img.src = src; } catch (e) { /* пропустим */ }
        }
      };
      await Promise.all([one(), one(), one()]);
    }
    async function show() {
      if (cur < 0) { box.innerHTML = grid(); fill(); return; }
      token++;
      box.innerHTML = view();
      const img = box.querySelector('.sn-vimg img'), day = list[cur].day;
      const th = thumbs.get(day);
      if (th) { img.src = th; img.classList.add('in', 'lo'); }
      try { const src = await getImg(day, false); if (list[cur] && list[cur].day === day && src) { img.onload = () => img.classList.remove('lo'); img.src = src; img.classList.add('in'); } }
      catch (e) { F.fail(e); }
    }
    show();
    (async () => {
      try { const r = await F.api('/api/skin/photos', {}); list = (r && r.photos) || []; F.S.snPhotos = list; }
      catch (e) { if (!list) { F.fail(e); el.close(); return; } }
      if (cur < 0) show();
    })();
    const go = d => { const n = cur + d; if (n < 0 || n >= list.length) return; cur = n; sure = 0; vib('select'); show(); };
    el.addEventListener('click', async ev => {
      const tile = ev.target.closest('.sn-tile[data-i]');
      if (tile) { cur = Number(tile.dataset.i); sure = 0; vib('tap'); show(); return; }
      if (ev.target.closest('[data-back]')) { cur = -1; sure = 0; vib('select'); show(); return; }
      if (ev.target.closest('[data-shoot]')) { el.close(); F.actions['sn-photo'](); return; }
      const st = ev.target.closest('[data-step]'); if (st) { go(Number(st.dataset.step)); return; }
      const del = ev.target.closest('[data-del]');
      if (!del) return;
      if (!sure) { sure = 1; vib('select'); del.classList.add('sure'); del.textContent = 'Точно удалить?'; setTimeout(() => { if (sure && del.isConnected) { sure = 0; del.classList.remove('sure'); del.textContent = 'Удалить'; } }, 3500); return; }
      const day = list[cur].day;
      await F.busy(del, async () => {
        try {
          const r = await send('/api/skin/photo/delete', { day });
          thumbs.delete(day); list = r.photos || list.filter(p => p.day !== day); F.S.snPhotos = list;
          cur = -1; sure = 0; vib('success'); F.toast('Фото удалено', 'ok'); show(); patchPhoto();
        } catch (e) { F.fail(e); }
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
