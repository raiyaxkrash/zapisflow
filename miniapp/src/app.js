import { appShell, bookingSummary } from './application/shell.js';
import { createNavigation, backDestination } from './application/navigation.js';
import { createTelegramBridge } from './telegram/bridge.js';
import { calendarKeys } from './ui/behaviors.js';
import { Skeleton, ErrorState } from './ui/primitives.js';
import { bindPortfolioMedia } from "./media.js";
import "./style.css";
import { calendarView, monthKey, monthQuery, shiftMonth } from "./calendar.js";
import { Api } from "./api/client.js";
import { bootstrap, botIdFromPath } from "./telegram/bootstrap.js";
import { setupTheme } from "./theme/theme.js";
import {
  escape as e,
  rub,
  button as b,
  field as f,
  header,
  bookingCard,
  canManage,
  icon,
  applyBrand,
  bookingProgress,
  paymentLabel,
  brandingEditor,
  empty,
  serviceRows,
  contacts,
  settingsEditor,
  serviceEditor,
  settingsMenu,
  staffEditor,
  manualEditor,
  reviewsView,
  portfolioView,
  setupChecklist,
  staffRows,
} from "./ui.js";

const api = new Api();
const root = document.getElementById("app");
const navigation = createNavigation();
let telegram, themeListener;
let tg,
  theme,
  ctx,
  services = [],
  team = [],
  chosen,
  selectedStaff = null,
  selectedSlot,
  hold,
  selectedDate,
  screen = "home",
  mode = "client",
  busy = false,
  pendingAction = null;
let brandingDirty = false;
let cleanMedia = () => {};
let clientMonth,
  scheduleMonth,
  scheduleDate,
  scheduleStaff,
  scheduleDay,
  dashboardCalendar = false;
const formValues = (form) => Object.fromEntries(new FormData(form));

