import React, { useEffect, useState } from 'react';

export const ThemeToggle: React.FC = () => {
  type Theme = 'system' | 'light' | 'dark';
  const [theme, setTheme] = useState<Theme>('system');

  useEffect(() => {
    let saved: string | null = null;
    try { saved = localStorage.getItem('zapisflow-theme'); } catch { /* Preferences are optional. */ }
    if (saved === 'light' || saved === 'dark' || saved === 'system') setTheme(saved);
  }, []);

  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const render = () => document.documentElement.setAttribute('data-theme', theme === 'system' ? (media.matches ? 'dark' : 'light') : theme);
    render();
    media.addEventListener('change', render);
    return () => media.removeEventListener('change', render);
  }, [theme]);

  const toggleTheme = () => {
    const next = { system: 'light', light: 'dark', dark: 'system' }[theme] as Theme;
    setTheme(next);
    try { localStorage.setItem('zapisflow-theme', next); } catch { /* Keep in-memory preference. */ }
  };
  const labels = { system: 'Система', light: 'Светлая', dark: 'Тёмная' };

  return (
    <button
      type="button"
      onClick={toggleTheme}
      className="theme-toggle-btn"
      aria-label={`Тема: ${labels[theme]}. Переключить тему`}
      title="Сменить тему оформления"
    >
      {theme === 'system' ? (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><rect x="3" y="4" width="18" height="12" rx="2" /><path d="M8 20h8 M12 16v4" /></svg>
      ) : theme === 'light' ? (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>
        </svg>
      ) : (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="5"></circle>
          <line x1="12" y1="1" x2="12" y2="3"></line>
          <line x1="12" y1="21" x2="12" y2="23"></line>
          <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line>
          <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line>
          <line x1="1" y1="12" x2="3" y2="12"></line>
          <line x1="21" y1="12" x2="23" y2="12"></line>
          <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line>
          <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line>
        </svg>
      )}
    </button>
  );
};
