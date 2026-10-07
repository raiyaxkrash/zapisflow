# Telegram Mini App ZapisFlow

Единый мобильный frontend обслуживает клиентский и мастерский режимы:
`MINI_APP_BASE_URL/b/<BotInstance.public_id>`. Каналы текущего продукта —
Telegram-бот, Telegram Mini App и маркетинговый сайт. Сайт не принимает записи;
Web Booking и website Login/OIDC удалены ([совместимость старых ссылок](WEB_BOOKING_RETIREMENT.md)).

## Архитектура

```text
Telegram chat → Caddy → FastAPI webhook → BotRegistry → aiogram/FSM
Telegram Mini App → Caddy → same-origin /api/miniapp → session/tenant adapters
                                                   ↓
                      BookingService / SlotEngine / PaymentService / repositories
                                                   ↓
                               PostgreSQL + Redis + Telegram Outbox
Marketing website → описание ZapisFlow → Manager Bot
```

Mini App — Vite/JavaScript с разделением application shell, client/master views,
API, theme, Telegram helpers и UI primitives. Тяжёлые мастерские экраны и их CSS,
branding editor и ICS загружаются отдельно. Backend остаётся источником цены,
длительности, доступности, прав, подписки и состояния записи.

## Два независимых входа

- **Кнопка сообщения «Записаться»** использует `menu:book` и существующий
  текстовый FSM. У неё нет `web_app`; Mini App не является зависимостью записи.
- **Системная Menu Button «ZapisFlow»** использует `MenuButtonWebApp` и
  `MINI_APP_BASE_URL/b/<public_id>` при включённом Mini App и валидном base URL.
  При OFF/пустом URL используется `MenuButtonCommands`.
- `mini_app_enabled=false` закрывает Mini App API (`403 MINI_APP_DISABLED`),
  включая старые сессии, и не отключает текстовую запись.

## Клиентский интерфейс

Навигация: **Главная → Записаться → Мои записи → Ещё**.

Главная показывает бизнес, доступные услуги, записи и настроенные разделы.
Запись: услуга → сотрудник/«Любой» → серверный календарь → слот → телефон и
условия → резерв/подтверждение → success или ручная предоплата. Слот повторно
проверяется сервером; при конфликте клиент возвращается к выбору времени.
Ближайшее время вычисляет SlotEngine. Повторная запись переносит только выбор
услуги/сотрудника и требует новую дату. ICS не требует внешнего аккаунта.

Мои записи разделены на предстоящие/прошедшие, имеют детали, разрешённую policy
отмену и продолжение незавершённого резерва. Портфолио, отзывы, контакты и
информация о бизнесе показывают реальные данные и учитывают видимость разделов.

## Рабочее пространство мастера

Навигация: **Сегодня → Календарь → Клиенты → Ещё**.

Дневные записи и карточка, ручная запись существующего клиента, CRM
поиск/история/заметки, услуги, сотрудники/привязка услуг, недельный график,
перерывы и отдельные даты сотрудника. Изменение графика предупреждает об
имеющихся записях и не удаляет их. Также доступны проверки предоплаты,
портфолио, отзывы, рассылки, аналитика, системные настройки и owner-only оформление.

Новый клиент и приглашение сотрудника создаются через существующий Telegram
интерфейс; Mini App не добавляет отдельную invite subsystem. STAFF видит
разрешённые данные своего расписания; CRM/settings/payment decisions доступны
OWNER/ADMIN. Оформление и профиль бота меняет OWNER. SaaS billing и Platform
Admin остаются в Manager Bot.

## Оформление

`MasterSettings.branding` и `MasterBrandAsset` относятся к проекту. Mini App и
клиентский бот используют один бренд. Редактор поддерживает название, tagline,
описание, приветствие, CTA, accent, theme/preset, видимость разделов, logo/cover,
контакты и reset. Черновик и live preview не публикуются до Save.

Logo/cover: PNG/JPEG/WebP, исходник до 4 MiB и 16 MP, серверная нормализация WebP,
максимум 320×320 и 1200×600. SVG/HTML/CSS/JS и пользовательские шрифты запрещены.
Logo/cover являются намеренно публичными изображениями бизнеса; private
portfolio/чеки/CRM остаются авторизованными. `Работает на ZapisFlow` не скрывается.

[Редактор](MINIAPP_BRANDING_EDITOR.md) · [Модель и безопасность](BRANDING_AND_MINIAPP.md)
· [Telegram-профиль](TELEGRAM_BOT_BRANDING.md).

## Авторизация и безопасность

Frontend передаёт исходный `Telegram.WebApp.initData`, не доверяет
`initDataUnsafe`. Backend проверяет подпись токеном конкретного бота, возраст,
формат identity, replay и состояние проекта/бота. Один Telegram ID соответствует
существующему User; новая модель пользователей Mini App не создаётся.

