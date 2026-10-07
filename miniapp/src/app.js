import * as ClientViews from "./client/views.js";
import { appShell, bookingSummary } from "./application/shell.js";
import { createNavigation, backDestination } from "./application/navigation.js";
import { createTelegramBridge } from "./telegram/bridge.js";
import { calendarKeys } from "./ui/behaviors.js";
import { Skeleton, ErrorState } from "./ui/primitives.js";
import { bindPortfolioMedia } from "./media.js";
import "./style.css";
import "./client/client.css";
import "./master/workspace.css";
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
  masterWorkspace,
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
let clientNotice = "";
let masterNotice = "",
  paymentFilter = "review",
  manualClient = null,
  weeklyDay = 0,
  freeSelection = null,
  manualSelection = null;
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
    (mode === "master" && masterNotice
      ? `<div role="status">${notice(masterNotice)}</div>`
      : "") +
    bookingProgress(screen) +
    (chosen && ["staff", "time", "confirm"].includes(screen)
      ? bookingSummary({
          service: chosen.title,
          price: rub(chosen.price),
          staff: team.find((row) => row.id === selectedStaff)?.display_name,
          slot: selectedSlot
            ? selectedSlot.slice(8, 10) +
              "." +
              selectedSlot.slice(5, 7) +
              " " +
              selectedSlot.slice(11, 16)
            : "",
        })
      : "") +
    content;
  root.innerHTML = appShell({
    content,
    ctx,
    mode,
    screen,
    theme: theme?.get(),
  });
  root.querySelector(".app-shell").dataset.mode = mode;
  if (mode === "client") root.querySelector(".app-top .theme-label")?.remove();
  cleanMedia = bindPortfolioMedia(root, api);
  telegram?.setBackVisible(screen !== "home" && screen !== "dashboard");
}
function notice(text) {
  return `<div class="notice">${e(text)}</div>`;
}
async function route(page, id) {
  const serial = navigation.begin(page, id);
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
    ErrorState({
      message: ClientViews.errorMessage(error),
      retry: false,
    }) +
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
    const data = await Promise.all([
      api.get("/client/services"),
      page === "home" ? api.get("/client/appointments") : Promise.resolve([]),
    ]);
    services = data[0];
    const blocked = !ctx.can_book
      ? notice("Запись сейчас недоступна. Свяжитесь с мастером.")
      : "";
    return (
      blocked +
      (page === "home"
        ? ClientViews.home(ctx, services, data[1])
        : header("Выберите подходящую услугу", "Услуги") +
          ClientViews.serviceCards(services))
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
      (clientNotice ? `<div role="status">${notice(clientNotice)}</div>` : "") +
      `<p class="sub">Часовой пояс студии: ${e(ctx.project.timezone)}</p><h3>${selected?.available ? "Свободное время" : "Нажмите на доступную дату"}</h3>` +
      (data.slots.length
        ? ClientViews.slots(data.slots, selectedSlot)
        : empty("Выберите дату с доступным временем."))
    );
  }
  if (page === "confirm")
    return ClientViews.details(hold, ctx, { confirmation: true });
  if (page === "client-detail") {
    const record = (await api.get("/client/appointments")).find(
      (a) => a.id === Number(id),
    );
    if (!record) throw new Error("Запись недоступна");
    return ClientViews.details(record, ctx);
  }
  if (page === "about")
    return (
      header(ctx.project.name, "О бизнесе") +
      `<p>${e(ctx.project.about || "Информация скоро появится")}</p>`
    );
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
  if (page === "bookings")
    return (
      (clientNotice ? `<div role="status">${notice(clientNotice)}</div>` : "") +
      ClientViews.bookings(await api.get("/client/appointments"), ctx.today)
    );
  if (page === "contact") return ClientViews.contactPage(ctx);
  if (page === "more") return ClientViews.more(ctx, theme?.get() || "system");
  if (page === "reviews")
    return reviewsView(await api.get("/client/reviews"), ctx.project.name);
  if (page === "portfolio")
    return portfolioView(await api.get("/client/portfolio"), ctx.project.name);
  if (page === "master-calendar") {
    dashboardCalendar = true;
    screen = "master-calendar";
    return render("dashboard");
  }
  if (page === "success") return ClientViews.success(hold);
  if (page === "branding") {
    const brand = await api.get("/master/branding");
    brandingDirty = false;
    return brandingEditor(brand);
  }
  if (mode === "master")
    return masterWorkspace(page, id, {
      api,
      ctx,
      services,
      team,
      selectedDate,
      dashboardCalendar,
      scheduleStaff,
      scheduleMonth,
      scheduleDate,
      scheduleDay,
      manualClient,
      paymentFilter,
      weeklyDay,
      freeSelection,
      manualSelection,
      publish(state) {
        ({
          services,
          team,
          selectedDate,
          dashboardCalendar,
          scheduleStaff,
          scheduleMonth,
          scheduleDate,
          scheduleDay,
        } = state);
      },
    });
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
    if (mode === "client" && error.code === "SLOT_TAKEN") {
      selectedSlot = null;
      hold = null;
      clientNotice =
        "Это время только что заняли. Выберите другое свободное время.";
      telegram?.notify("error");
      await route("time");
      return;
    }
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
    if (action === "nav") {
      if (mode === "master") masterNotice = "";
      if (id === "manual") {
        manualSelection = null;
        manualClient = null;
      }
      if (event.target.closest("[data-staff]"))
        scheduleStaff = Number(
          event.target.closest("[data-staff]").dataset.staff,
        );
      return route(id);
    }
    if (action === "retry")
      return retryAction
        ? retryAction()
        : ctx
          ? route(...navigation.retry())
          : start();
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
      hold = null;
      clientNotice = "";
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
    if (action === "nearest") {
      if (!ctx.can_book)
        throw new Error("Запись сейчас недоступна. Свяжитесь с мастером.");
      chosen = services.find((row) => row.id === Number(id));
      if (!chosen) throw new Error("Услуга недоступна");
      const result = await Promise.all([
        api.get(`/client/staff?service_id=${chosen.id}`),
        api.get(`/client/availability/nearest?service_id=${chosen.id}`),
      ]);
      team = result[0];
      const data = result[1];
      selectedSlot = null;
      hold = null;
      selectedStaff = data.staff_id || null;
      selectedDate = data.slot?.slice(0, 10) || ctx.today;
      clientMonth = monthKey(selectedDate);
      clientNotice = data.slot
        ? "Ближайшее свободное время: " + data.slot.slice(11, 16)
        : "Свободного времени до " +
          data.searched_until +
          " нет. Посмотрите другие даты.";
      return route("time");
    }
    if (action === "client-detail") return route("client-detail", id);
    if (action === "calendar-export") {
      const record = (await api.get("/client/appointments")).find(
        (row) => row.id === Number(id),
      );
      if (!record) throw new Error("Запись недоступна");
      return (await import("./client/calendar-event.js")).downloadCalendar(
        record,
        api.botId,
      );
    }
    if (action === "slot") {
      selectedSlot = id;
      clientNotice = "";
      const quote = await api.get(
        `/client/quote?service_id=${chosen.id}${selectedStaff ? "&staff_id=" + selectedStaff : ""}`,
      );
      hold = {
        ...quote,
        id: null,
        start_time: id,
        staff:
          team.find((row) => row.id === selectedStaff)?.display_name ||
          "Любой подходящий специалист",
        payment: [],
      };
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
      hold = null;
      clientNotice = "";
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
      clientNotice = "Запись отменена.";
      return route("bookings");
    }
    if (action === "proof-download") {
      const blob = await api.image(`/proofs/${id}`);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "receipt";
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      return;
    }
    if (action === "portfolio-delete") {
      if (!window.confirm("Удалить работу из портфолио?")) return;
      await api.mutate(`/master/portfolio/${id}`, {}, "DELETE");
      masterNotice = "Работа удалена.";
      return route("master-portfolio");
    }
    if (action === "free-slot") {
      manualClient = null;
      manualSelection = { ...freeSelection, start_time: id };
      return route("manual");
    }
    if (action === "weekly-day") {
      weeklyDay = Number(id);
      return route("schedule-weekly", scheduleStaff);
    }
    if (action === "day-override") {
      scheduleDate = selectedDate;
      scheduleMonth = monthKey(selectedDate);
      return route("schedule-dates");
    }
    if (action === "daily-today") {
      selectedDate = ctx.today;
      return route(dashboardCalendar ? "master-calendar" : "dashboard");
    }
    if (action === "payment-filter") {
      paymentFilter = id;
      return route("payments");
    }
    if (action === "manual-client") {
      manualSelection = null;
      manualClient = Number(id);
      return route("manual");
    }
    if (action === "service-active") {
      const row = services.find((s) => s.id === Number(id));
      if (!row) throw Error("Услуга недоступна");
      await api.mutate(
        `/master/services/${id}`,
        {
          title: row.title,
          description: row.description,
          price: row.price,
          duration_min: row.duration_min,
          buffer_min: row.buffer_min,
          deposit_type: row.deposit_type,
          deposit_value: row.deposit_value,
          is_active: !row.is_active,
        },
        "PUT",
      );
      masterNotice = "Услуга обновлена.";
      return route("manage-services");
    }
    if (action === "master-cancel" || action === "master-complete") {
      if (
        !window.confirm(
          action === "master-cancel"
            ? "Отменить запись? Сначала проверьте присланный чек."
            : "Завершить визит?",
        )
      )
        return;
      await api.mutate(`/master/appointments/${id}/action`, {
        action: action === "master-cancel" ? "cancel" : "complete",
      });
      masterNotice =
        action === "master-cancel" ? "Запись отменена." : "Визит завершён.";
      return route("appointment", id);
    }
    if (action === "approve") {
      await api.mutate(`/master/payments/${id}/decision`, { approve: true });
      masterNotice = "Оплата подтверждена.";
      telegram?.notify("success");
      return route("dashboard");
    }
    if (
      ["appointment", "client", "service-edit", "staff-edit"].includes(action)
    )
      return route(action, id);
  });
});
root.addEventListener("change", (event) => {
  if(event.target.name==='is_day_off'&&event.target.form?.id==='schedule-form'){const hours=root.querySelector('#weekly-hours');hours.hidden=event.target.checked;hours.disabled=event.target.checked;}

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
      if (!hold?.id)
        hold = await api.mutate("/client/holds", {
          service_id: chosen.id,
          staff_id: selectedStaff,
          start_time: selectedSlot,
        });
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
    if (form.id === "portfolio-form") {
      const file = form.elements.file.files[0];
      if (
        !file ||
        !["image/jpeg", "image/png"].includes(file.type) ||
        file.size > 8 * 1024 * 1024
      )
        throw Error("Выберите JPEG или PNG до 8 МБ");
      const progress = form.querySelector("progress");
      progress.hidden = false;
      await api.upload("/master/portfolio", file, (v) => (progress.value = v));
      masterNotice = "Работа добавлена.";
      return route("master-portfolio");
    }
    if (form.id === "free-form") {
      freeSelection = {
        service_id: Number(data.service_id),
        staff_id: Number(data.staff_id),
        target_date: data.date,
      };
      return route("free-windows");
    }
    if (form.id === "search-form") return route("clients", data.search);
    if (form.id === "notes-form") {
      await api.mutate(
        `/master/clients/${form.dataset.id}`,
        { notes: data.notes || null },
        "PATCH",
      );
      masterNotice = "Заметка сохранена.";
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
      masterNotice = "Настройки сохранены.";
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
      masterNotice = "Услуга сохранена.";
      return route("manage-services");
    }
    if (form.id === "staff-form") {
      data.is_active = form.elements.is_active.checked;
      data.service_ids = new FormData(form).getAll("service_ids").map(Number);
      await api.mutate(`/master/staff/${form.dataset.id}`, data, "PUT");
      masterNotice = "Сотрудник сохранён.";
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
      masterNotice = "Расписание сохранено.";
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
      masterNotice = "Расписание даты сохранено.";
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
      manualSelection = null;
      manualClient = null;
      masterNotice = "Запись создана.";
      return route("dashboard");
    }
  });
});
async function start() {
  shell('<p role="status">Проверяем вход через Telegram…</p>');
  try {
    theme?.dispose?.();
    telegram?.dispose();
    if (themeListener)
      window.removeEventListener("zapisflow-theme-change", themeListener);
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
      const previous = backDestination(
        screen,
        mode,
        ctx.branding?.show_staff !== false,
      );
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
root.addEventListener("keydown", (event) => calendarKeys(event, root));
