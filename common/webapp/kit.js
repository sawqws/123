// Общие помощники мини-приложений (подключать до app.js): тема, запросы, отклик, уведомления, лист снизу.
//   <script src="kit.js?v={{v}}"></script>  — затем в app.js: const { tg, $, esc, api, toast, haptic, sheet } = Kit;
// Kit.api(путь, тело) — GET без тела, POST с телом; подпись Telegram в заголовке X-Init-Data.
(function () {
  const tg = window.Telegram && window.Telegram.WebApp;
  const root = document.documentElement;

  function scheme() {
    const dark = tg && tg.colorScheme ? tg.colorScheme === 'dark'
      : window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches;
    root.dataset.scheme = dark ? 'dark' : 'light';
    // шапка и фон Telegram — в цвет страницы, чтобы не было полосы сверху
    const bg = getComputedStyle(root).getPropertyValue('--tg-theme-secondary-bg-color').trim();
    if (tg && bg) {
      try { tg.setHeaderColor(bg); tg.setBackgroundColor(bg); if (tg.setBottomBarColor) tg.setBottomBarColor(bg); } catch (e) { /* старый клиент */ }
    }
  }
  scheme();
  if (tg) {
    tg.ready();
    tg.expand();
    try { if (tg.disableVerticalSwipes) tg.disableVerticalSwipes(); } catch (e) { /* старый клиент */ }
    tg.onEvent && tg.onEvent('themeChanged', scheme);
  }

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function haptic(kind) {
    const h = tg && tg.HapticFeedback;
    if (!h) return;
    try {
      if (kind === 'tap' || !kind) h.impactOccurred('light');
      else if (kind === 'select') h.selectionChanged();
      else h.notificationOccurred(kind);
    } catch (e) { /* нет отклика */ }
  }

  async function api(path, body, opts) {
    const headers = { 'X-Init-Data': (tg && tg.initData) || '' };
    const init = { method: body ? 'POST' : 'GET', headers };
    if (body instanceof FormData) init.body = body;
    else if (body) { headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
    Object.assign(init, opts || {});
    const r = await fetch(path, init);
    let data = {};
    try { data = await r.json(); } catch (e) { /* пустой ответ */ }
    if (!r.ok) {
      const err = new Error(data.error || (r.status === 401 ? 'Открой приложение через Telegram' : 'Ошибка ' + r.status));
      err.status = r.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  let toastEl;
  let toastTimer;
  function toast(text, kind) {  // kind: true | 'err' | 'ok'
    if (!toastEl) {
      toastEl = $('toast') || document.body.appendChild(Object.assign(document.createElement('div'), { id: 'toast' }));
      toastEl.setAttribute('role', 'status');
    }
    const k = kind === true ? 'err' : (kind || '');
    toastEl.textContent = text;
    toastEl.className = 'toast ' + k;
    void toastEl.offsetWidth;
    toastEl.className = 'toast show ' + k;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastEl.className = 'toast ' + k; }, 2800);
  }

  // Лист снизу: Kit.sheet('<h2>…</h2>…') -> элемент; закрывается тапом по фону, кнопкой «Назад» Telegram или .close()
  function sheet(html, onClose) {
    const back = document.createElement('div');
    back.className = 'sheet-backdrop';
    const el = document.createElement('div');
    el.className = 'sheet';
    el.innerHTML = html;
    document.body.append(back, el);
    requestAnimationFrame(() => { back.classList.add('open'); el.classList.add('open'); });
    let closed = false;
    const close = () => {
      if (closed) return;
      closed = true;
      back.classList.remove('open');
      el.classList.remove('open');
      if (tg && tg.BackButton) { tg.BackButton.offClick(close); tg.BackButton.hide(); }
      setTimeout(() => { back.remove(); el.remove(); }, 320);
      if (onClose) onClose();
    };
    back.addEventListener('click', close);
    if (tg && tg.BackButton) { tg.BackButton.onClick(close); tg.BackButton.show(); }
    el.close = close;
    return el;
  }

  // Кнопка «занята»: await Kit.busy(btn, async () => …)
  async function busy(btn, fn) {
    if (!btn) return fn();
    btn.classList.add('busy');
    btn.disabled = true;
    try { return await fn(); } finally { btn.classList.remove('busy'); btn.disabled = false; }
  }

  // Нижние вкладки: Kit.tabbar(el, [{id, icon, label}], активная, onChange)
  function tabbar(el, items, active, onChange) {
    el.classList.add('tabbar');
    document.body.classList.add('has-tabbar');
    const draw = (cur) => {
      el.innerHTML = items.map((t) => `<button data-tab="${esc(t.id)}" class="${t.id === cur ? 'on' : ''}">` +
        `<span class="i">${t.icon}</span><span>${esc(t.label)}</span></button>`).join('');
    };
    draw(active);
    el.onclick = (ev) => {
      const b = ev.target.closest('[data-tab]');
      if (!b) return;
      haptic('select');
      draw(b.dataset.tab);
      onChange(b.dataset.tab);
    };
    return { set: draw };
  }

  window.Kit = { tg, $, esc, api, toast, haptic, sheet, busy, tabbar, scheme };
})();
