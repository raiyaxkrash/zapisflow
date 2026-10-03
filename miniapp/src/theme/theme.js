export function applyTheme(preference, tg, root = document.documentElement) {
  const dark = preference === 'dark' || (preference === 'system' && (tg?.colorScheme === 'dark' || (!tg && matchMedia('(prefers-color-scheme: dark)').matches)));
  root.dataset.theme = dark ? 'dark' : 'light';
  for (const key of ['--blue', '--ink', '--muted']) root.style.removeProperty(key);
  // Keep Telegram's own --tg-theme-* variables intact.
  if (preference === 'system' && tg?.themeParams) {
    const p = tg.themeParams;
    for (const [key, value] of Object.entries({ '--blue': p.button_color, '--ink': p.text_color, '--muted': p.hint_color })) {
      if (value && /^#[a-f\d]{6}$/i.test(value)) root.style.setProperty(key, value);
    }
  } else { for (const key of ['--blue', '--ink', '--muted']) root.style.removeProperty(key); }
}
export function setupTheme(tg, win = window) {
  let preference; try { preference = win.localStorage.getItem('zapisflow-theme') || 'system'; } catch { preference = 'system'; }
  if (!['system', 'light', 'dark'].includes(preference)) preference = 'system';
  const render = () => applyTheme(preference, tg, win.document.documentElement);
  render(); tg?.onEvent('themeChanged', render);
  return { get: () => preference, set: value => { preference = value; try { win.localStorage.setItem('zapisflow-theme', value); } catch {} render(); } };
}
