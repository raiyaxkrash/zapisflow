import React, { useState, useEffect } from 'react';
import { NAV_LINKS, EXTERNAL_LINKS } from '../data/content.ts';
import { ThemeToggle } from './ThemeToggle.tsx';

export const Header: React.FC = () => {
  const [isScrolled, setIsScrolled] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  useEffect(() => {
    const handleScroll = () => {
      setIsScrolled(window.scrollY > 20);
    };
    window.addEventListener('scroll', handleScroll, { passive: true });
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  const closeMenu = () => setMobileMenuOpen(false);

  return (
    <header className={`site-header ${isScrolled ? 'scrolled' : ''}`}>
      <div className="container header-inner">
        {/* Brand Logo */}
        <a href="#" className="brand-logo" aria-label="ZapisFlow главная">
          <div className="logo-icon">
            <svg width="22" height="22" viewBox="0 0 32 32" fill="none">
              <rect width="32" height="32" rx="8" fill="currentColor"/>
              <path d="M9 10H23L13 21H23" stroke="#FFFFFF" strokeWidth="2.8" strokeLinecap="round" strokeLinejoin="round"/>
              <circle cx="23" cy="22" r="2.2" fill="#38BDF8"/>
            </svg>
          </div>
          <span className="brand-name">ZapisFlow</span>
        </a>

        {/* Desktop Navigation */}
        <nav className="desktop-nav" aria-label="Основная навигация">
          <ul className="nav-list">
            {NAV_LINKS.map((link) => (
              <li key={link.href}>
                <a href={link.href} className="nav-link">
                  {link.label}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        {/* Header Actions */}
        <div className="header-actions">
          <ThemeToggle />
          <a
            href={EXTERNAL_LINKS.createBot}
            target="_blank"
            rel="noopener noreferrer"
            className="btn btn-primary header-cta"
          >
            <span>Создать бота</span>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="5" y1="12" x2="19" y2="12"></line>
              <polyline points="12 5 19 12 12 19"></polyline>
            </svg>
          </a>

          {/* Mobile Menu Hamburger */}
          <button
            type="button"
            className="mobile-menu-btn"
            onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
            aria-expanded={mobileMenuOpen}
            aria-label={mobileMenuOpen ? 'Закрыть меню' : 'Открыть меню'}
          >
            <span className={`hamburger-bar ${mobileMenuOpen ? 'open' : ''}`}></span>
          </button>
        </div>
      </div>

      {/* Mobile Navigation Drawer */}
      <div className={`mobile-nav-drawer ${mobileMenuOpen ? 'open' : ''}`} aria-hidden={!mobileMenuOpen}>
        <div className="container mobile-nav-content">
          <ul className="mobile-nav-list">
            {NAV_LINKS.map((link) => (
              <li key={link.href}>
                <a href={link.href} className="mobile-nav-link" onClick={closeMenu}>
                  {link.label}
                </a>
              </li>
            ))}
          </ul>
          <div className="mobile-cta-wrapper">
            <a
              href={EXTERNAL_LINKS.createBot}
              target="_blank"
              rel="noopener noreferrer"
              className="btn btn-primary btn-lg full-width"
              onClick={closeMenu}
            >
              Создать бота
            </a>
            <a
              href={EXTERNAL_LINKS.demoBot}
              target="_blank"
              rel="noopener noreferrer"
              className="btn btn-secondary btn-lg full-width"
              onClick={closeMenu}
            >
              Открыть демо
            </a>
          </div>
        </div>
      </div>
    </header>
  );
};
