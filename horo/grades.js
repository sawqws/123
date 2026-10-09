// Оценки по предметам: уровень и оценка по шкале школы, интегральная оценка.
// usage: node horo/grades.js [--html]   (--html: разметка для Telegram, так шлёт бот)
const { open, close, api, EDU } = require('./lib');

// Уровень -> оценка (так считает школа, сказал Глеб): от 3.5 — 5, от 2.5 — 4, от 2.0 — 3, ниже — 2.
// Оценку с сайта (fivePointResult) не берём: она с этим не совпадает (уровень 3.0 сайт показывает как 5).
const STEPS = [[3.5, 5], [2.5, 4], [2.0, 3]];
const grade = lvl => (STEPS.find(([min]) => lvl >= min) || [0, 2])[1];
const next = lvl => [...STEPS].reverse().find(([min]) => lvl < min);

(async () => {
  const s = await open();
  try {
    const disc = Object.fromEntries((await api(s.page, '/api/schools/v1/current/disciplines')).map(d => [d.disciplineId, d.name]));
    const rows = (await api(s.page, `${EDU}/disciplines`))
      .filter(d => d.currentLevel != null)
      .map(d => ({ name: disc[d.disciplineId] || d.disciplineId, lvl: d.currentLevel, five: grade(d.currentLevel), ia: d.integrativeAssessmentTotalResult }))
      .sort((a, b) => a.lvl - b.lvl || a.name.localeCompare(b.name));
    const b = x => (process.argv.includes('--html') ? `<b>${x}</b>` : x);
    const out = [`📊 ${b('Оценки')}`, ''];
    for (const r of rows) {
      const n = next(r.lvl);
      const lvl = Number.isInteger(r.lvl) ? r.lvl + '.0' : r.lvl;
      out.push(`${r.five >= 4 ? '🟢' : r.five === 3 ? '🟡' : '🔴'} ${b(r.name)} — ${r.five}`,
        `      уровень ${lvl}${n ? ` · на ${n[1]} нужен ${n[0].toFixed(1)}` : ' · максимум'}`);
    }
    out.push('', 'Оценка по уровню: от 2.0 — 3, от 2.5 — 4, от 3.5 — 5.');
    console.log(out.join('\n'));
  } finally {
    await close(s);
  }
})().catch(e => { console.error(e.message || e); process.exit(1); });
