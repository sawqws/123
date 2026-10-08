// pm2: запуск бота. Секреты берутся из /etc/horo-bot.env (в репозиторий не попадают).
// pm2 start bot/ecosystem.config.js && pm2 save
const fs = require('fs');
const path = require('path');

const env = { PYTHONUNBUFFERED: '1' };
const file = '/etc/horo-bot.env';
if (fs.existsSync(file)) {
  for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
    const m = line.match(/^\s*([A-Z_]+)\s*=\s*(.*?)\s*$/);
    if (m) env[m[1]] = m[2];
  }
}

module.exports = {
  apps: [{
    name: 'horo-bot',
    script: path.join(__dirname, 'bot.py'),
    interpreter: 'python3',
    cwd: path.join(__dirname, '..'),
    env,
    autorestart: true,     // после /update бот выходит, pm2 запускает его заново
    restart_delay: 5000,
  }],
};
