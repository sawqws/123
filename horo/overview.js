// Всё для приложения бота за один вход на сайт: задания, оценки, комментарии учителей.
// usage: node horo/overview.js   -> JSON {tasks, grades, comments, at}
const { open, close, subjIcon } = require('./lib');
const status = require('./status');
const grades = require('./grades');
const comments = require('./comments');

(async () => {
  const s = await open();
  try {
    const disc = await status.disciplines(s.page);
    const out = {
      tasks: await status.collect(s.page, disc),
      grades: await grades.collect(s.page, disc),
      comments: (await comments.collect(s.page, disc)).map(r => ({ ...r, icon: subjIcon(r.subj) })),
      at: Date.now(),
    };
    console.log(JSON.stringify(out));
  } finally {
    await close(s);
  }
})().catch(e => { console.error(e.message || e); process.exit(1); });
