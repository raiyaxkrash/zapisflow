import React, { useState } from 'react';

export const HeroVisual: React.FC = () => {
  const [selectedSlot, setSelectedSlot] = useState('13:30');
  const [selectedService, setSelectedService] = useState('Маникюр + гель-лак');

  return (
    <div className="hero-visual-wrapper" aria-label="Интерактивная демонстрация интерфейса ZapisFlow">
      {/* Main Telegram Chat Mockup */}
      <div className="mockup-card chat-card">
        {/* Telegram Header */}
        <div className="tg-header">
          <div className="tg-bot-avatar">
            <span>А</span>
            <span className="online-indicator"></span>
          </div>
          <div className="tg-bot-info">
            <div className="tg-bot-name">
              <span>Студия Анны | Запись</span>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="#1D72FE">
                <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/>
              </svg>
            </div>
            <div className="tg-bot-status">бот для онлайн-записи</div>
          </div>
          <div className="tg-header-menu">
            <span>•••</span>
          </div>
        </div>

        {/* Chat Body */}
        <div className="tg-chat-body">
          <div className="tg-message bot-msg">
            <div className="tg-bubble">
              <p>👋 Добро пожаловать! Выберите услугу для записи к мастеру Анне:</p>
              <div className="tg-timestamp">12:04</div>
            </div>
          </div>

          {/* Interactive Service Buttons */}
          <div className="tg-inline-buttons">
            <button
              type="button"
              className={`tg-btn ${selectedService === 'Маникюр + гель-лак' ? 'active' : ''}`}
              onClick={() => setSelectedService('Маникюр + гель-лак')}
            >
              <span>💅 Маникюр + гель-лак</span>
              <span className="tg-btn-price">1 800 ₽</span>
            </button>
            <button
              type="button"
              className={`tg-btn ${selectedService === 'Smart-педикюр' ? 'active' : ''}`}
              onClick={() => setSelectedService('Smart-педикюр')}
            >
              <span>✨ Smart-педикюр</span>
              <span className="tg-btn-price">2 400 ₽</span>
            </button>
          </div>

          <div className="tg-message user-msg">
            <div className="tg-bubble">
              <p>{selectedService}</p>
              <div className="tg-timestamp">12:05 <span className="read-receipt">✓✓</span></div>
            </div>
          </div>

          <div className="tg-message bot-msg">
            <div className="tg-bubble">
              <p>Отлично! Выберите дату и время или откройте визуальное расписание:</p>
              <div className="tg-timestamp">12:05</div>
            </div>
          </div>

          {/* Telegram WebApp Mini App Trigger */}
          <div className="tg-webapp-trigger">
            <div className="tg-webapp-btn">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect>
                <line x1="16" y1="2" x2="16" y2="6"></line>
                <line x1="8" y1="2" x2="8" y2="6"></line>
                <line x1="3" y1="10" x2="21" y2="10"></line>
              </svg>
              <span>Открыть календарь слотов</span>
            </div>
          </div>
        </div>
      </div>

      {/* Floating Mini App Calendar Mockup */}
      <div className="mockup-card miniapp-card">
        <div className="miniapp-header">
          <div className="miniapp-title-bar">
            <span className="miniapp-badge">Telegram Mini App</span>
            <span className="miniapp-close">✕</span>
          </div>
          <div className="miniapp-service-info">
            <div className="miniapp-service-name">{selectedService}</div>
            <div className="miniapp-service-meta">Мастер Анна • 1 ч 30 мин</div>
          </div>
        </div>

        {/* Calendar View: October 2026 */}
        <div className="calendar-box">
          <div className="calendar-month-row">
            <span className="calendar-month-name">Октябрь 2026</span>
            <div className="calendar-arrows">
              <button type="button" aria-label="Предыдущий месяц" disabled className="cal-arrow">‹</button>
              <button type="button" aria-label="Следующий месяц" className="cal-arrow">›</button>
            </div>
          </div>

          <div className="calendar-grid-weekdays">
            <span>Пн</span>
            <span>Вт</span>
            <span>Ср</span>
            <span>Чт</span>
            <span>Пт</span>
            <span>Сб</span>
            <span>Вс</span>
          </div>

          <div className="calendar-grid-days">
            {/* Week 1 */}
            <span className="day-cell past">28</span>
            <span className="day-cell past">29</span>
            <span className="day-cell past">30</span>
            <span className="day-cell available">1</span>
            <span className="day-cell available">2</span>
            <span className="day-cell unavailable">3</span>
            <span className="day-cell unavailable">4</span>
            {/* Week 2 */}
            <span className="day-cell available">5</span>
            <span className="day-cell available">6</span>
            <span className="day-cell available">7</span>
            <span className="day-cell available">8</span>
            <span className="day-cell available">9</span>
            <span className="day-cell unavailable">10</span>
            <span className="day-cell unavailable">11</span>
            {/* Week 3 */}
            <span className="day-cell available">12</span>
            <span className="day-cell available">13</span>
            <span className="day-cell available">14</span>
            <span className="day-cell available">15</span>
            <span className="day-cell selected">16</span>
            <span className="day-cell unavailable">17</span>
            <span className="day-cell unavailable">18</span>
            {/* Week 4 */}
            <span className="day-cell available">19</span>
            <span className="day-cell available">20</span>
            <span className="day-cell available">21</span>
            <span className="day-cell available">22</span>
            <span className="day-cell available">23</span>
            <span className="day-cell unavailable">24</span>
            <span className="day-cell unavailable">25</span>
          </div>
        </div>

        {/* Available Time Slots */}
        <div className="time-slots-box">
          <div className="time-slots-title">Свободное время на 16 октября:</div>
          <div className="slots-row">
            {['10:30', '13:30', '15:30', '18:00'].map((time) => (
              <button
                key={time}
                type="button"
                className={`slot-chip ${selectedSlot === time ? 'selected' : ''}`}
                onClick={() => setSelectedSlot(time)}
              >
                {time}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Floating Notification Card (Master CRM View) */}
      <div className="mockup-card notification-card">
        <div className="notif-icon">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"></path>
            <path d="M13.73 21a2 2 0 0 1-3.46 0"></path>
          </svg>
        </div>
        <div className="notif-content">
          <div className="notif-header">
            <span className="notif-tag">🔔 Уведомление мастеру</span>
            <span className="notif-status-badge">Подтверждено</span>
          </div>
          <div className="notif-body">
            <strong>Елена С.</strong> записалась на <strong>{selectedService}</strong>
          </div>
          <div className="notif-meta">
            16 октября в {selectedSlot} • Предоплата 500 ₽ внесена
          </div>
        </div>
      </div>
    </div>
  );
};
