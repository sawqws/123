/* Зеркало платформы (bot/mirror.py): этот файл идёт первым в <head>, до бандла платформы.
   Глушит отправку аналитики и отчётов об ошибках (Яндекс-метрика, Sentry): иначе платформа получала бы адрес зеркала.
   Sentry берёт «чистый» fetch из пустого iframe — поэтому патчим и окна iframe. */
(() => {
  const SRC = (typeof document !== 'undefined' && document.currentScript && document.currentScript.src) || '';  // …/guard.js?v=<версия> — та же у overlay
  const BLOCK = /(^|\.)(mc\.yandex\.(ru|com)|yandex\.(ru|com)\/(metrika|watch)|sentry\.)/i;
  const bad = u => {
    try { const x = new URL(String(u), location.href); return x.host !== location.host && BLOCK.test(x.host + x.pathname); } catch (e) { return false; }
  };
  const patch = w => {
    if (!w || w.__hdpxGuard) return;
    try {
      w.__hdpxGuard = true;
      const f = w.fetch;
      if (f) {
        w.fetch = function (input, init) {
          const u = input && input.url ? input.url : input;
          if (bad(u)) return Promise.resolve(new w.Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }));
          return f.apply(this, arguments);
        };
      }
      const X = w.XMLHttpRequest && w.XMLHttpRequest.prototype;
      if (X) {
        const open = X.open;
        X.open = function (m, u) { if (bad(u)) arguments[1] = 'data:application/json,{}'; return open.apply(this, arguments); };
      }
      const n = w.navigator;
      if (n && n.sendBeacon) { const b = n.sendBeacon.bind(n); n.sendBeacon = (u, d) => (bad(u) ? true : b(u, d)); }
      w.ym = function () {};  // если счётчик всё же вызовут
    } catch (e) { /* чужое окно — не трогаем */ }
  };
  patch(window);
  // Бренд: платформа включает «HDP» только на *.horodigital.ru (Bm = /horodigital.ru$/.test(location.host)),
  // иначе — чужой бренд «Perlem» (заголовок вкладки, значок, поддержка). Для этой одной проверки адрес — как у оригинала.
  const test = RegExp.prototype.test;
  RegExp.prototype.test = function (s) {
    if (this.source === 'horodigital.ru$' && s === location.host) return true;
    return test.apply(this, arguments);
  };
  // Тёмная тема по умолчанию (Глеб 20.10): своя тема платформы (hdp-theme-storage), включаем один раз до её бандла —
  // дальше как выберет он сам (переключатель платформы на главной или «Тёмная тема» в Настройках панели бота).
  try {
    if (!localStorage.getItem('hdpx.darkInit')) {
      let t; try { t = JSON.parse(localStorage.getItem('hdp-theme-storage')) || {}; } catch (e) { t = {}; }
      localStorage.setItem('hdp-theme-storage', JSON.stringify({ ...t, state: { ...(t.state || {}), darkThemeEnabled: true }, version: t.version || 0 }));
      localStorage.setItem('hdpx.darkInit', '1');
    }
  } catch (e) { /* без хранилища — как у платформы */ }
  // «⏻ Выключить всё» (cookie hdp_plain): кнопок бота на странице нет, кроме одной — ⏻ «включить» на месте
  // кнопки выключения (над вкладкой 🤖 у правого края). Одно нажатие (или Alt+B) — /_hdp/on, бот снова на странице.
  // Её можно убрать (Глеб 20.10): долгое нажатие или свайп вправо — уезжает за край; тройной тап по странице — обратно.
  // Выключение — тоже без перезагрузки (overlay.js plainOff → window.__hdpxPlain): режим можно включить в любой момент.
  if (typeof document !== 'undefined') {
    const reload = () => { location.href = '/_hdp/on?to=' + encodeURIComponent(location.pathname + location.search); };
    const V = (/[?&]v=([^&]+)/.exec(SRC) || [])[1] || '0';
    // Включить (Глеб 20.10: «сразу, без перезагрузки 2–3 с»): снимаем cookie; кнопки бота уже в странице — просто
    // показываем (__hdpxResume), иначе подгружаем их сюда — как их вставил бы сервер; не вышло — перезагрузка.
    let plainMode = false, tick = 0;
    const on = () => {
      if (!plainMode) return;
      plainMode = false;
      clearInterval(tick);
      document.cookie = 'hdp_plain=; Path=/; Max-Age=0; Secure; SameSite=Lax';
      fetch('/_hdp/on', { redirect: 'manual', credentials: 'same-origin' }).catch(() => {});  // и на сервере
      const b = document.getElementById('hdpx-on');
      if (b) { b.style.opacity = '0'; b.style.transform = 'translateX(115%)'; setTimeout(() => { if (!plainMode) b.remove(); }, 300); }
      if (window.__hdpxResume) { window.__hdpxResume(); return; }
      const css = document.createElement('link');
      css.rel = 'stylesheet'; css.href = '/_hdp/overlay.css?v=' + V;
      const js = document.createElement('script');
      js.src = '/_hdp/overlay.js?v=' + V;
      js.onerror = css.onerror = reload;
      const pre = document.createElement('link');  // скрипт качается вместе со стилем, а не после него
      pre.rel = 'preload'; pre.as = 'script'; pre.href = js.src;
      document.head.append(pre, css);
      css.onload = () => document.head.append(js);  // сначала вид, потом кнопки — без вспышки без стилей
    };
    const KEY = 'hdpx.onHidden';
    let hidden = false;
    try { hidden = localStorage.getItem(KEY) === '1'; } catch (e) { /* без хранилища — видна */ }
    let tip;
    const say = text => {
      if (!tip) {
        tip = document.createElement('div');
        tip.style.cssText = 'position:fixed;left:50%;bottom:28px;z-index:2147483001;transform:translate(-50%,8px);opacity:0;' +
          'padding:9px 14px;border-radius:12px;background:rgba(32,30,58,.92);color:#fff;font:500 14px/1.3 system-ui,-apple-system,sans-serif;' +
          'white-space:nowrap;pointer-events:none;transition:opacity .2s ease,transform .25s cubic-bezier(.2,.8,.2,1)';
      }
      if (tip.parentElement !== document.body) document.body.append(tip);
      tip.textContent = text;
      requestAnimationFrame(() => { tip.style.opacity = '1'; tip.style.transform = 'translate(-50%,0)'; });
      clearTimeout(tip.t);
      tip.t = setTimeout(() => { tip.style.opacity = '0'; tip.style.transform = 'translate(-50%,8px)'; }, 2600);
    };
    const show = b => {
      b.style.transform = hidden ? 'translateX(115%)' : '';
      b.style.opacity = hidden ? '0' : '1';
      b.style.pointerEvents = hidden ? 'none' : '';
      b.setAttribute('aria-hidden', hidden ? 'true' : 'false');
    };
    const setHidden = v => {
      hidden = v;
      try { v ? localStorage.setItem(KEY, '1') : localStorage.removeItem(KEY); } catch (e) { /* только до перезагрузки */ }
      const b = document.getElementById('hdpx-on');
      if (b) show(b);
      say(v ? 'Кнопка спрятана · вернуть — тройной тап' : 'Кнопка ⏻ снова здесь');
    };
    const place = () => {
      if (!document.body) return;
      let b = document.getElementById('hdpx-on');
      if (!b) {
        b = document.createElement('button');
        b.id = 'hdpx-on';
        b.type = 'button';
        b.title = 'Включить HDP-бота · удержи или смахни вправо — спрятать';
        b.setAttribute('aria-label', 'Включить HDP-бота');
        b.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M12 3.5v8"/><path d="M6.6 6.8a7.5 7.5 0 1 0 10.8 0"/></svg>';
        b.style.cssText = 'position:fixed;right:0;z-index:2147483000;width:32px;height:40px;padding:0;display:grid;place-items:center;' +
          'border:0;border-radius:8px 0 0 8px;background:#5854a4;color:#fff;cursor:pointer;box-shadow:0 4px 16px rgba(40,36,90,.18);' +
          'touch-action:none;user-select:none;-webkit-user-select:none;-webkit-touch-callout:none;-webkit-tap-highlight-color:transparent;' +
          'transition:transform .28s cubic-bezier(.2,.8,.2,1),opacity .22s ease';
        // удержание 0,5 с или свайп вправо — спрятать; обычное нажатие — включить бота
        let x0 = null, timer = 0, used = false;
        const stop = () => { clearTimeout(timer); x0 = null; b.style.transition = 'transform .28s cubic-bezier(.2,.8,.2,1),opacity .22s ease'; };
        b.addEventListener('pointerdown', e => {
          x0 = e.clientX; used = false;
          try { b.setPointerCapture(e.pointerId); } catch (err) { /* без захвата — тоже работает */ }
          timer = setTimeout(() => { used = true; stop(); setHidden(true); }, 500);
        });
        b.addEventListener('pointermove', e => {
          if (x0 == null) return;
          const dx = e.clientX - x0;
          if (Math.abs(dx) > 6) clearTimeout(timer);
          if (dx > 0) { b.style.transition = 'none'; b.style.transform = `translateX(${dx}px)`; }
          if (dx > 10) { used = true; stop(); setHidden(true); }
        });
        b.addEventListener('pointerup', () => { if (x0 != null && !used) b.style.transform = ''; stop(); });
        b.addEventListener('pointercancel', () => { if (!used) b.style.transform = ''; stop(); });
        b.addEventListener('contextmenu', e => e.preventDefault());
        b.addEventListener('click', e => { if (used || hidden) { e.preventDefault(); used = false; return; } on(); });
        show(b);
      }
      if (b.parentElement !== document.body) document.body.append(b);
      // то же место, что у ⏻ «выключить»: над родной вкладкой-календарём, если она есть, иначе 190 + 72 px от низа
      const own = document.querySelector('[data-testid="aiAssistantButton"]');
      const r = own && own.getBoundingClientRect();
      const bottom = (r && r.height && r.right >= innerWidth - 2 ? Math.round(innerHeight - r.top + 12) : 190) + 72;
      if (b.style.bottom !== bottom + 'px') b.style.bottom = bottom + 'px';
    };
    // режим «выключено»: ⏻ «включить» на месте и раз в секунду — на своём месте (страница — приложение, раскладка меняется)
    const plainStart = () => {
      if (plainMode) return;
      plainMode = true;
      if (document.body) {
        place();
        const b = document.getElementById('hdpx-on');
        if (b && !hidden) {  // выезжает из-за края, как уехала кнопка «выключить»
          b.style.transition = 'none'; b.style.transform = 'translateX(115%)'; b.style.opacity = '0';
          requestAnimationFrame(() => requestAnimationFrame(() => { b.style.transition = 'transform .28s cubic-bezier(.2,.8,.2,1),opacity .22s ease'; show(b); }));
        }
      }
      clearInterval(tick);
      tick = setInterval(place, 1000);
    };
    window.__hdpxPlain = plainStart;
    if (/(^|;\s*)hdp_plain=1(;|$)/.test(document.cookie || '')) {
      plainStart();
      addEventListener('DOMContentLoaded', () => { if (plainMode) place(); });
      // кнопки бота заранее в кэш браузера (файлы с версией, кэш на год) — «включить» срабатывает без ожидания сети
      addEventListener('load', () => setTimeout(() => {
        for (const f of ['overlay.css', 'overlay.js']) fetch(`/_hdp/${f}?v=${V}`, { credentials: 'same-origin' }).catch(() => {});
      }, 2500));
    }
    // спрятана — тройной тап (клик) по пустому месту страницы возвращает её
    let taps = [];
    addEventListener('pointerup', e => {
      if (!plainMode || !hidden || (e.target.closest && e.target.closest('a, button, input, textarea, select, label, [contenteditable]'))) return;
      const now = Date.now();
      taps = taps.filter(t => now - t < 700).concat(now);
      if (taps.length >= 3) { taps = []; setHidden(false); }
    }, { capture: true, passive: true });
    addEventListener('keydown', e => { if (plainMode && e.altKey && (e.code === 'KeyB' || e.key === 'b' || e.key === 'и')) { e.preventDefault(); on(); } }, true);
  }
  // Тест (Глеб 20.10): на новом вопросе курсор сразу в поле ответа; Enter — к следующему пустому полю, затем «Далее».
  // Только «Далее» (data-testid=testFormNextButton) — «Отправить на проверку» Enter не нажимает.
  if (typeof document !== 'undefined') {
    const FIELD = 'input:not([type]),input[type=text],input[type=number],input[type=search],input[type=tel],textarea';
    const fields = () => {
      const v = document.querySelector('[data-testid=questionView]');
      return v ? [...v.querySelectorAll(FIELD)].filter(i => !i.readOnly && !i.disabled && i.offsetParent) : [];
    };
    const nextBtn = () => {
      const b = document.querySelector('[data-testid=testFormNextButton]');
      return b && !b.disabled && b.getAttribute('aria-disabled') !== 'true' && !/отправ/i.test(b.textContent || '') ? b : null;
    };
    // iPad: клавиатура держится только за фокус из нажатия — до появления нового поля держим её на невидимом
    let proxy, back;
    const hold = () => {
      back = document.activeElement;
      if (!proxy) {
        proxy = document.createElement('input');
        proxy.setAttribute('aria-hidden', 'true');
        proxy.tabIndex = -1;
        proxy.style.cssText = 'position:fixed;left:0;top:0;width:1px;height:1px;opacity:0;font-size:16px;border:0;padding:0;pointer-events:none';
      }
      if (proxy.parentElement !== document.body) document.body.append(proxy);
      try { proxy.focus({ preventScroll: true }); } catch (e) { proxy.focus(); }
      setTimeout(() => {  // вопрос не сменился — вернуть курсор в прежнее поле
        if (document.activeElement !== proxy) return;
        if (back && back.isConnected && back !== document.body) back.focus(); else proxy.blur();
      }, 2500);
    };
    const free = () => { const a = document.activeElement; return !a || a === document.body || a === proxy || a.matches('[data-testid=testFormNextButton]'); };
    // новый вопрос — по заголовку «Вопрос N/M» (дёшево, на каждое изменение страницы); поле ищем несколько раз
    // каждые 50 мс до 1,5 с (оно дорисовывается позже заголовка), а не на каждое изменение — иначе лишний пересчёт вёрстки
    let seen = '', tries = 0, timer = 0;
    const focusField = () => {
      const f = fields();
      if (!f.length) {
        if (++tries < 30) { timer = setTimeout(focusField, 50); return; }
        if (document.activeElement === proxy) proxy.blur();  // вопрос без поля (выбор, порядок)
        return;
      }
      const to = f.find(i => !i.value) || f[0];
      if (!free()) return;
      try { to.focus({ preventScroll: true }); } catch (e) { to.focus(); }
      to.scrollIntoView({ block: 'nearest' });
    };
    const aim = () => {
      const t = document.querySelector('[data-testid=questionTitle]');
      if (!t) { seen = ''; return; }
      const key = location.pathname + '|' + t.textContent;
      if (key === seen) return;
      seen = key; tries = 0; clearTimeout(timer);
      timer = setTimeout(focusField, 0);
    };
    new MutationObserver(aim).observe(document, { childList: true, subtree: true, characterData: true });
    // «Назад» (Глеб 20.10) — двойной Enter; поэтому «Далее» ждёт 0,3 с, не будет ли второго нажатия
    const prevBtn = () => {
      const n = document.querySelector('[data-testid=testFormNextButton]');
      const box = n && n.parentElement;
      const b = document.querySelector('[data-testid=testFormPrevButton],[data-testid=testFormBackButton]') ||
        (box ? [...box.querySelectorAll('button')] : []).find(x => /^назад$/i.test((x.textContent || '').trim()));
      return b && !b.disabled && b.getAttribute('aria-disabled') !== 'true' ? b : null;
    };
    let lastEnter = 0, pending = 0;
    addEventListener('keydown', e => {
      if (e.key !== 'Enter' && !/^(Shift|Alt|Control|Meta)$/.test(e.key)) lastEnter = 0;  // набрал ответ — это уже не двойной Enter
      if (e.key !== 'Enter' || e.repeat || e.shiftKey || e.altKey || e.ctrlKey || e.metaKey || e.isComposing) return;
      const a = document.activeElement;
      const f = fields();
      const i = f.indexOf(a);
      // выбор варианта (фокус на нём) — тоже «Далее»; чужие поля и кнопки не трогаем
      const onPage = !a || a === document.body || a === proxy ||
        (!!a.closest('[data-testid=questionView]') && !a.matches(FIELD + ',select,[contenteditable]'));
      if (i < 0 && !onPage) return;  // своё поле или кнопка — Enter работает как обычно
      if (!document.querySelector('[data-testid=questionTitle]')) return;
      e.preventDefault(); e.stopPropagation();
      const now = Date.now();
      if (now - lastEnter < 350) {  // второй Enter подряд — «Назад» вместо «Далее»
        lastEnter = 0; clearTimeout(pending);
        const p = prevBtn();
        if (p) { if (document.activeElement !== proxy) hold(); setTimeout(() => p.click(), 30); }
        return;
      }
      lastEnter = now;
      const later = i >= 0 ? f.slice(i + 1).find(x => !x.value) : null;
      if (later) { later.focus(); return; }
      const b = nextBtn();
      if (!b) return;
      if (i >= 0) hold();  // уход из поля = blur: платформа сохраняет ответ, клавиатура остаётся
      pending = setTimeout(() => b.click(), 330);
    }, true);
    // «Далее» мышкой или пальцем — тоже держим клавиатуру до следующего поля
    addEventListener('pointerdown', e => {
      if (e.target.closest && e.target.closest('[data-testid=testFormNextButton]') && fields().includes(document.activeElement)) hold();
    }, true);
  }
  // Плавность (Глеб 20.10): палец коснулся карточки задания — сервер уже берёт её данные у платформы,
  // пока браузер только начинает переход (mirror.Ahead): страница задания открывается без долгого скелета.
  if (typeof document !== 'undefined') {
    let last = '';
    const intent = e => {
      const a = e.target.closest && e.target.closest('a[href*="/task/"]');
      const m = a && /\/topic\/([\w-]+)\/task\/([\w-]+)/.exec(a.getAttribute('href') || '');
      if (!m || m[0] === last) return;
      last = m[0];
      setTimeout(() => { if (last === m[0]) last = ''; }, 15000);
      const sw = navigator.serviceWorker && navigator.serviceWorker.controller;
      if (sw) sw.postMessage({ hdpx: 'task', t: m[1], x: m[2] });  // sw.js берёт всю цепочку одним ответом
      else fetch(`/_hdp/ahead?t=${m[1]}&x=${m[2]}`, { credentials: 'same-origin', keepalive: true }).catch(() => {});
    };
    // sw.js (данные задания одним ответом). Выключить: localStorage hdpx.noSW = 1 — снимается сам при следующем заходе
    if (navigator.serviceWorker && location.protocol === 'https:') {
      addEventListener('load', () => setTimeout(() => {
        let off = false;
        try { off = !!localStorage.getItem('hdpx.noSW'); } catch (e) { /* без хранилища — как обычно */ }
        if (off) navigator.serviceWorker.getRegistrations().then(rs => rs.forEach(r => r.unregister())).catch(() => {});
        else navigator.serviceWorker.register('/_hdp/sw.js', { scope: '/' }).catch(() => {});
      }, 1500));
    }
    addEventListener('pointerdown', intent, { capture: true, passive: true });
    // курсор (трекпад iPad, мышь): задержался на карточке 150 мс — тоже заранее; пробежал мимо — нет
    let hover = 0;
    addEventListener('mouseover', e => {
      clearTimeout(hover);
      if (e.target.closest && e.target.closest('a[href*="/task/"]')) hover = setTimeout(() => intent(e), 150);
    }, { capture: true, passive: true });
  }
  // Камера (Глеб 20.10: «чтобы я мог фоткать пробелом»): на странице задания с полем ответа — кнопка 📷 рядом со
  // скрепкой платформы (или Пробел, когда курсор не в поле). Живая камера: Пробел / кнопка — снимок, он сразу
  // прикрепляется к ответу через скрепку платформы (вместо окна выбора файла ей отдаём снимок); Enter / Esc — готово.
  if (typeof document !== 'undefined' && navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
    let pend = null, ours = false;  // снимок для следующего «выбора файла» платформы; ours — это наша подстановка
    // Снимок камеры — сразу не больше MAX_SIDE (Глеб 20.10: «фотки долго грузятся»): платформе меньше работы.
    const MAX_SIDE = 2400, JPEG_Q = 0.82;
    // фото из окна скрепки не трогаем: платформа сама уменьшает их до 1365 px перед загрузкой (проверено 20.10:
    // своё сжатие давало двойное и файл больше — 315 КБ против 271)
    const click0 = HTMLInputElement.prototype.click;
    HTMLInputElement.prototype.click = function () {
      if (this.type === 'file' && pend) {
        const dt = new DataTransfer();
        pend.forEach(f => dt.items.add(f));
        pend = null;
        this.files = dt.files;
        ours = true;
        try {
          this.dispatchEvent(new Event('input', { bubbles: true }));
          this.dispatchEvent(new Event('change', { bubbles: true }));
        } finally { ours = false; }
        return;
      }
      if (this.type === 'file' && !this.__hdpxMulti) {  // поле выбора может быть и вне страницы — перехват на нём самом
        this.__hdpxMulti = true;
        this.addEventListener('change', onMulti, true);
      }
      return click0.apply(this, arguments);
    };
    const attachBtn = () => document.querySelector('[data-testid=attachButton]');
    // Платформа теряет фото, если их грузится несколько сразу (21.10: выбрал 3 — прикрепилось 1, на самой платформе
    // тоже; по одному — все на месте). Поэтому отдаём ей по одному: следующее — когда предыдущее загрузилось, то есть
    // у его карточки картинка уже с платформы (/store/…); сама карточка появляется сразу, ещё до загрузки. До 2 мин.
    const ATT = '[data-testid$="Attachment"]';
    const loaded = () => [...document.querySelectorAll(ATT)].filter(c => { const i = c.querySelector('img'); return !i || /\/store\//.test(i.getAttribute('src') || ''); }).length;
    const queue = [];
    let feeding = false;
    const feed = () => {
      if (feeding || !queue.length) return;
      const b = attachBtn();
      if (!b) { queue.length = 0; return; }
      feeding = true;
      const file = queue.shift(), before = loaded(), t0 = Date.now();
      pend = [file]; b.click();
      if (pend) { pend = null; feeding = false; return; }  // платформа не открыла выбор файла — не ждём
      const wait = setInterval(() => {
        if (loaded() > before || Date.now() - t0 > 120000) {
          clearInterval(wait); feeding = false; setTimeout(feed, 3000);  // платформа ещё дописывает своё — как человек
        }
      }, 250);
    };
    const attach = file => { if (!attachBtn()) return false; queue.push(file); feed(); return true; };
    // выбрал несколько файлов в окне скрепки — платформе по одному (нашу подстановку не трогаем)
    function onMulti(e) {
      const inp = e.target;
      if (ours || !inp || inp.type !== 'file' || !inp.files || inp.files.length < 2 || inp.__hdpxSeen === e) return;
      inp.__hdpxSeen = e;  // и на самом поле, и на window — один раз
      e.stopImmediatePropagation();
      [...inp.files].forEach(f => queue.push(f));
      setTimeout(feed, 0);  // не внутри её события выбора файла
    }
    addEventListener('change', onMulti, true);
    // карточка прикреплённого фото у поля ответа: подпись (alt) — имя нашего файла (платформа добавляет к нему
    // «HDP_IMAGE_THUMB_») → по нему её заменить/удалить
    const chip = name => [...document.querySelectorAll('[data-testid=imageAttachment]')].find(c => {
      const i = c.querySelector('img'); return i && (i.alt === name || i.alt.endsWith('_' + name));
    });
    const unattach = (name, tries = 0) => {  // фото ещё грузится (карточки нет) — подождём её до 60 с
      const c = chip(name);
      if (c) { const x = c.querySelector('[class*="_close_"]'); if (x) x.click(); return; }
      if (tries < 120) setTimeout(() => unattach(name, tries + 1), 500);
    };
    const loadImg = url => new Promise((ok, bad) => { const i = new Image(); i.onload = () => ok(i); i.onerror = bad; i.src = url; });
    const CSS = `
      :host{all:initial}
      .cam{position:fixed;inset:0;z-index:2147483600;background:#0b0b10;color:#fff;font:500 15px/1.3 -apple-system,system-ui,sans-serif;
        opacity:0;transition:opacity .2s ease;-webkit-user-select:none;user-select:none}
      .cam.on{opacity:1}
      video{position:absolute;left:50%;top:calc(50% - 50px);transform:translate(-50%,-50%);max-width:100%;max-height:calc(100% - 170px);border-radius:16px;background:#000}
      .flash{position:absolute;inset:0;background:#fff;opacity:0;pointer-events:none}
      .bar{position:absolute;left:0;right:0;bottom:0;height:140px;display:flex;align-items:center;justify-content:center}
      .shutter{width:76px;height:76px;border-radius:50%;border:5px solid rgba(255,255,255,.92);background:#fff;background-clip:content-box;padding:4px;cursor:pointer;
        transition:transform .12s ease}
      .shutter:active{transform:scale(.9)}
      .info{position:absolute;left:24px;bottom:58px;opacity:.7;font-size:14px}
      .x{position:absolute;top:18px;right:18px;width:44px;height:44px;border-radius:50%;border:0;background:rgba(255,255,255,.14);color:#fff;font-size:20px;cursor:pointer}
      .thumb{position:absolute;right:24px;bottom:34px;width:72px;height:72px;border-radius:14px;overflow:visible;cursor:pointer;display:none;
        border:0;padding:0;background:none}
      .thumb.has{display:block}
      .thumb img{width:72px;height:72px;object-fit:cover;border-radius:14px;border:2px solid rgba(255,255,255,.9);box-shadow:0 6px 20px rgba(0,0,0,.45);display:block}
      .thumb.pop img{animation:pop .38s cubic-bezier(.2,.9,.25,1.25)}
      @keyframes pop{from{transform:scale(.35) translate(-120px,-60px);opacity:.2}to{transform:none;opacity:1}}
      .badge{position:absolute;top:-8px;right:-8px;min-width:22px;height:22px;padding:0 6px;border-radius:11px;background:#6d68d8;color:#fff;font-size:13px;
        display:grid;place-items:center;box-sizing:border-box}
      .dot{position:absolute;left:-5px;bottom:-5px;width:12px;height:12px;border-radius:50%;background:#f5b942;border:2px solid #0b0b10}
      .dot.ok{background:#3ecf7a}
      .ed{position:absolute;inset:0;background:#0b0b10;display:none;flex-direction:column}
      .ed.on{display:flex}
      .stage{flex:1;position:relative;display:grid;place-items:center;overflow:hidden;touch-action:none}
      .stage img{max-width:calc(100% - 40px);max-height:calc(100% - 30px);border-radius:10px;transition:transform .25s ease}
      .crop{position:absolute;border:2px solid #fff;box-shadow:0 0 0 9999px rgba(0,0,0,.55);display:none;touch-action:none}
      .crop.on{display:block}
      .crop i{position:absolute;width:26px;height:26px;border:4px solid #fff;box-sizing:border-box}
      .crop i[data-h=nw]{left:-4px;top:-4px;border-right:0;border-bottom:0}.crop i[data-h=ne]{right:-4px;top:-4px;border-left:0;border-bottom:0}
      .crop i[data-h=sw]{left:-4px;bottom:-4px;border-right:0;border-top:0}.crop i[data-h=se]{right:-4px;bottom:-4px;border-left:0;border-top:0}
      .strip{display:flex;gap:8px;justify-content:center;padding:10px 12px 0;overflow-x:auto}
      .strip img{width:52px;height:52px;object-fit:cover;border-radius:9px;opacity:.55;cursor:pointer;border:2px solid transparent}
      .strip img.cur{opacity:1;border-color:#fff}
      .tools{display:flex;gap:10px;justify-content:center;align-items:center;padding:14px 12px 26px;flex-wrap:wrap}
      .tools button{height:44px;padding:0 16px;border-radius:22px;border:0;background:rgba(255,255,255,.12);color:#fff;font:500 15px -apple-system,system-ui,sans-serif;cursor:pointer}
      .tools button.on{background:#fff;color:#111}
      .tools .main{background:#6d68d8}
      .tools .del{color:#ff8f8f}
      .hint{text-align:center;opacity:.55;font-size:13px;padding-top:6px}`;
    let host = null, ui = null, stream = null, shots = [], cur = -1;
    const close = () => {
      if (stream) stream.getTracks().forEach(t => t.stop());
      stream = null;
      if (host) { const h = host; ui.cam.classList.remove('on'); setTimeout(() => h.remove(), 200); }
      host = ui = null;
    };
    const watchUpload = shot => {  // зелёная точка, когда карточка фото появилась у поля ответа
      const t = setInterval(() => {
        if (!ui || chip(shot.name)) { clearInterval(t); shot.up = true; if (ui) drawThumb(false); }
      }, 400);
      setTimeout(() => clearInterval(t), 120000);
    };
    const add = (blob, at) => {
      const name = `photo_${Date.now()}.jpg`;
      if (!attach(new File([blob], name, { type: 'image/jpeg' }))) { ui.info.textContent = 'Не прикрепилось — нажми скрепку'; return null; }
      const shot = { name, blob, url: URL.createObjectURL(blob), up: false };
      if (at == null) shots.push(shot); else shots.splice(at, 0, shot);
      watchUpload(shot);
      return shot;
    };
    const drawThumb = pop => {
      const last = shots[shots.length - 1];
      ui.thumb.classList.toggle('has', !!last);
      if (!last) return;
      ui.timg.src = last.url;
      ui.badge.textContent = shots.length;
      ui.dot.classList.toggle('ok', shots.every(s => s.up));
      if (pop) { ui.thumb.classList.remove('pop'); void ui.thumb.offsetWidth; ui.thumb.classList.add('pop'); }
    };
    // ---- редактор: повернуть, обрезать, удалить; сохранить — фото у поля ответа заменяется новым ----
    let rot = 0, cropOn = false, rect = null;
    const edOpen = i => {
      if (!shots.length) return;
      cur = Math.max(0, Math.min(i, shots.length - 1));
      rot = 0; cropOn = false; rect = null;
      ui.eimg.style.transform = '';
      ui.eimg.src = shots[cur].url;
      ui.crop.classList.remove('on'); ui.bcrop.classList.remove('on');
      ui.strip.replaceChildren(...shots.map((s, k) => { const im = document.createElement('img'); im.src = s.url; im.className = k === cur ? 'cur' : ''; im.onclick = () => edOpen(k); return im; }));
      ui.ed.classList.add('on');
    };
    const edClose = () => { ui.ed.classList.remove('on'); cur = -1; drawThumb(false); };
    const imgBox = () => {  // где на экране картинка (с учётом поворота)
      const r = ui.eimg.getBoundingClientRect(), st = ui.stage.getBoundingClientRect();
      return { x: r.left - st.left, y: r.top - st.top, w: r.width, h: r.height };
    };
    const placeCrop = () => { const c = ui.crop.style; c.left = rect.x + 'px'; c.top = rect.y + 'px'; c.width = rect.w + 'px'; c.height = rect.h + 'px'; };
    const toggleCrop = () => {
      cropOn = !cropOn;
      ui.bcrop.classList.toggle('on', cropOn);
      if (cropOn) { const b = imgBox(); rect = { x: b.x + b.w * 0.06, y: b.y + b.h * 0.06, w: b.w * 0.88, h: b.h * 0.88 }; placeCrop(); }
      ui.crop.classList.toggle('on', cropOn);
    };
    const save = async () => {
      const s = shots[cur];
      if (!s) return;
      if (!rot && !cropOn) return edClose();
      const im = await loadImg(s.url);
      const W = im.naturalWidth, H = im.naturalHeight, turn = rot % 180 !== 0;
      const full = document.createElement('canvas');  // сначала поворот
      full.width = turn ? H : W; full.height = turn ? W : H;
      const g = full.getContext('2d');
      g.translate(full.width / 2, full.height / 2); g.rotate(rot * Math.PI / 180); g.drawImage(im, -W / 2, -H / 2);
      let out = full;
      if (cropOn && rect) {  // потом обрезка: рамка на экране → пиксели повёрнутой картинки
        const b = imgBox(), kx = full.width / b.w, ky = full.height / b.h;
        const x = Math.max(0, (rect.x - b.x) * kx), y = Math.max(0, (rect.y - b.y) * ky);
        const w = Math.min(full.width - x, rect.w * kx), h = Math.min(full.height - y, rect.h * ky);
        out = document.createElement('canvas'); out.width = Math.round(w); out.height = Math.round(h);
        out.getContext('2d').drawImage(full, x, y, w, h, 0, 0, out.width, out.height);
      }
      const blob = await new Promise(r => out.toBlob(r, 'image/jpeg', JPEG_Q));
      if (!blob) return;
      unattach(s.name);
      shots.splice(cur, 1);
      URL.revokeObjectURL(s.url);
      const ns = add(blob, cur);
      if (ns) edOpen(shots.indexOf(ns)); else edClose();
      ui.info.textContent = 'Сохранено — фото у ответа заменено';
    };
    const del = () => {
      const s = shots[cur];
      if (!s) return;
      unattach(s.name);
      shots.splice(cur, 1);
      URL.revokeObjectURL(s.url);
      if (shots.length) edOpen(Math.min(cur, shots.length - 1)); else edClose();
    };
    // рамка обрезки: тянуть за углы или двигать целиком
    let drag = null;
    const onDown = e => {
      if (!cropOn) return;
      const h = e.target.dataset && e.target.dataset.h;
      drag = { h: h || 'move', x: e.clientX, y: e.clientY, r: { ...rect } };
      e.target.setPointerCapture && e.target.setPointerCapture(e.pointerId);
      e.preventDefault();
    };
    const onMove = e => {
      if (!drag) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y, b = imgBox(), r = { ...drag.r }, MIN = 40;
      if (drag.h === 'move') { r.x += dx; r.y += dy; }
      if (drag.h.includes('w')) { r.x += dx; r.w -= dx; }
      if (drag.h.includes('e')) r.w += dx;
      if (drag.h.includes('n')) { r.y += dy; r.h -= dy; }
      if (drag.h.includes('s')) r.h += dy;
      r.w = Math.max(MIN, Math.min(r.w, b.w)); r.h = Math.max(MIN, Math.min(r.h, b.h));
      r.x = Math.max(b.x, Math.min(r.x, b.x + b.w - r.w)); r.y = Math.max(b.y, Math.min(r.y, b.y + b.h - r.h));
      rect = r; placeCrop();
    };
    const open = async () => {
      if (host || !attachBtn()) return;
      shots = []; cur = -1;
      host = document.createElement('div');
      host.id = 'hdpx-cam';
      const root = host.attachShadow({ mode: 'closed' });  // своя «коробка»: тёмная тема платформы её не перекрашивает
      root.innerHTML = `<style>${CSS}</style><div class="cam"><video playsinline muted autoplay></video><div class="flash"></div>
        <div class="bar"><button class="shutter" aria-label="Снимок"></button></div><div class="info">Пробел — снимок · Enter — готово</div>
        <button class="thumb" aria-label="Снимки — открыть"><img alt=""><span class="badge"></span><span class="dot"></span></button>
        <button class="x" aria-label="Готово">✕</button>
        <div class="ed"><div class="stage"><img alt=""><div class="crop"><i data-h="nw"></i><i data-h="ne"></i><i data-h="sw"></i><i data-h="se"></i></div></div>
          <div class="strip"></div><div class="hint">R — повернуть · C — обрезать · Enter — сохранить · Esc — к камере</div>
          <div class="tools"><button class="back">‹ Камера</button><button class="rot">↻ Повернуть</button><button class="cropb">✂︎ Обрезать</button>
          <button class="del">Удалить</button><button class="main save">Сохранить</button></div></div></div>`;
      const q = sel => root.querySelector(sel);
      ui = { cam: q('.cam'), video: q('video'), flash: q('.flash'), info: q('.info'), thumb: q('.thumb'), timg: q('.thumb img'), badge: q('.badge'),
        dot: q('.dot'), ed: q('.ed'), stage: q('.stage'), eimg: q('.stage img'), crop: q('.crop'), strip: q('.strip'), bcrop: q('.cropb') };
      document.body.append(host);
      requestAnimationFrame(() => ui && ui.cam.classList.add('on'));
      q('.x').onclick = close;
      q('.shutter').onclick = () => shoot();
      ui.thumb.onclick = () => edOpen(shots.length - 1);
      q('.back').onclick = edClose;
      q('.rot').onclick = () => root_rot();
      ui.bcrop.onclick = toggleCrop;
      q('.del').onclick = del;
      q('.save').onclick = save;
      ui.crop.addEventListener('pointerdown', onDown);
      ui.stage.addEventListener('pointermove', onMove);
      ui.stage.addEventListener('pointerup', () => { drag = null; });
      ui.stage.addEventListener('pointercancel', () => { drag = null; });
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: false,
          video: { facingMode: { ideal: 'environment' }, width: { ideal: 4032 }, height: { ideal: 3024 } } });
        if (!ui) { stream.getTracks().forEach(t => t.stop()); return; }
        ui.video.srcObject = stream;
      } catch (e) {
        ui.info.textContent = 'Нет доступа к камере — разреши её для сайта в Safari';
      }
    };
    const shoot = () => {
      const v = ui && ui.video;
      if (!v || !v.videoWidth || cur >= 0) return;
      const c = document.createElement('canvas');
      const k = Math.min(1, MAX_SIDE / Math.max(v.videoWidth, v.videoHeight));  // меньше файл — быстрее загрузка
      c.width = Math.round(v.videoWidth * k); c.height = Math.round(v.videoHeight * k);
      c.getContext('2d').drawImage(v, 0, 0, c.width, c.height);
      const f = ui.flash;
      f.style.transition = 'none'; f.style.opacity = '.85';
      requestAnimationFrame(() => { f.style.transition = 'opacity .25s ease'; f.style.opacity = '0'; });
      c.toBlob(b => { if (b && ui && add(b)) drawThumb(true); }, 'image/jpeg', JPEG_Q);
    };
    const typing = () => { const a = document.activeElement; return a && (a.isContentEditable || /^(INPUT|TEXTAREA|SELECT|IFRAME)$/.test(a.tagName)); };
    addEventListener('keydown', e => {
      if (host) {
        const k = e.key, stop = () => { e.preventDefault(); e.stopPropagation(); };
        if (cur >= 0) {  // редактор
          if (k === 'Escape') { stop(); edClose(); } else if (k === 'Enter') { stop(); save(); }
          else if (k === 'r' || k === 'R' || k === 'к' || k === 'К') { stop(); root_rot(); }
          else if (k === 'c' || k === 'C' || k === 'с' || k === 'С') { stop(); toggleCrop(); }
          else if (k === 'Delete' || k === 'Backspace') { stop(); del(); }
          else if (k === 'ArrowLeft' || k === 'ArrowRight') { stop(); edOpen(cur + (k === 'ArrowLeft' ? -1 : 1)); }
          else if (e.code === 'Space') stop();
          return;
        }
        if (e.code === 'Space' || k === ' ') { stop(); if (!e.repeat) shoot(); }
        else if (k === 'Enter' || k === 'Escape') { stop(); close(); }
        return;
      }
      if ((e.code === 'Space' || e.key === ' ') && !e.repeat && !e.altKey && !e.ctrlKey && !e.metaKey && !typing() && attachBtn()) {
        e.preventDefault(); e.stopPropagation(); open();
      }
    }, true);
    const root_rot = () => {  // повёрнутая на 90° картинка должна влезть в экран — уменьшаем
      rot = (rot + 90) % 360;
      if (cropOn) toggleCrop();
      const st = ui.stage.getBoundingClientRect(), w = ui.eimg.offsetWidth, h = ui.eimg.offsetHeight;
      const k = rot % 180 ? Math.min(1, (st.width - 40) / h, (st.height - 30) / w) : 1;
      ui.eimg.style.transform = `rotate(${rot}deg) scale(${k})`;
    };
    // 📷 рядом со скрепкой платформы (своя кнопка поверх, вёрстку не трогает)
    const camBtn = () => {
      const a = attachBtn();
      let b = document.getElementById('hdpx-camb');
      if (!a || !a.getBoundingClientRect().width) { if (b) b.remove(); return; }
      if (!b) {
        b = document.createElement('button');
        b.id = 'hdpx-camb'; b.type = 'button'; b.title = 'Камера · Пробел — снимок';
        b.setAttribute('aria-label', 'Камера');
        b.innerHTML = '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 8.5A2.5 2.5 0 0 1 6.5 6h1.6l1.4-2h5l1.4 2h1.6A2.5 2.5 0 0 1 20 8.5v8A2.5 2.5 0 0 1 17.5 19h-11A2.5 2.5 0 0 1 4 16.5z"/><circle cx="12" cy="12.5" r="3.4"/></svg>';
        b.addEventListener('click', open);
        document.body.append(b);
      }
      const ar = a.getBoundingClientRect();  // слева от скрепки: справа от QR сразу начинается поле ответа
      const st = `position:fixed;z-index:2147482000;top:${Math.round(ar.top)}px;left:${Math.max(2, Math.round(ar.left - ar.width - 6))}px;width:${Math.round(ar.width)}px;` +
        `height:${Math.round(ar.height)}px;display:grid;place-items:center;border:0;background:transparent;color:${getComputedStyle(a).color};cursor:pointer;padding:0`;
      if (b.style.cssText !== st) b.style.cssText = st;
    };
    setInterval(camBtn, 700);
    addEventListener('resize', camBtn);
  }
  const d = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow');
  if (d && d.get) {
    Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
      configurable: true, enumerable: d.enumerable,
      get() { const w = d.get.call(this); patch(w); return w; },  // окно другого сайта patch пропустит (доступ бросит ошибку)
    });
  }
})();
