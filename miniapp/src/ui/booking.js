import { Button } from './primitives.js';
export { calendarView as Calendar } from '../calendar.js';
export { bookingSummary as BookingSummary } from '../application/shell.js';
// DTOs contain server-authoritative price, duration, status and availability.
import { serviceRows, staffRows, bookingCard } from '../ui.js';
export const ServiceCard = service => serviceRows([service]);
export const StaffCard = staff => staffRows([staff]);
export const AppointmentCard = bookingCard;
export const SlotPicker = slots => `<div class="slots" role="group" aria-label="Свободное время">${slots.map(slot=>Button({label:slot.label,action:'slot',id:slot.value,variant:'secondary',disabled:!slot.available})).join('')}</div>`;
