# ⚡ ZapisFlow — Мультитенантная SaaS-платформа онлайн-записи в Telegram

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![aiogram 3.x](https://img.shields.io/badge/aiogram-3.14+-green.svg)](https://docs.aiogram.dev/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![PostgreSQL 16](https://img.shields.io/badge/PostgreSQL-16-blue.svg)](https://www.postgresql.org/)
[![Redis 7](https://img.shields.io/badge/Redis-7-red.svg)](https://redis.io/)
[![SQLAlchemy 2.0](https://img.shields.io/badge/SQLAlchemy-2.0-red.svg)](https://docs.sqlalchemy.org/)
[![Alembic](https://img.shields.io/badge/Alembic-Migrations-orange.svg)](https://alembic.sqlalchemy.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED.svg)](https://www.docker.com/)
[![Tests](https://img.shields.io/badge/tests-237%20passed-success.svg)](https://github.com/raiyaxkrash/zapisflow)

**ZapisFlow** — это промышленная мультитенантная облачная платформа для автоматизации онлайн-записи, предоплат и управления студиями красоты через персональных Telegram-ботов мастеров.

Платформа позволяет мастерам подключать собственных Telegram-ботов без навыков программирования за 2 минуты, а клиентам — записываться на процедуры через современный Single-Message интерфейс без спама в чате.

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
            │  /health/live & /health/ready           │
            └──────────────┬──────────────────────────┘
                           │
              ┌────────────┴────────────┐
              ▼                         ▼
      Platform Manager Bot      Dynamic BotRegistry
      • Онбординг мастеров      • AES-256-GCM + AAD
      • Токены через BotFather  • Кэширование экземпляров
      • Управление подписками   • Redis Pub/Sub инвалидация
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
     • Выбор мастера и услуги    • Подтверждение предоплат
     • Интерактивный календарь   • Журнал записей и график
     • Расчёт окон SlotEngine    • Клиентская база и LTV
     • Защита от овербукинга     • Финансовая аналитика
                           │
                           ▼
        ┌───────────────────────────────────────┐
        │  Multi-Replica Multi-Tenant Scheduler │
        │  • Очистка броней (Hold Cleaner)      │
        │  • Генерация и доставка напоминаний   │
        │  • Жизненный цикл подписок            │
        │  • Распределённые Advisory Locks      │
        └──────────────────┬────────────────────┘
                           │
             ┌─────────────┴─────────────┐
             ▼                           ▼
       PostgreSQL 16                  Redis 7
     (БД & Аудит-логи)         (FSM, Дедупликация, Locks)
```

---

## 🚀 Ключевые возможности

### 🏢 Платформенный управляющий бот (Manager Bot):
- **Автоматический онбординг мастера:**
  - Создание студии (Master) и пошаговый мастер подключения бота.
  - Проверка токена через Telegram `getMe`, валидация коллизий и защита от дублирования.
  - Автоматическая регистрация вебхука с уникальным `public_id` UUID и секретным токеном `X-Telegram-Bot-Api-Secret-Token`.
  - Безопасная ротация токенов и отключение/включение бота в 1 клик.
- **SaaS-подписки и биллинг:**
  - Пробный период 14 дней (`TRIAL`) при регистрации без ввода карты.
  - Прозрачный переход между тарифами (месяц, квартал, год).
  - При истечении тарифа (`EXPIRED`) доступ мастера к данным и клиентской базе сохраняется в полном объёме; онлайн-запись вежливо уведомляет клиентов о регламентных работах.
  - Защита от несанкционированного ручного продления в продакшене.

### 👤 Клиентский опыт в боте мастера (Customer Experience):
- **Single-Message UI:** Навигация и выбор слота происходят редактированием одного сообщения — чат остаётся безупречно чистым.
- **Умный расчёт свободных окон (`SlotEngine`):**
  - Учитывает индивидуальную длительность процедуры и обязательный буфер мастера после каждого клиента.
  - Учитывает рабочий график, перерывы на обед, выходные дни и исключения.
- **100% гарантия от овербукинга (Double-Booking Prevention):**
  - Механизм временного удержания слота (`HOLD`) на 30 минут для перевода предоплаты.
  - Строгие ограничения `EXCLUSION CONSTRAINT` на уровне PostgreSQL 16 с использованием `btree_gist` и блокировки `SELECT ... FOR UPDATE`.
- **Предоплата и загрузка чека:**
  - Автоматический расчёт суммы (фиксированная или % от услуги).
  - Реквизиты мастера с удобным копированием номера карты.
  - Загрузка чека (фотографией или документом) для быстрой сверки.
- **Личный кабинет «Мои записи»:**
  - Просмотр актуальных и прошедших визитов, отмена с учётом политики студии.
- **Портфолио и контакты:**
  - Галерея работ по категориям, адрес, схема проезда и ссылки на соцсети.

### 👑 Кабинет мастера в боте (`/admin`):
- **Входящие чеки (Inbox):** Мгновенные уведомления о новых оплатах с кнопками подтверждения или аргументированного отклонения («Не поступил платёж», «Неверная сумма», «Нечитаемый чек»).
- **Журнал визитов:** Фильтры по статусам (*Сегодня*, *Завтра*, *Ожидают проверки*, *Ожидают оплаты*), карточка визита, перенос даты (*Reschedule*), отметка о неявке (*No-Show*).
- **Гибкий график:** Быстрое закрытие дат, индивидуальные часы работы на конкретный день, блокировка перерывов.
- **Ручная запись клиентов (Walk-in):** Быстрое добавление записи мастером без прохождения клиентского пути.
- **Клиентская база (CRM):** Поиск гостей по имени, телефону или `@username`, расчёт **LTV** (совокупный доход), процент отмен, заметки о предпочтениях.
- **Автоматические напоминания клиентам:**
  - За 24 часа: напоминание о записи на завтра с суммой к доплате на месте.
  - За 3 часа: финальное напоминание с адресом студии.
- **Бизнес-аналитика:** Доход от услуг, удержанные предоплаты, валовая прибыль, средний чек, топ услуг за день, месяц или за всё время.

---

## 🛠 Технологический стек

| Направление | Стек | Назначение |
|---|---|---|
| **Язык разработки** | Python 3.12+ | Строгая типизация, современный асинхронный синтаксис |
| **Telegram Framework** | `aiogram 3.14+` | Асинхронные роутеры, фильтры, FSM и middlewares |
| **HTTP Webhook Engine** | `FastAPI` + `uvicorn` | Приём входящих апдейтов, потоковый лимитер, health checks |
| **База данных** | PostgreSQL 16 | Реляционные данные, `btree_gist` exclusion constraints |
| **ORM / Драйвер** | `SQLAlchemy 2.0` + `asyncpg` | Асинхронный пул соединений, строгая типизация Mapped |
| **Миграции БД** | `Alembic` | Версионирование структуры таблиц (0001–0008) |
| **Кэш & Брокер** | `Redis 7` | FSM состояний, дедупликация апдейтов, Pub/Sub инвалидация |
| **Криптография** | `cryptography` (AES-256-GCM) | Аутентифицированное шифрование токенов ботов с AAD |
| **Фоновые задачи** | `APScheduler 3.10+` | Распределённый планировщик с advisory locks |
| **Обратный прокси** | `Caddy` / `NGINX` | TLS 1.2/1.3, Rate-limiting, HSTS, защита от атак |
| **Контейнеризация** | `Docker` + `Docker Compose` | Развёртывание под непривилегированным пользователем `appuser` |

---

## 📁 Структура проекта

```
zapisflow/
├── app/
│   ├── bot/                  # Клиентский бот и панель мастера (/admin)
│   │   ├── filters/          # Фильтры прав (IsAdminFilter per-master)
│   │   ├── handlers/         # Хэндлеры (client, admin)
│   │   ├── keyboards/        # Фабрики inline-клавиатур
│   │   ├── middlewares/      # TenantContext, DbSession, UserContext
│   │   └── states/           # FSM-состояния визардов
│   ├── manager_bot/          # Управляющий бот платформы
│   │   ├── handlers.py       # Онбординг, подключение ботов, биллинг
│   │   ├── keyboards.py      # Клавиатуры управляющего бота
│   │   └── dispatcher.py     # Изолированный диспетчер платформы
│   ├── config/
│   │   └── settings.py       # Pydantic Settings v2 и fail-fast валидация
│   ├── core/
│   │   ├── security.py       # Фильтрация секретов и маскирование в логах
│   │   └── token_crypto.py   # AES-256-GCM authenticated шифрование токенов
│   ├── database/
│   │   ├── models/           # SQLAlchemy 2.0 модели (Master, BotInstance, Appointment, etc.)
│   │   └── session.py        # Асинхронный движок и фабрика сессий
│   ├── repositories/         # Репозитории доступа к данным
│   ├── scheduler/            # MultiTenantScheduler и фоновые воркеры
│   ├── services/             # Сервисный слой (BotRegistry, Booking, Provisioning, Billing)
│   ├── web/
│   │   └── app.py            # FastAPI Webhook Ingestion Engine
│   └── main.py               # Точка входа приложения
├── alembic/                  # Миграции базы данных (версии 0001–0008)
├── deploy/                   # Конфигурации обратных прокси
│   ├── caddy/Caddyfile       # Caddy с авто-TLS Let's Encrypt
│   └── nginx/nginx.conf      # Защищённый NGINX с rate-limiting
├── docs/                     # Документация
│   ├── production_deployment.md  # Регламент промышленного развёртывания
│   └── security_checklist.md     # Чек-лист безопасности и аудита
├── scripts/                  # Скрипты обслуживания и верификации бэкапов
├── tests/                    # 237 автоматических тестов (pytest)
├── docker-compose.yml        # Оркестрация контейнеров
├── Dockerfile                # Multi-stage сборка с non-root пользователем
├── requirements.txt          # Зависимости Python
└── .env.example              # Пример переменных окружения
```

---

## 🔒 Безопасность и отказоустойчивость

- **Криптографическая защита токенов:** Все токены Telegram-ботов шифруются алгоритмом **AES-256-GCM** с привязкой **Associated Authenticated Data (AAD)** к идентификатору бота (`telegram_bot_id`). Подмена или расшифровка токена другим ботом невозможна на уровне протокола.
- **Очистка FSM в Redis:** Временные plaintext-токены удаляются из состояния памяти сразу после валидации.
- **Защита вебхуков от DoS и OOM:** Потоковый ограничитель `read_limited_request_body` читает тело запроса порциями и прерывает соединение с ошибкой `413 Payload Too Large`, исключая переполнение памяти при `Transfer-Encoding: chunked`.
- **Timing-Safe сравнение:** Секретные заголовки `X-Telegram-Bot-Api-Secret-Token` проверяются строго через `secrets.compare_digest`, исключая атаки по времени.
- **Идемпотентная дедупликация апдейтов:** Входящие `update_id` дедуплицируются атомарно в Redis (`SET NX EX`).
- **Строгая многотенантность:** Полный отказ от глобальной модели администраторов. Владелец студии A имеет доступ строго к студии A.
- **Санитизация Health Checks:** Эндпоинт `/health/ready` скрывает детали подключения и пароли БД/Redis, отдавая только безопасный статус.

---

## ⚙️ Настройка переменных окружения (`.env`)

Создайте файл `.env` на основе примера:
```bash
cp .env.example .env
```

Основные параметры:
```env
# Режим окружения (development / production)
APP_ENV=production
APP_MODE=webhook
DOMAIN=api.yourdomain.com

# Публичный URL для вебхуков (обязательно HTTPS в production)
WEBHOOK_BASE_URL=https://api.yourdomain.com
WEBHOOK_HOST=0.0.0.0
WEBHOOK_PORT=8000
WEBHOOK_MAX_BODY_BYTES=1048576

# PostgreSQL 16 (asyncpg)
POSTGRES_PASSWORD=secure_password
DATABASE_URL=postgresql+asyncpg://postgres:secure_password@postgres:5432/beauty_bot_db
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=20
DB_POOL_PRE_PING=True

# Redis 7
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=strong_redis_password
REDIS_DB=0

# Криптографический ключ AES-256-GCM (32 байта Base64)
# Генерация: python -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"
BOT_TOKEN_ENCRYPTION_KEY=ВашКлюч32БайтаBase64==

# Платформенный управляющий бот (Manager Bot)
MANAGER_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
MANAGER_WEBHOOK_SECRET=СлучайнаяСтрокаМинимум32Символа

# Фоновый планировщик
SCHEDULER_ENABLED=True
SCHEDULER_TICK_SECONDS=30

# Биллинг
TRIAL_DURATION_DAYS=14
```

---

## 🚀 Запуск проекта

### Вариант 1. Запуск через Docker Compose (Рекомендуемый для Production)

```bash
# 1. Запуск БД и Redis
docker compose up -d postgres redis

# 2. Применение миграций Alembic
docker compose run --rm migrate

# 3. Запуск веб-приложения и Caddy Reverse Proxy
docker compose up -d backend caddy
```

Проверка статуса:
```bash
curl -i https://api.yourdomain.com/health/live
curl -i https://api.yourdomain.com/health/ready
```

### Вариант 2. Локальный запуск для разработки

```bash
# 1. Активация виртуального окружения
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 2. Установка зависимостей
pip install -r requirements.txt

# 3. Применение миграций
alembic upgrade head

# 4. Запуск в режиме разработки
python -m app.main
```

---

## 🧪 Тестирование

Проект покрыт автоматизированными модульными, интеграционными и E2E тестами (237 тестов, 100% успешно):
```bash
pytest -v
```

Тестирование охватывает:
- Миграции Alembic (0001–0008) и целостность схемы.
- Межтенантную изоляцию и невозможность подмены bot token.
- Потоковый ограничитель тела запроса и защиту от DoS.
- Конкурентное бронирование и предотвращение овербукинга.
- Идемпотентность продления подписок и блокировку ручного биллинга в прод.
- Резервное копирование (`pg_dump`) и восстановление (`pg_restore`) с проверкой ключей шифрования.

---

## 📄 Лицензия

Проект распространяется под лицензией MIT. Подробности в файле [LICENSE](LICENSE).
