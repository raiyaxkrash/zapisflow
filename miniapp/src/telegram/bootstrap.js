export function bootstrap(win = window) {
  const tg = win.Telegram?.WebApp;
  if (!tg?.initData) throw new Error('Откройте Mini App кнопкой меню в Telegram-боте проекта');
  tg.ready(); tg.expand();
  const updateViewport = () => {
    const root = win.document.documentElement;
    if (tg.viewportHeight) root.style.setProperty('--app-height', `${tg.viewportHeight}px`);
    for (const side of ['top', 'bottom', 'left', 'right']) {
      const inset = Math.max(tg.safeAreaInset?.[side] || 0, tg.contentSafeAreaInset?.[side] || 0);
      root.style.setProperty(`--app-safe-${side}`, `${inset}px`);
    }
  };
  updateViewport();
  for (const event of ['viewportChanged', 'safeAreaChanged', 'contentSafeAreaChanged']) tg.onEvent(event, updateViewport);
  return tg;
}
export function botIdFromPath(path) {
  const match = path.match(/^\/b\/([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})\/?$/i);
  if (!match) throw new Error('Неверная ссылка. Откройте Mini App из бота проекта');
  return match[1];
}
