# Аудит Mini App и клиентских каналов ZapisFlow

База: codex/web-booking, e5b05b62b3262463c70ada5626892e5141677712. Production не исследуется и не изменяется в этой задаче.

## Current architecture
Mini App: Vite7 + vanilla ES modules, app.js267 строк, ui.js с безопасным escape, api/client.js с cookie/CSRF и повторяемыми idempotency keys. Marketing/Web: React18 + TypeScript/Vite6. Backend: общие BookingService, SlotEngine, User, Appointment, Payment/Proof. Web OIDC и Mini App initData — разные transport auth над одной identity.

## Current client flow
Главная сразу выводит услуги; затем специалист, календарь/слоты, hold, телефон/policy, подтверждение, ручная предоплата. Заявки и отмена используют tenant-scoped API. Нет визуального прогресса или устойчивой сводки выбора; статусы оплаты отображаются как PENDING/SUBMITTED (`ui.js`). Contacts есть; gallery/reviews отсутствуют в Mini App transport, хотя доменные модели существуют.

## Current master flow
Режим переключается без смены session. Dashboard показывает записи дня, карточки/чеки, CRM, услуги, команду, weekly/date schedules, настройки записи и реквизиты. Staff invitation остаётся Telegram-only. Нет оформления бизнеса. Settings — плоский список; дата-календарь уже имеет предупреждение о существующих записях.

## Current web-booking flow
Анонимный каталог → выбор → OIDC → shared hold/confirm → account/cancel/proof. Flag отдельный от mini_app_enabled. Контекст не содержит брендинга. Marketing home должен оставаться ZapisFlow.

## Current Telegram Bot UX
/start содержит общий beauty copy и неэкранированное first_name (`app/bot/handlers/client/start.py`). Главное меню уже использует обычный MenuCallback(book), системный MenuButton отдельно. Есть параметры has_portfolio/has_reviews, но tenant branding ещё нет. Booking rules менять не требуется.

## Current design system
Marketing: #1D72FE blue, #0F172A/#475569 typography, #FAFAFC canvas, 6/10/16/24 radii, calm slate dark theme. Mini App использует #1768e8/#142b48 и несколько слоёв prototype CSS в style.css. Фотографии и данные QA синтетические, не production.

## Current technical debt
Большого1000+ frontend-файла нет. React migration увеличит runtime при baseline JS32.66KB; сейчас оправдано модульное выделение brand/editor/shell, а не полная миграция. Тестовый harness читает app.js и подменяет transport; его важно сохранить. CSS содержит неиспользуемую prototype workspace/phone chrome, следует заменить application-oriented stylesheet.

## Current accessibility
Semantic buttons/labels и focus-visible уже есть. Calendar aria-label/pressed есть, arrows отсутствуют. Raw payment statuses не читаются клиентом. Top mode selector расходует место; нижняя навигация tiny text-only. Нужны44px touch targets, focus management после route, error rolealert, reduced motion, контраст custom accent.

## Current performance
Mini App baseline: JS32.66KB (gzip11.48), CSS17.68KB(gzip4.46), build266ms. Marketing app70.63KB+vendor141.78KB; CSS31.83KB. npm audit оба0. BEFORE136 screenshots, four resolutions375/390/430/768, client/master, light/dark, browser mock transport: runtime errors0. Измерения production WebView TTI не заявляются.

## Current state management
Локальное состояние module scope, revision защищает stale render. Busy блокирует parallel UI mutation. Transport сохраняет idempotency key до success. Auth token не хранится в localStorage; только theme preference. Нельзя менять эти гарантии при redesign.

## Current API architecture
Mini App /api/miniapp, website /api/web-booking и /api/auth. Services read DB price. Branding needs shared project DTO and owner-only mutation adapters, not duplicated per frontend.

## Current security boundaries
Mini App initData→server session, OIDC state/nonce/PKCE→Redis session; CSRF/Origin/tenant validated server-side. Upload proof decoder Pillow and8MB validation exist. Portfolio stores Telegram file IDs; browser branding assets need browser-safe transport without exposing bot tokens. New inputs require strict schemas, no arbitrary HTML/CSS/JS/URLs, immutable tenant-owned assets.

## Current responsive issues
BEFORE screenshots confirm overly flat hierarchy, large unused vertical canvas, tightly packed bottom labels. No claim of overflow without measurement. Master daily cards expose dates YYYY-MM-DD and raw payment statuses.

## Current Telegram-specific issues
BackButton currently always returns home/dashboard, not previous step. Safe-area events already supported. Theme System imports Telegram arbitrary text/accent colors, conflicting with shared identity and potentially contrast. Keep Telegram scheme, use ZapisFlow/tenant tokens. Menu button behavior is already independent.

## Recommended information architecture
Client: Главная, Записаться, Мои записи, Ещё (контакты/portfolio/reviews). Master: Сегодня, Календарь, Клиенты, Ещё (услуги, команда, расписание, реквизиты, настройки, оформление). Preserve existing transport flows.

## Recommended visual direction
Use marketing blue/slate tokens and typography rhythm; brand hero optional logo/cover, quieter surfaces, clear status hierarchy, thumb-friendly actions. Brand modifies accent/name/content, not structure or custom CSS.

## Recommended customization architecture
Extend existing MasterSettings with bounded validated branding JSON; existing contacts stay existing fields. Defaults must work for absent/corrupt data. Assets normalize through existing Pillow security pipeline, never trust filename or store base64. Owner-only save/reset/upload with existing HTTP mutation ledger. Telegram profile synchronization explicit and retryable; local save never depends on Telegram availability.

## High-impact product improvements
1 Booking progress + compact summary: high impact/low risk/no domain change.
2 Repeat service/staff with NEW slot: medium impact/low risk/existing appointment identifiers require scope validation.
3 Add-to-calendar ICS: medium impact/low risk/escaped text + timezone instants.
4 Owner client preview + optional onboarding checklist: high impact/low risk/capability-preserving UI.
Nearest slot deferred until bounded SlotEngine endpoint justified; no browser scanning.

## Risks
Actual Telegram clients and live profile sync need manual UAT. Assets need persistent shared deployment storage. Branding must not bypass subscription/channel gates or hide payment/policy. Existing6 backend warnings identified; baseline initially failed due missing DB/Redis, then WIN1251 default cluster; isolated UTF8 PostgreSQL configured. Test-only Redis Windows vendor runtime is older than production Redis7; final report must disclose rather than claim exact parity.

## Implementation plan
Baseline complete before application changes → shared tokens + branding schema/service/assets → owner APIs/context → cross-channel bot/web integration → Mini App shell/editor/client/master → targeted/full regression/security/visual QA → logical commits and feature push only. No production deployment.
