// Мини-приложение «Ловец»: имена и слежение, пожелания (автоловля), идеи. Общие функции — kit.js.
let S = null;
const ICON = { watch: '👀', caught: '🎯', fragment: '💎', invalid: '🚫', paused: '⏸' };

function chips(el, items, current, onPick) {  // items: [[значение, подпись]]
  el.innerHTML = items.map(([v, cap]) => `<button class="chip${String(v) === String(current) ? ' on' : ''}" data-v="${esc(v)}">${esc(cap)}</button>`).join('');
  el.onclick = (ev) => { const b = ev.target.closest('.chip'); if (b) { haptic('select'); onPick(b.dataset.v); } };
}

function render(s) {
  S = s;
  const acc = $('acc');
  if (!s.agent) { acc.textContent = 'Секретарь не запущен'; acc.className = 'badge bad'; }
  else if (!s.account) { acc.textContent = 'нет входа в аккаунт'; acc.className = 'badge warn'; }
  else { acc.textContent = '@' + (s.account.username || s.account.name); acc.className = 'badge ok'; }
  $('sub').textContent = s.last ? 'Последнее: ' + s.last : 'Ловлю юзернеймы на твои каналы.';

  // список
  $('list').innerHTML = s.names.length
    ? '<div class="list fade">' + s.names.map((n) => {
      const cap = n.link ? `<a href="${esc(n.link)}">${esc(n.link.replace('https://', ''))}</a>` : (n.note || s.status[n.status] || n.status);
      const end = n.status === 'caught' ? '' : `<button class="btn small ghost" data-rm="${esc(n.name)}">Убрать</button>`;
      return `<div class="row st-${esc(n.status)}"><span class="icon">${ICON[n.status] || '•'}</span><span class="main"><span class="name">@${esc(n.name)}</span><span class="meta">${cap}</span></span><span class="end">${end}</span></div>`;
    }).join('') + (s.names.some((n) => n.status === 'paused') ? '<button class="row" data-resume><span class="icon">▶️</span><span class="main"><span class="name">Снова следить</span></span></button>' : '') + '</div>'
    : '<div class="empty"><span class="emoji">🎯</span>Пока пусто — напиши @имя выше или выбери пожелания</div>';

  // настройки
  chips($('intervals'), s.intervals.map((i) => [i, i + ' с']), s.settings.interval, (v) => save('settings', { interval: Number(v) }));
  const tg = s.settings.target ? [[0, 'Новый канал'], [s.settings.target, s.settings.target_title || 'свой канал']] : [[0, 'Новый канал'], ['pick', 'Свой канал…']];
  chips($('targets'), tg, s.settings.target || 0, (v) => v === 'pick' ? pickChannel() : save('settings', { target: Number(v), target_title: v === '0' ? '' : s.settings.target_title }));

  // пожелания
  const w = s.wish;
  $('wish-text').textContent = s.wish_text;
  chips($('w-len'), s.options.len, w.len, (v) => save('wish', { len: v }));
  chips($('w-style'), s.options.style, w.style, (v) => save('wish', { style: v }));
  chips($('w-count'), s.options.count.map((c) => [c, String(c)]), w.count, (v) => save('wish', { count: Number(v) }));
  if (document.activeElement !== $('w-theme')) $('w-theme').value = w.theme || '';
  $('w-letters').classList.toggle('on', !!w.letters);
  const h = s.hunt;
  $('hunt').textContent = h.on ? '⏹ Остановить' : '🚀 Ловить';
  $('hunt').className = 'btn ' + (h.on ? 'danger' : 'primary');
  const line = h.on ? `<div class="hunt"><span class="dot"></span><span class="txt">Ловлю: проверил ${h.checked}, поймал ${h.caught.length}/${w.count}</span></div>` : '';
  $('hunt-status').innerHTML = line || (h.caught.length ? `<div class="card"><h2>Прошлый раз поймал</h2>${h.caught.map((x) => '@' + esc(x)).join(', ')}</div>` : '');
  $('hunt-line').innerHTML = line;
  $('hunt-line').hidden = !h.on;
}

async function load() {
  try { render(await api('state')); } catch (e) { toast(e.message, true); }
}

async function save(name, body) {
  try { render(await api(name, body)); haptic('tap'); } catch (e) { toast(e.message, true); }
}

