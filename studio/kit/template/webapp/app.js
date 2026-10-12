// Мини-приложение «Заметки»: список, добавить, удалить. Общие функции — kit.js.
function render(s) {
  $('count').textContent = s.notes.length || '';
  $('list').innerHTML = s.notes.length
    ? '<div class="list fade">' + s.notes.map((n, i) =>
      `<div class="row"><div class="main"><div class="note-text">${esc(n.text)}</div><div class="meta">${esc(n.at)}</div></div>` +
      `<button class="btn small ghost" data-del="${i}">Удалить</button></div>`).join('') + '</div>'
    : '<div class="empty"><span class="emoji">🗒</span>Пока пусто — напиши первую заметку</div>';
}

async function load() {
  try { render(await api('state')); } catch (e) { toast(e.message, true); }
}

$('add').addEventListener('click', async () => {
  const text = $('text').value.trim();
  if (!text) { toast('Напиши что-нибудь', true); return; }
  try {
    render(await api('add', { text }));
    $('text').value = '';
    haptic('success');
  } catch (e) { toast(e.message, true); }
});

document.addEventListener('click', async (ev) => {
  const b = ev.target.closest('[data-del]');
  if (!b || !(await ask('Удалить заметку?'))) return;
  try { render(await api('delete', { index: Number(b.dataset.del) })); haptic('success'); } catch (e) { toast(e.message, true); }
});

load();
poll(load, 30);
