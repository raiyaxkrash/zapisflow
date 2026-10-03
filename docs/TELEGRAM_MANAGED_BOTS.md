# Интеграция Telegram Managed Bots (Bot API 9.6) в ZapisFlow

## 1. Обзор функционала

В платформе **ZapisFlow** реализована официальная поддержка **Telegram Managed Bots** (Bot API 9.6). Это решение принципиально упрощает онбординг мастеров и владельцев студий:

- **Создание бота в 1 клик:** мастер больше не обязан открывать диалог с `@BotFather`, вручную выполнять команды `/newbot`, придумывать сложные технические параметры и копировать чувствительные API-токены.
- **Прямая интеграция с клиентом Telegram:** мастер подтверждает создание бота через нативный интерфейс Telegram (по ссылке `t.me/newbot/...` или через клавиатурную кнопку `KeyboardButtonRequestManagedBot`).
- **Автоматическая инициализация:** платформа получает токен от Telegram Bot API методом `getManagedBotToken`, шифрует его по алгоритму **AES-256-GCM**, привязывает к проекту мастера, регистрирует защищённый вебхук, базовые команды и кнопку Mini App.
- **Автоматическая ротация:** токен безопасности управляемого бота может быть обновлен в 1 клик через `replaceManagedBotToken`.
- **Сохранение классического способа:** подключение ранее созданного бота через ввод токена из `@BotFather` полностью сохранено в качестве резервного (fallback) механизма.

---

## 2. Архитектура решения

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                             Telegram Platform                               │
│                                                                             │
│  [ Мастер в Telegram ] ──── (1. Deep Link / Reply Button) ────► Telegram    │
│                                                                      │      │
│  [ Платформенный Manager Bot ] ◄── (2. ManagedBotCreated Update) ────┘      │
│            │                                                                │
│            ▼ (3. getManagedBotToken API call)                               │
│   Telegram Bot API ─────────────────────────────────────────────────────────┤
│            │ (4. HTTP API Token)                                            │
│            ▼                                                                │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                    ZapisFlow Backend Service                          │  │
│  │                                                                       │  │
│  │  1. ManagedBotService: транслитерация и формирование Handle/Link      │  │
│  │  2. TokenCrypto: шифрование AES-256-GCM с telegram_bot_id в AAD       │  │
│  │  3. BotInstanceRepository: запись в PostgreSQL                        │  │
│  │     (provisioning_source="managed_bot", managed_by_platform=true)     │  │
│  │  4. TelegramProvisioningGateway:                                     │  │
│  │     • setWebhook (HTTPS, drop_pending_updates=False)                  │  │
│  │     • setMyCommands (/start, /book, /my_bookings, /help)              │  │
│  │     • setChatMenuButton (Mini App WebAppInfo при наличии)             │  │
│  │  5. AuditService: фиксация событий MANAGED_BOT_CREATED/PROVISIONED    │  │
│  │  6. BotRegistry: инвалидация и регистрация тенанта                    │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Модели базы данных и миграции

### 3.1. Расширение таблицы `bot_instances`
Миграция: `alembic/versions/2026_10_03_0023_managed_bots_support.py`
- `provisioning_source` (`VARCHAR(32)`, по умолчанию `'manual_token'`) — источник подключения (`'manual_token'` или `'managed_bot'`).
- `managed_by_platform` (`BOOLEAN`, по умолчанию `false`) — признак бота, созданного через механизм Telegram Managed Bots.
- `telegram_owner_user_id` (`BIGINT`, `nullable`, индекс `ix_bot_instances_telegram_owner_user_id`) — Telegram User ID человека-владельца бота (используется строго для аудита, авторизации и разграничения прав доступа; **не** передаётся в вызовы токенов Bot API).

### 3.2. Таблица запросов на создание ботов `managed_bot_creation_requests`
Миграция: `alembic/versions/2026_10_03_0024_managed_bot_creation_requests.py`
Для надёжной, устойчивой к сбоям привязки создаваемого бота к конкретному проекту мастера (особенно когда у мастера несколько проектов или сессия FSM была очищена) используется специальная таблица запросов:
- `owner_user_id` (`BIGINT`) — внутренний ID пользователя.
- `telegram_owner_user_id` (`BIGINT`, индекс `ix_managed_bot_requests_telegram_owner`) — Telegram ID мастера.
- `master_id` (`BIGINT`) — ID целевого проекта.
- `request_id` (`BIGINT`, по умолчанию `1`) — ID запроса кнопки Telegram.
- `status` (`managed_bot_request_status`: `PENDING`, `COMPLETED`, `EXPIRED`, `FAILED`).
- `telegram_bot_id` (`BIGINT`, nullable) — ID созданного бота после завершения.
- `expires_at` (`TIMESTAMPTZ`) — время жизни запроса (по умолчанию 2 часа).
- **Частичный уникальный индекс:** `uq_pending_managed_bot_request_per_user` (`WHERE status = 'PENDING'`), гарантирующий наличие строго одного активного запроса на пользователя и предотвращающий состояние гонки.

