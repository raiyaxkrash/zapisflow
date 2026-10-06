export const escape = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
export const rub = (value) =>
  new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: 2,
  }).format(Number(value));
export const canManage = (ctx) => Boolean(ctx.capabilities.can_manage);
export function button(label, action, id = "", className = "primary") {
  return `<button type="button" class="${className}" data-action="${action}" data-id="${escape(id)}">${escape(label)}</button>`;
}
export function field(label, name, value = "", type = "text", extra = "") {
  return `<label class="field">${escape(label)}<input name="${name}" type="${type}" value="${escape(value)}" ${extra}></label>`;
}
export function header(sub, title) {
  return `<p class="greeting">${escape(sub)}</p><h2>${escape(title)}</h2>`;
}
export function bookingCard(a, manager = false) {
  return `<article class="summary"><span class="pill">${escape(a.status_label)}</span><h3>${escape(a.service)}</h3><p>${escape(a.start_time.slice(8, 10) + "." + a.start_time.slice(5, 7) + "." + a.start_time.slice(0, 4))} · ${escape(a.start_time.slice(11, 16))} · ${escape(a.staff)}</p>${manager ? `<p>${escape(a.client)} · ${escape(a.phone)}</p>` : ""}<div class="summary-row"><span>Стоимость</span><strong>${rub(a.price)}</strong></div>${a.payment.length ? `<p class="sub">Оплата: ${escape(paymentLabel(a.payment[0].status))}</p>` : ""}</article>`;
}

export function icon(name) {
  const paths = {
    home: "M3 10 12 3 21 10v10H3z M9 20v-7h6v7",
    calendar: "M4 5h16v16H4z M4 10h16 M8 3v4 M16 3v4",
    users:
      "M8 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8 M2 21v-3a6 6 0 0 1 12 0v3 M17 4a4 4 0 0 1 0 8 M18 15a5 5 0 0 1 4 5",
    more: "M4 12h1 M11 12h1 M18 12h1",
    scissors:
      "M4 4 20 20 M4 20 20 4 M6 6a3 3 0 1 0-6 0 3 3 0 0 0 6 0 M6 18a3 3 0 1 0-6 0 3 3 0 0 0 6 0",
  };
  return `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[name] || paths.more}"/></svg>`;
}
export function colorTokens(hex) {
  const value = /^#[a-f\d]{6}$/i.test(hex || "") ? hex : "#1D72FE";
  const rgb = [1, 3, 5].map((i) => parseInt(value.slice(i, i + 2), 16));
  const linear = rgb.map((v) => {
    v /= 255;
    return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  });
  const lum = linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
  const foreground =
    (lum + 0.05) / 0.05 >= 1.05 / (lum + 0.05) ? "#000000" : "#FFFFFF";
  return {
    "--zf-accent": value,
    "--zf-accent-hover": value,
    "--zf-accent-foreground": foreground,
    "--zf-accent-soft": `color-mix(in srgb, ${value} 12%, var(--zf-surface))`,
    "--zf-accent-pressed": value,
  };
}
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

export async function brandingEditor(brand) {
  return (await import("./branding/editor.js")).editor(brand);
}

export function empty(text) {
  return `<div class="empty">${e(text)}</div>`;
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
