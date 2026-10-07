# Оформление бизнеса и обновлённые клиентские интерфейсы

## Архитектура
Оформление принадлежит Master, хранится в MasterSettings.branding. Один проект использует одинаковое название, тексты, акцент и изображения в Telegram и Mini App. Главная marketing остаётся ZapisFlow. BookingService, SlotEngine, Appointment, Payment, SubscriptionAccessPolicy и User не заменяются.

Mini App остаётся Vite + ES modules: текущая кодовая база не оправдывает стоимость React runtime. Редактор оформления загружается отдельным chunk только при открытии владельцем. CSS tokens зеркалируются между независимыми Docker build contexts; автоматический тест проверяет равенство. Маркетинговый сайт остаётся React18/TypeScript; браузерная запись удалена.

## Настройки владельца
Управление → Ещё → Оформление: название, логотип, обложка, tagline, описание, приветствие, подпись CTA, акцент #RRGGBB, System/Light/Dark, Clean/Soft/Compact, портфолио/отзывы/контакты/команда. Контакты и соцсети доступны и в редакторе оформления; используются те же поля MasterSettings, без дублирующей модели.

Черновик и локальные image previews применяются только после «Сохранить». Save отображает явное подтверждение. Reset требует подтверждения, удаляет брендовые изображения и возвращает defaults. Просмотр глазами клиента сохраняет текущую session/capabilities, не подменяет identity. Подпись «Работает на ZapisFlow» обязательна. Настройки не вводят платные entitlement.

## Безопасность и assets
Только владелец проекта меняет оформление. Используются действующие Origin, CSRF, session, tenant-scoping и HTTP idempotency. Master ID не принимается от браузера. Branding не принимает arbitrary HTML/CSS/JS. Цвет строго #RRGGBB, длины ограничены, неправильные настройки дают безопасные defaults.

PNG/JPEG/WebP до 4 МБ и 16 млн пикселей; SVG запрещён. Pillow проверяет действительный формат, применяет ориентацию и нормализует в WebP без metadata. Logo ≤320px, cover ≤1200×600px, каждый результат ≤256 KiB. Два ограниченных binary assets хранятся отдельно от settings в PostgreSQL; нет base64, пользовательских путей и оригиналов. Резервное копирование PostgreSQL включает assets. Публичные UUID URLs не содержат credentials; images привязаны к проекту, no-store предотвращает stale branding. Портфолио читает существующие Telegram file IDs через сервер с ограниченным буфером.

## Telegram
/start читает актуальное оформление и экранирует имя пользователя/бизнеса. Обычная кнопка записи сохраняет callback menu:book; системная Mini App Menu Button и её switch не меняются. Optional menu sections учитывают настройки проекта, в том числе после возврата из записи и оплаты. Старые кнопки скрытых разделов дают alert. Ручная синхронизация name/description/short_description, стабильных commands и системной Menu Button повторяема; timeout/API failure не отменяет сохранённый бренд. Username автоматически не меняется. Фото профиля в этой версии настраивается отдельно через BotFather; это ограничение реализации, не утверждение об отсутствии современного API.

## Темы и доступность
System использует Telegram colorScheme в WebView и prefers-color-scheme в браузере. Light/Dark — пользовательский выбор. Только предпочтение темы хранится в localStorage, не auth token. Акцент автоматически получает чёрный/белый foreground по максимальному WCAG contrast. Touch targets ≥44px, focus-visible, semantic labels, reduced motion, calendar keyboard arrows и native Enter/Space. Safe areas используют существующий Telegram viewport adapter.

## API
Mini App owner: POST /api/miniapp/master/branding/save (атомарный multipart Save, см. MINIAPP_BRANDING_EDITOR.md); GET/PUT /api/miniapp/master/branding; POST /reset; POST/DELETE /assets/{logo|cover}; POST /sync-telegram.
Client: branding в /api/miniapp/context; GET /client/reviews; GET /client/portfolio; GET /client/portfolio/{id}/image.
Public media: GET /api/branding/{public_id}/assets/{logo|cover}.
Все сокращённые пути выше относятся к указанному prefix. Изображения и optional sections не обходят существующие channel/subscription checks.

## Локальная разработка
Backend — действующий workflow README с отдельной тестовой PostgreSQL и Redis. Mini App: cd miniapp, npm install, npm run dev. Маркетинговый сайт: cd marketing, npm install, npm run dev. Открытие Mini App вне Telegram штатно fail-closed; тесты подменяют transport/Telegram context только в браузерном harness, production bypass отсутствует.

Проверки: pytest -q; python -m compileall app tests; npm test и npm run build в miniapp и marketing; git diff --check. Новые тесты покрывают owner permissions, tenant assets, CSRF, defaults/reset, malicious texts/colors, MIME/size, Telegram timeout/403/invalid parameter, контраст, tokens parity и draft/save. Дополнительно нужен ручной Telegram iOS/Android/Desktop UAT.

## Подготовка deployment — не выполнялась
Новая миграция 2026_10_05_0029 после 0028: MasterSettings.branding и master_brand_assets. Перед будущим deployment backup DB, alembic upgrade head, rebuild backend/Mini App/marketing. Новых secrets/env/storage volumes нет. Шаблон gateway в этой ветке включает /api/branding. При будущей установке сверить действующие proxy rules; они должны пропускать /api/branding так же, как /api/miniapp. Не менять production Caddy автоматически. Нужен настоящий Telegram owner login для проверки uploads/profile sync.

## Public branding data

Logo and cover are intentionally public business presentation assets. Public read access is not considered tenant data leakage. Mutation remains OWNER-only. Private user/business assets use separate authenticated delivery paths.

Публичны: название бизнеса, tagline, публичное описание, logo/cover, акцент/тема, публичные контакты и метаданные видимости разделов. Чтение логотипа другого бизнеса по его публичному идентификатору бизнеса — ожидаемое поведение, а не cross-tenant IDOR.

`GET /api/branding/{public_id}/assets/{kind}` принимает только UUID `public_id` и strict allowlist `logo|cover`. Он не предоставляет доступ к чекам, приватному портфолио, CRM, клиентским/внутренним файлам или произвольным путям. Контент — нормализованный server-side WebP, с `X-Content-Type-Options: nosniff`, без пользовательского имени файла. `Cache-Control: no-store` исключает устаревшие изображения после замены по тому же URL.

Загрузка, удаление, сброс оформления и синхронизация Telegram требуют owner, соответствующий tenant session, Origin и CSRF. Подмена bot public ID в заголовке чужой сессии отклоняется. Приватное Mini App портфолио сохраняет authenticated API загрузку и Blob URL; его контракт отличается от публичных logo/cover.

## Финальный QA и deployment

См. [MINIAPP_FINAL_QA.md](MINIAPP_FINAL_QA.md) и
[MINIAPP_DEPLOYMENT_CHECKLIST.md](MINIAPP_DEPLOYMENT_CHECKLIST.md).
Website booking не является текущим каналом продукта. Публичный branding asset
контекст относится к Telegram/Mini App бизнесу, а не к браузерному booking flow.
Локальные visual/auth fixtures не заменяют реальный owner upload и Telegram profile UAT.
