# Пульт — своё приложение удалённого доступа (Windows)

- Основа — открытый RustDesk (AGPL-3.0), версия в `RUSTDESK_REF` (`.github/workflows/pult-app.yml`). Свои изменения — только здесь:
  `brand.py` (иконки, логотипы, фиолетовый акцент, свой ключ подписи custom.txt), `config.json` (имя «Pult», сервер,
  настройки по умолчанию и запреты: без автообновления от RustDesk), `assets/` (иконка `icon.svg` → `node assets/render.js <папка>` → PNG/ICO).
- Имя программы латиницей «Pult»: оно же имя службы, папки `Program Files\Pult\Pult.exe` и скриптов (кириллица там ломает .bat/.vbs).
  По-русски «Пульт» — на логотипе.
- Сборка — workflow `pult-app` (push в pult-app/ или вручную): мост Flutter↔Rust (ubuntu) → сборка Windows (~1 ч) →
  тест установки на windows-2022/2025 (служба Pult, ID, окно, скриншот, удаление) → релиз `pult-app` с `Pult-setup.exe`.
- Ключ подписи custom.txt создаётся на каждую сборку и удаляется; у разных сборок разные ключи — это нормально.
- Свой сервер связи: добавить в `config.json` → `default-settings`: `custom-rendezvous-server`, `relay-server`, `key`
  (открытый ключ hbbs) — тогда 60 к/с и качество выше «Лучшего» (на общих серверах RustDesk сам режет до 30 к/с).
- Установка у Глеба — `docs/pult/pult.bat` (скачивает релиз, ставит тихо, пароль, ID на Рабочий стол).
