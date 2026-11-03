/* trends — «Тренды» как Trend View в WHOOP: любой показатель за неделю / месяц / полгода.
   Сверху — выбор показателя (лист со списком по группам), Неделя/Месяц/Полгода, «‹ 5 окт – 3 ноя ›» (назад в историю,
   вперёд до сегодня), «Среднее» крупно, «▲ 8% к прошлому месяцу», фраза простыми словами и график с полосой
   «обычно у тебя» и подсказкой по касанию (F.bars / F.line из app.js). Ч/б: цвет — только у ▲▼.
   Сервер: coach/f_trends.py (POST /api/trends {} → группы; {metric, period: w|m|6m, end?} → точки и среднее).
   Входы: «Все показатели» во «Здоровье» (Тренды) и в «Как менялось» разборов, «＋», адрес ?p=trends[&m=hrv]. */
(window.FormaModules = window.FormaModules || []).push(F => {
  const { esc, num, plural } = F;
  const NB = ' ';
  const PER = [['w', 'Неделя'], ['m', 'Месяц'], ['6m', 'Полгода']];
  const PER_N = { w: 7, m: 30, '6m': 180 };
  const IC = {
    trends: F.svgi('<path d="M3 17l5.5-6 4 4L21 6"/><path d="M15.5 6H21v5.5"/>'),
    down: F.svgi('<path d="M6 9.5l6 6 6-6"/>'),
    prev: F.svgi('<path d="M15 5l-7 7 7 7"/>'),
    next: F.svgi('<path d="M9 5l7 7-7 7"/>'),
    check: F.svgi('<path d="M5 12.5l4.5 4.5L19 7.5"/>'),
  };
  const store = { get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* без хранилища — не страшно */ } } };
  const q = new URLSearchParams(location.search);
  const M = {
    list: null, listErr: null, listLoading: false,
    data: {}, err: {}, loading: new Set(),
    metric: (q.get('p') === 'trends' && q.get('m')) || store.get('forma-trend-m') || 'hrv',
    period: PER_N[store.get('forma-trend-p')] ? store.get('forma-trend-p') : 'm',
    end: null, arg: null, shown: null, sheet: null,
  };
  const key = () => `${M.metric}|${M.period}|${M.end || ''}`;
  const meta = k => { for (const g of (M.list || {}).groups || []) for (const i of g.items) if (i.key === k) return { ...i, group: g.title }; return null; };

  // ---------- загрузка ----------
  async function loadList() {
    if (M.list || M.listLoading) return;
    M.listLoading = true; M.listErr = null;
    try { M.list = await F.api('/api/trends', {}); } catch (e) { M.listErr = e.message || String(e); }
    M.listLoading = false;
    // список нужен только листу выбора (подпись группы есть и в ответе тренда) — экран заново не рисуем;
    // открытый адрес с неизвестным показателем (?m=…) — на запас сил
    if (M.list && !meta(M.metric)) { M.metric = 'hrv'; if (F.S.screen === 'trends') F.render(false); }
  }
  async function load(k = key()) {
    if (M.data[k] || M.loading.has(k)) return;
    const [metric, period, end] = k.split('|');
    M.loading.add(k); delete M.err[k];
    try { M.data[k] = await F.api('/api/trends', { metric, period, ...(end ? { end } : {}) }); } catch (e) { M.err[k] = e.message || String(e); }
    M.loading.delete(k);
    if (F.S.screen === 'trends' && k === key()) F.render(false);
  }

  // ---------- числа простыми словами ----------
  const hmin = h => { const t = Math.round(Math.abs(h) * 60), H = Math.floor(t / 60), m = t % 60; return H ? `${H}${NB}ч${NB}${String(m).padStart(2, '0')}${NB}мин` : `${m}${NB}мин`; };
  const hshort = h => { const t = Math.round(h * 60); return `${Math.floor(t / 60)}ч ${t % 60}м`; };
  const n0 = v => Math.abs(v) >= 1000 ? Math.round(v).toLocaleString('ru-RU') : num(v);
  const dOf = d => d.dec === 1 ? 1 : 0;
  // полностью, с единицей — для фраз и подсказки
  const full = (d, v) => {
    if (v == null) return '—';
    if (d.unit === 'ч') return hmin(v);
    if (d.unit === '%') return `${num(v, dOf(d))}%`;
    if (d.unit === 'из 21') return `${num(v, 1)} из 21`;
    return `${dOf(d) ? num(v, 1) : n0(v)}${NB}${d.unit}`;
  };
  // коротко — подписи на графике
  const short = (d, v) => {
    if (v == null) return '—';
    if (d.unit === 'ч') return hshort(v);
    if (d.unit === '%') return `${num(v, dOf(d))}%`;
    if (d.unit === 'из 21') return num(v, 1);
    if (d.unit === 'шагов' || d.unit === 'ккал') return n0(v);
    return `${dOf(d) ? num(v, 1) : n0(v)}${d.unit === 'мин' ? ' мин' : d.unit === 'мс' ? ' мс' : d.unit === '°C' ? '°' : ''}`;
  };
  // крупное «Среднее»: число и единица отдельно
  const bigOf = (d, v) => {
    if (v == null) return ['—', ''];
    if (d.unit === 'ч') { const t = Math.round(v * 60); return [`${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`, 'ч:мин']; }
    if (d.unit === '%') return [num(v, dOf(d)), '%'];
    return [dOf(d) ? num(v, 1) : n0(v), d.unit];
  };
  const PERW = { w: 'за неделю', m: 'за месяц', '6m': 'за полгода' };
  const SAME = { w: 'так же, как на прошлой неделе', m: 'так же, как в прошлом месяце', '6m': 'так же, как в прошлом полугодии' };
  const GN = { load: 'Нагрузка', recovery: 'Восстановление', sleep: 'Сон', other: 'Другое' };

  // ▲ 8% к прошлому месяцу: цвет — только у стрелки (хорошо/плохо), без оценки — без цвета
  function cmp(d) {
    if (d.prev_avg == null || d.change == null) return `<p class="tr-cmp flat">${d.avg == null ? '' : 'Сравнить не с чем — за прошлый период мало данных'}</p>`;
    const pct = d.change_pct, x = d.change;
    const same = pct != null ? Math.abs(pct) < 1 : Math.abs(x) < (d.unit === '°C' ? .1 : .05);
    if (same) return `<p class="tr-cmp flat">${SAME[d.period]}</p>`;
    const up = x > 0, good = d.good == null ? null : d.good === up;
    const what = pct != null ? `${Math.abs(pct)}%` : d.unit === 'ч' ? hmin(Math.abs(x)) : `${num(Math.abs(x), 1)}${NB}${d.unit}`;
    return `<p class="tr-cmp ${good == null ? 'flat' : good ? 'up' : 'down'}"><i>${up ? '▲' : '▼'}</i>${what} ${esc(d.vs)}</p>`;
  }

  // ---------- части экрана ----------
  function picker(d) {
    const mm = meta(M.metric), m = mm || d || {}, gname = mm ? mm.group : GN[m.group] || 'Показатель';
    return `<button class="tr-pick" data-a="tr-pick" aria-haspopup="dialog" aria-label="Показатель: ${esc(m.title || '')}. Выбрать другой">
      <span class="tr-pk-x"><small>${esc(gname)}</small>
      <b>${esc(m.title || 'Показатель')}</b></span><span class="tr-pk-i">${IC.down}</span></button>`;
  }
  function controls(d) {
    const seg = `<div class="seg tr-seg" role="tablist" aria-label="Период">${PER.map(([v, l]) => `<button class="${M.period === v ? 'on' : ''}" data-a="tr-per" data-v="${v}" role="tab" aria-selected="${M.period === v}">${l}</button>`).join('')}</div>`;
    const back = d ? d.can_back : false, fwd = d ? d.can_next : !!M.end;
    const nav = `<div class="tr-nav"><button class="tr-arr" data-a="tr-prev" ${back ? '' : 'disabled'} aria-label="Раньше">${IC.prev}</button>
      <b class="num">${d ? esc(d.label) : '&nbsp;'}</b>
      <button class="tr-arr" data-a="tr-next" ${fwd ? '' : 'disabled'} aria-label="Позже">${IC.next}</button></div>`;
    return `<div class="tr-ctl rise">${picker(d)}${seg}${nav}</div>`;
  }
  function summary(d, busy) {
    const [v, u] = bigOf(d, d.avg);
    const days = d.days ? `${d.days} ${plural(d.days, 'день', 'дня', 'дней')} с данными` : '';
    return `<section class="card tr-sum rise${busy ? ' tr-busy' : ''}" aria-live="polite">
      <div class="tr-k"><span>Среднее ${PERW[d.period]}</span>${days ? `<span class="tr-days">${days}</span>` : ''}</div>
      <div class="tr-big"><b class="num">${v}</b>${u ? `<small>${esc(u)}</small>` : ''}</div>
      ${cmp(d)}<p class="tr-text">${esc(d.text)}</p>${d.note ? `<p class="tr-note">${esc(d.title)} — ${esc(d.note)}.</p>` : ''}</section>`;
  }
  function chart(d, busy) {
    const rows = d.points, band = d.typical;
    const has = rows.some(r => r.v != null);
    const tipX = r => r.partial ? 'день ещё идёт — в среднее не входит' : r.need ? `нужно было ${hmin(r.need)}` : band ? `обычно ${short(d, band[0])} – ${short(d, band[1])}` : '';
    let svg;
    if (!has) svg = `<div class="chart-empty">${d.metric === 'weight' ? 'Записей веса за этот период нет' : 'Нет данных за этот период'}</div>`;
    else if (d.chart === 'line') svg = F.line(rows, 'v', 'var(--ink)', null, { w: chartWidth(), fmt: v => short(d, v), band, label: `${d.title} ${PERW[d.period]}`, tip: r => tipX(r) });
    else {
      const needs = rows.map(r => r.need).filter(x => x != null);
      const nd = needs.length ? needs.reduce((a, b) => a + b, 0) / needs.length : null;
      svg = F.bars(rows, 'v', { w: chartWidth(), max: d.top || null, color: r => r.partial ? 'var(--hint-2)' : 'var(--ink)', fmt: v => short(d, v), band,
        norm: nd != null ? { v: nd, t: `нужно ${hshort(nd)}` } : null, label: `${d.title} ${PERW[d.period]}`,
        tip: r => [full(d, r.v), tipX(r)] });
    }
    const key = [];
    if (band) key.push(`<span><i class="tr-bandk"></i>обычно у тебя ${d.unit === 'из 21' ? `${num(band[0], 1)}–${num(band[1], 1)}` : `${full(d, band[0])} – ${full(d, band[1])}`} · 30 дней</span>`);
    if (d.metric === 'sleep_hours') key.push('<span><i class="kline"></i>сколько нужно было спать — по WHOOP</span>');
    if (rows.some(r => r.partial)) key.push('<span><i class="tr-partk"></i>сегодня — день ещё идёт</span>');
    return `<section class="card chart-card tr-chart rise${busy ? ' tr-busy' : ''}"><h3 class="ch-t">${esc(d.title)} по дням</h3>
      <div class="chart-box">${svg}</div>${key.length ? `<div class="ch-key">${key.join('')}</div>` : ''}</section>`;
  }
  // от 1600 px у трендов две колонки (trends.css, :has), а общая ширина графика в app.js считает три
  const W3 = matchMedia('(min-width: 1600px)'), HAS = (() => { try { return CSS.supports('selector(:has(*))'); } catch (e) { return false; } })();
  const chartWidth = () => {
    if (!W3.matches || !HAS) return undefined;
    const app = document.getElementById('app'), w = (app && app.clientWidth) || 1500;
    return Math.max(200, Math.min((w - 28) / 2, 800) - 40);
  };
  const skeleton = () => `<section class="card tr-sum"><div class="sk" style="height:14px;width:36%"></div><div class="sk" style="height:44px;width:42%;margin-top:12px"></div>
      <div class="sk" style="height:14px;width:60%;margin-top:12px"></div><div class="sk" style="height:14px;width:90%;margin-top:14px"></div></section>
    <section class="card"><div class="sk" style="height:14px;width:30%"></div><div class="sk" style="height:170px;margin-top:14px"></div></section>`;

  F.screens.trends = (t, dd, arg) => {
    if (arg && arg !== M.arg) { M.arg = arg; M.metric = arg; M.end = null; }
    loadList();
    const k = key(), d = M.data[k];
    if (!d && !M.err[k]) load(k);
    const show = d || M.shown;  // пока грузится новое — прежний график приглушённо, без пустого экрана
    if (d) M.shown = d;
    let h = F.dHead('Тренды', 'все показатели WHOOP', 'var(--ink)');
    h += controls(d || (show && show.metric === M.metric && show.period === M.period ? show : null));
    if (!d && M.err[k]) h += F.retryCard('Тренд не загрузился', 'tr-retry');
    else if (!show) h += skeleton();
    else h += summary(show, !d) + chart(show, !d);
    return h;
  };

  // ---------- лист выбора показателя ----------
  function pickSheet() {
    if (!M.list) {
      if (M.listErr) { M.listErr = null; loadList(); }
      return F.toast('Список показателей загружается…');
    }
    const rows = g => g.items.map(i => `<button class="row tap tr-row${i.key === M.metric ? ' on' : ''}" data-a="tr-set" data-v="${esc(i.key)}" aria-pressed="${i.key === M.metric}">
      <span class="main"><div class="title">${esc(i.title)}</div><div class="meta">${esc([i.unit === 'из 21' ? 'шкала 0–21' : i.unit, i.unit === 'из 21' ? '' : i.note].filter(Boolean).join(' · '))}</div></span>
      <span class="end tr-ck">${i.key === M.metric ? IC.check : ''}</span></button>`).join('');
    M.sheet = F.sheet(`<h2>Показатели</h2>${M.list.groups.map(g => `<div class="tr-g"><h3 class="tr-gh">${esc(g.title)}</h3><div class="list">${rows(g)}</div></div>`).join('')}
      <p class="tr-miss">${esc(M.list.missing)}.</p>`, () => { M.sheet = null; });
    const on = M.sheet.querySelector('.tr-row.on');
    if (on && on.scrollIntoView) requestAnimationFrame(() => on.scrollIntoView({ block: 'nearest' }));
  }

  // ---------- действия ----------
  const go = () => { store.set('forma-trend-m', M.metric); store.set('forma-trend-p', M.period); F.render(false); };
  F.actions['tr-open'] = (el, v) => { F.openScreen('trends', v || undefined); };
  F.actions['tr-pick'] = () => { F.haptic('tap'); pickSheet(); };
  F.actions['tr-set'] = (el, v) => {
    if (M.sheet && M.sheet.close) M.sheet.close();
    if (!v || v === M.metric) return;
    F.haptic('select'); M.metric = v; M.arg = null; go();
  };
  F.actions['tr-per'] = (el, v) => { if (!PER_N[v] || v === M.period) return; F.haptic('select'); M.period = v; go(); };
  const cur = () => M.data[key()];
  const shift = dir => {
    const d = cur(); if (!d) return;
    if (dir < 0 && !d.can_back) return;
    if (dir > 0 && !d.can_next) return;
    const base = F.parseDay(dir < 0 ? d.since : d.until);
    base.setDate(base.getDate() + (dir < 0 ? -1 : PER_N[M.period]));
    const e = F.isoDay(base);
    M.end = e >= d.today ? null : e;
    F.haptic('select'); go();
  };
  F.actions['tr-prev'] = () => shift(-1);
  F.actions['tr-next'] = () => shift(1);
  F.actions['tr-retry'] = () => { M.err = {}; F.render(false); };
  // клавиатура (ноутбук): ← → — период назад/вперёд, когда фокус не на графике и не в поле
  addEventListener('keydown', e => {
    if (F.S.screen !== 'trends' || document.querySelector('.sheet') || e.altKey || e.ctrlKey || e.metaKey) return;
    const a = document.activeElement;
    if (a && (a.matches('input, textarea, select') || a.closest('svg.chart'))) return;
    if (e.key === 'ArrowLeft') { e.preventDefault(); shift(-1); } else if (e.key === 'ArrowRight') { e.preventDefault(); shift(1); }
  });
  // данные обновились (раз в 2 минуты) — тренды пересчитаются при следующем открытии
  F.onData.push(() => { if (F.S.screen !== 'trends') { M.data = {}; M.err = {}; M.shown = null; } });

  F.slots.plus.push(row => row('tr-open', '', IC.trends, 'var(--ink)', 'Тренды', 'все показатели WHOOP по дням'));
});
