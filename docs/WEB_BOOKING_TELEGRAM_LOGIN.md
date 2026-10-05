# Веб-запись и Telegram Login

Веб-запись — отдельный канал над существующими `BookingService`, `SlotEngine`,
`Appointment`, `Payment` и `PaymentProof`. Mini App использует `initData`, сайт —
OIDC. Для одинакового Telegram ID используется одна запись `User`.

## Telegram / BotFather

Официальный протокол: https://core.telegram.org/bots/telegram-login.

Оператор выбирает один платформенный auth bot. Можно использовать Manager Bot,
если его назначение и настройки Login согласованы; клиентские боты мастеров
для авторизации сайта не используются. Автоматического создания auth bot нет.

В BotFather → нужный bot → Login Widget:

1. Зарегистрировать website origin `https://zapisflow.su`.
2. Зарегистрировать точный redirect URI
   `https://zapisflow.su/api/auth/telegram/callback`.
3. Получить Client ID и Client Secret, добавить только в server environment.
4. Оставить алгоритм подписи **RS256**. Другие алгоритмы намеренно не принимаются.

Flow: authorization code + PKCE S256; scope `openid profile phone`;
`state`, browser binding cookie и `nonce` генерируются сервером.
Токен проверяется по официальным JWKS, issuer, audience, exp, iat и nonce.
Telegram ID берётся из подписанного claim `id`, **не из OIDC `sub`**.
Authorization code и OAuth токены не передаются в frontend bundle/storage.

## Environment

```dotenv
WEB_BOOKING_BASE_URL=https://zapisflow.su
TELEGRAM_LOGIN_CLIENT_ID=
TELEGRAM_LOGIN_CLIENT_SECRET=
TELEGRAM_LOGIN_REDIRECT_URI=https://zapisflow.su/api/auth/telegram/callback
WEB_SESSION_TTL_SECONDS=86400
```

Заполнить ID/secret через защищённый environment. Значения примера не включают
секретов. `MINI_APP_BASE_URL` и `mini_app_enabled` не управляют веб-записью.

Сессии хранятся в Redis: случайный opaque ID, в ключе только SHA-256;
HttpOnly / Secure / SameSite=Lax cookie, TTL, rotation после входа и logout.
Потеря Redis-сессии требует повторного входа, записи в PostgreSQL сохраняются.
Intent хранится вместе с попыткой входа/сессией. Он не резервирует время:
после входа hold повторно проверяет слот обычным SlotEngine.

## URL и API

Страницы: `/book/<bot_public_id>` и `/account/bookings`.
Главная `/` остаётся маркетинговым лендингом.

| Method | Route |
| --- | --- |
| GET | `/api/auth/telegram/login` |
| GET | `/api/auth/telegram/callback` |
| GET | `/api/auth/me` |
| POST | `/api/auth/logout` |
| GET | `/api/web-booking/account/bookings` |
| GET | `/api/web-booking/{public_id}/context` |
| GET | `/api/web-booking/{public_id}/services` |
| GET | `/api/web-booking/{public_id}/staff` |
| GET | `/api/web-booking/{public_id}/availability/calendar` |
| GET | `/api/web-booking/{public_id}/slots` |
| POST | `/api/web-booking/{public_id}/holds` |
| POST | `/api/web-booking/{public_id}/confirm` |
| GET | `/api/web-booking/{public_id}/appointments` |
| POST | `/api/web-booking/{public_id}/appointments/{id}/cancel` |
| GET | `/api/web-booking/{public_id}/appointments/{id}/payment` |
| POST | `/api/web-booking/{public_id}/appointments/{id}/proof` |

Все mutations требуют web-session, точный Origin и `X-CSRF-Token`.
Booking operations требуют UUID `Idempotency-Key`; response ledger записывается
атомарно с бизнес-операцией в существующей таблице `miniapp_operations`.
Ключи различаются по bot/user/UUID; fingerprint включает HTTP path и body.
Frontend не задаёт tenant/user/status/price; дополнительные authoritative поля
отклоняются существующими input contracts.

