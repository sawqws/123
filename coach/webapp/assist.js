// assist — тренер-помощник (Глеб 03.11: «как с тобой, Claude Code»): тренер сам записывает воду, еду, вес, привычку,
// цель на день. Сервер дописывает в ответ строку «✅ Записал: …», а здесь — чипы «Отменить» под последним ответом
// на 10 с (POST /api/ask/undo {id}). app.js зовёт F.onActs(done) после ответа и F.actChips(m) у каждого ответа тренера.
(window.FormaModules = window.FormaModules || []).push(F => {
  const LIVE = 10000;
  let live = null, timer = 0;  // {created, acts: [{undo, label}], until}

  const drop = () => { live = null; document.querySelectorAll('.act-chips').forEach(el => { el.classList.add('out'); setTimeout(() => el.remove(), 260); }); };

  F.onActs = done => {
    const acts = (done.actions || []).filter(a => a.ok && a.undo);
    const last = [...(done.history || [])].reverse().find(m => m.role === 'coach');
    clearTimeout(timer);
    live = acts.length && last ? { created: last.created, acts, until: Date.now() + LIVE } : null;
    if (live) timer = setTimeout(drop, LIVE);
  };

  F.actChips = m => {
    if (!live || m.created !== live.created || !live.acts.length) return '';
    const left = Math.max(0, live.until - Date.now());
    if (!left) return '';
    return `<div class="act-chips" style="--left:${left}ms">${live.acts.map(a =>
      `<span class="act-chip"><span class="act-l">${F.esc(a.label)}</span><button class="act-undo" data-a="act-undo" data-v="${F.esc(a.undo)}">Отменить</button></span>`).join('')}</div>`;
  };

  F.actions['act-undo'] = async (el, v) => {
    if (!live) return;
    el.disabled = true; F.haptic('tap');
    try {
      const r = await F.api('/api/ask/undo', { id: v });
      F.S.history = r.history;
      if (r.day && F.S.data && !F.S.dayData) F.S.data.today = r.day;
      live.acts = live.acts.filter(a => a.undo !== v);
      F.toast(`↩️ Отменил: ${r.label}`, 'ok');
      F.render(false);
    } catch (e) { el.disabled = false; F.fail(e); }
  };
});
