# Master Mini App — Фаза 5

## Scope

Мобильное рабочее пространство использует дизайн-систему и Telegram foundation Фазы 3. Client Mini App Фазы 4 сохранён. Новое оформление бизнеса не реализуется: существующий editor не расширен. Production, .env, DNS и Caddy не затрагивались.

## Навигация

Сегодня / Календарь / Клиенты / Ещё. STAFF видит только разрешённые рабочие destinations; CRM, настройки, платежные решения, аналитика и портфолио требуют OWNER/ADMIN на backend. Owner-only ограничения прежнего editor сохранены.

Ещё: услуги, команда, расписание, оплаты, портфолио, реальные отзывы, история рассылок, аналитика, контакты, реквизиты и настройки записи. Существующая ссылка Оформление сохранена без разработки Фазы 6.

## Ежедневная работа

- Сегодня: дата, число активных записей, число чеков **за выбранный день**, ближайшая будущая запись при наличии server_now, записи дня, добавить запись и открыть общую очередь оплат. Checklist не блокирует работу и расположен ниже записей.
- Календарь: предыдущий/следующий день, Сегодня, month picker, server-count indicators. Общий календарь показывает записи, не выдаёт вымышленные состояния рабочего графика.
- Свободное время: мастер выбирает услугу, сотрудника и дату; готовые слоты получает SlotEngine. Нажатие переносит выбор в существующую ручную форму. После сохранения intent очищается; бронирование проверяется сервером повторно.
- Детали: клиент, телефон, услуга, сотрудник, дата/время, цена, статус, заметки, фактические платежи и чеки. Открыть клиента, завершить подтверждённый визит, отменить запись — через существующие операции BookingService. При отправленном чеке сначала требуется платёжное решение.
- Оплаты: последние 100 записей с предоплатой; записи с отправленным чеком идут первыми. Фильтры проверки/все, существующие approve/reject операции PaymentService. Это клиентская предоплата, не SaaS billing.

Чек раньше открывался прямой ссылкой без обязательного X-MiniApp-Bot header. Теперь скачивается через authenticated API client и временный Blob URL с освобождением ресурса. Проверка прав /proofs остаётся прежней.

## CRM, услуги и команда

Список клиентов поддерживает существующий поиск по имени. В одном дополнительном aggregate query backend возвращает число записей, последний завершённый визит и ближайшую будущую активную запись. Карточка показывает будущие записи и историю, server CRM amount, телефон, заметку с textarea и feedback Сохранено. Сумма названа стоимостью визитов, не банковским остатком/доказанной оплатой.

Из карточки клиента можно открыть ручную запись с предвыбранным клиентом. Клиенты без телефона остаются доступны; пустые/опасные phone strings не превращаются в ссылки.

Услуги: цена, длительность, статус, назначения сотрудников; редактирование, включить/выключить. Форма сохраняет существующие параметры предоплаты и интервала. Назначения редактируются в Команде через существующий StaffService API.

Команда: avatar fallback, имя, специализация, статус, услуги, редактирование и расписание конкретного сотрудника. Создание/одноразовое приглашение остаётся в **Manager Bot → проект → Команда**; atomic claim logic не изменена и покрыта существующим backend suite. Роли, которых нет в Staff DTO, не выдумываются.

## Расписание

Семь строк недели; выбор дня восстанавливает часы и перерывы. Нет шаблона — Не настроено, а не вымышленный рабочий день. Выходной скрывает и отключает ненужные поля часов. Обновление использует текущий Schedule API.

Отдельные даты сохраняют По обычному расписанию / Выходной / Особое расписание, часы, перерывы, scope предупреждение и server appointment_count. Изменение графика не удаляет существующие Appointment. Успешное сохранение показывается явно.

## Портфолио, отзывы, рассылки и аналитика

Портфолио имеет собственную authenticated management gallery, загрузку JPEG/PNG до 8 МБ и удаление после confirmation. Используются текущие PortfolioCategory/PortfolioItem/PortfolioRepository и Telegram storage. Изображение декодируется и нормализуется существующим safe_image, исходное имя и metadata не используются. SVG отклоняется. Нормальные retries защищены MiniAppOperation; сбой после Telegram send до DB commit может оставить лишнее media message, но не дублирует запись/платёж. Существующий repository add_item исправлен: обязательный master_id сохраняется вместе с category_id.

