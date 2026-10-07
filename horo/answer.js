// Отправляет текстовый ответ в задание с проверкой учителем (detailedAnswer).
// usage: node horo/answer.js <ссылка на задание> <answer.txt>
// Каждая строка файла становится отдельным абзацем.
const fs = require('fs');
const { open, close, api, parseUrl, loadTask, htmlToText, EDU } = require('./lib');

const [url, file] = process.argv.slice(2);
const lines = fs.readFileSync(file, 'utf8').split('\n').map(s => s.trimEnd()).filter((s, i, a) => s || (i > 0 && a[i - 1]));

(async () => {
  const s = await open();
  const { page } = s;
  try {
    const t = await loadTask(page, url);
    if (t.data.type !== 'detailedAnswer') throw new Error('Это не задание с проверкой учителем, а ' + t.data.type);
    await page.goto(parseUrl(url).taskUrl, { waitUntil: 'networkidle', timeout: 60000 });
    await page.waitForTimeout(2000);
    const ed = page.locator('[contenteditable=true].mce-content-body').first();
    await ed.click();
    // Вставляем через API редактора TinyMCE: при наборе с клавиатуры он сам делает из «1.» списки.
    const esc = x => x.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const html = lines.map(l => `<p>${esc(l) || '&nbsp;'}</p>`).join('');
    const ok = await page.evaluate(h => {
      const e = window.tinymce && (window.tinymce.activeEditor || window.tinymce.editors[0]);
      if (!e) return false;
      e.setContent(h); e.fire('change'); e.fire('input'); e.save && e.save();
      return true;
    }, html);
    if (!ok) throw new Error('Не нашёл редактор TinyMCE на странице');
    await page.keyboard.press('End'); await page.keyboard.type(' '); await page.keyboard.press('Backspace');
    await page.waitForTimeout(500);
    const typed = (await ed.innerText()).replace(/\s+/g, ' ').trim();
    const want = lines.join(' ').replace(/\s+/g, ' ').trim();
    if (typed !== want) throw new Error('В поле оказался не тот текст:\n' + typed.slice(0, 300));
    await page.getByRole('button', { name: 'Ответить' }).click();
    await page.waitForTimeout(4000);
    // проверяем, что ответ дошёл
    const p = (await loadTask(page, url)).meta.progress;
    const att = await api(page, `${EDU}/progress/${p.uuid}/detailed-answer-attempts`);
    const last = att[att.length - 1];
    console.log(`Статус: ${p.status.type}`);
    console.log('Отправлено:\n' + (last ? htmlToText(last.answer.text) : '(ответ не найден)'));
  } finally {
    await close(s);
  }
})().catch(e => { console.error('ОШИБКА:', e.message || e); process.exit(1); });
