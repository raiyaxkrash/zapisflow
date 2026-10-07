# Финальный QA редизайна Mini App

Дата: 7 октября 2026. Ветка: `codex/miniapp-redesign-customization`.
База финального прохода: `3fcd3b7f08d9268bcaeca2edd91ce6e1075260c9`.
Проверенный runtime commit: `5b13357` (следующий documentation commit не меняет runtime).
Scope: regression, visual/accessibility/security smoke, migrations, cleanup и
документация. Production, .env, DNS/Caddy и реальные Telegram credentials не изменялись.

## Изменения финального прохода

1. Найдено и исправлено полное перекрытие keyboard focus слота нижней навигацией.
   Page padding не решал проблему автоматического Tab scroll. Добавлен
   `scroll-padding-bottom` с Telegram/system safe area; воспроизводимый browser
   regression хранится в `miniapp/qa/keyboard-focus.js`.
2. При auth/start контекст проекта запрашивался дважды подряд. Первый home теперь
   использует только что полученный контекст; обычные переходы домой и retry
   перечитывают его. Новый DOM-flow regression подтверждает оба условия.
3. Удалён только подтверждённый дубликат CSS safe-area правила. Production UI
   не содержит console.log/debugger/TODO редизайна. Новые подсистемы не добавлены.
4. README и architecture/deployment docs синхронизированы: текстовая запись и
   системный Mini App entrypoint независимы, master/client screens и owner editor
   описаны по текущим handlers. Старые phase documents остаются историческими snapshots.

## Regression и безопасность

| Область | Покрытие |
| --- | --- |
| Client | Home/services/staff/calendar/slots/confirmation/success/My bookings/details/cancel/repeat/nearest/ICS/prepayment/proof/portfolio/reviews/contacts |
| Master | Today/calendar/appointment/CRM/client/services/team/weekly/date overrides/payments/broadcasts/analytics/settings/branding |
| Telegram Bot | /start, menu callbacks, text booking FSM, старые callbacks скрытых разделов, brand text escaping, profile sync failures |
| Mini App ON/OFF | WebApp/Commands menu, 403 MINI_APP_DISABLED, старые sessions, text booking независим от flag |
| Staff invite | Реальный handler/repository contract raw_token + internal user_id; valid/expired/used/wrong, scoped create и concurrent claim |
| Booking race | Два User/две PostgreSQL sessions на одном слоте: один Appointment, второй отказ |
| Tenant/RBAC | Чужие clients/appointments/services/staff/private portfolio/proofs и branding mutations запрещены; OWNER-only оформление; STAFF billing/settings denial |
| Auth | initData HMAC/age/replay, wrong bot, expiry/token version, tenant cookie/header, CSRF/Origin, fail-closed rate limit |
| Upload/XSS | Plain text/strict hex, unsafe contact URL, malformed filename, SVG/wrong MIME/size/pixel cap; image decode/normalize; HTML escaping |
| Secrets/logs | Regression initData/bot-token log protection; diff не включает .env/credentials/screenshots/skills |

Намеренно публичные logo/cover бизнеса не являются private tenant assets.
Чтение через UUID `/api/branding/.../assets/logo|cover` разрешено контрактом;
попытки изменения чужого бренда и чтения private media блокируются. Такой доступ
нельзя смешивать с asset IDOR чека/CRM/portfolio. Это уже зафиксированный контракт,
не новое ослабление проверки.

Тесты бизнес-правил используют настоящий PostgreSQL/Redis. Browser journeys
используют production bundle и mocked transport/Telegram; это не полный
real-Telegram end-to-end и не production authentication bypass.

## Визуальная и accessibility проверка

Артефакты не коммитятся:

- Before: `.artifacts/miniapp-redesign/before/`.
- Final: `.artifacts/miniapp-redesign/final/`: client/master/branding/journeys.
- Размеры: 320×568, 375×812, 390×844, 430×932, 768×1024, 1024×768.
- Light/Dark; brand System/Light/Dark; ZapisFlow/Studio Anna/Barber House.
- Yellow/red/green/blue/purple/almost white/almost black accents.
- Client 132, master 180, branding 129 screenshots; journeys ещё 3.
- Browser runtime errors, horizontal overflow и axe violations: 0 на проверенных
  сценариях. Axe использует WCAG 2/2.1/2.2 tags, но не сертифицирует WCAG целиком.
- Keyboard regression: 8 сочетаний размера/темы с safe bottom 34 px; слот реально
  достигается Tab и не скрыт целиком под fixed navigation. Есть named forms,
  labels и focus-visible; календарь поддерживает стрелки/Home/End.

Before/after сравнение учитывает разные тестовые данные, не является pixel-diff:

| Экран | Изменение |
| --- | --- |
| Client Home | Явный primary CTA, записи/статусы и повтор вместо почти пустого списка |
| Booking/services/staff | Крупные карточки, цена/длительность/описание, progress и summary |
| Calendar/slots | Сохранены backend states/horizon, добавлены читаемые selected/today, keyboard и touch controls |
| My bookings | Предстоящие/прошедшие, детали, payment actions и повтор |
| Master Today | Быстрые действия, понятные статусы, следующая/дневные записи |
| Master Calendar | Выбор дня/month и показатели существующих записей |
| CRM | Поиск, карточка клиента, история/notes, крупные строки |
| Services | Статус, сотрудники, явные edit/on/off actions |
| Schedule | Отдельные weekly/date screens, предупреждения об existing appointments |
| Branding editor | Черновик, client preview, progressive disclosure, save/reset и отдельный profile sync |

