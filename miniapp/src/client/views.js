import {
  escape as e,
  rub,
  button as b,
  field as f,
  paymentLabel,
} from "../ui.js";
import { EmptyState } from "../ui/primitives.js";
export const dateLabel = (value) =>
  new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(value.slice(0, 10) + "T12:00:00Z"));
export const timeLabel = (value) => value.slice(11, 16);
const terminal = [
  "COMPLETED",
  "CANCELLED_BY_CLIENT",
  "CANCELLED_BY_ADMIN",
  "EXPIRED",
];
export const upcoming = (rows, today) =>
  rows.filter(
    (a) => a.start_time.slice(0, 10) >= today && !terminal.includes(a.status),
  );
export const serviceCards = (rows) =>
  rows.length
    ? `<div class="client-services">${rows.map((s) => `<article class="client-service"><button type="button" data-action="choose-service" data-id="${s.id}"><span class="service-monogram" aria-hidden="true">${e(s.title.slice(0, 1))}</span><span><strong>${e(s.title)}</strong><small>${s.duration_min} минут</small>${s.description ? `<p>${e(s.description)}</p>` : ""}</span><span class="client-price">${rub(s.price)}</span></button><button type="button" class="nearest-link" data-action="nearest" data-id="${s.id}">Ближайшее свободное время</button></article>`).join("")}</div>`
    : EmptyState({
        title: "Услуг пока нет",
        description:
          "Мастер скоро добавит услуги. Контакты помогут связаться напрямую.",
      });
