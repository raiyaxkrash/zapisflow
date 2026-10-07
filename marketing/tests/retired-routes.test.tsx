import { beforeEach, afterEach, describe, it, expect } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { App } from '../src/App';

beforeEach(() => {
  Object.defineProperty(window, 'matchMedia', { writable: true, value: (query: string) => ({ matches: false, media: query, addEventListener() {}, removeEventListener() {} }) });
});
afterEach(() => { cleanup(); window.history.replaceState({}, '', '/'); });

describe('marketing-only website', () => {
  it.each(['/book', '/book/11111111-2222-3333-4444-555555555555', '/account/bookings'])('retires %s without a booking or login form', path => {
    window.history.replaceState({}, '', path);
    const { container } = render(<App />);
    expect(screen.getByRole('heading', { name: 'Запись через сайт больше недоступна' })).toBeDefined();
    expect(screen.getByRole('link', { name: 'На главную ZapisFlow' }).getAttribute('href')).toBe('/');
    expect(container.querySelector('form')).toBeNull();
  });
  it('keeps the homepage as marketing without website booking promises', () => {
    const { container } = render(<App />);
    expect(screen.getByRole('heading', { level: 1 }).textContent).toContain('прямо в Telegram');
    expect(container.textContent).not.toMatch(/веб-запись по ссылке|Telegram Login/);
  });
});
