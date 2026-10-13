export const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const rub = value => new Intl.NumberFormat('ru-RU', {style:'currency', currency:'RUB', maximumFractionDigits:2}).format(Number(value));
export const canManage = ctx => Boolean(ctx.capabilities.can_manage);
export function button(label, action, id = '', className = 'primary') {
  return `<button type="button" class="${className}" data-action="${action}" data-id="${escape(id)}">${escape(label)}</button>`;
}
export function field(label, name, value = '', type = 'text', extra = '') {
  return `<label class="field">${escape(label)}<input name="${name}" type="${type}" value="${escape(value)}" ${extra}></label>`;
}
export function header(sub, title) { return `<p class="greeting">${escape(sub)}</p><h2>${escape(title)}</h2>`; }
export function bookingCard(a, manager = false) {
  return `<article class="summary"><span class="pill">${escape(a.status_label)}</span><h3>${escape(a.service)}</h3><p>${escape(a.start_time.slice(0,10))} · ${escape(a.start_time.slice(11,16))} · ${escape(a.staff)}</p>${manager ? `<p>${escape(a.client)} · ${escape(a.phone)}</p>` : ''}<div class="summary-row"><span>Стоимость</span><strong>${rub(a.price)}</strong></div>${a.payment.length ? `<p class="sub">Оплата: ${escape(a.payment[0].status)}</p>` : ''}</article>`;
}
