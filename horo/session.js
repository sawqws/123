// Войти на horodigital.ru (если сессия истекла) и сохранить её в horo/tmp/state.json.
// Зовёт зеркало сайта (bot/mirror.py), когда платформа ответила 401.
const { open, close } = require('./lib');

(async () => {
  const s = await open();
  await close(s);
  console.log('ok');
})().catch(e => { console.error(e.message || e); process.exit(1); });