Отзывы: просмотр фактических опубликованных отзывов через существующий read-only канал; модерация, которой нет в API, не добавлена.

Рассылки: последние 50 кампаний, настоящие статусы и counts. Создание/отправка остаются в существующей Telegram-админке (/admin); ссылка и текст прямо это объясняют. Новая подсистема marketing automation не создаётся.

Аналитика за текущий месяц: AnalyticsService.get_metrics_for_range с границами в timezone проекта. Записи, завершённые визиты, уникальные клиенты и популярные услуги. Стоимость завершённых услуг не называется балансом банка. Frontend не пересчитывает эти показатели.

## API и безопасность

Новые тонкие адаптеры:

- GET /api/miniapp/master/calendar — server counts, STAFF scope.
- GET /api/miniapp/master/free-windows — существующий SlotEngine, tenant/staff/service validation.
- GET /api/miniapp/master/payments — tenant queue, OWNER/ADMIN.
- POST /api/miniapp/master/appointments/{id}/action — cancel/complete через BookingService, общий mutation ledger/CSRF.
- GET /api/miniapp/master/analytics и /master/broadcasts — existing domain/read-only tenant data.
- GET/POST /api/miniapp/master/portfolio, DELETE /master/portfolio/{id}, GET /master/portfolio/{id}/image — authenticated management, bounded image transfer, current Telegram storage.

CRM list расширен optional safe data; detail DTO содержит optional master_client_id. Context добавляет server_now и публичную ссылку на чат управления для OWNER/ADMIN. Schema/migrations не менялись, Alembic head остаётся 2026_10_05_0029.

Новые mutations используют существующие session, Origin, CSRF, RBAC и idempotency; IDs не принимаются как tenant authority. Проверки обеих сторон portfolio tenant/category, MIME, размеров, декодирования и безопасные Blob URLs. Клиент/STAFF не получает management read endpoints. Никаких token/secret в bundle или документации.

## Архитектура и QA

Master orchestration выделен в lazy src/master/render.js; presentational DTO views — src/master/workspace.js, scoped styles — workspace.css. App controller сохраняет текущие form/action contracts и публикует state после render. Client views не изменены.

Кнопки/inputs semantic, labels/focus/reduced-motion из foundation, календарные labels и native confirmation. Touch targets минимум 44px по высоте. Все мастерские экраны проверяются Light/Dark в 320×568, 375×812, 390×844, 430×932, 768×1024; локальные screenshots не коммитятся.

Реальные Telegram iOS/Android/Desktop, скачивание receipt из WebView и реальный Telegram photo upload остаются manual UAT. Tests и browser harness не заменяют такую проверку. Production deploy не выполнялся.

### Результаты локальной проверки

- Final backend: **788 passed, 0 failed, 0 skipped, 6 existing warnings**; fresh PostgreSQL test DB после upgrade head. Включены Manager Bot, atomic invite, tenant isolation, auth, Mini App ON/OFF и Telegram text booking regression tests.
- Mini App: **70 passed**, marketing: **23 passed**; обе production builds PASS; compileall/diff-check PASS.
- Initial Mini App JS: **53.55 → 49.68 kB** (gzip **18.26 → 17.19 kB**). Master render/workspace lazy chunk **19.47 kB / 6.65 gzip**, не запрашивается клиентским home. CSS **20.98 → 25.51 kB** (gzip **4.69 → 5.23**).
- Chromium master QA: **150 screenshots**, 15 screens × 5 viewports × Light/Dark. **0 runtime errors**, **0 horizontal overflows**, **0 violations** выбранных axe WCAG A/AA rules на 375px. Это автоматическая проверка, не сертификат соответствия WCAG.
- Client regression: **110 screenshots**, все journeys прошли. При сравнении с Фазой 4 совпали 104 снимка; шесть отличаются только состоянием primary CTA в confirmation, его размеры/раскладка не менялись. Client source views/CSS не изменены.
- Локальный Redis runtime отличается от production Redis 7; production и Telegram native clients не проверялись в этой фазе.

Артефакты: `.artifacts/miniapp-redesign/phase-5/`, клиентская матрица в `client-regression/`; исключены из Git.
