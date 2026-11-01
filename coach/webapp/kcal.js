/* Вкладка «Питание» = «Калории» целиком (coach/SYNC.md, п. 5; Глеб 01.11: «один сайт, один вход»).
   Сайт «Калории» отдаёт сам сервер Формы по /kcal/ (тот же домен) — он встраивается рамкой на всю область вкладки.
   Вход без кода: токен «Калорий» лежит в localStorage['kcal.token'] — на этом домене он общий для Формы и «Калорий»;
   нет его — берём у сервера по сессии Формы (POST /api/kcal/link). Рамка создаётся один раз при первом показе
   вкладки и дальше живёт (переключение вкладок — мгновенно, без перезагрузки и с той же прокруткой).
   Тема — как у Формы, смена — postMessage {type: 'forma-theme', theme}. «Калории» сообщают {type: 'kcal-changed'}
   после сохранения — Форма тихо обновляет день (мини-карточка «Питание» и вода на главной). */
(window.FormaModules = window.FormaModules || []).push(F => {
  const { S, api } = F;
  const TOKEN = 'kcal.token';
  const PULL_AFTER = 3500;  // «Калории» шлют день на сервер через ~2 с после сохранения — забираем чуть позже
  const ls = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); return localStorage.getItem(k) === v; } catch (e) { return false; } },
  };
  const theme = () => document.documentElement.classList.contains('lt') ? 'light' : 'dark';
  let pane = null, frame = null, ready = false, sent = null, timer = 0, opening = false;

  // каркас, похожий на «Калории», пока они грузятся: сегменты, кольцо дня, приёмы пищи
  const SKEL = `<div class="kp-sk" aria-hidden="true"><div class="kp-seg"><i class="sk"></i><i class="sk"></i><i class="sk"></i><i class="sk"></i></div>
    <div class="kp-hero"><div class="sk kp-ring"></div><div class="kp-lines"><div class="sk line" style="width:70%"></div><div class="sk line"></div>
    <div class="sk line" style="width:84%"></div><div class="sk line" style="width:56%"></div></div></div>
    <div class="sk kp-card"></div><div class="sk kp-card short"></div><div class="sk kp-card short"></div></div>`;

  function ensure() {
    if (pane) return;
    pane = document.createElement('section');
    pane.id = 'kcal-pane';
    pane.setAttribute('aria-label', 'Питание');
    pane.innerHTML = SKEL + `<div class="kp-err card" hidden><div class="empty"><span class="e">⚠️</span><b>Питание не открылось</b><span class="kp-msg"></span></div>
      <button class="btn primary" data-a="kcal-retry">Повторить</button></div>`;
    document.body.append(pane);
    open();
  }
  function fail(e) {
    opening = false;
    pane.classList.add('err');
    pane.querySelector('.kp-err').hidden = false;
    pane.querySelector('.kp-msg').textContent = (e && e.message) || 'Проверь интернет и попробуй ещё раз.';
  }
  async function open(relink) {
    if (opening) return;
    opening = true;
    pane.classList.remove('err');
    pane.querySelector('.kp-err').hidden = true;
    let tok = relink ? null : ls.get(TOKEN), shared = true;
    if (!tok) {
      try {
        const r = await api('/api/kcal/link', {});
        if (!r || !r.token) throw new Error('Сервер не выдал вход — попробуй ещё раз');
        tok = r.token;
      } catch (e) { fail(e); return; }
      shared = ls.set(TOKEN, tok);  // нет хранилища — отдадим токен «Калориям» через #pair= (они его прочтут и уберут)
    }
    if (frame) frame.remove();
    ready = false; pane.classList.remove('ready');
    const frameTheme = theme();
    frame = document.createElement('iframe');
    frame.className = 'kp-frame';
    frame.title = 'Калории';
    frame.setAttribute('allow', 'camera; clipboard-write');  // фото еды, копирование
    frame.addEventListener('load', () => {
      opening = false; ready = true;
      requestAnimationFrame(() => pane.classList.add('ready'));
      sent = frameTheme;
      if (theme() !== sent) post();  // тему сменили, пока грузилось
    });
    frame.src = `/kcal/?embed=1&theme=${frameTheme}${shared ? '' : '#pair=' + encodeURIComponent(tok)}`;
    pane.append(frame);
  }
  function post() {
    if (!frame || !ready || !frame.contentWindow) return;
    sent = theme();
    try { frame.contentWindow.postMessage({ type: 'forma-theme', theme: sent }, location.origin); } catch (e) { /* рамка уже ушла */ }
  }
  // тема Формы меняется классом html.lt (theme() в app.js: Telegram, система) — следим за ним, а не за каждым местом смены
  new MutationObserver(() => { if (ready && theme() !== sent) post(); })
    .observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });

  // «Калории» что-то сохранили → тихо обновить день Формы (с паузой: подряд несколько правок — один запрос)
  async function pull() {
    timer = 0;
    try {
      const d = await api('/api/all');
      S.data = d;
      F.onData.forEach(fn => { try { fn(d); } catch (e) { console.error(e); } });
      if (!S.screen && S.tab === 'today' && !S.dayData && !document.querySelector('.sheet')) F.render(false);
    } catch (e) { /* тихо: следующая правка или общий таймер обновят */ }
  }
  addEventListener('message', ev => {
    if (!frame || ev.source !== frame.contentWindow || ev.origin !== location.origin) return;
    const t = ev.data && ev.data.type;
    if (t === 'kcal-changed') { clearTimeout(timer); timer = setTimeout(pull, PULL_AFTER); }
    else if (t === 'kcal-auth') open(true);  // «Калориям» не подошёл токен — новый по сессии Формы, без кода
  });

  F.actions['kcal-retry'] = () => { F.haptic('tap'); open(); };
  // сама вкладка: содержимое — рамка #kcal-pane поверх области вкладки (body.tab-food, app.js), в #view пусто
  F.views.food = () => { ensure(); return '<div class="kp-hold" aria-hidden="true"></div>'; };
  F.kcalPane = { ensure, open };
});
