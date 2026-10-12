/* Главный — мини-приложение: боты, сервер, напоминания. Без сборки и библиотек. */
(() => {
  const tg = window.Telegram && window.Telegram.WebApp;
  const $ = (s, el = document) => el.querySelector(s);
  const view = $('#view');
  const S = { data: null, tab: 'bots', open: null, logs: null, logsBusy: false, busy: {}, updating: null, error: null, tabDrawn: null };

  const I = {
    bots: '<rect x="4" y="7" width="16" height="12" rx="3"/><path d="M12 7V4M9 12h.01M15 12h.01M9.5 15.5h5"/>',
    server: '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>',
    bell: '<path d="M6 8a6 6 0 1 1 12 0c0 7 3 8 3 8H3s3-1 3-8"/><path d="M10.3 20a2 2 0 0 0 3.4 0"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    alert: '<path d="M12 8v5M12 16.5h.01"/><circle cx="12" cy="12" r="9"/>',
    up: '<path d="M12 19V5M6 11l6-6 6 6"/>',
    restart: '<path d="M20 12a8 8 0 1 1-2.4-5.7L20 8.6"/><path d="M20 3.5v5.1h-5.1"/>',
    logs: '<path d="m5 8 4 4-4 4M12 17h7"/>',
    open: '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    chev: '<path d="m9 18 6-6-6-6"/>',
    moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>',
    card: '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="M3 10h18M7 15h4"/>',
    lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  };
  const icon = (n, cls = 'ic') => `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true">${I[n]}</svg>`;
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const plural = (n, a, b, c) => { const m = Math.abs(n) % 100, k = m % 10; return m > 10 && m < 20 ? c : k > 1 && k < 5 ? b : k === 1 ? a : c; };
  const fmt = (x, d = 1) => Number(x).toFixed(d).replace(/\.0+$/, '').replace('.', ',');
  const mem = mb => mb >= 1024 ? `${fmt(mb / 1024)}\u00a0ГБ` : `${Math.round(mb)}\u00a0МБ`;
  const NB = '\u00a0';  // число и единица не переносятся по отдельности
  const ago = s => { s = Math.max(0, s | 0); if (s < 3600) return `${Math.max(1, (s / 60) | 0)}${NB}мин`; if (s < 86400) return `${(s / 3600) | 0}${NB}ч`; const d = (s / 86400) | 0; return `${d}${NB}${plural(d, 'день', 'дня', 'дней')}`; };
  const hue = s => { let h = 0; for (const c of String(s)) h = (h * 31 + c.codePointAt(0)) % 360; return h; };
  const emoji = b => (b.label.slice(0, Math.max(0, b.label.length - b.title.length)).trim() || '🤖');

  // ---------- Telegram ----------
  const haptic = {
    tap: () => { try { tg.HapticFeedback.impactOccurred('light'); } catch (e) { /* вне Telegram */ } },
    pick: () => { try { tg.HapticFeedback.selectionChanged(); } catch (e) { /* вне Telegram */ } },
    ok: () => { try { tg.HapticFeedback.notificationOccurred('success'); } catch (e) { /* вне Telegram */ } },
    bad: () => { try { tg.HapticFeedback.notificationOccurred('error'); } catch (e) { /* вне Telegram */ } },
  };
  const confirmBox = text => new Promise(res => {
    if (tg && tg.showConfirm && tg.platform !== 'unknown') { try { return tg.showConfirm(text, ok => res(!!ok)); } catch (e) { /* старый клиент */ } }
    res(window.confirm(text));
  });
  function theme() {
    document.body.classList.toggle('dark', tg ? tg.colorScheme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches);
    try { tg.setHeaderColor('secondary_bg_color'); tg.setBackgroundColor('secondary_bg_color'); } catch (e) { /* старый клиент */ }
    try { tg.setBottomBarColor && tg.setBottomBarColor('secondary_bg_color'); } catch (e) { /* старый клиент */ }
  }

  // ---------- API ----------
  async function api(path, body) {
    const opt = { headers: { 'X-Init-Data': (tg && tg.initData) || '' } };
    if (body) { opt.method = 'POST'; opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
    const r = await fetch('/app/api/' + path, opt);
    const j = await r.json().catch(() => ({ error: 'Главный не ответил' }));
    if (!r.ok) { const e = new Error(j.error || 'ошибка'); e.status = r.status; throw e; }
    return j;
  }
  const act = (action, arg) => api('action', { action, arg });

  // ---------- живой статус ----------
  let liveTimer = 0;
  function live(text, state = 'run', hideIn = 0) {
    const el = $('#live');
    clearTimeout(liveTimer);
    if (!text) { el.classList.remove('on'); return; }
    const lead = state === 'run' ? '<div class="spin"></div>' : icon(state === 'ok' ? 'check' : 'alert', `ic ${state}`);
    el.innerHTML = `${lead}<div class="live-t">${esc(text)}</div>`;
    el.classList.add('on');
    if (hideIn) liveTimer = setTimeout(() => el.classList.remove('on'), hideIn);
  }

  // ---------- загрузка ----------
  async function load() {
    try {
      const d = await api('state');
      const was = S.data;
      S.data = d; S.error = null;
      track(was, d);
    } catch (e) {
      if (S.updating) { live('Главный перезапускается…'); return; }
      if (e.status === 401 || e.status === 403 || !S.data) { S.error = e; }
      else { live('Нет связи с Главным — повторю', 'bad', 3000); }
    }
    render();
  }
  function track(was, d) {
    const u = S.updating;
    if (u) {
      if (d.boot !== u.boot) { S.updating = null; haptic.ok(); live(u.self ? 'Главный перезапущен' : 'Обновил — все боты перезапущены', 'ok', 4000); return; }
      if (!u.self && d.live && d.live.kind === 'update' && d.live.done && d.live.at > u.at) {
        S.updating = null;
        d.live.ok ? haptic.ok() : haptic.bad();
        live(d.live.text, d.live.ok ? 'ok' : 'bad', 5000); return;
      }
      if (d.live && d.live.text && !d.live.done) live(d.live.text);
      return;
    }
    if (d.live && !d.live.done && d.live.text && d.now - d.live.at < 1800) live(d.live.text);
    else if (was && was.live && !was.live.done && d.live && d.live.done) live(d.live.text, d.live.ok ? 'ok' : 'bad', 4000);
  }
  let pollT = 0;
  function poll() {
    clearTimeout(pollT);
    pollT = setTimeout(async () => { if (!document.hidden) await load(); poll(); }, S.updating ? 1500 : 6000);
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { load(); poll(); } });

  // ---------- вкладки ----------
  const TABS = [['bots', 'Боты', 'bots'], ['server', 'Сервер', 'server'], ['rmd', 'Напоминания', 'bell']];
  function tabs() {
    const nav = $('#tabs');
    const down = S.data ? S.data.bots.filter(b => b.status && b.status !== 'online').length : 0;
    nav.classList.toggle('hidden', !!S.error);
    nav.innerHTML = TABS.map(([id, t, ic]) => `<button class="tab ${S.tab === id ? 'on' : ''}" data-tab="${id}">${icon(ic)}<span>${t}</span>${id === 'bots' && down ? `<i class="badge">${down}</i>` : ''}</button>`).join('');
  }
  $('#tabs').addEventListener('click', e => {
    const t = e.target.closest('[data-tab]');
    if (!t || t.dataset.tab === S.tab) return;
    haptic.pick(); S.tab = t.dataset.tab; window.scrollTo(0, 0); render();
  });

  function render() {
    tabs();
    if (S.error) return gate(S.error);
    if (!S.data) return skeleton();
    const html = S.tab === 'bots' ? botsView() : S.tab === 'server' ? serverView() : rmdView();
    const anim = S.tabDrawn !== S.tab;
    view.className = anim ? 'enter' : '';
    view.innerHTML = html;
    S.tabDrawn = S.tab;
    requestAnimationFrame(() => view.querySelectorAll('.bar i').forEach(b => { b.style.width = b.dataset.w; }));
    if (S.open) sheet(false);
  }

  function skeleton() {
    const row = '<div class="card bot"><div class="ava sk"></div><div class="bot-main"><div class="sk sk-line" style="width:46%"></div><div class="sk sk-line" style="width:72%"></div></div></div>';
    view.className = '';
    view.innerHTML = `<div class="head"><div class="sk" style="height:28px;width:40%;margin:3px 0 8px"></div><div class="sk sk-line" style="width:56%"></div></div>
      <div class="card"><div class="hero"><div class="hero-badge sk"></div><div class="hero-text"><div class="sk sk-line" style="width:60%"></div><div class="sk sk-line" style="width:40%"></div></div></div><div class="sk" style="height:44px;margin-top:16px;border-radius:12px"></div></div>
      <div class="section"><span class="sk" style="width:60px;height:12px"></span></div><div class="stack">${row.repeat(5)}</div>`;
  }

  function gate(e) {
    const outside = !(tg && tg.initData);
    view.className = 'enter';
    view.innerHTML = `<div class="gate"><div><div class="big">${outside ? '📱' : '🔒'}</div>
      <b>${outside ? 'Открой в Telegram' : 'Нет доступа'}</b>
      <p>${outside ? 'Приложение работает внутри бота «Главный» — кнопка «Приложение» слева от поля ввода.' : esc(e.message || 'Это личный бот')}</p></div></div>`;
  }

  // ---------- Боты ----------
  function statusOf(b) {
    if (!b.status) return { cls: 's-none', text: 'не поставлен', short: 'нет' };
    if (b.status === 'online') return { cls: 's-on', text: 'работает', short: ago(b.uptime) };
    return { cls: 's-bad', text: b.status === 'stopped' ? 'остановлен' : 'упал', short: b.status === 'stopped' ? 'стоп' : 'упал' };
  }
  function botsView() {
    const d = S.data, bots = d.bots;
    const run = bots.filter(b => b.status), on = run.filter(b => b.status === 'online'), down = run.length - on.length;
    const used = run.reduce((s, b) => s + (b.mem || 0), 0);
    const hero = !d.pm2
      ? { ok: false, t: 'pm2 не отвечает', s: 'Состояние ботов неизвестно — попробую ещё раз' }
      : down ? { ok: false, t: `${down} ${plural(down, 'бот не работает', 'бота не работают', 'ботов не работают')}`, s: `Работают ${on.length} из ${run.length}` }
        : { ok: true, t: 'Все боты работают', s: `${on.length} ${plural(on.length, 'бот', 'бота', 'ботов')} · ${mem(used)} памяти` };
    const upd = !!S.updating || (d.live && d.live.kind === 'update' && !d.live.done && d.now - d.live.at < 1800);
    const sub = `${bots.length} ${plural(bots.length, 'бот', 'бота', 'ботов')} · сервер занят на ${d.server.mem_total ? Math.round((1 - d.server.mem_free / d.server.mem_total) * 100) : '—'}%`;
    return `<header class="head"><h1>Главный</h1><p>${esc(sub)}</p></header>
      <div class="card"><div class="hero"><div class="hero-badge ${hero.ok ? 'ok' : 'bad'}">${icon(hero.ok ? 'check' : 'alert')}</div>
        <div class="hero-text"><b>${esc(hero.t)}</b><span>${d.paid ? payLine(d.paid) : 'Срок оплаты не указан'}</span></div></div>
        <div class="hero-actions"><button class="btn tinted" data-act="update" ${upd ? 'disabled' : ''}>${upd ? '<div class="spin"></div>Обновляю…' : icon('up') + 'Обновить всех'}</button></div></div>
      <div class="section"><span>Боты</span><span class="num">${run.length} из ${bots.length}</span></div>
      <div class="stack">${bots.map(botCard).join('')}</div>`;
  }
  function botCard(b) {
    const st = statusOf(b);
    const bits = [];
    if (b.username) bits.push('@' + b.username);
    if (b.status === 'online') bits.push(mem(b.mem));
    else if (b.status) bits.push(b.status);
    if (b.status && b.restarts) bits.push(`↻ ${b.restarts}`);
    if (!b.status) bits.push(b.about || 'не поставлен');
    return `<button class="card bot ${b.status ? '' : 'off'} ${st.cls}" data-bot="${esc(b.name)}">
      <div class="ava" style="--h:${hue(b.name)}">${esc(emoji(b))}</div>
      <div class="bot-main"><div class="bot-top"><span class="bot-name ell">${esc(b.title)}</span>
        <span class="pill ${st.cls}"><i class="dot"></i><span class="pill-t num">${esc(st.short)}</span></span></div>
        <div class="bot-sub ell">${esc(bits.join(' · '))}</div></div>
      ${icon('chev', 'ic chev')}</button>`;
  }

  // ---------- детали бота ----------
  function sheet(animate = true) {
    const b = S.data && S.data.bots.find(x => x.name === S.open);
    const wrap = $('#sheet');
    if (!b) return closeSheet();
    const st = statusOf(b);
    const busy = S.busy[b.name];
    const logs = S.logs && S.logs.name === b.name ? S.logs.text : null;
    $('#sheet-body').innerHTML = `
      <div class="d-head"><div class="ava big" style="--h:${hue(b.name)}">${esc(emoji(b))}</div>
        <div class="d-title"><h2>${esc(b.title)}</h2>
          ${b.username ? `<a href="#" data-act="open" class="ell">@${esc(b.username)}</a>` : ''}
          <span class="pill solid ${st.cls}"><i class="dot"></i>${esc(st.text)}${b.status === 'online' ? ' · ' + ago(b.uptime) : ''}</span></div></div>
      ${b.about ? `<p class="about">${esc(b.about)}</p>` : ''}
      ${b.status ? `<div class="stats">
        ${b.status === 'online' ? `<div class="stat"><span>Работает</span><b class="num">${esc(ago(b.uptime))}</b></div>`
          : `<div class="stat"><span>Статус</span><b class="ell" style="color:var(--danger)">${esc(b.status)}</b></div>`}
        <div class="stat"><span>Память</span><b class="num">${b.status === 'online' ? esc(mem(b.mem)) : '—'}</b></div>
        <div class="stat"><span>Процессор</span><b class="num">${esc(fmt(b.cpu))}<small>%</small></b></div>
        <div class="stat"><span>Перезапусков</span><b class="num">${b.restarts}</b></div></div>` : '<p class="about">Бот не поставлен на сервер. Поставить — в чате Главного: «⋯ Ещё» → «➕ Поставить».</p>'}
      <div class="d-actions">
        ${b.status ? `<button class="btn primary" data-act="restart" ${busy ? 'disabled' : ''}>${busy === 'restart' ? '<div class="spin" style="border-top-color:currentColor"></div>Перезапускаю…' : icon('restart') + 'Перезапустить'}</button>` : ''}
        <div class="row2">
          ${b.status ? `<button class="btn tinted" data-act="logs" ${S.logsBusy ? 'disabled' : ''}>${icon('logs')}Логи</button>` : ''}
          ${b.username ? `<button class="btn tinted" data-act="open">${icon('open')}Открыть</button>` : ''}
        </div></div>
      ${logs != null || S.logsBusy ? `<div class="logs"><div class="logs-head"><span>Последние строки</span>${logs != null ? `<button data-act="logs">${icon('restart', 'ic')}Обновить</button>` : ''}</div>
        ${logs != null ? `<pre id="logpre">${esc(logs)}</pre>` : '<div class="card"><div class="sk sk-line"></div><div class="sk sk-line" style="width:80%"></div><div class="sk sk-line" style="width:64%"></div></div>'}</div>` : ''}`;
    const r2 = $('.d-actions .row2');
    if (!r2.children.length) r2.remove();
    const pre = $('#logpre');
    if (pre && S.logs && S.logs.fresh) { pre.scrollTop = pre.scrollHeight; S.logs.fresh = false; }
    if (animate) {
      wrap.classList.add('open'); wrap.setAttribute('aria-hidden', 'false'); document.body.classList.add('locked');
      $('#sheet-body').scrollTop = 0;
      try { tg.BackButton.show(); } catch (e) { /* вне Telegram */ }
    }
  }
  function openSheet(name) { S.open = name; S.logs = null; S.logsBusy = false; haptic.tap(); sheet(true); }
  function closeSheet() {
    S.open = null; S.logs = null;
    const wrap = $('#sheet');
    wrap.classList.remove('open'); wrap.setAttribute('aria-hidden', 'true'); document.body.classList.remove('locked');
    try { tg.BackButton.hide(); } catch (e) { /* вне Telegram */ }
  }
  $('.sheet-backdrop').addEventListener('click', closeSheet);
  try { tg.BackButton.onClick(closeSheet); } catch (e) { /* вне Telegram */ }

  // ---------- Сервер ----------
  function level(p) { return p >= 0.9 ? 'var(--danger)' : p >= 0.7 ? 'var(--orange)' : 'var(--green)'; }
  function metric(name, used, total, right, sub) {
    const p = total ? Math.max(0, Math.min(1, used / total)) : 0;
    return `<div class="metric"><div class="m-top"><b>${name}</b><span class="num">${right}</span></div>
      <div class="bar"><i style="--c:${level(p)}" data-w="${(p * 100).toFixed(1)}%"></i></div><div class="m-sub">${sub}</div></div>`;
  }
  function payLine(p) {
    return p.left > 0 ? `Оплачен до ${p.text} · ${p.left}${NB}${plural(p.left, 'день', 'дня', 'дней')}` : p.left === 0 ? 'Оплата сервера — сегодня!' : 'Срок оплаты прошёл!';
  }
  function serverView() {
    const s = S.data.server, p = S.data.paid;
    const rows = [];
    if (s.mem_total) {
      const used = s.mem_total - s.mem_free;
      rows.push(metric('Память', used, s.mem_total, `${Math.round(used / s.mem_total * 100)}%`, `Занято ${mem(used)} из ${mem(s.mem_total)} · свободно ${mem(s.mem_free)}`));
    }
    if (s.disk_total) {
      const used = s.disk_total - s.disk_free;
      rows.push(metric('Диск', used, s.disk_total, `${Math.round(used / s.disk_total * 100)}%`, `Занято ${fmt(used)} из ${fmt(s.disk_total)} ГБ · свободно ${fmt(s.disk_free)} ГБ`));
    }
    if (s.load != null) {
      const l = parseFloat(s.load) || 0;
      rows.push(metric('Нагрузка', l, s.cpus || 1, fmt(l, 2), `${s.cpus} ${plural(s.cpus, 'ядро', 'ядра', 'ядер')} · без перезагрузки ${ago(s.uptime || 0)}`));
    }
    let pay;
    if (p) {
      const c = p.left <= 1 ? 'var(--danger)' : p.left <= 7 ? 'var(--orange)' : 'var(--green)';
      const note = p.left < 0 ? 'Срок прошёл — продли и пришли новый /paid в чат' : p.left <= 7 ? 'Скоро срок — продли, иначе боты выключатся' : 'Напомню за 7, 3 и 1 день';
      pay = `<div class="card ${p.left <= 7 ? 'warn' : ''}" style="--c:${c}"><div class="pay"><div class="pay-ic">${icon('card')}</div>
        <div class="pay-text"><b>Оплачен до ${esc(p.text)}</b><span>${esc(note)}</span></div>
        <div class="days num">${p.left < 0 ? '!' : p.left}<small>${p.left < 0 ? 'просрочен' : plural(p.left, 'день', 'дня', 'дней')}</small></div></div></div>`;
    } else {
      pay = `<div class="card"><div class="pay"><div class="pay-ic" style="--c:var(--hint)">${icon('card')}</div>
        <div class="pay-text"><b>Срок оплаты не указан</b><span>Пришли в чат <code>/paid 07.11</code> — напомню заранее</span></div></div></div>`;
    }
    return `<header class="head"><h1>Сервер</h1><p>Обновляется само, пока приложение открыто</p></header>
      <div class="card">${rows.join('') || '<div class="m-sub">Нет данных о сервере</div>'}</div>
      <div class="section"><span>Оплата</span></div>${pay}`;
  }

  // ---------- Напоминания ----------
  function rmdView() {
    const d = S.data, rs = d.reminders;
    const dg = d.digest === 'off'
      ? { t: 'Отбивка выключена', s: 'Включить: /digest 21:00 в чате' }
      : { t: `Вечерняя отбивка в ${d.digest}`, s: 'Боты, сервер, оплата и планы на завтра. Время — /digest в чате' };
    const list = rs.length ? `<div class="stack">${rs.map(r => `<div class="card rmd" data-rid="${esc(r.id)}">
        <div class="rmd-time num">${esc(r.time)}</div>
        <div class="rmd-main"><div class="rmd-text">${esc(r.text)}</div><div class="rmd-when">${esc(r.when.replace(/ в \d\d:\d\d$/, ""))}</div></div>
        <button class="icon-btn" data-del="${esc(r.id)}" aria-label="Удалить">${icon('trash')}</button></div>`).join('')}</div>`
      : `<div class="card empty"><div class="big">⏰</div><b>Напоминаний нет</b><p>Задай в чате Главного:</p>
        <div class="examples"><div><code>/remind 09:00 текст</code> — каждый день</div><div><code>/remind пн-пт 18:30 текст</code> — по дням</div><div><code>/remind 25.10 10:00 текст</code> — один раз</div></div></div>`;
    return `<header class="head"><h1>Напоминания</h1><p>${rs.length ? `${rs.length} ${plural(rs.length, 'напоминание', 'напоминания', 'напоминаний')}` : 'Главный напишет в чат в нужное время'}</p></header>
      <div class="card"><div class="pay"><div class="pay-ic" style="--c:${d.digest === 'off' ? 'var(--hint)' : '#af52de'}">${icon('moon')}</div>
        <div class="pay-text"><b>${esc(dg.t)}</b><span>${esc(dg.s)}</span></div></div></div>
      <div class="section"><span>Список</span></div>${list}`;
  }

  // ---------- действия ----------
  view.addEventListener('click', async e => {
    const bot = e.target.closest('[data-bot]');
    if (bot) return openSheet(bot.dataset.bot);
    const del = e.target.closest('[data-del]');
    if (del) return delReminder(del.dataset.del, del.closest('.rmd'));
    const a = e.target.closest('[data-act="update"]');
    if (a) return updateAll();
  });
  $('#sheet-body').addEventListener('click', async e => {
    const a = e.target.closest('[data-act]');
    if (!a) return;
    e.preventDefault();
    const b = S.data.bots.find(x => x.name === S.open);
    if (!b) return;
    if (a.dataset.act === 'open') { haptic.tap(); const url = `https://t.me/${b.username}`; try { tg.openTelegramLink(url); } catch (er) { window.open(url, '_blank'); } return; }
    if (a.dataset.act === 'logs') return loadLogs(b);
    if (a.dataset.act === 'restart') return restart(b);
  });

  async function loadLogs(b) {
    haptic.tap(); S.logsBusy = true; sheet(false);
    try { const r = await act('logs', b.name); S.logs = { name: b.name, text: r.text, fresh: true }; }
    catch (er) { haptic.bad(); live(er.message, 'bad', 3500); }
    S.logsBusy = false;
    if (S.open === b.name) sheet(false);
  }
  async function restart(b) {
    const self = b.self;
    if (!(await confirmBox(self ? 'Перезапустить Главного? Приложение переподключится через несколько секунд.' : `Перезапустить «${b.title}»?`))) return;
    haptic.tap(); S.busy[b.name] = 'restart'; sheet(false);
    live(`Перезапускаю ${b.title}…`);
    try {
      const r = await act('restart', b.name);
      if (self) { delete S.busy[b.name]; S.updating = { boot: S.data.boot, at: r.at || 0, self: true }; poll(); return; }
      r.ok ? haptic.ok() : haptic.bad();
      live(r.text, r.ok ? 'ok' : 'bad', 4000);
    } catch (er) { haptic.bad(); live(er.message, 'bad', 4000); }
    delete S.busy[b.name];
    await load();
  }
  async function updateAll() {
    if (!(await confirmBox('Скачать свежий код из main и перезапустить всех ботов? Главный тоже перезапустится.'))) return;
    haptic.tap();
    try {
      const r = await act('update');
      S.updating = { boot: S.data.boot, at: r.at || 0 };
      live('Скачиваю свежий код из main…'); render(); poll();
    } catch (er) { haptic.bad(); live(er.message, 'bad', 4000); }
  }
  async function delReminder(id, row) {
    const r = S.data.reminders.find(x => x.id === id);
    if (!(await confirmBox(`Удалить напоминание «${r ? r.text : ''}»?`))) return;
    try {
      const res = await act('reminder_delete', id);
      haptic.ok();
      if (row) { row.classList.add('gone'); await new Promise(ok => setTimeout(ok, 260)); }
      S.data.reminders = S.data.reminders.filter(x => x.id !== id);
      live(res.text, 'ok', 2200);
      render();
    } catch (er) { haptic.bad(); live(er.message, 'bad', 3500); }
  }

  // ---------- старт ----------
  if (tg) {
    try { tg.ready(); tg.expand(); } catch (e) { /* старый клиент */ }
    try { tg.disableVerticalSwipes && tg.disableVerticalSwipes(); } catch (e) { /* старый клиент */ }
    try { tg.onEvent('themeChanged', () => { theme(); render(); }); } catch (e) { /* старый клиент */ }
  }
  theme();
  render();
  if (!(tg && tg.initData)) { S.error = new Error('нет initData'); render(); return; }
  load().then(poll);
})();
