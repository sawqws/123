// Список несделанных обязательных заданий (уровни 1–3, как на главной сайта).
// usage: node horo/status.js [--late] [--json]
//   --late  только просроченные
//   --json  вывести список в JSON (для бота)
const { open, close, api, EDU } = require('./lib');

const NAMES = { appointed: 'Пора начать', reworking: 'На доработке', failed: 'Не зачтено' };

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
      rows.push({
        subj: disc[tp.disciplineId] || tp.disciplineId,
        st: NAMES[st],
        auto: t.type === 'test',
        title: t.title.trim(),
        late: tp.studyPeriod.deadlineDate < today,
        dl: tp.studyPeriod.deadlineDate.split('-').reverse().slice(0, 2).join('.'),
        url: `https://horodigital.ru/student/topic/${tp.uuid}/task/${t.uuid}`,
        rawStatus: st,
      });
    }
    if (process.argv.includes('--late')) rows.splice(0, rows.length, ...rows.filter(r => r.late));
    if (process.argv.includes('--json')) { console.log(JSON.stringify(rows)); return; }
    if (!rows.length) { console.log('Ничего не найдено 🎉'); return; }
    rows.sort((a, b) => a.subj.localeCompare(b.subj) || a.st.localeCompare(b.st));
    let subj = '';
    for (const r of rows) {
      if (r.subj !== subj) console.log(`\n${(subj = r.subj)}`);
      console.log(`${r.late ? '❗' : '•'} ${r.title} — ${r.st}, срок ${r.dl}${r.auto ? ', автопроверка' : ''}\n  ${r.url}`);
    }
    const auto = rows.filter(r => r.auto).length;
    console.log(`\nВсего: ${rows.length}, из них с автопроверкой ${auto}, просрочено ${rows.filter(r => r.late).length}.`);
  } finally {
    await close(s);
  }
})().catch(e => { console.error(e.message || e); process.exit(1); });
