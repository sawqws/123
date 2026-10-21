// Вопрос singleMatching («сопоставь»): клик по одной карточке, затем по другой — сайт соединяет их линией.
// Соединённые карточки сайт хранит в попытке: при досдаче (--continue-only, «Продолжить») пары уже стоят,
// у них data-testid=chosenSingleMatchingCard, а свободных singleMatchingCard нет (так упала досдача 16.10).
// Клик по соединённой карточке тоже работает: она соединяется с новой, а прежние партнёры освобождаются.
const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const CARD = '[data-testid=singleMatchingCard], [data-testid=chosenSingleMatchingCard]';
const key = (x, y) => [x, y].sort().join('\n');

// Пары на странице: соседи каждой линии singleMatchingLinkLine.
const pairsOf = view => view.locator('[data-testid=singleMatchingLinkLine]').evaluateAll(ls => ls.map(l =>
  [l.previousElementSibling, l.nextElementSibling].map(e => (e ? e.innerText : '').trim())));

async function matchPairs(page, view, n, a, pause = 1) {
  const card = t => view.locator(CARD).filter({ hasText: new RegExp('^\\s*' + esc(t) + '\\s*$') });
  for (const [l, r] of a) {
    if ((await pairsOf(view)).some(([x, y]) => key(x, y) === key(l, r))) continue;
    if ((await card(l).count()) !== 1 || (await card(r).count()) !== 1) throw new Error(`Вопрос ${n}: нет карточки «${l}» или «${r}»`);
    await card(l).click(); await page.waitForTimeout(400 * pause);
    await card(r).click(); await page.waitForTimeout(600 * pause);
  }
  const got = await pairsOf(view);
  const want = new Set(a.map(([l, r]) => key(l, r)));
  if (got.length !== want.size || !got.every(([x, y]) => want.has(key(x, y))))
    throw new Error(`Вопрос ${n}: пары не совпали: ${got.map(p => p.join(' = ')).join(' | ')}`);
}

module.exports = { matchPairs, pairsOf };
