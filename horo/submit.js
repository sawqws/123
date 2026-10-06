// Вводит ответы в тест и отправляет его.
// usage: node horo/submit.js <ссылка на задание> <answers.json> [--no-submit]
//
// answers.json — ответы по номерам вопросов:
//   manualInput:     "1": "ha hecho"
//   blanks:          "2": ["was", "were", "not"]   (пропуски по порядку в тексте: и списки, и поля для ввода)
//   singleSelection: "3": "текст варианта"         (точно как в fetch.js)
//   multipleSelection: "4": ["вариант A", "вариант C"]
const fs = require('fs');
const { open, close, loadTask, BASE } = require('./lib');

const [url, ansFile, flag] = process.argv.slice(2);
const noSubmit = flag === '--no-submit';
const ANS = JSON.parse(fs.readFileSync(ansFile, 'utf8'));
const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

(async () => {
  const s = await open();
  const { page } = s;
  try {
    const t = await loadTask(page, url);
    const qs = t.data.questions;
    const N = qs.length;
    if (t.data.type !== 'test') throw new Error('Это не тест, а ' + t.data.type);
    for (let i = 1; i <= N; i++) if (ANS[i] === undefined) throw new Error('Нет ответа на вопрос ' + i);

    page.on('response', async r => {
      const m = r.request().method();
      if (m !== 'GET' && r.url().includes('/test-attempts')) console.log(`  [сайт] ${m} ${r.status()} ${r.url().split('/test-attempts')[1] || '/'}`);
    });

    // Попытку создаёт кнопка «Начать» (или «Продолжить», если её уже начали).
    await page.goto(t.taskUrl, { waitUntil: 'networkidle', timeout: 60000 });
    await page.waitForTimeout(2000);
    const start = page.getByRole('button', { name: /^(Начать|Продолжить)$/ });
    if (await start.count()) {
      console.log('Нажимаю:', await start.first().innerText());
      await start.first().click();
      await page.waitForTimeout(3000);
    } else if (!page.url().includes('/questions/')) {
      throw new Error('Нет кнопки «Начать»: возможно, попытки закончились или тест уже сдан');
    }
    await page.goto(`${t.taskUrl}/questions/${qs[0].uuid}`, { waitUntil: 'networkidle', timeout: 60000 });
    await page.waitForTimeout(2000);

    for (let n = 1; n <= N; n++) {
      const q = qs[n - 1];
      const a = ANS[n];
      const title = (await page.locator('[data-testid=questionTitle]').innerText()).trim();
      if (title !== `Вопрос ${n}/${N}`) throw new Error(`Ожидал «Вопрос ${n}/${N}», на странице «${title}»`);
      const view = page.locator('[data-testid=questionView]');

      if (q.type === 'manualInput') {
        const inp = view.locator('input').first();
        let v = '';
        for (let k = 0; k < 4 && v !== a; k++) { await inp.click(); await inp.fill(a); await page.waitForTimeout(500); v = await inp.inputValue(); }
        if (v !== a) throw new Error(`Вопрос ${n}: поле не приняло ответ`);
        console.log(`В${n}: ${v}`);
      } else if (q.type === 'blanks') {
        const blanks = view.locator('[data-testid=blankLocator]');
        const nb = await blanks.count();
        if (!Array.isArray(a) || nb !== a.length) throw new Error(`Вопрос ${n}: пропусков ${nb}, ответов ${Array.isArray(a) ? a.length : 'не массив'}`);
        const got = [];
        for (let k = 0; k < nb; k++) {
          const b = blanks.nth(k);
          if (await b.evaluate(e => e.tagName === 'INPUT')) {
            let v = '';
            for (let r = 0; r < 4 && v !== a[k]; r++) { await b.click(); await b.fill(a[k]); await page.waitForTimeout(400); v = await b.inputValue(); }
            if (v !== a[k]) throw new Error(`Вопрос ${n}, пропуск ${k + 1}: поле не приняло ответ`);
            got.push(v);
          } else {
            await b.click();
            await page.waitForTimeout(500);
            const opt = page.locator('[class*=_popup_] p[class*=_item_]').filter({ hasText: new RegExp('^\\s*' + esc(a[k]) + '\\s*$') });
            if ((await opt.count()) !== 1) throw new Error(`Вопрос ${n}, пропуск ${k + 1}: нет варианта «${a[k]}» в списке`);
            await opt.click();
            await page.waitForTimeout(400);
            const v = await b.locator('input').first().inputValue();
            if (v !== a[k]) throw new Error(`Вопрос ${n}, пропуск ${k + 1}: выбралось «${v}»`);
            got.push(v);
          }
        }
        console.log(`В${n}: ${got.join(' | ')}`);
      } else if (q.type === 'singleSelection' || q.type === 'multipleSelection') {
        for (const want of [].concat(a)) {
          const el = view.locator('._answers_7mugm_161 *').filter({ hasText: new RegExp('^\\s*' + esc(want) + '\\s*$') }).last();
          if (!(await el.count())) throw new Error(`Вопрос ${n}: нет варианта «${want}»`);
          await el.click();
          await page.waitForTimeout(400);
        }
        await page.screenshot({ path: `${__dirname}/tmp/q${n}.png` });
        console.log(`В${n}: выбрано ${[].concat(a).join(' | ')} (проверь по запросу [сайт] ниже)`);
      } else {
        throw new Error(`Вопрос ${n}: тип ${q.type} скрипт пока не умеет`);
      }

      if (n < N) {
        await page.click('[data-testid=testFormNextButton]');
        await page.waitForTimeout(2000);
      } else if (noSubmit) {
        console.log('--no-submit: ответы введены, тест не отправлен');
      } else {
        await page.getByRole('button', { name: 'Отправить на проверку' }).click();
        await page.waitForTimeout(5000);
        await page.waitForLoadState('networkidle').catch(() => {});
        const res = (await page.evaluate(() => document.body.innerText)).split('Только обязательные').pop().trim();
        console.log('\nРЕЗУЛЬТАТ:\n' + res.replace(/\n+/g, '\n').slice(0, 400));
      }
    }
  } finally {
    await close(s);
  }
})().catch(e => { console.error('ОШИБКА:', e.message || e); process.exit(1); });
