/* wx — «Ещё из WHOOP»: то, чего WHOOP не отдаёт по API (VO₂ max, Healthspan — возраст WHOOP и темп старения, стресс,
   давление, дневник WHOOP, любые другие цифры), — со скрина приложения WHOOP. Карточка во «Здоровье»
   (F.slots.healthAfterMonitor), экран ?p=wx (графики, дневник по дням, все записи с удалением), строки в «＋» и в «Ещё».
   Сервер — coach/f_wx.py: /api/wx {} → {latest, series, items, count, known}; /photo {image} → предпросмотр (НЕ сохраняет);
   /save {day, time, metrics} и /delete {id} → то же, что /api/wx. Кратко — F.S.data.ext.wx = {latest, count} из /api/all.
   Скрин: выбор картинки → сжатие ≤ 1800 px → «Читаю цифры…» → предпросмотр с переключателями → «Сохранить (N)». */
(window.FormaModules = window.FormaModules || []).push(F => {
  const { esc, svgi, cap } = F;
  const I = {
    cam: F.I.cam,
    band: svgi('<rect x="7" y="6.5" width="10" height="11" rx="3"/><path d="M9.2 6.5V3h5.6v3.5M9.2 17.5V21h5.6v-3.5M10 12h1.2l.8-1.6 1 3.2.8-1.6H14"/>'),
    x: svgi('<path d="M7.5 7.5l9 9M16.5 7.5l-9 9"/>'),
  };
  // известные показатели: подпись, единица, знаков (сервер шлёт свои в known — они главнее)
  const KNOWN = {
    vo2max: { label: 'VO₂ max', unit: 'мл/кг/мин', digits: 1 },
    whoop_age: { label: 'Возраст WHOOP', unit: 'лет', digits: 1 },
    pace: { label: 'Темп старения', unit: '×', digits: 1 },
    stress: { label: 'Стресс', unit: '', digits: 1 },
    bp_sys: { label: 'Давление верхнее', unit: 'мм рт. ст.', digits: 0 },
    bp_dia: { label: 'Давление нижнее', unit: 'мм рт. ст.', digits: 0 },
  };
  const ORDER = ['vo2max', 'whoop_age', 'pace', 'stress', 'bp_sys', 'bp_dia'];
  const ABOUT = {
    vo2max: 'Сколько кислорода тело берёт на пике нагрузки — выносливость. Растёт от регулярного кардио.',
    whoop_age: 'Возраст организма по сну, пульсу и нагрузке — часть Healthspan.',
    pace: 'Темп старения из Healthspan: меньше 1× — организм «стареет» медленнее календаря.',
    stress: 'Стресс по пульсу и гибкости пульса, шкала 0–3: днём выше — нормально, к ночи должен падать.',
    bp: 'Давление по данным WHOOP — оценка, не замер тонометром.',
  };
  const EMPTY = 'Пришли скрин из WHOOP — VO₂ max, Healthspan, стресс, давление, дневник: Форма прочитает цифры';
  const CARD_MAX = 6, LIST_MAX = 30, JR_DAYS = 7;

  // ---------- числа, единицы, даты ----------
  const known = d => ({ ...KNOWN, ...((d && d.known) || {}) });
  const nf = (v, dg) => {
    if (v == null || v === '') return '—';
    const x = Number(v);
    if (!Number.isFinite(x)) return String(v);
    return F.num(x, dg == null ? (Number.isInteger(x) ? 0 : 1) : dg).replace(/,0+$/, '');
  };
  const isTimes = u => u === '×' || u === 'x';
  const withUnit = (s, u) => !u ? s : isTimes(u) ? `${s}×` : `${s} ${u}`;
  const vHtml = (s, u) => !u ? esc(s) : isTimes(u) ? `${esc(s)}×` : `${esc(s)}<small>${esc(u)}</small>`;
  const sign = (x, dg) => { const r = Math.round(x * 10 ** dg) / 10 ** dg; return r > 0 ? `+${nf(r, dg)}` : r < 0 ? `−${nf(-r, dg)}` : '±0'; };
  const okDay = s => /^\d{4}-\d{2}-\d{2}$/.test(s || '');
  const todayIso = () => (F.S.data && F.S.data.today && F.S.data.today.day) || F.isoDay(new Date());
  const dayShort = s => { if (!okDay(s)) return ''; const d = F.parseDay(s); return `${d.getDate()} ${F.MON_S[d.getMonth()]}`; };
  const dayWord = s => { if (!okDay(s)) return ''; const w = F.dateWord(s); return /^(Сегодня|Вчера)$/.test(w) ? w.toLowerCase() : dayShort(s); };
  const dayFull = s => { if (!okDay(s)) return ''; const w = F.dateWord(s); return /^(Сегодня|Вчера)$/.test(w) ? `${w}, ${F.dateLine(s)}` : F.dateLine(s); };
  const whenFull = m => [okDay(m.day) ? (/^(Сегодня|Вчера)$/.test(F.dateWord(m.day)) ? F.dateWord(m.day).toLowerCase() : F.dateLine(m.day)) : '', m.time || ''].filter(Boolean).join(', ');
  const when = m => `${m.day || ''} ${m.time || ''}`;
  const plural = (n, a, b, c) => `${n} ${F.plural(n, a, b, c)}`;
  // подпись, единица, значение записи: число — по знакам показателя, иначе текст
  function view(m, d) {
    const k = known(d)[m.key] || {};
    const label = m.label || k.label || 'Показатель';
    const num = m.value != null && m.value !== '' && Number.isFinite(Number(m.value));
    let unit = num ? (m.unit != null && m.unit !== '' ? m.unit : k.unit || '') : '';
    if (num && /^(лет|год|года)$/.test(unit)) {  // 25,3 года · 21 год · 30 лет
      const x = Number(nf(m.value, k.digits).replace(',', '.'));
      unit = Number.isInteger(x) ? F.plural(x, 'год', 'года', 'лет') : 'года';
    }
    const val = num ? nf(m.value, k.digits) : (m.text || (m.value != null ? String(m.value) : '—'));
    return { label, unit, val, num };
  }

  // ---------- данные ----------
  const ext = () => F.S.data && F.S.data.ext && F.S.data.ext.wx;
  function norm(r) {
    const items = Array.isArray(r.items) ? r.items.filter(Boolean) : [];
    const latest = Array.isArray(r.latest) ? r.latest.filter(Boolean) : [];
    return { latest, items, series: r.series && typeof r.series === 'object' ? r.series : {},
      count: Number.isFinite(r.count) ? r.count : items.length, known: r.known && typeof r.known === 'object' ? r.known : {} };
  }
  const data = () => F.S.wx || (ext() ? norm(ext()) : null);
  const sig = r => `${r.count}:${(r.latest || []).map(x => x.id).sort().join(',')}`;
  function put(r) {
    if (!r || typeof r !== 'object') return;
    F.S.wx = norm(r);
    if (F.S.data) { F.S.data.ext = F.S.data.ext || {}; F.S.data.ext.wx = { latest: F.S.wx.latest, count: F.S.wx.count }; }
  }
  async function load(screen) {
    if (F.S.wxLoading) return;
    F.S.wxLoading = true;
    try { put(await F.api('/api/wx', {})); F.S.wxFail = F.S.wxErr = false; } catch (e) { F.S.wxFail = !data(); if (screen) { F.S.wxErr = true; F.fail(e); } }
    F.S.wxLoading = false;
    if (F.S.screen === 'wx' || (!F.S.screen && F.S.tab === 'trends')) F.render(false);
  }
  F.onData.push(d => {
    const e = d.ext && d.ext.wx;
    if (e && F.S.wx && sig(norm(e)) !== sig(F.S.wx)) F.S.wx = null;  // записали в другом окне — список перечитаем
    if (!e && !F.S.wxTried) { F.S.wxTried = true; load(false); }
  });
  const vib = kind => {
    F.haptic(kind);
    const tgh = F.tg && F.tg.HapticFeedback && F.tg.initData;
    if (!tgh && navigator.vibrate) { try { navigator.vibrate(kind === 'success' ? [10, 70, 18] : 8); } catch (e) { /* нет вибрации */ } }
  };
  // точки показателя по дням (по возрастанию): из series, иначе из записей (последняя за день)
  function points(d, key) {
    const s = d.series && d.series[key];
    if (Array.isArray(s) && s.length) return s.filter(p => okDay(p.day) && Number.isFinite(Number(p.v))).map(p => ({ day: p.day, v: Number(p.v) })).sort((a, b) => a.day.localeCompare(b.day));
    const by = {};
    [...d.items].reverse().forEach(m => { if (m.key === key && okDay(m.day) && Number.isFinite(Number(m.value))) by[m.day] = Number(m.value); });
    return Object.keys(by).sort().map(day => ({ day, v: by[day] }));
  }
  // ряд для F.line: по дню от первой точки до сегодня (≤ 400 дней), пустые дни — null
  function rowsOf(pts) {
    const end = [todayIso(), pts[pts.length - 1].day].sort()[1];
    const d0 = F.parseDay(pts[0].day), lim = F.parseDay(end);
    lim.setDate(lim.getDate() - 399);
    const cur = d0 < lim ? lim : d0, by = {}, rows = [];
    pts.forEach(p => { by[p.day] = p.v; });
    for (let i = 0; i < 400; i++) {
      const day = F.isoDay(cur);
      rows.push({ day, v: day in by ? by[day] : null });
      if (day >= end) break;
      cur.setDate(cur.getDate() + 1);
    }
    return rows.filter(r => r.v != null).length >= 2 ? rows : pts.map(p => ({ day: p.day, v: p.v }));
  }

  // ---------- строки карточки: свежие показатели, давление одной строкой, дневник отдельно ----------
  function cardLines(d) {
    const best = {};
    d.latest.forEach(m => {
      if (m.key === 'journal') return;
      const id = m.key === 'other' || !m.key ? `o:${String(m.label || '').toLowerCase()}` : m.key;
      if (!best[id] || when(m) > when(best[id])) best[id] = m;
    });
    const out = [], ord = k => { const i = ORDER.indexOf(k); return i < 0 ? 99 : i; };
    const s = best.bp_sys, di = best.bp_dia;
    if (s && di && s.day === di.day) {
      const vs = view(s, d), vd = view(di, d);
      out.push({ label: 'Давление', val: `${vs.val}/${vd.val}`, unit: vs.unit, day: s.day, w: when(s) > when(di) ? when(s) : when(di), o: ord('bp_sys') });
      delete best.bp_sys; delete best.bp_dia;
    }
    Object.values(best).forEach(m => { const v = view(m, d); out.push({ ...v, day: m.day, w: when(m), o: ord(m.key) }); });
    // свежие дни сверху; в один день — известные показатели раньше прочих цифр
    return out.sort((a, b) => String(b.day || '').localeCompare(String(a.day || '')) || a.o - b.o || b.w.localeCompare(a.w)).slice(0, CARD_MAX);
  }
  function journalLine(d) {
    const jl = d.latest.filter(m => m.key === 'journal' && okDay(m.day));
    if (!jl.length) return null;
    const day = jl.map(m => m.day).sort().pop();
    const src = d.items.length ? d.items : jl;
    return { day, n: src.filter(m => m.key === 'journal' && m.day === day).length || 1 };
  }
  const skel = n => `<article class="card">${F.skel(n)}</article>`;
  const shotBtn = (cls = '') => `<button class="btn primary wx-shot ${cls}" data-a="wx-photo" aria-label="Скрин из WHOOP: прочитать цифры">${I.cam}<span>Скрин из WHOOP</span></button>`;

  // ---------- карточка во «Здоровье» ----------
  F.slots.healthAfterMonitor.push(() => {
    const d = data(), title = 'Ещё из WHOOP';
    if (!d) return F.S.wxFail ? '' : `<div class="sec rise">${cap(title)}${skel(3)}</div>`;
    const lines = cardLines(d), jr = journalLine(d);
    if (!d.count && !lines.length && !jr) {
      return `<div class="sec rise">${cap(title)}<section class="card wx-card wx-empty"><p>${EMPTY}</p>${shotBtn()}</section></div>`;
    }
    const row = (label, val, meta, txt) => `<button type="button" class="wx-r" data-a="wx-open"><span class="wx-l"><b>${esc(label)}</b>${meta ? `<small>${esc(meta)}</small>` : ''}</span><span class="wx-v ${txt ? 'txt' : 'num'}">${val}</span></button>`;
    return `<div class="sec rise">${cap(title, '<button class="link" data-a="wx-open" aria-label="Ещё из WHOOP: графики и все записи">Все</button>')}
      <section class="card wx-card"><div class="wx-rows">${lines.map(x => row(x.label, vHtml(x.val, x.unit), dayWord(x.day), x.num === false)).join('')}
        ${jr ? row('Дневник', `<span class="wx-n">${plural(jr.n, 'ответ', 'ответа', 'ответов')}</span>`, dayWord(jr.day)) : ''}</div>
        ${shotBtn()}</section></div>`;
  });
  F.slots.plus.push(row => row('wx-photo', '', I.band, 'var(--ink)', 'Ещё из WHOOP', 'скрин: VO₂ max, Healthspan, стресс'));
  F.slots.moreProfile = F.slots.moreProfile || [];
  F.slots.moreProfile.push(row => {
    const d = data(), top = d ? cardLines(d).slice(0, 2) : [];
    return row({ a: 'wx-open', ic: I.band, title: 'Ещё из WHOOP',
      meta: top.length ? esc(top.map(x => `${x.label} ${withUnit(x.val, x.unit)}`).join(' · ')) : 'VO₂ max, Healthspan, стресс — со скрина',
      end: d && d.count ? `<b class="num">${d.count}</b>` : '' });
  });

  // ---------- экран «Ещё из WHOOP» ----------
  F.screens.wx = () => {
    const d = F.S.wx;
    const title = F.dHead('Ещё из WHOOP', 'VO₂ max, Healthspan, стресс, давление', 'var(--ink)');
    if (!d) {
      if (F.S.wxErr) return title + '<section class="card wx-empty"><p>Не загрузилось.</p><button class="btn primary" data-a="wx-retry">Повторить</button></section>';
      load(true);
      return title + skel(5);
    }
    if (!d.count && !d.items.length) {
      return title + `<section class="card wx-card wx-empty wx-hero rise"><span class="wx-ei">${I.band}</span>
        <p><b>Пока пусто</b>Открой в WHOOP экран с цифрами — Healthspan, VO₂ max, стресс, давление или дневник — сделай скрин и пришли сюда. Форма прочитает цифры, ты проверишь и сохранишь.</p>
        ${shotBtn()}</section>`;
    }
    const K = known(d), newest = d.items[0];
    let h = title + `<section class="card wx-card wx-hero rise">
      <p class="wx-lead"><b class="num">${d.count}</b> ${F.plural(d.count, 'запись', 'записи', 'записей')}${newest ? ` · последняя ${esc(whenFull(newest))}` : ''}</p>
      ${shotBtn()}<p class="wx-hint">Любой экран WHOOP с цифрами — перед сохранением всё можно проверить.</p></section>`;
    // графики известных показателей (давление — одной карточкой), одиночные точки — плитками
    const tiles = [];
    ORDER.filter(k => k !== 'bp_sys' && k !== 'bp_dia').forEach(k => {
      const pts = points(d, k);
      if (!pts.length) return;
      const k0 = K[k] || {}, last = pts[pts.length - 1];
      const m = d.items.find(x => x.key === k) || { key: k, value: last.v, day: last.day };
      const v = view({ ...m, value: last.v }, d);
      if (pts.length < 2) { tiles.push({ label: v.label, html: vHtml(v.val, v.unit), day: last.day, about: 'ещё один скрин — и будет график' }); return; }
      const dg = k0.digits != null ? k0.digits : 1, short = v.unit && v.unit.length <= 3;
      const fmt = x => short ? withUnit(nf(x, dg), v.unit) : nf(x, dg);
      h += `<div class="sec rise">${cap(esc(v.label))}<section class="card chart-card wx-ch">
        <div class="wx-big"><b class="num">${esc(v.val)}${isTimes(v.unit) ? '×' : ''}</b>${v.unit && !isTimes(v.unit) ? `<span>${esc(v.unit)}</span>` : ''}</div>
        <div class="wx-when">${esc(dayFull(last.day))} · <span class="num">${sign(last.v - pts[0].v, dg)}</span> с ${esc(dayShort(pts[0].day))}</div>
        <div class="chart-box">${F.line(rowsOf(pts), 'v', 'var(--ink)', null, { fmt, label: `${v.label}: как менялось` })}</div>
        ${ABOUT[k] ? `<p class="wx-about">${ABOUT[k]}</p>` : ''}</section></div>`;
    });
    h += bpBlock(d, tiles);
    // другие цифры — последнее значение на подпись
    const other = {};
    d.items.forEach(m => { if (m.key === 'journal' || ORDER.includes(m.key)) return; const id = String(m.label || '').toLowerCase(); if (!other[id]) other[id] = m; });
    Object.values(other).forEach(m => { const v = view(m, d); tiles.push({ label: v.label, html: vHtml(v.val, v.unit), day: m.day, about: '', txt: !v.num }); });
    if (tiles.length) {
      h += `<div class="sec rise">${cap('Последние значения')}<div class="wx-tiles">${tiles.map(x => `<div class="wx-tile"><span class="wx-tk">${esc(x.label)}</span>
        <b class="${x.txt ? 'txt' : 'num'}">${x.html}</b><span class="wx-td">${esc(dayFull(x.day))}${x.about ? ` · ${esc(x.about)}` : ''}</span></div>`).join('')}</div></div>`;
    }
    h += journalBlock(d);
    // все записи: ✕ → «Удалить?» → удалить
    const shown = F.S.wxAll ? d.items : d.items.slice(0, LIST_MAX);
    if (shown.length) {
      h += `<div class="sec rise">${cap('Все записи', `<span class="num">${d.count}</span>`)}<div class="list plain wx-all">${shown.map(m => {
        const v = view(m, d), what = m.key === 'journal' ? `Дневник: ${v.label}` : v.label;
        return `<div class="row wx-item"><span class="main"><div class="title">${esc(what)}</div><div class="meta"><span class="num">${esc(withUnit(v.val, v.unit))}</span> · ${esc(whenFull(m))}</div></span>
          <button type="button" class="wx-x" data-a="wx-del" data-v="${esc(m.id)}" aria-label="Удалить: ${esc(what)}">${I.x}</button></div>`;
      }).join('')}</div>${d.items.length > shown.length ? `<button class="btn wx-more" data-a="wx-all">Показать все ${d.items.length}</button>` : ''}</div>`;
    }
    h += `<div class="sec rise"><div class="list"><button class="row tap" data-a="ask-go" data-v="Посмотри мои цифры из WHOOP — VO₂ max, Healthspan, стресс, давление: что они значат и что улучшить?">
      <span class="ic">${F.I.coach}</span><span class="main"><div class="title">Спросить в чате</div><div class="meta">что значат эти цифры</div></span><span class="end chev"></span></button></div>
      <p class="foot">Цифры — из приложения WHOOP: Форма читает их со скрина, а ты проверяешь перед сохранением.</p></div>`;
    return h;
  };
  function bpBlock(d, tiles) {
    const s = points(d, 'bp_sys'), di = points(d, 'bp_dia');
    if (!s.length && !di.length) return '';
    const unit = (d.items.find(m => m.key === 'bp_sys' || m.key === 'bp_dia') || {}).unit || 'мм рт. ст.';
    const ls = s[s.length - 1], ld = di[di.length - 1];
    const big = ls && ld && ls.day === ld.day ? `${nf(ls.v, 0)}/${nf(ld.v, 0)}` : ls ? nf(ls.v, 0) : nf(ld.v, 0);
    const day = [ls && ls.day, ld && ld.day].filter(Boolean).sort().pop();
    if (s.length < 2 && di.length < 2) { tiles.push({ label: 'Давление', html: vHtml(big, unit), day, about: '' }); return ''; }
    const fmt = x => nf(x, 0);
    const one = (pts, t) => pts.length >= 2 ? `<h3 class="ch-t wx-sub">${t}</h3><div class="chart-box">${F.line(rowsOf(pts), 'v', 'var(--ink)', null, { fmt, label: `Давление ${t.toLowerCase()}` })}</div>` : '';
    return `<div class="sec rise">${cap('Давление')}<section class="card chart-card wx-ch">
      <div class="wx-big"><b class="num">${esc(big)}</b><span>${esc(unit)}</span></div><div class="wx-when">${esc(dayFull(day))}</div>
      ${one(s, 'Верхнее')}${one(di, 'Нижнее')}<p class="wx-about">${ABOUT.bp}</p></section></div>`;
  }
  function journalBlock(d) {
    const by = {};
    d.items.forEach(m => { if (m.key === 'journal' && okDay(m.day)) (by[m.day] = by[m.day] || []).push(m); });
    const days = Object.keys(by).sort().reverse();
    if (!days.length) return '';
    const shown = F.S.wxJrAll ? days : days.slice(0, JR_DAYS);
    return `<div class="sec rise">${cap('Дневник WHOOP', `<span class="num">${days.length}</span> ${F.plural(days.length, 'день', 'дня', 'дней')}`)}
      <section class="card wx-jr">${shown.map(day => `<div class="wx-jd"><h4>${esc(dayFull(day))}</h4>${by[day].map(m => {
        const v = view(m, d);
        return `<div class="wx-qa"><span>${esc(v.label)}</span><b>${esc(withUnit(v.val, v.unit))}</b></div>`;
      }).join('')}</div>`).join('')}
      ${days.length > shown.length ? `<button class="btn wx-more" data-a="wx-jr-all">Все дни · ${days.length}</button>` : ''}</section></div>`;
  }

  // ---------- действия ----------
  F.actions['wx-open'] = () => { if (F.S.screen === 'wx') return; vib('tap'); F.openScreen('wx'); };
  F.actions['wx-retry'] = () => { F.S.wxErr = false; F.render(false); };
  F.actions['wx-all'] = () => { F.S.wxAll = true; vib('select'); F.render(false); };
  F.actions['wx-jr-all'] = () => { F.S.wxJrAll = true; vib('select'); F.render(false); };
  F.actions['wx-del'] = async (el, id) => {
    if (!el.classList.contains('sure')) {  // первое касание — спросить
      vib('select');
      const label = el.getAttribute('aria-label');
      el.classList.add('sure'); el.innerHTML = '<span>Удалить?</span>'; el.setAttribute('aria-label', 'Точно удалить?');
      clearTimeout(el._t);
      el._t = setTimeout(() => { if (el.isConnected && el.classList.contains('sure')) { el.classList.remove('sure'); el.innerHTML = I.x; el.setAttribute('aria-label', label); } }, 3500);
      return;
    }
    clearTimeout(el._t);
    await F.busy(el, async () => {
      try { put(await F.api('/api/wx/delete', { id })); vib('success'); F.toast('Запись удалена', 'ok'); F.render(false); } catch (e) { F.fail(e); }
    });
  };
  F.actions['wx-photo'] = () => {
    vib('tap');
    let inp = document.getElementById('wx-pick');
    if (!inp) {
      inp = Object.assign(document.createElement('input'), { type: 'file', accept: 'image/*', id: 'wx-pick', hidden: true });
      inp.setAttribute('aria-hidden', 'true');
      document.body.append(inp);
    }
    inp.value = '';
    inp.onchange = () => { const f = inp.files && inp.files[0]; inp.value = ''; if (f) photoSheet(f); };
    inp.click();
  };

  // сжатие, как в weight.js: ≤ side px, JPEG; не влезло — тише качество, потом меньше сторона (мелкие цифры WHOOP — 1800 px)
  async function jpeg(file, side, max) {
    const url = URL.createObjectURL(file);
    try {
      const img = await new Promise((ok, bad) => { const i = new Image(); i.onload = () => ok(i); i.onerror = bad; i.src = url; });
      let k = Math.min(1, side / Math.max(img.naturalWidth, img.naturalHeight)), q = .86, out = '';
      const cv = document.createElement('canvas');
      for (let i = 0; i < 6; i++) {
        cv.width = Math.max(1, Math.round(img.naturalWidth * k)); cv.height = Math.max(1, Math.round(img.naturalHeight * k));
        const cx = cv.getContext('2d'); cx.fillStyle = '#fff'; cx.fillRect(0, 0, cv.width, cv.height); cx.drawImage(img, 0, 0, cv.width, cv.height);
        out = cv.toDataURL('image/jpeg', q);
        if (out.length <= max) return out;
        if (q > .66) q -= .1; else k *= .8;
      }
      if (out.length <= max) return out;
      throw new Error('big');
    } finally { URL.revokeObjectURL(url); }
  }

  // скрин → лист «Читаю цифры…» → предпросмотр с переключателями → «Сохранить (N)»
  async function photoSheet(file) {
    const el = F.sheet(`<div class="wx-form" data-wx-form><h2>Скрин из WHOOP</h2><div class="wx-read"><span class="wx-ei pulse">${I.cam}</span>
      <p><b>Читаю цифры…</b>Обычно 10–20 секунд.</p></div>${F.skel(3)}</div>`);
    const box = el.querySelector('[data-wx-form]');
    let image;
    try { image = await jpeg(file, 1800, 900000); } catch (e) { vib('error'); el.close(); F.toast('Не получилось прочитать картинку — попробуй другой скрин', 'err'); return; }
    try {
      const r = await F.api('/api/wx/photo', { image });
      if (r && r.error) throw new Error(r.error);
      if (!el.isConnected) return;
      vib('success');
      preview(el, box, r || {});
    } catch (e) {
      if (!el.isConnected) return;
      vib('error');
      failBox(el, box, e.message || String(e));
    }
  }
  function failBox(el, box, text, skipped) {
    box.innerHTML = `<h2>Скрин из WHOOP</h2><div class="wx-read"><span class="wx-ei">${I.cam}</span><p><b>Не получилось</b>${esc(text)}</p></div>
      ${skipped && skipped.length ? `<p class="wx-skip">Не взял: ${esc(skipped.join('; '))}</p>` : ''}
      <div class="wx-btns"><button class="btn primary" type="button" data-again>Другой скрин</button><button class="btn" type="button" data-close>Закрыть</button></div>`;
    box.querySelector('[data-again]').onclick = () => { el.close(); F.actions['wx-photo'](); };
    box.querySelector('[data-close]').onclick = () => el.close();
  }
  function preview(el, box, r) {
    const ms = (Array.isArray(r.metrics) ? r.metrics : []).filter(Boolean), skipped = Array.isArray(r.skipped) ? r.skipped.filter(Boolean) : [];
    if (!ms.length) { failBox(el, box, 'Не нашёл на скрине цифр. Пришли экран WHOOP, где видны VO₂ max, Healthspan, стресс, давление или дневник.', skipped); return; }
    const d = data(), on = ms.map(() => true), today = todayIso();
    const day = okDay(r.day) && r.day <= today ? r.day : today;
    box.innerHTML = `<h2>Проверь цифры</h2><p class="small muted">Нашёл на скрине — сними лишнее и сохрани.</p>
      <label class="field wx-day"><span>День</span><input name="day" type="date" max="${today}" value="${esc(day)}"></label>
      <div class="wx-pick" role="group" aria-label="Что сохранить">${ms.map((m, i) => {
        const v = view(m, d), jr = m.key === 'journal';
        return `<button type="button" class="wx-m on" data-i="${i}" role="switch" aria-checked="true"><span class="wx-ml"><b>${esc(v.label)}</b>
          <small>${jr ? 'Дневник · ' : ''}<span class="num">${esc(withUnit(v.val, v.unit))}</span></small></span><span class="switch on" aria-hidden="true"></span></button>`;
      }).join('')}</div>
      ${skipped.length ? `<p class="wx-skip">Не взял: ${esc(skipped.join('; '))}</p>` : ''}
      <button class="btn primary wx-save" type="button" data-save></button>`;
    el.scrollTop = 0;
    const save = box.querySelector('[data-save]');
    const count = () => {
      const n = on.filter(Boolean).length;
      save.disabled = !n; save.textContent = n ? `Сохранить (${n})` : 'Выбери, что сохранить';
    };
    count();
    box.querySelector('.wx-pick').addEventListener('click', ev => {
      const b = ev.target.closest('.wx-m'); if (!b) return;
      const i = Number(b.dataset.i); on[i] = !on[i];
      b.classList.toggle('on', on[i]); b.setAttribute('aria-checked', String(on[i]));
      b.querySelector('.switch').classList.toggle('on', on[i]);
      vib('select'); count();
    });
    const submit = () => {
      if (save.disabled) return;
      const metrics = ms.filter((m, i) => on[i]), n = metrics.length;
      const dayV = box.querySelector('[name=day]').value || day;
      F.busy(save, async () => {
        try {
          put(await F.api('/api/wx/save', { day: dayV, time: r.time || '', metrics }));
          vib('success'); F.toast(`Сохранил ${plural(n, 'показатель', 'показателя', 'показателей')}`, 'ok'); el.close();
          F.render(false);  // тихо: карточка или экран меняют только отличия
        } catch (e) { F.fail(e); count(); }
      });
    };
    save.onclick = submit;
    // Enter на компьютере — сохранить (на переключателе Enter переключает, как пробел)
    box.addEventListener('keydown', ev => { if (ev.key === 'Enter' && !ev.target.closest('button')) { ev.preventDefault(); submit(); } });
  }
});
