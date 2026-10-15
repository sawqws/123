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
    ['hrv', 'ВСР', 'вариабельность пульса', I.heart, 'var(--rec-blue)'],
    ['rhr', 'Пульс покоя', 'сердце во сне', I.pulse, 'var(--rec-blue)'],
    ['resp', 'Дыхание', 'вдохов в минуту во сне', I.lungs, 'var(--rec-blue)'],
    ['spo2', 'SpO₂', 'кислород в крови', I.o2, 'var(--rec-blue)'],
    ['temp', 'Температура кожи', 'отклонение от нормы', I.temp, 'var(--rec-blue)'],
    ['kcal', 'Калории', 'траты за день', I.flame, 'var(--food)'],
    ['zones', 'Пульсовые зоны', 'Z1–Z5 на тренировке', I.zones, 'var(--z4)'],
    ['target', 'Цель нагрузки', 'ориентир на день', I.target, 'var(--strain)'],
  ];
  const OWN_HELP = ['rec', 'strain', 'sleep', 'zones', 'target'];

  function bodyStats(d) {
    const w = d.body || {}, p = d.profile || {};
    const h = w.height_meter ? Math.round(w.height_meter * 100) : p.height ? Math.round(Number(p.height)) : null;
    const kg = w.weight_kilogram || (p.weight ? Number(String(p.weight).replace(',', '.')) : null);
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
        end: `<b class="num">${gs.length}</b>` }));
    const morning = d.morning === 'off' ? 'выкл' : esc(d.morning || '07:00');
    h += group('Тренер',
      row({ a: 'more-edit', ic: I.sun, col: 'var(--rec-yellow)', title: 'Утренний разбор', meta: d.morning === 'off' ? 'выключен — включи в профиле' : 'план на день после сна', end: `<b class="num">${morning}</b>` })
      + row({ a: 'tochat', ic: I.chat, title: 'Режим', meta: d.mode === 'chat' ? 'режим чата уже включён' : 'всё кнопками в чате', end: `<span>${d.mode === 'chat' ? 'чат' : 'приложение'}</span>` }));
    const synced = d.synced ? new Date(d.synced).toLocaleString('ru', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '';
    h += group('WHOOP',
      row({ tag: 'div', ic: I.band, col: d.connected ? 'var(--teal)' : 'var(--hint)', title: d.connected ? 'Подключён' : 'Не подключён',
        meta: d.connected ? (synced ? `синхронизация ${synced}` : 'данные ещё не загружены') : d.keys ? 'нажми «Подключить» ниже' : 'инструкция — на главной',
        end: `<i class="more-dot" style="background:${d.connected ? 'var(--teal)' : 'var(--rec-red)'}"></i>` })
      + (d.connected ? row({ a: 'sync', ic: I.sync, title: 'Обновить данные', meta: 'загрузить свежее из WHOOP' }) : '')
      + (d.keys ? row({ a: 'connect', ic: I.plug, title: d.connected ? 'Подключить заново' : 'Подключить WHOOP', meta: d.connected ? 'если данные перестали приходить' : 'вход через аккаунт WHOOP' })
        : d.connected ? '' : row({ a: 'tab', v: 'today', ic: I.plug, title: 'Как подключить', meta: '3 минуты, один раз' })));
    h += group('Как это работает', HELP.map(([k, title, meta, ic, col]) => row({ a: 'more-help', v: k, ic, col, title, meta })).join(''));
    h += `<div class="more-g rise"><div class="label">Поддержка</div>${Kit.supportRow('/api/support')}</div>`;
    h += `<p class="more-ver">Форма · тренер по данным WHOOP${VER ? ` · версия <span class="num">${esc(VER)}</span>` : ''}</p>`;
    return h;
  }
  F.screens.more = moreScreen;
  // аватар в шапке — экран «Ещё»; «Заполнить профиль» в Питании и др. — сразу лист профиля
  F.actions.profile = el => el.classList.contains('avatar') ? F.openScreen('more') : F.profileSheet();
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
        ${box('Что это', '<p>Насколько организм отдохнул и готов к нагрузке сегодня. WHOOP считает его ночью по ВСР, пульсу покоя, дыханию и сну.</p><p><b>67–100%</b> — зелёный: можно в полную силу. <b>34–66%</b> — жёлтый: умеренно. <b>0–33%</b> — красный: лёгкий день и отдых.</p>')}
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
      const ZN = [['Z1', 50, 60, 'разминка, заминка'], ['Z2', 60, 70, 'лёгкое кардио, выносливость'], ['Z3', 70, 80, 'темповая работа'], ['Z4', 80, 90, 'тяжело: раунды, интервалы'], ['Z5', 90, 100, 'максимум, рывки']];
      const ws = (t.workouts || []).filter(w => (w.zones || []).length);
      const tot = [0, 0, 0, 0, 0];
      ws.forEach(w => w.zones.slice(1).forEach((m, i) => { tot[i] += m || 0; }));
      h = `<h2>Пульсовые зоны</h2><div class="card more-zl">${ZN.map(([z, a, bb, what], i) => `<div class="more-zr"><i style="background:var(--z${i + 1})"></i><b>${z}</b>
          <span>${what}</span><em class="num">${mx ? `${Math.round(mx * a / 100)}–${Math.round(mx * bb / 100)}` : `${a}–${bb}%`}</em></div>`).join('')}</div>
        <p class="small muted">${mx ? `По твоему макс. пульсу ${mx} уд/мин из WHOOP.` : 'Проценты от максимального пульса — он появится, когда WHOOP его посчитает.'}${ws.length ? ` За день в зонах: ${tot.map((m, i) => `Z${i + 1} ${m}`).join(' · ')} мин.` : ''}</p>
        ${box('Что это', '<p>Зона — насколько близко пульс к твоему максимуму. Чем выше зона, тем сильнее растёт нагрузка дня.</p><p>Для бокса нужны обе: <b>Z2</b> строит выносливость (дольше держишь темп), <b>Z4–Z5</b> — взрывную работу в раунде.</p>')}
        ${box('Как использовать', '<ul><li>Лёгкий день — держись в Z1–Z2</li><li>Интервалы: работа в Z4–Z5, отдых до Z2</li><li>Много Z5 подряд несколько дней — восстановление упадёт</li></ul>')}`;
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
    add('Как улучшить сон?', I.moon, 'var(--sleep)', 'режим и привычки');
    return out.slice(0, 6);
  }
  function context(t) {
    const r = t.recovery, s = t.sleep, c = t.cycle || {};
    const it = (k, v, col) => `<span class="co-ctx-i"><i style="background:${col}"></i>${k}<b class="num">${v}</b></span>`;
    return `<div class="co-ctx">${it('Восст.', r ? r.score + '%' : '—', r ? F.C[r.zone] : 'var(--hint)')}${it('Сон', s ? hm(s.hours) : '—', 'var(--sleep)')}${it('Нагрузка', c.strain != null ? num(c.strain, 1) : '—', 'var(--strain)')}</div>`;
  }
  function coachView() {
    const S = F.S, t = S.data.today, hist = S.history;
    const name = ((S.data.user && S.data.user.name) || '').trim().split(/\s+/)[0];
    let h = F.hero('Тренер', 'на данных твоего WHOOP', hist && hist.length ? '<button class="avatar ghost" data-a="clear" aria-label="Новый разговор">✕</button>' : '');
    h += F.slots.coachTop.map(fn => { try { return fn(t) || ''; } catch (e) { console.error('coachTop', e); return ''; } }).join('');
    if (!hist && S.historyErr) return h + F.retryCard('Разговор не загрузился', 'history-retry');
    if (!hist) return h + '<div class="chat"><div class="sk msg-sk me"></div><div class="sk msg-sk"></div><div class="sk msg-sk me short"></div></div>';
    const ps = prompts(t), empty = !hist.length && !S.asking;
    if (empty) {
      h += `<section class="co-hello rise"><span class="co-badge">${I.spark}</span><h2>${name ? `Привет, ${esc(name)}` : 'Привет'}</h2>
        <p>Я вижу твой сон, восстановление, нагрузку и еду. Спроси что угодно про тренировки, сон и питание — или выбери вопрос ниже.</p>${context(t)}</section>
        <div class="co-grid rise">${ps.map(p => `<button class="co-card" data-a="suggest" data-v="${esc(p.text)}" style="--c:${p.col}">
          <span class="co-ic">${p.ic}</span><b>${esc(p.text)}</b><small>${esc(p.sub)}</small></button>`).join('')}</div>`;
    } else {
      h += '<div class="chat co-chat">';
      hist.forEach(m => {
        h += m.role === 'user' ? `<div class="msg me">${esc(m.text)}</div>`
          : `<div class="co-who"><span class="co-dot">${I.spark}</span>Тренер</div><div class="msg them prose">${F.md(m.text)}</div>`;
      });
      if (S.asking) h += `<div class="msg me">${esc(S.asking)}</div><div class="co-who"><span class="co-dot">${I.spark}</span>Тренер</div><div class="msg them"><span class="dots"><i></i><i></i><i></i></span></div>`;
      h += '</div>';
    }
    h += `<div class="composer">${empty ? '' : `<div class="chips">${ps.map(p => `<button class="chip" data-a="suggest" data-v="${esc(p.text)}">${esc(p.text)}</button>`).join('')}</div>`}
      <div class="ask"><button class="send alt" data-a="photo" data-v="ask" aria-label="Фото" ${S.asking ? 'disabled' : ''}>${F.I.cam}</button>
      <textarea id="q" rows="1" placeholder="Спроси тренера…">${esc(S.qDraft || '')}</textarea>
      <button class="send" data-a="ask" aria-label="Отправить" ${S.asking ? 'disabled' : ''}>↑</button></div>
      <p class="co-note">Тренер не врач: при боли, травме или плохом самочувствии — к врачу.</p></div>`;
    return h;
  }
  F.views.coach = coachView;
});
