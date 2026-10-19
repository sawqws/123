# Планер «Ритм» (`planner/`)

> Справка Claude по этой папке (загружается сама при работе с её файлами). Общие правила — в корневом CLAUDE.md.

- Планер «Ритм» (`planner/`, отдельный бот): Mini App + чат, данные в `planner/data/` (вне git), секреты в `/etc/planner-bot.env`, pm2 `planner-bot`. Установка: `curl -fsSL https://raw.githubusercontent.com/sawqws/123/main/planner/install.sh | bash`. Тесты: `python3 planner/test_planner.py`. https для мини-приложения: Caddy (скачивается в `planner/data/caddy/`, порты 80/443, сертификат на `<ip-через-дефисы>.sslip.io` и на IP), запасной — cloudflared (с сервера play2go не заработал); `/status` у владельца. Ссылки на экраны приложения — `?p=/day/YYYY-MM-DD`, `/week`, `/month`, `/goals`, `/goal/<id>`, `/notes`, `/settings` (не в `#`: туда Telegram пишет свои параметры). Приложение на kit (акцент персик, в мелочах), чат: «заметка: …», «цель на месяц: …», /month /notes /settings; видео и кружки — кадры+речь через `media.video` → Claude, как фото.
