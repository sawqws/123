/* Форма на экране «Домой» (/site/): вход по коду из бота, потом грузит обычное приложение.
   У значка на iPhone свои cookie (не как в Safari), поэтому код можно ввести прямо здесь. */
(function () {
  const cfg = window.FORMA_SITE || { scripts: [] };
  const standalone = navigator.standalone || matchMedia('(display-mode: standalone)').matches;
  const view = document.getElementById('view');
  const tabs = document.getElementById('tabs');
  const esc = Kit.esc;
  if (standalone) document.body.classList.add('standalone');

  // сессия кончилась посреди работы — снова экран входа, а не «Открой через Telegram»
  const api0 = Kit.api;
  Kit.api = async function (path, body, opts) {
    try { return await api0(path, body, opts); } catch (e) { if (e.status === 401) loginScreen(true); throw e; }
  };

  function load() {
    cfg.scripts.forEach(src => {
      const s = document.createElement('script');
      s.src = src; s.async = false;
      document.body.appendChild(s);
    });
    if (!standalone) setTimeout(installHint, 1500);
  }

  let shown = false;
  function loginScreen(expired) {
    if (shown) return;
    shown = true;
    if (tabs) tabs.hidden = true;
    document.body.classList.add('sl-on');
    const bot = cfg.bot ? `@${esc(cfg.bot)}` : 'боту Формы';
    const box = document.createElement('div');
    box.className = 'site-login';
    box.innerHTML = `
      <div class="sl-top">
        <img class="sl-icon" src="icon-180.png" alt="" width="88" height="88">
        <h1>Форма</h1>
        <p>Тренер по данным WHOOP</p>
      </div>
      <form class="sl-card" novalidate>
        <label for="sl-code">${expired ? 'Вход закончился — введи новый код' : 'Код для входа'}</label>
        <input id="sl-code" inputmode="numeric" autocomplete="one-time-code" maxlength="7" placeholder="000 000" aria-describedby="sl-how">
        <button class="btn primary" type="submit">Войти</button>
        <p class="sl-err" role="alert"></p>
        <p id="sl-how" class="sl-how">Напиши <b>/site</b> ${bot} в Telegram — пришлёт код на 15 минут.</p>
        ${cfg.bot ? `<a class="sl-tg" href="https://t.me/${encodeURIComponent(cfg.bot)}?text=%2Fsite">Открыть бота</a>` : ''}
      </form>`;
    document.querySelectorAll('.site-login').forEach(n => n.remove());
    view.hidden = true;
    document.body.appendChild(box);
    const inp = box.querySelector('input');
    const errEl = box.querySelector('.sl-err');
    inp.addEventListener('input', () => {
      const d = inp.value.replace(/\D/g, '').slice(0, 6);
      inp.value = d.length > 3 ? d.slice(0, 3) + ' ' + d.slice(3) : d;
      errEl.textContent = '';
      if (d.length === 6) submit();
    });
    let busy = false;
    async function submit() {
      const code = inp.value.replace(/\D/g, '');
      if (busy) return;
      if (code.length !== 6) { errEl.textContent = 'В коде 6 цифр'; return; }
      busy = true; box.classList.add('busy');
      try {
        const r = await fetch('/site/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code }) });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.error || 'Не получилось войти');
        location.replace('/site/');
      } catch (e) {
        errEl.textContent = e.message === 'Failed to fetch' ? 'Нет связи с сервером' : e.message;
        inp.select(); busy = false; box.classList.remove('busy');
      }
    }
    box.querySelector('form').addEventListener('submit', e => { e.preventDefault(); submit(); });
    setTimeout(() => inp.focus(), 300);
  }

  function installHint() {
    try { if (localStorage.getItem('forma-hint')) return; } catch (e) { /* без хранилища */ }
    const ios = /iP(hone|ad|od)/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    const el = document.createElement('div');
    el.className = 'site-hint';
    el.setAttribute('role', 'status');
    el.innerHTML = `<img src="icon-180.png" alt="" width="40" height="40"><div><b>Форма на экран «Домой»</b>
      <span>${ios ? 'Нажми «Поделиться» внизу → «На экран „Домой“»' : 'Меню браузера → «Установить приложение»'}</span></div>
      <button type="button" aria-label="Закрыть">✕</button>`;
    el.querySelector('button').onclick = () => { el.remove(); try { localStorage.setItem('forma-hint', '1'); } catch (e) { /* ничего */ } };
    document.body.appendChild(el);
  }

  fetch('/site/me', { headers: { 'X-Init-Data': '' } })
    .then(r => { if (r.ok) load(); else loginScreen(/[?&]expired=1/.test(location.search)); })
    .catch(() => {
      view.removeAttribute('aria-busy');
      view.innerHTML = '<div class="card"><div class="empty"><span class="e">⚠️</span><b>Нет связи</b>Проверь интернет</div><button class="btn primary" onclick="location.reload()">Повторить</button></div>';
    });
})();
