# YooKassa: оплата SaaS-подписки

Используется существующий durable checkout, не Telegram Payments. Цена и срок берутся из активного SubscriptionPlan в PostgreSQL; оплаченный заказ хранит их snapshot.

## Окружение

```dotenv
PAYMENT_PROVIDER=yookassa
PAYMENT_CURRENCY=RUB
YOOKASSA_MODE=live
YOOKASSA_SHOP_ID=
YOOKASSA_SECRET_KEY=
YOOKASSA_ALLOW_TEST_IN_PRODUCTION=false
WEBHOOK_BASE_URL=https://api.zapisflow.su
BILLING_RETURN_URL=https://api.zapisflow.su/billing/yookassa/return
YOOKASSA_FISCAL_MODE=self_employed
```

Заполните live credentials только в серверном `.env`/secret store. Старое значение `yookassa_web` остаётся совместимым alias. `manual` запрещён при запуске production; `disabled` безопасно отключает оплату.

Для тестов используются исключительно `YOOKASSA_TEST_SHOP_ID` и `YOOKASSA_TEST_SECRET_KEY`. На production дополнительно обязательны `YOOKASSA_ALLOW_TEST_IN_PRODUCTION=true` и непустой `YOOKASSA_TEST_ALLOWED_TELEGRAM_IDS` — список Telegram ID разрешённых владельцев. Test/live credentials не смешиваются.

## Поток и проверки

Manager Bot → выбранный DB plan → локальный PENDING order → commit → YooKassa POST → confirmation URL. Safe request snapshot (описание, return URL, receipt/email без credentials) фиксируется перед первым POST и не меняется при retry. Stable checkout UUID — Idempotence-Key; после 23 часов новый POST с этим UUID запрещён, чтобы не пересечь срок хранения ключа провайдером.

Webhook: **POST https://api.zapisflow.su/billing/yookassa/webhook**. В кабинете магазина настройте `payment.succeeded` и `payment.canceled`. Входящее уведомление лишь указывает на платёж; подтверждение получается через authenticated GET API. Проверяются remote ID, UUID заказа, сумма, валюта и tenant metadata. Проверка в боте и reconciliation используют тот же путь активации.

Один платёж создаёт один SubscriptionPeriod: PostgreSQL row lock и UNIQUE защищают от replay и параллельной обработки. `waiting_for_capture` не активирует подписку; приложение создаёт single-stage платежи (`capture=true`), автоматический capture неподтверждённого двухстадийного платежа не выполняется. SUSPENDED не снимается оплатой.

`SubscriptionService.create_subscription_payment` при выбранной YooKassa только stage-ит заказ в транзакции вызывающего кода. Сначала commit, затем `YooKassaCheckoutService.start_checkout`; сетевой POST до commit запрещён.

## Чеки

`self_employed`: чек выдаётся через «Мой налог»; не отправляется искусственный receipt. YooKassa прекратила автоматическое формирование чеков для самозанятых 29 декабря 2025 года: https://yookassa.ru/developers/using-api/changelog .

`merchant_receipt`: настройте реальные значения `YOOKASSA_RECEIPT_VAT_CODE`, `YOOKASSA_RECEIPT_PAYMENT_SUBJECT` и `YOOKASSA_RECEIPT_PAYMENT_MODE`. Manager Bot запросит email покупателя. Для услуги/предоплаты API поддерживает `service`/`full_prepayment`; VAT не выбирается приложением. Настройку чеков и необходимость чека зачёта предоплаты подтвердите у магазина: https://yookassa.ru/developers/payment-acceptance/receipts/basics .

## Выкатка

Сначала проверить clean git и создать PostgreSQL/.env backups. Использовать актуальную ветку, без reset/force:

```bash
git fetch origin
git pull --ff-only origin codex/yookassa-web-billing
docker compose -f docker-compose.yml -f deploy/miniapp/compose.yml config -q
docker compose -f docker-compose.yml -f deploy/miniapp/compose.yml build
docker compose run --rm migrate alembic upgrade head
docker compose -f docker-compose.yml -f deploy/miniapp/compose.yml up -d
docker compose -f docker-compose.yml -f deploy/miniapp/compose.yml ps
curl -fsS https://api.zapisflow.su/health/live
curl -fsS https://api.zapisflow.su/health/ready
docker compose logs backend --since=15m
```

Migration `2026_10_04_0027` разрешает NULL BotInstance в существующем outbox для Manager Bot. Финансовое состояние и уведомление владельцу сохраняются одной транзакцией; worker проверяет владельца tenant и разрешает Manager token только в памяти. Token не попадает в payload. Tenant-доставки продолжают требовать BotInstance.

Telegram message delivery остаётся at-least-once: crash после принятия сообщения Telegram до фиксации SENT может дать редкое повторное уведомление. Бизнес-продление при этом не повторяется.

Проверка текущего head:

```bash
docker compose run --rm migrate alembic current
docker compose run --rm migrate alembic heads
```

Перед live нужен тестовый платёж разрешённого владельца, webhook/API проверка и проверка повторной доставки без второго продления. Не включайте live только на основании зелёных unit tests.

## Проверки реализации (4 октября 2026)

- Полный pytest на изолированных PostgreSQL 16 / Redis 7: **703 passed, 0 failed, 0 skipped, 6 warnings**.
- Финальные targeted checkout/startup/outbox tests: **56 passed**; отдельные config/client/provider tests: **36 passed**.
- `python -m compileall app tests`, `git diff --check`: PASS.
- Alembic: один head `2026_10_04_0027`; fresh upgrade и migration regression suite прошли.
- Docker Compose config и backend Docker build: PASS. Production stack не перезапускался.
- Новый provider проходит Ruff; в проверенных legacy-файлах 35 замечаний против исходных 36 (не устранялись несвязанные lint-проблемы).
- Реальный платёж текущей версии не проводился; production credentials/config не менялись.

## Изменённые файлы

- `.env.example`
- `README.md`
- `alembic/versions/2026_10_04_0027_manager_outbox.py`
- `app/config/settings.py`
- `app/database/models/telegram_outbox.py`
- `app/manager_bot/handlers.py`
- `app/manager_bot/keyboards.py`
- `app/manager_bot/states.py`
- `app/scheduler/jobs/telegram_outbox_worker.py`
- `app/scheduler/jobs/yookassa_reconciliation.py`
- `app/scheduler/scheduler.py`
- `app/services/billing/__init__.py`
- `app/services/billing/yookassa_checkout.py`
- `app/services/billing/yookassa_client.py`
- `app/services/billing/yookassa_provider.py`
- `app/services/subscription_notification_service.py`
- `app/services/subscription_service.py`
- `app/services/telegram_outbox.py`
- `app/web/app.py`
- `docs/yookassa-billing.md`
- `tests/test_alembic_migrations.py`
- `tests/test_phase_10_production.py`
- `tests/test_telegram_outbox.py`
- `tests/test_webhook_runtime_regressions.py`
- `tests/test_yookassa_checkout.py`
- `tests/test_yookassa_payment_check.py`
- `tests/test_yookassa_provider.py`
