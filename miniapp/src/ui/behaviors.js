export function calendarKeys(event,root) {
  if(!event.target.classList.contains('calendar-day'))return;
  const delta={ArrowLeft:-1,ArrowRight:1,ArrowUp:-7,ArrowDown:7}[event.key];if(!delta)return;
  event.preventDefault();const days=[...root.querySelectorAll('.calendar-day')];let index=days.indexOf(event.target)+delta;
  while(days[index]?.disabled)index+=Math.sign(delta);days[index]?.focus();
}
