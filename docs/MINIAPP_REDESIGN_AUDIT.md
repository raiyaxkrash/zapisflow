# Mini App redesign — аудит и baseline Фазы 1

Дата: 7 октября 2026 года. Документ заменяет прежний аудит до редизайна: его цифры и утверждения о недостающих функциях больше не являются baseline.

## Scope и состояние Git

- База: `codex/web-booking`, `origin/codex/web-booking` — `1712ee06e6fa716d9106da32de53c678782adb73`.
- Feature: `codex/miniapp-redesign-customization`, отдельный существующий worktree.
- Feature до начала: `540a44d7b47bb73981286f3cc1ff6f03cc3513ca`; безопасно обновлена fast-forward до актуальной базы, история сохранена.
- Working tree до работы чистый. Изменения другой маркетинговой рабочей копии не затрагивались.
- Alembic: одна head, `2026_10_05_0029`.
- Фаза 1: только исследование, локальные проверки и этот документ. Код приложения, настройки окружения и миграции не изменяются. Production не исследуется и не обновляется.
- Web Booking больше не нужен как продукт по текущему решению владельца. Здесь он только инвентаризирован; ничего не удалено.

## Current architecture

### Каналы и tenant

Tenant — проект `Master`, принадлежащий `User`; один владелец может иметь несколько проектов. `BotInstance` — Telegram-канал проекта, а не отдельный tenant. `public_id` определяет доверенный канал/проект на входе; числовые IDs из браузера не являются полномочиями.

```text
Telegram → webhook gateway → BotRegistry → aiogram dispatcher
         → middlewares → handlers → services/repositories
Mini App → same-origin /api/miniapp → session/tenant adapters
Website  → /api/auth + /api/web-booking → отдельная OIDC/session auth
                                      ↓
                 BookingService / SlotEngine / PaymentService
                                      ↓
                PostgreSQL: User, Master, Staff, Appointment, Payment
                Redis: FSM, dedup, cache, locks, website sessions
```

Источники: `app/web/app.py`, `app/services/bot_registry.py`, `app/bot/bot_instance.py`, `app/web/miniapp.py`, `app/web/web_booking.py`, `app/services/booking_service.py`, `app/services/slot_engine.py`.

### Реальные подсистемы

| Подсистема | Текущее назначение |
| --- | --- |
| Client Telegram Bot | Текстовая запись/FSM, услуги, контакты, записи, клиентская предоплата; обычный `MenuCallback(book)` независим от Mini App |
| Manager Bot | Onboarding, проекты/боты, управление услугами, командой, графиком, CRM, подпиской; Platform Admin отдельный RBAC |
| Mini App | Клиентские экраны и рабочая область мастера; доступ к master actions задаётся серверными capabilities |
| Marketing | React/TypeScript landing; сейчас также содержит браузерную запись, которая подлежит будущему удалению |
| BookingService / SlotEngine | Создание/отмена обычного Appointment, hold, доступность с weekly schedule, overrides, буферами, часовым поясом и горизонтом |
| Staff | StaffMember/StaffService, primary staff, пользовательские роли, приглашения; tenant-scoped связи |
| CRM | MasterClient, заметки и история; данные карточки ограничены проектом |
| Payments | Ручная клиентская предоплата: Payment/PaymentProof; SaaS YooKassa: SubscriptionPayment/SubscriptionPeriod — отдельный domain |
| Reliability | Redis update claim, DB ProcessedWebhookUpdate, transaction boundary, Telegram Outbox и scheduler retries |
| Branding | MasterSettings.branding + MasterBrandAsset; owner-only изменение; общий клиентский контекст для трёх каналов |

### Mini App auth и security boundaries

`app/services/miniapp_auth.py` проверяет Telegram initData сервером. `app/web/miniapp.py` создаёт opaque DB session (`MiniAppSession`), использует HttpOnly cookie `__Host-zapisflow-miniapp`, CSRF token, Origin и доверенный bot context. HTTP mutation idempotency хранится в `MiniAppOperation`. Website OIDC — другой механизм над тем же User по Telegram identity; не смешивать его с initData.