function shell(content) {
  cleanMedia();
  applyBrand(ctx?.branding, document.documentElement);
  content =
    bookingProgress(screen) +
    (chosen && ["staff", "time", "confirm"].includes(screen)
      ? bookingSummary({service:chosen.title,price:rub(chosen.price),slot:selectedSlot ? selectedSlot.slice(8,10)+'.'+selectedSlot.slice(5,7)+' '+selectedSlot.slice(11,16) : ''})
      : "") +
    content;
  root.innerHTML = appShell({content,ctx,mode,screen,theme:theme?.get()});
  cleanMedia = bindPortfolioMedia(root, api);
  telegram?.setBackVisible(screen !== 'home' && screen !== 'dashboard');

}
function notice(text) {
  return `<div class="notice">${e(text)}</div>`;
}
async function route(page, id) {
  const serial = navigation.begin(page,id);
  screen = page;
  telegram?.setPrimaryAction(null);
  shell(Skeleton());
  try {
    const content = await render(page, id);
    if (navigation.current(serial)) {
      shell(content);
      root.querySelector("main")?.focus({ preventScroll: true });
    }
  } catch (error) {
    if (navigation.current(serial)) showError(error);
  }
}
function showError(error) {
  shell(
    ErrorState({message:error.message,retry:false}) +
      (error.status === 401
        ? "<p>Закройте Mini App и откройте его снова из бота.</p>"
        : b("Повторить", "retry")) +
      b(
        "На главную",
        "nav",
        mode === "client" ? "home" : "dashboard",
        "secondary",
      ),
  );
}
async function render(page, id) {
  if (page === "home" || page === "services") {
    services = await api.get("/client/services");
    return (
      header(
        `Рады вас видеть, ${ctx.user.first_name}`,
        page === "home" ? "Время для себя" : "Выберите услугу",
      ) +
      (page === "home"
        ? `${ctx.branding?.cover_url ? `<img class="cover" src="${e(ctx.branding.cover_url)}" alt="" loading="lazy">` : ""}<div class="studio-banner"><h3>${e(ctx.project.name)}</h3><p>${e(ctx.branding?.tagline || ctx.contacts.studio_address || "")}</p><p>${e(ctx.branding?.welcome_text || ctx.contacts.working_hours_text || "")}</p>${b(ctx.branding?.booking_cta_label || "Записаться", "nav", "services")}</div><h3>Услуги</h3>`
        : "") +
      (!ctx.can_book
        ? notice("Онлайн-запись сейчас недоступна. Свяжитесь со студией.")
        : "") +
      serviceRows(services)
    );
  }
  if (page === "staff") {
    team = await api.get(`/client/staff?service_id=${chosen.id}`);
    return (
      header(chosen.title, "К кому запишемся?") +
      (team.length
        ? `<div class="options">${b("Любой специалист", "staff", "any", "person")}${staffRows(team)}</div>`
        : empty("Нет доступных специалистов"))
    );
  }
  if (page === "time") {
    clientMonth ||= monthKey(ctx.today);
    const scope = `service_id=${chosen.id}${selectedStaff ? `&staff_id=${selectedStaff}` : ""}`;
    const month = await api.get(
      `/client/availability/calendar?${monthQuery(clientMonth)}&${scope}`,
    );
    const selected = month.days.find((day) => day.date === selectedDate);
    const data = selected?.available
      ? await api.get(`/client/slots?${scope}&target_date=${selectedDate}`)
      : { slots: [] };
    return (
      header(chosen.title, "Выберите дату") +
      calendarView(month, {
        selected: selectedDate,
        action: "client-date",
        monthAction: "client-month",
      }) +
      `<p class="sub">Часовой пояс студии: ${e(ctx.project.timezone)}</p><h3>${selected?.available ? "Свободное время" : "Нажмите на доступную дату"}</h3>` +
      (data.slots.length
        ? `<div class="times">${data.slots.map((slot) => b(slot.slice(11, 16), "slot", slot, "slot")).join("")}</div>`
        : empty("Выберите дату с доступным временем."))
    );
  }
  if (page === "confirm") {
    return (
      header("Время зарезервировано", "Всё верно?") +
      bookingCard(hold) +
      `<form id="confirm-form">${f("Телефон", "phone", ctx.user.phone || "", "tel", 'required autocomplete="tel"')}<label class="check"><input name="policy" type="checkbox" required><span>Согласен с условиями отмены. ${ctx.cancel_policy_hours} ч — срок из настроек студии. Внесённая предоплата при отмене не возвращается.</span></label><button class="primary">${Number(hold.deposit) ? "Подтвердить и получить реквизиты" : "Подтвердить запись"}</button></form>`
    );
  }
  if (page === "payment") {
    const data = await api.get(`/client/appointments/${id}/payment`);
    hold = data.appointment;
    const p = hold.payment[0];
    return (
      header(
        "Предоплата",
        p.status === "SUBMITTED" ? "Чек на проверке" : "Время за вами",
      ) +
      bookingCard(hold) +
      (p.rejection_reason ? notice(p.rejection_reason) : "") +
      `<div class="summary">${Object.entries({
        bank_name: "Банк",
        bank_card_number: "Карта / телефон",
        bank_recipient_name: "Получатель",
      })
        .map(
          ([key, label]) =>
            `<div class="summary-row"><span>${label}</span><strong>${e(data.requisites[key] || "")}</strong></div>`,
        )
        .join("")}</div>` +
      (p.status === "PENDING" || p.status === "REJECTED"
        ? `${notice("Прикрепите фото чека: JPEG или PNG, до 8 МБ. Мастер проверит предоплату.")}<form id="proof-form"><label class="field">Чек<input id="proof-file" name="file" type="file" accept="image/jpeg,image/png" required></label><img id="proof-preview" class="proof-preview" alt="Предпросмотр чека" hidden><progress id="upload-progress" max="100" value="0" hidden></progress><button class="primary">Отправить чек</button></form>`
        : "") +
      b("Мои записи", "nav", "bookings", "secondary")
    );
  }
  if (page === "bookings") {
    const rows = await api.get("/client/appointments");
    const grouped = [
      [
        "Предстоящие",
        rows.filter(
          (a) =>
            a.start_time.slice(0, 10) >= ctx.today &&
            ![
              "COMPLETED",
              "CANCELLED_BY_CLIENT",
              "CANCELLED_BY_ADMIN",
              "EXPIRED",
            ].includes(a.status),
        ),
      ],
      [
        "Прошедшие и отменённые",
        rows.filter(
          (a) =>
            a.start_time.slice(0, 10) < ctx.today ||
            [
              "COMPLETED",
              "CANCELLED_BY_CLIENT",
              "CANCELLED_BY_ADMIN",
              "EXPIRED",
            ].includes(a.status),
        ),
      ],
    ];
    return (
      header("Ваши визиты", "Мои записи") +
      (rows.length
        ? grouped
            .map(
              ([label, items]) =>
                `<h3>${label}</h3>` +
                items
                  .map(
                    (a) =>
                      bookingCard(a) +
                      (!a.policy_agreed && a.status === "WAITING_PAYMENT"
                        ? b(
                            "Продолжить подтверждение",
                            "resume",
                            a.id,
                            "secondary",
                          )
                        : a.payment.length &&
                            ["WAITING_PAYMENT", "PAYMENT_PROOF_SENT"].includes(
                              a.status,
                            )
                          ? b("Предоплата / чек", "payment", a.id, "secondary")
                          : "") +
                      (a.cancel_allowed
                        ? b("Отменить запись", "cancel", a.id, "link")
                        : "") +
                      (a.service_id
                        ? b("Повторить запись", "repeat", a.id, "secondary")
                        : ""),
                  )
                  .join(""),
            )
            .join("")
        : empty("Пока нет записей. Выберите услугу и удобное время."))
    );
  }
  if (page === "contact") return contacts(ctx);
  if (page === "more")
    return (
      header(ctx.project.name, "Ещё") +
      [
        [ctx.branding?.show_contacts !== false, "contact", "Контакты"],
        [ctx.branding?.show_portfolio !== false, "portfolio", "Портфолио"],
        [ctx.branding?.show_reviews !== false, "reviews", "Отзывы"],
      ]
        .filter(([show]) => show)
        .map(([, p, l]) => b(l, "nav", p, "settings-row"))
        .join("")
    );
  if (page === "reviews")
    return reviewsView(await api.get("/client/reviews"), ctx.project.name);
  if (page === "portfolio")
    return portfolioView(await api.get("/client/portfolio"), ctx.project.name);
  if (page === "master-calendar") {
    dashboardCalendar = true;
    screen = "master-calendar";
    return render("dashboard");
  }
  if (page === "success")
    return (
      `<div class="success-mark" aria-hidden="true">✓</div>` +
      header("Всё готово", "Вы записаны") +
      bookingCard(hold) +
      b("Мои записи", "nav", "bookings")
    );
  if (page === "branding") {
    const brand = await api.get("/master/branding");
    brandingDirty = false;
    return brandingEditor(brand);
  }
  if (page === "dashboard") {
    const rows = await api.get(
      `/master/appointments?target_date=${selectedDate}`,
    );
    const day = new Date(`${selectedDate}T12:00:00Z`);
    const controls = `<div class="daily-navigation">${b("‹", "daily-shift", -1, "secondary")}<strong>${e(new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", timeZone: "UTC" }).format(day))}</strong>${b("Выбрать дату", "daily-calendar", "open", "secondary")}${b("›", "daily-shift", 1, "secondary")}</div>`;
    const month = {
      year: day.getUTCFullYear(),
      month: day.getUTCMonth() + 1,
      today: ctx.today,
      days: Array.from(
        {
          length: new Date(
            Date.UTC(day.getUTCFullYear(), day.getUTCMonth() + 1, 0),
          ).getUTCDate(),
        },
        (_, i) => ({
          date: `${monthKey(selectedDate)}-${String(i + 1).padStart(2, "0")}`,
          available: true,
        }),
      ),
    };
    return (
      header(ctx.project.name, "Сегодня и расписание") +
      (await setupChecklist(ctx)) +
      `<p class="sub">Записей на выбранную дату: ${rows.length}</p>` +
      controls +
      (dashboardCalendar
        ? calendarView(month, {
            selected: selectedDate,
            master: true,
            action: "daily-date",
            monthAction: "daily-month",
          })
        : "") +
      (rows.length
        ? rows
            .map(
              (a) =>
                bookingCard(a, true) +
                b("Открыть запись", "appointment", a.id, "secondary"),
            )
            .join("")
        : empty("На эту дату записей нет"))
    );
  }
  if (page === "appointment") {
    const a = await api.get(`/master/appointments/${id}`);
    return (
      header("Карточка записи", a.client) +
      bookingCard(a, true) +
      `<p>${e(a.notes || "")}</p>` +
      a.payment
        .map(
          (p) =>
            `<h3>Предоплата ${rub(p.amount)}</h3><p>${e(paymentLabel(p.status))}</p>` +
            p.proofs
              .map(
                (proof) =>
                  `<a class="secondary proof-link" href="/api/miniapp/proofs/${proof.id}">Скачать чек</a>`,
              )
              .join("") +
            (ctx.capabilities.can_edit_project && p.status === "SUBMITTED"
              ? `${b("Подтвердить чек", "approve", p.id)}<form id="reject-form" data-id="${p.id}">${f("Причина отклонения", "reason", "", "text", 'required maxlength="255"')}<button class="secondary">Отклонить чек</button></form>`
              : ""),
        )
        .join("")
    );
  }
  if (page === "clients") {
    const rows = await api.get(
      `/master/clients?search=${encodeURIComponent(id || "")}`,
    );
    return (
      header(ctx.project.name, "Клиенты") +
      `<form id="search-form">${f("Поиск по имени", "search", id || "")}<button class="secondary">Найти</button></form>` +
      (rows.length
        ? rows
            .map(
              (r) =>
                `<button class="service" data-action="client" data-id="${r.id}"><span class="detail"><strong>${e(r.name)}</strong><small>${e(r.phone || "")}</small></span>›</button>`,
            )
            .join("")
        : empty("Клиенты не найдены"))
    );
  }
  if (page === "client") {
    const card = await api.get(`/master/clients/${id}`);
    return (
      header("Клиент", card.name) +
      `<p>${e(card.phone || "")}</p><p>Визитов: ${card.total_bookings} · ${rub(card.total_spent)}</p><form id="notes-form" data-id="${id}">${f("Заметки", "notes", card.notes || "", "text", 'maxlength="2000"')}<button class="primary">Сохранить заметку</button></form><h3>История</h3>` +
      card.history.map((a) => bookingCard(a, true)).join("")
    );
  }
  if (page === "settings") return settingsMenu(ctx);
  if (["contacts-edit", "requisites", "booking-settings"].includes(page)) {
    return settingsEditor(
      page,
      await api.get("/master/settings"),
      ctx.project.name,
    );
  }
  if (page === "manage-services") {
    services = await api.get("/master/services");
    return (
      header(ctx.project.name, "Услуги") +
      b("+ Добавить услугу", "service-edit", "new") +
      serviceRows(services, "service-edit")
    );
  }
  if (page === "service-edit") {
    const s = services.find((s) => s.id === Number(id)) || {
      title: "",
      price: 0,
      duration_min: 60,
      buffer_min: 15,
      deposit_type: "FIXED",
      deposit_value: 0,
      is_active: true,
    };
    return serviceEditor(s, id);
  }
  if (page === "team") {
    team = await api.get("/master/staff");
    return (
      header(ctx.project.name, "Команда") +
      (team.length
        ? team
            .map(
              (s) =>
                `<button class="settings-row" data-action="staff-edit" data-id="${s.id}"><span>${e(s.display_name)}<small>${s.is_active ? "Активен" : "Неактивен"}</small></span>›</button>`,
            )
            .join("")
        : empty("Нет сотрудников")) +
      notice(
        "Добавление и приглашение сотрудника доступны в Telegram-админке проекта.",
      )
    );
  }
  if (page === "staff-edit") {
    const s = team.find((s) => s.id === Number(id));
    services = await api.get("/master/services");
    return staffEditor(s, services, id);
  }
  if (["schedule", "schedule-weekly", "schedule-dates"].includes(page)) {
    team = await api.get("/master/staff");
    if (!team.length) return empty("Добавьте сотрудника в Telegram-админке");
    const staffId = Number(id) || scheduleStaff || team[0].id;
    scheduleStaff = staffId;
    if (page === "schedule")
      return (
        header(ctx.project.name, "Расписание") +
        b("Еженедельное расписание", "nav", "schedule-weekly", "settings-row") +
        b("Отдельные даты", "nav", "schedule-dates", "settings-row") +
        b("Горизонт записи", "nav", "booking-settings", "settings-row")
      );
    const chooseStaff = `<form id="schedule-staff-form" data-page="${page}"><label class="field">Сотрудник<select name="staff_id">${team.map((row) => `<option value="${row.id}" ${row.id === staffId ? "selected" : ""}>${e(row.display_name)}</option>`).join("")}</select></label><button class="secondary">Показать</button></form>`;
    if (page === "schedule-weekly") {
      const data = await api.get(`/master/schedule?staff_id=${staffId}`);
      const weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
      return (
        header("Расписание", "Еженедельное расписание") +
        chooseStaff +
        data.weekly
          .map(
            (row) =>
              `<p>${weekdays[row.weekday]}: ${row.is_day_off ? "выходной" : `${e(row.work_start)}–${e(row.work_end)}`}</p>`,
          )
          .join("") +
        `<form id="schedule-form" data-staff="${staffId}"><label class="field">День недели<select name="weekday">${weekdays.map((label, i) => `<option value="${i}">${label}</option>`).join("")}</select></label><label class="check"><input type="checkbox" name="is_day_off">Выходной</label>${f("Начало", "work_start", "10:00", "time", "required")}${f("Конец", "work_end", "19:00", "time", "required")}${f("Перерывы (10:30-11:00, 13:00-14:00)", "breaks", "")}<button class="primary">Сохранить день</button></form>`
      );
    }
    scheduleMonth ||= monthKey(ctx.today);
    const data = await api.get(
      `/master/schedule/calendar?${monthQuery(scheduleMonth)}&staff_id=${staffId}`,
    );
    scheduleDate ||= ctx.today;
    scheduleDay = data.days.find((row) => row.date === scheduleDate);
    const day = scheduleDay;
    const editor = day
      ? `<h3>${e(new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", timeZone: "UTC" }).format(new Date(day.date + "T12:00:00Z")))}</h3>` +
        (day.scope === "project"
          ? notice(
              "Настройка для всей студии. Изменение повлияет на всех специалистов.",
            )
          : "") +
        (day.appointment_count
          ? notice(
              `На эту дату уже есть ${day.appointment_count} записи. Изменение расписания не отменит существующие записи.`,
            )
          : "") +
        `<form id="date-schedule-form" data-staff="${staffId}" data-date="${day.date}" data-scope="${day.scope}"><label class="field">Режим дня<select name="mode"><option value="weekly" ${!day.has_override ? "selected" : ""}>По обычному расписанию</option><option value="day_off" ${day.has_override && day.mode === "day_off" ? "selected" : ""}>Выходной</option><option value="custom" ${day.has_override && day.mode === "custom" ? "selected" : ""}>Особое расписание</option></select></label><div id="custom-hours" ${day.mode === "custom" && day.has_override ? "" : "hidden"}>${f("Начало", "work_start", (day.work_start || "10:00").slice(0, 5), "time")}${f("Конец", "work_end", (day.work_end || "18:00").slice(0, 5), "time")}${f("Перерывы (10:30-11:00, 13:00-14:00)", "breaks", day.breaks.map((pair) => pair.map((t) => t.slice(0, 5)).join("-")).join(", "))}</div><button class="primary">Сохранить</button></form>`
      : empty("Выберите дату");
    return (
      header("Расписание", "Отдельные даты") +
      chooseStaff +
      calendarView(data, {
        selected: scheduleDate,
        master: true,
        action: "schedule-date",
        monthAction: "schedule-month",
      }) +
      editor
    );
  }
  if (page === "manual") {
    services = await api.get("/master/services");
    team = await api.get("/master/staff");
    const clients = await api.get("/master/clients");
    return manualEditor(ctx, clients, services, team);
  }
  return empty("Экран недоступен");
}

