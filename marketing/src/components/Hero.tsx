import React from 'react';
import { EXTERNAL_LINKS } from '../data/content.ts';
import { HeroVisual } from './HeroVisual.tsx';

export const Hero: React.FC = () => {
  return (
    <section className="hero-section" aria-labelledby="hero-heading">
      <div className="container hero-container">
        <div className="hero-content">
          {/* Eyebrow badge */}
          <div className="hero-badge">
            <span className="badge-pulse"></span>
            <span>Telegram-first платформа для записи</span>
          </div>

          {/* Main Title */}
          <h1 id="hero-heading" className="hero-title">
            Запись клиентов <br />
            <span className="text-highlight">прямо в Telegram</span>
          </h1>

          {/* Subtitle */}
          <p className="hero-subtitle">
            Собственный Telegram-бот для вашего бизнеса: запись, расписание, клиенты, сотрудники и напоминания — в одном месте.
          </p>

          {/* CTA Buttons */}
          <div className="hero-cta-group">
            <a
              href={EXTERNAL_LINKS.createBot}
              target="_blank"
              rel="noopener noreferrer"
              className="btn btn-primary btn-lg"
            >
              <span>Создать своего бота</span>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="5" y1="12" x2="19" y2="12"></line>
                <polyline points="12 5 19 12 12 19"></polyline>
              </svg>
            </a>

            <a
              href={EXTERNAL_LINKS.demoBot}
              target="_blank"
              rel="noopener noreferrer"
              className="btn btn-secondary btn-lg"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polygon points="5 3 19 12 5 21 5 3"></polygon>
              </svg>
              <span>Открыть демо</span>
            </a>
          </div>

          {/* Quick Value Proof */}
          <div className="hero-perks">
            <div className="perk-item">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#10B981" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="20 6 9 17 4 12"></polyline>
              </svg>
              <span>14 дней бесплатно</span>
            </div>
            <div className="perk-item">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#10B981" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="20 6 9 17 4 12"></polyline>
              </svg>
              <span>Без привязки карты</span>
            </div>
            <div className="perk-item">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#10B981" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="20 6 9 17 4 12"></polyline>
              </svg>
              <span>Запуск за 2 минуты</span>
            </div>
          </div>
        </div>

        {/* Hero Visual Layer */}
        <div className="hero-visual-column">
          <HeroVisual />
        </div>
      </div>
    </section>
  );
};
