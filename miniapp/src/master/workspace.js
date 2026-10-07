import {
  escape as e,
  button as b,
  field as f,
  rub,
  paymentLabel,
  notice,
} from "../ui.js";
import { EmptyState } from "../ui/primitives.js";
const dayLabel = (v) =>
  new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "long",
    timeZone: "UTC",
  }).format(new Date(v.slice(0, 10) + "T12:00:00Z"));
const live = (a) =>
  ["CONFIRMED", "WAITING_PAYMENT", "PAYMENT_PROOF_SENT"].includes(a.status);
export const title = (name, label) =>
  `<p class="greeting">${e(name)}</p><h2>${e(label)}</h2>`;
export function card(a) {
  return `<article class="master-visit"><button type="button" class="master-visit-main" data-action="appointment" data-id="${a.id}"><span class="master-time">${e(a.start_time.slice(11, 16))}</span><span><strong>${e(a.client || "Клиент")}</strong><small>${e(a.service)} · ${e(a.staff)}</small><span class="pill">${e(a.status_label)}</span>${a.payment?.length ? `<small>${e(paymentLabel(a.payment[0].status))}</small>` : ""}</span><span aria-hidden="true">›</span></button></article>`;
}
export function today(ctx, rows, controls, calendar, date) {
  const active = rows.filter(live);
  const next =
    ctx.server_now && date === ctx.today
      ? active.find(
          (a) => Date.parse(a.start_time) >= Date.parse(ctx.server_now),
        )
      : null;
  const editable = ctx.capabilities.can_edit_project;
  return (
    title(ctx.project.name, date === ctx.today ? "Сегодня" : "Записи дня") +
    controls +
    `<div class="master-metrics"><div><strong>${active.length}</strong><span>активных записей</span></div><div><strong>${rows.filter((a) => a.payment?.some((p) => p.status === "SUBMITTED")).length}</strong><span>чеков за этот день</span></div></div>` +
    (editable
      ? `<div class="master-quick">${b("+ Запись", "nav", "manual")}${b("Проверить оплаты", "nav", "payments", "secondary")}</div>`
      : "") +
    (next ? `<section><h3>Следующая запись</h3>${card(next)}</section>` : "") +
    calendar +
    `<h3>Записи на ${e(dayLabel(date))}</h3>` +
    (rows.length
      ? rows.map(card).join("")
      : EmptyState({
          title: "День свободен",
          description: "Здесь появятся записи на выбранную дату.",
          ...(editable
            ? {
                action: {
                  label: "Добавить запись",
                  action: "nav",
                  id: "manual",
                },
              }
            : {}),
        })) +
    (editable
      ? `<div class="master-quick">${b("Изменить график", "nav", "schedule", "secondary")}${calendar ? b("Свободное время", "nav", "free-windows", "secondary") + b("Особое расписание даты", "day-override", "date", "secondary") : ""}</div>`
      : "") +
    (ctx.capabilities.role === "owner"
      ? `<details class="master-notes"><summary>Перед первой записью</summary><p>✓ Бот подключён · ✓ Mini App доступен</p><p>Проверьте услуги и график — это не блокирует работу.</p>${b("Услуги", "nav", "manage-services", "secondary")}${b("Расписание", "nav", "schedule", "secondary")}</details>`
      : "")
  );
}
export function details(ctx, a) {
  return (
    title("Запись", a.client) +
    `<dl class="master-facts">${[
      ["Услуга", a.service],
      ["Сотрудник", a.staff],
      ["Дата", dayLabel(a.start_time)],
      ["Время", a.start_time.slice(11, 16)],
      ["Стоимость", rub(a.price)],
      ["Статус", a.status_label],
      ["Телефон", a.phone],
      ["Заметки", a.notes],
    ]
      .filter(([, v]) => v)
      .map(([k, v]) => `<div><dt>${e(k)}</dt><dd>${e(v)}</dd></div>`)
      .join("")}</dl>` +
    phoneLink(a.phone) +
    `<div class="master-quick">${a.master_client_id && ctx.capabilities.can_edit_project ? b("Открыть клиента", "client", a.master_client_id, "secondary") : ""}${ctx.capabilities.can_edit_project && a.status === "CONFIRMED" ? b("Завершить визит", "master-complete", a.id) : ""}${ctx.capabilities.can_edit_project && a.cancel_allowed && !a.payment?.some((p) => p.status === "SUBMITTED") ? b("Отменить запись", "master-cancel", a.id, "secondary") : ""}</div>` +
    (a.payment || [])
      .map(
        (p) =>
          `<section class="master-payment"><h3>Предоплата ${rub(p.amount)}</h3><p>${e(paymentLabel(p.status))}</p>${p.proofs.map((proof) => b("Скачать чек", "proof-download", proof.id, "secondary")).join("")}${ctx.capabilities.can_edit_project && p.status === "SUBMITTED" ? `${b("Подтвердить чек", "approve", p.id)}<form id="reject-form" data-id="${p.id}">${f("Причина отклонения", "reason", "", "text", 'required maxlength="255"')}<button class="secondary">Отклонить чек</button></form>` : ""}</section>`,
      )
      .join("")
  );
}
export function phoneLink(phone) {
  return phone && /^\+?[0-9 ()-]{5,30}$/.test(phone)
    ? `<a class="secondary master-phone" href="tel:${e(phone.replace(/[^+0-9]/g, ""))}">Позвонить ${e(phone)}</a>`
    : "";
}
export function clients(ctx, rows, search) {
  return (
    title(ctx.project.name, "Клиенты") +
    `<form id="search-form">${f("Поиск по имени", "search", search || "", "search", 'maxlength="100"')}<button class="secondary">Найти</button></form>` +
    (rows.length
      ? `<div class="master-clients">${rows.map((r) => `<button type="button" class="master-client" data-action="client" data-id="${r.id}"><span class="avatar" aria-hidden="true">${e(r.name.slice(0, 1))}</span><span><strong>${e(r.name)}</strong><small>${e(r.phone || "Телефон не указан")}</small>${r.total_bookings != null ? `<small>Записей: ${r.total_bookings}${r.last_visit ? " · Последний визит " + e(dayLabel(r.last_visit)) : ""}${r.next_visit ? " · Следующая запись " + e(dayLabel(r.next_visit)) : ""}</small>` : ""}</span></button>`).join("")}</div>`
      : EmptyState({
          title: search ? "Клиенты не найдены" : "Пока нет клиентов",
          description: search
            ? "Попробуйте другое имя."
            : "Клиенты появятся после первой записи. Добавление вручную доступно в Telegram-админке.",
        }))
  );
}
export function client(ctx, c) {
  const future = c.history.filter(
    (a) => live(a) && a.start_time.slice(0, 10) >= ctx.today,
  );
  return (
    title("Клиент", c.name) +
    phoneLink(c.phone) +
    `<p>Записей: ${c.total_bookings} · Стоимость визитов: ${rub(c.total_spent)}</p>` +
    b("Создать запись", "manual-client", c.id) +
    `<details class="master-notes"><summary>Заметка о клиенте</summary><p>${e(c.notes || "Заметок пока нет")}</p><form id="notes-form" data-id="${c.id}"><label class="field">Заметки<textarea name="notes" maxlength="2000" rows="4">${e(c.notes || "")}</textarea></label><button class="primary">Сохранить заметку</button></form></details><h3>Будущие записи</h3>` +
    (future.length
      ? future.map(card).join("")
      : EmptyState({ title: "Ближайших записей нет" })) +
    `<h3>История</h3>` +
    c.history
      .filter((a) => !future.includes(a))
      .map(card)
      .join("")
  );
}
export function services(ctx, rows, team) {
  return (
    title(ctx.project.name, "Услуги") +
    b("+ Добавить услугу", "service-edit", "new") +
    (rows.length
      ? rows
          .map(
            (s) =>
              `<article class="master-service"><h3>${e(s.title)}</h3><p>${rub(s.price)} · ${s.duration_min} минут</p><span class="pill">${s.is_active ? "Активна" : "Выключена"}</span><p class="sub">${e(
                team
                  .filter(
                    (t) =>
                      !t.service_ids.length || t.service_ids.includes(s.id),
                  )
                  .map((t) => t.display_name)
                  .join(", ") || "Специалисты не назначены",
              )}</p><div class="master-quick">${b("Редактировать", "service-edit", s.id, "secondary")}${b(s.is_active ? "Выключить" : "Включить", "service-active", s.id, "secondary")}</div></article>`,
          )
          .join("")
      : EmptyState({
          title: "Услуг пока нет",
          description:
            "Добавьте первую услугу, чтобы клиенты могли записываться.",
        }))
  );
}
export function team(ctx, rows, services) {
  return (
    title(ctx.project.name, "Команда") +
    (rows.length
      ? rows
          .map(
            (s) =>
              `<article class="master-service"><div class="master-person"><span class="avatar" aria-hidden="true">${e(s.display_name.slice(0, 1))}</span><h3>${e(s.display_name)}</h3></div>${s.specialization ? `<p>${e(s.specialization)}</p>` : ""}<span class="pill">${s.is_active ? "Активен" : "Неактивен"}</span><p>${e(
                services
                  .filter(
                    (v) =>
                      !s.service_ids.length || s.service_ids.includes(v.id),
                  )
                  .map((v) => v.title)
                  .join(", ") || "Услуг пока нет",
              )}</p><div class="master-quick">${b("Редактировать", "staff-edit", s.id, "secondary")}${b("Расписание", "nav", "schedule-weekly", "secondary").replace('data-id="schedule-weekly"', `data-id="schedule-weekly" data-staff="${s.id}"`)}</div></article>`,
          )
          .join("")
      : EmptyState({
          title: "Нет сотрудников",
          description:
            "Откройте Manager Bot → ваш проект → Команда, чтобы добавить или пригласить сотрудника.",
        })) +
    notice(
      "Добавление и приглашение: Manager Bot → ваш проект → Команда. Приглашение остаётся одноразовым и привязывает сотрудника к этому проекту.",
    )
  );
}
export function payments(ctx, rows, filter = "review") {
  const displayed =
    filter === "all"
      ? rows
      : rows.filter((a) => a.payment.some((p) => p.status === "SUBMITTED"));
  return (
    title(ctx.project.name, "Оплаты") +
    `<div class="master-quick">${b("Ожидают проверки", "payment-filter", "review", filter === "review" ? "primary" : "secondary")}${b("Все", "payment-filter", "all", filter === "all" ? "primary" : "secondary")}</div><p class="sub">Последние 100 записей с предоплатой</p>` +
    (displayed.length
      ? displayed.map(card).join("")
      : EmptyState({
          title: "Нет оплат для проверки",
          description: "Когда клиент отправит чек, он появится здесь.",
        }))
  );
}

