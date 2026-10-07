# ZapisFlow

ZapisFlow — сервис записи клиентов через Telegram. Каждый мастер или студия ведёт отдельный проект со своим клиентским ботом, услугами, сотрудниками, расписанием, записями и CRM.

## Что такое ZapisFlow

Платформа предназначена для частных мастеров и небольших студий: бьюти-мастеров, специалистов по волосам, барберов, визажистов, массажистов и других специалистов, работающих по предварительной записи. Проекты изолированы друг от друга; один владелец может создать несколько проектов и подключить к ним отдельные Telegram-боты.

## Возможности

### Для мастера

- Создание проекта и начальная настройка в Manager Bot.
- Подключение собственного Telegram-бота через токен BotFather, проверка готовности и включение/отключение webhook.
- Каталог услуг: описание, цена, длительность, буфер и размер предоплаты.
- Настройка реквизитов проекта для клиентской предоплаты; готовность проекта к активации проверяется с учётом услуг, где предоплата задана.
- Еженедельное расписание, перерывы, отдельные рабочие даты, выходные и блокировка интервалов; настраиваемые часовой пояс, минимальное время до визита и горизонт записи.
- Несколько сотрудников в проекте, привязка сотрудников к услугам и отдельное расписание.
- Управление записями: просмотр, подтверждение/отмена, перенос, завершение визита и отметка неявки.
- Входящие подтверждения клиентской предоплаты, карточки клиентов с историей и заметками, поиск и сегменты CRM.
- Контакты проекта, портфолио, просмотр отзывов и статистика.
- Создание и возобновление рассылок по клиентским сегментам.
- Настройка клиентских напоминаний и параметров записи.

### Для клиента

Клиентский бот показывает услуги, портфолио, контакты и информацию о мастере. Клиент выбирает сотрудника/услугу, дату и свободное время, создаёт запись, просматривает и отменяет свои записи. Если у услуги задана предоплата, бот показывает реквизиты проекта, принимает фото или документ подтверждения, а мастер проверяет оплату. Перенос записи клиент согласует с мастером; самостоятельный перенос доступен мастеру в панели проекта.

Клиент также может оставить отзыв после визита. Напоминания о предстоящей записи отправляются фоновыми задачами, если они включены в настройках проекта.

### Для команды проекта

Роли `OWNER`, `ADMIN` и `STAFF` проверяются в контексте конкретного проекта. Владелец управляет проектом и его подпиской; администратор ведёт рабочие данные в пределах разрешений; сотрудник получает доступ к разрешённым операциям по своему расписанию и записям. Для сотрудников предусмотрены приглашение и назначение услуг.

### Platform Admin

В отдельной админке того же Manager Bot пользователям платформы с правами Platform Admin доступны Dashboard, пользователи, проекты, клиентские боты, подписки, платежи, тарифы и сводные SaaS-показатели. Доступ ограничен проверкой Platform Admin и не предоставляется обычным владельцам проектов.

## Как работает

Рабочие интерфейсы ZapisFlow:

- **Manager Bot** — регистрация пользователя, создание проектов, настройка проекта, подключение клиентского бота и управление подпиской.
- **Client Bot** — отдельный бот проекта для записи и взаимодействия с его клиентами. Входящий webhook определяет tenant по связанному `BotInstance`, а не по данным кнопки или клиента.
- **Telegram Mini App** — клиентская запись и мобильное рабочее пространство мастера поверх той же модели User/Appointment.
- **Marketing Website** — информация о ZapisFlow и переход в Manager Bot; браузерная запись не предоставляется.
- **Platform Admin** — административные разделы платформы внутри Manager Bot, закрытые отдельной авторизацией.

Клиентская предоплата и SaaS-платёж — разные процессы. Клиентская предоплата относится к записи и оплачивается мастеру по его реквизитам; подтверждение чека выполняет мастер. SaaS-платёж относится к подписке владельца проекта на ZapisFlow и обрабатывается отдельно через YooKassa, если провайдер включён.

## Архитектура

```text
Telegram
   ↓ HTTPS
Caddy
   ↓
FastAPI webhook gateway
   ├─ Manager Bot dispatcher
   └─ public bot ID → BotRegistry → BotInstance → aiogram dispatcher
                                      ↓
                              middleware, handlers
                                      ↓
                              services / repositories
                                ↙             ↘
                         PostgreSQL           Redis
```

