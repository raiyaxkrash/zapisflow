# Client Mini App — Фаза 4

## Границы

Клиентский интерфейс использует shell, tokens и Telegram bridge Фазы 3. Master workspace и editor оформления не переделаны. Маркетинговый сайт не изменён. Website booking удалён в Фазе 2 и не возвращается этой работой.

## Навигация и экраны

- **Главная:** название/логотип проекта, приветствие, основной CTA, ближайшая запись, до трёх недавних услуг из завершённых визитов, услуги и ссылки на разделы.
- **Записаться:** услуга → сотрудник → серверный календарь/слоты → проверка выбора → подтверждение. Progress и sticky summary сохраняют контекст.
- **Мои записи:** предстоящие и прошедшие/отменённые; отдельные детали, возобновление резерва, предоплата/чек, отмена и повторный выбор.
- **Ещё:** доступные портфолио, отзывы, контакты, описание бизнеса и System/Light/Dark. Настройки видимости из существующего context сохраняются.

Портфолио загружается через существующий authenticated media transport с ленивой загрузкой и отзывом Blob URLs. Отзывы показывают реальные оценки. Контакты скрывают пустые значения; ссылки проверяют формат и допустимый протокол/host. Работает на ZapisFlow остаётся в footer.

## Подтверждение и backend source of truth

Выбор времени вызывает readonly `GET /api/miniapp/client/quote?service_id=…&staff_id=…` и не создаёт Appointment. Quote проверяет tenant, активность услуги/сотрудника и доступность записи по подписке; цена, длительность и предоплата приходят с сервера. Расчёт предоплаты вынесен без изменения формулы в `BookingService.calculate_deposit`, общий для quote и существующей записи.

После телефона и согласия UI вызывает существующие `POST /client/holds` и `POST /client/appointments`. BookingService заново проверяет слот и создаёт обычный Appointment; idempotency, transaction ownership и платежные домены сохраняются. При потерянном ответе используется существующий retry contract. SLOT_TAKEN возвращает к календарю с объяснением; frontend не вычисляет свободные даты и не меняет Appointment.status.

Предоплата продолжает существующий Payment/PaymentProof flow. Нулевая предоплата показывает success. Отмена после системного confirmation использует существующий API и доменную cancellation logic.

## Небольшие улучшения

1. **Повторить запись:** только доступная услуга/сотрудник; старая дата, слот и hold очищаются. Новое подтверждение обязательно.
2. **Ближайшее свободное время:** `GET /api/miniapp/client/availability/nearest?service_id=…&staff_id=…`; SlotEngine проверяет дни последовательно на сервере. Поиск ограничен меньшим из горизонта и 30 дней вперёд (до 31 даты), rate limit 15 запросов/мин. Ответ сообщает searched_until; отсутствие результата не означает отсутствие слотов за этой границей. Слот не удерживается до подтверждения.
3. **Добавить в календарь:** лениво загружаемый ICS exporter для CONFIRMED с серверной snapshot duration. UTC timestamps, UTF-8 folding и escaping исключают внедрение дополнительных событий. Авторизация сторонних календарей не требуется.

## Архитектура

`src/client/views.js` — чистые представления клиентских DTO; `src/client/client.css` — клиентские surfaces поверх tokens; `src/client/calendar-event.js` — отдельный lazy chunk. Controller сохраняет API/FSM транспорта и общую identity. Master-only modules остаются lazy. `duration_min` добавлен в Appointment DTO как optional backward-compatible поле из snapshot.

Дата/время отображаются в локальном времени студии из ISO DTO, без конвертации в timezone устройства. Availability, права, статусы, стоимость и правила предоплаты остаются на backend.

## Доступность и темы

Native buttons/inputs, видимые labels, semantic facts, aria-current progress, aria-pressed slots, calendar descriptions, focus-visible и reduced-motion foundation. Календарь доступен с клавиатуры через существующий helper. Ошибки и результаты используют понятный текст/status; primary targets и slots не менее 44px по высоте. На 320px календарь уменьшает внешние отступы.

System учитывает Telegram либо browser media query; ручная тема доступна в Ещё. Client CSS scoped через data-mode, Master не получает клиентскую раскладку.

## Проверки и ограничения

Регрессионные API tests используют настоящий PostgreSQL и проверяют quote без Appointment, единую availability и cross-tenant rejection. Frontend tests запускают реальный controller с mock API: booking, retries, конфликт слота, предоплата/чек, детали, отмена, повтор и Master regression.

Browser QA использует production build с локальными mock Telegram/API, не auth bypass приложения. Снимки всех клиентских экранов: 320×568, 375×812, 390×844, 430×932, 768×1024, Light/Dark. Артефакты локальны и не коммитятся. Настоящие Telegram iOS/Android/Desktop и открытие ICS из WebView требуют ручного UAT; production не обновлялся.

### Итог локальной проверки

- Backend: 779 passed, 0 failed, 0 skipped, 6 existing warnings. Первый запуск на пустой БД остановлен из-за отсутствующих таблиц; финальный прогон выполнен на отдельной fresh БД после upgrade head.
- Mini App: 62 passed; marketing: 23 passed; обе production builds успешны.
- Mini App initial JS: 45.52 → 53.55 kB (+17.6%); gzip 16.26 → 18.26 kB. CSS: 15.53 → 20.98 kB, gzip 3.94 → 4.69 kB. ICS lazy chunk 1.28 kB / 0.80 gzip.
- Chromium: 110 screenshots, 10 viewport/theme combinations, 0 runtime errors, 0 horizontal overflow; axe на 375px во всех 11 экранах обеих тем: 0 violations выбранных WCAG A/AA rules. Это не сертификат соответствия WCAG и не реальный Telegram UAT.
- В сравнении с baseline основной CTA теперь виден на главной; цена/длительность/условия доступны до создания резерва; детали и success используют отдельные понятные состояния. Fixed navigation остаётся доступной одной рукой; отступ снизу позволяет прокрутить контент выше неё.
- PostgreSQL 16 используется локально; локальный Redis runtime отличается от production Redis 7. Проверки транспортной auth/session/security из существующего suite проходят; production-инфраструктура не затрагивалась.
