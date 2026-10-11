// pm2: запуск «Глаза». Секреты берутся из /etc/eye-bot.env (в репозиторий не попадают).
// pm2 start eye/ecosystem.config.js && pm2 save
const fs = require('fs');
const path = require('path');

const env = { PYTHONUNBUFFERED: '1' };
const file = '/etc/eye-bot.env';
if (fs.existsSync(file)) {
  for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
    const m = line.match(/^\s*([A-Z_]+)\s*=\s*(.*?)\s*$/);
    if (m) env[m[1]] = m[2];
  }
}
// свой venv: Pillow (фото, HEIC) и edge-tts (голос); без него — обычный python3
const venv = path.join(__dirname, '.venv', 'bin', 'python');

module.exports = {
  apps: [{
    name: 'eye-bot',
    script: path.join(__dirname, 'eye.py'),
    interpreter: fs.existsSync(venv) ? venv : 'python3',
    cwd: path.join(__dirname, '..'),
    env,
    autorestart: true,
    restart_delay: 5000,
  }],
};