export function weeklyEditor(rows, staffId, selected = 0) {
  const days = [
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
  ];
  const row = rows.find((r) => r.weekday === Number(selected)) || {
    weekday: Number(selected),
    is_day_off: true,
    breaks: [],
  };
  return `<div class="master-week">${days
    .map((label, index) => {
      const d = rows.find((r) => r.weekday === index);
      return `<button type="button" data-action="weekly-day" data-id="${index}" aria-pressed="${index === Number(selected)}"><strong>${label}</strong><span>${!d ? "Не настроено" : d.is_day_off ? "Выходной" : e(d.work_start.slice(0, 5)) + "–" + e(d.work_end.slice(0, 5))}</span></button>`;
    })
    .join(
      "",
    )}</div><h3>${days[Number(selected)]}</h3><form id="schedule-form" data-staff="${staffId}"><input type="hidden" name="weekday" value="${selected}"><label class="check"><input type="checkbox" name="is_day_off" ${row.is_day_off ? "checked" : ""}>Выходной</label><fieldset id="weekly-hours" aria-label="Рабочее время" ${row.is_day_off?"hidden disabled":""}>${f("Начало", "work_start", (row.work_start || "10:00").slice(0, 5), "time", "required")}${f("Конец", "work_end", (row.work_end || "19:00").slice(0, 5), "time", "required")}${f("Перерывы (10:30-11:00, 13:00-14:00)", "breaks", (row.breaks || []).map((pair) => pair.map((v) => v.slice(0, 5)).join("-")).join(", "))}</fieldset><button class="primary">Сохранить день</button></form>`;
}

