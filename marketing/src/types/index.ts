export interface NavMenuItem {
  label: string;
  href: string;
}

export interface FeatureItem {
  id: string;
  title: string;
  description: string;
  badge?: string;
  icon: string;
  size?: 'normal' | 'large';
}

export interface StepItem {
  number: string;
  title: string;
  description: string;
  detail: string;
}

export interface ForWhomItem {
  id: string;
  title: string;
  subtitle: string;
  icon: string;
}

export interface PainSolutionItem {
  id: string;
  pain: string;
  painDetail: string;
  solution: string;
  solutionDetail: string;
}

export interface FaqItem {
  id: string;
  question: string;
  answer: string;
}
