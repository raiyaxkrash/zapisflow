# Telegram-бот для онлайн-записи к частному мастеру

Промышленный асинхронный Telegram-бот для онлайн-записи клиентов к мастеру с подтверждением предоплаты, расчетом свободных окон, админ-панелью внутри Telegram и CRM-модулем.

## 🛠 Технологический стек
- **Python**: 3.12+
- **Фреймворк бота**: `aiogram 3.14+`
- **СУБД**: `PostgreSQL 16`
- **ORM / Migrations**: `SQLAlchemy 2.0 (asyncpg)` + `Alembic`
- **Кэш / FSM / Distributed Lock**: `Redis 7`
- **Конфигурация**: `Pydantic Settings v2`
- **Контейнеризация**: `Docker` + `docker-compose`

## 🚀 Быстрый старт

### 1. Клонирование и настройка окружения
Скопируйте шаблон переменных окружения и укажите ваш токен бота и ID администраторов:
```bash
cp .env.example .env
```

Отредактируйте `.env`:
- `BOT_TOKEN`: токен от [@BotFather](https://t.me/BotFather)
- `ADMIN_IDS`: ваш числовой Telegram ID

### 2. Запуск через Docker Compose (Рекомендуемый способ)
Запустите базу данных, Redis и бота одной командой:
```bash
docker-compose up -d --build
```

### 3. Локальный запуск для разработки
```bash
python -m venv .venv
source .venv/bin/activate  # или .venv\Scripts\activate на Windows
pip install -r requirements.txt

# Применение миграций базы данных:
alembic upgrade head

# Запуск приложения:
python -m app.main
```