export function telegramAdmin(ctx, label = "Открыть Telegram-админку") {
  return /^https:\/\/t\.me\/[A-Za-z0-9_]+$/.test(ctx.management_chat_url || "")
    ? `<a class="secondary master-phone" href="${e(ctx.management_chat_url)}" rel="noopener noreferrer">${e(label)}</a><p class="sub">В чате бота отправьте /admin.</p>`
    : `<p class="sub">В чате клиентского бота отправьте /admin.</p>`;
}
export function analytics(ctx, data) {
  return (
    title(ctx.project.name, "Аналитика за этот месяц") +
    `<dl class="master-facts">${[
      ["Записи", data.total],
      ["Завершённые визиты", data.completed],
      ["Клиенты", data.unique_clients],
      ["Стоимость завершённых услуг", rub(data.revenue)],
    ]
      .map(
        ([label, value]) => `<div><dt>${label}</dt><dd>${e(value)}</dd></div>`,
      )
      .join(
        "",
      )}</dl><p class="sub">Стоимость завершённых услуг — доменная метрика, а не остаток на банковском счёте.</p><h3>Популярные услуги</h3>` +
    (data.top_services?.length
      ? data.top_services
          .map(
            (s) =>
              `<article class="master-service"><strong>${e(s.title)}</strong><p>Визитов: ${s.count}</p></article>`,
          )
          .join("")
      : EmptyState({ title: "Пока недостаточно данных" }))
  );
}
export function broadcasts(ctx, rows) {
  const labels = {
    DRAFT: "Черновик",
    SENDING: "Отправляется",
    COMPLETED: "Завершена",
    CANCELLED: "Отменена",
  };
  return (
    title(ctx.project.name, "Рассылки") +
    telegramAdmin(ctx, "Новая рассылка в Telegram") +
    notice(
      "Создание и отправка остаются в Telegram-админке. Здесь — последние 50 рассылок.",
    ) +
    (rows.length
      ? rows
          .map(
            (r) =>
              `<article class="master-service"><span class="pill">${e(labels[r.status] || "Уточните статус")}</span><p>${e(r.text)}</p><p class="sub">Получателей: ${r.total_count} · Отправлено: ${r.success_count}</p></article>`,
          )
          .join("")
      : EmptyState({
          title: "Рассылок пока нет",
          description: "Создайте первую рассылку в Telegram-админке.",
        }))
  );
}

export function portfolio(ctx, rows) {
  return (
    title(ctx.project.name, "Портфолио") +
    `<form id="portfolio-form"><label class="field">Добавить работу<input type="file" name="file" accept="image/jpeg,image/png" required></label><p class="sub">JPEG или PNG до 8 МБ. Изображение будет сохранено в Telegram.</p><progress max="100" value="0" hidden></progress><button class="primary">Загрузить</button></form>` +
    (rows.length
      ? `<div class="gallery">${rows.map((r) => `<figure><img data-private-image="${e(r.image_url.replace(/^\/api\/miniapp/, ""))}" alt="${e(r.title || "Работа")}" loading="lazy"><figcaption>${e(r.caption || r.title || "Работа")}${!r.is_active ? "<small>Скрыта</small>" : ""}${b("Удалить", "portfolio-delete", r.id, "secondary")}</figcaption></figure>`).join("")}</div>`
      : EmptyState({
          title: "Портфолио пока пусто",
          description: "Добавьте фото, чтобы показать клиентам ваши работы.",
        }))
  );
}
