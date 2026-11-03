/* more — экран «Ещё» (как More в WHOOP) и вкладка «Тренер» (как WHOOP Coach). Подключение: window.FormaModules (см. начало app.js). */
(window.FormaModules = window.FormaModules || []).push(F => {
  const { esc, num, big, hm, plural } = F;
  // версия ресурсов (?v=… у скрипта) — внизу экрана «Ещё»
  const VER = (() => { try { const v = new URL(document.currentScript.src).searchParams.get('v') || ''; return /^[0-9a-z]+$/i.test(v) ? v.slice(0, 7) : ''; } catch (e) { return ''; } })();
  const sv = d => F.svgi(d);
  const I = {
    edit: sv('<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>'),
    goal: sv('<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r=".8"/>'),
    sun: sv('<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M5.3 18.7l1.6-1.6M17.1 6.9l1.6-1.6"/>'),
    chat: F.I.coach,
    band: sv('<rect x="7" y="3" width="10" height="18" rx="4"/><path d="M10 10.5h4M10 13.5h2.5"/>'),
    sync: F.I.sync,
    plug: F.I.plug,
    heart: sv('<path d="M12 20s-7.5-4.6-7.5-10.2A4.3 4.3 0 0 1 12 7.2a4.3 4.3 0 0 1 7.5 2.6C19.5 15.4 12 20 12 20z"/>'),
    pulse: sv('<path d="M3 12h4l2-5 4 10 2-5h6"/>'),
    lungs: sv('<path d="M12 4v8M12 12c-1.2-1-2.4-1-3 0M12 12c1.2-1 2.4-1 3 0"/><path d="M8.5 7.5C6 8 4 11.5 4 16c0 2 1 3 2.5 3S9 18 9 16v-5.5M15.5 7.5C18 8 20 11.5 20 16c0 2-1 3-2.5 3S15 18 15 16v-5.5"/>'),
    o2: sv('<circle cx="9" cy="12" r="5"/><path d="M16 15.5c0-1.2 3-1.6 3-3.2 0-.9-.7-1.5-1.5-1.5s-1.4.5-1.5 1.2M16 18.5h3"/>'),
    temp: sv('<path d="M10 14.5V5a2 2 0 0 1 4 0v9.5a4 4 0 1 1-4 0z"/><path d="M12 10v6"/>'),
    zones: sv('<path d="M4 20V15M9 20v-8M14 20V8M19 20V4"/>'),
    target: sv('<circle cx="12" cy="12" r="8.5"/><path d="M12 3.5V7M12 17v3.5M3.5 12H7M17 12h3.5"/><circle cx="12" cy="12" r="2"/>'),
    flame: F.I.flame,
    spark: sv('<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M18.5 15.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/>'),
    moon: F.I.moon, food: F.I.food, bolt: F.I.bolt, run: F.I.run, rec: F.I.today, stats: F.I.stats,
  };

  // ---------- экран «Ещё» ----------
  const row = ({ a, v = '', ic, col, title, meta, end = '', tag = 'button' }) => {
    const attrs = a ? ` data-a="${a}"${v !== '' ? ` data-v="${esc(v)}"` : ''}` : '';
    return `<${tag} class="row${a ? ' tap' : ''}"${attrs}><span class="ic"${col ? ` style="color:${col}"` : ''}>${ic}</span>
      <span class="main"><div class="title">${title}</div>${meta ? `<div class="meta">${meta}</div>` : ''}</span>
      <span class="end">${end}${a ? '<span class="chev"></span>' : ''}</span></${tag}>`;
  };
  const group = (title, rows) => `<div class="more-g rise"><div class="label">${title}</div><div class="list">${rows}</div></div>`;
  // справка: кто открывает свой лист; hrv, rhr, resp, spo2, temp, kcal — готовые расшифровки app.js (F.explain)
  const HELP = [
    ['rec', 'Восстановление', 'насколько ты готов к нагрузке', I.rec, 'var(--rec-green)'],
    ['strain', 'Нагрузка', 'шкала 0–21 за день', I.bolt, 'var(--strain)'],
    ['sleep', 'Сон', 'сколько нужно и выполнение', I.moon, 'var(--sleep)'],
    ['hrv', 'Запас сил', 'насколько отдохнул организм — по пульсу во сне', I.heart, 'var(--rec-blue)'],
    ['rhr', 'Пульс покоя', 'сердце во сне', I.pulse, 'var(--rec-blue)'],
    ['resp', 'Дыхание', 'вдохов в минуту во сне', I.lungs, 'var(--rec-blue)'],
    ['spo2', 'Кислород в крови', 'сколько кислорода в крови ночью', I.o2, 'var(--rec-blue)'],
    ['temp', 'Температура кожи', 'разница с обычной', I.temp, 'var(--rec-blue)'],
    ['kcal', 'Калории', 'траты за день', I.flame, 'var(--food)'],
    ['zones', 'Пульсовые зоны', 'зоны 1–5 на тренировке', I.zones, 'var(--z4)'],
    ['target', 'Цель нагрузки', 'ориентир на день', I.target, 'var(--strain)'],
  ];
  const OWN_HELP = ['rec', 'strain', 'sleep', 'zones', 'target'];

  function bodyStats(d) {
    const w = d.body || {}, p = d.profile || {};
    const h = w.height_meter ? Math.round(w.height_meter * 100) : p.height ? Math.round(Number(p.height)) : null;
    const kg = (w.scale && w.scale.kg) || w.weight_kilogram || (p.weight ? Number(String(p.weight).replace(',', '.')) : null);  // свежий замер весов — первым
    const st = (k, v, u) => `<div class="more-st"><b class="num">${v == null || Number.isNaN(v) ? '—' : `${v}<small>${u}</small>`}</b><span>${k}</span></div>`;
    return `<div class="more-sts">${st('Рост', h, 'см')}${st('Вес', kg ? num(kg, 1).replace(/,0$/, '') : null, 'кг')}${st('Макс. пульс', w.max_heart_rate || null, 'уд/мин')}</div>`;
  }
  function moreScreen(t, d) {
    const p = d.profile || {}, name = ((d.user && d.user.name) || '').trim(), gs = d.goals || [];
    const ini = name ? esc(name[0].toUpperCase()) : F.I.user;
    const who = [p.age ? `${p.age} ${plural(Number(p.age) || 0, 'год', 'года', 'лет')}` : '', p.sport || ''].filter(Boolean).map(esc).join(' · ');
    let h = F.dHead('Ещё', 'профиль и настройки', 'var(--ink)');
    h += `<section class="card more-me rise"><button class="more-head" data-a="more-edit">
        <span class="more-ava">${ini}</span><span class="more-nm"><b>${esc(name || 'Профиль')}</b><span>${who || 'заполни профиль — советы станут точнее'}</span></span>
        <span class="chev"></span></button>${bodyStats(d)}</section>`;
    h += group('Профиль',
      row({ a: 'more-edit', ic: I.edit, title: 'Изменить профиль', meta: 'возраст, рост, вес, спорт, еда' })
      + row({ a: 'more-edit', ic: I.goal, col: 'var(--teal)', title: 'Мои цели', meta: gs.length ? esc(gs[0].text) + (gs.length > 1 ? ` и ещё ${gs.length - 1}` : '') : 'тренер привязывает к ним советы',
        end: `<b class="num">${gs.length}</b>` })
      + (F.slots.moreProfile || []).map(fn => { try { return fn(row) || ''; } catch (e) { console.error('moreProfile', e); return ''; } }).join(''));
    const morning = d.morning === 'off' ? 'выкл' : esc(d.morning || '07:00');
    h += group('Тренер',
      row({ a: 'more-edit', ic: I.sun, col: 'var(--rec-yellow)', title: 'Утренний разбор', meta: d.morning === 'off' ? 'выключен — включи в профиле' : 'план на день после сна', end: `<b class="num">${morning}</b>` })
      + row({ a: 'skin', ic: I.spark, title: 'Вид', meta: 'чёрно-белый iOS или прежний «как WHOOP»', end: `<span>${document.documentElement.classList.contains('wh') ? 'WHOOP' : 'Ч/Б'}</span>` })
      + row({ a: 'tochat', ic: I.chat, title: 'Режим', meta: d.mode === 'chat' ? 'режим чата уже включён' : 'всё кнопками в чате', end: `<span>${d.mode === 'chat' ? 'чат' : 'приложение'}</span>` })
      // сайт в браузере: «На экран „Домой“» — строкой здесь, а не всплывающей плашкой (Глеб 03.11: ничего не всплывает само)
      + (document.body.classList.contains('site') && !document.body.classList.contains('standalone')
        ? row({ a: 'home-hint', ic: F.I.plus, title: 'На экран «Домой»', meta: 'Форма как приложение, без Telegram' }) : ''));
    const synced = d.synced ? new Date(d.synced).toLocaleString('ru', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '';
    h += group('WHOOP',
      row({ tag: 'div', ic: I.band, col: d.connected ? 'var(--teal)' : 'var(--hint)', title: d.connected ? 'Подключён' : 'Не подключён',
        meta: d.connected ? (synced ? `синхронизация ${synced}` : 'данные ещё не загружены') : d.keys ? 'нажми «Подключить» ниже' : 'инструкция — на главной',
        end: `<i class="more-dot" style="background:${d.connected ? 'var(--teal)' : 'var(--rec-red)'}"></i>` })
      + (d.connected ? row({ a: 'sync', ic: I.sync, title: 'Обновить данные', meta: 'загрузить свежее из WHOOP' }) : '')
      + row({ a: 'streak', ic: I.flame, title: 'Серия', meta: d.streak && d.streak.anchored ? 'сверено с WHOOP' : 'дней подряд · сверить с WHOOP', end: `<b class="num">${(d.streak && d.streak.n) || 0}</b>` })
      + (d.keys ? row({ a: 'connect', ic: I.plug, title: d.connected ? 'Подключить заново' : 'Подключить WHOOP', meta: d.connected ? 'если данные перестали приходить' : 'вход через аккаунт WHOOP' })
        : d.connected ? '' : row({ a: 'tab', v: 'today', ic: I.plug, title: 'Как подключить', meta: '3 минуты, один раз' })));
    h += group('Как это работает', HELP.map(([k, title, meta, ic, col]) => row({ a: 'more-help', v: k, ic, col, title, meta })).join(''));
    // самопроверка (Глеб 03.11): обойти все экраны и кнопки на примере данных, найденное — в ремонт; только владельцу
    if (d.admin) h += group('Проверка', row({ a: 'selfcheck', ic: F.I.check, title: 'Проверить приложение', meta: 'все экраны и кнопки — найду и починю' }));
    h += `<div class="more-g rise"><div class="label">Поддержка</div>${Kit.supportRow('/api/support')}</div>`;
    h += `<p class="more-ver">Форма · тренер по данным WHOOP${VER ? ` · версия <span class="num">${esc(VER)}</span>` : ''}</p>`;
    return h;
  }
  F.screens.more = moreScreen;
  F.actions.selfcheck = async el => {  // итог — сообщением в чат (минут 10); чаще раза в 30 мин сервер не даст
    if (el.classList.contains('busy')) return;
    F.haptic('tap'); el.classList.add('busy');
    try { const r = await F.api('/api/selfcheck', {}); F.toast(r.text || 'Проверяю — итог пришлю в чат', 'ok'); } catch (e) { F.fail(e); }
    el.classList.remove('busy');
  };
  F.actions['home-hint'] = () => {
    const ios = /iP(hone|ad|od)/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    F.haptic('tap');
    F.sheet(`<h2>На экран «Домой»</h2><div class="card prose"><p>${ios ? 'Нажми «Поделиться» внизу Safari → «На экран „Домой“».'
      : 'Меню браузера (⋮) → «Установить приложение» или «Добавить на главный экран».'}</p><p>Форма откроется значком, как приложение, без Telegram.</p></div>`);
  };
  // аватар в шапке — экран «Ещё»; «Заполнить профиль» в Питании и др. — сразу лист профиля
  // огонёк серии в капсуле — лист «Серия» (сверка с WHOOP)
  F.actions.profile = (el, v, ev) => ev && ev.target.closest && ev.target.closest('.mepill .fl') ? F.streakSheet()
    : el.classList.contains('avatar') || el.classList.contains('mepill') ? F.openScreen('more') : F.profileSheet();
  F.actions.skin = () => {  // вид: ч/б iOS 26 (по умолчанию, mono.css) ⇄ прежний «как WHOOP» (whoop.css)
    const r = document.documentElement, wh = !r.classList.contains('wh');
    r.classList.toggle('wh', wh); r.classList.toggle('mono', !wh);
    if (wh) r.classList.remove('lt');
    try { localStorage.setItem('forma-skin', wh ? 'whoop' : 'mono'); } catch (e) { /* без хранилища — до перезапуска */ }
    F.theme();
    F.haptic('select'); F.render(false);
  };
  F.actions['more-edit'] = () => {
    F.profileSheet();
    // лист закрылся (сохранили или нет) — обновить цифры на экране: цели меняются без «Сохранить»
    const sh = [...document.querySelectorAll('.sheet')].pop();
    if (!sh) return;
    const mo = new MutationObserver(() => {
      if (sh.isConnected) return;
      mo.disconnect();
      if (F.S.screen === 'more' && !document.querySelector('.sheet')) F.render(false);
    });
    mo.observe(document.body, { childList: true });
  };
  F.actions['more-help'] = (el, v) => OWN_HELP.includes(v) ? helpSheet(v) : F.explain(v);

  // ---------- справка: восстановление, нагрузка, сон, зоны, цель ----------
  const ZW = { green: 'зелёное', yellow: 'жёлтое', red: 'красное' };
  const box = (title, body) => `<div class="label">${title}</div><div class="card prose">${body}</div>`;
  const pair = (k1, v1, d1, k2, v2, d2) => `<div class="grid2">${F.metric(k1, v1, d1)}${F.metric(k2, v2, d2)}</div>`;
  const avgOf = (rows, k) => { const v = rows.map(r => r[k]).filter(x => x != null); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
  function helpSheet(kind) {
    const d = F.S.data, t = F.S.dayData || d.today, r = t.recovery, s = t.sleep, c = t.cycle || {}, b = t.base || {};
    const rows = F.lastRows(30);
    F.haptic('tap');
    let h = '';
    if (kind === 'rec') {
      const g = rows.filter(x => x.zone === 'green').length, y = rows.filter(x => x.zone === 'yellow').length, rd = rows.filter(x => x.zone === 'red').length;
      h = `<h2>Восстановление</h2>${pair('Сегодня', r ? `${r.score}<small>%</small>` : '—', r ? ZW[r.zone] : 'ещё не посчитано',
        'Обычно', b.recovery != null ? `${num(b.recovery)}<small>%</small>` : '—', g + y + rd ? `за 30 дней: ${g} зел. · ${y} жёлт. · ${rd} кр.` : 'нет данных')}
        ${box('Что это', '<p>Насколько организм отдохнул и готов к нагрузке сегодня. WHOOP считает его ночью по запасу сил, пульсу в покое, дыханию и сну.</p><p><b>67–100%</b> — зелёный: можно в полную силу. <b>34–66%</b> — жёлтый: умеренно. <b>0–33%</b> — красный: лёгкий день и отдых.</p>')}
        ${box('Как поднять', '<ul><li>Сон 8–9 часов, отбой в одно время</li><li>После тяжёлого дня — лёгкий</li><li>Вода в течение дня, ужин за 2–3 часа до сна</li></ul>')}`;
    } else if (kind === 'strain') {
      h = `<h2>Нагрузка</h2>${pair('Сегодня', c.strain != null ? num(c.strain, 1) : '—', 'из 21', 'Обычно', b.strain != null ? num(b.strain, 1) : '—', 'за день, 30 дней')}
        ${box('Что это', '<p>Сколько за день потрудились сердце и мышцы, по шкале от 0 до 21. Считается по пульсу: тренировки, ходьба, всё движение.</p><p>Шкала нелинейная: от 10 до 14 добраться легко, от 18 до 21 — очень тяжело.</p>')}
        ${box('Уровни', '<ul><li><b>0–9</b> — лёгкий день</li><li><b>10–13</b> — средний</li><li><b>14–17</b> — тяжёлый</li><li><b>18–21</b> — максимальный</li></ul>')}`;
    } else if (kind === 'sleep') {
      const perf = s ? (s.performance != null ? s.performance : s.need ? Math.round(s.hours / s.need * 100) : null) : null;
      h = `<h2>Сон</h2>${pair('Спал', s ? hm(s.hours) : '—', perf != null ? `выполнение ${perf}%` : '', 'Нужно было', s && s.need ? hm(s.need) : '—', b.sleep != null ? `обычно спишь ${hm(b.sleep)}` : '')}
        ${box('Что это', '<p><b>Нужно сна</b> — сколько требуется именно сегодня: база плюс добавка за вчерашнюю нагрузку и недосып.</p><p><b>Выполнение</b> — сколько процентов от нужного ты поспал: 85% и выше — хорошо.</p><p>Глубокий сон восстанавливает мышцы, REM — голову и память. Подросткам нужно 8–10 часов.</p>')}
        ${box('Как спать лучше', '<ul><li>Ложиться и вставать в одно время, даже в выходные</li><li>За час до сна — без экрана</li><li>Прохладная тёмная комната</li></ul>')}`;
    } else if (kind === 'zones') {
      // границы зон — % от максимального пульса, как в WHOOP: Z1 50–60, Z2 60–70, Z3 70–80, Z4 80–90, Z5 90–100
      const mx = (d.body || {}).max_heart_rate;
      const ZN = [['Зона 1', 50, 60, 'разминка, заминка'], ['Зона 2', 60, 70, 'лёгкое кардио, выносливость'], ['Зона 3', 70, 80, 'темповая работа'], ['Зона 4', 80, 90, 'тяжело: раунды, интервалы'], ['Зона 5', 90, 100, 'максимум, рывки']];
      const ws = (t.workouts || []).filter(w => (w.zones || []).length);
      const tot = [0, 0, 0, 0, 0];
      ws.forEach(w => w.zones.slice(1).forEach((m, i) => { tot[i] += m || 0; }));
      h = `<h2>Пульсовые зоны</h2><div class="card more-zl">${ZN.map(([z, a, bb, what], i) => `<div class="more-zr"><i style="background:var(--z${i + 1})"></i><b>${z}</b>
          <span>${what}</span><em class="num">${mx ? `${Math.round(mx * a / 100)}–${Math.round(mx * bb / 100)}` : `${a}–${bb}%`}</em></div>`).join('')}</div>
        <p class="small muted">${mx ? `По твоему макс. пульсу ${mx} уд/мин из WHOOP.` : 'Проценты от максимального пульса — он появится, когда WHOOP его посчитает.'}${ws.length ? ` За день в зонах: ${tot.map((m, i) => `зона ${i + 1} — ${m}`).join(' · ')} мин.` : ''}</p>
        ${box('Что это', '<p>Зона — насколько близко пульс к твоему максимуму. Чем выше зона, тем сильнее растёт нагрузка дня.</p><p>Для бокса нужны обе: <b>зона 2</b> строит выносливость (дольше держишь темп), <b>зоны 4–5</b> — взрывную работу в раунде.</p>')}
        ${box('Как использовать', '<ul><li>Лёгкий день — держись в зонах 1–2</li><li>Интервалы: работа в зонах 4–5, отдых до зоны 2</li><li>Много зоны 5 подряд несколько дней — восстановление упадёт</li></ul>')}`;
    } else if (kind === 'target') {
      const band = t.band;
      h = `<h2>Цель нагрузки</h2>${pair('Цель сегодня', band ? `${band[0]}–${band[1]}` : '—', r ? `восстановление ${ZW[r.zone]}` : 'нет восстановления', 'Сейчас', c.strain != null ? num(c.strain, 1) : '—', 'нагрузка за день')}
        ${band ? `<section class="card">${F.strainScale(t, c.strain != null ? c.strain : null)}</section>` : ''}
        ${box('Что это', '<p>Ориентир, сколько нагрузки взять сегодня, чтобы прогрессировать и не перетрениться. Зависит от утреннего восстановления.</p><ul><li>Зелёное — <b>14–18</b>: можно тяжёлую тренировку или спарринг</li><li>Жёлтое — <b>10–14</b>: обычная тренировка без рекордов</li><li>Красное — <b>4–10</b>: техника, растяжка, прогулка</li></ul><p class="small muted">Границы — наш расчёт по зонам восстановления: WHOOP через API цель не отдаёт.</p>')}`;
    }
    F.sheet(h);
    F.animate();
  }

  // ---------- вкладка «Тренер» (как WHOOP Coach) ----------
  // подсказки по ситуации дня: восстановление, тренировка, сон, еда
  const S0 = () => F.S.data || {};
  function prompts(t) {
    const r = t.recovery, s = t.sleep, ws = t.workouts || [], e = t.eaten || {}, out = [];
    const add = (text, ic, col, sub) => { if (!out.some(x => x.text === text)) out.push({ text, ic, col, sub }); };
    if (ws.length) add(`Разбери мою тренировку: ${ws[ws.length - 1].sport.toLowerCase()}`, I.bolt, 'var(--strain)', 'нагрузка, пульс, зоны');
    if (r && r.zone === 'green') { add('Можно сегодня спарринг?', I.rec, 'var(--rec-green)', `восстановление ${r.score}%`); add('Как выжать максимум из зелёного дня?', I.spark, 'var(--rec-green)', 'тренировка на полную'); }
    if (r && r.zone === 'yellow') { add('Как тренироваться при жёлтом восстановлении?', I.rec, 'var(--rec-yellow)', `восстановление ${r.score}%`); add('Что сделать, чтобы завтра было зелёное?', I.spark, 'var(--rec-yellow)', 'сон, еда, нагрузка'); }
    if (r && r.zone === 'red') { add('Почему упало восстановление?', I.rec, 'var(--rec-red)', `восстановление ${r.score}%`); add('Что делать в день восстановления?', I.spark, 'var(--rec-red)', 'лёгкая активность'); }
    if (s && s.need && s.hours < s.need * .85) add('Как восполнить недосып?', I.moon, 'var(--sleep)', `спал ${hm(s.hours)} из ${hm(s.need)}`);
    if (ws.length) add('Что съесть после тренировки?', I.food, 'var(--food)', 'белок, углеводы, вода');
    if (!e.count) add('Что съесть сегодня, чтобы хватило сил?', I.food, 'var(--food)', 'еды за день ещё нет');
    if (!r) add('Как понять, готов ли я к тренировке?', I.rec, 'var(--teal)', 'без данных восстановления');
    if (t.bedtime) add('Во сколько лечь сегодня?', I.moon, 'var(--sleep)', `нужно ${hm(t.bedtime.need)} сна`);
    add('План тренировок на неделю', I.stats, 'var(--teal)', 'по нагрузке и восстановлению');
    const sk = S0().ext && S0().ext.skin;  // тренер — по всему: кожа, учёба, настроение
    if (sk && sk.has) add('Что улучшить в уходе за кожей?', I.spark, 'var(--ink)', 'по уходу и разборам фото');
    add('Как улучшить сон?', I.moon, 'var(--sleep)', 'режим и привычки');
    add('Как успевать учёбу и бокс?', I.stats, 'var(--ink)', 'режим дня');
    return out.slice(0, 6);
  }
  function context(t) {
    const r = t.recovery, s = t.sleep, c = t.cycle || {};
    const it = (k, v, col) => `<span class="co-ctx-i"><i style="background:${col}"></i>${k}<b class="num">${v}</b></span>`;
    return `<div class="co-ctx">${it('Восст.', r ? r.score + '%' : '—', r ? F.C[r.zone] : 'var(--hint)')}${it('Сон', s ? hm(s.hours) : '—', 'var(--sleep)')}${it('Нагрузка', c.strain != null ? num(c.strain, 1) : '—', 'var(--strain)')}</div>`;
  }
  // Чат тренера (Глеб 03.11): шапка и поле стоят, прокручиваются только сообщения (.co-scroll); подсказки — по кнопке 💡,
  // сами не всплывают; после 30 мин тишины — новый разговор (сервер: coach.CHAT_IDLE, chat_history).
  const IDLE = 30 * 60 * 1000;
  const who = `<div class="co-who"><span class="co-dot">${I.spark}</span>Тренер</div>`;
  const note = '<p class="co-note">Тренер не врач: при боли, травме, сыпи или плохом самочувствии — к врачу и скажи родителям.</p>';
  I.bulb = sv('<path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-3.6 10.8c.7.6 1.1 1.3 1.1 2.2v.5h5v-.5c0-.9.4-1.6 1.1-2.2A6 6 0 0 0 12 3z"/>');
  function coachView() {
    const S = F.S, t = S.data.today, hist = S.history;
    const name = ((S.data.user && S.data.user.name) || '').trim().split(/\s+/)[0];
    let h = F.hero('Тренер', 'знает твой день — спроси о чём угодно', hist && hist.length ? '<button class="avatar ghost" data-a="clear" aria-label="Новый разговор">✕</button>' : '');
    // вид WHOOP: вместо большого заголовка — строка «Ф Тренер · бета» и «новый разговор» справа (как WHOOP Coach)
    h += `<div class="co-top"><span class="co-beta"><i>Ф</i>Тренер · бета</span>${hist && hist.length ? `<button class="co-new" data-a="clear" aria-label="Новый разговор">${I.edit}</button>` : ''}</div>`;
    h += F.slots.coachTop.map(fn => { try { return fn(t) || ''; } catch (e) { console.error('coachTop', e); return ''; } }).join('');
    if (!hist && S.historyErr) return h + `<div class="co-scroll">${F.retryCard('Разговор не загрузился', 'history-retry')}</div>`;
    if (!hist) return h + '<div class="co-scroll"><div class="chat"><div class="sk msg-sk me"></div><div class="sk msg-sk"></div><div class="sk msg-sk me short"></div></div></div>';
    const ps = prompts(t), empty = !hist.length && !S.asking;
    h += '<div class="co-scroll" role="log">';
    if (empty) {
      // начальный экран как у WHOOP Coach: первое сообщение тренера и готовые вопросы пузырями
      h += `<div class="chat co-chat rise">${who}
        <div class="msg them">${name ? `Привет, ${esc(name)}! ` : ''}Спрашивай о чём угодно: спорт, сон, еда, кожа, учёба, настроение. Я вижу твой сон, восстановление, нагрузку, еду, режим, привычки и уход.${context(t)}</div>
        <div class="co-sugg">${ps.map(p => `<button class="co-s" data-a="suggest" data-v="${esc(p.text)}">${esc(p.text)}</button>`).join('')}</div></div>`;
    } else {
      h += '<div class="chat co-chat">';
      hist.forEach(m => { h += m.role === 'user' ? `<div class="msg me">${esc(m.text)}</div>` : `${who}<div class="msg them prose">${F.md(m.text)}</div>${F.actChips ? F.actChips(m) : ''}`; });
      if (S.asking) h += `<div class="msg me">${esc(S.asking)}</div>${who}<div class="msg them${S.askLive ? ' prose' : ''}">${S.askLive ? F.md(S.askLive) : '<span class="dots"><i></i><i></i><i></i></span>'}</div>`;
      h += '</div>';
    }
    h += note + '</div>';
    // подсказки над полем — только по нажатию 💡 (Глеб 03.11: «не всплывать на пол экрана»); на пустом экране они и так в чате
    const tips = !empty && S.coTips;
    h += `<div class="composer">${tips ? `<div class="chips co-tips">${ps.map(p => `<button class="chip" data-a="suggest" data-v="${esc(p.text)}">${esc(p.text)}</button>`).join('')}</div>` : ''}
      <div class="ask"><button class="send alt" data-a="photo" data-v="ask" aria-label="Фото" ${S.asking ? 'disabled' : ''}>${F.I.cam}</button>
      ${empty ? '' : `<button class="send alt co-tip${tips ? ' on' : ''}" data-a="co-tips" aria-label="Подсказки" aria-pressed="${tips ? 'true' : 'false'}">${I.bulb}</button>`}
      <textarea id="q" rows="1" placeholder="${empty ? 'Спроси о чём угодно' : 'Сообщение'}">${esc(S.qDraft || '')}</textarea>
      <button class="send" data-a="ask" aria-label="Отправить" ${S.asking ? 'disabled' : ''}>↑</button></div></div>`;
    return h;
  }
  F.views.coach = coachView;
  F.actions['co-tips'] = () => { F.haptic('select'); F.S.coTips = !F.S.coTips; F.render(false); };

  // ---------- своя прокрутка сообщений ----------
  const S = F.S, root = document.documentElement, vv = window.visualViewport;
  const coBox = () => document.querySelector('body.tab-coach .co-scroll');
  const toEnd = sc => { if (sc) sc.scrollTop = sc.scrollHeight; };
  S.coBottom = true;
  // пользователь внизу — новые сообщения и ответ потоком докручиваются; прокрутил вверх читать — не дёргаем
  document.addEventListener('scroll', e => {
    const sc = e.target;
    if (sc && sc.classList && sc.classList.contains('co-scroll')) S.coBottom = sc.scrollHeight - sc.scrollTop - sc.clientHeight < 48;
  }, { capture: true, passive: true });
  // область меняет высоту (клавиатура, подсказки, поле в несколько строк) — кто был внизу, остаётся внизу
  const ro = window.ResizeObserver ? new ResizeObserver(() => { if (S.coBottom) toEnd(coBox()); }) : null;
  let seen = null;
  // поле ввода — над меню Формы, а с клавиатурой iOS — прямо над ней (visualViewport): страница целиком не едет
  let maxH = 0, maxW = 0;
  function fit() {
    const coach = document.body.classList.contains('tab-coach');
    const w = window.innerWidth, vh = vv ? vv.height : window.innerHeight, top = vv ? Math.max(0, vv.offsetTop) : 0;
    if (w !== maxW) { maxW = w; maxH = 0; }
    maxH = Math.max(maxH, window.innerHeight, vh);
    const typing = document.activeElement && document.activeElement.id === 'q';
    const kb = coach && typing && vh < maxH - 120;
    document.body.classList.toggle('kb', kb);
    root.style.setProperty('--vv-h', Math.round(vh) + 'px');
    root.style.setProperty('--vv-top', Math.round(top) + 'px');
    const bar = document.querySelector('.tabbar');
    let pad = 16;  // широкий экран: меню слева, внизу только поле
    if (bar && !F.wide() && !kb) { const r = bar.getBoundingClientRect(); pad = Math.max(16, Math.round(window.innerHeight - r.top) + 8); }
    root.style.setProperty('--co-pad', (kb ? 8 : pad) + 'px');
  }
  F.coLayout = force => {
    fit();
    const sc = coBox();
    if (!sc) { seen = null; return; }
    if (sc !== seen) { if (ro) { ro.disconnect(); ro.observe(sc); } seen = sc; S.coBottom = true; toEnd(sc); return; }  // открыли вкладку — последние сообщения
    if (force || S.coBottom) { S.coBottom = true; toEnd(sc); }
  };
  if (vv) { vv.addEventListener('resize', () => F.coLayout()); vv.addEventListener('scroll', fit); }
  addEventListener('resize', () => F.coLayout());
  document.addEventListener('focusin', e => { if (e.target.id === 'q') setTimeout(() => F.coLayout(), 60); });
  document.addEventListener('focusout', e => { if (e.target.id === 'q') setTimeout(() => F.coLayout(), 60); });

  // 30 минут тишины — при открытии вкладки чистый лист (сервер отдаёт то же: /api/history — только текущий разговор)
  F.coFresh = () => {
    const h = S.history;
    if (!h || !h.length || S.asking) return false;
    const at = Date.parse(h[h.length - 1].created || '');
    if (!at || Date.now() - at < IDLE) return false;
    S.history = []; S.coTips = false;
    return true;
  };
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && S.tab === 'coach' && !S.screen && F.coFresh()) F.render(false);
  });
});