---

## 4. Пользовательский сценарий (UX)

### 4.1. Выбор метода подключения
При нажатии кнопки «🤖 Подключить Telegram-бота» в карточке проекта мастеру предлагается выбор:
1. **✨ Создать нового бота в 1 клик (Рекомендуется)**
2. **🔑 Подключить через токен (BotFather)**

### 4.2. Экран подготовки Managed Bot
При выборе создания нового бота:
- На основе названия проекта генерируются читаемый логин бота (`suggested_username`) и название (`suggested_name`).
- Мастер видит экран с возможностями:
  - **🚀 Создать бота в Telegram:** переход по официальной ссылке Deep Link:
    `https://t.me/newbot/{manager_bot_username}/{suggested_bot_username}?name={suggested_bot_name}`
  - **⌨️ Создать через кнопку в чате:** отображение нативной кнопки Telegram `KeyboardButtonRequestManagedBot`.
  - **✏️ Изменить логин:** ввод желаемого логина с проверкой формата.
  - **🔑 Подключить токеном вручную:** быстрый переход к ручному вводу.

### 4.3. Обработка создания бота
1. Telegram передаёт в управляющий бот сервисное обновление (`message.managed_bot_created` или `ManagedBotUpdated`).
2. Система находит активный запрос мастера в таблице `managed_bot_creation_requests` и валидирует права на проект (никаких эвристик `masters[0]`).
3. Менеджер-бот через метод Bot API `getManagedBotToken(user_id=created_bot_user.id)` запрашивает выданный токен (в Bot API передаётся именно Telegram User ID созданного бота!).
4. Происходит шифрование токена и привязка к проекту мастера со статусом `SETUP_REQUIRED`. Запрос помечается как `COMPLETED`.
5. Устанавливается вебхук, команды и кнопка меню.
6. Мастер получает поздравление и кнопки прямого перехода в своего бота (`https://t.me/{username}?start=admin`).

### 4.4. Автоматическая ротация токена
Для ботов с флагом `managed_by_platform = True` в меню ротации доступна кнопка:
- **♻️ Обновить токен автоматически:** вызывает `replaceManagedBotToken(user_id=bot_instance.telegram_bot_id)` через платформенного бота, шифрует новый токен, увеличивает `token_version` и обновляет вебхук без ручных действий мастера.

---

## 5. Настройка окружения

Для полноценной работы функционала в файле `.env` должны быть настроены параметры:

```env
# Юзернейм платформенного управляющего бота (без @)
MANAGER_BOT_USERNAME=zapisflow_bot

# Токен платформенного управляющего бота
MANAGER_BOT_TOKEN=123456789:ABCdefGHI...

# Ключ шифрования токенов клиентов (32 байта / 64 hex символа)
BOT_TOKEN_ENCRYPTION_KEY=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef

# Опционально: URL Mini App для кнопки меню клиентов
MINI_APP_URL=https://app.zapisflow.su
```

### Настройка в @BotFather для Manager Bot:
1. Откройте диалог с `@BotFather`.
2. Отправьте `/mybots` и выберите вашего платформенного менеджера (например, `@zapisflow_bot`).
3. Перейдите в **Bot Settings** -> **Managed Bots**.
4. Включите разрешение **Can Manage Bots**.

---

## 6. Безопасность и отказоустойчивость

1. **Защита токенов:** токены управляемых ботов ни при каких обстоятельствах не выводятся в логи, не сохраняются в открытом виде и не передаются в FSM-хранилище. В базу данных сохраняется только AES-256-GCM шифротекст с привязкой к Telegram Bot ID в качестве Additional Authenticated Data (AAD).
2. **Идемпотентность:** дублирующиеся обновления от Telegram обрабатываются без создания паразитных экземпляров ботов или повторных установок тарифа.
3. **Изоляция проектов:** один и тот же Telegram-бот не может быть подключен одновременно к двум разным проектам. Проверка владения (`owner_user_id == actor_user_id`) предотвращает подмену тенантов (IDOR).
