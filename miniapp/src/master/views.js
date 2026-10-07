import {
  escape as e,
  field as f,
  button as b,
  header,
  rub,
  empty,
  notice,
} from "../ui.js";
export function serviceEditor(s, id) {
  return (
    header("Услуга", id === "new" ? "Новая услуга" : s.title) +
    `<form id="service-form" data-id="${e(id)}">${f("Название", "title", s.title, "text", 'required maxlength="255"')}<label class="field">Описание<textarea name="description" rows="3" maxlength="2000">${e(s.description || "")}</textarea></label>${f("Цена, ₽", "price", s.price, "number", 'min="0" step="0.01" required')}${f("Длительность, мин", "duration_min", s.duration_min, "number", 'min="1" required')}${f("Буфер, мин", "buffer_min", s.buffer_min, "number", 'min="0" required')}<label class="field">Тип предоплаты<select name="deposit_type"><option value="FIXED" ${s.deposit_type === "FIXED" ? "selected" : ""}>Сумма</option><option value="PERCENT" ${s.deposit_type === "PERCENT" ? "selected" : ""}>Процент</option></select></label>${f("Предоплата", "deposit_value", s.deposit_value, "number", 'min="0" step="0.01" required')}<label class="check"><input name="is_active" type="checkbox" ${s.is_active ? "checked" : ""}>Услуга активна</label><button class="primary">Сохранить</button></form>`+b("Назначить сотрудников в разделе Команда","nav","team","secondary")
  );
}

export function settingsMenu(ctx) {
  return (
    header(ctx.project.name, "Ещё") +
    [
      ["manage-services", "Услуги"],
      ["team", "Команда"],
      ["schedule", "Расписание"],
      ["payments", "Оплаты"],
      ["master-portfolio", "Портфолио"],
      ["reviews", "Отзывы"],
      ["broadcasts", "Рассылки"],
      ["analytics", "Аналитика"],
      ["contacts-edit", "Контакты"],
      ["requisites", "Предоплата и реквизиты"],
      ["booking-settings", "Настройки записи"],
      ...(ctx.capabilities.role === "owner"
        ? [["branding", "Оформление"]]
        : []),
      ["manual", "Добавить запись"],
    ]
      .map(([p, l]) => b(l, "nav", p, "settings-row"))
      .join("")
  );
}

export function staffEditor(s, services, id) {
  return (
    header("Сотрудник", s.display_name) +
    `<form id="staff-form" data-id="${id}">${f("Имя", "display_name", s.display_name, "text", "required")}${f("Специализация", "specialization", s.specialization || "")}<label class="check"><input name="is_active" type="checkbox" ${s.is_active ? "checked" : ""}>Активен</label><h3>Услуги</h3>${notice("Если назначения пустые, сотрудник оказывает все активные услуги — правило текущего ZapisFlow.")}${services.map((row) => `<label class="check"><input name="service_ids" value="${row.id}" type="checkbox" ${s.service_ids.includes(row.id) ? "checked" : ""}>${e(row.title)}</label>`).join("")}<button class="primary">Сохранить</button></form>`
  );
}

export function manualEditor(
  ctx,
  clients,
  services,
  team,
  selection = {},
  clientId = null,
) {
  return (
    header("Расписание", "Добавить запись") +
    (clients.length
      ? `<form id="manual-form">${[
          ["master_client_id", "Клиент", clients.map((c) => [c.id, c.name])],
          [
            "service_id",
            "Услуга",
            services.filter((s) => s.is_active).map((s) => [s.id, s.title]),
          ],
          [
            "staff_id",
            "Сотрудник",
            team.filter((s) => s.is_active).map((s) => [s.id, s.display_name]),
          ],
        ]
          .map(
            ([key, label, rows]) =>
              `<label class="field">${label}<select name="${key}">${rows.map(([v, l]) => `<option value="${v}" ${(key === "master_client_id" ? clientId : selection?.[key]) === v ? "selected" : ""}>${e(l)}</option>`).join("")}</select></label>`,
          )
          .join(
            "",
          )}${f("Дата", "date", selection?.start_time?.slice(0, 10) || ctx.today, "date", "required")}${f("Время (по часовому поясу студии)", "time", selection?.start_time?.slice(11, 16) || "10:00", "time", "required")}${f("Телефон клиента (необязательно)", "phone", "", "tel")}${f("Заметки", "notes", "")}<button class="primary">Проверить время и создать</button></form>`
      : empty("Сначала добавьте клиента через Telegram-админку"))
  );
}

export function setupChecklist(ctx) {
  if (ctx.capabilities.role !== "owner") return "";
  return `<details class="setup-checklist"><summary>Подготовьте приложение для клиентов</summary><p>✓ Бот подключён</p><p>Проверьте услуги и расписание перед первой записью.</p>${b("Проверить услуги", "nav", "manage-services", "secondary")}${b("Проверить расписание", "nav", "schedule", "secondary")}<p>Добавьте логотип и цвет по желанию. Это не требуется для записи.</p>${b("Оформить бизнес", "nav", "branding", "secondary")}</details>`;
}