Не выявлено подтверждённого ухудшения ключевого сценария после устранения
перекрытого keyboard focus. На небольшом экране календарь/подробные формы
требуют вертикальной прокрутки: это не скрытая функциональность. Настоящие
VoiceOver/TalkBack, Telegram keyboard/safe-area и native dialogs требуют UAT.
Цель — WCAG 2.2 AA; полная сертификация не заявляется.

## Performance

| Asset | Phase 1 | Phase 3 | Final |
| --- | ---: | ---: | ---: |
| Initial Mini App JS | 40.55 kB / gzip 14.51 | 45.52 / 16.26 | 51.59 / 17.87 |
| Initial Mini App CSS | 11.19 / 3.13 | 15.53 / 3.94 | 21.93 / 4.84 |
| Marketing app JS | 75.99 / 19.66 | 58.79 / 14.14 | 58.79 / 14.14 |
| Marketing vendor JS | 141.78 / 45.52 | 141.78 / 45.52 | 141.78 / 45.52 |
| Marketing CSS | 34.65 / 7.33 | 32.03 / 6.64 | 32.03 / 6.64 |

Initial JS вырос на 27.2% raw / 23.2% gzip относительно Phase 1. Рост обоснован
client UX, navigation, themes, accessibility и reliability; framework runtime
не добавлен. Initial CSS вырос, но остаётся 4.84 kB gzip; master CSS 4.53/1.07 kB
исключён из client initial load. Lazy chunks: master renderer 19.47/6.65,
branding editor 6.10/2.65, settings 1.40/0.88, views 3.88/1.77, ICS 1.28/0.80 kB.

На старте auth/context/services/My bookings — четыре API requests; лишний
повтор context удалён. Phase 1 имела три requests без нового home history block.
Ближайшее время приходит по явному действию из backend, календарь не сканируется
frontend. Logo/cover нормализованы; private portfolio использует lazy loading и
revocable Blob URLs. Browser network assertions подтверждают отсутствие master
CSS/renderer у клиента. Пять локальных samples: первый main heading 104–171 ms, median 143 ms; Phase 1 — 95–107 ms, median 98 ms. Более наполненный home и текущая нагрузка локального QA отличаются; это наблюдение, не одинаковый production benchmark. Local render timings не эквивалентны native TTI/CWV.

## Tests и схема

- Backend: **811 passed, 0 failed, 0 skipped**, 6 прежних warnings, 349.04 s.
- Mini App: **79 passed, 0 failed, 0 skipped**.
- Marketing: **23 passed**; TypeScript проверен его build (`tsc && vite build`).
- Обе production builds, compileall, Ruff F821 и diff-check: PASS.
- Browser client/master/branding smoke + booking/cancel/repeat/proof journey:
  PASS; keyboard regression: 8 PASS. Browser proof — fixture, реального перевода нет.
- npm audit Mini App/marketing: 0 vulnerabilities; новых dependencies нет.
- Одна Alembic head: `2026_10_05_0029`.
- Fresh disposable DB → head: PASS.
- Отдельная disposable DB, `0028` → seed existing tenant/settings/bot/appointment
  → `0029`: PASS; address/horizon/запись/ACTIVE bot сохранены, branding defaults работают.
- Production migration/downgrade/DB operations не выполнялись.

Миграционные tests нельзя запускать на production или нужной пользовательской
БД. Используются только отдельные TEST_DATABASE_URL/TEST_REDIS_URL.

## Повтор browser regression

Требуется установленный Playwright и Chromium, плюс локальная собранная Mini App
через Vite preview. Можно использовать отдельный local QA tooling directory,
который исключён из Git; новый production dependency не требуется.

```sh
# Из корня проекта; PLAYWRIGHT_MODULE — путь к установленному пакету,
# если он не доступен обычному require('playwright').
MINIAPP_QA_URL=http://127.0.0.1:5185 \
PLAYWRIGHT_MODULE=/path/to/node_modules/playwright \
node miniapp/qa/keyboard-focus.js
```

Harness имеет только synthetic identity/network fixtures. Не подключать его к
production и не передавать реальные tokens/initData/cookies.

## Repository и выкатка

Web Booking не зарегистрирован в backend и не имеет рабочего frontend flow.
Старые /book и /account/bookings показывают unavailable; deprecated DB column,
миграционная история, retirement tests и архивный аудит сохранены намеренно.
Website Login/OIDC config отсутствует. Local skills/manifest/artifacts исключены
через local excludes и не tracked. Другие рабочие деревья не изменялись.

Deployment — только [отдельный checklist](MINIAPP_DEPLOYMENT_CHECKLIST.md):
backup, approved SHA, все overlays, backend/build/migration, Mini App/marketing
build, targeted restart, health, real Telegram UAT и logs. В этой фазе — только
commit/push feature branch, без merge/deploy.

## Remaining manual UAT

- Telegram iOS/Android/Desktop: настоящая auth/session/cookie, viewport/keyboard,
  BackButton, themes и слабая сеть.
- VoiceOver/TalkBack, native dialogs и реальные ассистивные технологии.
- Владелец: настоящие logo/cover uploads, preview/save/reset и Telegram profile sync.
- Выделенный test tenant: text booking и Mini App booking/cancel/repeat/prepayment,
  owner decision и staff invite через реальные Telegram accounts, без денежного перевода.
- Fresh Docker image/gateway/static hosting после отдельно разрешённой выкатки.

Локальный QA даёт readiness **для review**, не подтверждение production работы.
