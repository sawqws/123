// Задания на доработке и не зачтённые: последний комментарий учителя или баллы теста.
// usage: node horo/comments.js
const { open, close, api, EDU, htmlToText } = require('./lib');

(async () => {
  const s = await open();
  try {
    const disc = Object.fromEntries((await api(s.page, '/api/schools/v1/current/disciplines')).map(d => [d.disciplineId, d.name]));
    const q = `${EDU}/topics?tasksLevelId[0]=1&tasksLevelId[1]=2&tasksLevelId[2]=3&tasksLevelId[3]=4`
      + '&tasksProgressStatus[0]=reworking&tasksProgressStatus[1]=failed&onlyDebts=false';
    let n = 0;
    for (const tp of await api(s.page, q)) for (const t of tp.tasks) {
      const p = t.progress;
      if (!['reworking', 'failed'].includes(p.status.type)) continue;
      n++;
      let note = '';
      if (t.type === 'detailedAnswer' && p.uuid) {
        const att = await api(s.page, `${EDU}/progress/${p.uuid}/detailed-answer-attempts`);
        const last = att.filter(a => a.review && (a.review.comment || a.review.grade)).pop();
        if (last) note = `учитель: ${htmlToText(last.review.comment) || '(без комментария)'}${last.review.grade && last.review.grade.value != null ? `, оценка ${last.review.grade.value}%` : ''}`;
      } else if (p.grade && p.grade.currentValue != null) {
        note = `тест: ${p.grade.currentValue}%, попыток ${p.grade.usedAttemptsCount}/${t.availableAttemptsCount || '?'}`;
      }
      console.log(`${disc[tp.disciplineId]} — ${t.title.trim()} (${p.status.type === 'failed' ? 'не зачтено' : 'на доработке'})\n  ${note || 'комментария нет'}\n  https://horodigital.ru/student/topic/${tp.uuid}/task/${t.uuid}\n`);
    }
    if (!n) console.log('Заданий на доработке нет 🎉');
  } finally {
    await close(s);
  }
})().catch(e => { console.error(e.message || e); process.exit(1); });