export function appointmentCard(a) {
  return `<article class="client-appointment"><div class="visit-date"><strong>${e(dateLabel(a.start_time))}</strong><span>${e(timeLabel(a.start_time))}</span></div><h3>${e(a.service)}</h3><p>${e(a.staff)}</p><span class="pill">${e(a.status_label)}</span>${a.payment?.length ? `<p class="sub">${e(paymentLabel(a.payment[0].status))}</p>` : ""}<div class="client-actions">${b("Подробнее", "client-detail", a.id, "secondary")}${!a.policy_agreed && a.status === "WAITING_PAYMENT" ? b("Продолжить подтверждение", "resume", a.id, "secondary") : a.payment?.length && ["WAITING_PAYMENT", "PAYMENT_PROOF_SENT"].includes(a.status) ? b("Предоплата / чек", "payment", a.id, "secondary") : ""}${a.cancel_allowed ? b("Отменить запись", "cancel", a.id, "link") : ""}${a.service_id ? b("Повторить запись", "repeat", a.id, "secondary") : ""}</div></article>`;
}
export function home(ctx, services, visits) {
  const next = upcoming(visits, ctx.today).sort((a, b) =>
    a.start_time.localeCompare(b.start_time),
  )[0];
  const recentIds = [
    ...new Set(
      visits
        .filter((a) => a.status === "COMPLETED")
        .sort((a, b) => b.start_time.localeCompare(a.start_time))
        .map((a) => a.service_id),
    ),
  ];
  const recent = recentIds
    .map((id) => services.find((s) => s.id === id))
    .filter(Boolean)
    .slice(0, 3);
  return `<section class="client-hero">${ctx.branding?.cover_url ? `<img class="client-cover" src="${e(ctx.branding.cover_url)}" alt="" loading="lazy">` : ""}<p class="greeting">Рады вас видеть, ${e(ctx.user.first_name)}</p><h2>${e(ctx.project.name)}</h2>${ctx.branding?.tagline || ctx.contacts.studio_address ? `<p>${e(ctx.branding?.tagline || ctx.contacts.studio_address)}</p>` : ""}${ctx.branding?.welcome_text ? `<p>${e(ctx.branding.welcome_text)}</p>` : ""}${b(ctx.branding?.booking_cta_label || "Записаться", "nav", "services")}</section>${next ? `<section><h3>Ближайшая запись</h3>${appointmentCard(next)}</section>` : ""}${recent.length ? `<section><h3>Недавние услуги</h3>${serviceCards(recent)}</section>` : ""}<section><h3>Услуги</h3>${serviceCards(services)}</section><div class="client-links">${ctx.branding?.show_portfolio !== false ? b("Портфолио", "nav", "portfolio", "secondary") : ""}${ctx.branding?.show_contacts !== false ? b("Контакты", "nav", "contact", "secondary") : ""}</div>`;
}
export function bookings(rows, today) {
  const future = upcoming(rows, today);
  return `<p class="greeting">Ваши визиты</p><h2>Мои записи</h2>${
    rows.length
      ? `<section><h3>Предстоящие</h3>${future.length ? future.map(appointmentCard).join("") : EmptyState({ title: "Ближайших записей нет", action: { label: "Записаться", action: "nav", id: "services" } })}</section><section><h3>Прошедшие и отменённые</h3>${rows
          .filter((a) => !future.includes(a))
          .map(appointmentCard)
          .join("")}</section>`
      : EmptyState({
          title: "У вас пока нет записей",
          description: "Когда вы запишетесь, визиты появятся здесь.",
          action: { label: "Записаться", action: "nav", id: "services" },
        })
  }`;
}
export function details(a, ctx, { confirmation = false } = {}) {
  return `<div class="client-detail"><p class="greeting">${confirmation ? "Ваш выбор" : "Запись"}</p><h2>${confirmation ? "Всё верно?" : e(a.service)}</h2><div class="visit-date"><strong>${e(dateLabel(a.start_time))}</strong><span>${e(timeLabel(a.start_time))}</span></div><dl class="client-facts">${[
    ["Услуга", a.service],
    ["Сотрудник", a.staff],
    ["Стоимость", rub(a.price)],
    ["Длительность", a.duration_min ? `${a.duration_min} минут` : null],
    ["Предоплата", rub(a.deposit)],
    ["Статус", confirmation ? null : a.status_label],
    ["Оплата", a.payment?.length ? paymentLabel(a.payment[0].status) : null],
  ]
    .filter(([, value]) => value !== null)
    .map(
      ([label, value]) => `<div><dt>${e(label)}</dt><dd>${e(value)}</dd></div>`,
    )
    .join(
      "",
    )}</dl></div>${confirmation ? `<form id="confirm-form">${f("Телефон", "phone", ctx.user.phone || "", "tel", 'required autocomplete="tel"')}<label class="check"><input name="policy" type="checkbox" required><span>Согласен с условиями отмены. ${e(ctx.cancel_policy_hours)} ч — срок из настроек студии. Внесённая предоплата при отмене не возвращается.</span></label><button class="primary">${Number(a.deposit) ? "Подтвердить и получить реквизиты" : "Подтвердить запись"}</button></form>` : `<p class="sub">Условия отмены: ${e(ctx.cancel_policy_hours)} ч. Внесённая предоплата не возвращается.</p>${a.cancel_allowed ? b("Отменить запись", "cancel", a.id, "link") : ""}${a.duration_min && a.status === "CONFIRMED" ? b("Добавить в календарь", "calendar-export", a.id, "secondary") : ""}${a.service_id ? b("Повторить запись", "repeat", a.id, "secondary") : ""}${ctx.branding?.show_contacts !== false ? b("Контакты", "nav", "contact", "secondary") : ""}`}`;
}
export function success(a) {
  return `<div class="client-success"><span class="success-mark" aria-hidden="true">✓</span><p class="greeting">Всё готово</p><h2>Вы записаны</h2><div class="visit-date"><strong>${e(dateLabel(a.start_time))}</strong><span>${e(timeLabel(a.start_time))}</span></div><h3>${e(a.service)}</h3><p>${e(a.staff)}</p><strong class="client-price">${rub(a.price)}</strong>${b("Мои записи", "nav", "bookings")}${a.duration_min ? b("Добавить в календарь", "calendar-export", a.id, "secondary") : ""}</div>`;
}
export function more(ctx, theme) {
  return `<h2>Ещё</h2>${[
    ["contact", "Контакты", ctx.branding?.show_contacts],
    ["portfolio", "Портфолио", ctx.branding?.show_portfolio],
    ["reviews", "Отзывы", ctx.branding?.show_reviews],
    ["about", "О бизнесе", true],
  ]
    .filter(([, , show]) => show !== false)
    .map(([id, label]) => b(label, "nav", id, "settings-row"))
    .join("")}<label class="field">Тема<select id="theme">${[
    ["system", "Система"],
    ["light", "Светлая"],
    ["dark", "Тёмная"],
  ]
    .map(
      ([id, label]) =>
        `<option value="${id}"${id === theme ? " selected" : ""}>${label}</option>`,
    )
    .join("")}</select></label>`;
}

