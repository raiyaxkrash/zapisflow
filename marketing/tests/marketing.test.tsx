import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { NAV_LINKS, EXTERNAL_LINKS, FAQ_DATA, FEATURES_DATA, FOR_WHOM_DATA, STEPS_DATA } from '../src/data/content.ts';
import { Header } from '../src/components/Header.tsx';
import { FAQ } from '../src/components/FAQ.tsx';
import { Pricing } from '../src/components/Pricing.tsx';
import { BotVsMiniApp } from '../src/components/BotVsMiniApp.tsx';
import { App } from '../src/App.tsx';

describe('ZapisFlow Marketing Site Data Tests', () => {
  it('contains valid and secure external links without invented usernames', () => {
    expect(EXTERNAL_LINKS.demoBot).toBe('https://t.me/zapisflowsbot');
    expect(EXTERNAL_LINKS.support).toBe('https://t.me/zapisflow');
    expect(EXTERNAL_LINKS.createBot).toBe('https://t.me/zapisflowsbot');
  });

  it('contains all required navigation items', () => {
    const labels = NAV_LINKS.map(l => l.label);
    expect(labels).toContain('Возможности');
    expect(labels).toContain('Как работает');
    expect(labels).toContain('Для кого');
    expect(labels).toContain('Тариф');
    expect(labels).toContain('FAQ');
  });

  it('contains all 7 required FAQ entries from product specification', () => {
    expect(FAQ_DATA.length).toBeGreaterThanOrEqual(7);
    const questions = FAQ_DATA.map(f => f.question.toLowerCase());
    
    expect(questions.some(q => q.includes('приложение'))).toBe(true);
    expect(questions.some(q => q.includes('собственный бот'))).toBe(true);
    expect(questions.some(q => q.includes('сотрудник'))).toBe(true);
    expect(questions.some(q => q.includes('без mini app'))).toBe(true);
    expect(questions.some(q => q.includes('позже'))).toBe(true);
    expect(questions.some(q => q.includes('записывается'))).toBe(true);
    expect(questions.some(q => q.includes('отменять записи'))).toBe(true);
  });

  it('has 4 onboarding steps', () => {
    expect(STEPS_DATA).toHaveLength(4);
    expect(STEPS_DATA[0].title).toContain('проект');
    expect(STEPS_DATA[1].title).toContain('бота');
    expect(STEPS_DATA[2].title).toContain('расписание');
    expect(STEPS_DATA[3].title).toContain('записи');
  });

  it('has all core target beauty niches', () => {
    expect(FOR_WHOM_DATA.length).toBe(8);
    const titles = FOR_WHOM_DATA.map(n => n.title.toLowerCase());
    expect(titles.some(t => t.includes('маникюр'))).toBe(true);
    expect(titles.some(t => t.includes('бров'))).toBe(true);
    expect(titles.some(t => t.includes('лэш') || t.includes('ресниц'))).toBe(true);
    expect(titles.some(t => t.includes('барбер'))).toBe(true);
    expect(titles.some(t => t.includes('массаж'))).toBe(true);
    expect(titles.some(t => t.includes('косметол'))).toBe(true);
    expect(titles.some(t => t.includes('студи'))).toBe(true);
  });

  it('includes key product features like Multi-Staff, Prepayment, CRM, Mini App', () => {
    const featureIds = FEATURES_DATA.map(f => f.id);
    expect(featureIds).toContain('online-booking');
    expect(featureIds).toContain('own-bot');
    expect(featureIds).toContain('schedule');
    expect(featureIds).toContain('multi-staff');
    expect(featureIds).toContain('crm');
    expect(featureIds).toContain('prepayment');
    expect(featureIds).toContain('reminders');
    expect(featureIds).toContain('mini-app');
  });
});

describe('ZapisFlow Marketing Site Component Tests', () => {
  beforeEach(() => {
    // Mock matchMedia
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: (query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }),
    });
  });

  it('renders Header with navigation and logo', () => {
    render(<Header />);
    expect(screen.getByText('ZapisFlow')).toBeDefined();
    expect(screen.getAllByText('Возможности').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('Тариф').length).toBeGreaterThanOrEqual(1);
  });

  it('renders Pricing with exact 499 ₽ price and 14 days trial', () => {
    render(<Pricing />);
    expect(screen.getByText('499 ₽')).toBeDefined();
    expect(screen.getByText('14 дней бесплатно')).toBeDefined();
    expect(screen.getByText('Попробовать бесплатно')).toBeDefined();
  });

  it('toggles FAQ items interactively and updates ARIA states', () => {
    render(<FAQ />);
    const questionBtn = screen.getByText('Нужно ли клиенту устанавливать отдельное приложение?');
    const buttonElement = questionBtn.closest('button');
    expect(buttonElement).not.toBeNull();
    expect(buttonElement?.getAttribute('aria-expanded')).toBe('true');

    // Click to close
    fireEvent.click(buttonElement!);
    expect(buttonElement?.getAttribute('aria-expanded')).toBe('false');

    // Click to reopen
    fireEvent.click(buttonElement!);
    expect(buttonElement?.getAttribute('aria-expanded')).toBe('true');
  });

  it('toggles BotVsMiniApp comparison tabs', () => {
    render(<BotVsMiniApp />);
    expect(screen.getByText('Классический Telegram-бот')).toBeDefined();
    expect(screen.getAllByText('Telegram Mini App').length).toBeGreaterThanOrEqual(1);

    const botTab = screen.getByRole('tab', { name: 'Классический бот' });
    fireEvent.click(botTab);
    expect(screen.getByText('Привычный чат и быстрые кнопки')).toBeDefined();
  });

  it('renders full App without crashing', () => {
    const { container } = render(<App />);
    expect(container.querySelector('.site-wrapper')).not.toBeNull();
    expect(container.querySelector('#main-content')).not.toBeNull();
    
    // Check main hero title
    const heroHeading = screen.getByRole('heading', { level: 1 });
    expect(heroHeading.textContent).toContain('Запись клиентов');
    expect(heroHeading.textContent).toContain('прямо в Telegram');

    expect(screen.getByText('Перестаньте вести запись вручную')).toBeDefined();
  });
});
