// Показывает задание: условие, вопросы, варианты, прошлые попытки, комментарии учителя.
// Только чтение: попытку не создаёт.
// usage: node horo/fetch.js <ссылка на задание>
const fs = require('fs');
const path = require('path');
const { open, close, api, download, loadTask, htmlToText, images, TMP, EDU } = require('./lib');

(async () => {
  const url = process.argv[2];
  const s = await open();
  try {
    const t = await loadTask(s.page, url);
    const d = t.data, m = t.meta, p = m.progress || {}, g = p.grade || {};
    const dir = path.join(TMP, t.task);
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, 'task.json'), JSON.stringify(t, null, 1));

    let imgN = 0;
    const seen = {};
    const saveImages = async h => {
      const out = [];
      for (const src of images(h)) {
        if (src.includes('/api/equations/')) { out.push('формула: ' + decodeURIComponent(src.split('?')[1] || '')); continue; }
        if (seen[src]) { out.push('картинка: ' + seen[src]); continue; }
        const f = (seen[src] = path.join(dir, `img${++imgN}.png`));
        await download(s.page, src, f);
        out.push('картинка: ' + f);
      }
      return out;
    };

    console.log(`ТЕМА: ${t.topicData.title} | срок ${t.topicData.studyPeriod.deadlineDate} +${t.topicData.studyPeriod.extraDeadlineDays} дн.`);
    console.log(`ЗАДАНИЕ: ${d.title}`);
    console.log(`ТИП: ${d.type} | оценка: ${d.evaluationType} | статус: ${p.status && p.status.type}`);
    if (d.type === 'test') console.log(`ЗАЧЁТ: ${m.thresholdScoreValue}/${m.maximumScoreValue} (${d.percentageToPass}%) | попыток: ${g.usedAttemptsCount}/${d.availableAttempts}`);
    const dh = d.description && d.description.htmlText;
    if (dh) { console.log('УСЛОВИЕ:\n' + htmlToText(dh)); (await saveImages(dh)).forEach(x => console.log('  ' + x)); }
    for (const f of d.files || []) console.log('ФАЙЛ:', f.fileName || JSON.stringify(f));

    (d.questions || []).forEach((q, i) => (q._n = i + 1));
    for (const q of d.questions || []) {
      const h = q.header && q.header.htmlText;
      console.log(`\n--- Вопрос ${q._n} [${q.type}]${q.caseSensitive ? ' регистр важен' : ''}`);
      console.log(htmlToText(h));
      (await saveImages(h)).forEach(x => console.log('  ' + x));
      if (q.type === 'blanks') {
        const th = q.text.htmlText;
        console.log('ТЕКСТ: ' + htmlToText(th));
        (await saveImages(th)).forEach(x => console.log('  ' + x));
        const sel = Object.fromEntries((q.selections || []).map(x => [x.uuid, x]));
        const ids = [...th.matchAll(/<input[^>]*id="([^"]+)"/g)].map(x => x[1]);
        ids.forEach((id, k) => {
          const x = sel[id] || {};
          console.log(`  пропуск ${k + 1} [[${id.slice(0, 8)}]]: ` + (x.options ? 'выбор из: ' + x.options.map(o => o.text).join(' | ') : 'ввести текст'));
        });
      }
      const opts = q.options || q.answers || q.variants;
      if (Array.isArray(opts)) opts.forEach((o, k) => console.log(`  вариант ${k + 1}: ${htmlToText(o.text || (o.description && o.description.text) || (o.header && o.header.htmlText) || JSON.stringify(o))}`));
      const rest = Object.keys(q).filter(k => !['header', 'uuid', 'type', 'score', 'caseSensitive', 'text', 'selections', 'penalty', '_n', 'options'].includes(k));
      if (rest.length) console.log('  прочие поля:', JSON.stringify(Object.fromEntries(rest.map(k => [k, q[k]]))).slice(0, 800));
    }

    if (t.attempts.length) {
      console.log('\nПРОШЛЫЕ ПОПЫТКИ:');
      const byId = Object.fromEntries((d.questions || []).map(q => [q.uuid, q._n]));
      for (const a of [...t.attempts].sort((x, y) => x.startedAt.localeCompare(y.startedAt))) {
        console.log(`  ${a.startedAt.slice(0, 16)} ${a.status.type} ${a.grade ? a.grade.scoreValue + ' баллов, ' + a.grade.percentageValue + '%' : ''}`);
        for (const x of a.answers) console.log(`    в${byId[x.questionUuid]}: ${JSON.stringify(x.data)}`);
      }
    }

    if (d.type === 'detailedAnswer' && p.uuid) {
      const da = await api(s.page, `${EDU}/progress/${p.uuid}/detailed-answer-attempts`);
      console.log('\nОТВЕТЫ И КОММЕНТАРИИ УЧИТЕЛЯ:');
      for (const a of da) {
        console.log(`  ${a.createdAt.slice(0, 16)} ${a.status} оценка=${a.review && a.review.grade ? a.review.grade.value : '-'}`);
        console.log(`    ответ: ${htmlToText(a.answer.text)} | файлов: ${a.answer.attachments.length}`);
        if (a.review && a.review.comment) console.log(`    учитель: ${htmlToText(a.review.comment)}`);
      }
    }
    console.log(`\nJSON: ${path.join(dir, 'task.json')}`);
  } finally {
    await close(s);
  }
})().catch(e => { console.error(e.message || e); process.exit(1); });
