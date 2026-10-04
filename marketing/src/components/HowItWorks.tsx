import React from 'react';
import { STEPS_DATA } from '../data/content.ts';

export const HowItWorks: React.FC = () => {
  return (
    <section id="how-it-works" className="section how-it-works-section" aria-labelledby="how-it-works-heading">
      <div className="container">
        <div className="section-header">
          <span className="badge">Быстрый старт</span>
          <h2 id="how-it-works-heading">Как это работает</h2>
          <p>От идеи до первых автоматических записей — всего 4 простых шага без программирования и сложных настроек.</p>
        </div>

        <div className="steps-timeline">
          {STEPS_DATA.map((step) => (
            <div key={step.number} className="step-card">
              <div className="step-header">
                <span className="step-number">{step.number}</span>
                <span className="step-badge">{step.detail}</span>
              </div>
              <h3 className="step-title">{step.title}</h3>
              <p className="step-desc">{step.description}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
};
