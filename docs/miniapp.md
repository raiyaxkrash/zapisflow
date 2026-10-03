# Telegram Mini App ZapisFlow

Один мобильный frontend обслуживает все проекты по адресу
`https://<miniapp-domain>/b/<BotInstance.public_id>`. Он использует существующие
таблицы и сервисы ZapisFlow. Модуль подключается явно; production в рамках
разработки не изменялся. Docker build и реальный Telegram UAT остаются отдельными
обязательными проверками перед включением.

## Архитектура

```text
Telegram WebApp SDK → единый Vite frontend
                     → /api/miniapp на том же origin
                     → FastAPI → доверенный BotInstance → Master
                               → существующие domain services → PostgreSQL
                               → Redis rate limits
                               → Telegram Outbox / существующее хранение чеков
```

От прототипа сохранены визуальное оформление, карточки и светлая/чёрная темы.
Демонстрационные студии, слоты и клиенты в рабочем frontend отсутствуют.
Тестовые данные и подмены Telegram API находятся исключительно в tests.

## Авторизация и tenant

1. Frontend вызывает `Telegram.WebApp.ready()` и `expand()`, получает исходный
   `initData`, viewport, safe area и изменения темы. `initDataUnsafe` не используется.
2. `POST /api/miniapp/auth` принимает публичный UUID бота и исходный `init_data`.
3. Backend проверяет current/status бота и проекта, расшифровывает токен только
   на сервере и проверяет [официальный Telegram HMAC](https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app).
   Допустимый возраст по умолчанию 300 секунд, отклонение в будущее — 30 секунд.
   Дубли ключей и некорректные пользователи отклоняются.
4. User и MasterClient определяются после проверки подписи. Права вычисляет
   `MasterAuthorizationService`, а не frontend.
5. Выдаётся случайная HTTP-only Secure cookie `__Host-zapisflow-miniapp` с
   `SameSite=None`, path `/`, без Domain. В PostgreSQL хранятся только хеши
   session/CSRF/initData credentials. Сессия действует 30 минут без автопродления.
6. Повторное использование подписанного initData без той же действующей cookie
   отклоняется. Изменение URL-кодирования или порядка полей не обходит защиту.
   При повторной авторизации действующей сессии CSRF меняется.
7. Каждый API-запрос включает `X-MiniApp-Bot` открытой страницы. Backend сверяет
   его с ботом сессии; cookie другой вкладки/проекта не переключает tenant молча.
8. Все mutations требуют точного Origin, CSRF и UUID `Idempotency-Key`.
   При каждом запросе заново проверяются статус бота, версия токена и роль.
   Redis outage приводит к безопасному отказу, а не обходу защиты.

Клиент видит свои записи. OWNER/ADMIN управляют текущим проектом. STAFF видит
только свои записи и связанные с ними чеки; CRM, настройки и решения по платежам
доступны OWNER/ADMIN. SaaS billing и Platform Admin в Mini App не добавлялись.

## Подключённые сценарии

**Клиент:** реальные контакты/описание проекта, активные услуги, подходящие
специалисты или «Любой», серверные слоты, временный резерв, телефон и согласие
с условиями, подтверждение, свои записи, продолжение незавершённого резерва,
отмена, реквизиты и загрузка чека.

**Управление:** расписание выбранного дня, карточка записи, чек и решение
по предоплате, ручная запись существующего клиента с необязательным телефоном,
поиск клиентов, история/заметки, создание и изменение услуг, предоплата,
контакты/реквизиты/горизонт записи, сотрудники и привязка услуг,
недельное расписание, перерывы, отдельные рабочие дни и выходные сотрудника.

Новый staff/invite и новый клиент для ручной записи создаются через существующую
Telegram-админку. Общие date overrides проекта отображаются в расписании, но
меняются через Telegram-админку; Mini App редактирует overrides сотрудника.
Branding использует реальные название и описание; отдельной модели logo нет.
Портфолио, отзывы и рассылки сохраняются в Telegram-интерфейсе, отдельные экраны
для них в этом модуле не добавлялись.