Master API проверяет права сервером. Скрытая кнопка не заменяет authorization. Public branding images — намеренно публичный бизнес-контент; portfolio/proofs имеют другие правила доступа. Удаление Web Booking не должно удалить Mini App auth, shared identity или asset protection.

### Frontend architecture и state management

- Mini App: Vite 7, vanilla ES modules, без React runtime. `miniapp/src/app.js` — **1005 физических строк**: shell, mutable state, routing, click/form handlers и координация API.
- Уже выделены API, Telegram bootstrap, theme, calendar, media, UI helpers; master views/settings и branding editor загружаются динамически.
- `root.innerHTML` используется для целой оболочки; async navigation защищена revision counter. `busy` предотвращает параллельные UI actions; API сохраняет idempotency key при retry.
- Marketing: React 18, TypeScript, Vite 6; `App.tsx` выбирает landing либо `WebBooking.tsx`. Website import статический: booking код входит в initial маркетинговый bundle.
- Логика слотов, платежных сумм и subscription gating остаётся в backend. Frontend строит календарную сетку и форматирует DTO, но не вычисляет свободное время вместо SlotEngine.
- Не вводить новую систему appointments и не переписывать backend ради UI.

## Baseline tests — свежий запуск

Локальная Windows-среда; отдельная тестовая БД PostgreSQL 16. Миграции применены к **fresh test DB**, не production. Redis для этого запуска — локальный Windows Redis 3.0.504; это ограничение среды, не проверка поведения production Redis 7/multi-replica.

| Проверка | Результат |
| --- | --- |
| `pytest -q` | **783 passed, 0 failed, 0 skipped, 6 warnings**, 261.06 s |
| Mini App `npm ci` | PASS |
| Mini App `npm test` | **43 passed, 0 failed, 0 skipped** |
| Mini App `npm run build` | PASS, 908 ms |
| Marketing `npm ci` | PASS |
| Marketing `npm test` | **34 passed, 0 failed, 0 skipped** |
| Marketing `npm run build` | PASS, TypeScript + Vite, 9.02 s |
| npm audit, обе frontend directories | 0 vulnerabilities |
| Fresh DB → Alembic head | PASS, `2026_10_05_0029` |

Warnings сохранены, не исправлены: 4 RuntimeWarning про unawaited AsyncMock в managed-bot tests (`AuditService`/`ManagedBotRequestRepository`), 2 SAWarning «transaction already deassociated» в rollback/constraint tests. npm ci также сообщал об esbuild install script, не охваченном allowScripts; обе сборки успешно выполнены. Предыдущие ошибки/предупреждения не исправлялись в этой фазе.

Локальные полные логи: `.artifacts/phase1/`. Результаты относятся к указанному SHA и текущей среде; не являются production UAT.

## Installed local skills

