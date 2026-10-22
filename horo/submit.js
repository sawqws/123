// Вводит ответы в тест и отправляет его.
// usage: node horo/submit.js <ссылка на задание> <answers.json> [--no-submit] [--defer] [--continue-only]
//
// --defer (или HORO_DEFER=1): ответы вводятся быстро, но тест НЕ сдаётся — бот сдаст его позже сам
//   (Глеб, 15.10: «решить максимально быстро, сдать через какое-то время»); пишется horo/tmp/<задание>/defer.json.
// --continue-only: досдать отложенный тест в той же попытке — новую не начинать («Попробовать ещё раз» не жмём).
//   Платформа сохраняет ответы только при сдаче: у отложенной попытки статус created, ответов 0 и кнопка «Начать»,
//   поэтому ответы вводятся заново. Если попытка уже сдана (недавно), печатает «УЖЕ СДАН» и баллы.
// После сдачи последней строкой: «БАЛЛЫ: 15/17» и «ПОПЫТКИ: 1/3» (сколько использовано из скольких).
//
// answers.json — ответы по номерам вопросов:
//   manualInput:     "1": "ha hecho"
//   blanks:          "2": ["was", "were", "not"]   (пропуски по порядку в тексте: и списки, и поля для ввода)
//   singleSelection: "3": "текст варианта"         (точно как в fetch.js)
//   multipleSelection: "4": ["вариант A", "вариант C"]   (вариант-картинка без текста — его uuid)
//   sequence:        "5": ["первый", "второй", ...]  (тексты вариантов в нужном порядке)
//   singleMatching:  "6": [["левая карточка", "правая карточка"], ...]   (карточка-картинка без текста — её uuid)
//   grouping:        "7": {"Regular": ["текст", ...], "Irregular": [...]}  (лишние не указывать)
const fs = require('fs');
const path = require('path');
const { open, close, loadTask, BASE, TMP } = require('./lib');
const { matchPairs } = require('./matching');