async function run(action) {
  if (busy) return;
  busy = true;
  const enabledButtons = [...root.querySelectorAll("button")].filter(
    (button) => !button.disabled,
  );
  enabledButtons.forEach((button) => (button.disabled = true));
  try {
    await action();
    pendingAction = null;
  } catch (error) {
    pendingAction =
      error.code === "NETWORK" || error.status === 503 ? action : null;
    showError(error);
  } finally {
    busy = false;
    enabledButtons.forEach((button) => {
      if (button.isConnected) button.disabled = false;
    });
  }
}
root.addEventListener("click", (event) => {
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const { action, id } = target.dataset;
  const retryAction = pendingAction;
  run(async () => {
    if (
      brandingDirty &&
      ["nav", "mode"].includes(action) &&
      !window.confirm("Изменения не сохранены. Выйти?")
    )
      return;
    if (["nav", "mode"].includes(action)) brandingDirty = false;
    if (action === "nav") return route(id);
    if (action === "retry")
      return retryAction ? retryAction() : ctx ? route(...navigation.retry()) : start();
    if (action === "resume") {
      hold = (await api.get("/client/appointments")).find(
        (a) => a.id === Number(id),
      );
      if (!hold) throw new Error("Резерв недоступен");
      return route("confirm");
    }
    if (action === "mode") {
      if (id === "master" && !canManage(ctx)) return;
      mode = id;
      selectedDate = ctx.today;
      return route(id === "master" ? "dashboard" : "home");
    }
    if (action === "choose-service") {
      if (!ctx.can_book) return route("contact");
      chosen = services.find((s) => s.id === Number(id));
      selectedStaff = null;
      selectedSlot = null;
      return route(ctx.branding?.show_staff === false ? "time" : "staff");
    }
    if (action === "staff") {
      selectedStaff = id === "any" ? null : Number(id);
      selectedDate = ctx.today;
      clientMonth = monthKey(ctx.today);
      return route("time");
    }
    if (action === "client-date") {
      selectedDate = id;
      return route("time");
    }
    if (action === "client-month") {
      clientMonth = id;
      return route("time");
    }
    if (action === "schedule-date") {
      scheduleDate = id;
      return route("schedule-dates", scheduleStaff);
    }
    if (action === "schedule-month") {
      scheduleMonth = id;
      scheduleDate = null;
      return route("schedule-dates", scheduleStaff);
    }
    if (action === "daily-calendar") {
      dashboardCalendar = !dashboardCalendar;
      return route("dashboard");
    }
    if (action === "daily-date") {
      selectedDate = id;
      dashboardCalendar = false;
      return route("dashboard");
    }
    if (action === "daily-month") {
      selectedDate = id + "-01";
      return route("dashboard");
    }
    if (action === "daily-shift") {
      const day = new Date(selectedDate + "T12:00:00Z");
      day.setUTCDate(day.getUTCDate() + Number(id));
      selectedDate = day.toISOString().slice(0, 10);
      return route("dashboard");
    }
    if (action === "slot") {
      selectedSlot = id;
      hold = await api.mutate("/client/holds", {
        service_id: chosen.id,
        staff_id: selectedStaff,
        start_time: selectedSlot,
      });
      return route("confirm");
    }
    if (action === "brand-color") {
      const input = root.querySelector("[name=accent_color]");
      input.value = id;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      return;
    }
    if (action === "reset-brand") {
      if (!window.confirm("Вернуть оформление ZapisFlow?")) return;
      await api.mutate("/master/branding/reset", {});
      ctx = await api.get("/context");
      return route("branding");
    }
    if (action === "delete-brand-asset") {
      if (!window.confirm("Удалить изображение?")) return;
      await api.mutate(`/master/branding/assets/${id}`, {}, "DELETE");
      ctx = await api.get("/context");
      return route("branding");
    }
    if (action === "sync-brand") {
      const result = await api.mutate("/master/branding/sync-telegram", {});
      root
        .querySelector("main")
        .insertAdjacentHTML("beforeend", notice(result.message));
      return;
    }
    if (action === "repeat") {
      const rows = await api.get("/client/appointments");
      const old = rows.find((a) => a.id === Number(id));
      services = await api.get("/client/services");
      chosen = services.find((s) => s.id === old?.service_id);
      if (!chosen)
        throw new Error("Эта услуга больше недоступна. Выберите другую.");
      team = await api.get(`/client/staff?service_id=${chosen.id}`);
      selectedStaff = team.some((t) => t.id === old.staff_id)
        ? old.staff_id
        : null;
      selectedSlot = null;
      selectedDate = ctx.today;
      clientMonth = monthKey(ctx.today);
      return route("time");
    }
    if (action === "payment") return route("payment", id);
    if (action === "cancel") {
      if (
        !window.confirm(
          "Отменить запись? Внесённая предоплата не возвращается.",
        )
      )
        return;
      await api.mutate(`/client/appointments/${id}/cancel`, {});
      return route("bookings");
    }
    if (action === "approve") {
      await api.mutate(`/master/payments/${id}/decision`, { approve: true });
      return route("dashboard");
    }
    if (
      ["appointment", "client", "service-edit", "staff-edit"].includes(action)
    )
      return route(action, id);
  });
});
root.addEventListener("change", (event) => {
  if (
    event.target.name === "mode" &&
    event.target.form?.id === "date-schedule-form"
  )
    root.querySelector("#custom-hours").hidden =
      event.target.value !== "custom";
  if (
    event.target.closest("#branding-form") &&
    ["logo", "cover"].includes(event.target.name)
  ) {
    const file = event.target.files[0];
    if (
      file &&
      ["image/jpeg", "image/png", "image/webp"].includes(file.type) &&
      file.size <= 4 * 1024 * 1024
    ) {
      const image = root.querySelector("#draft-" + event.target.name);
      if (image.src.startsWith("blob:")) URL.revokeObjectURL(image.src);
      image.src = URL.createObjectURL(file);
      image.hidden = false;
      brandingDirty = true;
    }
  }
  if (event.target.id === "theme") theme.set(event.target.value);
  if (event.target.id === "proof-file" && event.target.files[0]) {
    const image = root.querySelector("#proof-preview");
    if (image.src.startsWith("blob:")) URL.revokeObjectURL(image.src);
    image.src = URL.createObjectURL(event.target.files[0]);
    image.hidden = false;
  }
});
root.addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.target;
  const data = formValues(form);
  run(async () => {
    if (form.id === "branding-form") {
      const logo = form.elements.logo.files[0],
        cover = form.elements.cover.files[0];
      const body = Object.fromEntries(
        Object.entries(data).filter(
          ([key]) =>
            ![
              "logo",
              "cover",
              "show_portfolio",
              "show_reviews",
              "show_contacts",
              "show_staff",
            ].includes(key),
        ),
      );
      for (const key of ["portfolio", "reviews", "contacts", "staff"])
        body["show_" + key] = form.elements["show_" + key].checked;
      await api.mutate("/master/branding", body, "PUT");
      for (const [kind, file] of [
        ["logo", logo],
        ["cover", cover],
      ])
        if (file)
          await api.upload(`/master/branding/assets/${kind}`, file, () => {});
      brandingDirty = false;
      ctx = await api.get("/context");
      theme.useBrand?.(ctx.branding?.theme_mode);
      await route("branding");
      root
        .querySelector("main")
        .insertAdjacentHTML(
          "afterbegin",
          '<p role="status" class="save-status">Оформление сохранено</p>',
        );
      return;
    }
    if (form.id === "date-form") {
      selectedDate = data.date;
      return route("time");
    }
    if (form.id === "dashboard-date-form") {
      selectedDate = data.date;
      return route("dashboard");
    }
    if (form.id === "confirm-form") {
      const result = await api.mutate("/client/appointments", {
        appointment_id: hold.id,
        phone: data.phone,
        policy_agreed: data.policy === "on",
      });
      hold = result;
      telegram?.notify("success");
      return route(Number(result.deposit) ? "payment" : "success", result.id);
    }
    if (form.id === "proof-form") {
      const file = form.querySelector("input[type=file]").files[0];
      if (
        !file ||
        !["image/jpeg", "image/png"].includes(file.type) ||
        file.size > 8 * 1024 * 1024
      )
        throw new Error("Выберите JPEG или PNG до 8 МБ");
      const progress = form.querySelector("progress");
      progress.hidden = false;
      await api.upload(
        `/client/appointments/${hold.id}/proof`,
        file,
        (p) => (progress.value = p),
      );
      return route("bookings");
    }
    if (form.id === "search-form") return route("clients", data.search);
    if (form.id === "notes-form") {
      await api.mutate(
        `/master/clients/${form.dataset.id}`,
        { notes: data.notes || null },
        "PATCH",
      );
      return route("client", form.dataset.id);
    }
    if (form.id === "reject-form") {
      await api.mutate(`/master/payments/${form.dataset.id}/decision`, {
        approve: false,
        reason: data.reason,
      });
      return route("dashboard");
    }
    if (form.id === "settings-form") {
      for (const input of form.querySelectorAll("input[type=number]"))
        data[input.name] = Number(input.value);
      for (const input of form.querySelectorAll("input[type=checkbox]"))
        data[input.name] = input.checked;
      for (const key of Object.keys(data))
        if (data[key] === "") data[key] = null;
      await api.mutate("/master/settings", data, "PATCH");
      ctx = await api.get("/context");
      return route("settings");
    }
    if (form.id === "service-form") {
      for (const key of ["duration_min", "buffer_min"])
        data[key] = Number(data[key]);
      data.is_active = form.elements.is_active.checked;
      await api.mutate(
        `/master/services${form.dataset.id === "new" ? "" : `/${form.dataset.id}`}`,
        data,
        form.dataset.id === "new" ? "POST" : "PUT",
      );
      return route("manage-services");
    }
    if (form.id === "staff-form") {
      data.is_active = form.elements.is_active.checked;
      data.service_ids = new FormData(form).getAll("service_ids").map(Number);
      await api.mutate(`/master/staff/${form.dataset.id}`, data, "PUT");
      return route("team");
    }
    if (form.id === "schedule-staff-form")
      return route(form.dataset.page || "schedule", data.staff_id);
    if (form.id === "schedule-form") {
      const body = {
        staff_id: Number(form.dataset.staff),
        is_day_off: form.elements.is_day_off.checked,
        work_start: data.work_start,
        work_end: data.work_end,
        breaks: data.breaks
          ? data.breaks.split(",").map((item) =>
              item
                .trim()
                .split("-")
                .map((s) => s.trim()),
            )
          : [],
      };
      if (data.weekday !== "") body.weekday = Number(data.weekday);
      else body.target_date = data.target_date;
      await api.mutate("/master/schedule", body, "PUT");
      return route("schedule-weekly", body.staff_id);
    }
    if (form.id === "date-schedule-form") {
      const body = {
        staff_id: Number(form.dataset.staff),
        scope: form.dataset.scope,
        mode: data.mode,
      };
      if (data.mode === "custom") {
        body.work_start = data.work_start;
        body.work_end = data.work_end;
        body.breaks = data.breaks
          ? data.breaks.split(",").map((item) =>
              item
                .trim()
                .split("-")
                .map((s) => s.trim()),
            )
          : [];
      }
      const result = await api.mutate(
        `/master/schedule/dates/${form.dataset.date}`,
        body,
        "PUT",
      );
      await route("schedule-dates", body.staff_id);
      if (result.warning)
        root
          .querySelector("main")
          .insertAdjacentHTML("beforeend", notice(result.warning));
      return;
    }
    if (form.id === "manual-form") {
      // Browser timezone never decides the studio instant: obtain matching
      // authoritative backend slot, then reuse normal booking service.
      const slots = await api.get(
        `/client/slots?service_id=${data.service_id}&staff_id=${data.staff_id}&target_date=${data.date}`,
      );
      const start = slots.slots.find((s) => s.slice(11, 16) === data.time);
      if (!start)
        throw new Error("Это время недоступно. Выберите свободный слот");
      await api.mutate("/master/appointments", {
        service_id: Number(data.service_id),
        staff_id: Number(data.staff_id),
        master_client_id: Number(data.master_client_id),
        start_time: start,
        phone: data.phone || null,
        notes: data.notes || null,
      });
      return route("dashboard");
    }
  });
});
async function start() {
  shell('<p role="status">Проверяем вход через Telegram…</p>');
  try {
    theme?.dispose?.();
    telegram?.dispose();
    if(themeListener)window.removeEventListener('zapisflow-theme-change',themeListener);
    tg = bootstrap();
    telegram = createTelegramBridge(tg);
    theme = setupTheme(tg);
    themeListener = () => telegram.syncChrome();
    window.addEventListener("zapisflow-theme-change", themeListener);
    telegram.syncChrome();
    const botId = botIdFromPath(location.pathname);
    await api.auth(botId, tg.initData);
    ctx = await api.get("/context");
    theme.useBrand?.(ctx.branding?.theme_mode);
    selectedDate = ctx.today;
    telegram.onBack(() => {
      const previous = backDestination(screen, mode, ctx.branding?.show_staff !== false);
      if (brandingDirty && !window.confirm("Изменения не сохранены. Выйти?"))
        return;
      brandingDirty = false;
      route(previous || (mode === "client" ? "home" : "dashboard"));
    });
    await route("home");
  } catch (error) {
    showError(error);
  }
}
start();

root.addEventListener("input", (event) => {
  const form = event.target.closest("#branding-form");
  if (!form) return;
  brandingDirty = true;
  if (event.target.id === "accent-picker")
    form.elements.accent_color.value = event.target.value;
  const draft = Object.fromEntries(new FormData(form));
  const preview = root.querySelector("#brand-preview");
  applyBrand(draft, preview);
  preview.dataset.theme =
    draft.theme_mode === "system"
      ? document.documentElement.dataset.theme
      : draft.theme_mode;
  preview.querySelector("h3").textContent =
    draft.brand_name || "Название бизнеса";
  preview.querySelector("p").textContent = draft.tagline || "";
  preview.querySelector("button").textContent =
    draft.booking_cta_label || "Записаться";
});
root.addEventListener("keydown", event => calendarKeys(event,root));
