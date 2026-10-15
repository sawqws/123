/* journal — «Дневник» как WHOOP Journal: утром отмечаешь «да/нет» про вчера, а приложение считает, как каждая
   привычка сказывается на восстановлении следующего утра. Сервер — coach/f_journal.py. */
(window.FormaModules = window.FormaModules || []).push(F => {
  const J = { data: null, loading: false, err: false, seq: 0, sheet: null };
  const ICO = {
    book: F.svgi('<path d="M6 3.5h11.5a1 1 0 0 1 1 1v15a1 1 0 0 1-1 1H6a1.5 1.5 0 0 1-1.5-1.5v-14A1.5 1.5 0 0 1 6 3.5z"/><path d="M8.5 3.5v17M11.5 8h4.5M11.5 11.5h4.5"/>'),
    check: F.svgi('<path d="M5 12.5l4.5 4.5L19 7.5"/>'),
  };
  const ext = () => (F.S.data && F.S.data.ext && F.S.data.ext.journal) || null;
  const habit = k => (J.data.habits || []).find(h => h.key === k) || { title: k };
  const signed = v => `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v)}%`;
  const up = t => t ? t[0].toUpperCase() + t.slice(1) : t;
  const impColor = v => v > 0 ? 'var(--rec-green)' : v < 0 ? 'var(--rec-red)' : 'var(--hint)';

  async function load(day) {
    J.loading = true; J.err = false;
    try {
      const r = await F.api(`/api/journal${day ? `?d=${encodeURIComponent(day)}` : ''}`);
      J.data = r; syncExt(r.ext);
    } catch (e) { J.err = true; F.fail(e); }
    J.loading = false;
    if (F.S.screen === 'journal') F.render(false);
  }
  function syncExt(x) { if (x && F.S.data) { F.S.data.ext = F.S.data.ext || {}; F.S.data.ext.journal = x; } }

  // ---------- главная: «Дневник за вчера» в «Мой дневник» ----------
  F.slots.homeJournal.push(() => {
    const x = ext();
    if (!x) return '';
    const done = x.pending === 0;
    return `<section class="card rise tap jr-home" data-a="jr-open" role="button">
      ${F.head(`<span class="hico" style="--hc:var(--teal)">${ICO.book}</span>Дневник за вчера`, `<span class="num"><b>${x.answered}</b> / ${x.total}</span><span class="chev"></span>`)}
      ${F.bar(F.pct(x.answered, x.total), 'var(--teal)')}
      <div class="jr-home-b"><p class="small muted">${done ? 'Всё отмечено. Влияние привычек — внутри.'
        : `${x.pending} ${F.plural(x.pending, 'вопрос', 'вопроса', 'вопросов')} без ответа — это 20 секунд.`}</p>
        ${done ? '' : '<button class="btn primary sm" data-a="jr-open">Заполнить</button>'}</div></section>`;
  });

  // ---------- «＋»: строка «Дневник» ----------
  F.slots.plus.push(row => {
    const x = ext();
    return row('jr-open', '', ICO.book, 'var(--teal)', 'Дневник', x ? (x.pending ? `за вчера: ${x.pending} без ответа` : 'за вчера всё отмечено') : 'привычки за вчера');
  });

  // ---------- «Здоровье»: топ-3 влияния ----------
  F.slots.healthAfterMonitor.push(() => {
    const x = ext();
    if (!x) return '';
    const top = x.top || [];
    const max = Math.max(10, ...top.map(i => Math.abs(i.impact)));
    return `<section class="card rise tap jr-health" data-a="jr-open" role="button">${F.cap('Что влияет на восстановление', '<span class="chev"></span>')}
      ${top.length ? top.map(i => impRow(i.short, i.impact, max, `${i.yes} да · ${i.no} нет`)).join('')
        : '<p class="small muted">Отвечай в дневнике каждое утро. Когда наберётся хотя бы по 3 дня «да» и «нет», здесь будет видно, какие привычки помогают восстановлению, а какие мешают.</p>'}</section>`;
  });

  // строка влияния: подпись и полоска от центра (+ вправо, − влево)
  function impRow(title, v, max, sub) {
    if (v == null) {
      return `<div class="jr-imp off"><div class="jr-imp-t"><b>${F.esc(up(title))}</b><small>${sub}</small></div><span class="jr-imp-v">мало данных</span></div>`;
    }
    const w = Math.min(50, Math.abs(v) / max * 50);
    return `<div class="jr-imp"><div class="jr-imp-t"><b>${F.esc(up(title))}</b><small>${sub}</small></div>
      <span class="jr-imp-v num" style="color:${impColor(v)}">${signed(v)}</span>
      <div class="jr-div"><i class="jr-mid"></i><i class="jr-fill" style="${v >= 0 ? 'left:50%' : `left:${50 - w}%`};width:${w}%;background:${impColor(v)}"></i></div></div>`;
  }

  // ---------- экран дневника ----------
  F.screens.journal = () => {
    const head = F.dHead('Дневник', 'как прошёл вчерашний день', 'var(--teal)');
    if (!J.data) {
      if (J.err) return head + F.retryCard('Дневник не загрузился', 'jr-retry');
      if (!J.loading) load();
      return head + `<section class="card">${F.skel(4)}</section>`;
    }
    const d = J.data, day = d.day, ans = d.answers || {}, hints = d.hints || {};
    const sel = d.selected || [];
    const done = sel.filter(k => k in ans).length;
    let h = head;
    h += `<div class="jr-pager rise"><button class="pg" data-a="jr-day" data-v="-1" aria-label="День назад" ${day <= d.first ? 'disabled' : ''}>${F.I.back}</button>
      <div class="pg-t"><b>${F.esc(F.dateWord(day))}</b><span>${F.dateLine(day)} · <span class="num">${done}/${sel.length}</span></span></div>
      <button class="pg" data-a="jr-day" data-v="1" aria-label="День вперёд" ${day >= d.yesterday ? 'disabled' : ''}>${F.I.next}</button></div>`;
    h += `<p class="small muted jr-lead">Отмечай про день целиком: вечер, еду, сон. Ответ сравнивается с восстановлением на следующее утро.</p>`;
    for (const g of d.groups) {
      const keys = sel.filter(k => habit(k).group === g.key);
      if (!keys.length) continue;
      h += `<div class="sec rise">${F.cap(F.esc(g.title))}<div class="card jr-qs">${keys.map(k => {
        const v = ans[k];
        return `<div class="jr-q"><div class="jr-qt">${F.esc(habit(k).title)}${hints[k] ? `<small>${F.esc(hints[k])}</small>` : ''}</div>
          <div class="jr-yn" role="group"><button class="${v === false ? 'on no' : ''}" data-a="jr-ans" data-v="${k}:0" aria-pressed="${v === false}">Нет</button>
          <button class="${v === true ? 'on yes' : ''}" data-a="jr-ans" data-v="${k}:1" aria-pressed="${v === true}">Да</button></div></div>`;
      }).join('')}</div></div>`;
    }
    h += `<button class="btn tonal jr-setup-b rise" data-a="jr-setup">Настроить вопросы · ${sel.length} из ${d.habits.length}</button>`;
    h += impactBlock(d);
    return h;
  };

  // «Влияние на восстановление»: все привычки с ответами, сильные сверху
  function impactBlock(d) {
    const list = d.impact || [];
    const ok = list.filter(i => i.enough), few = list.filter(i => !i.enough);
    const max = Math.max(10, ...ok.map(i => Math.abs(i.impact)));
    let body;
    if (!list.length) {
      body = `<p class="small muted">Пока нет ответов, для которых WHOOP уже посчитал восстановление следующего утра. Заполняй дневник каждый день — через пару недель здесь появятся цифры.</p>`;
    } else {
      body = ok.map(i => impRow(i.short, i.impact, max, `да → ${Math.round(i.yes_avg)}% · нет → ${Math.round(i.no_avg)}% · ${i.yes + i.no} дн.`)).join('')
        + few.map(i => impRow(i.short, null, max, `«да» ${i.yes}, «нет» ${i.no} — нужно по ${d.min}`)).join('');
    }
    return `<div class="sec rise">${F.cap('Влияние на восстановление', 'за 90 дней')}<section class="card jr-impact">
      <p class="small muted jr-how">Средний % восстановления наутро после «да» минус после «нет». Это связь, а не точная причина: на восстановление влияет всё сразу.</p>
      ${body}</section></div>`;
  }

  // ---------- настройка списка ----------
  function setupSheet() {
    const d = J.data;
    if (!d) return;
    const on = new Set(d.selected);
    const pick = k => `<button class="jr-pick ${on.has(k) ? 'on' : ''}" data-a="jr-pick" data-v="${k}" aria-pressed="${on.has(k)}">
      <span class="jr-box">${ICO.check}</span><span>${F.esc(habit(k).title)}</span></button>`;
    J.sheet = F.sheet(`<h2>Что отслеживать</h2><p class="small muted">Выбери то, что бывает у тебя по-разному. Чем меньше вопросов, тем проще отвечать каждый день.</p>
      ${d.groups.map(g => `<div class="jr-sg"><div class="jr-sg-t">${F.esc(g.title)}</div>${d.habits.filter(h => h.group === g.key).map(h => pick(h.key)).join('')}</div>`).join('')}
      <button class="btn primary" data-a="jr-save">Сохранить</button><button class="btn ghost" data-a="jr-default">Как было по умолчанию</button>`);
  }
  async function saveSetup(keys, el) {
    try {
      const r = await F.busy(el, () => F.api('/api/journal/setup', { keys, day: J.data.day }));
      J.data = r; syncExt(r.ext); F.haptic('success');
      if (J.sheet) J.sheet.close();
      F.render(false);
    } catch (e) { F.fail(e); }
  }

  // ---------- действия ----------
  Object.assign(F.actions, {
    'jr-open': () => {
      J.data = null; J.err = false;
      if (F.S.screen !== 'journal') F.openScreen('journal'); else F.render(false);
    },
    'jr-retry': () => { J.err = false; F.render(false); },
    'jr-day': (el, v) => {
      if (!J.data || J.loading) return;
      const nd = F.isoDay(new Date(F.parseDay(J.data.day).getTime() + Number(v) * 864e5));
      if (nd < J.data.first || nd > J.data.yesterday) return;
      F.haptic('select'); load(nd);
    },
    'jr-ans': async (el, v) => {
      if (!J.data) return;
      const [k, b] = v.split(':'), want = b === '1';
      const prev = J.data.answers[k];
      const value = prev === want ? null : want;  // повторное нажатие снимает ответ
      if (value == null) delete J.data.answers[k]; else J.data.answers[k] = value;
      F.haptic('select'); F.render(false);
      const my = ++J.seq, day = J.data.day;
      try {
        const r = await F.api('/api/journal/answer', { day, key: k, value });
        syncExt(r.ext);
        if (my === J.seq && J.data && J.data.day === day) { J.data = r; if (F.S.screen === 'journal') F.render(false); }
      } catch (e) {
        F.fail(e);
        if (J.data && J.data.day === day) { if (prev == null) delete J.data.answers[k]; else J.data.answers[k] = prev; F.render(false); }
      }
    },
    'jr-setup': () => { F.haptic('tap'); setupSheet(); },
    'jr-pick': el => { el.classList.toggle('on'); el.setAttribute('aria-pressed', el.classList.contains('on')); F.haptic('select'); },
    'jr-save': el => {
      const keys = [...(J.sheet || document).querySelectorAll('.jr-pick.on')].map(b => b.dataset.v);
      if (!keys.length) { F.toast('Выбери хотя бы один вопрос', 'err'); return; }
      saveSetup(keys, el);
    },
    'jr-default': el => saveSetup(J.data.default, el),
  });
});
