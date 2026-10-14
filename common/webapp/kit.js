// Общие помощники мини-приложений (подключать до app.js): тема, запросы, отклик, уведомления, лист снизу.
//   <script src="kit.js?v={{v}}"></script>  — затем в app.js: const { tg, $, esc, api, toast, haptic, sheet } = Kit;
// Kit.api(путь, тело) — GET без тела, POST с телом; подпись Telegram в заголовке X-Init-Data.
(function () {
  const tg = window.Telegram && window.Telegram.WebApp;
  const root = document.documentElement;

  function scheme() {
    // вид «пастель» всегда светлый, тема Telegram на него не влияет
    root.dataset.scheme = 'light';
    // шапка и фон Telegram — в цвет страницы, чтобы не было полосы сверху
    const bg = getComputedStyle(root).getPropertyValue('--bg').trim();
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
    let r;
    try {
      r = await fetch(path, init);
    } catch (e) {  // обрыв сети: вместо «Failed to fetch» — по-русски
      const err = new Error('Нет связи с сервером — проверь интернет и попробуй ещё раз');
      err.status = 0;
      throw err;
    }
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
  let lastClose = 0;
  function sheet(html, onClose) {
    const back = document.createElement('div');
    back.className = 'sheet-backdrop';
    const el = document.createElement('div');
    el.className = 'sheet';
    el.innerHTML = html;
    const x = document.createElement('button');  // явный выход: на iPad кнопки «Назад» и тапа по фону мало
    x.type = 'button'; x.className = 'sheet-x'; x.setAttribute('aria-label', 'Закрыть'); x.textContent = '✕';
    el.prepend(x);
    document.body.append(back, el);
    requestAnimationFrame(() => { back.classList.add('open'); el.classList.add('open'); });
    let closed = false;
    const close = () => {
      if (closed) return;
      closed = true;
      lastClose = Date.now();
      back.classList.remove('open');
      el.classList.remove('open');
      document.removeEventListener('keydown', esc_);
      if (tg && tg.BackButton) { tg.BackButton.offClick(close); tg.BackButton.hide(); }
      setTimeout(() => { back.remove(); el.remove(); }, 320);
      if (onClose) onClose();
    };
    back.addEventListener('click', close);
    x.addEventListener('click', close);
    const esc_ = (ev) => { if (ev.key === 'Escape') close(); };
    document.addEventListener('keydown', esc_);
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

  // Картинка -> base64 JPEG (без «data:»), длинная сторона ≤ max: скриншот влезает в запрос к серверу бота
  function shrink(file, max) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      const rd = new FileReader();  // data:, а не blob: — так пускает CSP приложения Секретаря (img-src data:)
      rd.onload = () => { img.src = rd.result; };
      rd.onerror = () => reject(new Error('Картинка не открылась'));
      img.onload = () => {
        const k = Math.min(1, (max || 1600) / Math.max(img.naturalWidth, img.naturalHeight));
        const c = document.createElement('canvas');
        c.width = Math.max(1, Math.round(img.naturalWidth * k));
        c.height = Math.max(1, Math.round(img.naturalHeight * k));
        c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
        resolve(c.toDataURL('image/jpeg', 0.82).split(',')[1]);
      };
      img.onerror = () => reject(new Error('Картинка не открылась'));
      rd.readAsDataURL(file);
    });
  }

  // 🛠️ Поддержка и пожелания: Kit.support('/api/support') — лист с полем и скриншотом,
  // «Отправить» -> POST {text, photo?: base64} (+ bot, если даны цели: opts.targets = [{id, name}]);
  // opts.api(путь, тело) — свой запрос вместо Kit.api (общий для страницы — Kit.supportOpts = {api}).
  // Строка для своих экранов — Kit.supportRow(путь[, подпись]): клик по [data-support] открывает лист сам.
  const SUP_TEXT = 'Напиши, что сломалось или чего не хватает — починю/добавлю.';
  function support(apiPath, opts) {
    opts = Object.assign({}, window.Kit && window.Kit.supportOpts, opts);  // supportOpts: {api} — своя авторизация
    const targets = opts.targets || [];
    let photo = null;
    let bot = opts.target || (targets[0] && targets[0].id) || '';
    const el = sheet(`<h2>🛠️ Поддержка и пожелания</h2>
      <p class="hint sup-lead">${esc(opts.hint || SUP_TEXT)}</p>
      ${targets.length ? `<div class="chips sup-targets">${targets.map((t) =>
        `<button class="chip${t.id === bot ? ' on' : ''}" data-bot="${esc(t.id)}">${esc(t.name)}</button>`).join('')}</div>` : ''}
      <textarea class="sup-text" rows="5" maxlength="4000"
        placeholder="${esc(opts.placeholder || 'Например: «кнопка Неделя ничего не показывает» или «добавь итог за месяц»')}"></textarea>
      <div class="sup-shot">
        <label class="btn sm sup-pick">📎 Скриншот<input type="file" accept="image/*" hidden></label>
        <span class="sup-prev"></span>
      </div>
      <button class="btn primary sup-send">Отправить</button>`);
    const q = (s) => el.querySelector(s);
    const prev = q('.sup-prev');
    const drawPrev = () => {
      prev.innerHTML = photo ? `<img alt="" src="data:image/jpeg;base64,${photo}"><button class="sup-x" aria-label="Убрать">✕</button>` : '';
    };
    el.addEventListener('click', (ev) => {
      const chip = ev.target.closest('[data-bot]');
      if (chip) {
        bot = chip.dataset.bot;
        el.querySelectorAll('[data-bot]').forEach((b) => b.classList.toggle('on', b === chip));
        haptic('select');
      }
      if (ev.target.closest('.sup-x')) { photo = null; drawPrev(); }
    });
    q('input[type=file]').addEventListener('change', async (ev) => {
      const f = ev.target.files && ev.target.files[0];
      ev.target.value = '';
      if (!f) return;
      try { photo = await shrink(f, 1600); drawPrev(); } catch (e) { toast(e.message, true); }
    });
    const send = q('.sup-send');
    send.addEventListener('click', () => busy(send, async () => {
      const text = q('.sup-text').value.trim();
      if (!text) { toast('Напиши, что поправить', true); q('.sup-text').focus(); return; }
      const body = { text };
      if (photo) body.photo = photo;
      if (targets.length) body.bot = bot;
      try {
        await (opts.api || api)(apiPath, body);
        haptic('success');
        el.close();
        toast('Принял — работаю, ответ придёт в чат', 'ok');
      } catch (e) {
        haptic('error');
        toast(e.message, true);
      }
    }));
    return el;
  }
  function supportRow(apiPath, sub) {
    return `<div class="list sup-list"><button class="row tap" data-support="${esc(apiPath)}">` +
      '<span class="ic">🛠️</span><span class="main"><div class="title">Поддержка и пожелания</div>' +
      `<div class="meta">${esc(sub || 'Ошибка или идея — починю/добавлю')}</div></span><span class="end chev"></span></button></div>`;
  }
  document.addEventListener('click', (ev) => {
    const b = ev.target.closest && ev.target.closest('[data-support]');
    if (!b || b === document.body || !b.dataset.support) return;  // <body data-support> — адрес для плавающей кнопки, а не кнопка
    ev.preventDefault();
    if (Date.now() - lastClose < 700 || document.querySelector('.sup-text')) return;  // «призрачный» тап после закрытия / лист уже открыт
    haptic('tap');
    document.querySelectorAll('.sheet').forEach((x) => { if (x.close) x.close(); });  // лист настроек и т. п. — закрыть
    const o = window.Kit && window.Kit.supportOpts;
    if (o && o.open) return o.open();  // у приложения свой лист (Главный — с выбором бота)
    support(b.dataset.support);
  });
  // Плавающая кнопка «🛠️ Поддержка» на каждом экране: <body data-support="/api/support">.
  // Стоит над нижними вкладками, не зависит от того, что сейчас нарисовано.
  function supportFab() {
    const path = document.body.dataset.support;
    if (!path || document.querySelector('.sup-fab')) return;
    const b = document.createElement('button');
    b.className = 'sup-fab';
    b.type = 'button';
    b.dataset.support = path;
    b.setAttribute('aria-label', 'Поддержка и пожелания');
    b.innerHTML = '<span class="i">🛠️</span><span class="t">Поддержка</span>';
    document.body.appendChild(b);
    awayOnScroll(b);
  }
  // плавающая кнопка не мешает читать: при прокрутке вниз уезжает, при прокрутке вверх и у верха — возвращается,
  // в самом низу (там своя строка «Поддержка») — прячется
  function awayOnScroll(el) {
    let lastY = scrollY;
    let ticking = false;
    addEventListener('scroll', () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => {
        // срабатывает только после заметной прокрутки (вниз 48 px, вверх 24 px), а не от каждого движения пальца
        const y = scrollY;
        const bottom = innerHeight + y >= document.documentElement.scrollHeight - 90;
        if (y < 60) { el.classList.remove('away'); lastY = y; }
        else if (bottom) el.classList.add('away');
        else if (y - lastY > 48) { el.classList.add('away'); lastY = y; }
        else if (lastY - y > 24) { el.classList.remove('away'); lastY = y; }
        else if ((y > lastY) === el.classList.contains('away')) lastY = y;  // движение в ту же сторону — копим от новой точки
        ticking = false;
      });
    }, { passive: true });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', supportFab); else supportFab();

  window.Kit = { tg, $, esc, api, toast, haptic, sheet, busy, tabbar, scheme, support, supportRow, supportFab, shrink };
})();
