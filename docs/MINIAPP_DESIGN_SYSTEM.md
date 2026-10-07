# ZapisFlow Mini App foundation — Фаза 3

База: `ef8e88e` на `codex/miniapp-redesign-customization`. Эта фаза создаёт foundation; полный client/master screen redesign остаётся Фазами 4/5. Backend contracts, auth, price/availability, permissions и payment state не перенесены во frontend. Production не менялся.

## Дизайн-система

Marketing остаётся визуальным ориентиром: calm slate/blue, #FAFAFC / #0F172A, акцент #1D72FE, dark surfaces #090D16 / #101626 / #161F36. Приложение использует system fonts ради скорости WebView; marketing сохраняет Inter/Plus Jakarta Sans и desktop layout.

Canonical source: `shared/design/tokens.css`. Docker build contexts независимы, поэтому `miniapp/src/tokens.css` и `marketing/src/tokens.css` содержат идентичные mirrors. Foundation test проверяет все три файла; менять их вместе. Runtime imports за границами Docker contexts нет. Marketing aliases для базовой палитры/radii теперь читают semantic tokens.

| Группа | Токены |
| --- | --- |
| Surfaces | bg, surface, surface-elevated / surface-raised |
| Content | text, text-muted / text-secondary / muted, border / divider |
| Accent | accent, hover, active / pressed, soft, foreground, focus / focus-ring |
| Feedback | success, warning, danger, info |
| Space | 4/8/12/16/20/24/32/40 px |
| Radius | sm 6, md 10, lg 16, xl 24, pill 9999 px |
| Type | display 32, page 28, section 20, body/button 16, secondary 14, caption 12, price 18 px |
| Motion | fast 150, normal 250, slow 350 ms; reduced motion respected |
| Controls | minimum 44 px |

Все имена начинаются с `--zf-`. Default blue foreground исправлен на читаемый black; hover/active цвет различается. Не применять белый текст ко всем accents автоматически.

## Theme и accent

`theme/theme.js`: System/Light/Dark; default System. В Telegram следуем colorScheme, с themeParams bg fallback; live themeChanged без reload. Проверенные header/text themeParams можно применить к chrome только при AA contrast ≥4.5. Невалидные цвета игнорируются. В browser/component dev — prefers-color-scheme и live media changes. Явное предпочтение сохраняется в localStorage, не содержит auth data; ошибки storage не мешают приложению. Invalid preference → System. Dispose удаляет подписки.

Marketing ThemeToggle циклически переключает System → Light → Dark → System, сохраняет preference и обновляется по browser media events. Выбор пользователя имеет приоритет перед system/brand default.

`theme/accent.js`: только #RRGGBB, fallback #1D72FE; foreground выбран по относительной luminance. Hover/active смешиваются в сторону, сохраняющую контраст foreground; soft/focus derived через CSS color-mix. Проверены default и крайние palette values. Это foundation существующего branding, не новый branding editor. Нейтральный focus outline сохраняется независимо от accent.

## Архитектура

```text
app.js → application/shell + navigation
       → ui facade → primitives / icons / escape
       → calendar + booking foundations
       → theme + Telegram bridge
       → existing Api transport
```

ES modules сохранены. Аудит не доказал необходимость React runtime: incremental extraction проще сохранить совместимым с API и bundle budget. Controller сокращён с 1005 до приблизительно 957 строк; screen render/forms остаются в нём намеренно до соответствующих фаз. Это не заявление о завершённом разделении всех экранов.

`ui.js` сохраняет прежние exports для screens; button/header/empty делегируют primitives. ServiceCard/StaffCard/AppointmentCard переиспользуют существующие renderers, а не создают вторую логику карточек.

`application/navigation.js` задаёт client/master destinations, active parent для вложенных screens, back targets и revision guard последней async navigation. Retry читает snapshot route; stale response не перерисовывает новый экран. Visibility — только UI: серверный RBAC остаётся обязательным.

## Component inventory

`ui/primitives.js`: Button, IconButton, Card, Section, PageHeader, TopBar, BottomNav, Badge, StatusPill, Input, Textarea, Switch, SegmentedControl, Dialog, BottomSheet, Skeleton, EmptyState, ErrorState.

`ui/booking.js`: ServiceCard, StaffCard, AppointmentCard, Calendar (existing calendarView), SlotPicker, BookingSummary. Server DTOs являются источником значений. SlotPicker принимает `{value,label,available}`; Calendar принимает backend dates/reasons/horizon. Frontend не вычисляет свободные слоты и не определяет цену.

