import React from 'react';
import { Header } from './components/Header.tsx';
import { Hero } from './components/Hero.tsx';
import { SocialProof } from './components/SocialProof.tsx';
import { HowItWorks } from './components/HowItWorks.tsx';
import { Features } from './components/Features.tsx';
import { BotVsMiniApp } from './components/BotVsMiniApp.tsx';
import { ForWhom } from './components/ForWhom.tsx';
import { PainSolution } from './components/PainSolution.tsx';
import { Pricing } from './components/Pricing.tsx';
import { FAQ } from './components/FAQ.tsx';
import { FinalCTA } from './components/FinalCTA.tsx';
import { Footer } from './components/Footer.tsx';

export const App: React.FC = () => {
  // Retired public booking links must not silently become a booking screen.
  const path = window.location.pathname;
  if (path === '/book' || path.startsWith('/book/') || path === '/account/bookings') {
    return (
      <div className="site-wrapper">
        <Header />
        <main id="main-content" className="container section">
          <h1>Запись через сайт больше недоступна</h1>
          <p>Записаться к мастеру можно в его Telegram-боте или Mini App.</p>
          <a className="btn btn-primary" href="/">На главную ZapisFlow</a>
        </main>
        <Footer />
      </div>
    );
  }
  return (
    <div className="site-wrapper">
      <Header />
      <main id="main-content">
        <Hero />
        <SocialProof />
        <HowItWorks />
        <Features />
        <BotVsMiniApp />
        <ForWhom />
        <PainSolution />
        <Pricing />
        <FAQ />
        <FinalCTA />
      </main>
      <Footer />
    </div>
  );
};

export default App;
