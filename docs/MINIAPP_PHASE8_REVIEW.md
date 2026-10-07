# Mini App — проверка Фазы 8

База: `939c509f154536600a008f90c72b99b9423884df`, ветка `codex/miniapp-redesign-customization`.
Production и его конфигурация не изменяются. Web Booking, выведенный из текущего клиентского продукта в Фазе 2, не восстанавливается.

## Безопасность

Проверены адаптеры `app/web/miniapp.py`, `app/web/branding.py`, `app/services/branding.py`, клиентский API/media слой и существующие regression tests.

- Tenant определяется серверной MiniAppSession, привязанной к BotInstance и версии токена. Заголовок клиента должен совпадать с контекстом сессии. Числовые ID проверяются внутри tenant.
- Запись и отмена используют существующие domain services; frontend не рассчитывает доступность и не меняет статус записи самостоятельно.
- Мутации проверяют Origin и хеш CSRF token; после блокировки повторно проверяют доступность проекта/бота. Права считываются сервером. Branding editor и синхронизация профиля доступны только владельцу.
- `initData` проверяется сервером. В браузере не сохраняются Telegram токены или секреты; cookie сессии остаётся HttpOnly.
- Branding принимает ограниченный plain text, строгий `#RRGGBB` и перечисления темы/стиля. Произвольные HTML/CSS/JS не поддерживаются. Telegram текст экранируется, ссылки контактов строятся из проверенных значений.
- Logo/cover ограничены 4 MiB и 16 MP, декодируются Pillow, нормализуются в WebP (320×320 / 1200×600), метаданные не сохраняются. SVG запрещён; filename проверяется; assets хранятся в PostgreSQL, пользовательский путь файла не используется.
- Публичность логотипа и обложки намеренная: это медиа публичного бизнеса. Чеки и private portfolio используют авторизованный tenant-scoped транспорт; object URLs отзываются при уходе со страницы.
- Изменения branding атомарны; текущий контекст читается заново, URL asset содержит revision. Неправильные настройки приводят к безопасным defaults.
- Проверка зависимостей Mini App и marketing: npm audit, 0 vulnerabilities на момент Фазы 8. Новые зависимости не добавлены.

Подтверждённого нового дефекта tenant isolation, CSRF или загрузки assets при этом проходе не найдено. Это не гарантия отсутствия всех уязвимостей и не замена production/native UAT.

## Подтверждённые находки и изменения

| Находка | Исправление | Проверка |
| --- | --- | --- |
| Тайм-аут fetch заканчивался на заголовках, зависшее тело JSON/image не имело deadline | Deadline сохраняется до окончания чтения тела, очищается в finally | Stalled JSON и image body regression tests |
| Календарь поддерживал стрелки, но не быстрый переход к краям доступного диапазона | Home/End выбирают первую/последнюю доступную дату, пропуская disabled | DOM focus regression test |
| Предпросмотр принудительно плавно прокручивался даже при reduced motion | Helper учитывает prefers-reduced-motion | Regression test обоих режимов |
| CSS мастерского workspace включён в начальный client bundle | Browser-only dynamic import при открытии master workspace | Production build + browser smoke |

Три выбранных usability улучшения: ограниченное ожидание сетевого ответа, быстрые клавиатурные переходы календаря, reduced-motion предпросмотр. Все локальны, не требуют новых API/таблиц и не меняют booking/payment rules. Уже реализованные nearest slot, repeat, ICS и checklist не дублируются.

## Accessibility

Используются существующие semantic forms, labels, focus-visible, диалоговые примитивы и календарные состояния. Цвет не является единственным индикатором выбранной даты/недоступности. Цвет foreground вычисляется из luminance accent. Browser fixtures проверяют light/dark и крайние цвета; axe проверяет автоматизируемую часть WCAG, но не подтверждает полное соответствие WCAG 2.2 AA самостоятельно.

Ручная проверка Telegram iOS/Android/Desktop, screen reader и реального устройства с клавиатурой остаётся UAT: browser harness подставляет Telegram/API, а не ослабляет production auth.

## Performance

| Build | Initial JS | gzip JS | Initial CSS | gzip CSS |
| --- | ---: | ---: | ---: | ---: |
| Фаза 1/2 | 40.55 kB | 14.51 kB | 11.19 kB | 3.13 kB |
| Фаза 3 | 45.52 kB | 16.26 kB | 15.53 kB | 3.94 kB |
| База Фазы 8 | 51.05 kB | 17.63 kB | 26.35 kB | 5.35 kB |
| Фаза 8 | 51.49 kB | 17.84 kB | 21.82 kB | 4.83 kB |

Initial CSS −17.2% (gzip −9.7%). Initial JS +0.9%; рост обусловлен исправлением deadline, keyboard/motion helpers и CSS loader. Мастерский CSS 4.53 kB / gzip 1.07 kB загружается отдельно; renderer, settings, branding editor и ICS уже используют lazy chunks. Клиент не обязан загружать CRM/analytics renderer. Portfolio использует IntersectionObserver и private media transport; logo/cover нормализованы сервером. Абсолютные показатели TTI из mocked localhost не эквивалентны реальному Telegram WebView.

## Проверки и артефакты

Regression tests: `miniapp/tests/phase8.test.js`.
Browser captures: локальный `.artifacts/miniapp-redesign/phase-8/` (не коммитятся).
Полный backend suite запускается на новой disposable PostgreSQL базе с актуальными migrations и Redis; production DB не используется.
Итоговые результаты тестов и visual QA фиксируются в финальном отчёте после окончания проверки.

### Итог проверки

- Backend: **811 passed, 0 failed, 0 skipped**, 6 существующих warnings (AsyncMock/SQLAlchemy transaction), 338.66 s.
- Mini App: **78 passed, 0 failed, 0 skipped**.
- Marketing: **23 passed**, build PASS.
- Mini App build, compileall и diff-check: PASS.
- Chromium production bundle: 127 локальных screenshots; финальный smoke/axe — 0 runtime errors, 0 axe violations, 0 horizontal overflows. Основные Client Home/Booking/Calendar/My bookings и Master Today/Calendar/CRM проверены в Light/Dark; branding fixtures покрывают размеры 320/375/390/430/768 и крайние accent colors. Axe не заменяет ручной screen-reader/native QA.
- Browser network assertion: в начальной клиентской загрузке отсутствуют workspace CSS и master renderer JS; оба появляются при входе в master mode.
- Migration fresh test DB → `2026_10_05_0029`: PASS. Схема в Фазе 8 не менялась.
- Новые screenshots, browser harness, skills и локальные test credentials не включаются в Git.
