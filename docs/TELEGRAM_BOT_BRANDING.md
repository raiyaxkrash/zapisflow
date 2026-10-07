# Оформление клиентского Telegram-бота

## Один источник настроек

Mini App и клиентский бот читают `MasterSettings.branding`. Новых таблиц, миграций и повторного provisioning не требуется. Manual и managed BotInstance используют одинаковые клиентские обработчики. Manager Bot остаётся платформенным ZapisFlow; его обработчики и identity mapping не меняются.

`/start` показывает название бизнеса и сохранённое приветствие. Пустое название использует существующее имя проекта; пустое приветствие — нейтральный текст записи. Невалидные настройки дают стандартные значения. При SETUP_REQUIRED обычный клиент получает сообщение о настройке записи. Ветка `/start admin` обрабатывается до приветствия. HTML пользовательских текстов экранируется.

## Меню и существующие сообщения

Первые действия: запись, мои записи, услуги. Optional portfolio/reviews/contacts учитывают настройки проекта. Отдельного раздела команды и FAQ в текущем клиентском меню нет; новые подсистемы не добавлены. Скрытие команды в презентации не отключает необходимый выбор сотрудника при записи.

Возвраты после booking, оплаты, отмены и отзыва перечитывают актуальные настройки. Нажатие старой кнопки скрытого раздела показывает alert, включая portfolio navigation и отправку контакта. Core booking остаётся доступным. Callback identifiers сохраняются, в частности `menu:book`, `menu:services`, `menu:my_bookings`, `menu:main`, `menu:about`. Branding CTA меняет только подпись.

Кнопка записи в сообщении — обычный callback существующего FSM. WebApp/URL у неё отсутствуют. Системная Menu Button отдельно открывает Mini App с подписью **ZapisFlow**, чтобы этот вход оставался узнаваемым независимо от CTA проекта:

```text
MINI_APP_BASE_URL/b/<BotInstance.public_id>
```

При `mini_app_enabled=false` или пустом base URL используется `MenuButtonCommands`. Текстовая запись от Mini App не зависит. Существующая provisioning/switch логика сохраняется.

## Профиль Telegram и retry

В редакторе владельца: сначала **Сохранить оформление**, затем **Обновить профиль Telegram**. Endpoint `POST /api/miniapp/master/branding/sync-telegram` читает опубликованные настройки, не отправляет локальный черновик и не меняет токен. Он синхронизирует name (до 64 символов), description (512), short description (120), стабильные команды `/start`, `/cancel` и системную Menu Button. Локальный branding уже сохранён отдельной операцией.

Timeout, Telegram API error, 403, сеть и недоступный BotRegistry дают понятный ответ с повтором синхронизации. Секреты и диагностические детали не включаются в ответ. После частичного внешнего обновления повтор безопасно применяет те же desired values. Существующий outbox обслуживает доставку сообщений; новые типы outbox задач для изменения профиля не вводятся. Автоматического фонового retry профиля нет — владелец повторяет действие.

API редактора защищено owner-only, session, Origin, CSRF, tenant context и rate limit. При отключённом Mini App редактор/API недоступны штатно; системная кнопка переключается через существующий owner/provisioning flow.

Актуальная [официальная Bot API](https://core.telegram.org/bots/api) поддерживает setMyName, setMyDescription, setMyShortDescription, setMyCommands, setChatMenuButton и setMyProfilePhoto. Автоматическая синхронизация фото не реализована в этой фазе; его можно настроить отдельно через BotFather. Username автоматически не меняется.

## Проверки и ограничения

Regression tests покрывают настройки двух tenants, default/custom welcome, escaping, manual/managed, SETUP_REQUIRED, deep link, старые callbacks, text FSM, menu ON/OFF, профиль и ошибки синхронизации. Реальный Telegram API используется только как mock в локальных тестах. Проверка отображения на настоящих Telegram iOS/Android/Desktop после разрешённого deployment остаётся ручным UAT.

## Финальная проверка

Актуальная регрессия и ручные ограничения: [MINIAPP_FINAL_QA.md](MINIAPP_FINAL_QA.md).
Текстовая запись и системная Menu Button проверяются независимо; logo Mini App
не означает автоматическое изменение profile photo Telegram. Для будущей выкатки
использовать [deployment checklist](MINIAPP_DEPLOYMENT_CHECKLIST.md), без массовой
синхронизации всех production bots. Telegram API/native отображение остаются UAT.
