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
