// Оценки по предметам: текущий уровень, оценка по 5-балльной шкале, интегральная оценка.
// usage: node horo/grades.js
const { open, close, api, EDU } = require('./lib');

(async () => {
  const s = await open();
  try {
    const disc = Object.fromEntries((await api(s.page, '/api/schools/v1/current/disciplines')).map(d => [d.disciplineId, d.name]));
    const rows = (await api(s.page, `${EDU}/disciplines`))
      .filter(d => d.fivePointResult != null)
      .map(d => ({ name: disc[d.disciplineId] || d.disciplineId, five: d.fivePointResult, lvl: d.currentLevel, ia: d.integrativeAssessmentTotalResult }))
      .sort((a, b) => a.five - b.five || a.name.localeCompare(b.name));
    for (const r of rows) console.log(`${r.five >= 4 ? '🟢' : r.five === 3 ? '🟡' : '🔴'} ${r.name}: ${r.five} (уровень ${Number.isInteger(r.lvl) ? r.lvl + '.0' : r.lvl}, ${r.ia}%)`);
    console.log('\nУ предметов без оценки сайт её пока не выставил.');
  } finally {
    await close(s);
  }
})().catch(e => { console.error(e.message || e); process.exit(1); });
