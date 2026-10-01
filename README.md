# ZapisFlow

ZapisFlow — SaaS-платформа записи клиентов через Telegram для мастеров ресниц и бровей, маникюра и педикюра, парикмахеров, барберов, визажистов, массажистов, небольших студий и салонов. Каждый проект работает как отдельный tenant со своими ботом, сотрудниками, услугами, расписанием, клиентами и подпиской.

## Возможности

**Клиентский бот проекта:** выбор специалиста (или любого свободного), услуги, даты и времени; просмотр и отмена своих записей; отзывы после визита; портфолио, цены и контакты студии. Для переноса записи клиент связывается с мастером, а мастер меняет время в админке. Если для услуги настроена предоплата, клиент видит реквизиты и отправляет подтверждение оплаты; чек проверяет мастер. Напоминания отправляются фоновыми задачами.

**Мастер и студия:** управление услугами, ценами и длительностью, графиком и выходными, блокировкой времени, записями и их переносом, клиентской CRM и заметками, портфолио, контактами, статистикой и рассылками. В Manager Bot владелец ведёт проекты, подключает и отключает клиентского Telegram-бота, управляет сотрудниками и их услугами, настройками проекта и подпиской.

**Администратор платформы:** в отдельной части Manager Bot доступны Dashboard, пользователи, проекты, боты, подписки, платежи, тарифы и SaaS-метрики. Доступ проверяется системой Platform Admin.

## Архитектура

```text
User → Master / Project → BotInstance → Client Bot
                        ↘ Manager Bot (проект и подписка)

User → Project subscription → SubscriptionPlan → SubscriptionPayment → YooKassa

Telegram → Caddy (HTTPS) → FastAPI webhooks → PostgreSQL
                                      ↘ Redis (FSM, дедупликация, инвалидация)

Business transaction → Telegram Outbox (PostgreSQL) → фоновый worker → Telegram
```

Manager Bot создаёт проекты и управляет ими. Клиент попадает в конкретный проект через подключённый к нему бот; в клиентском боте нет общего каталога проектов. FastAPI принимает webhooks Manager Bot и клиентских ботов. `BotRegistry` разрешает `BotInstance`, проверяет его состояние и версию токена, кэширует подключение и получает сообщения об инвалидации через Redis.

Telegram Outbox хранит задачи отправки в PostgreSQL вместе с бизнес-изменением и доставляет их после commit. Повтор бизнес-операции ограничен ключами идемпотентности и ограничениями БД. Telegram API не даёт общей транзакции с PostgreSQL: при сбое после принятия сообщения Telegram, но до фиксации `SENT`, повторная доставка уведомления возможна.

## Multi-Tenant

`Master` представляет проект. `BotInstance.master_id` задаёт доверенный tenant для клиентского webhook; при отсутствии такого контекста production-обработка прекращается. Сотрудники, услуги, записи, расписания, контакты и CRM связаны с проектом. Заметки о клиенте хранятся в `MasterClient`, поэтому один Telegram-пользователь может иметь разные данные CRM в разных проектах. Проверки прав и tenant-scoped запросы дополняются ограничениями PostgreSQL, в том числе составными внешними ключами для ключевых связей.

## Multi-Staff

Права действуют внутри конкретного проекта:

| Роль | Доступ |
| --- | --- |
| `OWNER` | Владелец проекта; управляет ботом и подпиской, а также настройками и сотрудниками проекта. |
| `ADMIN` | Управляет рабочими данными проекта, включая записи, клиентов, услуги и сотрудников, в рамках проверок авторизации. Операции, требующие владельца, ему недоступны. |
| `STAFF` | Привязанный специалист; доступ ограничен его рабочими данными. Управление подпиской и проектом ему недоступно. |

Сотруднику можно назначить услуги и персональный график; для привязки Telegram-аккаунта используются приглашения с ограниченным сроком действия.

## Subscription & Billing

Подписка проекта имеет состояния `TRIAL`, `ACTIVE`, `EXPIRED`, `SUSPENDED`. Пробный период — **14 дней**; использование trial фиксируется за пользователем, поэтому повторное создание проекта не выдаёт новый trial. Основной активный тариф — **ZapisFlow Basic** (`basic_monthly`), **499 ₽ / 30 дней**. Цена, валюта и срок берутся из `SubscriptionPlan` в PostgreSQL, а не из callback или формы. Исторические многомесячные тарифы остаются в БД, но миграция `0022` отключает их продажу.

