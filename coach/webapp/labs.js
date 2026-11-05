/* labs — «Анализы крови»: карточка во «Здоровье» (F.slots.healthAfterMonitor: сколько в норме, что вне нормы, шкала
   нормы у каждой строки), экран ?p=labs (дисклеймер «не диагноз», показатели по группам — шкала нормы, «было → стало»,
   по нажатию график и все значения; сдачи по дням с удалением), лист «Добавить анализы» (дата + поля по группам).
   Сервер — coach/f_labs.py: /api/labs {} → {latest, series, days, count, summary, known, groups, disclaimer};
   /save {day, values: {key: число}} и /delete {day, key?} → то же. Кратко — F.S.data.ext.labs = {latest, summary}.
   Нормы на графике не рисуем (Глеб 03.11) — норма видна шкалой и словами «норма 20–150». */
(window.FormaModules = window.FormaModules || []).push(F => {
  const { esc, svgi, cap } = F;
  const I = {
    drop: svgi('<path d="M12 3.2s6 6.4 6 10.8a6 6 0 0 1-12 0c0-4.4 6-10.8 6-10.8z"/><path d="M9.2 14.2a2.9 2.9 0 0 0 2.8 2.9"/>'),
    plus: svgi('<path d="M12 5.5v13M5.5 12h13"/>'),
    x: svgi('<path d="M7.5 7.5l9 9M16.5 7.5l-9 9"/>'),
  };
  const DISCLAIMER = 'Это не диагноз. Нормы — общий ориентир для подростка, у лаборатории свои — смотри бланк. Что-то вне нормы — обсуди с врачом.';
  const FLAG = { low: 'ниже нормы', high: 'выше нормы', ok: 'норма' };
  const CARD_OUT = 4;

  // ---------- числа и даты ----------
  const okDay = s => /^\d{4}-\d{2}-\d{2}$/.test(s || '');
  const todayIso = () => (F.S.data && F.S.data.today && F.S.data.today.day) || F.isoDay(new Date());
  const nf = (v, dg) => v == null || !Number.isFinite(Number(v)) ? '—' : F.num(Number(v), dg).replace(/,0+$/, '').replace(/(,\d*?)0+$/, '$1');
  const dayShort = s => { if (!okDay(s)) return ''; const d = F.parseDay(s); return `${d.getDate()} ${F.MON_S[d.getMonth()]}`; };
  const dayYear = s => { if (!okDay(s)) return ''; const d = F.parseDay(s); return `${dayShort(s)}${d.getFullYear() !== new Date().getFullYear() ? ` ${d.getFullYear()}` : ''}`; };
  const plural = (n, a, b, c) => `${n} ${F.plural(n, a, b, c)}`;
  const known = d => (d && d.known) || {};
  const dg = (d, key) => { const k = known(d)[key]; return k && k.digits != null ? k.digits : 1; };
  const normText = (x, d) => {
    const g = Math.max(dg(d, x.key), x.lo != null && x.lo % 1 ? 1 : 0);
    if (x.lo != null && x.hi != null) return `${nf(x.lo, g)}–${nf(x.hi, g)}`;
    return x.lo != null ? `от ${nf(x.lo, g)}` : x.hi != null ? `до ${nf(x.hi, g)}` : '';
  };
  const sign = (x, g) => { const r = Math.round(x * 10 ** g) / 10 ** g; return r > 0 ? `+${nf(r, g)}` : r < 0 ? `−${nf(-r, g)}` : '±0'; };

  // ---------- данные ----------
  const ext = () => F.S.data && F.S.data.ext && F.S.data.ext.labs;
  function norm(r) {
    const latest = Array.isArray(r.latest) ? r.latest.filter(Boolean) : [];
    return { latest, days: Array.isArray(r.days) ? r.days : [], series: r.series && typeof r.series === 'object' ? r.series : {},
      count: Number.isFinite(r.count) ? r.count : 0, summary: r.summary || { total: latest.length, ok: 0, low: [], high: [], day: '' },
      known: r.known || {}, groups: r.groups || {}, disclaimer: r.disclaimer || DISCLAIMER, full: Array.isArray(r.days) };
  }
  const data = () => F.S.labs || (ext() ? norm(ext()) : null);
  const sig = r => `${(r.latest || []).map(x => `${x.key}:${x.day}:${x.v}`).join(',')}`;
  function put(r) {
    if (!r || typeof r !== 'object') return;
    F.S.labs = norm(r);
    if (F.S.data) { F.S.data.ext = F.S.data.ext || {}; F.S.data.ext.labs = { latest: F.S.labs.latest, summary: F.S.labs.summary }; }
  }
  async function load(screen) {
    if (F.S.labsLoading) return;
    F.S.labsLoading = true;
    try { put(await F.api('/api/labs', {})); F.S.labsFail = F.S.labsErr = false; } catch (e) { F.S.labsFail = !data(); if (screen) { F.S.labsErr = true; F.fail(e); } }
    F.S.labsLoading = false;
    if (F.S.screen === 'labs' || (!F.S.screen && F.S.tab === 'trends')) F.render(false);
  }
  F.onData.push(d => {
    const e = d.ext && d.ext.labs;
    if (e && F.S.labs && sig(norm(e)) !== sig(F.S.labs)) F.S.labs = null;  // записали в другом окне — перечитаем
    if (!e && !F.S.labsTried) { F.S.labsTried = true; load(false); }
  });
  const vib = kind => {
    F.haptic(kind);
    const tgh = F.tg && F.tg.HapticFeedback && F.tg.initData;
    if (!tgh && navigator.vibrate) { try { navigator.vibrate(kind === 'success' ? [10, 70, 18] : 8); } catch (e) { /* нет вибрации */ } }
  };

  // ---------- шкала нормы: полоса, норма — светлее, точка — значение ----------
  function scale(x) {
    const v = Number(x.v), lo = x.lo, hi = x.hi;
    let a, b;
    if (lo != null && hi != null) { const w = hi - lo; a = lo - w * .45; b = hi + w * .45; }
    else if (hi != null) { a = 0; b = hi * 1.7; }
    else { a = lo * .4; b = lo * 2.2; }
    a = Math.min(a, v); b = Math.max(b, v);
    if (lo == null && a > 0) a = 0;
    const p = t => Math.max(0, Math.min(100, (t - a) / ((b - a) || 1) * 100));
    const l = lo != null ? p(lo) : 0, r = hi != null ? p(hi) : 100;
    const pos = Math.max(2.5, Math.min(97.5, p(v)));
    return `<span class="lb-sc ${x.flag}" aria-hidden="true"><span class="lb-nz" style="left:${l.toFixed(1)}%;width:${(r - l).toFixed(1)}%"></span><i style="left:${pos.toFixed(1)}%"></i></span>`;
  }
  const mark = f => f === 'low' ? '▼' : f === 'high' ? '▲' : '';
  const flagHtml = x => `<span class="lb-f ${x.flag}">${mark(x.flag) ? `<b aria-hidden="true">${mark(x.flag)}</b>` : ''}${FLAG[x.flag] || ''}</span>`;
  const vHtml = (x, d) => `<span class="lb-vv"><b class="num">${esc(nf(x.v, dg(d, x.key)))}</b><small>${esc(x.unit || '')}</small></span>`;
  function trendText(x, d) {
    if (!x.prev) return '';
    const g = dg(d, x.key);
    if (x.trend === 'same') return `как в прошлый раз (${dayYear(x.prev.day)})`;
    const how = x.better === 'better' ? ' · ближе к норме' : x.better === 'worse' ? ' · дальше от нормы' : '';
    return `<span class="num">${sign(x.delta, g)}</span> с ${esc(dayYear(x.prev.day))}${how}`;
  }

  // ---------- карточка во «Здоровье» ----------
  const skel = n => `<article class="card">${F.skel(n)}</article>`;
  const addBtn = (cls = '') => `<button class="btn primary lb-add ${cls}" data-a="lb-add" aria-label="Добавить анализы">${I.plus}<span>Добавить анализы</span></button>`;
  F.slots.healthAfterMonitor.push(() => {
    const d = data(), title = 'Анализы';
    if (!d) return F.S.labsFail ? '' : `<div class="sec rise">${cap(title)}${skel(3)}</div>`;
    if (!d.latest.length) {
      return `<div class="sec rise">${cap(title)}<section class="card lb-card lb-empty"><p>Впиши анализы крови с бланка — Форма покажет, что в норме, и как меняется от сдачи к сдаче.</p>${addBtn()}</section></div>`;
    }
    const s = d.summary, out = d.latest.filter(x => x.flag !== 'ok'), okN = d.latest.length - out.length;
    const row = x => `<button type="button" class="lb-r" data-a="lb-open" data-v="${esc(x.key)}"><span class="lb-l"><b>${esc(x.label)}</b>${flagHtml(x)}</span>
      <span class="lb-rv">${vHtml(x, d)}${scale(x)}</span></button>`;
    const okRow = okN ? `<button type="button" class="lb-r lb-okr" data-a="lb-open"><span class="lb-l"><b>В норме</b><small>${esc(d.latest.filter(x => x.flag === 'ok').slice(0, 4).map(x => x.label).join(', '))}${okN > 4 ? ' и др.' : ''}</small></span>
      <span class="lb-rv"><span class="lb-vv"><b class="num">${okN}</b><small>из ${d.latest.length}</small></span></span></button>` : '';
    return `<div class="sec rise">${cap(title, '<button class="link" data-a="lb-open" aria-label="Анализы: все показатели и графики">Все</button>')}
      <section class="card lb-card"><p class="lb-lead">${out.length ? `<b>${plural(out.length, 'показатель', 'показателя', 'показателей')} вне нормы</b>` : '<b>Всё в норме</b>'}<span>сдано ${esc(dayYear(s.day))}</span></p>
        <div class="lb-rows">${out.slice(0, CARD_OUT).map(row).join('')}${okRow}</div>
        <p class="lb-note">Не диагноз — что вне нормы, покажи врачу.</p></section></div>`;
  });
  F.slots.plus.push(row => row('lb-add', '', I.drop, 'var(--ink)', 'Анализы крови', 'вписать с бланка'));
  F.slots.moreProfile = F.slots.moreProfile || [];
  F.slots.moreProfile.push(row => {
    const d = data(), s = d && d.summary;
    return row({ a: 'lb-open', ic: I.drop, title: 'Анализы',
      meta: d && d.latest.length ? `${s.low.length + s.high.length ? `вне нормы: ${s.low.length + s.high.length}` : 'всё в норме'} · ${esc(dayYear(s.day))}` : 'нормы и динамика',
      end: d && d.latest.length ? `<b class="num">${d.latest.length}</b>` : '' });
  });

  // ---------- экран «Анализы» ----------
  // ряд для F.line: по дню от первой сдачи до последней (≤ 400 дней), между сдачами — пусто
  function rowsOf(pts) {
    if (pts.length < 2) return pts.map(p => ({ day: p.day, v: p.v }));
    const span = (F.parseDay(pts[pts.length - 1].day) - F.parseDay(pts[0].day)) / 864e5;
    if (span > 399 || span < 1) return pts.map(p => ({ day: p.day, v: p.v }));
    const by = {}, rows = [], cur = F.parseDay(pts[0].day), end = pts[pts.length - 1].day;
    pts.forEach(p => { by[p.day] = p.v; });
    for (let i = 0; i < 400; i++) { const day = F.isoDay(cur); rows.push({ day, v: day in by ? by[day] : null }); if (day >= end) break; cur.setDate(cur.getDate() + 1); }
    return rows;
  }
  function detail(x, d) {
    const k = known(d)[x.key] || {}, g = dg(d, x.key);
    const pts = (d.series[x.key] || []).filter(p => okDay(p.day) && Number.isFinite(Number(p.v))).map(p => ({ day: p.day, v: Number(p.v), flag: p.flag }));
    const chart = pts.length >= 2 ? `<div class="chart-box">${F.line(rowsOf(pts), 'v', 'var(--ink)', null, { fmt: v => nf(v, g), label: `${x.label}: как менялось` })}</div>` : '<p class="lb-one">Одна сдача — после следующей здесь будет график.</p>';
    const hist = [...pts].reverse().map(p => `<div class="lb-h"><span>${esc(dayYear(p.day))}</span><b class="num">${esc(nf(p.v, g))} <small>${esc(x.unit)}</small></b><span class="lb-f ${p.flag}">${mark(p.flag) ? `<b aria-hidden="true">${mark(p.flag)}</b>` : ''}${FLAG[p.flag] || ''}</span>
      <button type="button" class="lb-x" data-a="lb-del" data-v="${esc(p.day)}|${esc(x.key)}" aria-label="Удалить: ${esc(x.label)}, ${esc(dayYear(p.day))}">${I.x}</button></div>`).join('');
    return `<div class="lb-det">${chart}${k.about ? `<p class="lb-about">${esc(k.about)}</p>` : ''}
      <p class="lb-about">Норма — ${esc(normText(x, d))} ${esc(x.unit)}${x.lab ? ' (с бланка)' : ' (ориентир)'}${x.unit_in ? ` · на бланке в ${esc(x.unit_in)}, пересчитано` : ''}</p>
      <div class="lb-hist">${hist}</div></div>`;
  }
  F.screens.labs = () => {
    const d = F.S.labs && F.S.labs.full ? F.S.labs : null;
    const title = F.dHead('Анализы', 'кровь · нормы и динамика', 'var(--ink)');
    if (!d) {
      if (F.S.labsErr) return title + '<section class="card lb-empty"><p>Не загрузилось.</p><button class="btn primary" data-a="lb-retry">Повторить</button></section>';
      load(true);
      return title + skel(5);
    }
    const disc = `<section class="card lb-disc rise" role="note"><p><b>Не диагноз.</b> ${esc(String(d.disclaimer).replace(/^Это не диагноз\.\s*/, ''))}</p></section>`;
    if (!d.latest.length) {
      return title + disc + `<section class="card lb-card lb-empty lb-hero rise"><span class="lb-ei">${I.drop}</span>
        <p><b>Пока пусто</b>Впиши анализы с бланка: гемоглобин, ферритин, витамин D и другие. Форма отметит, что вне нормы, и покажет динамику.</p>${addBtn()}</section>`;
    }
    const s = d.summary, outN = s.low.length + s.high.length;
    let h = title + `<section class="card lb-card lb-hero rise">
      <p class="lb-lead big"><b class="num">${s.ok}</b><span>из ${d.latest.length} в норме${outN ? ` · ${outN} вне нормы` : ''}</span></p>
      <p class="lb-sub">Последняя сдача — ${esc(dayYear(s.day))} · всего ${plural(d.count, 'сдача', 'сдачи', 'сдач')}</p>${addBtn()}</section>` + disc;
    const open = F.S.lbOpen || (F.S.lbOpen = {});
    const groups = {};
    d.latest.forEach(x => { (groups[x.group] = groups[x.group] || []).push(x); });
    Object.keys(d.groups).filter(g => groups[g]).forEach(g => {
      h += `<div class="sec rise">${cap(esc(d.groups[g]))}<section class="card lb-card lb-grp">${groups[g].map(x => {
        const on = !!open[x.key], tr = trendText(x, d);
        return `<div class="lb-item${on ? ' on' : ''}"><button type="button" class="lb-r" data-a="lb-tog" data-v="${esc(x.key)}" aria-expanded="${on}">
          <span class="lb-l"><b>${esc(x.label)}</b>${x.flag !== 'ok' ? flagHtml(x) : ''}<small>норма ${esc(normText(x, d))}${x.day !== s.day ? ` · ${esc(dayYear(x.day))}` : ''}</small>${tr ? `<small class="lb-tr">${tr}</small>` : ''}</span>
          <span class="lb-rv">${vHtml(x, d)}${scale(x)}</span><span class="lb-chev" aria-hidden="true"></span></button>${on ? detail(x, d) : ''}</div>`;
      }).join('')}</section></div>`;
    });
    h += `<div class="sec rise">${cap('Сдачи', `<span class="num">${d.count}</span>`)}<div class="list plain lb-days">${d.days.map(day => {
      const bad = day.values.filter(v => v.flag !== 'ok').length;
      return `<div class="row lb-day"><span class="main"><div class="title">${esc(F.dateLine ? F.dateLine(day.day) : dayYear(day.day))}</div>
        <div class="meta">${plural(day.values.length, 'показатель', 'показателя', 'показателей')}${bad ? ` · вне нормы ${bad}` : ''}</div></span>
        <button type="button" class="btn lb-dadd" data-a="lb-add" data-v="${esc(day.day)}" aria-label="Дописать в сдачу ${esc(dayYear(day.day))}">${I.plus}</button>
        <button type="button" class="lb-x" data-a="lb-del" data-v="${esc(day.day)}" aria-label="Удалить сдачу ${esc(dayYear(day.day))}">${I.x}</button></div>`;
    }).join('')}</div></div>`;
    h += `<div class="sec rise"><div class="list"><button class="row tap" data-a="ask-go" data-v="Посмотри мои анализы крови: что в норме, что нет и что обсудить с врачом? Без добавок и диет.">
      <span class="ic">${F.I.coach}</span><span class="main"><div class="title">Спросить в чате</div><div class="meta">что значат цифры и что спросить у врача</div></span><span class="end chev"></span></button></div>
      <p class="foot">${esc(d.disclaimer)} Добавки и лекарства — только по назначению врача.</p></div>`;
    return h;
  };

  // ---------- действия ----------
  F.actions['lb-open'] = (el, key) => {
    vib('tap');
    if (key) { F.S.lbOpen = F.S.lbOpen || {}; F.S.lbOpen[key] = true; }
    if (F.S.screen === 'labs') { F.render(false); return; }
    F.openScreen('labs');
  };
  F.actions['lb-retry'] = () => { F.S.labsErr = false; F.render(false); };
  F.actions['lb-tog'] = (el, key) => {
    const o = F.S.lbOpen = F.S.lbOpen || {};
    o[key] = !o[key]; vib('select'); F.render(false);
  };
  F.actions['lb-del'] = async (el, v) => {
    if (!el.classList.contains('sure')) {  // первое касание — спросить
      vib('select');
      const label = el.getAttribute('aria-label');
      el.classList.add('sure'); el.innerHTML = '<span>Удалить?</span>'; el.setAttribute('aria-label', 'Точно удалить?');
      clearTimeout(el._t);
      el._t = setTimeout(() => { if (el.isConnected && el.classList.contains('sure')) { el.classList.remove('sure'); el.innerHTML = I.x; el.setAttribute('aria-label', label); } }, 3500);
      return;
    }
    clearTimeout(el._t);
    const [day, key] = String(v || '').split('|');
    await F.busy(el, async () => {
      try { put(await F.api('/api/labs/delete', key ? { day, key } : { day })); vib('success'); F.toast(key ? 'Значение удалено' : 'Сдача удалена', 'ok'); F.render(false); } catch (e) { F.fail(e); }
    });
  };
  F.actions['lb-add'] = (el, day) => addSheet(okDay(day) ? day : '');

  // ---------- лист «Добавить анализы» ----------
  async function addSheet(day0) {
    vib('tap');
    let d = F.S.labs && F.S.labs.full ? F.S.labs : null;
    const el = F.sheet('<div class="lb-form" data-lb-form></div>');
    const box = el.querySelector('[data-lb-form]');
    if (!d) {
      box.innerHTML = `<h2>Анализы крови</h2>${F.skel(4)}`;
      try { put(await F.api('/api/labs', {})); d = F.S.labs; } catch (e) { if (el.isConnected) { el.close(); F.fail(e); } return; }
      if (!el.isConnected) return;
    }
    const today = todayIso(), K = d.known, day = day0 || today;
    const have = (d.days.find(x => x.day === day) || { values: [] }).values;
    const cur = k => { const x = have.find(v => v.key === k); return x ? String(nf(x.v, K[k].digits)) : ''; };
    const groups = {};
    Object.keys(K).forEach(k => { (groups[K[k].group] = groups[K[k].group] || []).push(k); });
    box.innerHTML = `<h2>${day0 ? 'Дописать в сдачу' : 'Анализы крови'}</h2>
      <p class="small muted">Впиши с бланка, что есть, — пустое пропущу. Запятая или точка — всё равно.</p>
      <form data-lb-f novalidate>
        <label class="field lb-dayf"><span>День сдачи</span><input name="day" type="date" max="${today}" value="${esc(day)}" required></label>
        ${Object.keys(d.groups).filter(g => groups[g]).map(g => `<fieldset class="lb-fs"><legend>${esc(d.groups[g])}</legend><div class="lb-grid">${groups[g].map(k => `
          <label class="field lb-in"><span>${esc(K[k].label)}, ${esc(K[k].unit)}</span>
          <input name="${esc(k)}" type="text" inputmode="decimal" autocomplete="off" enterkeyhint="done" placeholder="${esc(normText({ key: k, lo: K[k].lo, hi: K[k].hi }, d))}" data-was="${esc(cur(k))}" value="${esc(cur(k))}"></label>`).join('')}</div></fieldset>`).join('')}
        <p class="lb-note">${esc(DISCLAIMER)}</p>
        <button class="btn primary lb-save" type="submit">Сохранить</button></form>`;
    el.scrollTop = 0;
    const form = box.querySelector('[data-lb-f]');
    if (matchMedia('(pointer: fine)').matches) setTimeout(() => { const f = form.querySelector('.lb-in input'); if (f && el.isConnected) f.focus(); }, 350);
    form.addEventListener('submit', async ev => {
      ev.preventDefault();
      const values = {};
      Object.keys(K).forEach(k => { const f = form.elements[k]; const v = f ? f.value.trim() : ''; if (v && v !== f.dataset.was) values[k] = v; });
      const n = Object.keys(values).length;
      if (!n) { vib('error'); F.toast('Впиши хотя бы одно значение', 'err'); return; }
      await F.busy(ev.submitter || form.querySelector('[type=submit]'), async () => {
        try {
          put(await F.api('/api/labs/save', { day: form.elements.day.value || day, values }));
          vib('success'); F.toast(`Сохранил ${plural(n, 'значение', 'значения', 'значений')}`, 'ok'); el.close();
          if (F.S.screen !== 'labs') F.openScreen('labs'); else F.render(false);
        } catch (e) { vib('error'); F.fail(e); }
      });
    });
    form.addEventListener('keydown', ev => {
      if (ev.key !== 'Enter' || ev.target.tagName !== 'INPUT') return;
      ev.preventDefault();
      const ins = [...form.querySelectorAll('input')], i = ins.indexOf(ev.target);  // Enter — к следующему полю, на последнем — сохранить
      if (i >= 0 && i < ins.length - 1 && !ev.metaKey && !ev.ctrlKey) ins[i + 1].focus(); else form.requestSubmit();
    });
  }
});