FastAPI проверяет входящие webhook-запросы и маршрутизирует обновления. Для клиентских ботов `BotRegistry` разрешает `BotInstance`, допущенный политикой состояния (`ACTIVE` или предусмотренный кодом `SETUP_REQUIRED`), использует зашифрованный токен и создаёт/переиспользует runtime-бота. Отключённые экземпляры не обслуживаются. Dispatcher подключает роутеры клиентского интерфейса и панели проекта. PostgreSQL хранит бизнес-данные; Redis используется для FSM, дедупликации обновлений и уведомления экземпляров приложения об инвалидировании кэша.

Mini App обращается к `/api/miniapp` через Caddy на том же origin. FastAPI проверяет Telegram initData и короткую tenant-scoped сессию, затем вызывает те же services/repositories. Маркетинговый сайт хранит только публичное описание продукта и не имеет отдельной модели записи.

Отправка исходящих сообщений выполняется через **Telegram Outbox**: строка outbox и бизнес-изменение фиксируются в одной транзакции PostgreSQL, после чего фоновый worker отправляет сообщение. Это позволяет восстановить незавершённую доставку после сбоя. Telegram Bot API не поддерживает атомарную транзакцию с PostgreSQL, поэтому при сбое после принятия сообщения Telegram, но до фиксации `SENT`, уведомление может быть отправлено повторно. Бизнес-изменения защищаются отдельно идемпотентностью и ограничениями базы данных.

## Технологии

| Компонент | Версия в конфигурации проекта |
| --- | --- |
| Python | 3.12 в Docker-образе; пакет заявляет совместимость с Python 3.11+ |
| FastAPI / Uvicorn | `>=0.121.0` / `>=0.30.0` |
| aiogram | `3.31.0` |
| PostgreSQL / Redis | `16-alpine` / `7-alpine` |
| SQLAlchemy / asyncpg | `2.0.35` / `0.29.0` |
| Alembic | `1.13.3` |
| YooKassa | HTTP API-клиент и redirect checkout |
| Reverse proxy | Docker Compose и Caddy `2-alpine` |

Точные Python-зависимости зафиксированы в `requirements.txt`; диапазоны проектных метаданных находятся в `pyproject.toml`.

## Структура проекта

```text
app/
├── bot/             # Client Bot, панель проекта, клавиатуры и middleware
├── manager_bot/     # Manager Bot и Platform Admin
├── config/          # настройки окружения
├── core/            # шифрование токенов и защита секретов
├── database/        # SQLAlchemy-модели, сессии и seed-код
├── repositories/    # tenant-scoped доступ к данным
├── services/        # запись, слоты, CRM, платежи, подписки, BotRegistry, Outbox
│   └── billing/     # YooKassa checkout и billing-сессии
├── scheduler/       # фоновые задачи и workers
├── scripts/         # служебные команды
└── web/             # FastAPI webhooks, health и billing pages
alembic/             # миграции схемы
deploy/caddy/        # конфигурация Caddy
miniapp/             # Vite Mini App: клиент и мастер, same-origin API
marketing/           # React/TypeScript landing, без браузерной записи
tests/               # модульные и интеграционные тесты
docker-compose.yml   # PostgreSQL, Redis, migrate, backend и Caddy
Dockerfile
```

## База данных

Схемой управляет Alembic, основная и production-база — PostgreSQL. Ключевые сущности: `User`, `Master` (проект и текущий статус подписки), `BotInstance`, `MasterSettings`, `Service`, `StaffMember`/`StaffService`, `Appointment`, клиентский `Payment` и подтверждения `PaymentProof`, `MasterClient`, `Review`, портфолио и рассылки. SaaS-подписка хранит тариф в `SubscriptionPlan`, состояние на проекте, а оплаты и периоды — в `SubscriptionPayment` и `SubscriptionPeriod`; отдельной таблицы `Subscription` в текущей модели нет.

Миграции добавляют tenant-связи, составные внешние ключи и другие ограничения целостности. Пересечения записей специалиста ограничиваются на стороне PostgreSQL. Текущую ревизию можно проверить командами `alembic current` и `alembic heads`.

## Безопасность