Шесть полезных skills находятся в **project-root `.agents/skills/`**, поддерживаемом [официальной документацией Codex](https://learn.chatgpt.com/docs/build-skills). `.codex/skills-manifest.local.md` содержит provenance и список файлов. Сами skills, manifest и artifacts исключены через локальный Git exclude, не repository .gitignore.

| Skill | Источник / версия | Назначение и review |
| --- | --- | --- |
| playwright | openai/skills `49f948faa9258a0c61caceaf225e179651397431` | Browser reconnaissance; Apache-2.0. Wrapper может загружать npm packages, не запускался; использован уже закреплённый локальный Playwright |
| security-best-practices | тот же openai/skills SHA | FastAPI, vanilla JS, React security references; Apache-2.0, инструкции без получения secrets |
| react-best-practices | vercel-labs/agent-skills `063bee94c3f4df8453406c830b0a7df0f2860278` | Bundle/state/performance marketing; MIT, без executable automation; Next-specific советы не применяются вслепую |
| composition-patterns | тот же Vercel SHA | Границы компонентов и state ownership; MIT; React 19 рекомендации не применимы к React 18 |
| frontend-design | Проверенный ранее установленный локальный источник; SHA256 `D91970639E9F5C37682AC7AB60094D35F1C7C1F38D731BD56396563AEE10C1D3` для SKILL.md | Visual hierarchy и copy; Apache-2.0. Upstream commit неизвестен, он не выдумывается; scripts отсутствуют |
| webapp-testing | anthropics/skills `683bc88e56f3e09ba94f7055977f3d3aa499f202` | Добавлен в этой фазе после чтения SKILL, helper и Apache-2.0 license; local browser screenshots/logs |

Первые пять уже присутствовали локально и сохранены; шестой установлен после review. `webapp-testing` helper запускает только явно переданные server commands и завершает свои процессы; использует subprocess shell и localhost TCP probing. Не содержит загрузок, чтения secrets или filesystem deletion; не выполнялся. Скрипты внешних skills не запускались автоматически. Skills не являются доказательством проверки Telegram native clients.

**SKILLS NOT TRACKED BY GIT.** В репозиторий попадает только список в этом audit, не скачанные файлы или локальный manifest.

## Visual baseline и методика

Свежие screenshots: `.artifacts/miniapp-redesign/before/1712ee0/`, отдельно от старых снимков. Реальный production build Mini App запущен через локальный Vite preview. HTTP API и Telegram context подменены **только в browser harness**, приложение/production auth не изменены.

- 375×812, 390×844, 430×932, 768×1024; light/dark.
- 136 снимков основной матрицы: client home/services/staff/calendar/slots/confirmation/bookings; master dashboard/calendar/clients/client card/settings/services/team/schedule/booking settings/branding.
- Ещё 2 снимка: client payment/proof и master date overrides, 390×844 light. Итого **138 свежих снимков**.
- Mock journeys дополнительно прошли hold → agreement/confirmation → payment → proof → my bookings; master dashboard → appointment → client → specific date.
- Main matrix: 0 browser pageerrors, 0 horizontal overflows. Axe на 34 screen/theme комбинациях 390px: 0 violations по wcag2a/2aa/21aa.
- Клиент не загрузил owner chunks во всех 8 client viewport/theme runs.
- Fixtures: Studio Anna, default blue, одна услуга/специалист, синтетические даты и чеки. Состав appointments и дата «сегодня» в mock не проверяют backend фильтрацию.

Это подтверждает отрисовку и навигацию при корректном API. Настоящие Telegram iOS/Android/Desktop, VoiceOver/TalkBack, keyboard overlays, network latency и production identity не проверены. Axe не сертифицирует WCAG целиком.

## Current client flow и Client UX findings

Фактический flow: Home → services → staff → calendar/date → slot → hold/confirmation (phone, policy) → payment/proof, если требуется → my bookings. Область «Ещё» содержит настроенные contacts/portfolio/reviews. Источники: `miniapp/src/app.js`, `ui.js`, `calendar.js`, `tests/test_miniapp.py`.

| ID | Наблюдение | Влияние / рекомендация |
| --- | --- | --- |
| C1 | Home повторяет имя в wordmark и hero, generic «Время для себя» даже для иных направлений; это видно на client-home screenshot | Уменьшить повторение и сделать hierarchy бизнес → ближайшая запись/основное действие; не навязывать beauty copy всем businesses |
| C2 | Theme selector постоянно занимает header, даже для клиента | Перенести preference в «Ещё»; освободить верхний экран для бизнеса |
| C3 | Одна услуга/один специалист всё равно требуют отдельного экрана выбора | Рассмотреть auto-selection только при единственном допустимом варианте от backend; явно показывать выбранное в summary |
| C4 | Sticky summary уже есть, но staff selection в ней не показан: title/price и затем date/time (`app.js:65–69`) | Добавить имя выбранного специалиста; сохранить компактность |
| C5 | Progress уже есть: 4 этапа; дата и слоты объединены в «Время» | Сохранить, уточнить активный этап и возможность вернуться без потери выбора |
| C6 | My bookings уже делятся на будущие/историю, repeat preselects service/staff без копирования старой даты | Развивать существующую функцию; не создавать новый appointment по одному клику repeat |
| C7 | Payments имеют понятные status labels и upload progress; error retry есть | Сохранить эти состояния; нужны delayed/network/expired-session UAT и фокус на конкретной ошибке формы |
| C8 | Portfolio/reviews/contacts уже доступны, sections могут скрываться; fake ratings отсутствуют | Не добавлять заново. Проверять empty/privacy cases и изображения в дальнейшей визуальной матрице |

## Current master flow и Master UX findings

Сегодня → календарь/дата → appointment → clients/card; «Ещё» → services/team/schedule/requisites/contacts/booking settings/branding/manual appointment. Staff invitation остаётся частью Telegram management. Не все Manager Bot функции перенесены в Mini App: subscription/Platform Admin и расширенный CRM не нужно показывать как готовые Mini App screens.

| ID | Подтверждение | Рекомендация |
| --- | --- | --- |
| M1 | В owner header постоянно две широкие кнопки «Клиент/Управление» (`app.js` shell) | Сделать role context компактнее; client preview отдельным осознанным действием, без смены identity |
| M2 | Сегодня выводит последовательность крупных cards и отдельные кнопки «Открыть запись» | Повысить плотность полезной информации, поставить next appointment/payments выше вторичных настроек; метрики только из достоверного API |
| M3 | Settings menu объединяет services, team, schedule, contacts, payments, booking settings, branding, manual entry | Группировка «Бизнес / Запись / Команда / Оформление» внутри «Ещё»; не создавать дополнительный top-level labyrinth |
| M4 | Weekly editor сохраняет один weekday за раз, breaks — строка `10:30-11:00, ...` (`app.js:485–496`) | Более ясная mobile form и structured break rows; backend contract сохранить |
| M5 | Specific dates уже имеют modes weekly/day_off/custom и warning о существующих appointments (`app.js:499–528`) | Это работающая базовая функция, не gap. Улучшать explanation/legend и preview результата |
| M6 | Branding owner-only: live draft preview, reset, logo/cover, colors/text/sections уже реализованы | Progressive disclosure и preview клиентского screen вместо создания второй branding системы |
| M7 | Empty states есть, в schedule без staff инструкция ведёт в Telegram admin | Согласовать cross-channel handoff и объяснить, где выполнить действие |

## Design comparison — marketing как источник

| Аспект | Marketing сейчас | Mini App сейчас | Направление |
| --- | --- | --- | --- |
| Typography | Plus Jakarta Sans headings, Inter body, system fallbacks (`marketing/src/index.css`) | 16px/1.5 system-ui; h2 28px, h3 20px | Единая type scale/weight/rhythm; не обязательно добавлять remote fonts в Telegram |
| Palette | #FAFAFC, #0F172A, #475569, #1D72FE | Те же базовые semantic tokens | Уже согласовано. Убрать параллельные расходящиеся aliases при дальнейшей работе |
| Surfaces | White/slate, dark #090D16/#101626/#161F36 | Те же shared defaults | Сохранить спокойный контраст и business accent layer |
| Radius | 6/10/16/24, pill CTA | 6/10/16/24 tokens, ряд форм/CTA 10–16 | Иерархия radius по component role, не одна карточка для всего |
| Elevation | Несколько shadows/glow marketing sections | Лёгкие shadows + borders | Сайт допускает больше presentation; рабочее приложение требует менее декоративных поверхностей |
| Icons | Inline SVG components | Небольшой inline SVG helper | Единый stroke/size, без heavy library |
| Theme | Light/dark toggle, prefers-color-scheme initial | System/light/dark, Telegram colorScheme | Согласовать explicit/system preference UX; brand mode не перекрывает уже выбранную клиентом тему |
| Motion | 150/250/350ms; section/CTA effects | 150ms interaction, reduced-motion CSS | Движение отвечает действиям, не отвлекает от записи |

`miniapp/src/tokens.css` и `marketing/src/tokens.css` уже зеркалируются, parity проверяется frontend tests. Landing дополнительно использует собственные aliases/shadows: наличие общего token file не означает полной component parity.

## Accessibility findings

Реализованы semantic buttons/labels, focus-visible, minimum 44px controls, main focus после async route, aria-current для navigation/progress, calendar labels, arrow-key movement, reduced-motion. Значения accent строго валидируются; foreground автоматически рассчитывается, а не всегда белый.

**A1 — MEDIUM, calendar composite navigation.** `calendar.js` генерирует множество button day cells; `app.js` arrows переводят focus между доступными buttons, но нет единственного roving tabindex/полной grid navigation модели. Рекомендация: выбрать и протестировать последовательный accessible calendar pattern, не добавить role=grid без соответствующего поведения.

**A2 — MEDIUM, full shell replacement.** `app.js:72` заменяет root и `app.js:122` фокусирует main. Это лучше потери фокуса, но при forms/dialogs повторная перерисовка требует сценариев возврата фокуса, announcement ошибок и сохранения ввода. Это риск архитектуры; измеренного screenreader regression в этой фазе нет.

**D1 — LOW, interaction colors.** `miniapp/src/ui.js:colorTokens` сейчас задаёт hover/pressed равными accent. Контраст foreground рассчитывается, но hover/press differentiation требует дополнительных state tokens и визуального review. Не считать это отсутствием всей branding системы.

**A3 — coverage gap.** Axe проверен на default accent и основной matrix; extreme custom colors, disabled states, dialogs, long text и native Telegram keyboard требуют отдельной проверки будущей фазы. Не заявлять WCAG AA certification на основании нулевых axe violations.

## Performance findings

| Build asset | Raw | Gzip |
| --- | --- | --- |
| Mini App initial JS | 40.55 kB | 14.51 kB |
| Mini App CSS | 11.19 kB | 3.13 kB |
| Lazy master views | 4.10 kB | 1.81 kB |
| Lazy branding editor | 3.80 kB | 1.73 kB |
| Lazy master settings | 1.40 kB | 0.88 kB |
| Marketing app JS | 75.99 kB | 19.66 kB |
| Marketing vendor JS | 141.78 kB | 45.52 kB |
| Marketing CSS | 34.65 kB | 7.33 kB |

Локальный production preview, 5 новых browser pages с mock transport: от начала page.goto до первого main heading **95–107 ms**, median 98 ms; 3 initial API requests, 6 resource entries. Это измерение первого готового экрана в синтетической среде, **не production TTI/Core Web Vitals**.

P1: сохранить lazy master loading. P2: WebBooking статически импортируется в landing App; его дальнейшее удаление уменьшит marketing scope. P3: request waterfalls и DOM replacement нужно профилировать при настоящей задержке. Большие assets уже нормализуются: logos ≤320px, covers ≤1200×600, upload cap 4 MiB; thumbnails в gallery требуют измерения на реальном наполнении.

## Security findings и технический долг

В текущем scope не подтвержден новый CRITICAL/HIGH дефект; это **не полный pentest и не production security attestation**.

- S1: сохранить tenant-context checks, owner-only branding, CSRF/Origin и Mini App session revocation при разделении frontend и удалении website auth. Источники: `app/web/miniapp.py:116–220`, `app/web/branding.py:35–61`.
- S2: brand texts — plain text; helper escape обязателен при HTML string rendering. Large-file refactor увеличивает риск забыть escape, поэтому security regression должен сопровождать extraction.
- S3: `app/services/branding.py` принимает PNG/JPEG/WebP, проверяет реальный формат/размер/пиксели, нормализует WebP; SVG не принимается. Публичные business images не предназначены для confidential assets.
- S4: removed website cookies/Redis sessions должны иметь отдельную cleanup/expiry strategy; не трогать Mini App sessions или Telegram identity.
- T1: 1005-line controller затрудняет изолированные UI tests; существующая архитектура уже модульная, но orchestration смешана с forms.
- T2: mutable shared screen/service/staff/hold state требует явной reset policy при back/role change/retry.
- T3: theme API и landing aliases различаются; синхронизировать contracts, не копировать весь Mini App в React.
- T4: 6 pytest warnings — отдельный QA debt, не скрывать через filterwarnings в redesign.

Нужна ли React/TypeScript migration: **ещё не принято**. Размер controller оправдывает splitting/state isolation, но сам по себе не доказывает необходимость нового runtime. В отдельной фазе сравнить incremental ES modules с React/TS по тестируемости и bundle budget. Next.js/monorepo framework не требуется.

## Web Booking removal map — ничего не удалено

| Категория | Footprint | Планируемое обращение |
| --- | --- | --- |
| Можно удалить после review | `app/web/web_booking.py`, `app/services/web_oidc.py` | Website `/api/auth/*`, `/api/web-booking/*`, OIDC state/nonce/PKCE и Redis web sessions; проверить отсутствие внешних callers перед удалением |
| Можно удалить / разомкнуть frontend | `marketing/src/WebBooking.tsx`, `web-booking.css`, imports/path routing в `App.tsx` | Удалить `/book/<id>` и `/account/bookings` UI; **landing `/` оставить**; entrypoint для старых ссылок согласовать как unavailable, не молчаливый blank page |
| Integration wiring | `app/web/app.py:296–297`; `BotProvisioningService.set_web_booking_enabled`; `cb_bot_web_booking` и channels keyboard | Удалить website registration/toggle/links без удаления Telegram/Mini App controls |
| Shared code — оставить | User/Master/BotInstance/Staff/Appointment/Payment/PaymentProof; BookingService/SlotEngine/SubscriptionAccessPolicy; Mini App context/mutations, branding/media, schedule/calendar contracts | Не удалять только потому, что website transport их импортирует. branding helpers в `miniapp/src/ui.js`, `WebBooking.tsx` и tokens — сначала проверить remaining callers, не считать весь branding website-only |
| Миграции — оставить | `2026_10_05_0028_web_booking.py`, последующие `0029` и вся Alembic chain | Нельзя переписать применённую историю или downgrade production ради удаления UI |
| Schema можно оставить deprecated | `bot_instances.web_booking_enabled` | Сначала перестать использовать; отдельный schema cleanup позже при необходимости. Нет WebAppointment/WebUser tables и отдельной DB WebSession в текущем коде |
| Redis/session cleanup | `web:login:*`, `web:session:*`, cookie `zf_web_session` | После отключения routes безопасная invalidation/TTL без массовой очистки Redis и без Mini App cookie изменений |
| Config/env | Settings validators, `.env.example`: WEB_BOOKING_BASE_URL, TELEGRAM_LOGIN_CLIENT_ID/SECRET/REDIRECT_URI, WEB_SESSION_TTL_SECONDS | Удалить website-only config из кода/example/docs; production env не менять в этой фазе; менеджерский BOT_TOKEN не удалять |
| Tests | `tests/test_web_booking.py`, `tests/test_web_oidc.py`, `marketing/tests/web-booking.test.tsx`; cross-channel assertions в branding/frontend tests | Удалить только website expectations, сохранить shared domain/security regressions и добавить tests отсутствующих deprecated routes |
| Docs/copy | `docs/WEB_BOOKING_TELEGRAM_LOGIN.md`, website parts README/BRANDING_AND_MINIAPP.md, marketing CTA/FAQ | Не рекламировать удалённый канал; маркетинговый сайт сохраняется |
| Deployment | marketing Docker/static routing, Vite proxy, gateway/API routing policies | Отдельно проверить конкретный deployment overlay в будущей фазе. Tracked Mini App gateway `/api/miniapp/*` и `/api/branding/*` оставить; production Caddy здесь не исследовался |

## Manager Bot bug status

Известный mismatch `claim_invite_token_atomic(token_plain=..., user_telegram_id=...)` **уже исправлен в текущей базе**.

`app/manager_bot/handlers.py:250–252` передаёт `raw_token=token_plain`, `user_id=user.id` — внутренний User ID, не Telegram ID. `tests/test_manager_invite_contract.py` находится в fresh passing suite. В Фазе 1 исправлений этого участка не было; повторять старый root cause как актуальный нельзя.

## Recommended information architecture

### Client IA

Сохранить существующие четыре destinations: **Главная / Записаться / Мои записи / Ещё**. «Ещё»: контакты, portfolio/reviews при наличии, тема и информация о ZapisFlow. На главной — бренд, ближайшая запись, primary booking CTA и services без повторяющихся заголовков. Back сохраняет intent; success ведёт к обычному Appointment.

### Master IA

Сохранить **Сегодня / Календарь / Клиенты / Ещё**. Сегодня: ближайшая запись и actionable payments; календарь — центр работы с датой. «Ещё» сгруппировать по задаче; оформление и preview owner-only. Не добавлять все Manager Bot функции в Mini App автоматически и не показывать недостоверную revenue.

## Recommended design system

- База marketing: calm slate/blue; бизнес-брендинг сверху, обязательная ненавязчивая «Работает на ZapisFlow».
- Tokens: сохранить `--zf-bg/surface/surface-elevated/text/text-muted/border/accent/accent-hover/accent-soft/accent-foreground/focus/danger/success/warning`; убрать расхождения aliases постепенно.
- Spacing: 4/8/12/16/24/32; radius: 6/10/16/24 по component role. Content width адаптивный, mobile-first.
- Typography: общий scale 12/14/16/20/28, достаточный line height; system fonts в Telegram допустимы ради загрузки.
- Components: AppShell, TabNavigation, Button, Field/Error, BusinessHeader, ServiceRow, StaffCard, Calendar/DayCell, SlotPicker, BookingSummary, AppointmentCard, Empty/Skeleton/Alert, OwnerSettingsSection.
- Theme: System/light/dark; Telegram System следует colorScheme, user choice выше brand default. Accent foreground derived, focus/status не должны зависеть только от цвета.
- Calendar: server-authoritative availability, явные today/selected/past/horizon/loading, текстовые labels, клавиатура; slot targets ≥44px.
- Motion: быстрые functional transitions, prefers-reduced-motion; не украшать каждый экран анимацией.

## Recommended Telegram integrations

Проверено по [официальной Telegram Mini Apps документации](https://core.telegram.org/bots/webapps). Реализовывать позже, с API version guards.

- BackButton уже используется: согласовать history/back с draft и confirmation.
- MainButton/BottomButton: один native primary action при выборе/confirm; MainButton остаётся свойством API, BottomButton — современный тип. Исключить дублирование native и sticky CTA.
- SecondaryButton — только если нужен явный вторичный action и поддерживается клиентом.
- HapticFeedback уже вызывается при successful confirmation (`app.js:800`); сохранить и проверить поддержку, расширять только при полезной error feedback, не на каждое нажатие.
- safeAreaInset/contentSafeAreaInset и viewport events уже подключены bootstrap; нужен native device QA, stable-height/keyboard сценарии.
- themeParams и colorScheme: синхронизировать Telegram chrome с выбранной темой, не произвольно заменять tenant colors.

Официальный API не доказывает поддержку каждого метода установленным клиентом Telegram. Не добавлять auth bypass или зависимость текстовой записи от Mini App.

## Recommended additional features — оценка, без реализации

| Кандидат | Статус сейчас | Impact | Effort | Risk | Рекомендация |
| --- | --- | --- | --- | --- | --- |
| Ближайшее свободное время | Отдельный nearest-slot UX не найден | Высокий | Средний | Средний: horizon/performance/race | Thin backend operation только через SlotEngine; сначала profile и contract |
| Повторить запись | Уже есть | Высокий | Низкий | Низкий | Улучшить видимость и безопасный fallback для удалённой услуги; новый slot обязательно |
| Добавить в календарь | Client ICS action в Mini App не найден | Средний | Низкий/средний | Низкий: timezone/escaping | Локальная ICS generation без внешнего аккаунта, после подтверждения |
| Sticky summary | Уже есть | Высокий | Низкий | Низкий | Дополнить staff и edit/back affordance |
| Booking progress | Уже есть | Высокий | Низкий | Низкий | Чётче показывать выбранное/текущий этап, без лишних экранов |
| Owner onboarding checklist | Уже есть | Средний | Низкий | Низкий | Связать checklist со следующим действием; необязательное оформление не блокирует запуск |
| Client preview | Уже есть role switch и branding draft preview | Средний | Средний | Средний: role/draft semantics | Более явный preview, не менять security session |

**Top 5 приоритетов:** компактная главная/навигация; staff-aware summary/progress; accessible calendar; понятные master schedule/settings forms; ICS после успешной записи. Nearest slot — следующий кандидат после оценки нагрузки, не обещание реализации в Фазе 1.

## Risks и границы доказательств

1. Web Booking removal может затронуть common helpers; нужна import/caller map и regression gates, история migrations неизменна.
2. Полная frontend migration без необходимости увеличит initial bundle; бюджет роста 20–25% требует измерения и justification.
3. Vanilla innerHTML требует неизменного escaping и аккуратного focus/draft preservation.
4. Mock browser journey не заменяет native Telegram UAT и настоящую DB booking concurrency проверку.
5. Redis 3 baseline не закрывает Redis 7/multi-replica differences; повторить integration gates в production-compatible isolated environment позже.
6. Старые screenshots и старый audit не используются как свежие измерения.
7. Owner/client preview нельзя реализовывать через bypass RBAC; изменения brand сохраняются только explicit Save.
8. Нужны реальные наполненные бизнесы, длинные тексты, logo/cover, крайние accents и слабая сеть для следующего QA.

## Plan for Phase 2–9 — только план

| Фаза | Работа | Gate перед следующей фазой |
| --- | --- | --- |
| 2 | Удаление Web Booking transport/UI/config по карте; shared code/history сохраняются; отдельно QA warnings | Backend/Mini App/marketing tests, old-route behavior, security review; никакого auto-deploy |
| 3 | Зафиксировать IA/tokens/component boundaries; выбрать incremental modules либо обоснованный React/TS; state ownership | Design review, bundle budget, auth/RBAC compatibility |
| 4 | Client home/navigation/services/staff/booking/calendar/summary/success/my bookings | Keyboard, loading/error/empty, complete mocked journey, shared BookingService regressions |
| 5 | Master today/calendar/appointment/client/services/team/schedule/settings | Staff/owner permissions, date overrides/horizon, payment decisions, focus/forms |
| 6 | Довести существующее branding: progressive editor, assets, preview/reset, cross-channel welcome/menu | Upload/XSS/IDOR/CSRF/defaults, extreme colors, tenant separation |
| 7 | Telegram native integration и избранные небольшие improvements (например ICS) | Version guards, safe areas/back/haptics, no duplicated primary CTA |
| 8 | Full QA/performance/a11y/security; before/after одинаковая matrix; production-compatible isolated DB/Redis | 0 failed/0 skipped где инфраструктура доступна, builds PASS, migration history consistent, documented UAT gaps |
| 9 | Документация и review-ready feature branch, финальный отчёт/план deployment | Push feature, без merge/deploy; настоящий Telegram UAT отдельно разрешён и выполнен до production verdict |

Фаза 1 завершает baseline и этот план. Никакая следующая фаза не начата.
