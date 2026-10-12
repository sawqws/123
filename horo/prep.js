// Подготовка к проверочной: что прошли по предметам. Только чтение (GET API): попытки не тратит,
// «Начать» не нажимает, страницы вопросов не открывает.
// usage: node horo/prep.js                  -> JSON: все темы [{uuid, subj, icon, title, start, deadline, tasks}]
//        node horo/prep.js <uuid темы>...   -> JSON: материалы тем [{uuid, title, materials, checks, questions}]
//   materials — теория темы с сайта (раздел «Материалы»), checks — условия итоговых/проверочных заданий темы,
//   questions — вопросы уже сданных тестов темы (как спрашивают на проверочной).
const { open, close, api, htmlToText, subjIcon, EDU } = require('./lib');

const TOPICS = `${EDU}/topics?onlyDebts=false`;
// задания, условие которых подсказывает, что будет на проверочной
const CHECK = /подтвержд|итогов|летучк|подготовк|зач[её]т|контрольн|проверочн|самостоят|test|repaso|revision|повторени/i;
const MAX_MATERIAL = 3500;   // символов теории на один материал
const MAX_TOPIC = 9000;      // и на тему целиком

// формулы на сайте — картинки с LaTeX в адресе (svg.image?… или /api/equations/…?…): оставляем сам LaTeX
const latex = h => (h || '').replace(/<img[^>]*src="[^"]*(?:svg\.image|\/api\/equations\/[^"?]*)\?([^"]+)"[^>]*>/g, (_, f) => {
  let t = f.replace(/&amp;/g, '&');
  try { t = decodeURIComponent(t); } catch (e) { /* уже раскодировано */ }
  return ` $${t.trim()}$ `;
});
const clean = s => htmlToText(latex(s)).replace(/\[img\]/g, '').replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
const cut = (s, n) => (s.length > n ? s.slice(0, n).replace(/\s+\S*$/, '') + ' …' : s);

// Все темы ученика (и прошедшие, и текущие) — для выбора предмета и «что прошли».
async function topics(page, disc) {
  return (await api(page, TOPICS)).map(tp => {
    const subj = disc[tp.disciplineId] || String(tp.disciplineId);
    return {
      uuid: tp.uuid,
      subj,
      icon: subjIcon(subj),
      title: (tp.title || '').trim(),
      start: tp.studyPeriod.startDate,
      deadline: tp.studyPeriod.deadlineDate,
      tasks: tp.tasks.filter(t => !t.hidden).sort((a, b) => (a.order || 0) - (b.order || 0))
        .map(t => ({ uuid: t.uuid, title: t.title.trim(), type: t.type, status: t.progress.status.type })),
    };
  });
}

// Теория, условия итоговых заданий и вопросы сданных тестов по выбранным темам.
async function details(page, uuids) {
  const all = await api(page, TOPICS);
  const out = [];
  for (const uuid of uuids) {
    const tp = all.find(t => t.uuid === uuid);
    if (!tp) continue;
    const r = { uuid, title: (tp.title || '').trim(), materials: [], checks: [], questions: [] };
    let size = 0;
    try {
      const list = (await api(page, `${EDU}/topics/${uuid}/materials`)).sort((a, b) => a.order - b.order);
      for (const m of list) {
        if (size > MAX_TOPIC) break;
        const d = await api(page, `${EDU}/topics/${uuid}/materials/${m.uuid}`);
        const text = cut(clean(d.content && d.content.htmlText), MAX_MATERIAL);
        if (!text) continue;
        size += text.length;
        r.materials.push({ title: (m.title || '').trim(), text });
      }
    } catch (e) { r.error = 'материалы: ' + (e.message || e).split('\n')[0]; }
    for (const t of tp.tasks.filter(x => !x.hidden)) {
      const isCheck = CHECK.test(t.title);
      const doneTest = t.type === 'test' && t.progress.status.type === 'approved';
      if (!isCheck && !(doneTest && r.questions.length < 12)) continue;
      try {
        const d = await api(page, `${EDU}/topics/${uuid}/tasks/${t.uuid}`);  // то же, что fetch.js: только чтение
        const desc = cut(clean(d.description && d.description.htmlText), 700);
        if (isCheck && desc) r.checks.push({ title: t.title.trim(), text: desc });
        if (doneTest) {
          for (const q of (d.questions || []).slice(0, 4)) {
            const h = cut(clean(q.header && q.header.htmlText), 220);
            if (h && r.questions.length < 12) r.questions.push(h);
          }
        }
      } catch (e) { /* одно задание не прочиталось — остальное есть */ }
    }
    out.push(r);
  }
  return out;
}

module.exports = { topics, details };

if (require.main === module) (async () => {
  const uuids = process.argv.slice(2).filter(a => /^[0-9a-f-]{36}$/.test(a));
  const s = await open();
  try {
    if (uuids.length) console.log(JSON.stringify(await details(s.page, uuids)));
    else {
      const { disciplines } = require('./status');
      console.log(JSON.stringify(await topics(s.page, await disciplines(s.page))));
    }
  } finally {
    await close(s);
  }
})().catch(e => { console.error(e.message || e); process.exit(1); });
