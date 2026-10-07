# Удаление браузерной записи — Фаза 2

ZapisFlow принимает клиентские записи через Telegram text/FSM и Telegram Mini App. Маркетинговый сайт остаётся landing и ведёт в Manager Bot; отдельный website booking transport удалён.

## Что удалено

- Frontend WebBooking, его stylesheet и website-only branding helper; соответствующие OIDC/booking tests.
- FastAPI website booking/auth routers и Telegram OIDC implementation: state/nonce/PKCE, browser cookie/session issuance и website Redis runtime больше не выполняются.
- Website opt-in service method, Manager Bot toggle, website Vite API proxy и promises в marketing copy.
- Website-only Settings/env example и инструкция Telegram Login. Реальные .env не менялись.

## Старые ссылки и сообщения

`/book/*`, `/book` и `/account/bookings` в marketing SPA показывают branded unavailable screen с переходом на `/`. Формы записи/login и API requests отсутствуют. Это экран приложения, не заявление о серверном HTTP 404 для static SPA.

Старые `/api/web-booking/*` и `/api/auth/*` endpoints не зарегистрированы и возвращают 404. Старые Manager callbacks `mgr:bot:web:*` только показывают alert об удалённом канале; не меняют БД и не предоставляют информацию о проекте. Expired callbacks используют существующий безопасный answer helper.

## Что сохранено

BookingService, SlotEngine, User identity, Appointment, Staff/Service, weekly/date schedules, client Payment/PaymentProof и SaaS billing не удалялись. Mini App initData, session, Origin, CSRF, tenant/RBAC, subscription gating и mutation idempotency сохранены. Mini App визуально не изменён.

История Alembic не переписана: head остаётся `2026_10_05_0029`, включая historical `0028`. `BotInstance.web_booking_enabled` остаётся deprecated mapped column, не читается и не записывается production feature code. Старые true значения ничего не включают. Новая/destructive migration не нужна.

## Оставшиеся references

- Alembic `0028` и deprecated ORM column — совместимость БД.
- MINIAPP_REDESIGN_AUDIT.md — явно помеченный snapshot Фазы 1, не текущая продуктовая инструкция.
- Retirement documentation и tests — отрицательные checks удалённых routes/config и независимости Mini App от deprecated flag.
- Marketing legacy pathname guard и Manager legacy callback — только безопасный unavailable response.
- «Website» в SaaS checkout/billing — другой payment domain, не клиентская веб-запись; сохраняется.

Нет активных OIDC/login/session/booking handlers для сайта. Shared semantic tokens между marketing и Mini App сохранены. Website-only color-helper tests удалены вместе с helper; equivalent Mini App contrast/URL/tenant tests сохранены.

## Staff invite

Mismatch аргументов уже отсутствует в базе `1712ee0`. Handler передаёт `raw_token` и internal `User.id`; repository claim atomic UPDATE WHERE RETURNING. Существующие regressions покрывают valid/expired/used/invalid token, internal identity, wrong-tenant token creation и concurrent claim. В этой фазе staff invite domain code не изменялся.

## Проверки и ограничения

Targeted backend: 113 passed. Mini App: 43 passed, build PASS, client/master browser journey PASS с mock API. Marketing: 20 passed, build PASS; real Chromium на главной и обоих retired pages — без pageerrors, форм или API requests.

Первый full run на ранее использованной тестовой БД: 766 passed, 9 failed (защитный RuntimeError downgrade после оставшихся Manager outbox rows). Это не новый дефект удаления: full migration suite требует свежей isolated DB; повторный запуск выполняется на новой БД. Миграции ради этого не переписывались. Повторный full run на fresh isolated PostgreSQL DB: **775 passed, 0 failed, 0 skipped, 6 baseline warnings**, 241.66 s. Fresh DB → head, compileall и diff-check PASS; Alembic head одна: `2026_10_05_0029`. Новые regressions проверяют недоступность website API, отсутствие auth cookies, удалённые config поля, безопасный legacy callback и работу Mini App при сохранённом deprecated flag. Existing text booking, Mini App owner/client/auth/feature flag и staff invite tests должны оставаться зелёными.

Production не изменялся. Native Telegram UAT и deployment не выполнялись. При будущей выкатке separately проверить внешние routing overlays: удалить obsolete website API forwarding только в рамках разрешённого deployment; не менять Mini App/branding или billing proxy. Уже выданные Redis website sessions больше не используются и истекут по прежнему TTL; массовая очистка Redis не требуется.