- Tenant для Client Bot берётся из server-side связи `BotInstance.master_id`; неизвестный или отключённый бот не должен обрабатываться как другой проект.
- Запросы и административные действия ограничиваются `master_id` и ролевой авторизацией. Составные внешние ключи защищают ключевые связи между проектом, сотрудниками, услугами, записями и платежами.
- Webhook Manager Bot проверяется секретным заголовком. Для клиентских ботов применяются секрет конкретного BotInstance, проверки состояния и публичного идентификатора экземпляра.
- Токены пользовательских ботов хранятся в зашифрованном виде; `BOT_TOKEN_ENCRYPTION_KEY` и provider credentials должны поступать только из окружения/секретов.
- Redis-дедупликация дополняется долговечным `ProcessedWebhookUpdate` в PostgreSQL; повторные бизнес-команды и платежи защищаются идемпотентностью и уникальными ограничениями.
- Telegram Outbox хранит задачу доставки отдельно от plaintext bot token и разрешает BotInstance при отправке.
- Сумма SaaS-платежа берётся из плана и локального платежа, затем сверяется с аутентифицированным ответом YooKassa. Webhook YooKassa служит сигналом для проверки через API провайдера, а не самостоятельным доказательством оплаты.
- Mini App проверяет Telegram initData сервером, хранит хеши коротких сессий, сверяет tenant, Origin и CSRF; изменение оформления доступно только OWNER. Logo/cover нормализуются сервером и являются публичными изображениями бизнеса; чеки и private portfolio требуют авторизации.
- В Manager Bot есть ограничение частоты на повторную проверку SaaS-платежа; это не следует считать глобальным rate limit всех действий пользователей.

## Подписки и оплата

Проект может иметь статусы подписки `TRIAL`, `ACTIVE`, `EXPIRED` или `SUSPENDED`. Длительность trial задаётся `TRIAL_DURATION_DAYS` (пример в `.env.example` — 14 дней); выдача пробного доступа учитывается на уровне владельца, поэтому новый проект не должен сбрасывать использованный trial. Окончание периода переводит подписку в `EXPIRED`; данные проекта сохраняются, но создание новых записей и рассылки ограничиваются entitlement-проверками. `SUSPENDED` — отдельная административная блокировка и не снимается оплатой автоматически.

Миграции задают тариф `basic_monthly` / **ZapisFlow Basic**, **499 RUB за 30 дней**. Цена и срок читаются из активной записи `SubscriptionPlan` в PostgreSQL. Другие исторические тарифы сохраняются для данных, но миграция `0022` выключает продажи неподтверждённых многомесячных планов.

При `PAYMENT_PROVIDER=yookassa` (совместимый alias: `yookassa_web`) Manager Bot создаёт/переиспользует локальный `SubscriptionPayment`, затем открывает подтверждение YooKassa. Webhook и кнопка проверки получают статус через API YooKassa, сверяют его с локальным заказом и запускают идемпотентную активацию:

```text
Manager Bot → локальный заказ → подтверждение YooKassa
           → webhook/проверка статуса через YooKassa API
           → SubscriptionPayment → SubscriptionPeriod → статус проекта
```

Повторное webhook-уведомление или ручная проверка используют идемпотентный путь активации. Возврат браузера не подтверждает оплату. Тестовый магазин и боевой магазин выбираются раздельно через `YOOKASSA_MODE`; включение провайдера требует корректных секретов и параметров чеков согласно настройкам магазина. В `.env.example` провайдер выключен (`PAYMENT_PROVIDER=disabled`), поэтому образец конфигурации не запускает реальные платежи.

Веб-маршруты для одноразовой checkout-сессии (`/billing/checkout/{token}`) и её сервиса в коде также присутствуют; страница требует email для чека в режиме `merchant_receipt`. Manager Bot напрямую выдаёт подтверждение YooKassa в режиме `self_employed`, а в `merchant_receipt` сначала запрашивает email для чека; вызова выдачи website checkout-сессии из пользовательского интерфейса в текущем коде нет. Поэтому сайт checkout не описывается как доступный стандартный сценарий покупки.

## Локальный запуск

Нужны Git, Docker и Docker Compose. Создайте `.env` на основе шаблона и заполните параметры до запуска. Для webhook-режима настройте HTTPS-домены, токен Manager Bot, секрет webhook, пароль PostgreSQL, пароль Redis и ключ шифрования токенов. Пароль в `POSTGRES_PASSWORD` должен соответствовать credentials в `DATABASE_URL`. Не коммитьте `.env`.

```bash
git clone https://github.com/raiyaxkrash/zapisflow.git
cd zapisflow
cp .env.example .env
# Отредактируйте .env: DOMAIN, WEBHOOK_BASE_URL и обязательные секреты.
docker compose config -q
docker compose up -d --build
docker compose ps
```

