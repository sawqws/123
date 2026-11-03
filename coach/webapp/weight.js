/* weight — «Вес и состав тела»: замеры с умных весов (вручную, скрином или текстом), состав тела плитками, график
   30/90 дней/всё (вес, жир, мышцы), список замеров с правкой и удалением. Карточка «Вес» на главной (F.slots.homeAfterMyDay),
   экран ?p=weight, строки в «＋» и в «Ещё» (F.slots.moreProfile). Сервер — coach/f_weight.py.
   Данные — F.S.wt (полный ответ /api/weight) или F.S.data.ext.weight (кратко из /api/all).
   Скрин весов: выбор фото → сжатие ≤ 1600 px → /api/weight/photo → тот же лист замера, заполненный распознанным, → «Сохранить». */
(window.FormaModules = window.FormaModules || []).push(F => {
  const { esc, svgi, cap } = F;
  const I = {
    scale: svgi('<rect x="3.5" y="3.5" width="17" height="17" rx="4.5"/><path d="M8.2 9.2a5.2 5.2 0 0 1 7.6 0"/><path d="M12 9.6l1.3-1.8"/>'),
    shot: svgi('<rect x="6" y="2.8" width="12" height="18.4" rx="2.6"/><path d="M9 7.5h6M9 11h6M9 14.5h3.5"/>'),
    pen: svgi('<path d="M4 20l1-4.5L15.5 5a2.1 2.1 0 0 1 3 3L8 18.5z"/><path d="M13.5 7l3 3"/>'),
    text: svgi('<path d="M5 6.5h14M5 12h14M5 17.5h8"/>'),
  };
  // поля замера: [ключ, подпись, единица, знаков, подсказка в поле]
  const MAIN = [['fat_pct', 'Жир', '%', 1, '14,2'], ['muscle_kg', 'Мышцы', 'кг', 1, '50,1'], ['lean_kg', 'Сухая масса', 'кг', 1, '54,4']];
  const MORE = [['water_pct', 'Вода', '%', 1, '58'], ['bone_kg', 'Кости', 'кг', 1, '2,8'], ['visceral', 'Висцеральный жир', '', 1, '4'],
    ['bmr', 'Обмен веществ', 'ккал', 0, '1650'], ['bmi', 'ИМТ', '', 1, '20,1'], ['protein_pct', 'Белок', '%', 1, '18']];
  const ALL = [...MAIN, ...MORE];
  const ABOUT = { fat_pct: 'доля жира в теле', muscle_kg: 'мышцы вместе с водой в них', lean_kg: 'всё, кроме жира', water_pct: 'вода в теле',
    bone_kg: 'минералы костей', visceral: 'жир вокруг органов, уровень по весам', bmr: 'тратишь в покое за сутки', bmi: 'вес к росту',
    protein_pct: 'доля белка' };
  const METRICS = [['kg', 'Вес', 'кг', 1], ['fat_pct', 'Жир', '%', 1], ['muscle_kg', 'Мышцы', 'кг', 1]];
  const RANGES = [[30, '30 дней'], [90, '90 дней'], [0, 'Всё']];
  const HINT = 'Взвешивайся утром натощак, после туалета — так цифры сравнимы.';
  const LIST_MAX = 20;

  // ---------- числа и даты ----------
  const nf = (v, d = 1) => v == null ? '—' : F.num(v, d).replace(/,0+$/, '');
  const digits = k => (ALL.find(x => x[0] === k) || [0, 0, 0, 1])[3];
  const sign = (x, d = 1) => { const r = Math.round(x * 10 ** d) / 10 ** d; return r > 0 ? `+${nf(r, d)}` : r < 0 ? `−${nf(-r, d)}` : '±0'; };
  const dayShort = s => { const d = F.parseDay(s); return `${d.getDate()} ${F.MON_S[d.getMonth()]}`; };
  const whenWord = m => { const w = F.dateWord(m.day); return `${/^(Сегодня|Вчера)$/.test(w) ? w.toLowerCase() : F.dateLine(m.day)}${m.time ? `, ${m.time}` : ''}`; };
  const SRC = { photo: 'скрин', text: 'текстом', manual: '' };
  const todayIso = () => (F.S.data && F.S.data.today && F.S.data.today.day) || F.isoDay(new Date());
  const seenHint = () => { try { return localStorage.getItem('forma-wt-hint') === '1'; } catch (e) { return false; } };
  const markHint = () => { try { localStorage.setItem('forma-wt-hint', '1'); } catch (e) { /* без хранилища — покажем ещё раз */ } };

  // ---------- данные ----------
  const ext = () => F.S.data && F.S.data.ext && F.S.data.ext.weight;
  const data = () => F.S.wt || ext() || null;
  function put(r) {
    if (!r || !Array.isArray(r.items)) return;
    F.S.wt = r;
    if (F.S.data) {
      F.S.data.ext = F.S.data.ext || {};
      const since = F.isoDay(new Date(F.parseDay(todayIso()).getTime() - 29 * 864e5));
      F.S.data.ext.weight = { count: r.count, last: r.last, prev: r.prev, change: r.change, spark: daily(r.items).filter(m => m.day >= since).map(m => ({ day: m.day, kg: m.kg })) };
    }
  }
  async function load(screen) {
    if (F.S.wtLoading) return;
    F.S.wtLoading = true;
    try { put(await F.api('/api/weight', {})); F.S.wtFail = F.S.wtErr = false; } catch (e) { F.S.wtFail = !data(); if (screen) { F.S.wtErr = true; F.fail(e); } }
    F.S.wtLoading = false;
    if (F.S.screen === 'weight' || (!F.S.screen && F.S.tab === 'today')) F.render(false);
  }
  F.onData.push(d => {
    const e = d.ext && d.ext.weight;
    if (e && F.S.wt && e.last && (!F.S.wt.last || e.last.id !== F.S.wt.last.id)) F.S.wt = null;  // замер добавили в другом окне
    if (!e && !F.S.wtTried) { F.S.wtTried = true; load(false); }
  });
  const vib = kind => {
    F.haptic(kind);
    const tgh = F.tg && F.tg.HapticFeedback && F.tg.initData;
    if (!tgh && navigator.vibrate) { try { navigator.vibrate(kind === 'success' ? [10, 70, 18] : 8); } catch (e) { /* нет вибрации */ } }
  };
  // по замеру на день — самый ранний (утренний), по возрастанию; items с сервера — новые сверху
  function daily(items) {
    const by = {};
    [...items].reverse().forEach(m => { if (!by[m.day]) by[m.day] = m; });
    return Object.keys(by).sort().map(k => by[k]);
  }

  // ---------- мини-график для карточки ----------
  function spark(pts) {
    if (!pts || pts.length < 2) return '';
    const W = 120, H = 44, vs = pts.map(p => p.kg), lo = Math.min(...vs), hi = Math.max(...vs), sp = hi - lo || 1;
    const t0 = F.parseDay(pts[0].day).getTime(), t1 = F.parseDay(pts[pts.length - 1].day).getTime(), span = t1 - t0 || 1;
    const P = pts.map(p => [4 + (F.parseDay(p.day).getTime() - t0) / span * (W - 8), 6 + (hi - p.kg) / sp * (H - 12)]);
    const d = P.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join('');
    const L = P[P.length - 1];
    return `<svg class="wt-spark" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" aria-hidden="true"><path d="${d}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
      <circle cx="${L[0].toFixed(1)}" cy="${L[1].toFixed(1)}" r="3.4" fill="currentColor"/></svg>`;
  }
  const skel = n => `<article class="card">${F.skel(n)}</article>`;

  // ---------- карточка на главной ----------
  F.slots.homeAfterMyDay.push((t0, isToday) => {
    if (!isToday) return '';
    const d = data();
    if (!d) return F.S.wtFail ? '' : `<div class="sec rise">${cap('Вес')}${skel(2)}</div>`;
    if (!d.count) {
      return `<div class="sec rise">${cap('Вес')}<section class="card wt-card wt-empty">
        <span class="wt-ei">${I.scale}</span><p><b>Вес и состав тела</b>Встань на умные весы и пришли скрин или цифры — нормы еды и воды будут по свежему весу.</p>
        <div class="wt-btns"><button class="btn primary" data-a="wt-photo">Скрин с весов</button><button class="btn" data-a="wt-add">Ввести</button></div></section></div>`;
    }
    const m = d.last, ch = (d.change || {}).prev, bits = [];
    if (ch && ch.kg != null) bits.push(`${sign(ch.kg)} кг к прошлому`);
    if (m.fat_pct != null) bits.push(`жир ${nf(m.fat_pct)} %`);
    if (m.muscle_kg != null) bits.push(`мышцы ${nf(m.muscle_kg)} кг`);
    return `<div class="sec rise">${cap('Вес', '<button class="link" data-a="wt-open" aria-label="Вес: все замеры и график">Все</button>')}
      <section class="card wt-card wt-home"><button type="button" class="wt-main" data-a="wt-open" aria-label="Вес ${nf(m.kg)} кг, ${esc(whenWord(m))}">
        <span class="wt-now"><b class="num">${nf(m.kg)}<small>кг</small></b><span class="wt-when">${esc(whenWord(m))}</span></span>${spark(d.spark)}</button>
        ${bits.length ? `<div class="wt-chips">${bits.map(b => `<span class="num">${esc(b)}</span>`).join('')}</div>` : ''}
        <div class="wt-btns"><button class="btn" data-a="wt-photo">${I.shot}<span>Скрин</span></button><button class="btn" data-a="wt-add">${F.I.plus}<span>Замер</span></button></div></section></div>`;
  });
  F.slots.plus.push(row => row('wt-open', '', I.scale, 'var(--ink)', 'Вес', 'замер с весов: скрин или цифры'));
  F.slots.moreProfile = F.slots.moreProfile || [];
  F.slots.moreProfile.push(row => {
    const d = data(), m = d && d.last;
    return row({ a: 'wt-open', ic: I.scale, title: 'Вес и состав тела', meta: m ? `${nf(m.kg)} кг · ${esc(whenWord(m))}` : 'замеры с умных весов',
      end: d && d.count ? `<b class="num">${d.count}</b>` : '' });
  });

  // ---------- экран «Вес» ----------
  F.screens.weight = () => {
    const d = F.S.wt;
    const title = F.dHead('Вес', 'и состав тела', 'var(--ink)');
    if (!d) {
      if (F.S.wtErr) return title + '<section class="card wt-empty"><p>Не загрузилось.</p><button class="btn primary" data-a="wt-retry">Повторить</button></section>';
      load(true);
      return title + skel(5);
    }
    if (!d.count) {
      return title + `<section class="card wt-card wt-empty wt-hero rise"><span class="wt-ei">${I.scale}</span>
        <p><b>Пока нет замеров</b>Пришли скрин из приложения весов — я сам найду вес, жир, мышцы и остальное. Или введи цифры вручную.</p>
        <div class="wt-btns"><button class="btn primary" data-a="wt-photo">${I.shot}<span>Скрин с весов</span></button><button class="btn" data-a="wt-add">${F.I.plus}<span>Ввести</span></button></div>
        <p class="wt-hint">${HINT}</p></section>
        <p class="foot">Нормы еды, воды и белка Форма считает по последнему замеру.</p>`;
    }
    const m = d.last, ch = d.change || {};
    const line = (x, word) => x && x.kg != null ? `<span><b class="num">${sign(x.kg)} кг</b> ${word}</span>` : '';
    const d30 = ch.d30 && ch.d30.days >= 7 ? line(ch.d30, `за ${ch.d30.days} ${F.plural(ch.d30.days, 'день', 'дня', 'дней')}`) : '';
    let h = title + `<section class="card wt-card wt-hero rise">
      <div class="wt-big"><b class="num">${nf(m.kg)}</b><span>кг</span></div>
      <div class="wt-when">замер ${esc(whenWord(m))}${SRC[m.src] ? ` · ${SRC[m.src]}` : ''}</div>
      ${line(ch.prev, ch.prev ? `к прошлому, ${esc(dayShort(d.prev.day))}` : '') || d30 ? `<div class="wt-ch">${line(ch.prev, ch.prev ? `к прошлому, ${esc(dayShort(d.prev.day))}` : '')}${d30}</div>` : ''}
      <div class="wt-btns"><button class="btn primary" data-a="wt-add" aria-label="Добавить замер">${F.I.plus}<span>Замер</span></button><button class="btn" data-a="wt-photo" aria-label="Скрин с весов">${I.shot}<span>Скрин</span></button></div>
      ${d.count < 3 ? `<p class="wt-hint">${HINT}</p>` : ''}</section>`;
    const tiles = ALL.filter(([k]) => m[k] != null);
    if (tiles.length) {
      h += `<div class="sec rise">${cap('Состав тела', esc(dayShort(m.day)))}<div class="wt-tiles">${tiles.map(([k, label, unit, dg]) => {
        const p = d.prev && d.prev[k] != null ? m[k] - d.prev[k] : null;
        return `<div class="wt-tile"><span class="wt-tk">${label}</span><b class="num">${nf(m[k], dg)}${unit && unit !== 'уровень' ? `<small>${unit}</small>` : ''}</b>
          <span class="wt-td">${p != null && Math.abs(p) >= (dg ? .05 : .5) ? `<span class="num">${sign(p, dg)}</span> к прошлому` : esc(ABOUT[k])}</span></div>`;
      }).join('')}</div></div>`;
    }
    h += `<div class="sec rise">${cap('Как менялось')}<section class="card chart-card" data-wt-chart>${chartBlock(d)}</section></div>`;
    const shown = F.S.wtAll ? d.items : d.items.slice(0, LIST_MAX);
    h += `<div class="sec rise">${cap('Замеры', `<span class="num">${d.count}</span>`)}<div class="list wt-list">${shown.map(x => {
      const meta = [whenWord(x), x.fat_pct != null ? `жир ${nf(x.fat_pct)} %` : '', x.muscle_kg != null ? `мышцы ${nf(x.muscle_kg)} кг` : '', SRC[x.src], x.note].filter(Boolean).join(' · ');
      return `<button class="row tap" data-a="wt-edit" data-v="${esc(x.id)}"><span class="ic">${x.src === 'photo' ? I.shot : x.src === 'text' ? I.text : I.scale}</span>
        <span class="main"><div class="title num">${nf(x.kg)} кг</div><div class="meta">${esc(meta)}</div></span><span class="end chev"></span></button>`;
    }).join('')}</div>${d.items.length > shown.length ? `<button class="btn wt-more" data-a="wt-all">Показать все ${d.count}</button>` : ''}</div>`;
    h += `<div class="sec rise"><div class="list"><button class="row tap" data-a="ask-go" data-v="Посмотри мои замеры веса и состава тела: как они меняются и что это значит для тренировок и еды?">
      <span class="ic">${F.I.coach}</span><span class="main"><div class="title">Спросить тренера</div><div class="meta">видит замеры и тренд</div></span><span class="end chev"></span></button></div>
      <p class="foot">Вес у подростка растёт вместе с ростом и мышцами — смотри на тренд за недели, а не на один день. Нормы еды, воды и белка Форма считает по последнему замеру.</p></div>`;  // подвал — в одном блоке с кнопкой: на широком не остаётся колонкой один
    return h;
  };

  // ---------- график: вес / жир / мышцы × 30 / 90 дней / всё ----------
  function chartBlock(d) {
    const items = d.items || [], has = k => items.some(x => x[k] != null);
    const mets = METRICS.filter(([k]) => k === 'kg' || has(k));
    let key = F.S.wtKey || 'kg';
    if (!mets.some(x => x[0] === key)) key = 'kg';
    const range = F.S.wtRange != null ? F.S.wtRange : 30;
    const [, label, unit, dg] = mets.find(x => x[0] === key);
    const days = daily(items), end = todayIso();
    const first = days.length ? days[0].day : end;
    const n = range || Math.max(2, Math.round((F.parseDay(end) - F.parseDay(first)) / 864e5) + 1);
    const by = {}; days.forEach(x => { by[x.day] = x; });
    const rows = [];
    for (let i = n - 1; i >= 0; i--) { const dd = F.isoDay(new Date(F.parseDay(end).getTime() - i * 864e5)); rows.push({ day: dd, v: by[dd] ? by[dd][key] : null }); }
    const pts = rows.filter(r => r.v != null), fmt = v => `${nf(v, dg)} ${unit}`;
    let lead = '<p class="ch-lead"><b>—</b> нет замеров за этот период</p>';
    if (pts.length) {
      const a = pts[0].v, b = pts[pts.length - 1].v;
      lead = `<p class="ch-lead"><span class="ch-v"><b class="num">${fmt(b)}</b> последний</span>${pts.length > 1 ? `<span class="ch-cmp flat">${sign(b - a, dg)} ${unit} с ${esc(dayShort(pts[0].day))}${key === 'kg' && d.trend ? trendWord(d, range) : ''}</span>` : ''}</p>`;
    }
    const seg = (items2, cur, act) => `<div class="seg wt-seg" role="tablist">${items2.map(([v, l]) => `<button type="button" class="${String(cur) === String(v) ? 'on' : ''}" data-a="${act}" data-v="${v}" role="tab" aria-selected="${String(cur) === String(v)}">${l}</button>`).join('')}</div>`;
    const chart = pts.length >= 2 ? F.line(rows, 'v', 'var(--ink)', null, { fmt, label: `${label} за ${range ? `${range} дней` : 'всё время'}` })
      : `<div class="chart-empty">${pts.length ? 'Нужен ещё один замер — тогда будет линия' : 'Нет замеров за этот период'}</div>`;
    return `${mets.length > 1 ? seg(mets.map(x => [x[0], x[1]]), key, 'wt-key') : ''}${seg(RANGES, range, 'wt-range')}
      <div class="ch-block"><h3 class="ch-t">${label}</h3>${lead}<div class="chart-box">${chart}</div></div>`;
  }
  function trendWord(d, range) {
    const t = range === 90 ? d.trend.d90 : range === 30 ? d.trend.d30 : null;
    return t && t.word && t.span >= 7 ? ` · ${t.word}` : '';
  }
  const redrawChart = () => { const el = document.querySelector('[data-wt-chart]'); if (el && F.S.wt) F.morph(el, chartBlock(F.S.wt)); };
  F.actions['wt-key'] = (el, v) => { if (F.S.wtKey === v) return; F.S.wtKey = v; vib('select'); redrawChart(); };
  F.actions['wt-range'] = (el, v) => { const n = Number(v); if (F.S.wtRange === n) return; F.S.wtRange = n; vib('select'); redrawChart(); };

  // ---------- действия ----------
  F.actions['wt-open'] = () => { if (F.S.screen === 'weight') return; vib('tap'); F.openScreen('weight'); };
  F.actions['wt-retry'] = () => { F.S.wtErr = false; F.render(false); };
  F.actions['wt-all'] = () => { F.S.wtAll = true; vib('select'); F.render(false); };
  F.actions['wt-add'] = () => formSheet(null);
  F.actions['wt-edit'] = (el, id) => { const m = F.S.wt && F.S.wt.items.find(x => x.id === id); if (m) formSheet(m); };
  F.actions['wt-photo'] = () => {
    vib('tap');
    let inp = document.getElementById('wt-pick');
    if (!inp) {
      inp = Object.assign(document.createElement('input'), { type: 'file', accept: 'image/*', id: 'wt-pick', hidden: true });
      inp.setAttribute('aria-hidden', 'true');
      document.body.append(inp);
    }
    inp.value = '';
    inp.onchange = () => { const f = inp.files && inp.files[0]; inp.value = ''; if (f) photoSheet(f); };
    inp.click();
  };

  // сжатие, как в skin.js: ≤ side px, JPEG; не влезло в лимит — тише качество, потом меньше сторона (мелкие цифры скрина — 1600 px)
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

  // скрин → лист «Читаю цифры…» → тот же лист с полями (распознанное), «Сохранить»
  async function photoSheet(file) {
    const el = F.sheet(`<div class="wt-form" data-wt-form><h2>Скрин с весов</h2><div class="wt-read"><span class="wt-ei pulse">${I.shot}</span>
      <p><b>Читаю цифры…</b>Обычно 10–20 секунд.</p></div>${F.skel(3)}</div>`);
    const box = el.querySelector('[data-wt-form]');
    let image;
    try { image = await jpeg(file, 1600, 900000); } catch (e) { vib('error'); el.close(); F.toast('Не получилось прочитать картинку — попробуй другой скрин', 'err'); return; }
    try {
      const r = await F.api('/api/weight/photo', { image });
      if (!el.isConnected) return;
      vib('success');
      fill(el, box, { ...r.item }, { preview: true, skipped: r.skipped || [] });
    } catch (e) {
      if (!el.isConnected) return;
      vib('error');
      box.innerHTML = `<h2>Скрин с весов</h2><div class="wt-read"><span class="wt-ei">${I.shot}</span><p><b>Не получилось</b>${esc(e.message || String(e))}</p></div>
        <div class="wt-btns"><button class="btn primary" type="button" data-again>Другой скрин</button><button class="btn" type="button" data-manual>Ввести вручную</button></div>`;
      box.querySelector('[data-again]').onclick = () => { el.close(); F.actions['wt-photo'](); };
      box.querySelector('[data-manual]').onclick = () => { el.close(); setTimeout(() => formSheet(null), 120); };
    }
  }

  // ---------- лист замера: новый, правка, предпросмотр скрина ----------
  function formSheet(m) {
    vib('tap');
    const el = F.sheet('<div class="wt-form" data-wt-form></div>');
    fill(el, el.querySelector('[data-wt-form]'), m ? { ...m } : null, { edit: !!m });
  }
  function fill(el, box, m, o = {}) {
    const v = m || { day: todayIso(), time: '' };
    const val = k => v[k] == null ? '' : String(v[k]).replace('.', ',');
    const inp = ([k, label, unit, , ph]) => `<label class="field wt-f"><span>${label}${unit ? `, ${unit}` : ''}</span>
      <input name="${k}" type="text" inputmode="decimal" autocomplete="off" enterkeyhint="done" placeholder="${ph}" value="${esc(val(k))}"></label>`;
    const moreOpen = MORE.some(([k]) => v[k] != null);
    const firstHint = !o.edit && !seenHint();
    box.innerHTML = `<h2>${o.preview ? 'Проверь цифры' : o.edit ? 'Замер' : 'Новый замер'}</h2>
      ${o.preview ? `<p class="small muted">Распознал со скрина — поправь, если что-то не так.${o.skipped.length ? ` Пропустил: ${esc(o.skipped.join('; '))}.` : ''}</p>` : ''}
      ${!o.edit && !o.preview ? `<button type="button" class="wt-paste" data-paste>${I.text}<span>Вставить текстом из приложения весов</span></button>
        <div class="wt-pastebox" hidden><textarea rows="3" maxlength="2000" placeholder="Вес 63,4 кг&#10;Жир 14,2 %&#10;Мышечная масса 50,1 кг" aria-label="Текст с весов"></textarea>
        <button type="button" class="btn" data-parse>Разобрать</button></div>` : ''}
      <form data-wt-f novalidate>
        <label class="field wt-kg"><span>Вес, кг</span><input name="kg" type="text" inputmode="decimal" autocomplete="off" enterkeyhint="done" placeholder="63,4" value="${esc(val('kg'))}" required></label>
        <div class="wt-grid">${MAIN.map(inp).join('')}</div>
        <details class="wt-moref"${moreOpen ? ' open' : ''}><summary>Ещё показатели</summary><div class="wt-grid">${MORE.map(inp).join('')}</div></details>
        <div class="wt-grid wt-dt"><label class="field"><span>День</span><input name="day" type="date" max="${todayIso()}" value="${esc(v.day || todayIso())}"></label>
          <label class="field"><span>Время</span><input name="time" type="time" value="${esc(v.time || '')}"></label></div>
        <label class="field"><span>Заметка</span><input name="note" type="text" maxlength="120" placeholder="по желанию" value="${esc(v.note || '')}"></label>
        ${firstHint ? `<p class="wt-hint">${HINT}</p>` : ''}
        <button class="btn primary" type="submit">Сохранить</button>
        ${o.edit ? '<button class="btn wt-del" type="button" data-del>Удалить замер</button>' : ''}</form>`;
    if (firstHint) markHint();
    el.scrollTop = 0;
    const form = box.querySelector('[data-wt-f]');
    if (!o.preview && matchMedia('(pointer: fine)').matches) setTimeout(() => { const k = form.querySelector('[name=kg]'); if (k && !k.value) k.focus(); }, 350);
    const paste = box.querySelector('[data-paste]');
    if (paste) {
      paste.onclick = () => { const pb = box.querySelector('.wt-pastebox'); pb.hidden = !pb.hidden; paste.classList.toggle('on', !pb.hidden); vib('select'); if (!pb.hidden) pb.querySelector('textarea').focus(); };
      box.querySelector('[data-parse]').onclick = ev => {
        const text = box.querySelector('.wt-pastebox textarea').value.trim();
        if (!text) { F.toast('Вставь текст с цифрами', 'err'); return; }
        F.busy(ev.currentTarget, async () => {
          try {
            const r = await F.api('/api/weight/text', { text });
            Object.entries(r.item).forEach(([k, x]) => { const f = form.elements[k]; if (f && x != null) f.value = String(x).replace('.', ','); });
            if (MORE.some(([k]) => r.item[k] != null)) form.querySelector('.wt-moref').open = true;
            form.dataset.src = 'text';
            box.querySelector('.wt-pastebox').hidden = true; paste.classList.remove('on');
            vib('success'); F.toast(`Нашёл: ${r.found.length} ${F.plural(r.found.length, 'показатель', 'показателя', 'показателей')}`, 'ok');
          } catch (e) { F.fail(e); }
        });
      };
    }
    if (o.preview) form.dataset.src = 'photo';
    form.addEventListener('submit', async ev => {
      ev.preventDefault();
      const body = {};
      ['kg', ...ALL.map(x => x[0]), 'day', 'time', 'note'].forEach(k => { const f = form.elements[k]; body[k] = f ? f.value.trim() : ''; });
      if (!body.kg) { vib('error'); F.toast('Напиши вес, например 63,4', 'err'); form.elements.kg.focus(); return; }
      if (o.edit) body.id = m.id; else body.src = form.dataset.src || 'manual';
      await F.busy(ev.submitter || form.querySelector('[type=submit]'), async () => {
        try {
          const r = await F.api('/api/weight/save', body);
          put(r); vib('success'); F.toast(o.edit ? 'Замер сохранён' : `Записал: ${nf(r.saved.kg)} кг`, 'ok'); el.close();
          if (!o.edit && F.S.screen !== 'weight') F.openScreen('weight'); else F.render(false);
        } catch (e) { F.fail(e); }
      });
    });
    form.addEventListener('keydown', ev => { if (ev.key === 'Enter' && ev.target.tagName === 'INPUT') { ev.preventDefault(); form.requestSubmit(); } });
    const del = box.querySelector('[data-del]');
    if (del) {
      let sure = 0;
      del.onclick = async () => {
        if (!sure) { sure = 1; vib('select'); del.classList.add('sure'); del.textContent = 'Точно удалить?'; setTimeout(() => { if (sure && del.isConnected) { sure = 0; del.classList.remove('sure'); del.textContent = 'Удалить замер'; } }, 3500); return; }
        await F.busy(del, async () => {
          try { put(await F.api('/api/weight/delete', { id: m.id })); vib('success'); F.toast('Замер удалён', 'ok'); el.close(); F.render(false); } catch (e) { F.fail(e); }
        });
      };
    }
  }
});