Телефон хранится в существующем `User.phone`, а CRM notes — в tenant-scoped
`MasterClient.notes`. Новая параллельная модель профиля не создавалась.
Списки ограничены: клиентские записи/CRM — 100, дневное расписание — 200.

## Booking, транзакции и доставка

`SlotEngine` вычисляет доступность с учётом timezone проекта, staff, horizon,
buffer, перерывов и overrides. Цена, длительность и депозит берутся из Service
в PostgreSQL. Поля tenant/user/price/status из frontend не принимаются.

`BookingService.create_hold_booking(reserve_only=True)` резервирует слот до
подтверждения, включая услуги без депозита. Старое поведение Telegram handlers
сохранено по умолчанию. `confirm_reserved_booking` проверяет право на запись,
пользователя, статус и срок резерва; нулевой депозит подтверждается без Payment.
Положительный депозит использует существующий Payment/PaymentProof flow.
Без обязательных реквизитов создание резерва отклоняется с rollback.
Согласие записывается в Appointment и audit с текущим cancel_policy_hours.

Владелец HTTP-транзакции — function-scope dependency FastAPI. Business mutation,
audit, Outbox и `MiniAppOperation` response ledger фиксируются одним commit
**до отправки HTTP-ответа**. Ошибка до commit откатывает все DB mutations.
Повтор того же ключа/запроса возвращает прежний результат; другой запрос с тем же
ключом отклоняется. Защита от конкуренции: locks существующей BookingService,
PostgreSQL exclusion constraint и UNIQUE scoped operation key.

Повтор после потери HTTP-ответа не создаёт второй business effect. Frontend
сохраняет ключ незавершённого запроса в памяти и предлагает повторить действие.
Уведомления идут через существующий transactional Telegram Outbox.
Telegram delivery остаётся **at-least-once**: crash между приёмом сообщения
Telegram и фиксацией SENT может дать повторное уведомление.

## Два платёжных домена

**Клиентская предоплата:** Appointment → Payment → реквизиты → изображение чека
→ SUBMITTED → OWNER/ADMIN approve/reject через существующий PaymentService.
JPEG/PNG до 8 МБ декодируется, проверяется и перекодируется в JPEG; имя файла
фиксированное, метаданные/посторонние байты удаляются. Размер и число пикселей
ограничены. PDF/исполняемые файлы не принимаются.

Файл передаётся в тот же клиентский Telegram-чат через BotRegistry; БД хранит
существующие Telegram file IDs. Доступ к загрузке/скачиванию проверяется по
tenant и пользователю/назначенному staff. Raw token и Telegram file URL в API
не выдаются. Crash после sendDocument до commit может оставить лишнее медиа
в Telegram; DB не получает двойную запись/платёж. Обычный повтор одного
idempotency key не загружает файл второй раз.

**SaaS billing:** существующая подписка ZapisFlow/YooKassa не изменялась.
У клиентского Payment сейчас нет online acquiring adapter, поэтому backend
возвращает `online_payment_available=false`, не выдаёт фиктивную checkout URL
и не показывает неработающую кнопку. Mini App не связан с названием provider.

## API

Все пути имеют префикс `/api/miniapp`. Входы используют строгие Pydantic
allow-lists; основные service/staff/slot/appointment ответы имеют OpenAPI schemas.

