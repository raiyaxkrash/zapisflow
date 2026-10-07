export function calendarKeys(event, root) {
  if (!event.target.classList.contains('calendar-day')) return;
  const days = [...root.querySelectorAll('.calendar-day')];
  const current = days.indexOf(event.target);
  if (current < 0) return;
  const delta = {ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7}[event.key];
  let index;
  let direction;
  if (event.key === 'Home') { index = 0; direction = 1; }
  else if (event.key === 'End') { index = days.length - 1; direction = -1; }
  else if (delta) { index = current + delta; direction = Math.sign(delta); }
  else return;
  event.preventDefault();
  while (days[index]?.disabled) index += direction;
  days[index]?.focus();
}

export function scrollToPreview(element, win = window) {
  const reduced = win.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  element?.scrollIntoView?.({behavior: reduced ? 'auto' : 'smooth', block: 'start'});
}
