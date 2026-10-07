import { TopBar, BottomNav, Button } from '../ui/primitives.js';
import { destinations, activeDestination } from './navigation.js';
import { escape as e } from '../ui/escape.js';
export function appShell({content,ctx,mode,screen,theme='system'}) {
  const caps=ctx?.capabilities;
  const modes=caps?.can_manage?`<div class="mode-switch" role="group" aria-label="Режим приложения">${['client','master'].map(id=>Button({label:id==='client'?'Клиент':'Управление',action:'mode',id,variant:mode===id?'primary':'secondary'})).join('')}</div>`:'';
  return `<div class="app-shell">${TopBar({name:ctx?.project.name||'ZapisFlow',logo:ctx?.branding?.logo_url,theme})}${modes}<main class="view app-view" tabindex="-1">${content}${ctx?'<footer class="powered">Работает на ZapisFlow</footer>':''}</main>${ctx?BottomNav({items:destinations(mode,caps),active:activeDestination(screen)}):''}</div>`;
}
export function bookingSummary({service,staff,slot,price}) { return `<div class="booking-summary" aria-label="Ваш выбор">${e(service)} · ${e(price)}${staff?' · '+e(staff):''}${slot?' · '+e(slot):''}</div>`; }
