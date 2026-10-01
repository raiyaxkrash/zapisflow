# Руководство по развёртыванию платформы в продакшн (Production Deployment Guide)

Настоящий документ описывает регламент подготовки, настройки и промышленного запуска мультитенантной SaaS-платформы Beauty Bot.

---

## 1. Системные требования

### Рекомендуемая конфигурация хоста (VPS / Bare Metal):
- **ОС:** Linux (Ubuntu 22.04 LTS / Debian 12 x86_64)
- **CPU:** минимум 2 ядра (рекомендуется 4 ядра)
- **RAM:** минимум 4 GB (рекомендуется 8 GB)
- **Диск:** минимум 40 GB NVMe/SSD
- **Сеть:** статический публичный IPv4, открытые порты 80 (HTTP) и 443 (HTTPS)
- **Доменное имя:** A-запись, направленная на IP сервера (например, `api.beautybot.example.com`)

### Необходимое ПО на сервере:
- **Docker Engine:** 24.0+
- **Docker Compose:** v2.20+
- **PostgreSQL Client (pg_dump / pg_restore):** 16.x
- **Reverse Proxy:** Caddy 2.7+ (рекомендуется за счёт встроенного Let's Encrypt TLS) или NGINX 1.24+

---

## 2. Архитектура продакшн-контура

```
                       ┌──────────────────────┐
                       │  Telegram API Cloud  │
                       └──────────┬───────────┘
                                  │ HTTPS Webhook
                                  ▼
                       ┌──────────────────────┐
                       │ Caddy / NGINX Proxy  │ (TLS 1.2/1.3, Rate-Limit, 2MB limit)
                       └──────────┬───────────┘
                                  │ HTTP :8000
                                  ▼
               ┌──────────────────────────────────────┐
               │         FastAPI Backend              │ (Non-root 'appuser')
               ├──────────────────────────────────────┤
               │ • /telegram/webhook/{public_bot_id}  │
               │ • /telegram/manager-webhook          │
               │ • /health/live & /health/ready       │
               │ • MultiTenantScheduler               │
               └──────────┬────────────────┬──────────┘
                          │                │
             Async Engine │                │ Redis Protocol
                          ▼                ▼
             ┌─────────────────┐  ┌──────────────────┐
             │  PostgreSQL 16  │  │     Redis 7      │
             │ (Data & Audits) │  │(Dedup, Locks, PS)│
             └─────────────────┘  └──────────────────┘
```

---

## 3. Настройка переменных окружения (`.env`)

Создайте файл `.env` в корне проекта (права доступа `chmod 600 .env`):

```bash
# Окружение
APP_ENV=production
APP_MODE=webhook
DOMAIN=api.beautybot.example.com

# Вебхук и публичный домен (строго HTTPS)
WEBHOOK_BASE_URL=https://api.beautybot.example.com
WEBHOOK_HOST=0.0.0.0
WEBHOOK_PORT=8000
WEBHOOK_MAX_BODY_BYTES=1048576

# База данных PostgreSQL (строго уникальные пароли!)
POSTGRES_PASSWORD=SUPER_STRONG_PASSWORD
DATABASE_URL=postgresql+asyncpg://postgres:SUPER_STRONG_PASSWORD@postgres:5432/beauty_bot_db
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=20
DB_POOL_PRE_PING=True

# Redis 7 (с обязательным паролем)
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=SUPER_STRONG_REDIS_PASSWORD
REDIS_DB=0

# Криптографический мастер-ключ AES-256-GCM (32 байта base64)
# Генерация: python -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"
BOT_TOKEN_ENCRYPTION_KEY=YourGenerated32ByteBase64KeyHere==

# Управляющий бот платформы (Manager Bot)
MANAGER_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
# Секретный заголовок для проверки вебхука управляющего бота (минимум 32 символа)
# Генерация: python -c "import secrets; print(secrets.token_urlsafe(32))"
MANAGER_WEBHOOK_SECRET=YourGeneratedSecureWebhookSecretString32CharsLong

# Фоновый планировщик (MultiTenantScheduler)
SCHEDULER_ENABLED=True
SCHEDULER_TICK_SECONDS=30
SCHEDULER_BATCH_SIZE=100

# Пока платёжный провайдер не подключён, онлайн-оплата выключена.
PAYMENT_PROVIDER=disabled
TRIAL_DURATION_DAYS=14
```

`REDIS_PASSWORD` обязателен; используйте латинские буквы, цифры, `_` и `-`.

---

## 4. Развёртывание и миграции базы данных

### Шаг 1. Сборка и запуск контейнеров базы данных и Redis:
```bash
docker compose up -d postgres redis
```

### Шаг 2. Применение миграций Alembic:
```bash
docker compose run --rm migrate
```

### Шаг 3. Запуск веб-приложения и обратного прокси:
```bash
docker compose up -d backend caddy
```

---

## 5. Проверка работоспособности (Health Checks)

1. **Liveness Check:**
   ```bash
   curl -i https://api.beautybot.example.com/health/live
   # Ожидаемый ответ: HTTP 200 {"status":"alive"}
   ```

2. **Readiness Check (проверка PostgreSQL и Redis):**
   ```bash
   curl -i https://api.beautybot.example.com/health/ready
   # Ожидаемый ответ: HTTP 200 {"status":"ready","database":"ok","redis":"ok"}
   ```

---

## 6. Регламент резервного копирования и восстановления

### Резервное копирование PostgreSQL (ежедневно по cron):
```bash
# Дамп в сжатом бинарном формате custom (-F c)
docker compose exec -T postgres pg_dump -U postgres -d beauty_bot_db -F c -b -v > /backups/postgres_$(date +%Y%m%d_%H%M%S).dump
```

### Восстановление PostgreSQL из резервной копии:
```bash
# Восстановление в чистую БД
docker compose exec -T postgres pg_restore -U postgres -d beauty_bot_db --clean --if-exists -v < /backups/postgres_20261001_000000.dump
```

### Резервное копирование Redis:
```bash
docker compose exec -T redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli BGSAVE'
docker compose cp redis:/data/dump.rdb /backups/redis_$(date +%Y%m%d_%H%M%S).rdb
```

---

## 7. Регламент пилотного запуска мастера (Pilot Onboarding SOP)

1. Мастер открывает Manager Bot и отправляет команду `/start`.
2. Регистрирует проект (студию).
3. Передаёт полученный в BotFather токен бота.
4. Платформа проверяет токен через `getMe`, шифрует его ключом AES-256-GCM с привязкой AAD к telegram_bot_id и регистрирует вебхук в Telegram.
5. Мастер настраивает услуги, график работы и платёжные реквизиты.
6. В пилотной фазе тариф активируется платформенным администратором (так как автоматический платёжный шлюз не интегрирован).
7. Бот переходит в статус `ACTIVE` и начинает принимать клиентские записи.
