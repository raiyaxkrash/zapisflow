import {
  escape as e,
  rub,
  button as b,
  field as f,
  header,
  bookingCard,
  empty,
  notice,
  paymentLabel,
  setupChecklist,
  settingsMenu,
  settingsEditor,
  serviceEditor,
  serviceRows,
  staffEditor,
  manualEditor,
} from "../ui.js";
import { calendarView, monthKey, monthQuery } from "../calendar.js";
import * as W from "./workspace.js";
export async function renderMaster(page, id, env) {
  const { api, ctx } = env;
  let {
    services,
    team,
    selectedDate,
    dashboardCalendar,
    scheduleStaff,
    scheduleMonth,
    scheduleDate,
    scheduleDay,
  } = env;
  try {
    if (page === "dashboard") {
      const rows = await api.get(
        `/master/appointments?target_date=${selectedDate}`,
      );
      const day = new Date(`${selectedDate}T12:00:00Z`);
      const controls = `<div class="daily-navigation">${b("‹", "daily-shift", -1, "secondary").replace("<button ", '<button aria-label="Предыдущий день" ')}<strong>${e(new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", timeZone: "UTC" }).format(day))}</strong>${b("›", "daily-shift", 1, "secondary").replace("<button ", '<button aria-label="Следующий день" ')}<div class="daily-tools">${b("Сегодня", "daily-today", "today", "secondary")}${b("Выбрать дату", "daily-calendar", "open", "secondary")}</div></div>`;
      const month = dashboardCalendar
        ? await api.get(
            `/master/calendar?${monthQuery(monthKey(selectedDate))}`,
          )
        : null;
      return W.today(
        ctx,
        rows,
        controls,
        dashboardCalendar
          ? calendarView(month, {
              selected: selectedDate,
              master: true,
              action: "daily-date",
              monthAction: "daily-month",
            })
          : "",
        selectedDate,
      );
    }

    if (page === "appointment") {
      const a = await api.get(`/master/appointments/${id}`);
      return W.details(ctx, a);
    }

    if (page === "clients") {
      const rows = await api.get(
        `/master/clients?search=${encodeURIComponent(id || "")}`,
      );
      return W.clients(ctx, rows, id);
    }

    if (page === "client") {
      const card = await api.get(`/master/clients/${id}`);
      return W.client(ctx, card);
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
      team = await api.get("/master/staff");
      return W.services(ctx, services, team);
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
      services = await api.get("/master/services");
      return W.team(ctx, team, services);
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
          b(
            "Еженедельное расписание",
            "nav",
            "schedule-weekly",
            "settings-row",
          ) +
          b("Отдельные даты", "nav", "schedule-dates", "settings-row") +
          b("Горизонт записи", "nav", "booking-settings", "settings-row")
        );
      const chooseStaff = `<form id="schedule-staff-form" data-page="${page}"><label class="field">Сотрудник<select name="staff_id">${team.map((row) => `<option value="${row.id}" ${row.id === staffId ? "selected" : ""}>${e(row.display_name)}</option>`).join("")}</select></label><button class="secondary">Показать</button></form>`;
      if (page === "schedule-weekly") {
        const data = await api.get(`/master/schedule?staff_id=${staffId}`);
        return (
          W.title("Расписание", "Еженедельное расписание") +
          chooseStaff +
          W.weeklyEditor(data.weekly, staffId, env.weeklyDay)
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
    if (page === "free-windows") {
      services = await api.get("/master/services");
      team = await api.get("/master/staff");
      const choice = env.freeSelection;
      const slots = choice
        ? await api.get(
            `/master/free-windows?service_id=${choice.service_id}&staff_id=${choice.staff_id}&target_date=${choice.target_date}`,
          )
        : null;
      return (
        W.title("Расписание", "Свободное время") +
        `<form id="free-form"><label class="field">Услуга<select name="service_id">${services
          .filter((s) => s.is_active)
          .map(
            (s) =>
              `<option value="${s.id}" ${s.id === choice?.service_id ? "selected" : ""}>${e(s.title)}</option>`,
          )
          .join(
            "",
          )}</select></label><label class="field">Сотрудник<select name="staff_id">${team
          .filter((s) => s.is_active)
          .map(
            (s) =>
              `<option value="${s.id}" ${s.id === choice?.staff_id ? "selected" : ""}>${e(s.display_name)}</option>`,
          )
          .join(
            "",
          )}</select></label>${f("Дата", "date", choice?.target_date || selectedDate, "date", "required")}<button class="primary">Показать свободное время</button></form>` +
        (slots
          ? slots.slots.length
            ? `<div class="times">${slots.slots.map((v) => b(v.slice(11, 16), "free-slot", v, "secondary")).join("")}</div>`
            : empty(
                "Свободного времени нет. Выберите другую дату или специалиста.",
              )
          : "")
      );
    }
    if (page === "manual") {
      services = await api.get("/master/services");
      team = await api.get("/master/staff");
      const clients = await api.get("/master/clients");
      return manualEditor(
        ctx,
        clients,
        services,
        team,
        env.manualSelection,
        env.manualClient,
      );
    }
    if (page === "master-portfolio")
      return W.portfolio(ctx, await api.get("/master/portfolio"));
    if (page === "analytics")
      return W.analytics(ctx, await api.get("/master/analytics"));
    if (page === "broadcasts")
      return W.broadcasts(ctx, await api.get("/master/broadcasts"));
    if (page === "payments")
      return W.payments(
        ctx,
        await api.get("/master/payments"),
        env.paymentFilter || "review",
      );
    return empty("Экран недоступен");
  } finally {
    env.publish({
      services,
      team,
      selectedDate,
      dashboardCalendar,
      scheduleStaff,
      scheduleMonth,
      scheduleDate,
      scheduleDay,
    });
  }
}
