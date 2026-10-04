import React from 'react';
import { EXTERNAL_LINKS } from '../data/content.ts';

export const Footer: React.FC = () => {
  const currentYear = new Date().getFullYear();

  return (
    <footer className="site-footer">
      <div className="container footer-inner">
        {/* Brand info */}
        <div className="footer-brand-col">
          <a href="#" className="brand-logo footer-logo" aria-label="ZapisFlow">
            <div className="logo-icon">
              <svg width="22" height="22" viewBox="0 0 32 32" fill="none">
                <rect width="32" height="32" rx="8" fill="currentColor"/>
                <path d="M9 10H23L13 21H23" stroke="#FFFFFF" strokeWidth="2.8" strokeLinecap="round" strokeLinejoin="round"/>
                <circle cx="23" cy="22" r="2.2" fill="#38BDF8"/>
              </svg>
            </div>
            <span className="brand-name">ZapisFlow</span>
          </a>
          <p className="footer-tagline">
            Telegram-first SaaS платформа для онлайн-записи клиентов частных мастеров и студий красоты.
          </p>
        </div>

        {/* Navigation links */}
        <div className="footer-nav-col">
          <div className="footer-col-title">Навигация</div>
          <ul className="footer-links">
            <li><a href="#features">Возможности</a></li>
            <li><a href="#how-it-works">Как работает</a></li>
            <li><a href="#for-whom">Для кого</a></li>
            <li><a href="#pricing">Тариф</a></li>
            <li><a href="#faq">FAQ</a></li>
          </ul>
        </div>

        {/* Telegram links */}
        <div className="footer-nav-col">
          <div className="footer-col-title">Telegram</div>
          <ul className="footer-links">
            <li>
              <a href={EXTERNAL_LINKS.demoBot} target="_blank" rel="noopener noreferrer">
                Демо-бот (@zapisflowsbot)
              </a>
            </li>
            <li>
              <a href={EXTERNAL_LINKS.support} target="_blank" rel="noopener noreferrer">
                Поддержка (@zapisflow)
              </a>
            </li>
            <li>
              <a href={EXTERNAL_LINKS.createBot} target="_blank" rel="noopener noreferrer">
                Создать бота
              </a>
            </li>
          </ul>
        </div>
      </div>

      <div className="container footer-bottom">
        <p className="copyright">
          © {currentYear} ZapisFlow. Все права защищены.
        </p>
        <div className="footer-legal">
          <span>Безопасные платежи через ЮKassa</span>
          <span className="footer-dot">•</span>
          <span>Официальный Telegram Bot API</span>
        </div>
      </div>
    </footer>
  );
};
