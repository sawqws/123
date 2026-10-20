/* Зеркало платформы (bot/mirror.py): этот файл идёт первым в <head>, до бандла платформы.
   Глушит отправку аналитики и отчётов об ошибках (Яндекс-метрика, Sentry): иначе платформа получала бы адрес зеркала.
   Sentry берёт «чистый» fetch из пустого iframe — поэтому патчим и окна iframe. */
(() => {
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
  // «⏻ Выключить всё» (cookie hdp_plain): кнопок бота на странице нет, кроме одной — ⏻ «включить» на месте
  // кнопки выключения (над вкладкой 🤖 у правого края). Одно нажатие (или Alt+B) — /_hdp/on, бот снова на странице.
  // Её можно убрать (Глеб 20.10): долгое нажатие или свайп вправо — уезжает за край; тройной тап по странице — обратно.
  if (typeof document !== 'undefined' && /(^|;\s*)hdp_plain=1(;|$)/.test(document.cookie || '')) {
    const on = () => { location.href = '/_hdp/on?to=' + encodeURIComponent(location.pathname + location.search); };
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
    addEventListener('DOMContentLoaded', place);
    setInterval(place, 1000);  // страница — приложение: перерисовывается и меняет раскладку
    // спрятана — тройной тап (клик) по пустому месту страницы возвращает её
    let taps = [];
    addEventListener('pointerup', e => {
      if (!hidden || (e.target.closest && e.target.closest('a, button, input, textarea, select, label, [contenteditable]'))) return;
      const now = Date.now();
      taps = taps.filter(t => now - t < 700).concat(now);
      if (taps.length >= 3) { taps = []; setHidden(false); }
    }, { capture: true, passive: true });
    addEventListener('keydown', e => { if (e.altKey && (e.code === 'KeyB' || e.key === 'b' || e.key === 'и')) { e.preventDefault(); on(); } }, true);
  }
  const d = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow');
  if (d && d.get) {
    Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
      configurable: true, enumerable: d.enumerable,
      get() { const w = d.get.call(this); patch(w); return w; },  // окно другого сайта patch пропустит (доступ бросит ошибку)
    });
  }
})();
