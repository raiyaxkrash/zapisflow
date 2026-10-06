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
Actual Telegram clients and live profile sync need manual UAT. Assets are bounded normalized binary rows in PostgreSQL, included in existing backups; no separate storage volume. Branding must not bypass subscription/channel gates or hide payment/policy. Existing6 backend warnings identified; baseline initially failed due missing DB/Redis, then WIN1251 default cluster; isolated UTF8 PostgreSQL configured. Test-only Redis Windows vendor runtime is older than production Redis7; final report must disclose rather than claim exact parity.

## Implementation plan
Baseline complete before application changes → shared tokens + branding schema/service/assets → owner APIs/context → cross-channel bot/web integration → Mini App shell/editor/client/master → targeted/full regression/security/visual QA → logical commits and feature push only. No production deployment.


## Implementation decisions and verification
Selected low-risk improvements: booking progress + sticky summary, repeat service/staff with new date, owner client preview, readable payment/success states. ICS and nearest-slot endpoint are deferred, not advertised as implemented. No React migration, auth bypass, domain rewrite or production deployment.

Owner editor is lazy-loaded. Initial JS grew from32.66KB to 42.54KB (+30.3%, 9.88KB absolute); no runtime/UI library was added. This exceeds the preferred25% budget modestly and is an explicit trade-off for cross-channel branding, gallery/reviews, navigation and keyboard support. CSS decreased from17.68KB to 10.55KB. Final numbers are build outputs, not production TTI claims.

BEFORE136 screenshots at four widths. AFTER280 Mini App screenshots at seven widths and both themes. Axe checks40 screens at390px with WCAG2A/AA/2.1AA tags: no violations at check time. These mocked local checks are not real Telegram client certification or production UAT. Browser engines and real uploads/profile sync not checked are listed explicitly in the final report.

Final backend verification:752 passed,0 failed,0 skipped,6 pre-existing warnings. Frontend verification:37 Mini App tests and34 Marketing/Web tests. Owner editor and master settings are separate lazy chunks. app.js is below1000 formatted lines after extracting stateless presentation. Fresh upgrade to0029 and0028→0029 preserve existing studio/contact/horizon data.

Final visual QA includes80 branded Mini App screens (Studio Anna and Barber House) plus28 web journeys. Axe:40 default Mini App screens +80 branded screens +4 web variants, no violations. Screenshots remain local. Browser transport/auth are test mocks; actual Telegram/Web OIDC UAT is not claimed. Optional owner setup prompt is non-blocking and uses only verified bot context, without fabricated schedule completion.

Initial JS+CSS total:53.09KB versus50.34KB baseline (+5.5%); gzip17.97KB versus15.94KB (+12.7%). Owner chunks:editor3.80KB,settings1.40KB. Marketing app75.99KB+vendor141.78KB,CSS34.57KB. npm audit:0 vulnerabilities in both frontends. Browser QA uses Chromium; real Safari/Firefox and Telegram clients remain manual UAT.

## Независимая финальная проверка ветки

Проверка начата с `9c314c75ff7812763c88ed6428423c948d27fd0d` и чистого рабочего дерева. Исправлены: отсутствие tenant-заголовка при загрузке приватного портфолио, повторные предупреждения после отказа от черновика оформления, переполнение длинного текста в сводке и карточках, технический статус оплаты в карточке мастера. Статические формы мастера вынесены в ленивый модуль.

Логотип и обложка — **публичные материалы бизнеса**: anonymous web-booking должен отображать их без авторизации. URL другого публичного бизнеса возвращает его публичный ресурс, а не ресурс текущей сессии. Это не закрытое хранилище. Изменение/удаление требуют владельца и tenant-scoped Mini App session/CSRF; приватное портфолио требует session и `X-MiniApp-Bot`. Не использовать публичные assets для конфиденциальных изображений.

Независимые результаты: backend 760 passed / 0 failed / 0 skipped; Mini App 43 passed; marketing/web 34 passed. Production-сборки проверены в локальном Chromium с тестовыми транспортами: 14 client journeys, owner workspace, ошибки 401/403/404/409/422/500 и 28 web journeys. Реальные Telegram/OIDC и другие browser engines остаются ручным UAT. Проверены PostgreSQL16 fresh upgrade и 0028→0029 с сохранением контактов/горизонта. Локальный Redis для тестов — Windows3, а не production Redis7.

Актуальная Mini App production-сборка: initial JS40.55KB, CSS11.19KB; base JS32.66KB, CSS17.68KB. Общий initial JS+CSS51.74KB против50.34KB (+2.8%). JS +24.2%; owner chunks не загружаются клиентскому режиму. Оба npm audit без уязвимостей. Полный Ruff содержит прежний долг:1788 замечаний против1795 в base; изменённые branding service/router/tests проходят целевую проверку. Эти цифры относятся к этой проверке, а не являются постоянными гарантиями или production TTI.

Критерий финального задания «Tenant A не может читать Tenant B logo» **не выполнен буквально**: проверка с сохранённым ресурсом B возвращает200, как предусмотрено публичным endpoint. Этот контракт требует разрешения перед merge: либо принять публичность logo/cover, либо согласовать новую модель доставки публичных изображений. Приватные операции и портфолио fail-closed. Итог независимого аудита при текущем буквальном критерии: NOT READY FOR MERGE.