Compose запускает PostgreSQL 16 и Redis 7 во внутренней сети, одноразовый сервис `migrate`, backend и Caddy. Backend ждёт успешного завершения миграций; публично Caddy публикует порты 80 и 443. Для получения TLS-сертификатов DNS `DOMAIN` (и `BILLING_DOMAIN`, если используется billing origin) должен указывать на сервер. При настройке billing-поддомена Caddy проксирует только поддерживаемые billing-маршруты.

## Настройка окружения

Актуальные имена переменных перечислены в `.env.example` и проверяются `app/config/settings.py` и Compose.

| Назначение | Основные переменные |
| --- | --- |
| Режим приложения | `APP_ENV`, `APP_MODE`, `TIMEZONE` |
| Telegram | `MANAGER_BOT_TOKEN`, `MANAGER_WEBHOOK_SECRET`, `SUPPORT_TELEGRAM_USERNAME`; `BOT_TOKEN` и `ADMIN_IDS` используются legacy polling/seed-кодом |
| PostgreSQL | `POSTGRES_PASSWORD`, `DATABASE_URL`, `DB_POOL_*` |
| Redis и BotRegistry | `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB`, `REDIS_PASSWORD`, `BOT_REGISTRY_CACHE_MAX_SIZE`, `BOT_REGISTRY_CACHE_TTL_SECONDS`, `BOT_REGISTRY_INVALIDATION_CHANNEL` |
| Входящие webhook | `DOMAIN`, `WEBHOOK_BASE_URL`, `WEBHOOK_HOST`, `WEBHOOK_PORT`, `WEBHOOK_MAX_BODY_BYTES`, `WEBHOOK_UPDATE_DEDUP_TTL` |
| Шифрование | `BOT_TOKEN_ENCRYPTION_KEY` |
| Telegram Mini App | `MINI_APP_BASE_URL`, `MINI_APP_DOMAIN`, `MINI_APP_SESSION_SECONDS`, `MINI_APP_AUTH_MAX_AGE_SECONDS` |
| YooKassa и возврат | `PAYMENT_PROVIDER`, `PAYMENT_CURRENCY`, `YOOKASSA_MODE`, `YOOKASSA_ALLOW_TEST_IN_PRODUCTION`, `YOOKASSA_TEST_ALLOWED_TELEGRAM_IDS`, `YOOKASSA_SHOP_ID`, `YOOKASSA_SECRET_KEY`, `YOOKASSA_TEST_SHOP_ID`, `YOOKASSA_TEST_SECRET_KEY`, `YOOKASSA_RECONCILIATION_INTERVAL_SECONDS`, `BILLING_RETURN_URL`; домен Caddy: `BILLING_DOMAIN` |
| Чек YooKassa | `YOOKASSA_FISCAL_MODE`, `YOOKASSA_RECEIPT_VAT_CODE`, `YOOKASSA_RECEIPT_PAYMENT_SUBJECT`, `YOOKASSA_RECEIPT_PAYMENT_MODE` |
| Trial и расписание | `TRIAL_DURATION_DAYS`, `HOLD_DURATION_MINUTES`, `MIN_ADVANCE_HOURS`, `MAX_ADVANCE_DAYS`, `DEFAULT_BUFFER_MINUTES`, `GRID_STEP_MINUTES` |
| Фоновые задачи | `SCHEDULER_ENABLED`, `SCHEDULER_BATCH_SIZE`, `JOB_PROCESSING_TIMEOUT_SECONDS`, `JOB_MAX_ATTEMPTS`, `HOLD_CLEANER_INTERVAL_SECONDS`, `REMINDER_GENERATION_INTERVAL_SECONDS`, `REMINDER_DELIVERY_INTERVAL_SECONDS` |

`PAYMENT_PROVIDER=disabled` оставляет SaaS-оплату выключенной. Для теста YooKassa в production-контексте нужен явный `YOOKASSA_ALLOW_TEST_IN_PRODUCTION=true` и ограниченный список `YOOKASSA_TEST_ALLOWED_TELEGRAM_IDS`; тестовые и боевые Shop ID/Secret Key хранятся отдельно. Не задавайте общие банковские реквизиты через env: реквизиты клиентской предоплаты задаются отдельно в настройках каждого проекта.

Параметры фоновых задач приведены в `.env.example` со значениями по умолчанию приложения. `BILLING_DOMAIN` задаёт домен Caddy в Compose, а `DOMAIN` — основной API-домен; `WEBHOOK_BASE_URL` использует публичный URL API.

## Миграции

Compose-сервис `migrate` по умолчанию выполняет `alembic upgrade head`; `docker compose up` запускает его перед backend. Проверка состояния БД:

