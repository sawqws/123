// Задания на доработке и не зачтённые: последний комментарий учителя или баллы теста.
// usage: node horo/comments.js [--html]
//   --html  разметка для Telegram: предметы жирным, названия — ссылки, комментарий цитатой (так шлёт бот)
const { open, close, api, EDU, htmlToText, subjIcon } = require('./lib');

const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

(async () => {
  const html = process.argv.includes('--html');
  const s = await open();
  try {
    const disc = Object.fromEntries((await api(s.page, '/api/schools/v1/current/disciplines')).map(d => [d.disciplineId, d.name]));
    const q = `${EDU}/topics?tasksLevelId[0]=1&tasksLevelId[1]=2&tasksLevelId[2]=3&tasksLevelId[3]=4`
      + '&tasksProgressStatus[0]=reworking&tasksProgressStatus[1]=failed&onlyDebts=false';
    const rows = [];
    for (const tp of await api(s.page, q)) for (const t of tp.tasks) {
      const p = t.progress;
      if (!['reworking', 'failed'].includes(p.status.type)) continue;
      const r = {
        subj: disc[tp.disciplineId] || String(tp.disciplineId),
        title: t.title.trim(),
        failed: p.status.type === 'failed',
        url: `https://horodigital.ru/student/topic/${tp.uuid}/task/${t.uuid}`,
        comment: '', score: null, attempts: '',
      };
      if (t.type === 'detailedAnswer' && p.uuid) {
        const att = await api(s.page, `${EDU}/progress/${p.uuid}/detailed-answer-attempts`);
        const last = att.filter(a => a.review && (a.review.comment || a.review.grade)).pop();
        if (last) {
          r.comment = htmlToText(last.review.comment || '').replace(/\.+$/, '').trim();
          if (last.review.grade && last.review.grade.value != null) r.score = last.review.grade.value;
        }
      } else if (p.grade && p.grade.currentValue != null) {
        r.score = p.grade.currentValue;
        r.attempts = `попыток ${p.grade.usedAttemptsCount}/${t.availableAttemptsCount || '?'}`;
      }
      rows.push(r);
    }
    if (!rows.length) { console.log('🎉 Заданий на доработке нет'); return; }

    const b = x => (html ? `<b>${esc(x)}</b>` : x);
    const out = [`💬 ${b('Комментарии учителей')}`, `${rows.length} на доработке или не зачтено`];
    const bySubj = {};
    for (const r of rows) (bySubj[r.subj] ||= []).push(r);
    for (const subj of Object.keys(bySubj).sort((a, c) => bySubj[c].length - bySubj[a].length || a.localeCompare(c))) {
      out.push('', `${subjIcon(subj)} ${b(subj)} · ${bySubj[subj].length}`);
      for (const r of bySubj[subj]) {
        const meta = [r.score != null ? `${r.score}%` : '', r.attempts].filter(Boolean).join(', ');
        const head = `${r.failed ? '❌' : '🔁'} ${html ? `<a href="${r.url}">${esc(r.title)}</a>` : r.title}${meta ? ` · ${meta}` : ''}`;
        const text = r.comment || (r.score != null ? 'без комментария' : 'комментария нет');
        out.push(head, html ? `<blockquote>${esc(text)}</blockquote>` : `   «${text}»`);
        if (!html) out.push(`   ${r.url}`);
      }
    }
    out.push('', html ? '<i>🔁 на доработке · ❌ не зачтено</i>' : '🔁 на доработке · ❌ не зачтено');
    console.log(out.join('\n'));
  } finally {
    await close(s);
  }
})().catch(e => { console.error(e.message || e); process.exit(1); });
