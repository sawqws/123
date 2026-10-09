// pm2: запуск агента Telegram. Секреты — в /etc/tg-agent.env (в репозиторий не попадают).
// pm2 start tgbot/ecosystem.config.js && pm2 save
const fs = require('fs');
const path = require('path');

const env = { PYTHONUNBUFFERED: '1' };
const file = '/etc/tg-agent.env';
if (fs.existsSync(file)) {
  for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
    const m = line.match(/^\s*([A-Z_]+)\s*=\s*(.*?)\s*$/);
    if (m) env[m[1]] = m[2];
  }
}

module.exports = {
  apps: [{
    name: 'tg-agent',
    script: path.join(__dirname, 'agent.py'),
    interpreter: path.join(__dirname, '.venv', 'bin', 'python'),
    cwd: __dirname,
    env,
    autorestart: true,     // после /update агент выходит, pm2 запускает его заново
    restart_delay: 5000,
  }],
};