export function slots(values, selected) {
  return `<div class="client-slot-groups">${[
    ["Утро", 0, 12],
    ["День", 12, 17],
    ["Вечер", 17, 24],
  ]
    .map(([label, min, max]) => {
      const rows = values.filter(
        (value) =>
          Number(value.slice(11, 13)) >= min &&
          Number(value.slice(11, 13)) < max,
      );
      return rows.length
        ? `<section><h3>${label}</h3><div class="times">${rows.map((value) => `<button type="button" class="slot" data-action="slot" data-id="${e(value)}" aria-pressed="${value === selected}">${e(value.slice(11, 16))}${value === selected ? '<span class="sr-only">Выбрано</span>' : ""}</button>`).join("")}</div></section>`
        : "";
    })
    .join("")}</div>`;
}
export function errorMessage(error) {
  if (error.code === "SLOT_TAKEN")
    return "Это время только что заняли. Выберите другое свободное время.";
  return /[а-яё]/i.test(error.message || "")
    ? error.message.slice(0, 300)
    : "Не удалось выполнить действие. Попробуйте ещё раз.";
}

export function contactPage(ctx) {
  const links = {
    studio_phone: "Телефон",
    whatsapp_phone: "WhatsApp",
    studio_address: "Адрес",
    working_hours_text: "Режим работы",
    telegram_username: "Telegram",
    vk_profile: "ВКонтакте",
  };
  const rows = Object.entries(links)
    .filter(([key]) => ctx.contacts[key])
    .map(([key, label]) => {
      const value = String(ctx.contacts[key]);
      let url = "";
      if (key === "studio_phone" && /^\+?[0-9 ()-]{5,30}$/.test(value))
        url = "tel:" + value.replace(/[^+0-9]/g, "");
      if (key === "whatsapp_phone" && /^\+?[0-9 ()-]{5,30}$/.test(value))
        url = "https://wa.me/" + value.replace(/[^0-9]/g, "");
      if (key === "telegram_username" && /^@?[A-Za-z0-9_]{5,32}$/.test(value))
        url = "https://t.me/" + value.replace(/^@/, "");
      if (key === "vk_profile") {
        try {
          const parsed = new URL(value);
          if (
            parsed.protocol === "https:" &&
            ["vk.com", "www.vk.com", "vk.ru", "www.vk.ru"].includes(
              parsed.hostname,
            )
          )
            url = parsed.href;
        } catch {}
      }
      return `<div class="client-contact"><span>${label}</span>${url ? `<a href="${e(url)}" rel="noopener noreferrer">${e(value)}</a>` : `<strong>${e(value)}</strong>`}</div>`;
    });
  return `<h2>Контакты</h2><p>${e(ctx.contacts.contacts_intro_text || "Будем на связи")}</p>${rows.length ? `<div class="client-contacts">${rows.join("")}</div>` : EmptyState({ title: "Контакты скоро появятся", description: "Мастер ещё не добавил способы связи." })}${ctx.project.about ? `<p>${e(ctx.project.about)}</p>` : ""}`;
}