Cookie `__Host-zapisflow-miniapp`: Secure, HttpOnly, SameSite=None, path=/, без
Domain. PostgreSQL хранит хеши коротких session/CSRF/initData credentials.
Сессия по умолчанию 1800 секунд, initData — 300 секунд. Повторная авторизация
действующей сессии меняет CSRF; token rotation, expiry и disable закрывают доступ.
Каждый запрос сверяет `X-MiniApp-Bot` с серверной сессией.

Mutations требуют точный Origin, CSRF и UUID Idempotency-Key. Авторизация
вычисляется сервером, entity IDs проверяются внутри tenant. Business mutation,
audit, Outbox и MiniAppOperation фиксируются единой DB-транзакцией до ответа.
Повтор ключа возвращает прежний результат; изменённый payload отклоняется.
BookingService locks и PostgreSQL exclusion constraint защищают слот.

Telegram Outbox — at-least-once: сбой между доставкой и фиксацией SENT может
дублировать сообщение, но не должен повторять бизнес-операцию. Raw tokens,
initData, cookies и CSRF не включаются в пользовательские ответы/логи.

## Платежи

**Клиент → мастер:** существующие Appointment/Payment/PaymentProof, реквизиты,
загрузка JPEG/PNG чека до 8 MiB, серверное декодирование/перекодирование и проверка
OWNER/ADMIN. Online acquiring клиента не добавлен; фиктивных payment links нет.
**Владелец → ZapisFlow:** существующая SaaS подписка/YooKassa через Manager Bot.
Эти домены не объединяются.

## API

Примеры текущих транспортных адаптеров (не отдельная booking system):

| Пути | Назначение |
| --- | --- |
| `/api/miniapp/auth`, `/context` | Вход и доверенный контекст |
| `/client/services`, `/client/staff`, `/client/availability/calendar`, `/client/slots`, `/client/availability/nearest` | Каталог/доступность |
| `/client/holds`, `/client/appointments` | Резерв, подтверждение и записи |
| `/client/appointments/{id}/cancel`, `/payment`, `/proof` | Отмена и предоплата |
| `/master/appointments`, `/master/clients`, `/master/services`, `/master/staff` | Рабочие данные |
| `/master/schedule`, `/master/schedule/calendar`, `/master/settings` | График и настройки |
| `/master/payments`, `/master/portfolio`, `/client/reviews`, `/master/broadcasts`, `/master/analytics` | Рабочие разделы |
| `/master/branding`, `/save`, `/reset`, `/assets/{kind}`, `/sync-telegram` | Owner-only оформление (полный prefix: `/api/miniapp/master/branding`) |
| `/api/branding/{public_id}/assets/{kind}` | Публичные logo/cover |

Остальные строки таблицы имеют prefix `/api/miniapp`. Точные методы и схемы —
в `app/web/miniapp.py` / `app/web/branding.py`; suffix `/payment` относится к
конкретной записи, а не к SaaS billing.

## Темы и Telegram UX

System/Light/Dark без reload; System учитывает Telegram colorScheme, вне
Telegram — prefers-color-scheme. Brand accent получает безопасный foreground,
hover/active/soft/focus tokens. Telegram bridge обслуживает BackButton, chrome,
viewport/safe areas и haptics. Приложение учитывает reduced motion, keyboard,
labels и semantic forms. [Дизайн-система](MINIAPP_DESIGN_SYSTEM.md).

## Локальная проверка

```sh
cd miniapp
npm ci
npm test
npm run build
npm run dev
```

Реальный вход требует Telegram, подписанного initData, HTTPS и same-origin API.
Production auth bypass отсутствует. Browser QA подменяет сеть/Telegram только
в локальном harness; это не реальный Telegram UAT.

Backend suite: `pytest -q` с отдельными disposable PostgreSQL/Redis через
`TEST_DATABASE_URL`/`TEST_REDIS_URL`. Миграционные тесты меняют схему: никогда
не используйте production или базу с нужными данными.

## Схема и deployment

`0025`: MiniAppSession/MiniAppOperation; `0026`: switch; `0029`: branding и assets.
Применённая история не переписывается; одна head `2026_10_05_0029`.
Новых secrets/volumes для branding не требуется, изображения хранятся в БД.
`MINI_APP_BASE_URL` по умолчанию пуст; остальные настройки — в `.env.example`.

[Будущий deployment checklist](MINIAPP_DEPLOYMENT_CHECKLIST.md) ·
[Финальный QA](MINIAPP_FINAL_QA.md). Deploy и native Telegram UAT в этой ветке
не выполняются; локальные проверки не являются заявлением о production readiness.
