// Оценки по предметам: уровень и оценка по шкале школы, интегральная оценка.
// usage: node horo/grades.js [--html]   (--html: разметка для Telegram, так шлёт бот)
const { open, close, api, EDU } = require('./lib');

// Уровень -> оценка (так считает школа, сказал Глеб): от 3.5 — 5, от 2.5 — 4, от 2.0 — 3, ниже — 2.
// Оценку с сайта (fivePointResult) не берём: она с этим не совпадает (уровень 3.0 сайт показывает как 5).
const STEPS = [[3.5, 5], [2.5, 4], [2.0, 3]];
const grade = lvl => (STEPS.find(([min]) => lvl >= min) || [0, 2])[1];
const MAX_LVL = 4;  // шкала уровня для полоски (выше 3.5 — уже «5»)
const BAR = 8;
const next = lvl => [...STEPS].reverse().find(([min]) => lvl < min);

(async () => {
  const s = await open();
  try {
    const disc = Object.fromEntries((await api(s.page, '/api/schools/v1/current/disciplines')).map(d => [d.disciplineId, d.name]));
    const rows = (await api(s.page, `${EDU}/disciplines`))
      .filter(d => d.currentLevel != null)
      .map(d => ({ name: disc[d.disciplineId] || d.disciplineId, lvl: d.currentLevel, five: grade(d.currentLevel), ia: d.integrativeAssessmentTotalResult }))
      .sort((a, b) => a.lvl - b.lvl || a.name.localeCompare(b.name));
    const html = process.argv.includes('--html');
    const b = x => (html ? `<b>${x}</b>` : x);
    const code = x => (html ? `<code>${x}</code>` : x);
    const fmt = x => (Number.isInteger(x) ? x + '.0' : String(x));
    const dot = f => (f >= 4 ? '🟢' : f === 3 ? '🟡' : '🔴');
    const avg = rows.reduce((t, r) => t + r.five, 0) / (rows.length || 1);
    const count = f => rows.filter(r => dot(r.five) === f).length;
    const out = [`📊 ${b('Оценки')}`,
      `Средняя ${b(avg.toFixed(1))} · 🟢 ${count('🟢')} · 🟡 ${count('🟡')} · 🔴 ${count('🔴')}`];
    for (const r of rows) {
      const n = next(r.lvl);
      const cells = Math.max(0, Math.min(BAR, Math.round(r.lvl / MAX_LVL * BAR)));
      out.push('', `${dot(r.five)} ${b(r.name)} — ${b(r.five)}`,
        `${code('▰'.repeat(cells) + '▱'.repeat(BAR - cells))} ${fmt(r.lvl)}${n ? ` · на «${n[1]}» нужно ${n[0].toFixed(1)}` : ' · максимум 🏆'}`);
    }
    out.push('', html ? '<i>Уровень → оценка: от 2.0 — 3, от 2.5 — 4, от 3.5 — 5</i>' : 'Уровень → оценка: от 2.0 — 3, от 2.5 — 4, от 3.5 — 5');
    console.log(out.join('\n'));
  } finally {
    await close(s);
  }
})().catch(e => { console.error(e.message || e); process.exit(1); });