Подтверждённая оплата создаёт период подписки и продлевает `paid_until`. Повторное подтверждение одного платежа не создаёт второй период: обработка использует блокировки строк и уникальную связь платежа с периодом. Административный `SUSPENDED` не снимается оплатой. При `EXPIRED` новые записи и рассылки блокируются, но существующие записи и данные проекта остаются доступны для просмотра, а подписку можно продлить.

Схема YooKassa Web Checkout: **Manager Bot → checkout → YooKassa → платёж → webhook → подписка**. Кнопка «Проверить оплату» обращается к YooKassa API и применяет тот же идемпотентный путь подтверждения. Создание ссылки и возврат пользователя сами по себе не означают успешную оплату. Режим задаёт `PAYMENT_PROVIDER`; по умолчанию в `.env.example` он выключен. Тестовые и боевые реквизиты выбираются раздельно через `YOOKASSA_MODE`. Для production test-режима нужен явный opt-in и список разрешённых Telegram ID.

## Platform Manager Bot

Manager Bot служит для регистрации и ведения проектов. Авторизованный Platform Admin видит дополнительную админку:

- **Dashboard** — сводка платформы.
- **Пользователи и проекты** — просмотр аккаунтов и проектов, административная блокировка проекта.
- **Боты** — состояние подключений и управление ботами.
- **Подписки и платежи** — история, статусы и проверка платежа через YooKassa.
- **Тарифы** — список тарифов и управление их доступностью.
- **SaaS-аналитика** — агрегированные показатели платформы.

Обычный пользователь Manager Bot не получает доступ к этим разделам: каждый вход проверяется `PlatformAdminService`.

## Технологии

| Компонент | Версия или источник |
| --- | --- |
| Python | 3.12 в `Dockerfile` (`pyproject.toml` допускает 3.11+) |
| FastAPI / uvicorn | `>=0.115.0` / `>=0.30.0` в `requirements.txt` |
| aiogram | `3.14.0` |
| PostgreSQL / Redis | `16-alpine` / `7-alpine` в Compose |
| SQLAlchemy / asyncpg | `2.0.35` / `0.29.0` |
| Alembic | `1.13.3`; текущая миграция `2026_10_01_0022` |
| Docker Compose / Caddy | Compose stack и образ `caddy:2-alpine` |
| YooKassa | Web Checkout через HTTP-клиент приложения |

## Структура проекта

```text
app/
├── bot/              # клиентский бот и панель мастера /admin
├── manager_bot/      # проекты, подписки и Platform Admin
├── config/           # настройки окружения
├── core/             # безопасность и шифрование токенов
├── database/         # модели и сессии SQLAlchemy
├── repositories/     # доступ к данным
├── services/         # запись, CRM, подписка, биллинг, BotRegistry, Outbox
├── scheduler/        # фоновые задачи и доставка
├── scripts/          # служебные команды
└── web/              # FastAPI, webhooks и страницы billing
alembic/versions/     # миграции PostgreSQL
deploy/caddy/         # конфигурация HTTPS reverse proxy
tests/                # модульные и интеграционные тесты
docker-compose.yml    # postgres, redis, migrate, backend, caddy
```

## Установка

Нужны Docker и Docker Compose. Клонируйте репозиторий, создайте `.env` и заполните обязательные значения по `.env.example` **до запуска**: пароли PostgreSQL и Redis, ключ шифрования токенов, Manager Bot token, webhook secret и публичные домены. Значения `POSTGRES_PASSWORD` и пароль в `DATABASE_URL` должны совпадать.

```bash
git clone https://github.com/raiyaxkrash/zapisflow.git
cd zapisflow
cp .env.example .env
# Отредактируйте .env без добавления его в Git.
docker compose config -q
docker compose up -d --build
docker compose ps
```

Compose запускает `postgres`, `redis`, одноразовый `migrate`, `backend` и `caddy`; backend ожидает успешного завершения миграций. PostgreSQL и Redis доступны только во внутренней сети Compose, Caddy публикует 80/443. Для HTTPS домены из `DOMAIN` и `BILLING_DOMAIN` должны указывать на сервер.

## Configuration

Основные группы переменных в `.env.example`:

| Группа | Переменные |
| --- | --- |
| Режим и Telegram | `APP_ENV`, `APP_MODE`, `MANAGER_BOT_TOKEN`, `MANAGER_WEBHOOK_SECRET`, `BOT_TOKEN_ENCRYPTION_KEY` |
| PostgreSQL | `POSTGRES_PASSWORD`, `DATABASE_URL`, `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` |
| Redis | `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, `REDIS_DB` |
| Webhook и URL | `DOMAIN`, `WEBHOOK_BASE_URL`, `BILLING_DOMAIN`, `BILLING_RETURN_URL` |
| Оплата YooKassa | `PAYMENT_PROVIDER`, `PAYMENT_CURRENCY`, `YOOKASSA_MODE`, `YOOKASSA_SHOP_ID`, `YOOKASSA_SECRET_KEY`, отдельные `YOOKASSA_TEST_*` |
| Чеки и поддержка | `YOOKASSA_FISCAL_MODE`, `YOOKASSA_RECEIPT_*`, `SUPPORT_TELEGRAM_USERNAME` |

Для YooKassa задавайте фискальный режим и параметры чека согласно настройкам своего магазина. `PAYMENT_PROVIDER=disabled` не создаёт настоящую оплату. Секреты держите только в окружении сервера; `.env` не коммитьте. В production `APP_MODE=webhook`, а отсутствие обязательных секретов приводит к ошибке конфигурации.

## Database

Схемой управляет Alembic. В Compose миграции выполняются сервисом `migrate` перед запуском backend. Для проверки и отдельного запуска:

```bash
docker compose run --rm migrate
docker compose run --rm migrate alembic current
docker compose run --rm migrate alembic heads
```

`migrate` по умолчанию запускает `alembic upgrade head`; дополнительная команда после имени сервиса заменяет этот default. Для локального окружения с настроенным `DATABASE_URL` доступны также `alembic upgrade head`, `alembic current` и `alembic heads`.

## Production

Перед обновлением работающей установки сохраните БД и `.env`, изучите локальные изменения сервера и проверьте совместимость конфигурации. Для чистого checkout с уже настроенным `.env`:

```bash
git pull --ff-only
docker compose config -q
docker compose run --rm migrate
docker compose up -d --build
docker compose ps
curl -fsS https://api.zapisflow.su/health/live
curl -fsS https://api.zapisflow.su/health/ready
```

Эти URL относятся к существующему развёртыванию; для другой установки настройте свои DNS и TLS. Caddy обслуживает API на `api.zapisflow.su` и страницы billing на `pay.zapisflow.su`; основной сайт `zapisflow.su` находится отдельно. Проверяйте webhooks, состояние ботов и логи после обновления. Работоспособность HTTPS с VPS не означает доступность домена из каждой внешней сети.

## Security

- Tenant определяется по `BotInstance.master_id` на сервере; CRM и административные запросы проверяют проект и роль (`OWNER`, `ADMIN`, `STAFF`).
- Telegram webhooks требуют секретный заголовок; токены клиентских ботов хранятся в зашифрованном виде. Секреты маскируются в логах.
- Redis снижает число повторных обработок update и передаёт сигналы инвалидации `BotRegistry`; durable ledger и ограничения PostgreSQL защищают бизнес-изменения при повторной доставке.
- Сумма YooKassa сверяется с записью платежа и планом из БД. Один `SubscriptionPayment` не должен создавать несколько `SubscriptionPeriod`.
- Outbox отделяет commit бизнес-данных от Telegram API. Доставка уведомления после сбоя может повториться; абсолютной exactly-once доставки Telegram нет.

## Tests

Полный набор:

```bash
pytest -q
```

Результат запуска в окружении проекта: **518 passed, 0 failed, 2 warnings**. Тесты охватывают tenant isolation, роли, запись и конкуренцию за слот, подписки и платежи, webhooks, Outbox и миграции. Для PostgreSQL-специфичных сценариев необходима тестовая PostgreSQL, а не SQLite.

## Support

- 💬 Поддержка: [@zapisflow](https://t.me/zapisflow)
- Управляющий бот: [@zapisflowsbot](https://t.me/zapisflowsbot)

## Status

**ZapisFlow v1 завершён.** Дальнейшее развитие определяется реальным использованием продукта и обратной связью пользователей.