## Включение владельцем

Миграция `2026_10_05_0028` добавляет только
`bot_instances.web_booking_enabled`, default false. Все существующие и новые
проекты остаются выключенными до явного действия владельца.

Manager Bot → проект → «Запись через сайт» → «Включить».
Публичный каталог доступен только текущему ACTIVE BotInstance, ACTIVE Master
и действующему entitlement. Telegram text booking остаётся отдельным каналом.

## Предоплата и уведомления

Предоплата клиента мастеру остаётся ручной, не SaaS YooKassa billing.
Нормализованный image proof хранится существующим Telegram storage flow,
`PaymentProof` содержит обычные Telegram file IDs. Сайт не предполагает, что
клиент уже начал диалог с ботом мастера: для хранения proof используется чат
владельца с этим же tenant bot. **Владелец должен запустить своего клиентского
бота** до UAT предоплаты. Если этот чат недоступен, upload завершается безопасной
ошибкой; запись не исчезает, требуется связь с мастером.

Уведомления используют существующий Outbox; недоступный клиентский Telegram
чат не откатывает запись. После записи предлагается открыть tenant bot по
`https://t.me/<username>?start=web_booking`, затем пользователь запускает /start.

## Local development

```sh
cd marketing
npm ci
npm run dev
```

Vite проксирует `/api` на `http://127.0.0.1:8000` без смены Origin.
Backend запускается существующим dev workflow проекта.
Реальный Login требует зарегистрированного HTTPS origin: используйте защищённый
dev reverse proxy/HTTPS tunnel, соответствующий environment и BotFather URL.
HTTP localhost не ослабляет Secure cookies или production проверки.
Тесты подменяют verified identity только внутри test fixture; runtime bypass нет.

```sh
pytest -q
python -m compileall app tests
cd marketing
npm test
npm run build
cd ../miniapp
npm test
npm run build
```

## Deployment checklist (не выполняется автоматически)

1. Review/merge feature branch после UAT; backup DB/environment.
2. Добавить server-side env и зарегистрировать Login URLs в BotFather.
3. Собрать marketing static assets, обслуживать landing `/`, SPA fallback
   для `/book/*` и `/account/bookings`.
4. Проксировать **только** `/api/auth/*` и `/api/web-booking/*` к FastAPI
   на **том же website origin**. Redirect URI должен совпадать с website origin:
   это сохраняет browser binding и Secure session cookies при callback.
5. Строго ограничить origins: `https://zapisflow.su`, `https://api.zapisflow.su`,
   `https://app.zapisflow.su`; не использовать wildcard credential CORS.
   В этой реализации frontend использует same-origin proxy и не требует
   credential CORS между этими доменами.
6. Не логировать query string auth callback в CDN/reverse proxy/access logs.
   Backend очищает callback query в Uvicorn scope; proxy также должен скрывать
   code/state. Не логировать Authorization, Cookie, CSRF, token response.
   Для image proof proxy должен пропускать до 8 МБ плюс multipart overhead.
   Rate limits используют `request.client`: настройте доверенную proxy-chain,
   чтобы все посетители не объединялись под одним адресом reverse proxy;
   не доверяйте произвольному клиентскому `X-Forwarded-For`.
7. Выполнить штатную Alembic migration/build без удаления volumes.
8. Health checks и opt-in одного собственного test tenant.
9. Настоящий Login, refresh, logout, запись, слотовая гонка, отмена через другие
   каналы, чек, reminder CTA; отдельно проверить Mini App и текстового бота.
10. Проверить read-only anonymous flow и размеры 320–1440 px в браузере.

Production environment, DNS и Caddy этой задачей автоматически не изменяются.
Успешные автоматические tests не заменяют настоящий Telegram Login UAT.
