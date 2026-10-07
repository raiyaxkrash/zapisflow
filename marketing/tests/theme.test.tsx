import { beforeEach, afterEach, it, expect } from 'vitest';
import { cleanup, render, screen, fireEvent, act } from '@testing-library/react';
import { ThemeToggle } from '../src/components/ThemeToggle';
let changed: EventListener | undefined;
let dark = false;
beforeEach(() => {
  localStorage.clear(); dark = false; changed = undefined;
  Object.defineProperty(window, 'matchMedia', { writable: true, value: () => ({ get matches() { return dark; }, addEventListener(_name: string, handler: EventListener) { changed = handler; }, removeEventListener() {} }) });
});
afterEach(cleanup);
it('defaults to System and follows browser changes without reload', () => {
  render(<ThemeToggle />);
  expect(screen.getByRole('button').getAttribute('aria-label')).toContain('Система');
  act(() => { dark = true; changed?.(new Event('change')); });
  expect(document.documentElement.dataset.theme).toBe('dark');
});
it('cycles System, Light, Dark and returns to System with persistence', () => {
  render(<ThemeToggle />);
  const button = screen.getByRole('button');
  for (const preference of ['light', 'dark', 'system']) {
    fireEvent.click(button);
    expect(localStorage.getItem('zapisflow-theme')).toBe(preference);
  }
});
it('explicit Light is preserved when the browser switches to Dark', () => {
  localStorage.setItem('zapisflow-theme', 'light');
  render(<ThemeToggle />);
  act(() => { dark = true; changed?.(new Event('change')); });
  expect(document.documentElement.dataset.theme).toBe('light');
});
