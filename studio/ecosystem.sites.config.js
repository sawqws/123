// pm2: «Студия сайтов» — вторая Студия только для сайтов (сестра Глеба). Секреты — /etc/studio-sites.env.
// Установка: bash studio/install_sites.sh <токен бота>   (pm2-имя studio-sites, порт 8797, адрес https://web.<host>)
const fs = require('fs');
const path = require('path');

const env = {
  PYTHONUNBUFFERED: '1',
  STUDIO_MODE: 'sites',
  STUDIO_PORT: '8797',
  STUDIO_BOTS_DIR: '/srv/studio-sites',
  STUDIO_SITES_DIR: '/srv/studio-sites/sites',
  STUDIO_DATA: '/srv/studio-sites/data',
};
const file = '/etc/studio-sites.env';
if (fs.existsSync(file)) {
  for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
    const m = line.match(/^\s*([A-Z_]+)\s*=\s*(.*?)\s*$/);
    if (m) env[m[1]] = m[2];
  }
}

module.exports = {
  apps: [{
    name: 'studio-sites',
    script: path.join(__dirname, 'studio.py'),
    interpreter: 'python3',
    cwd: '/srv/studio-sites',  // не /root/horo: Claude этой Студии работает только в своей папке
    env,
    autorestart: true,
    restart_delay: 5000,
  }],
};
