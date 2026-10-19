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
  // «⏻ Выключить всё» (cookie hdp_plain): кнопок бота на странице нет. Включить обратно — 5 быстрых тапов или Alt+B
  if (typeof document !== 'undefined' && /(^|;\s*)hdp_plain=1(;|$)/.test(document.cookie || '')) {
    const on = () => { if (confirm('Включить кнопки HDP-бота?')) location.href = '/_hdp/on?to=' + encodeURIComponent(location.pathname + location.search); };
    let taps = [];
    addEventListener('pointerdown', () => {
      const now = Date.now();
      taps = taps.filter(t => now - t < 1500).concat(now);
      if (taps.length >= 5) { taps = []; on(); }
    }, true);
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