async function pickChannel() {
  let chans;
  try { ({ channels: chans } = await api('channels')); } catch (e) { toast(e.message, true); return; }
  if (!chans.length) { toast('У всех твоих каналов уже есть юзернейм — оставлю «новый канал»'); return; }
  const s = sheet(`<h2>На какой канал?</h2><p class="muted">Каналы без юзернейма, где ты создатель:</p><div class="list">${chans.map((c) =>
    `<button class="row" data-ch="${c.id}" data-title="${esc(c.title)}"><span class="icon">📣</span><span class="main"><span class="name">${esc(c.title)}</span></span><span class="end">›</span></button>`).join('')}</div>`);
  s.addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-ch]');
    if (!b) return;
    s.remove();
    save('settings', { target: Number(b.dataset.ch), target_title: b.dataset.title });
  });
}

$('add').addEventListener('click', async () => {
  const text = $('text').value.trim();
  if (!text) { toast('Напиши @имя', true); return; }
  busy(true);
  try {
    const r = await api('add', { text });
    render(r);
    $('text').value = '';
    const caught = r.added.filter((a) => a.status === 'ok');
    toast(caught.length ? '🎯 Поймал: ' + caught.map((a) => '@' + a.name).join(', ') : r.added.map((a) => a.line).join(' · '));
    haptic(caught.length ? 'success' : 'tap');
  } catch (e) { toast(e.message, true); } finally { busy(false); }
});

$('text').addEventListener('keydown', (ev) => { if (ev.key === 'Enter') $('add').click(); });

$('w-letters').addEventListener('click', () => save('wish', { letters: !S.wish.letters }));
$('w-theme').addEventListener('change', () => save('wish', { theme: $('w-theme').value.trim() }));
$('hunt').addEventListener('click', async () => {
  const theme = $('w-theme').value.trim();
  if (S.hunt.on) { if (!(await ask('Остановить ловлю по пожеланиям?'))) return; }
  save('wish', { theme, hunt: S.hunt.on ? 'off' : 'on' });
  if (!S.hunt.on) toast('Ловлю — о пойманных напишу в чат');
});

$('think').addEventListener('click', async () => {
  $('ideas').innerHTML = '<div class="skeleton"></div><div class="skeleton"></div>';
  busy(true);
  try {
    const r = await api('ideas', { theme: $('theme').value.trim() });
    $('ideas').innerHTML = r.ideas.length ? '<div class="list fade">' + r.ideas.map((i) =>
      `<div class="row"><span class="main idea"><span class="name">@${esc(i.name)}</span><span class="mark">${esc(i.mark)}</span></span><span class="end">${
        i.status === 'free' ? `<button class="btn small success" data-grab="${esc(i.name)}">Занять</button>` :
          i.status === 'taken' ? `<button class="btn small ghost" data-watch="${esc(i.name)}">Следить</button>` : ''}</span></div>`).join('') + '</div>'
      : '<div class="empty"><span class="emoji">🤷</span>Ничего не придумал — попробуй другую тему</div>';
  } catch (e) { toast(e.message, true); $('ideas').innerHTML = ''; } finally { busy(false); }
});

document.addEventListener('click', async (ev) => {
  const rm = ev.target.closest('[data-rm]');
  const grab = ev.target.closest('[data-grab]');
  const watch = ev.target.closest('[data-watch]');
  const resume = ev.target.closest('[data-resume]');
  try {
    if (rm) render(await api('remove', { name: rm.dataset.rm }));
    else if (resume) render(await api('resume', {}));
    else if (grab) {
      grab.disabled = true;
      const r = await api('grab', { name: grab.dataset.grab });
      render(r);
      toast(r.result === 'ok' ? '🎯 Поймал @' + grab.dataset.grab : r.result === 'busy' ? 'Уже занимаю — подожди минуту' : 'Не вышло: ' + r.result, r.result !== 'ok');
      if (r.result === 'ok') { haptic('success'); grab.closest('.end').innerHTML = '🎯'; } else grab.disabled = false;
    } else if (watch) {
      const r = await api('add', { text: watch.dataset.watch });
      render(r);
      toast(r.added[0].line);
      watch.closest('.end').innerHTML = '👀';
    }
  } catch (e) { toast(e.message, true); }
});

function busy(on) {
  document.querySelectorAll('.btn.primary').forEach((b) => { b.disabled = on; });
}

tabs($('tabs'), (name) => {
  ['names', 'wish', 'ideas'].forEach((t) => { $('tab-' + t).hidden = t !== name; });
});

load();
poll(load, 10);
