/* reviews — «Разборы за сегодня»: карточка на главной под кольцами (утро · после тренировки · вечер) и лист с полным
   текстом. Бот присылает в Telegram 3–5 строк и «📱 Открыть» (?p=review-<id> — сразу этот лист), целиком — здесь.
   Данные — F.S.data.ext.reviews (из /api/all: {items, new, evening_at, morning}); «новый» — точка, пока не открыт
   (POST /api/reviews/seen {id}). Открытие меняет только свою строку (без перерисовки экрана). Сервер — coach/f_reviews.py. */
(window.FormaModules = window.FormaModules || []).push(F => {
  const { esc, svgi, cap } = F;
  const IC = {
    morning: svgi('<circle cx="12" cy="12" r="4"/><path d="M12 2.8v2M12 19.2v2M2.8 12h2M19.2 12h2M5.5 5.5l1.4 1.4M17.1 17.1l1.4 1.4M5.5 18.5l1.4-1.4M17.1 6.9l1.4-1.4"/>'),
    workout: F.I.bolt,
    evening: F.I.moon,
  };
  const LABEL = { morning: 'Утро', workout: 'После тренировки', evening: 'Итог дня' };
  const WAIT = {
    morning: () => 'придёт, как только WHOOP посчитает сон',
    workout: () => 'после тренировки от 15 минут',
    evening: d => `вечером, в ${esc(d.evening_at || '21:00')}`,
  };

  const data = () => F.S.data && F.S.data.ext && F.S.data.ext.reviews;
  const find = id => ((data() || {}).items || []).find(x => String(x.id) === String(id));
  // первая строка разбора — превью в строке ленты (без Markdown)
  const preview = x => {
    // вывод (строка **жирным**) интереснее строки цифр над ним
    const rows = (x.text || '').split('\n').map(s => s.trim()).filter(Boolean);
    const ln = (rows.find(s => s.startsWith('**')) || rows[0] || '').replace(/\*\*/g, '').replace(/^(?:[-*•]|\d+[.)])\s+/, '');
    return x.kind === 'workout' ? `${x.title}${ln ? ' · ' + ln : ''}` : ln || x.title;
  };
  const title = x => x.kind === 'workout' ? 'После тренировки' : LABEL[x.kind] || 'Разбор';

  function row(x) {
    return `<button class="row tap rv-row${x.seen ? '' : ' new'}" data-a="rv-open" data-v="${x.id}" data-rv="${x.id}"
        aria-label="${esc(title(x))}, ${esc(x.time)}${x.seen ? '' : ', новый'}">
      <span class="ic">${IC[x.kind] || IC.morning}</span>
      <span class="main"><div class="title">${esc(title(x))}</div><div class="meta">${esc(preview(x))}</div></span>
      <span class="end rv-end"><span class="rv-t num">${esc(x.time)}</span><i class="rv-dot" aria-hidden="true"></i><span class="chev"></span></span></button>`;
  }
  const waiting = (kind, d) => `<div class="row rv-row rv-wait" aria-disabled="true"><span class="ic">${IC[kind]}</span>
      <span class="main"><div class="title">${LABEL[kind]}</div><div class="meta">${WAIT[kind](d)}</div></span></div>`;

  function list(d) {
    const its = d.items || [], has = k => its.some(x => x.kind === k);
    let h = '';
    if (!has('morning') && d.morning !== false) h += waiting('morning', d);
    h += its.filter(x => x.kind === 'morning').map(row).join('');
    h += its.filter(x => x.kind === 'workout').map(row).join('');
    if (!has('workout')) h += waiting('workout', d);
    h += its.filter(x => x.kind === 'evening').map(row).join('');
    if (!has('evening')) h += waiting('evening', d);
    return h;
  }
  const newCount = d => (d.items || []).filter(x => !x.seen).length;
  const capRight = d => { const n = newCount(d); return n ? `<span class="rv-new">${n === 1 ? 'новый' : `новых: ${n}`}</span>` : ''; };

  // ---------- карточка на главной (под кольцами) ----------
  F.slots.homeAfterDials.push((t, isToday) => {
    const d = data();
    if (!isToday || !d) return '';
    return `<div class="sec rise rv-sec">${cap('Разборы за сегодня', capRight(d))}<div class="list rv-list" data-rv-list>${list(d)}</div></div>`;
  });

  // ---------- лист с полным разбором ----------
  function open(id) {
    const x = find(id);
    if (!x) { F.toast('Разбор не найден — обнови приложение'); return; }
    F.haptic('select');
    const q = `Расскажи подробнее про сегодняшний разбор «${title(x)}»: что главное и с чего начать?`;
    const el = F.sheet(`<div class="rv-sheet"><div class="rv-k"><span class="rv-ic">${IC[x.kind] || ''}</span>${esc(title(x))} · <span class="num">${esc(x.time)}</span></div>
      <h2>${esc(x.kind === 'workout' ? x.title : x.title || title(x))}</h2>
      <div class="prose rv-prose">${F.md(x.text)}</div>
      <button class="btn rv-ask" data-a="ask-go" data-v="${esc(q)}">${F.I.coach}Спросить в чате</button></div>`);
    el.addEventListener('click', ev => { if (ev.target.closest('[data-a="ask-go"]')) el.close(); });
    if (!x.seen) seen(x);
  }
  async function seen(x) {
    x.seen = true;
    patch();
    try { await F.api('/api/reviews/seen', { id: x.id }); } catch (e) { /* не страшно: точка вернётся при обновлении */ }
  }
  // только точка в строке и счётчик в подписи — без перерисовки экрана
  function patch() {
    const d = data(); if (!d) return;
    document.querySelectorAll('[data-rv]').forEach(b => { const x = find(b.dataset.rv); if (x) b.classList.toggle('new', !x.seen); });
    document.querySelectorAll('.rv-sec .cap').forEach(c => {
      const r = c.querySelector('.cap-r'), html = capRight(d);
      if (r && html) F.morph(r, html); else if (r) r.remove(); else if (html) c.insertAdjacentHTML('beforeend', `<span class="cap-r">${html}</span>`);
    });
  }
  F.actions['rv-open'] = (el, v) => open(v);

  // «📱 Открыть» из сообщения бота: ?p=review-<id> — сразу лист этого разбора
  let opened = false;
  F.onData.push(() => {
    if (opened) return;
    opened = true;
    const m = (new URLSearchParams(location.search).get('p') || '').match(/^\/?review-(\d+)$/);
    if (!m) return;
    setTimeout(() => open(m[1]), 350);  // это его нажатие в Telegram — открыть можно
    // убрать ?p=review-… из адреса: перезагрузка или возврат в приложение лист сами больше не откроют (Глеб 03.11)
    try { const u = new URL(location.href); u.searchParams.delete('p'); history.replaceState(history.state, '', u.pathname + u.search + u.hash); } catch (e) { /* старый браузер */ }
  });
});
