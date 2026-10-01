# ⚡ ZapisFlow — Мультитенантная SaaS-платформа онлайн-записи в Telegram

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![aiogram 3.x](https://img.shields.io/badge/aiogram-3.14+-green.svg)](https://docs.aiogram.dev/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![PostgreSQL 16](https://img.shields.io/badge/PostgreSQL-16-blue.svg)](https://www.postgresql.org/)
[![Redis 7](https://img.shields.io/badge/Redis-7-red.svg)](https://redis.io/)
[![SQLAlchemy 2.0](https://img.shields.io/badge/SQLAlchemy-2.0-red.svg)](https://docs.sqlalchemy.org/)
[![Alembic](https://img.shields.io/badge/Alembic-0021%20head-orange.svg)](https://alembic.sqlalchemy.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED.svg)](https://www.docker.com/)
[![Tests](https://img.shields.io/badge/tests-504%20passed-success.svg)](https://github.com/raiyaxkrash/zapisflow)

**ZapisFlow** — это промышленная мультитенантная облачная B2B SaaS-платформа для автоматизации онлайн-записи, управления студиями красоты и работы частных мастеров (лэшмейкеры, бровисты, мастера маникюра, барберы, косметологи, визажисты) через персональных Telegram-ботов.

Платформа объединяет удобство Telegram с функционалом полноценной CRM: мастера и студии подключают собственных ботов за 2 минуты через `@BotFather`, клиенты записываются в удобном Single-Message интерфейсе без спама в чате, а владельцы проектов получают автоматизированный прием предоплат, управление штатом мастеров, сегментацию клиентской базы и финансовую аналитику.

---

## 🌟 Архитектура платформы

```
                       Telegram API Cloud
                               │
                               │ HTTPS Webhooks
                               ▼
                      Reverse Proxy (Caddy / NGINX)
                               │ (TLS 1.2/1.3, Rate-Limit, 2MB Body Limit)
                               ▼
                ┌─────────────────────────────────────────┐
                │       FastAPI Webhook Ingestion         │
                ├─────────────────────────────────────────┤
                │  /telegram/manager-webhook (Manager)   │
                │  /telegram/webhook/{public_id} (Bots)   │
                │  /billing/yookassa/webhook (YooKassa)   │
                │  /health/live & /health/ready           │
                └──────────────┬──────────────────────────┘
                               │
                  ┌────────────┴────────────┐
                  ▼                         ▼
          Platform Manager Bot      Dynamic BotRegistry
          • Онбординг мастеров      • AES-256-GCM + AAD
          • Управление студиями     • LRU-кэш ботов в памяти
          • Multi-Staff и роли      • Redis Pub/Sub инвалидация
          • YooKassa биллинг        • Ротация и удаление токенов
                  │                         │
                  └────────────┬────────────┘
                               ▼
                    TenantContextMiddleware
                               ▼
                   Shared aiogram Dispatcher
                               │
                 ┌─────────────┴─────────────┐
                 ▼                           ▼
         Client Experience            Master Admin CRM
         • Выбор специалиста/услуги  • Подтверждение чеков
         • Расчёт окон SlotEngine    • Журнал визитов и график
         • Single-Message UI         • Multi-Staff управление
         • Исключение овербукинга    • CRM, LTV и сегментация
         • Отзывы и портфолио        • Таргетированные рассылки
                               │
                               ▼
            ┌───────────────────────────────────────┐
            │  Multi-Replica Background Scheduler   │
            │  • Очистка броней (Hold Cleaner)      │
            │  • Жизненный цикл подписок и биллинг  │
            │  • Цепочки уведомлений (3д, 1д, эксп) │
            │  • Доставка через Telegram Outbox     │
            │  • Распределённые Advisory Locks      │
            └──────────────────┬────────────────────┘
                               │
                 ┌─────────────┴─────────────┐
                 ▼                           ▼
           PostgreSQL 16                  Redis 7
         (Composite FKs,             (FSM, Дедупликация,
          Exclusion Constraints,      Pub/Sub, Distributed
          JSONB, Audit Logs)          Advisory Locks)
```

---

## 🚀 Ключевые возможности

### 🏢 1. Платформенный управляющий бот (Manager Bot)
- **Мгновенный онбординг мастера:**
  - Создание проекта студии и пошаговый мастер подключения Telegram-бота.
  - Проверка токена через Telegram API `getMe`, валидация коллизий и защита от дублирования.
  - Автоматическая регистрация защищённого вебхука с уникальным UUID `public_id` и секретом `X-Telegram-Bot-Api-Secret-Token`.
  - Безопасная ротация токенов, включение, выключение и безопасное удаление бота.
- **Управление студией и настройками:**
  - Настройка расписания по дням недели, шага сетки и перерывов.
  - Настройка предоплат (фиксированная или процент), банковские реквизиты.
  - Редактирование контактов студии (телефон, адрес, Instagram, VK, Telegram, маршрут).

### 👥 2. Мульти-мастер студии (Multi-Staff & Роли)
- **Иерархия ролей проекта:**
  - `OWNER` — владелец студии (полный доступ, включая биллинг и удаление проекта).
  - `ADMIN` — администратор студии (управление записями, графиками, рассылками).
  - `STAFF` — сотрудник/мастер (доступ к собственному расписанию и визитам; биллинг заблокирован).
- **Сотрудники и их услуги:**
  - Добавление специалистов с указанием специализации.
  - Привязка конкретных услуг к конкретным мастерам (`StaffService`).
  - Индивидуальные рабочие графики каждого специалиста.
- **Безопасные ссылки-приглашения (Invite Tokens):**
  - Одноразовые токены приглашения сотрудников со сроком действия (24ч / 72ч).
  - В БД хранится только `HMAC-SHA256` хэш токена (raw-токен никогда не сохраняется в открытом виде).
  - Атомарная защита от race condition при одновременной активации.
- **Строгая изоляция данных на уровне СУБД:**
  - Составные внешние ключи `(master_id, staff_id) -> staff_members(master_id, id)` физически запрещают кросс-тенантные связи на уровне PostgreSQL.

### 👤 3. Клиентский опыт в боте мастера (Client Experience)
- **Single-Message UI:** Вся навигация, выбор даты, времени и подтверждение происходят через интерактивное редактирование одного сообщения — чат остаётся чистым.
- **Умный расчёт свободных окон (`SlotEngine`):**
  - Поддержка выбора: запись к конкретному сотруднику либо *«К любому свободному мастеру»*.
  - Учёт длительности услуги, обязательного технологического буфера мастера и индивидуального перерыва.
  - Учёт графика работы, отпусков и исключений в расписании.
- **100% защита от овербукинга (Double-Booking Prevention):**
  - Временное удержание слота (`HOLD`) на 30 минут для перевода предоплаты.
  - Ограничение `EXCLUSION CONSTRAINT` на уровне PostgreSQL 16 с расширением `btree_gist` и блокировки `SELECT ... FOR UPDATE`.
- **Предоплата и загрузка чека:**
  - Удобное копирование реквизитов мастера в 1 клик.
  - Отправка чека фото или файлом с мгновенной отправкой в Inbox мастера.
- **Личный кабинет клиента:**
  - Просмотр актуальных и прошедших записей, отмена визита по правилам студии.
  - Оценка визита и отзывы (1–5 звёзд) с привязкой к конкретному визиту и мастеру.
  - Портфолио по категориям, адрес студии и ссылки на соцсети.

### 💼 4. CRM и бизнес-функции мастера
- **Входящие чеки (Inbox):** Уведомления о новых оплатах с кнопками *«Подтвердить»* или *«Отклонить»* с указанием причины.
- **Интерактивный журнал записей:** Просмотр визитов (*Сегодня*, *Завтра*, *Ожидают оплаты*, *Все*), перенос времени (*Reschedule*), отметка о неявке (*No-Show*).
- **Клиентская база (CRM):**
  - Профиль клиента: контакты, совокупный доход (**LTV**), средний чек, процент отмен, персональные заметки.
  - **Автоматическая сегментация:** Новые, Постоянные, Спящие, Потерянные, VIP.
- **Сегментированные рассылки:**
  - Отправка сообщений целевым группам клиентов с предпросмотром перед отправкой.
  - Доставка через фоновую очередь Telegram Outbox без блокировки рабочих потоков.
- **Финансы и аналитика:** Выручка от услуг, удержанные предоплаты, валовый доход, средний чек, топ услуг за период.

### 💳 5. SaaS-монетизация и биллинг (YooKassa Web Checkout)
- **Каталог официальных тарифов:**
  - 1 месяц — **499 ₽** (`basic_monthly`)
  - 3 месяца — **1 299 ₽** (`basic_3_months`, выгода ~13%)
  - 6 месяцев — **2 390 ₽** (`basic_6_months`, выгода ~20%)
  - 12 месяцев — **4 490 ₽** (`basic_yearly`, выгода ~25%)
- **Честный пробный период (Trial):**
  - 14 дней полного доступа без ввода банковской карты.
  - Защита от злоупотреблений: триал закреплён за аккаунтом пользователя `User` (`trial_claimed_at`). Удаление, повторное создание проекта или смена сотрудников не сбрасывают триал.
  - При оплате во время триала оставшиеся дни сохраняются (`trial_ends_at + duration_days`).
- **Интеграция с YooKassa:**
  - Генерация платёжных ссылок через YooKassa Web Checkout.
  - Ручная проверка статуса через кнопку *«🔄 Проверить оплату»*.
  - Вебхуки `payment.succeeded` и `payment.canceled` с идемпотентной активацией.
  - Защита от состояний гонки (race conditions) через `with_for_update()`.
  - Graceful fallback: при недоступности онлайн-платежей отображается кнопка связи с поддержкой `@zapisflow`.
- **Entitlement Service & Уведомления:**
  - При истечении подписки (`EXPIRED`) блокируется только приём новых записей и рассылки. Мастер сохраняет неограниченный доступ к чтению данных CRM, базы клиентов и аналитики.
  - Клиентам в боте выводится вежливое сообщение с контактами мастера для прямой записи.
  - Цепочка уведомлений: напоминания за 3 дня, за 1 день, в день окончания и на следующий день после истечения.
- **CLI-инструмент администратора:**
  - Ручное начисление и продление подписки из терминала:
    `python -m app.scripts.activate_subscription --master-id 1 --days 30 --reason "Партнёрский доступ"`
- **Единая поддержка:**
  - Во всех меню и экранах доступна кнопка `[💬 Поддержка @zapisflow]` (`https://t.me/zapisflow`).

---

## 🛠 Технологический стек

| Направление | Стек | Назначение |
|---|---|---|
| **Язык разработки** | Python 3.12+ | Строгая типизация, асинхронный синтаксис |
| **Telegram Framework** | `aiogram 3.14+` | Асинхронные роутеры, фильтры, FSM и middlewares |
| **HTTP Webhook Engine** | `FastAPI` + `uvicorn` | Приём входящих апдейтов, вебхуки YooKassa, health checks |
| **База данных** | PostgreSQL 16 | Реляционная БД, `btree_gist`, составные FK, JSONB |
| **ORM / Драйвер** | `SQLAlchemy 2.0` + `asyncpg` | Асинхронный пул соединений, строгая типизация Mapped |
| **Миграции БД** | `Alembic` | Версионирование схемы БД (ревизии 0001–0021 head) |
| **Кэш & Брокер** | `Redis 7` | FSM состояний, дедупликация апдейтов, Pub/Sub инвалидация |
| **Криптография** | `cryptography` (AES-256-GCM) | Шифрование токенов с AAD, HMAC-SHA256 для invite-токенов |
| **Фоновые задачи** | `APScheduler 3.10+` | MultiTenantScheduler, воркеры подписок и напоминаний |
| **Очередь сообщений** | Telegram Outbox | Надёжная асинхронная доставка с дедупликацией и ретраями |
| **Платёжный шлюз** | `YooKassa Web Checkout` | Оплата банковскими картами, СБП, вебхуки |
| **Обратный прокси** | `Caddy` / `NGINX` | TLS 1.2/1.3, Rate-limiting, HSTS |
| **Контейнеризация** | `Docker` + `Docker Compose` | Multi-stage сборка под пользователем `appuser` |

---

## 📁 Структура проекта

```
zapisflow/
├── alembic/                      # Миграции базы данных (0001–0021 head)
│   ├── versions/                 # Файлы миграций
│   └── env.py                    # Конфигурация асинхронного Alembic
├── app/
│   ├── bot/                      # Клиентский бот мастера и панель (/admin)
│   │   ├── filters/              # Фильтры прав (IsAdminFilter, IsOwnerFilter)
│   │   ├── handlers/             # Хэндлеры (client, admin, common)
│   │   ├── keyboards/            # Inline-клавиатуры клиентского бота
│   │   ├── middlewares/          # TenantContext, DbSession, UserContext
│   │   └── states/               # FSM-состояния бронирования и визардов
│   ├── config/
│   │   └── settings.py           # Pydantic Settings v2 с валидацией
│   ├── core/
│   │   ├── security.py           # Маскирование секретов и токенов в логах
│   │   └── token_crypto.py       # AES-256-GCM authenticated шифрование
│   ├── database/
│   │   ├── models/               # Модели SQLAlchemy (Master, User, Staff, etc.)
│   │   └── session.py            # Фабрика асинхронных сессий asyncpg
│   ├── manager_bot/              # Управляющий бот платформы
│   │   ├── handlers.py           # Онбординг, Multi-Staff, биллинг, CRM
│   │   ├── keyboards.py          # Клавиатуры Manager Bot и поддержка
│   │   └── dispatcher.py         # Изолированный диспетчер платформы
│   ├── repositories/             # Репозитории доступа к данным
│   ├── scheduler/                # MultiTenantScheduler и фоновые задачи
│   │   └── jobs/                 # Воркеры: очистка броней, подписки, напоминания
│   ├── scripts/                  # CLI-скрипты платформы (activate_subscription.py)
│   ├── services/                 # Бизнес-логика платформы
│   │   ├── billing/              # YooKassaCheckoutService, WebhookService
│   │   ├── booking_service.py    # Резервирование слотов и валидация
│   │   ├── bot_registry.py       # Динамический реестр ботов с кэшем
│   │   ├── crm_service.py        # Сегментация клиентов, LTV, рассылки
│   │   ├── slot_engine.py        # Генератор свободных окон с буферами
│   │   ├── staff_service.py      # Управление сотрудниками и инвайтами
│   │   ├── subscription_service.py # Биллинг и тарифы
│   │   ├── subscription_entitlement_service.py # Контроль лимитов и прав
│   │   ├── subscription_notification_service.py # Жизненный цикл подписок
│   │   └── telegram_outbox.py    # Гарантированная отправка уведомлений
│   ├── utils/                    # Хелперы и утилиты форматирования
│   ├── web/
│   │   └── app.py                # FastAPI Webhook Ingestion Engine
│   └── main.py                   # Точка входа в приложение
├── deploy/                       # Конфигурации Caddy и NGINX
├── docs/                         # Архитектурная документация и регламенты
├── tests/                        # 504 автоматических теста (pytest)
├── docker-compose.yml            # Оркестрация сервисов
├── Dockerfile                    # Multi-stage production Dockerfile
├── requirements.txt              # Зависимости проекта
└── .env.example                  # Шаблон переменных окружения
```

---

## 🔒 Безопасность промышленного уровня

- **Защита токенов ботов (AES-256-GCM + AAD):** Все токены шифруются с привязкой к ID бота в качестве AAD. Подмена токена между проектами физически невозможна.
- **Одноразовые токены приглашений (HMAC-SHA256):** Ссылки для присоединения сотрудников к студии хэшируются. База данных не содержит открытых токенов.
- **Композитные внешние ключи (Composite FKs):** Все ключевые связи (`Appointment`, `StaffService`, `Schedule`, `Portfolio`) защищены составными внешними ключами `(master_id, entity_id)` на уровне PostgreSQL, гарантируя 100% изоляцию данных между арендаторами (тенентами).
- **Timing-Safe сравнение секретов:** Секретные токены вебхуков Telegram и YooKassa сравниваются через `secrets.compare_digest`.
- **Защита от DoS и OOM на вебхуках:** Ограничение размера входящего тела запроса (`WEBHOOK_MAX_BODY_BYTES`) с потоковой проверкой и ошибкой `413 Payload Too Large`.
- **Идемпотентность и атомарность:** Защита от дублирования вебхуков через Redis и блокировка строк `with_for_update()` при проведении оплат.

---

## ⚙️ Настройка переменных окружения (`.env`)

Создайте файл `.env` на основе примера:
```bash
cp .env.example .env
```

Основные параметры:
```env
# Режим окружения (production / development)
APP_ENV=production
APP_MODE=webhook
DOMAIN=api.zapisflow.su

# Публичные URL вебхуков
WEBHOOK_BASE_URL=https://api.zapisflow.su
WEBHOOK_HOST=0.0.0.0
WEBHOOK_PORT=8000
WEBHOOK_MAX_BODY_BYTES=1048576

# PostgreSQL 16 (asyncpg)
POSTGRES_PASSWORD=your_secure_password
DATABASE_URL=postgresql+asyncpg://postgres:your_secure_password@postgres:5432/beauty_bot_db
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=20
DB_POOL_PRE_PING=true

# Redis 7
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=your_secure_redis_password
REDIS_DB=0

# Шифрование токенов ботов (32 байта Hex или Base64)
# Генерация: python -c "import secrets; print(secrets.token_hex(32))"
BOT_TOKEN_ENCRYPTION_KEY=your_generated_32_byte_key

# Платформенный управляющий бот (Manager Bot)
MANAGER_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
MANAGER_WEBHOOK_SECRET=your_random_secret_string_min_32_chars
SUPPORT_TELEGRAM_USERNAME=zapisflow

# Биллинг YooKassa
PAYMENT_PROVIDER=yookassa_web
YOOKASSA_SHOP_ID=your_shop_id
YOOKASSA_SECRET_KEY=your_secret_key
YOOKASSA_MODE=production
BILLING_DOMAIN=pay.zapisflow.su
BILLING_RETURN_URL=https://zapisflow.su/
TRIAL_DURATION_DAYS=14
```

---

## 🚀 Запуск проекта

### Вариант 1. Запуск через Docker Compose (Production)

```bash
# 1. Запуск БД PostgreSQL и Redis
docker compose up -d postgres redis

# 2. Применение миграций Alembic
docker compose run --rm migrate

# 3. Запуск сервиса приложения и обратного прокси Caddy
docker compose up -d backend caddy
```

Проверка состояния сервиса:
```bash
curl -i https://api.zapisflow.su/health/live
curl -i https://api.zapisflow.su/health/ready
```

### Вариант 2. Локальный запуск для разработки

```bash
# 1. Создание и активация виртуального окружения
python -m venv .venv
source .venv/bin/activate  # Для Windows: .\.venv\Scripts\activate

# 2. Установка зависимостей
pip install -r requirements.txt

# 3. Применение миграций базы данных
alembic upgrade head

# 4. Запуск приложения
python -m app.main
```

---

## 🧪 Тестирование

Кодовая база покрыта исчерпывающим набором модульных, интеграционных и сквозных (E2E) тестов:
- **504 теста**, 100% успешно пройдены.
- Полное тестирование сценариев изоляции данных, SlotEngine, предотвращения овербукинга, прав ролей Multi-Staff, биллинга YooKassa и миграций Alembic.

Запуск тестов:
```bash
pytest -v
```

Запуск только тестов монетизации и биллинга:
```bash
pytest tests/test_phase_6_monetization.py -v
```

Запуск тестов мультитенантности и Multi-Staff:
```bash
pytest tests/test_phase_5_multi_staff.py -v
```

Проверка компиляции проекта:
```bash
python -m compileall app tests
```

---

## 📄 Лицензия

Проект распространяется под лицензией MIT. Подробности в файле [LICENSE](LICENSE).