Функции возвращают markup, интегрируемый существующим renderer. Labels/values/actions экранируются. `content` slots — исключительно application-generated escaped markup, не raw HTML бизнеса. Never pass arbitrary tenant HTML в Card/Section/Dialog content. Вводы имеют semantic labels и error descriptions. Dialog использует native `<dialog>` / showModal: native focus containment, Escape и backdrop; openDialog восстанавливает focus invoker. Unsupported showModal возвращает false: caller должен показать альтернативный UI.

Единая icon system: маленький inline SVG набор `ui/icons.js`; никакой внешней icon library. Emoji остаются допустимыми в Telegram messages, не основными application icons.

## Navigation / shell

Client: Главная / Записаться / Мои записи / Ещё.
Master: Сегодня / Календарь / Клиенты / Ещё; staff capability ограничивает destinations. Role switch не меняет identity/session.

Shell содержит TopBar, server-gated mode selector, main landmark, bottom navigation, обязательную подпись «Работает на ZapisFlow». Route loading использует Skeleton; ошибки — ErrorState/Retry; завершение navigation фокусирует main. Экранные формы и their domain flow не перестроены.

## Telegram helpers

Официальная спецификация: https://core.telegram.org/bots/webapps (проверена 7 октября 2026).

`telegram/bootstrap.js` сохраняет raw initData для существующего auth; вне Telegram fail-closed. Отслеживает viewportChanged и SDK safe/content safe insets; повторный bootstrap снимает прежние viewport подписки. VisualViewport resize определяет keyboard state; bottom nav скрывается при открытой клавиатуре, чтобы не закрывать форму. CSS использует max SDK inset и env safe-area fallback, включая left/right.

`telegram/bridge.js`: setBackVisible, onBack, notify, setPrimaryAction, syncChrome, dispose. Screen controller больше не вызывает BackButton/HapticFeedback напрямую. Метод native primary action подготовлен, но booking CTA не переносился в MainButton в этой фазе: нет двух одновременно видимых primary buttons. Replacing action снимает старый callback; route hides прежний native action.

MainButton — официальное свойство SDK; BottomButton — имя типа. Back/haptics/chrome version gated. Для старых 6.1 clients header использует bg_color; arbitrary hex header требует 6.9. SDK theme/chrome ошибки не скрываются generic catch. No fullscreen/swipe lock/payment APIs added.

## Accessibility rules

Цель WCAG 2.2 AA; это не сертификация. Semantic controls, visible focus, 44px targets, linked field errors, current navigation/step, native radio/switch/dialog semantics, reduced motion. Calendar содержит today/selected/reason labels и data-state, disabled backend unavailable dates, keyboard arrows и loading aria-busy; цвет не единственный признак. Полная roving grid модель и реальные screenreader journeys — отдельная QA задача Фазы 4.

Client branding не может определять доступ, subscription или booking result. Custom text всегда escaped, цвета strict; URL/storage/auth policy остаются существующими backend responsibilities.

## Verification / performance

Baseline Phase 2 Mini App initial JS 40.55 kB, gzip 14.51; CSS 11.19 kB, gzip 3.13. Foundation JS 45.52 kB (+12.3%), gzip 16.26; CSS 15.53 kB, gzip 3.94. Marketing JS 58.35 → 58.79 kB (+0.8%); vendor 141.78 kB не изменился, CSS 30.87 → 32.03 kB. Master/editor/settings lazy chunks сохранены; unused primitive/booking exports tree-shaken. Без новых dependencies или React/UI frameworks.

53 Mini App tests включают десять новых foundation tests: tokens parity, escaping/forms, dialog focus, role navigation/stale response, live browser/Telegram themes, accent contrast, native callback lifecycle, keyboard/safe areas и server DTO foundations. Existing 43 flows сохранены. Marketing добавлены три theme regressions.

Visual matrix локально `.artifacts/miniapp-redesign/phase-3/`: client/master, light/dark, 375×812 и 430×932, real production bundle с browser-only mocked API/Telegram context. Screenshots не входят в Git. Это не проверка native Telegram iOS/Android/Desktop.

Перед commit: full pytest на fresh isolated PostgreSQL, frontend tests/builds, compileall, diff-check, browser visual/runtime check. Production deployment и Фаза 4 не выполняются.

Итог: backend 775 passed, 0 failed, 0 skipped, 6 прежних warnings; Mini App 53 passed; marketing 23 passed; обе сборки/compileall/diff-check PASS. 68 final screenshots, 0 pageerrors/overflow и 0 axe violations на проверенных 34 screen/theme combinations 375px. Native browser dialog отдельно проверен: focus containment, Escape, возврат focus — PASS. Browser fixtures не заменяют реальные Telegram safe areas/keyboard/native UAT.
