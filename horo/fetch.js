// Показывает задание: условие, вопросы, варианты, прошлые попытки, комментарии учителя.
// Только чтение: попытку не создаёт.
// usage: node horo/fetch.js <ссылка на задание>
const fs = require('fs');
const path = require('path');
const { open, close, api, download, loadTask, htmlToText, images, TMP, EDU } = require('./lib');

// что Claude может посмотреть в файле: страницы PDF картинками (до 12), текст документа
function fileView(file, ext) {
  const { execFileSync } = require('child_process');
  const base = file.replace(/\.\w+$/, '');
  try {
    if (ext === 'pdf') {
      execFileSync('pdftoppm', ['-r', '110', '-png', '-l', '12', file, base], { timeout: 120000 });
      const dir = path.dirname(file), pre = path.basename(base) + '-';
      return fs.readdirSync(dir).filter(x => x.startsWith(pre) && x.endsWith('.png')).sort().map(x => 'страница: ' + path.join(dir, x));
    }
    if (['png', 'jpg', 'jpeg', 'webp', 'gif'].includes(ext)) return ['картинка: ' + file];
    if (['docx', 'xlsx', 'pptx'].includes(ext)) {  // это zip: текст — из XML внутри, без лишних зависимостей
      const parts = { docx: 'word/document.xml', pptx: 'ppt/slides/slide*.xml', xlsx: 'xl/sharedStrings.xml' }[ext];
      const xml = execFileSync('unzip', ['-p', file, parts], { timeout: 60000, maxBuffer: 50e6 }).toString();
      const text = xml.replace(/<\/(w:p|a:p|si)>/g, '\n').replace(/<[^>]+>/g, '').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
        .replace(/&quot;/g, '"').replace(/&amp;/g, '&').replace(/\n{3,}/g, '\n\n').trim();
      fs.writeFileSync(base + '.txt', text);
      return [`текст файла: ${base}.txt (${text.length} симв.)`];
    }
  } catch (e) { return [`(не открылся: ${e.message.split('\n')[0]})`]; }
  return [];
}

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
    // Файлы задания (вкладка «Файлы»: рабочий лист PDF, картинка, документ) — качаем, PDF режем на страницы-картинки,
    // из docx/xlsx/pptx достаём текст: Claude сам решает, отвечать текстом, на картинке или вернуть заполненный файл.
    let fileN = 0;
    for (const f of d.files || []) {
      const name = f.uploadFileName || f.fileName || '';
      const src = f.location || f.url || f.original;
      const ext = ((name.match(/\.(\w{2,5})$/) || [])[1] || (/pdf/.test(f.contentType || '') ? 'pdf' : /png/.test(f.contentType || '') ? 'png'
        : /jpe?g/.test(f.contentType || '') ? 'jpg' : 'bin')).toLowerCase();
      if (!src) { console.log('ФАЙЛ:', name || JSON.stringify(f)); continue; }
      const out = path.join(dir, `file${++fileN}.${ext}`);
      try { await download(s.page, src, out); } catch (e) { console.log(`ФАЙЛ: ${name} — не скачался (${e.message})`); continue; }
      console.log(`ФАЙЛ: ${name} → ${out}`);
      for (const line of fileView(out, ext)) console.log('  ' + line);
    }

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
      if (Array.isArray(opts)) for (const [k, o] of opts.entries()) {
        const im = o.description && o.description.image && o.description.image.location;
        const txt = htmlToText(o.text || (o.description && o.description.text) || (o.header && o.header.htmlText) || '');
        // Вариант-картинка без текста: сохраняем, в answers.json для него — uuid.
        if (im && !txt) {
          const f = path.join(dir, `opt${q._n}_${k + 1}.png`);
          await download(s.page, im, f);
          console.log(`  вариант ${k + 1} [${o.uuid}]: картинка ${f}`);
        } else console.log(`  вариант ${k + 1}: ${txt || JSON.stringify(o)}`);
      }
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
      // файлы прошлых ответов (его фото/скрины решения) и пометки учителя — чтобы при доработке видеть,
      // что уже сделано и что исправить. Сохраняются как prev<N>_<k>.<ext> и mark<N>_<k>.<ext>
      const ext = f => (/png/i.test(f.contentType || f.fileName || '') ? 'png' : /pdf/i.test(f.contentType || f.fileName || '') ? 'pdf' : 'jpg');
      const grab = async (f, name) => {
        const src = f.original || f.url || f.location || f.thumbnail;
        if (!src) return null;
        const out = path.join(dir, `${name}.${ext(f)}`);
        try { await download(s.page, src, out); return out; } catch (e) { return null; }
      };
      let n = 0;
      for (const a of da) {
        n++;
        console.log(`  ${a.createdAt.slice(0, 16)} ${a.status} оценка=${a.review && a.review.grade ? a.review.grade.value : '-'}`);
        console.log(`    ответ: ${htmlToText(a.answer.text)} | файлов: ${a.answer.attachments.length}`);
        for (const [k, f] of a.answer.attachments.entries()) {
          const got = await grab(f, `prev${n}_${k + 1}`);
          if (got) console.log(`    файл ответа: ${got}`);
        }
        if (a.review && a.review.comment) console.log(`    учитель: ${htmlToText(a.review.comment)}`);
        const marks = [...((a.review && a.review.attachments) || []), ...((a.review && a.review.highlightsReview && a.review.highlightsReview.highlightedImages) || [])];
        for (const [k, f] of marks.entries()) {
          const got = await grab(f, `mark${n}_${k + 1}`);
          if (got) console.log(`    пометки учителя: ${got}`);
        }
      }
    }
    console.log(`\nJSON: ${path.join(dir, 'task.json')}`);
  } finally {
    await close(s);
  }
})().catch(e => { console.error(e.message || e); process.exit(1); });
