import React from 'react';
import { PAIN_SOLUTION_DATA } from '../data/content.ts';

export const PainSolution: React.FC = () => {
  return (
    <section className="section pain-solution-section" aria-labelledby="pain-solution-heading">
      <div className="container">
        <div className="section-header">
          <span className="badge">Сравнение</span>
          <h2 id="pain-solution-heading">Забудьте про хаос в блокнотах и переписках</h2>
          <p>
            Посмотрите, как меняется рабочий день мастера после подключения бота ZapisFlow.
          </p>
        </div>

        <div className="pain-solution-cards">
          {PAIN_SOLUTION_DATA.map((item) => (
            <div key={item.id} className="contrast-card">
              {/* Left Column: Pain */}
              <div className="contrast-column pain-col">
                <div className="contrast-header">
                  <div className="status-cross">✕</div>
                  <span className="contrast-label">Было: без автоматизации</span>
                </div>
                <h3 className="contrast-title">{item.pain}</h3>
                <p className="contrast-text">{item.painDetail}</p>
              </div>

              {/* Right Column: Solution */}
              <div className="contrast-column solution-col">
                <div className="contrast-header">
                  <div className="status-check">✓</div>
                  <span className="contrast-label">Стало: с ZapisFlow</span>
                </div>
                <h3 className="contrast-title">{item.solution}</h3>
                <p className="contrast-text">{item.solutionDetail}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
};