```bash
docker compose run --rm migrate alembic current
docker compose run --rm migrate alembic heads
```

Для отдельного применения миграций:

```bash
docker compose run --rm migrate
```

## Тесты

Backend integration suite требует отдельную disposable PostgreSQL и Redis через `TEST_DATABASE_URL` / `TEST_REDIS_URL`. Не указывайте production DB: миграционные тесты выполняют upgrade/downgrade.

```bash
pytest -q
python -m compileall app tests

cd miniapp
npm ci
npm test
npm run build
cd ../marketing
npm ci
npm test
npm run build
```

Тесты покрывают tenant isolation и роли, webhook/дедупликацию, динамические боты и токены, запись и конкуренцию слотов, расписание, клиентские платежи, SaaS billing и YooKassa, CRM, рассылки, Outbox, Platform Admin, конфигурацию, Compose и миграции. Интеграционные проверки PostgreSQL-специфичного поведения требуют PostgreSQL; SQLite не заменяет проверки блокировок и ограничений PostgreSQL.

## Production

Production-развёртывание использует Linux VPS, Docker Compose, PostgreSQL, Redis, FastAPI и Caddy. Перед обновлением существующей установки проверьте изменения на сервере и сделайте резервные копии `.env` и PostgreSQL. После синхронизации кода примените миграции, пересоберите сервисы и проверьте контейнеры:

```bash
docker compose config -q
docker compose run --rm migrate
docker compose up -d --build
docker compose ps
```

Не удаляйте volumes PostgreSQL или Redis при обновлении. Проверьте логи backend и результаты health endpoints после запуска.

## Health checks

```bash
curl -fsS https://<ваш-api-домен>/health/live
curl -fsS https://<ваш-api-домен>/health/ready
```

`/health/live` проверяет, что приложение отвечает. `/health/ready` проверяет доступность PostgreSQL и Redis. URL зависит от `DOMAIN` и `WEBHOOK_BASE_URL` конкретного развёртывания.

## Документация и проверка

Актуальная архитектура: [Telegram Mini App](docs/miniapp.md). Финальный QA и ограничения ручного UAT: [Redesign QA](docs/MINIAPP_FINAL_QA.md). Инструкция будущего deployment: [Deployment checklist](docs/MINIAPP_DEPLOYMENT_CHECKLIST.md).

## Telegram Mini App

Единый клиентский и мастерский Mini App использует существующие сервисы и tenant-scoped API. Клиентская навигация: Главная, Записаться, Мои записи, Ещё. Мастерская: Сегодня, Календарь, Клиенты, Ещё. Есть календарь, ближайшее время, повторная запись с новой датой, ICS, CRM, услуги, команда, график, ручная предоплата, портфолио, рассылки и owner-only оформление с черновиком/предпросмотром. Подключение выполняется явно; production deployment и реальный Telegram UAT требуют отдельной проверки.
Архитектура, конфигурация, API и сценарии проверки: [docs/miniapp.md](docs/miniapp.md).

## Support

Поддержка платформы: [@zapisflow](https://t.me/zapisflow).

Подробнее о конфигурации live/test, чеках и выкатке: [YooKassa billing](docs/yookassa-billing.md).


## Клиентские каналы

Клиенты записываются в Telegram-боте через текстовый flow или в Telegram Mini App. Маркетинговый сайт показывает возможности ZapisFlow и ведёт в Manager Bot для создания проекта; он не принимает клиентские записи.

Бывшие страницы браузерной записи показывают сообщение о недоступности и ссылку на главную. Website Telegram Login/OIDC, его API и sessions удалены. Колонка `bot_instances.web_booking_enabled` оставлена deprecated для совместимости с применёнными миграциями; она не включает никакую возможность продукта.

## Оформление бизнеса и Mini App

Владелец может настроить название, изображения, акцент, тему, тексты и клиентские разделы. Telegram и Mini App используют настройки одного проекта. Инструкции и ограничения: [Оформление и Mini App](docs/BRANDING_AND_MINIAPP.md), [дизайн-система](docs/ZAPISFLOW_DESIGN_SYSTEM.md), [Mini App foundation](docs/MINIAPP_DESIGN_SYSTEM.md).

Клиентская навигация, booking preview, повторная запись и ICS: [Client Mini App](docs/MINIAPP_CLIENT.md).

Мастерское рабочее пространство, CRM, расписание и платежи: [Master Mini App](docs/MINIAPP_MASTER.md).
