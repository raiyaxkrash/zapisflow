const CLIENT = [['home','Главная','home'],['services','Записаться','calendar'],['bookings','Мои записи','calendar'],['more','Ещё','more']];
const MASTER = [['dashboard','Сегодня','home'],['master-calendar','Календарь','calendar'],['clients','Клиенты','users'],['settings','Ещё','more']];
const sections = {staff:'services',time:'services',confirm:'services',payment:'bookings','client-detail':'bookings',success:'bookings',about:'more',contact:'more',portfolio:'more',reviews:'more',appointment:'dashboard',client:'clients','manage-services':'settings',team:'settings',schedule:'settings','schedule-weekly':'settings','schedule-dates':'settings',branding:'settings','booking-settings':'settings','free-windows':'dashboard','master-portfolio':'settings',broadcasts:'settings',analytics:'settings',payments:'settings',requisites:'settings','contacts-edit':'settings',manual:'settings'};
export function destinations(mode, capabilities = {}) {
  if (mode === 'master') return capabilities.can_manage ? MASTER.filter(([id]) => capabilities.can_edit_project || !['clients','settings'].includes(id)) : [];
  return CLIENT;
}
export const activeDestination = screen => sections[screen] || screen;
export function backDestination(screen, mode, showStaff = true) {
  return ({payments:'settings',analytics:'settings',broadcasts:'settings','master-portfolio':'settings','free-windows':'master-calendar',staff:'services',time:showStaff?'staff':'services',confirm:'time','client-detail':'bookings',about:'more',contact:'more',portfolio:'more',reviews:'more','manage-services':'settings',team:'settings',schedule:'settings','schedule-dates':'schedule','schedule-weekly':'schedule',branding:'settings',client:'clients'})[screen] || (mode === 'master' ? 'dashboard' : 'home');
}
export function createNavigation() {
  let revision = 0, last = ['home'];
  return { begin(screen, id) { last = [screen, id]; return ++revision; }, current(serial) { return serial === revision; }, retry() { return [...last]; } };
}
