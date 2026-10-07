# Deployment checklist — redesigned Mini App

Документ для следующего, отдельно разрешённого deployment. Финальный QA этой
ветки не выполняет SSH/deploy, изменение .env, токенов, DNS или production Caddy.

## До обновления

- Выбрать проверенный commit/ветку после review. Проверить `git status`, branch,
  SHA и все действующие Compose overlays. При dirty tree остановиться: не reset/clean.
- Проверить DNS/TLS API и Mini App, `.env.example` против текущей конфигурации.
  Не менять BOT_TOKEN_ENCRYPTION_KEY и реальные credentials.
- Использовать **все** установленные overlays при каждой Compose операции:
  основной файл, `deploy/miniapp/compose.yml` и дополнительные operator-managed
  overlays для опубликованного marketing site. Последний не входит в базовый Compose.
- Создать timestamp backup PostgreSQL и .env с ограниченными правами. Убедиться,
  что dump непустой и может быть прочитан pg_restore; не выводить .env и не удалять
  прошлые backups. Сохранить прежний SHA, image/static release и Compose/Caddy файлы.

Пример функции для установки с Mini App (добавить текущие operator overlays):

```sh
set -euo pipefail
dc() { docker compose -f docker-compose.yml -f deploy/miniapp/compose.yml "$@"; }
backup_dir="../zapisflow-backups/$(date -u +%Y%m%d_%H%M%S)"
umask 077
mkdir -p "$backup_dir"
dc exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$backup_dir/database.dump"
test -s "$backup_dir/database.dump"
cp -- .env "$backup_dir/env.backup"
git rev-parse HEAD > "$backup_dir/commit.txt"
```

## Код, схема и сборка

1. `git fetch origin`, затем fast-forward только одобренной ветки/commit.
   Сверить SHA; не force push, не destructive rebase.
2. Проверить `dc config -q`, собрать backend/migrate новым Dockerfile.
3. Проверить `dc run --rm migrate alembic current` и `alembic heads`.
   Ожидается одна head `2026_10_05_0029`. Если revision уже head — миграция no-op.
4. Применить `dc run --rm migrate alembic upgrade head`, снова проверить current.
   История не переписывается. Изменения branding: JSON в MasterSettings и
   master_brand_assets; новые tenants не требуются, старые записи сохраняются.
5. Собрать `miniapp` (`miniapp/Dockerfile` делает npm ci/build).
6. Marketing: `npm ci`, `npm test`, `npm run build` в `marketing/`;
   опубликовать `dist/` через существующий static-release механизм оператора.
   Не создавать/включать Web Booking или website OIDC.
7. Перезапустить только изменённые backend/miniapp сервисы после успешной миграции.
   Не перезапускать PostgreSQL/Redis без необходимости, не выполнять down -v.
8. Caddy менять/перезапускать только при изменении proxy/static rules; validate
   до применения. Same-origin `/api/miniapp/*` и `/api/branding/*` должны идти
   в backend, `/b/<public_id>` — в Mini App SPA. Сохранять security/log redaction.
   Для marketing сохранять действующий домен/overlay, не подменять его шаблоном.

Новых branding secrets, filesystem uploads или volumes не требуется: две
нормализованные картинки на проект хранятся в PostgreSQL. Значения Mini App
конфигурации — MINI_APP_BASE_URL, MINI_APP_DOMAIN, MINI_APP_SESSION_SECONDS и
MINI_APP_AUTH_MAX_AGE_SECONDS. Не выводить их вместе с прочими env secrets.

## Health и UAT

- `dc ps`: backend, miniapp, Caddy healthy; миграция завершена успешно.
- `/health/live`: 200 alive; `/health/ready`: 200 database/redis ok.
- Mini App HTML/JS/CSS — HTTPS 200; landing — текущий ZapisFlow.
- На выделенном test tenant: /start, все кнопки, текстовая запись и отмена;
  booking button остаётся callback, системная ZapisFlow кнопка — Mini App.
- Mini App ON → WebApp Menu Button; OFF → Commands и 403 API, text booking работает.
- Telegram iOS/Android/Desktop: initData/session, Client/Master, themes, safe areas,
  клавиатура, BackButton, slow/offline retry, expiry.
- Client: service/staff/date/slot/confirm/success/My bookings/cancel/repeat,
  ручная предоплата и proof без реального перевода.
- Owner: daily calendar, CRM, услуги, staff invite, отдельная дата, horizon,
  payment decision, branding preview/save/reset/logo/cover и profile sync.
- Второй tenant: чужие CRM/appointment/asset mutations недоступны.
  Публичные logo/cover разрешены как business presentation; private assets защищены.
- Проверить backend logs после smoke: новые ERROR/Traceback/MissingGreenlet/
  TelegramBadRequest/unhandled updates требуют разбора и повторной проверки.
  Health 200 не заменяет продуктовый UAT.

## Rollback

Сохранить предыдущие совместимые backend/frontend images и static release.
Возврат кода/статики требует отдельного решения оператора и сверки совместимости
с применённой схемой. Не выполнять автоматический DB downgrade или восстановление
dump поверх новых клиентских данных. Backup не является разрешением удалить данные.
