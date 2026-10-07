import { escape as e } from './ui.js';

export const monthKey = date => date.slice(0, 7);
export function shiftMonth(key, offset) {
  const [year, month] = key.split('-').map(Number);
  const next = new Date(Date.UTC(year, month - 1 + offset, 1));
  return next.toISOString().slice(0, 7);
}
export function monthQuery(key) {
  const [year, month] = key.split('-').map(Number);
  return `year=${year}&month=${month}`;
}
export function calendarView({year, month, days, today, min_date='2000-01-01', max_date='2100-12-31'},
                             {selected, action='calendar-date', monthAction='calendar-month', master=false, loading=false}={}) {
  if (loading) return `<section class="calendar" aria-label="Календарь" aria-busy="true"><p role="status">Загружаем даты…</p></section>`;
  const key = `${year}-${String(month).padStart(2,'0')}`;
  const title = new Intl.DateTimeFormat('ru-RU', {month:'long',year:'numeric',timeZone:'UTC'}).format(new Date(Date.UTC(year,month-1,1)));
  const padding = (new Date(Date.UTC(year,month-1,1)).getUTCDay()+6)%7;
  const prev = shiftMonth(key,-1), next = shiftMonth(key,1);
  const label = {past:'Прошедшая дата',horizon:'За пределами горизонта записи',no_slots:'Нет свободного времени'};
  return `<section class="calendar" aria-label="Календарь"><div class="calendar-heading">
    <button type="button" data-action="${monthAction}" data-id="${prev}" aria-label="Предыдущий месяц" ${prev<monthKey(min_date)?'disabled':''}>‹</button>
    <h2>${e(title)}</h2><button type="button" data-action="${monthAction}" data-id="${next}" aria-label="Следующий месяц" ${next>monthKey(max_date)?'disabled':''}>›</button></div>
    <div class="calendar-weekdays" aria-hidden="true">${['Пн','Вт','Ср','Чт','Пт','Сб','Вс'].map(day=>`<span>${day}</span>`).join('')}</div>
    <div class="calendar-grid">${'<span class="calendar-blank"></span>'.repeat(padding)}${days.map(day=>{
      const blocked = !master && !day.available;
      const state = master ? ({weekly:'По расписанию',day_off:'Выходной',custom:'Особое расписание'}[day.mode] || 'По расписанию') : day.available?'Есть свободное время':label[day.reason] || 'Недоступно';
      const count = day.appointment_count ? `, записей: ${day.appointment_count}` : '';
      return `<button type="button" class="calendar-day ${day.date===selected?'is-selected':''} ${day.date===today?'is-today':''} ${master?`state-${day.mode}`:''}" data-action="${action}" data-id="${e(day.date)}" data-state="${e(day.reason || (day.available ? 'available' : 'unavailable'))}" ${day.date===today?'aria-current="date"':''} aria-label="${e((day.date===today?'Сегодня, ':'')+day.date+', '+state+count)}" aria-pressed="${day.date===selected}" title="${e(state+count)}" ${blocked?'disabled':''}><span>${Number(day.date.slice(-2))}</span>${master&&day.appointment_count?'<i aria-hidden="true" class="calendar-dot"></i>':''}</button>`;
    }).join('')}</div>${master?'<p class="calendar-legend"><span>○ По расписанию</span><span>— Выходной</span><span>◇ Особое расписание</span><span>● Есть записи</span></p>':''}</section>`;
}
