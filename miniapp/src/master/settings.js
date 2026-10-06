import { header, field as f } from "../ui.js";
export function settingsForm(page, data, projectName) {
  const fields =
    page === "contacts-edit"
      ? {
          studio_address: "Адрес",
          studio_phone: "Телефон",
          whatsapp_phone: "WhatsApp",
          working_hours_text: "Режим работы",
          contacts_intro_text: "Вступление",
          telegram_username: "Telegram username",
          vk_profile: "ВКонтакте",
          about_text: "О студии",
        }
      : page === "requisites"
        ? {
            bank_name: "Банк",
            bank_card_number: "Карта / телефон",
            bank_recipient_name: "Получатель",
          }
        : {
            booking_horizon_days: "Горизонт записи, дней",
            hold_duration_minutes: "Резервирование, минут",
            cancel_policy_hours: "Срок отмены, часов",
            min_advance_hours: "Минимум до визита, часов",
            grid_step_minutes: "Шаг слотов, минут",
            default_buffer_minutes: "Буфер, минут",
          };
  return (
    header(
      projectName,
      page === "requisites"
        ? "Реквизиты"
        : page === "contacts-edit"
          ? "Контакты"
          : "Настройки записи",
    ) +
    `<form id="settings-form">${Object.entries(fields)
      .map(([key, label]) =>
        f(
          label,
          key,
          data[key] ?? "",
          typeof data[key] === "number" ? "number" : "text",
        ),
      )
      .join(
        "",
      )}${page === "booking-settings" ? ["reminder_24h_enabled", "reminder_3h_enabled"].map((key, index) => `<label class="check"><input name="${key}" type="checkbox" ${data[key] ? "checked" : ""}>Напоминание за ${index ? 3 : 24} ч</label>`).join("") : ""}<button class="primary">Сохранить</button></form>`
  );
}
