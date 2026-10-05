import React, { useState } from 'react';
import { FAQ_DATA, EXTERNAL_LINKS } from '../data/content.ts';

export const FAQ: React.FC = () => {
  const [openItems, setOpenItems] = useState<Record<string, boolean>>({
    'faq-1': true,
    'faq-2': true,
  });

  const toggleItem = (id: string) => {
    setOpenItems((prev) => ({
      ...prev,
      [id]: !prev[id],
    }));
  };

  return (
    <section id="faq" className="section faq-section" aria-labelledby="faq-heading">
      <div className="container">
        <div className="section-header">
          <span className="badge">Вопросы и ответы</span>
          <h2 id="faq-heading">Часто задаваемые вопросы</h2>
          <p>
            Всё, что нужно знать о подключении и работе с платформой ZapisFlow.
          </p>
        </div>

        <div className="faq-accordion-container">
          <div className="faq-accordion" role="region" aria-label="Список частых вопросов">
            {FAQ_DATA.map((item) => {
              const isOpen = !!openItems[item.id];
              return (
                <div key={item.id} className={`faq-item ${isOpen ? 'open' : ''}`}>
                  <button
                    type="button"
                    className="faq-question-btn"
                    onClick={() => toggleItem(item.id)}
                    aria-expanded={isOpen}
                    aria-controls={`faq-answer-${item.id}`}
                  >
                    <span className="faq-question-text">{item.question}</span>
                    <span className="faq-icon-chevron" aria-hidden="true">
                      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="6 9 12 15 18 9"></polyline>
                      </svg>
                    </span>
                  </button>

                  <div
                    id={`faq-answer-${item.id}`}
                    className="faq-answer-collapse"
                    role="region"
                    hidden={!isOpen}
                  >
                    <div className="faq-answer-content">
                      <p>{item.answer}</p>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          <div className="faq-support-card">
            <div className="support-icon">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10"></circle>
                <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"></path>
                <line x1="12" y1="17" x2="12.01" y2="17"></line>
              </svg>
            </div>
            <div className="support-text">
              <h3>Остались вопросы?</h3>
              <p>Напишите нам в Telegram — ответим в течение нескольких минут и поможем настроить первого бота.</p>
            </div>
            <a
              href={EXTERNAL_LINKS.support}
              target="_blank"
              rel="noopener noreferrer"
              className="btn btn-secondary"
            >
              Написать в поддержку
            </a>
          </div>
        </div>
      </div>
    </section>
  );
};
