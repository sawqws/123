// Список несделанных обязательных заданий (уровни 1–3, как на главной сайта).
// usage: node horo/status.js [--late] [--json] [--html]
//   --late  только просроченные
//   --json  вывести список в JSON (для бота)
//   --html  разметка для Telegram: названия заданий — ссылки (так шлёт бот)
const { open, close, api, subjIcon, EDU } = require('./lib');

const NAMES = { appointed: 'Пора начать', reworking: 'На доработке', failed: 'Не зачтено' };
const ICONS = { appointed: '🆕', reworking: '🔁', failed: '❌' };
const DAY = 24 * 3600 * 1000;

const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const ddmm = d => d.split('-').reverse().slice(0, 2).join('.');
const word = (n, one, few, many) => (n % 10 === 1 && n % 100 !== 11 ? one : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? few : many);
const days = n => `${n} ${word(n, 'день', 'дня', 'дней')}`;

function render(rows, onlyLate, html) {
  const b = s => (html ? `<b>${esc(s)}</b>` : s);
  const i = s => (html ? `<i>${esc(s)}</i>` : s);
  const link = r => (html ? `<a href="${r.url}">${esc(r.title)}</a>` : r.title);
  const late = rows.filter(r => r.late).length;
  const out = onlyLate
    ? [`❗ ${b('Просрочки')}`, `${rows.length} ${word(rows.length, 'задание', 'задания', 'заданий')} после дедлайна`]
    : [`📋 ${b('Надо сделать')}`, `${rows.length} ${word(rows.length, 'задание', 'задания', 'заданий')}${late ? ` · ⏰ ${late} просрочено` : ' · всё в срок ✨'}`];

  const bySubj = {};
  for (const r of rows) (bySubj[r.subj] ||= []).push(r);
  const subjects = Object.keys(bySubj).sort((a, c) =>
    bySubj[c].filter(r => r.late).length - bySubj[a].filter(r => r.late).length
    || bySubj[c].length - bySubj[a].length || a.localeCompare(c));

  for (const subj of subjects) {
    const list = bySubj[subj];
    out.push('', `${subjIcon(subj)} ${b(subj)} · ${list.length}`);
    const byTopic = {};
    for (const r of list) (byTopic[r.topicId] ||= []).push(r);
    const topics = Object.values(byTopic).sort((a, c) => a[0].deadline.localeCompare(c[0].deadline));
    const lines = [];
    for (const t of topics) {
      if (topics.length > 1 || t.length > 1) lines.push(i(t[0].topic));
      for (const r of t.sort((a, c) => a.order - c.order)) {
        const when = r.late ? `⏰ ${days(r.lateDays)}` : `до ${r.dl}`;
        lines.push(`${ICONS[r.rawStatus]} ${link(r)} · ${when}${r.auto ? ' ⚡' : ''}`);
        if (!html) lines.push(`   ${r.url}`);
      }
    }
    // В Telegram задания предмета — цитатой; длинный список свёрнут и раскрывается по нажатию
    out.push(html ? `<blockquote${list.length > 5 ? ' expandable' : ''}>${lines.join('\n')}</blockquote>` : lines.join('\n'));
  }
  out.push('', i('🔁 доработка · ❌ не зачтено · 🆕 новое · ⏰ просрочено на · ⚡ сдам сам'));
  return out.join('\n');
}

(async () => {
  const s = await open();
  try {
    const disc = Object.fromEntries((await api(s.page, '/api/schools/v1/current/disciplines')).map(d => [d.disciplineId, d.name]));
    const q = `${EDU}/topics?tasksLevelId[0]=1&tasksLevelId[1]=2&tasksLevelId[2]=3`
      + '&tasksProgressStatus[0]=reworking&tasksProgressStatus[1]=failed&tasksProgressStatus[2]=appointed&onlyDebts=false';
    const today = new Date().toISOString().slice(0, 10);
    const rows = [];
    for (const tp of await api(s.page, q)) for (const t of tp.tasks) {
      const st = t.progress.status.type;
      if (!NAMES[st] || t.requirementType !== 'mandatory') continue;
      const deadline = tp.studyPeriod.deadlineDate;
      rows.push({
        subj: disc[tp.disciplineId] || tp.disciplineId,
        icon: subjIcon(disc[tp.disciplineId] || ''),
        st: NAMES[st],
        auto: t.type === 'test',
        title: t.title.trim(),
        late: deadline < today,
        lateDays: Math.round((Date.parse(today) - Date.parse(deadline)) / DAY),
        dl: ddmm(deadline),
        deadline,
        topic: (tp.title || '').trim(),
        topicId: tp.uuid,
        order: t.order || 0,
        url: `https://horodigital.ru/student/topic/${tp.uuid}/task/${t.uuid}`,
        rawStatus: st,
      });
    }
    const onlyLate = process.argv.includes('--late');
    if (onlyLate) rows.splice(0, rows.length, ...rows.filter(r => r.late));
    if (process.argv.includes('--json')) { console.log(JSON.stringify(rows)); return; }
    if (!rows.length) { console.log(onlyLate ? '🎉 Просрочек нет — всё сдано вовремя' : '🎉 Всё сделано, заданий нет'); return; }
    console.log(render(rows, onlyLate, process.argv.includes('--html')));
  } finally {
    await close(s);
  }
})().catch(e => { console.error(e.message || e); process.exit(1); });
