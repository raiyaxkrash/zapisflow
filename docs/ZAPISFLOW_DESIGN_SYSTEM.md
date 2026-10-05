# ZapisFlow — визуальная система

## Принципы
Три клиентских канала используют один бренд бизнеса поверх ZapisFlow. Marketing home сохраняет ZapisFlow. Брендинг меняет контент и accent, но не business rules, legal copy или обязательную подпись «Работает на ZapisFlow».

## Typography
System sans для приложения (быстрый WebView, Cyrillic). Marketing существующие Inter/Plus Jakarta Sans. Заголовки28/24/20, body16, supporting14, minimum12. Line-height1.5, ограничение длины строки72ch, tabular nums для времени/цен.

## Tokens
Canvas #FAFAFC; surface #FFFFFF; text #0F172A; muted #475569; border #CBD5E1; accent #1D72FE. Dark canvas #090D16; surface #101626; elevated #161F36; text #F8FAFC; muted #B6C2D3. Danger #B42318; success #087A55; warning #8A5700 (адаптивные light/dark surfaces).

CSS --zf-bg/surface/surface-elevated/text/text-muted/border/accent/accent-hover/accent-soft/accent-foreground/focus/danger/success/warning.
Accent только #RRGGBB; foreground white/near-black выбирается по максимальному WCAG contrast. Focus outline независим от акцента. No arbitrary CSS.

## Scale
Spacing4/8/12/16/24/32/48. Radius6/10/16/24. Cards borders + restrained shadow; no floating glass. Tap targets44px minimum, bottom nav56px+safe inset. Icon SVG stroke24 viewbox20/24px, декоративные aria-hidden.

## Buttons & forms
Одна primary action на screen; secondary outlined/tinted; destructive отдельно с confirmation. Labels всегда видимы; tel/email/number correct keyboards. Errors rolealert и visible message. Buttons показывают busy state; booking никогда не optimistic.

## Navigation
Client: Главная / Записаться / Мои записи / Ещё.
Master: Сегодня / Календарь / Клиенты / Ещё.
Owner client preview остаётся той же session с прежними capability. Staff-only screens не получают owner actions.

## Feedback
Skeleton отражает структуру экрана, aria-busy/status. Empty объясняет следующий шаг. Payment статусы русские. Error позволяет retry, не показывает HTTP/SQL/FSM. Saved feedback видим.

## Calendar
Available — button; unavailable/past/horizon — disabled с explanatory aria label. Today — обозначение; selected aria-pressed. Keyboard arrows seek enabled day; Enter/Space select. Нельзя рассчитывать availability во frontend.

## Themes & motion
System учитывает Telegram scheme (Mini App) либо prefers-color-scheme (web); Light/Dark overrides. Tenant default применяется до user override. 150ms functional transitions; prefers-reduced-motion отключает animation/transform. Insets берутся из Telegram+CSSenv; keyboard resize не закрывает CTA.

## Branding & preview
Brand name128, tagline120, description2000, welcome1000, CTA32. Logo/cover необязательны. Preview изменяет только local draft до Save. Sections скрывают лишь optional content; booking/policy/prepayment/Powered by остаются. Reset требует confirmation. Contacts/socials переиспользуют текущие настройки.

## Review gates
Light/dark/default + extreme accents, mobile320..430/tablet/desktop, keyboard, errors, focus, ownership, stable retries, no secrets in bundles. Browser mock screenshots не заменяют Telegram iOS/Android/Desktop UAT.