| Методы и пути | Назначение |
|---|---|
| POST `/auth`, GET `/context` | Авторизация, проект и capabilities |
| GET `/client/services`, `/client/staff`, `/client/slots` | Выбор и доступность |
| POST `/client/holds`, `/client/appointments` | Резерв и подтверждение |
| GET `/client/appointments` | Собственные записи |
| POST `/client/appointments/{id}/cancel` | Отмена |
| GET `/client/appointments/{id}/payment` | Реквизиты и статус |
| POST `/client/appointments/{id}/proof` | Multipart JPEG/PNG |
| GET `/proofs/{id}` | Защищённое скачивание |
| GET `/master/appointments`, `/master/appointments/{id}` | Расписание/карточка |
| POST `/master/appointments`, `/master/payments/{id}/decision` | Ручная запись/решение |
| GET `/master/clients`, GET/PATCH `/master/clients/{id}` | CRM |
| GET/PATCH `/master/settings` | Настройки |
| GET/POST `/master/services`, PUT `/master/services/{id}` | Услуги |
| GET `/master/staff`, PUT `/master/staff/{id}` | Команда/услуги |
| GET/PUT `/master/schedule` | Расписание и overrides |

## Миграция

Новая `2026_10_03_0025` после 0024 добавляет только `miniapp_sessions` и
`miniapp_operations`, FK, expiry index и UNIQUE ограничения. Старые миграции
и финансовые/booking данные не переписываются. Full test использует настоящие
PostgreSQL locks, отдельные sessions и fresh migrated disposable Mini App DB.

## Настройка и сборка

Ниже подготовленные команды для тестового/будущего окружения — это не отчёт
о выполненном production deployment.

```dotenv
MINI_APP_BASE_URL=https://app.example.com
MINI_APP_DOMAIN=app.example.com
MINI_APP_SESSION_SECONDS=1800
MINI_APP_AUTH_MAX_AGE_SECONDS=300
```

`MINI_APP_BASE_URL` по умолчанию пуст: прежний Telegram booking flow сохраняется.
При включении `/start` создаёт кнопку «Записаться» с WebApp URL конкретного
BotInstance. Managed provisioning также настраивает menu button. Устаревший
`MINI_APP_URL` не используется новым tenant-scoped интерфейсом.
Bot token/encryption key/DB/Redis secrets остаются в серверном environment.

```sh
cd miniapp
npm ci
npm test
npm run build
```

`npm run dev` доступен для разработки, но реальный вход требует Telegram,
подписанного initData, HTTPS origin и проксирования API на том же host.
Production frontend не содержит dev-login или mock fallback.

```sh
docker compose -f docker-compose.yml -f deploy/miniapp/compose.yml config -q
docker compose -f docker-compose.yml -f deploy/miniapp/compose.yml build
docker compose -f docker-compose.yml -f deploy/miniapp/compose.yml run --rm migrate
docker compose -f docker-compose.yml -f deploy/miniapp/compose.yml up -d
```

Overlay добавляет один внутренний static container. Caddy обслуживает новый app
host и same-origin API, не меняя основной сайт. CSP разрешает только свои assets
и официальный Telegram SDK; auth API не пишется в access log. Cookie/CSRF/auth
headers исключены из proxy error logs. Нужны DNS/TLS и настройка Mini App в
BotFather для соответствующих ботов. До Docker/UAT включать production не нужно.

## Проверки и ручной UAT

```sh
pytest -q
python -m compileall app tests
git diff --check
```

Для полного suite нужны `TEST_DATABASE_URL` отдельной PostgreSQL БД и
`TEST_REDIS_URL` отдельного Redis DB. Mini App tests требуют права CREATE DATABASE
на тестовом PostgreSQL и создают/удаляют случайную disposable БД. Никогда не
передавайте production connections в тесты.

Ручная проверка после отдельного разрешённого тестового deployment:

1. На двух разных ботов: `/start` → «Записаться» → Mini App. Проверить собственные
   названия/услуги и невозможность подменить UUID/cookie на другой проект.
2. Клиент: услуга → staff/любой → дата → слот → телефон/согласие → подтверждение
   → «Мои записи» → отмена. Одновременно занять один слот двумя клиентами:
   одна запись, второму понятный конфликт. Денег не переводить.
3. Предоплата: тестовая услуга с депозитом → реквизиты → JPEG/PNG чек → управление
   → скачать → approve/reject → проверить оба статуса и отсутствие дублей.
