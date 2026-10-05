import React, { useState } from 'react';

export const BotVsMiniApp: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'both' | 'bot' | 'miniapp'>('both');

  return (
    <section className="section dual-modes-section" aria-labelledby="modes-heading">
      <div className="container">
        <div className="section-header">
          <span className="badge">Свобода выбора</span>
          <h2 id="modes-heading">Два способа записи. Один ZapisFlow.</h2>
          <p>
            Вы сами решаете, какой интерфейс предложить клиентам. Переключайте классический диалог и Mini App в настройках вашего бота в любой момент.
          </p>

          {/* Mode Switcher Tabs */}
          <div className="mode-toggle-group" role="tablist" aria-label="Переключение режимов">
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === 'both'}
              className={`mode-tab-btn ${activeTab === 'both' ? 'active' : ''}`}
              onClick={() => setActiveTab('both')}
            >
              Сравнение рядом
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === 'bot'}
              className={`mode-tab-btn ${activeTab === 'bot' ? 'active' : ''}`}
              onClick={() => setActiveTab('bot')}
            >
              Классический бот
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === 'miniapp'}
              className={`mode-tab-btn ${activeTab === 'miniapp' ? 'active' : ''}`}
              onClick={() => setActiveTab('miniapp')}
            >
              Telegram Mini App
            </button>
          </div>
        </div>

        <div className="modes-comparison-grid">
          {/* Mode 1: Classic Telegram Bot */}
          {(activeTab === 'both' || activeTab === 'bot') && (
            <div className="mode-card mode-bot-card">
              <div className="mode-card-header">
                <div className="mode-tag bot-tag">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <rect x="3" y="11" width="18" height="10" rx="2"></rect>
                    <circle cx="12" cy="5" r="2"></circle>
                    <path d="M12 7v4"></path>
                    <line x1="8" y1="16" x2="8" y2="16"></line>
                    <line x1="16" y1="16" x2="16" y2="16"></line>
                  </svg>
                  <span>Классический Telegram-бот</span>
                </div>
                <h3 className="mode-title">Привычный чат и быстрые кнопки</h3>
                <p className="mode-desc">
                  Идеально для клиентов, которые ценят максимальную простоту и скорость: пошаговый диалог без лишних деталей.
                </p>
              </div>

              {/* Bot Preview Mockup */}
              <div className="mode-preview-box bot-preview">
                <div className="chat-bubble-sample sample-bot">
                  <span className="sample-sender">Бот:</span> Выберите мастера:
                </div>
                <div className="chat-buttons-sample">
                  <span className="sample-btn">Анна (Топ-мастер)</span>
                  <span className="sample-btn">Ольга (Стилист)</span>
                </div>
                <div className="chat-bubble-sample sample-user">
                  <span className="sample-sender">Вы:</span> Анна
                </div>
                <div className="chat-bubble-sample sample-bot">
                  <span className="sample-sender">Бот:</span> Свободные дни на этой неделе:
                </div>
                <div className="chat-buttons-sample date-chips">
                  <span className="sample-chip">Пт, 16 окт</span>
                  <span className="sample-chip">Сб, 17 окт</span>
                </div>
              </div>

              {/* Features list */}
              <ul className="mode-bullets">
                <li>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#1D72FE" strokeWidth="2.5">
                    <polyline points="20 6 9 17 4 12"></polyline>
                  </svg>
                  <span>Привычный текстовый интерфейс и мгновенный отклик</span>
                </li>
                <li>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#1D72FE" strokeWidth="2.5">
                    <polyline points="20 6 9 17 4 12"></polyline>
                  </svg>
                  <span>Удобные Inline-клавиатуры для выбора процедур</span>
                </li>
                <li>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#1D72FE" strokeWidth="2.5">
                    <polyline points="20 6 9 17 4 12"></polyline>
                  </svg>
                  <span>Работает стабильно даже при слабом мобильном интернете</span>
                </li>
              </ul>
            </div>
          )}

          {/* Mode 2: Telegram Mini App */}
          {(activeTab === 'both' || activeTab === 'miniapp') && (
            <div className="mode-card mode-miniapp-card">
              <div className="mode-card-header">
                <div className="mode-tag miniapp-tag">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <rect x="5" y="2" width="14" height="20" rx="2" ry="2"></rect>
                    <line x1="12" y1="18" x2="12.01" y2="18"></line>
                  </svg>
                  <span>Telegram Mini App</span>
                </div>
                <h3 className="mode-title">Визуальный календарь и карточки</h3>
                <p className="mode-desc">
                  Для тех, кто хочет предоставить клиенту опыт современного мобильного приложения прямо внутри мессенджера.
                </p>
              </div>

              {/* Mini App Preview Mockup */}
              <div className="mode-preview-box miniapp-preview">
                <div className="miniapp-bar-mock">
                  <span className="dot dot-red"></span>
                  <span className="dot dot-yellow"></span>
                  <span className="dot dot-green"></span>
                  <span className="miniapp-mock-title">Запись к мастеру</span>
                </div>
                <div className="miniapp-card-sample">
                  <div className="service-sample-row">
                    <div>
                      <div className="service-sample-title">Сложное окрашивание</div>
                      <div className="service-sample-sub">3 ч • Материалы включены</div>
                    </div>
                    <span className="service-sample-price">5 500 ₽</span>
                  </div>
                  <div className="miniapp-calendar-mini">
                    <div className="cal-mini-header">Октябрь 2026</div>
                    <div className="cal-mini-days">
                      <span className="cal-m-day active">16 Пт</span>
                      <span className="cal-m-day">17 Сб</span>
                      <span className="cal-m-day">19 Пн</span>
                      <span className="cal-m-day">20 Вт</span>
                    </div>
                  </div>
                </div>
              </div>

              {/* Features list */}
              <ul className="mode-bullets">
                <li>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#0D9488" strokeWidth="2.5">
                    <polyline points="20 6 9 17 4 12"></polyline>
                  </svg>
                  <span>Наглядный интерактивный календарь с доступными окнами</span>
                </li>
                <li>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#0D9488" strokeWidth="2.5">
                    <polyline points="20 6 9 17 4 12"></polyline>
                  </svg>
                  <span>Красивые карточки услуг с описаниями и длительностью</span>
                </li>
                <li>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#0D9488" strokeWidth="2.5">
                    <polyline points="20 6 9 17 4 12"></polyline>
                  </svg>
                  <span>Включается или отключается владельцем в 1 клик</span>
                </li>
              </ul>
            </div>
          )}
        </div>
      </div>
    </section>
  );
};
