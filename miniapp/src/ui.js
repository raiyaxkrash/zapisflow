import { Button, PageHeader, EmptyState } from "./ui/primitives.js";
import { escape } from "./ui/escape.js";
export { escape };
export const rub = (value) =>
  new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: 2,
  }).format(Number(value));
export const canManage = (ctx) => Boolean(ctx.capabilities.can_manage);
export function button(label, action, id = "", className = "primary") {
  return Button({label,action,id,variant:className});
}
export function field(label, name, value = "", type = "text", extra = "") {
  return `<label class="field">${escape(label)}<input name="${name}" type="${type}" value="${escape(value)}" ${extra}></label>`;
}
export function header(sub, title) {
  return PageHeader({title,subtitle:sub});
}
export function bookingCard(a, manager = false) {
  return `<article class="summary"><span class="pill">${escape(a.status_label)}</span><h3>${escape(a.service)}</h3><p>${escape(a.start_time.slice(8, 10) + "." + a.start_time.slice(5, 7) + "." + a.start_time.slice(0, 4))} · ${escape(a.start_time.slice(11, 16))} · ${escape(a.staff)}</p>${manager ? `<p>${escape(a.client)} · ${escape(a.phone)}</p>` : ""}<div class="summary-row"><span>Стоимость</span><strong>${rub(a.price)}</strong></div>${a.payment.length ? `<p class="sub">Оплата: ${escape(paymentLabel(a.payment[0].status))}</p>` : ""}</article>`;
}

import { icon } from "./ui/icons.js";
export { icon };
import { colorTokens } from "./theme/accent.js";
export { colorTokens };
export function applyBrand(brand, element = document.documentElement) {
  for (const [key, value] of Object.entries(colorTokens(brand?.accent_color)))
    element.style.setProperty(key, value);
  element.dataset.appearance = brand?.appearance_preset || "clean";
}
export const paymentLabel = (status) =>
  ({
    PENDING: "Ожидает предоплаты",
    SUBMITTED: "Чек на проверке",
    CONFIRMED: "Оплата подтверждена",
    REJECTED: "Нужен новый чек",
    EXPIRED: "Срок оплаты истёк",
    CANCELLED: "Оплата отменена",
  })[status] || "Уточните у мастера";
export function bookingProgress(page) {
  const active = { services: 0, staff: 1, time: 2, confirm: 3 }[page];
  if (active === undefined) return "";
  return `<ol class="booking-progress" aria-label="Этапы записи">${["Услуга", "Мастер", "Время", "Подтверждение"].map((label, i) => `<li ${i === active ? 'aria-current="step"' : ""}>${i + 1}. ${label}</li>`).join("")}</ol>`;
}

export async function brandingEditor(...args) {
  return (await import("./branding/editor.js")).editor(...args);
}

export function empty(text) {
  return EmptyState({title:text});
}
export function serviceRows(rows, action = "choose-service") {
  return rows.length
    ? rows
        .map(
          (s) =>
            `<button class="service" data-action="${action}" data-id="${s.id}"><span class="service-icon">${icon("scissors")}</span><span class="detail"><strong>${e(s.title)}</strong><small>${s.duration_min} минут${s.description ? " · " + e(s.description) : ""}</small></span><span class="price">${rub(s.price)}<small>›</small></span></button>`,
        )
        .join("")
    : empty("Услуг пока нет");
}
export function contacts(ctx) {
  const labels = {
    studio_phone: "Телефон",
    whatsapp_phone: "WhatsApp",
    studio_address: "Адрес",
    working_hours_text: "Режим работы",
    telegram_username: "Telegram",
    vk_profile: "ВКонтакте",
  };
  return (
    header(ctx.project.name, "Будем на связи") +
    `<p>${e(ctx.contacts.contacts_intro_text || "")}</p><div class="summary">${Object.entries(
      labels,
    )
      .filter(([k]) => ctx.contacts[k])
      .map(
        ([k, l]) =>
          `<div class="summary-row"><span>${l}</span><strong>${e(ctx.contacts[k])}</strong></div>`,
      )
      .join("")}</div><p>${e(ctx.project.about || "")}</p>`
  );
}

export async function settingsEditor(page, data, projectName) {
  return (await import("./master/settings.js")).settingsForm(
    page,
    data,
    projectName,
  );
}

export async function serviceEditor(...args) {
  return (await import("./master/views.js")).serviceEditor(...args);
}

const e = escape,
  f = field,
  b = button;

export async function settingsMenu(...args) {
  return (await import("./master/views.js")).settingsMenu(...args);
}

export async function staffEditor(...args) {
  return (await import("./master/views.js")).staffEditor(...args);
}

export async function manualEditor(...args) {
  return (await import("./master/views.js")).manualEditor(...args);
}

export function reviewsView(rows, projectName) {
  return (
    header(projectName, "Отзывы") +
    (rows.length
      ? rows
          .map(
            (r) =>
              `<article class="summary"><p aria-label="Оценка ${r.rating} из 5">${r.rating} / 5</p><p>${e(r.comment || "Без комментария")}</p><small>${e(r.date)}</small></article>`,
          )
          .join("")
      : empty("Отзывов пока нет. Они появятся после визитов."))
  );
}

export function portfolioView(rows, projectName) {
  return (
    header(projectName, "Наши работы") +
    (rows.length
      ? `<div class="gallery">${rows.map((r) => `<figure><img data-private-image="${e(r.image_url.replace(/^\/api\/miniapp/, ""))}" loading="lazy" alt="${e(r.title || "Работа мастера")}"><figcaption>${e(r.caption || r.title || "")}</figcaption></figure>`).join("")}</div>`
      : empty(
          "Мастер пока не добавил работы. Посмотрите услуги или свяжитесь с нами.",
        ))
  );
}

export async function setupChecklist(...args) {
  return (await import("./master/views.js")).setupChecklist(...args);
}

export function staffRows(rows) {
  return rows
    .map(
      (s) =>
        `<button type="button" class="person" data-action="staff" data-id="${s.id}"><span class="avatar" aria-hidden="true">${e((s.display_name || "?").slice(0, 1))}</span><span class="detail"><strong>${e(s.display_name)}</strong>${s.specialization ? `<small>${e(s.specialization)}</small>` : ""}</span><span aria-hidden="true">›</span></button>`,
    )
    .join("");
}

export async function masterWorkspace(...args){return (await import("./master/render.js")).renderMaster(...args);}

export const notice=text=>`<div class="notice">${escape(text)}</div>`;

export async function readBrandDraft(...args){return (await import("./branding/editor.js")).draft(...args);}
export async function updateBrandPreview(...args){return (await import("./branding/editor.js")).renderPreview(...args);}