4. Мастер: расписание → карточка → CRM notes → ручная запись существующего
   клиента → service edit → staff services → отдельный день/выходной/перерыв
   → horizon 14 → проверить клиентские слоты.
5. Безопасность: обычный клиент не получает Master Mode, STAFF не меняет CRM/
   настройки/payment decision, чужие entity IDs дают отказ, invalid/stale
   initData и CSRF отклоняются, token rotation/disable закрывает старую сессию.
6. На Telegram Android/iOS/Desktop: светлая/чёрная/System, themeChanged,
   клавиатура, safe areas, BackButton, загрузка чека, offline/retry и истёкшая
   сессия. Для Telegram Web требуется возможность использования Secure cookie
   внутри WebView; при блокировке cookie авторизация завершится отказом.

Проверки реального Telegram UI, fresh Docker images и production deployment
в этой реализации **не выполнены**. Они обязательны до заявления о готовности.

### Результаты проверки 04.10.2026

| Проверка | Результат |
|---|---|
| Backend Mini App targeted | 33 passed, 0 failed |
| Из них настоящая PostgreSQL integration/concurrency | 24 passed |
| Security subset этих тестов | 22 passed, 11 deselected |
| Frontend unit/DOM flows | 18 passed, 0 failed |
| Полный pytest | 626 passed, 0 failed, 0 skipped, 6 warnings |
| compileall / diff whitespace / Ruff новых Python files | PASS |
| Fresh npm ci + production build | PASS, JS 26.76 КБ, CSS 16.02 КБ |
| Чистое Python virtualenv: requirements, pip check, backend imports | PASS |
| Alembic fresh disposable DB / current / single head | PASS, 0025 |
| Compose overlay config с тестовыми переменными | PASS, standalone Compose CLI |
| Оба Caddyfile | Valid configuration, Caddy 2.11.7 |
| Fresh backend/Mini App Docker images | NOT VERIFIED: Docker engine отсутствует |
| Настоящие Telegram Android/iOS/Desktop/Web | NOT VERIFIED: deployment не выполнялся |

Security subset — часть 33 targeted, а не дополнительные 22 теста. Frontend DOM
тесты подменяют HTTP ответы; backend integration использует настоящую PostgreSQL,
но безопасные Telegram test doubles. Полный suite также использует отдельный
локальный Redis 7.4.11. Шесть warnings относятся к существующим AsyncMock
fixtures и transaction teardown тестам подключения ботов.

Во время полного прогона также восстановлен пропущенный `raise` при ошибке
provisioning и исправлены устаревшие test fixtures (`User.first_name`, тестовый
crypto key, `BotIdentity.first_name`, managed `bot_id` и AsyncMock gateway).
Скрытые ошибки подключения больше не возвращают `None` как успешный результат.

## Изменённые файлы

```text
.dockerignore
.env.example
.gitignore
README.md
alembic/versions/2026_10_03_0025_miniapp_security.py
app/bot/handlers/client/start.py
app/bot/keyboards/client/menu.py
app/config/settings.py
app/database/models/__init__.py
app/database/models/miniapp.py
app/services/booking_service.py
app/services/bot_provisioning_service.py
app/services/miniapp_auth.py
app/web/app.py
app/web/miniapp.py
app/web/miniapp_contracts.py
deploy/miniapp/Caddyfile
deploy/miniapp/compose.yml
deploy/miniapp/gateway.Caddyfile
docs/miniapp.md
miniapp/Dockerfile
miniapp/index.html
miniapp/package-lock.json
miniapp/package.json
miniapp/src/api/client.js
miniapp/src/app.js
miniapp/src/style.css
miniapp/src/telegram/bootstrap.js
miniapp/src/theme/theme.js
miniapp/src/ui.js
miniapp/tests/core.test.js
miniapp/tests/flows.test.js
pyproject.toml
requirements.txt
tests/test_alembic_migrations.py
tests/test_managed_bot_flow.py
tests/test_miniapp.py
```
