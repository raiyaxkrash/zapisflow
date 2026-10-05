import React from 'react';
import { EXTERNAL_LINKS } from '../data/content.ts';

export const FinalCTA: React.FC = () => {
  return (
    <section className="final-cta-section" aria-labelledby="final-cta-heading">
      <div className="container">
        <div className="final-cta-card">
          <div className="final-cta-glow"></div>
          <div className="final-cta-content">
            <span className="badge badge-inverse">Старт за 2 минуты</span>
            <h2 id="final-cta-heading" className="final-cta-title">
              Перестаньте вести запись вручную
            </h2>
            <p className="final-cta-subtitle">
              Создайте собственного Telegram-бота и автоматизируйте запись клиентов. 14 дней бесплатного доступа без привязки банковской карты.
            </p>
            <div className="final-cta-actions">
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
                <span>Открыть демо-бот</span>
              </a>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
};
