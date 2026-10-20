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
  const d = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow');
  if (d && d.get) {
    Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
      configurable: true, enumerable: d.enumerable,
      get() { const w = d.get.call(this); patch(w); return w; },  // окно другого сайта patch пропустит (доступ бросит ошибку)
    });
  }
})();
