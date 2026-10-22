// Отправляет текстовый ответ в задание с проверкой учителем (detailedAnswer).
// usage: node horo/answer.js <ссылка на задание> <answer.txt> [файл1 файл2 ...]
// Файлы (фото, pdf) прикрепляются к ответу.
// Каждая строка файла становится отдельным абзацем.
const fs = require('fs');
const { open, close, api, parseUrl, loadTask, htmlToText, EDU, getDraft, clearDraft, draftEmpty } = require('./lib');

const args = process.argv.slice(2);
const replace = args.includes('--replace');  // удалить свой непроверенный ответ и отправить заново
const [url, file, ...attach] = args.filter(a => a !== '--replace');
const lines = fs.readFileSync(file, 'utf8').split('\n').map(s => s.trimEnd()).filter((s, i, a) => s || (i > 0 && a[i - 1]));

(async () => {
  const s = await open();
  const { page } = s;
  try {
    const t = await loadTask(page, url);
    if (t.data.type !== 'detailedAnswer') throw new Error('Это не задание с проверкой учителем, а ' + t.data.type);
    await page.goto(parseUrl(url).taskUrl, { waitUntil: 'networkidle', timeout: 60000 });
    await page.waitForTimeout(2000);
    // платформа бывает медленной (22.10: «21. Lectura» — поле не появилось за 30 с): ещё раз открыть страницу и подождать дольше
    const input = page.locator('[data-testid=messageInput]').first();
    if (!(await input.waitFor({ timeout: 30000 }).then(() => true, () => false))) {
      await page.reload({ waitUntil: 'networkidle', timeout: 90000 }).catch(() => {});
      await input.waitFor({ timeout: 60000 }).catch(() => {
        throw new Error('Сайт не показал поле ответа — платформа тормозит, попробуй отправить ещё раз через пару минут');
      });
    }
    if (replace) {
      const dels = page.locator('[data-testid=deleteAnswerButton]');
      const before = await dels.count();
      if (before) {
        await dels.last().click(); await page.waitForTimeout(1000);
        await page.screenshot({ path: __dirname + '/tmp/delete.png' });
        const yes = page.getByRole('button', { name: /^(Удалить|Да|Подтвердить|Да, удалить)$/ });
        if (await yes.count()) await yes.last().click();
        await page.waitForTimeout(3000);
        if ((await dels.count()) >= before) throw new Error('Не получилось удалить старый ответ (см. horo/tmp/delete.png)');
        console.log('Старый ответ удалён');
      }
    }
    await page.locator('[data-testid=messageInput]').first().click();
    const ed = page.locator('[contenteditable=true].mce-content-body').first();
    // редактор (TinyMCE) платформа грузит отдельно и бывает долго (22.10: 4 ответа подряд — 30 с не хватило): ещё клик и ждём дольше
    if (!(await ed.waitFor({ timeout: 30000 }).then(() => true, () => false))) {
      await page.screenshot({ path: __dirname + '/tmp/editor.png' }).catch(() => {});
      await page.locator('[data-testid=messageInput]').first().click().catch(() => {});
      await ed.waitFor({ timeout: 90000 }).catch(() => {
        throw new Error('Сайт не открыл редактор ответа — платформа тормозит, попробуй ещё раз через пару минут (horo/tmp/editor.png)');
      });
    }
    if (attach.length) {
      // Файлы — по одному: если выбрать сразу несколько, сайт иногда теряет часть (из трёх держался только второй и третий).
      let uploads = 0;
      page.on('response', r => { if (r.request().method() === 'POST' && /horodigital\.ru\/store\/t\/upload/.test(r.url())) { uploads++; console.log('  [загрузка]', r.status(), r.url().split('/api/')[1] || r.url()); } });
      for (let i = 0; i < attach.length; i++) {
        const before = uploads;
        const [chooser] = await Promise.all([
          page.waitForEvent('filechooser', { timeout: 15000 }),
          page.locator('[data-testid=attachButton]').click(),
        ]);
        await chooser.setFiles([attach[i]]);
        for (let k = 0; k < 240 && uploads <= before; k++) await page.waitForTimeout(500);  // платформа бывает медленной: до 2 мин на фото
        await page.waitForTimeout(2500);
      }
      for (let k = 0; k < 240 && uploads < attach.length; k++) await page.waitForTimeout(500);  // «Ответить» до конца загрузки теряет файлы
      await page.waitForTimeout(3000);
      await page.screenshot({ path: __dirname + '/tmp/attach.png' });
      if (uploads < attach.length) console.log('  предупреждение: загрузок видно', uploads, 'из', attach.length, '(см. horo/tmp/attach.png)');
    }
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
    // проверяем, что ответ дошёл: с файлами платформа бывает медленной (ответ появлялся через 10+ с, проверка ошибочно писала «0 из 7»)
    let p, att, last;
    for (let k = 0; k < 20; k++) {
      await page.waitForTimeout(k ? 5000 : 4000);
      p = (await loadTask(page, url)).meta.progress;
      att = await api(page, `${EDU}/progress/${p.uuid}/detailed-answer-attempts`);
      last = att[att.length - 1];
      if (last && last.answer.attachments.length >= attach.length) break;
    }
    console.log(`Статус: ${p.status.type}`);
    console.log('Отправлено:\n' + (last ? htmlToText(last.answer.text) : '(ответ не найден)'));
    if (last) console.log('Прикреплено файлов: ' + last.answer.attachments.length + (last.answer.attachments.length ? ' (' + last.answer.attachments.map(a => a.fileName).join(', ') + ')' : ''));
    // поле ответа на сайте — пустое: сайт сам сохраняет черновик, пока в поле что-то есть, и после отправки
    // в нём оставались те же текст и файлы. Ждём, пока он допишет своё, и чистим (ещё раз, если успел вернуть)
    for (let k = 0; k < 3; k++) {
      await page.waitForTimeout(3000);
      await clearDraft(page, p.uuid).catch(e => console.log('  черновик сайта: ' + e.message.split('\n')[0]));
      await page.waitForTimeout(2000);
      if (draftEmpty(await getDraft(page, p.uuid))) break;
    }
    console.log('Поле ответа на сайте: ' + (draftEmpty(await getDraft(page, p.uuid)) ? 'пустое' : 'НЕ пустое'));
    if (attach.length && (!last || last.answer.attachments.length < attach.length)) {
      // дошли не все картинки — это ошибка, а не «успех»: учитель получит неполный ответ
      throw new Error(`Прикрепилось файлов ${last ? last.answer.attachments.length : 0} из ${attach.length} — ответ неполный, отправь заново с --replace`);
    }
  } finally {
    await close(s);
  }
})().catch(e => { console.error('ОШИБКА:', e.message || e); process.exit(1); });
