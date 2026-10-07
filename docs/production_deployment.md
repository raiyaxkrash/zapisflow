# Production deployment ZapisFlow

Текущий продукт: Telegram Bot + Telegram Mini App + marketing website.
Действующая инструкция для redesigned Mini App и безопасного обновления:
[Deployment checklist](MINIAPP_DEPLOYMENT_CHECKLIST.md).

Базовая конфигурация: Docker Compose, PostgreSQL 16, Redis 7, FastAPI backend,
одноразовый migrate и Caddy. Mini App подключается через
`deploy/miniapp/compose.yml`. Marketing собирается Vite; его static hosting
определяется установленным оператором overlay, а не отдельной booking subsystem.

Не запускайте production deployment по результатам локального QA автоматически.
Перед обновлением нужны clean Git, backup БД/.env, одобренный SHA, все текущие
Compose overlays и успешная миграция. После — health, реальный Telegram UAT и logs.
Нельзя использовать down -v, удалять volumes или переписывать применённые migrations.

Настройки: [.env.example](../.env.example). Схема: [Mini App architecture](miniapp.md).
Платежи подписки: [YooKassa](yookassa-billing.md). Client prepayment — отдельный
существующий Payment/PaymentProof flow, не SaaS acquiring.
