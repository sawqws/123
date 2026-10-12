'use strict';
/* 📖 Подготовка к проверочной — вкладка приложения HDP. Подключается до app.js, всё своё — внутри Prep.
   Предмет -> что прошли (темы с сайта, отмечены недавние) -> 1 / 5 / 10 минут -> своё ТЗ -> «Подготовить».
   Готовит бот (те же функции, что кнопки в чате): POST /api/prep/run, ход и результат — GET /api/prep/state.
   Результат приходит и сюда (карточки, ответы скрыты до нажатия), и в чат. */
window.Prep = (() => {
  const P = { subjects: null, loading: false, subj: '', sel: null, minutes: 5, tz: '', st: null, view: 'form', open: new Set(), timer: 0 };
  const MIN = [[1, '⚡', '1 мин', 'главное'], [5, '📝', '5 мин', 'конспект'], [10, '📚', '10 мин', 'с задачами']];
  const SPAN = { 1: 'за 1 минуту', 5: 'за 5 минут', 10: 'за 10 минут' };
  const e = s => Kit.esc(s);
  // текст от Claude: экранировать, **жирное**, переносы строк
  const fmt = s => e(s).replace(/\*\*([^*\n]+?)\*\*/g, '<b>$1</b>').replace(/\*/g, '').replace(/\n/g, '<br>');
  const MS = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
  const dm = s => { const [, m, d] = s.split('-').map(Number); return `${d} ${MS[m - 1]}`; };
  const save = () => { try { localStorage.setItem('hdp.prep', JSON.stringify({ subj: P.subj, minutes: P.minutes })); } catch (x) { /* без хранилища */ } };
  try { Object.assign(P, JSON.parse(localStorage.getItem('hdp.prep') || '{}')); } catch (x) { /* без хранилища */ }
  const here = () => S.tab === 'prep';
  const redraw = () => { if (here()) render(); };
  // выбранный предмет — в видимую часть ленты (она прокручивается вбок)
  const showChip = () => requestAnimationFrame(() => {
    const c = document.querySelector('.pp-subj .chip.on');
    if (c) c.scrollIntoView({ inline: 'center', block: 'nearest' });
  });

  /* ---------- данные ---------- */
  async function loadSubjects() {
    if (P.loading) return;
    P.loading = true;
    try {
      const r = await Kit.api('/api/prep/subjects');
      if (r.subjects.length || !r.loading) P.subjects = r.subjects;
      if (r.loading) setTimeout(() => { P.loading = false; loadSubjects(); }, 4000);
      if (!P.subjects || !P.subjects.find(s => s.name === P.subj)) P.subj = (P.subjects && P.subjects[0] && P.subjects[0].name) || '';
      P.sel = null;
    } catch (x) { Kit.toast(x.message, 'err'); P.subjects = P.subjects || []; }
    if (P.subjects) P.loading = false;
    redraw();
    showChip();
  }

  const key = st => JSON.stringify(st ? [st.running, st.title, st.sub, st.error, st.result && st.result.at] : null);

  async function poll(first) {
    clearTimeout(P.timer);
    P.timer = 0;
    P.polling = true;
    let changed = false;
    try {
      const was = P.st;
      const st = await Kit.api('/api/prep/state');
      changed = key(was) !== key(st);
      P.st = st;
      if (first && st.result && !st.running && P.view === 'form' && !P.seen) P.view = 'result';
      if (was && was.running && !st.running) {
        if (st.error) { Kit.toast(st.error, 'err'); Kit.haptic('error'); } else { P.view = 'result'; P.open.clear(); Kit.haptic('success'); window.scrollTo({ top: 0, behavior: 'smooth' }); }
      }
    } catch (x) { /* сеть моргнула — следующий опрос */ }
    P.polling = false;
    // пока вкладка открыта: во время подготовки — часто, иначе редко (вдруг готовят из чата)
    if (here()) P.timer = setTimeout(poll, P.st && P.st.running ? 2000 : 8000);
    if (changed) redraw();  // без изменений не перерисовываем: не сбить ввод ТЗ
  }

  const subj = () => (P.subjects || []).find(s => s.name === P.subj);
  const selected = () => { const s = subj(); return P.sel || (s ? s.recent.slice() : []); };

  /* ---------- вид ---------- */
  function view() {
    if (!P.subjects) loadSubjects();
    if (!P.polling && !P.timer) poll(!P.st);
    const st = P.st || {};
    if (st.running) return running(st);
    if (P.view === 'result' && st.result) return result(st.result);
    return form();
  }

  function running(st) {
    return `<div class="card live pp-run rise"><div class="live-h"><span class="spin"></span>
      <div class="main"><div class="title">${e(st.title || 'Готовлю')}</div><div class="hint clip">${e(st.sub || '')}</div></div>
      <span class="clock pp-clock" data-t="${Date.now() - (st.secs || 0) * 1000}">${mmss(st.secs || 0)}</span></div>
      <div class="bar indet" style="margin-top:14px"><i></i></div>
      <div class="live-f"><span>читаю теорию с сайта и пишу конспект</span><span>придёт сюда и в чат</span></div></div>
      ${skel(2)}`;
  }

  function form() {
    if (!P.subjects) return `<div class="card feature pp-hero rise"><div class="pp-t">Подготовка к проверочной</div>
      <div class="hint">Смотрю, что вы прошли…</div></div>${skel(3)}`;
    const s = subj();
    const sel = selected();
    let out = `<section class="card feature pp-hero rise">
      <div class="pp-t">Подготовка к проверочной</div>
      <div class="hint">Выбери предмет — возьму то, что вы недавно прошли, и подготовлю. Кратко и по делу.</div>
      ${P.subjects.length ? `<div class="chips pp-subj">${P.subjects.map(x => `<button class="chip ${x.name === P.subj ? 'on' : ''}" data-a="pp-subj" data-id="${e(x.name)}">${e(x.icon)} ${e(x.short)}</button>`).join('')}</div>`
        : '<div class="note" style="margin-top:14px">Тем с сайта пока нет — напиши своё ТЗ ниже</div>'}
    </section>`;
    if (s) {
      out += `<div class="label"><span>Что прошли</span><span class="n">${sel.length} из ${s.topics.length}</span></div>
        <div class="list pp-topics rise">${s.topics.map(t => {
          const on = sel.includes(t.uuid);
          return `<button class="row pp-topic ${on ? 'on' : ''}" data-a="pp-topic" data-id="${e(t.uuid)}" aria-pressed="${on}">
            <span class="pp-check"></span><span class="main"><div class="title two">${e(t.title)}</div>
            <div class="meta clip">${e(dm(t.start))} – ${e(dm(t.deadline))}${t.subs.length ? ' · ' + e(t.subs.slice(0, 3).join(', ')) : ''}</div></span></button>`;
        }).join('')}</div>`;
    }
    out += `<div class="label"><span>Сколько есть времени</span></div>
      <div class="pp-time rise">${MIN.map(([m, ic, t, h]) => `<button class="pp-min ${m === P.minutes ? 'on' : ''}" data-a="pp-min" data-id="${m}" aria-pressed="${m === P.minutes}">
        <span class="i">${ic}</span><b>${t}</b><span>${h}</span></button>`).join('')}</div>
      <div class="label"><span>Своё ТЗ</span><span class="n" style="text-transform:none;letter-spacing:0">необязательно</span></div>
      <textarea id="pp-tz" rows="3" placeholder="Например: только Полтавская битва и реформы Петра I">${e(P.tz)}</textarea>
      <button class="btn primary big pp-go" data-a="pp-go">📖 Подготовить ${SPAN[P.minutes]}</button>`;
    if (P.st && P.st.result) out += '<button class="btn ghost" data-a="pp-last">Последняя подготовка ›</button>';
    return out;
  }

  function result(r) {
    const head = `<section class="card feature pp-res-h rise">
      <div class="pp-ic">${e(r.icon || '📖')}</div>
      <div class="main"><div class="s-sub">${e(r.subj || 'Своё ТЗ')} · ${SPAN[r.minutes] || ''}</div>
        <div class="pp-t">${e(r.title || r.subj || 'Подготовка')}</div>
        ${r.tz ? `<div class="hint clip">ТЗ: ${e(r.tz)}</div>` : ''}</div></section>`;
    let out = head;
    if (r.text) out += `<div class="card rise"><div class="pp-text">${fmt(r.text)}</div></div>`;
    if ((r.points || []).length) {
      out += `<div class="label"><span>Главное</span></div><div class="card pp-points rise"><ul>${r.points.map(p => `<li>${fmt(p)}</li>`).join('')}</ul></div>`;
    }
    if ((r.sections || []).length) {
      out += '<div class="label"><span>Конспект</span></div>' + r.sections.map(s => `<div class="card pp-sec rise">
        ${s.title ? `<div class="pp-sec-t">${fmt(s.title)}</div>` : ''}<div class="pp-text">${fmt(s.text)}</div></div>`).join('');
    }
    if ((r.mistakes || []).length) {
      out += `<div class="label"><span>Типовые ошибки</span></div><div class="card pp-warn rise"><ul>${r.mistakes.map(m => `<li>${fmt(m)}</li>`).join('')}</ul></div>`;
    }
    const qs = r.questions || [];
    if (qs.length) {
      const fresh = r.more ? qs.length - (r.added || 0) : qs.length;
      out += `<div class="label"><span>Проверь себя</span><span class="n" id="pp-count">${P.open.size} / ${qs.length}</span></div>
        <div class="pp-qs">${qs.map((q, i) => `<button class="card pp-q ${P.open.has(i) ? 'open' : ''} ${i >= fresh ? 'new' : ''}" data-a="pp-q" data-id="${i}" aria-expanded="${P.open.has(i)}">
          <div class="pp-q-h"><span class="pp-n">${i + 1}</span><div class="pp-q-t">${fmt(q.q)}</div></div>
          <div class="pp-a"><span class="pp-a-hint">Нажми — покажу ответ</span><div class="pp-a-t">${fmt(q.a || '—')}</div></div></button>`).join('')}</div>`;
    }
    const topics = (r.topics || []).map(t => `<span class="pill">${e(t.title)}</span>`).join('');
    if (topics) out += `<div class="pp-topics-used">${topics}</div>`;
    out += `<div class="pp-actions">
      <div class="btns"><button class="btn tonal" data-a="pp-more">🔁 Ещё вопросы</button>
      ${r.minutes < 10 ? '<button class="btn primary" data-a="pp-deep">📚 Подробнее</button>' : ''}</div>
      <button class="btn ghost" data-a="pp-new">Новая подготовка</button>
      <div class="hint c">Это же — в чате, с ответами под спойлером</div></div>`;
    return out;
  }

  const skel = n => Array.from({ length: n }, () => '<div class="sk row-sk"></div>').join('');

  /* ---------- действия ---------- */
  async function run(body, btn) {
    await Kit.busy(btn, async () => {
      try {
        await Kit.api('/api/prep/run', body);
        P.st = { running: true, title: body.more ? 'Ещё вопросы' : 'Готовлю', sub: P.subj, secs: 0 };
        Kit.haptic('success');
        render(); window.scrollTo({ top: 0, behavior: 'smooth' });
        setTimeout(poll, 600);
      } catch (x) { Kit.toast(x.message, 'err'); Kit.haptic('error'); }
    });
  }

  const act = {
    'pp-subj': el => { P.subj = el.dataset.id; P.sel = null; save(); render(); showChip(); },
    'pp-topic': el => {
      const sel = selected();
      const i = sel.indexOf(el.dataset.id);
      if (i >= 0) sel.splice(i, 1); else sel.push(el.dataset.id);
      P.sel = sel;
      el.classList.toggle('on', i < 0);
      el.setAttribute('aria-pressed', i < 0);
      const n = document.querySelector('.label .n');
      if (n && subj()) n.textContent = `${sel.length} из ${subj().topics.length}`;
    },
    'pp-min': el => {
      P.minutes = +el.dataset.id; save();
      document.querySelectorAll('.pp-min').forEach(b => { const on = +b.dataset.id === P.minutes; b.classList.toggle('on', on); b.setAttribute('aria-pressed', on); });
      const go = document.querySelector('.pp-go');
      if (go) go.textContent = `📖 Подготовить ${SPAN[P.minutes]}`;
    },
    'pp-go': el => {
      const tz = P.tz.trim();
      const topics = subj() ? selected() : [];
      if (!tz && !topics.length) { Kit.toast(subj() ? 'Отметь хотя бы одну тему или напиши ТЗ' : 'Выбери предмет или напиши ТЗ', 'err'); Kit.haptic('warning'); return; }
      run({ subj: topics.length ? P.subj : '', topics, minutes: P.minutes, tz }, el);
    },
    'pp-q': el => {
      const i = +el.dataset.id;
      if (P.open.has(i)) P.open.delete(i); else P.open.add(i);
      el.classList.toggle('open', P.open.has(i));
      el.setAttribute('aria-expanded', P.open.has(i));
      const c = document.getElementById('pp-count');
      if (c) c.textContent = `${P.open.size} / ${document.querySelectorAll('.pp-q').length}`;
    },
    'pp-more': el => run({ more: true }, el),
    'pp-deep': el => run({ again: true, minutes: 10 }, el),
    'pp-new': () => { P.view = 'form'; P.seen = true; render(); window.scrollTo(0, 0); },
    'pp-last': () => { P.view = 'result'; render(); window.scrollTo(0, 0); },
  };

  document.addEventListener('input', ev => { if (ev.target.id === 'pp-tz') P.tz = ev.target.value; });
  // таймер подготовки тикает сам между опросами
  setInterval(() => { const c = document.querySelector('.pp-clock'); if (c) c.textContent = mmss((Date.now() - +c.dataset.t) / 1000); }, 1000);

  return { view, act };
})();
