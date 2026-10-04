import React from 'react';
import { EXTERNAL_LINKS } from '../data/content.ts';

export const Pricing: React.FC = () => {
  const inclusions = [
    'Собственный Telegram-бот под вашим брендом',
    'Онлайн-запись клиентов 24/7 без ограничений',
    'Гибкое расписание, рабочие дни и перерывы',
    'Подключение команды и нескольких сотрудников',
    'CRM-база клиентов с историей всех визитов',
    'Автоматические напоминания клиентам о записи',
    'Поддержка Telegram Mini App с календарем',
    'Приём подтверждений предоплаты',
    'Сегменты клиентов и рассылки',
    'Техническая поддержка и обновления',
  ];

  return (
    <section id="pricing" className="section pricing-section" aria-labelledby="pricing-heading">
      <div className="container">
        <div className="section-header">
          <span className="badge">Прозрачные условия</span>
          <h2 id="pricing-heading">Один честный тариф без скрытых платежей</h2>
          <p>
            Начните с бесплатного пробного периода. Убедитесь, насколько это удобно вам и вашим клиентам.
          </p>
        </div>

        <div className="pricing-card-wrapper">
          <div className="pricing-card">
            <div className="pricing-card-top">
              <div className="pricing-tier-badge">
                <span className="star-icon">★</span>
                <span>ZapisFlow Basic</span>
              </div>
              <h3 className="pricing-tier-name">Всё включено</h3>
              <p className="pricing-tier-subtitle">
                Полный доступ ко всем возможностям платформы без искусственных ограничений.
              </p>
            </div>

            <div className="pricing-trial-banner">
              <div className="trial-badge">14 дней бесплатно</div>
              <div className="trial-subtext">Попробуйте без риска и привязки банковской карты</div>
            </div>

            <div className="pricing-price-box">
              <div className="price-main">
                <span className="price-amount">499 ₽</span>
                <span className="price-period">/ месяц</span>
              </div>
              <div className="price-note">после окончания 14 дней пробного периода</div>
            </div>

            <div className="pricing-divider"></div>

            <div className="pricing-features-list">
              <div className="list-caption">В стоимость тарифа входит:</div>
              <ul>
                {inclusions.map((item, idx) => (
                  <li key={idx}>
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#1D72FE" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="20 6 9 17 4 12"></polyline>
                    </svg>
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>

            <div className="pricing-cta-box">
              <a
                href={EXTERNAL_LINKS.createBot}
                target="_blank"
                rel="noopener noreferrer"
                className="btn btn-primary btn-lg full-width"
              >
                <span>Попробовать бесплатно</span>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="5" y1="12" x2="19" y2="12"></line>
                  <polyline points="12 5 19 12 12 19"></polyline>
                </svg>
              </a>
              <div className="pricing-guarantee">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect>
                  <path d="M7 11V7a5 5 0 0 1 10 0v4"></path>
                </svg>
                <span>Оплата через официальную ЮKassa • Отмена в любой момент</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
};