const args = process.argv.slice(2);
const [url, ansFile] = args.filter(a => !a.startsWith('--'));
const noSubmit = args.includes('--no-submit');
const defer = args.includes('--defer') || process.env.HORO_DEFER === '1';
const continueOnly = args.includes('--continue-only');
// «Как человек» теперь не паузами над вопросами (решение шло по 5–6 минут), а отложенной сдачей (--defer):
// попытка начата сейчас, сдана через несколько минут. --fast оставлен для совместимости.
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

    const scores = (task, all) => {
      const done = [...all].filter(a => a.status && a.status.type === 'completed').sort((x, y) => String(x.updatedAt).localeCompare(String(y.updatedAt)));
      const last = done[done.length - 1];
      if (last && last.grade) console.log(`БАЛЛЫ: ${last.grade.scoreValue}/${task.meta.maximumScoreValue}`);
      console.log(`ПОПЫТКИ: ${all.length}/${task.meta.availableAttemptsCount}`);
      return last;
    };
    if (continueOnly) {
      const open = t.attempts.find(a => !a.status || a.status.type !== 'completed');
      if (!open) {
        const last = [...t.attempts].sort((x, y) => String(y.updatedAt).localeCompare(String(x.updatedAt)))[0];
        if (last && Date.now() - Date.parse(last.updatedAt) < 3 * 3600 * 1000) {
          console.log('УЖЕ СДАН: попытка закрыта');
          scores(t, t.attempts);
          return;
        }
        throw new Error('Нет начатой попытки — новую без спроса не начинаю');
      }
    }

    // Попытку создаёт кнопка «Начать» (или «Продолжить», если её уже начали; у начатой без ответов — тоже «Начать»).
    await page.goto(t.taskUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
    const start = page.getByRole('button', { name: continueOnly ? /^(Начать|Продолжить)$/ : /^(Начать|Продолжить|Попробовать ещё раз)$/ });
    await start.first().waitFor({ timeout: 30000 }).catch(() => {});
    if (await start.count()) {
      console.log('Нажимаю:', await start.first().innerText());
      await start.first().click();
      await page.waitForTimeout(3000);
    } else if (continueOnly) {
      throw new Error('Нет кнопки «Начать»/«Продолжить» у начатой попытки');
    } else if (!page.url().includes('/questions/')) {
      throw new Error('Нет кнопки «Начать»: возможно, попытки закончились или тест уже сдан');
    }
    await page.goto(`${t.taskUrl}/questions/${qs[0].uuid}`, { waitUntil: 'networkidle', timeout: 60000 });
    await page.waitForTimeout(2000);

    // на каком вопросе — для живой карточки бота («Сдаю на сайт · вопрос 5 из 12»): паузы идут минутами
    const prog = done => {
      try {
        fs.mkdirSync(path.join(TMP, t.task), { recursive: true });
        fs.writeFileSync(path.join(TMP, t.task, 'submit.json'), JSON.stringify({ n: done, N, at: Date.now() }));
      } catch (e) { /* без карточки сдача всё равно идёт */ }
    };
    for (let n = 1; n <= N; n++) {
      prog(n - 1);
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
        // Варианты ищем по uuid (id у input), отметку видно по классу _checked_ у соседнего span.
        const optText = o => (o.text || (o.description && o.description.text) || '').trim();
        const want = [].concat(a);
        const ids = want.map(w => {
          const o = q.options.find(o => o.uuid === w || optText(o) === w);
          if (!o) throw new Error(`Вопрос ${n}: нет варианта «${w}»`);
          return o.uuid;
        });
        const box = id => view.locator(`[role=button]:has(input[id="${id}"])`);
        const isOn = async id => (await box(id).locator('span[class*="_checked_"]').count()) > 0;
        for (const o of q.options) {
          const need = ids.includes(o.uuid);
          if (need !== (await isOn(o.uuid)) && (need || q.type === 'multipleSelection')) {
            await box(o.uuid).click();
            await page.waitForTimeout(400);
          }
        }
        for (const o of q.options) {
          if (ids.includes(o.uuid) !== (await isOn(o.uuid))) throw new Error(`Вопрос ${n}: отметка у «${optText(o)}» не совпала`);
        }
        console.log(`В${n}: ${want.join(' | ')}`);
      } else if (q.type === 'sequence') {
        // Список react-beautiful-dnd: двигаем клавиатурой (Space, стрелки, Space).
        const sel = '[data-rbd-droppable-id] [data-rbd-draggable-id]';
        const items = async () => (await view.locator(sel).allInnerTexts()).map(x => x.trim());
        for (let i = 0; i < a.length; i++) {
          const cur = (await items()).indexOf(a[i]);
          if (cur < 0) throw new Error(`Вопрос ${n}: нет элемента «${a[i]}»`);
          if (cur === i) continue;
          await view.locator(sel).nth(cur).focus();
          await page.keyboard.press('Space'); await page.waitForTimeout(250);
          for (let k = 0; k < cur - i; k++) { await page.keyboard.press('ArrowUp'); await page.waitForTimeout(250); }
          await page.keyboard.press('Space'); await page.waitForTimeout(600);
        }
        const got = await items();
        if (got.join('\n') !== a.join('\n')) throw new Error(`Вопрос ${n}: порядок не совпал: ${got.join(' | ')}`);
        console.log(`В${n}: ${got.join(' → ')}`);
      } else if (q.type === 'singleMatching') {
        // Пары: клик по левой карточке, затем по правой; уже соединённые (досдача) не трогаем, чужие — переставляем.
        // Карточка-картинка без текста задаётся uuid варианта — на странице ищем её по src картинки.
        const img = w => { const o = q.options.find(o => o.uuid === w); return o && !o.text && o.image ? o.image.location : w; };
        await matchPairs(page, view, n, a.map(p => p.map(img)));
        console.log(`В${n}: ${a.map(([l, r]) => `${l} = ${r}`).join(' | ')}`);
      } else if (q.type === 'grouping') {
        // Группы: клик по элементу, затем по группе.
        const group = g => view.locator('[role=group]').filter({ has: page.locator(`p:text-is(${JSON.stringify(g)})`) }).last();
        const inGroup = async g => (await group(g).locator('[role=listitem]').allInnerTexts()).map(x => x.trim());
        for (const [g, list] of Object.entries(a)) for (const txt of list) {
          if ((await inGroup(g)).includes(txt)) continue;
          await view.locator('[role=listitem]').filter({ hasText: txt }).first().click(); await page.waitForTimeout(500);
          await group(g).click(); await page.waitForTimeout(800);
        }
        for (const [g, list] of Object.entries(a)) {
          const got = await inGroup(g);
          if (got.length !== list.length || !list.every(x => got.includes(x))) throw new Error(`Вопрос ${n}: в группе «${g}» оказалось: ${got.join(' | ')}`);
        }
        console.log(`В${n}: ${Object.entries(a).map(([g, l]) => `${g}: ${l.join(' / ')}`).join(' | ')}`);
      } else {
        throw new Error(`Вопрос ${n}: тип ${q.type} скрипт пока не умеет`);
      }

      if (n < N) {
        await page.click('[data-testid=testFormNextButton]');
        await page.waitForTimeout(2000);
      } else if (noSubmit) {
        console.log('--no-submit: ответы введены, тест не отправлен');
      } else if (defer) {
        fs.writeFileSync(path.join(TMP, t.task, 'defer.json'),
          JSON.stringify({ url: t.taskUrl, answers: path.resolve(ansFile), N, at: Date.now() }));
        console.log(`\nОТЛОЖЕНО: ответы на все ${N} вопросов внесены, тест НЕ сдан — его сдаст бот позже. Больше ничего не запускай.`);
      } else {
        prog(N);
        await page.getByRole('button', { name: 'Отправить на проверку' }).click();
        await page.waitForTimeout(5000);
        await page.waitForLoadState('networkidle').catch(() => {});
        const res = (await page.evaluate(() => document.body.innerText)).split('Только обязательные').pop().trim();
        console.log('\nРЕЗУЛЬТАТ:\n' + res.replace(/\n+/g, '\n').slice(0, 400));
        // баллы и попытки — из API (их читает бот: вторая попытка, если не 100%)
        const after = await loadTask(page, url).catch(() => null);
        if (after) scores(after, after.attempts);
      }
    }
  } finally {
    await close(s);
  }
})().catch(e => { console.error('ОШИБКА:', e.message || e); process.exit(1); });
