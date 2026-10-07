const viewportCleanups = new WeakMap();
export function bootstrap(win = window) {
  const tg = win.Telegram?.WebApp;
  if (!tg?.initData) throw new Error('Откройте Mini App кнопкой меню в Telegram-боте проекта');
  viewportCleanups.get(tg)?.();
  tg.ready(); tg.expand();
  const updateViewport = () => {
    const root = win.document.documentElement;
    if (Number.isFinite(tg.viewportHeight) && tg.viewportHeight > 0) root.style.setProperty('--app-height', `${tg.viewportHeight}px`);
    for (const side of ['top', 'bottom', 'left', 'right']) {
      const inset = Math.max(0, Number(tg.safeAreaInset?.[side]) || 0, Number(tg.contentSafeAreaInset?.[side]) || 0);
      root.style.setProperty(`--app-safe-${side}`, `${inset}px`);
    }
  };
  const updateKeyboard = () => {
    const viewport=win.visualViewport;
    const keyboard=viewport && win.innerHeight - viewport.height > 150;
    win.document.documentElement.dataset.keyboard=keyboard?'open':'closed';
  };
  win.visualViewport?.addEventListener?.('resize',updateKeyboard);
  updateKeyboard();
  updateViewport();
  for (const event of ['viewportChanged', 'safeAreaChanged', 'contentSafeAreaChanged']) tg.onEvent?.(event, updateViewport);
  viewportCleanups.set(tg, () => {
    win.visualViewport?.removeEventListener?.('resize',updateKeyboard);
    for(const event of ['viewportChanged','safeAreaChanged','contentSafeAreaChanged'])tg.offEvent?.(event,updateViewport);
  });
  return tg;
}
export function botIdFromPath(path) {
  const match = path.match(/^\/b\/([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})\/?$/i);
  if (!match) throw new Error('Неверная ссылка. Откройте Mini App из бота проекта');
  return match[1];
}
