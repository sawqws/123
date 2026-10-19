# Сайт «Ритм» (`site/`)

> Справка Claude по этой папке (загружается сама при работе с её файлами). Общие правила — в корневом CLAUDE.md.

- Сайт «Ритм» (привычки в стиле Grit + задачи/цели): исходник `site/ritm.html` (формат артефакта claude.ai, опубликован как https://claude.ai/artifact/Azqh8eJWCmDpzh3X3M54t5, данные в db-хранилище аккаунта). Версия-приложение для iPad (PWA, «На экран Домой»): `python3 site/build_pwa.py` собирает `docs/` (манифест, иконки, офлайн sw.js), публикуется на https://sawqws.github.io/123/ (GitHub Pages из ветки `gh-pages`; её сам обновляет workflow `.github/workflows/pages.yml` при изменении `docs/` в main); там данные в localStorage устройства, копия — «Итоги → Данные». После правки `site/ritm.html` пересобрать `docs/` и переопубликовать артефакт.
