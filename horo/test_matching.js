// Тест вопроса singleMatching без сайта: страница-имитация с той же разметкой, что у horodigital.ru (16.10).
// Клик по карточке делает её активной, клик по второй — соединяет; соединённые можно соединить заново.
// usage: node horo/test_matching.js   (зовёт horo/test_matching.py)
const fs = require('fs');
const assert = require('assert');
const { matchPairs, pairsOf } = require('./matching');
const PW = fs.existsSync('/opt/node-tools/node_modules/playwright') ? '/opt/node-tools/node_modules/playwright' : 'playwright';
const { chromium } = require(PW);

const HTML = `<div data-testid="questionView"><div id="free"></div><div id="rows"></div></div>
<script>
  const PARENTS = ['a. school', "f. greeting the person he's writing to", 'b. opening'];
  const CHILDREN = ['1', '2', '3'];
  let pairs = [], active = null;
  window.clicks = 0;
  window.setPairs = p => { pairs = p; render(); };
  const card = (t, chosen) => '<div role="button" data-testid="' + (chosen ? 'chosenSingleMatchingCard' : 'singleMatchingCard') + '"'
    + (t === active ? ' class="_isActive_"' : '') + ' data-t="' + t.replace(/"/g, '&quot;') + '"><p>' + t + '</p></div>';
  function render() {
    const used = pairs.flat();
    document.getElementById('free').innerHTML = [...PARENTS, ...CHILDREN].filter(t => !used.includes(t)).map(t => card(t, false)).join('');
    document.getElementById('rows').innerHTML = pairs.map(([p, c]) =>
      '<div class="_row_">' + card(p, true) + '<div data-testid="singleMatchingLinkLine"></div>' + card(c, true) + '</div>').join('');
  }
  document.addEventListener('click', e => {
    const el = e.target.closest('[role=button]');
    if (!el) return;
    window.clicks++;
    const t = el.dataset.t;
    if (active === null || active === t) active = t;
    else {
      const p = PARENTS.includes(t) ? t : active, c = PARENTS.includes(t) ? active : t;
      pairs = pairs.filter(([x, y]) => x !== p && y !== c && x !== c && y !== p).concat([[p, c]]);
      active = null;
    }
    render();
  });
  render();
</script>`;

const WANT = [['1', "f. greeting the person he's writing to"], ['2', 'b. opening'], ['3', 'a. school']];
const sorted = ps => ps.map(p => [...p].sort().join('=')).sort();

(async () => {
  const exe = fs.existsSync('/opt/pw-browsers/chromium') ? '/opt/pw-browsers/chromium' : undefined;
  const browser = await chromium.launch({ headless: true, executablePath: exe });
  let page, view;
  const run = async (name, start, check) => {
    page = await browser.newPage();  // своя страница: setContent оставляет прежний window (и счётчик кликов)
    view = page.locator('[data-testid=questionView]');
    await page.setContent(HTML);
    if (start) await page.evaluate(p => window.setPairs(p), start);
    await check();
    await page.close();
    console.log('ok', name);
  };
  try {
    await run('новая попытка: все карточки свободны', null, async () => {
      await matchPairs(page, view, 2, WANT, 0);
      assert.deepStrictEqual(sorted(await pairsOf(view)), sorted(WANT));
    });
    // Досдача: первая (отложенная) попытка уже соединила пары — раньше тут было «нет карточки «1»…».
    await run('досдача: пары уже стоят', [["f. greeting the person he's writing to", '1'], ['b. opening', '2'], ['a. school', '3']], async () => {
      await matchPairs(page, view, 2, WANT, 0);
      assert.strictEqual(await page.evaluate(() => window.clicks), 0);
      assert.deepStrictEqual(sorted(await pairsOf(view)), sorted(WANT));
    });
    await run('досдача: часть пар неверная', [["f. greeting the person he's writing to", '2'], ['a. school', '3']], async () => {
      await matchPairs(page, view, 2, WANT, 0);
      assert.deepStrictEqual(sorted(await pairsOf(view)), sorted(WANT));
    });
    await run('нет такой карточки — понятная ошибка', null, async () => {
      await assert.rejects(matchPairs(page, view, 2, [['9', 'a. school']], 0), /Вопрос 2: нет карточки «9»/);
    });
  } finally {
    await browser.close();
  }
})().catch(e => { console.error('FAIL:', e.message); process.exit(1); });
