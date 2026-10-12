// pm2: запуск «Студии». Секреты берутся из /etc/studio-bot.env (в репозиторий не попадают).
// pm2 start studio/ecosystem.config.js && pm2 save
const fs = require('fs');
const path = require('path');

const env = { PYTHONUNBUFFERED: '1' };
const file = '/etc/studio-bot.env';
if (fs.existsSync(file)) {
  for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
    const m = line.match(/^\s*([A-Z_]+)\s*=\s*(.*?)\s*$/);
    if (m) env[m[1]] = m[2];
  }
}

module.exports = {
  apps: [{
    name: 'studio-bot',
    script: path.join(__dirname, 'studio.py'),
    interpreter: 'python3',
    cwd: path.join(__dirname, '..'),
    env,
    autorestart: true,
    restart_delay: 5000,
  }],
};
