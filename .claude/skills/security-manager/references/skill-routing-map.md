# Skill Routing Map — признак архитектуры → sec-* плейбук

Фаза 1. Для каждой области attack-surface map подбери плейбук(и). Один вектор =
одна строка входа для red-агента: `{id, area, file(s), sec_skill, hypothesis}`.
Не все области будут в каждом проекте — бери только релевантные фокусу.

| Признак в коде / архитектуре | Область | sec-* плейбук(и) |
|---|---|---|
| Google / Firebase login, OAuth-редиректы, `signInWith*`, `oauth`, `redirect_uri` | Авторизация | `sec-testing-oauth2-implementation-flaws`, `sec-configuring-oauth2-authorization-flow` |
| JWT (`jsonwebtoken`, `jose`, Firebase ID-token, `verifyIdToken`) | Токены | `sec-testing-jwt-token-security` |
| Кастомный логин, сессии, API-ключи на входе | Аутентификация | `sec-testing-api-authentication-weaknesses` |
| REST/GraphQL/gRPC эндпоинты, роуты API | API-поверхность | `sec-conducting-api-security-testing` |
| `apiKey`, выдача/хранение ключей, вебхуки с секретом | API-ключи | `sec-implementing-api-key-security-controls` |
| Нет лимитов на запросы, брутфорс-риск, публичный API | Rate limiting | `sec-implementing-api-rate-limiting-and-throttling` |
| Возврат объектов по id, `req.body` → модель, mass-assignment | Данные/BOLA | `sec-detecting-broken-object-property-level-authorization` |
| Доступ к ресурсу по id в URL/params, `/users/:id`, `/orders/:id` | IDOR | `sec-exploiting-idor-vulnerabilities` |
| Мультитенант, роли, `isAdmin`, проверки прав | Access control | `sec-testing-for-broken-access-control` |
| Загрузка/скачивание файла по пути, `path.join(req...)`, `sendFile` | Файлы/traversal | `sec-performing-directory-traversal-testing` |
| Загрузка «по URL», `fetch(userUrl)`, импорт медиа/картинки по ссылке | SSRF | `sec-performing-blind-ssrf-exploitation` |
| Рендер пользовательского HTML/markdown, `dangerouslySetInnerHTML`, шаблоны | XSS | `sec-testing-for-xss-vulnerabilities` |
| Парсинг XML, SOAP, SVG-загрузка, `libxml` | XXE | `sec-testing-for-xxe-injection-vulnerabilities` |
| `unserialize`, `pickle`, `JSON` → объект с типами, Laravel, `.NET ViewState` | Десериализация | `sec-exploiting-insecure-deserialization` |
| CORS-заголовки, `Access-Control-Allow-Origin`, `credentials: true` | CORS | `sec-testing-cors-misconfiguration` |
| Отдаёт HTML/страницы, нет CSP/HSTS, cookie без флагов | Заголовки | `sec-performing-security-headers-audit` |
| `.env`, хардкод-ключи, коммиты с секретами | Секреты | `sec-implementing-secret-scanning-with-gitleaks` |
| Долгоживущие сервис-аккаунты, статичные ключи облака/БД | Ротация | `sec-performing-service-account-credential-rotation` |
| `Dockerfile`, `docker-compose`, контейнеры в проде | Docker | `sec-hardening-docker-containers-for-production`, `sec-scanning-docker-images-with-trivy` |
| `daemon.json`, docker socket, привилегированные контейнеры | Docker daemon | `sec-hardening-docker-daemon-configuration` |
| `package.json` / `requirements.txt` с зависимостями | Supply chain | `sec-detecting-malicious-npm-packages`, `sec-detecting-typosquatting-packages-in-npm-pypi` |
| `.github/workflows/*.yml`, CI-секреты | CI/CD | `sec-securing-github-actions-workflows` |
| MCP-сервер, tool-манифесты для агентов | MCP/агенты | `sec-auditing-mcp-servers-for-tool-poisoning`, `sec-securing-agentic-ai-tool-invocation` |
| Агент читает чужой контент (чаты, статьи, URL, картинки) — Маша, Пульс | Prompt-injection | `sec-detecting-indirect-prompt-injection` |
| iOS/Swift-приложение, keychain, локальное хранилище | Мобилка | `sec-performing-ios-app-security-assessment`, `sec-exploiting-insecure-data-storage-in-mobile` |

## Дополняющие (не sec-*, но полезны)
- Общая ревизия «как правильно» на бэке → `ag-backend-security-coder`.
- Фронт (XSS/санитайз/CSP на клиенте) → `ag-frontend-security-coder`.
- API-дизайн-хардненинг → `ag-api-security-best-practices`.
- Финальный проход по диффу фиксов → `security-review`.

## Правила подбора
- **Фокус первичен.** Сначала покрой то, что Гриша просил; остальное — `bonus`.
- Один вектор — один плейбук; если область толстая (напр. «всё API»), режь на
  несколько векторов (по эндпоинту / по типу бага), чтобы red-агенты не дублировались.
- Если под область нет `sec-*` плейбука — red-агент работает от общих принципов
  OWASP (укажи это в его промпте), плюс подтяни `ag-*-security-coder`.
- Пробел проекта: нет защитного «secure file upload» плейбука (валидация
  magic-bytes/AV/presigned) — для загрузок покрывай traversal+SSRF и отметь в
  отчёте как кандидата на отдельный ручной хардненинг.
