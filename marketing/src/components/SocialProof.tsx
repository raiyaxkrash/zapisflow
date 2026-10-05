import React from 'react';
import { SOCIAL_PROOF_BADGES } from '../data/content.ts';

export const SocialProof: React.FC = () => {
  return (
    <section className="social-proof-section" aria-label="Преимущества архитектуры ZapisFlow">
      <div className="container">
        <div className="proof-grid">
          {SOCIAL_PROOF_BADGES.map((badge, idx) => (
            <div key={idx} className="proof-card">
              <div className="proof-header">
                <span className="proof-bullet"></span>
                <span className="proof-label">{badge.label}</span>
              </div>
              <p className="proof-desc">{badge.desc}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
};
