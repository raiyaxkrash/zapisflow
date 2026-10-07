import { applyTheme } from "../theme/theme.js";
import {
  escape as e,
  button as b,
  field as f,
  header,
  applyBrand,
} from "../ui.js";
import * as Client from "../client/views.js";
export function draft(form, base = {}) {
  const values = Object.fromEntries(
    new form.ownerDocument.defaultView.FormData(form),
  );
  const brand = {};
  for (const key of [
    "brand_name",
    "tagline",
    "description",
    "welcome_text",
    "booking_cta_label",
    "accent_color",
    "theme_mode",
    "appearance_preset",
  ])
    brand[key] = values[key] ?? base[key];
  for (const section of ["portfolio", "reviews", "contacts", "staff"])
    brand["show_" + section] = form.elements["show_" + section].checked;
  const contacts = {};
  for (const key of [
    "studio_address",
    "studio_phone",
    "whatsapp_phone",
    "telegram_username",
    "vk_profile",
  ])
    contacts[key] = values["contact_" + key] || null;
  return {
    branding: brand,
    contacts,
    delete_logo: form.dataset.deleteLogo === "true",
    delete_cover: form.dataset.deleteCover === "true",
  };
}
export function renderPreview(form, ctx, services = []) {
  const body = draft(form, ctx.branding);
  const preview = form.querySelector("#brand-preview");
  const logo = preview.querySelector("#draft-logo"),
    cover = preview.querySelector("#draft-cover");
  const urls = {
    logo_url: logo.hidden ? null : logo.getAttribute("src"),
    cover_url: cover.hidden ? null : cover.getAttribute("src"),
  };
  applyBrand(body.branding, preview);
  applyTheme(
    body.branding.theme_mode,
    form.ownerDocument.defaultView.Telegram?.WebApp,
    preview,
    form.ownerDocument.defaultView,
  );
  preview.querySelector("h3").textContent =
    body.branding.brand_name || ctx.project.name;
  preview.querySelector("#preview-content").innerHTML =
    Client.home(
      {
        ...ctx,
        user: { first_name: "Клиент" },
        contacts: body.contacts,
        project: {
          ...ctx.project,
          name: body.branding.brand_name || ctx.project.name,
          about: body.branding.description || "",
        },
        branding: { ...body.branding, ...urls, cover_url: null },
      },
      services,
      [],
    ) +
    `<section><h3>О бизнесе</h3><p>${e(body.branding.description || "Описание ещё не добавлено")}</p></section>` +
    (body.branding.show_contacts
      ? Client.contactPage({
          ...ctx,
          contacts: body.contacts,
          project: { ...ctx.project, about: "" },
        })
      : "") +
    `<footer class="powered">Работает на ZapisFlow</footer>`;
  preview.querySelectorAll("#preview-content button").forEach((button) => {
    button.disabled = true;
    button.removeAttribute("data-action");
  });
}
export function editor(
  brand,
  ctx = {
    project: { name: brand.brand_name },
    user: { first_name: "Клиент" },
    contacts: {},
    branding: brand,
    today: "",
  },
  services = [],
) {
  const text = (name, label, max, rows = 3) =>
    `<label class="field">${label}<textarea name="${name}" maxlength="${max}" rows="${rows}">${e(brand[name] || "")}</textarea></label>`;
  return (
    header("Настройки", "Оформление") +
    `<p class="sub">Ваш бренд поверх дизайна ZapisFlow. Клиенты увидят изменения после сохранения.</p><form id="branding-form"><section id="brand-preview" class="brand-preview" aria-label="Предпросмотр клиента"><img id="draft-logo" class="brand-logo" alt="Логотип" ${brand.logo_url ? `src="${e(brand.logo_url)}"` : "hidden"}><img id="draft-cover" class="cover" alt="Обложка" ${brand.cover_url ? `src="${e(brand.cover_url)}"` : "hidden"}><h3>${e(brand.brand_name)}</h3><div id="preview-content"><p>${e(brand.tagline)}</p><button type="button" class="primary" disabled>${e(brand.booking_cta_label || "Записаться")}</button><footer class="powered">Работает на ZapisFlow</footer></div></section>${b("Посмотреть глазами клиента", "brand-preview-only", "", "secondary")}<details open><summary>Бренд и изображения</summary>${f("Название бизнеса", "brand_name", brand.brand_name, "text", 'maxlength="128"')}${["logo", "cover"].map((kind) => `<label class="field">${kind === "logo" ? "Логотип" : "Обложка"}<input name="${kind}" type="file" accept="image/png,image/jpeg,image/webp"></label>${b("Убрать " + (kind === "logo" ? "логотип" : "обложку"), "delete-brand-asset", kind, "secondary")}`).join("")}<p class="sub">PNG, JPEG, WebP до 4 МБ. SVG не принимается.</p></details><details><summary>Цвета и тема</summary><label class="field">Акцент HEX<input name="accent_color" value="${e(brand.accent_color)}" pattern="#[0-9A-Fa-f]{6}" required maxlength="7"></label><label class="field">Выбрать цвет<input id="accent-picker" type="color" value="${e(brand.accent_color)}"></label><div class="preset-colors">${[
      ["#1D72FE", "Синий"],
      ["#C1354E", "Розовый"],
      ["#087A55", "Зелёный"],
      ["#202938", "Графит"],
    ]
      .map(([color, label]) => b(label, "brand-color", color, "secondary"))
      .join("")}</div><label class="field">Тема<select name="theme_mode">${[
      ["system", "Система"],
      ["light", "Светлая"],
      ["dark", "Тёмная"],
    ]
      .map(
        ([id, label]) =>
          `<option value="${id}" ${brand.theme_mode === id ? "selected" : ""}>${label}</option>`,
      )
      .join(
        "",
      )}</select></label><input type="hidden" name="appearance_preset" value="${e(brand.appearance_preset || "clean")}"></details><details><summary>Тексты</summary>${text("tagline", "Короткое описание", 120, 2)}${text("description", "О бизнесе", 2000)}${text("welcome_text", "Приветствие", 1000)}${f("Кнопка записи", "booking_cta_label", brand.booking_cta_label, "text", 'maxlength="32"')}</details><details><summary>Клиентские разделы</summary>${[
      ["portfolio", "Портфолио"],
      ["reviews", "Отзывы"],
      ["contacts", "Контакты"],
      ["staff", "Команда"],
    ]
      .map(
        ([key, label]) =>
          `<label class="check"><input name="show_${key}" type="checkbox" ${brand["show_" + key] !== false ? "checked" : ""}>${label}</label>`,
      )
      .join(
        "",
      )}<p class="sub">Запись и подпись ZapisFlow всегда остаются доступными.</p></details><details><summary>Контакты</summary>${[
      ["studio_address", "Адрес", "text"],
      ["studio_phone", "Телефон", "tel"],
      ["whatsapp_phone", "WhatsApp", "tel"],
      ["telegram_username", "Telegram", "text"],
      ["vk_profile", "ВКонтакте https://", "url"],
    ]
      .map(([key, label, type]) =>
        f(
          label,
          "contact_" + key,
          ctx.contacts[key] || "",
          type,
          `maxlength="${key === "studio_address" ? 500 : key === "vk_profile" ? 128 : 64}"`,
        ),
      )
      .join(
        "",
      )}</details><p id="brand-draft-status" role="status">Изменения ещё не сохранены</p><button class="primary">Сохранить оформление</button></form>${b("Обновить профиль Telegram", "sync-brand", "", "secondary")}<p class="hint">Сначала сохраните оформление. Обновляется имя и описание клиентского бота. Фото и username автоматически не меняются.</p>${b("Вернуть стандартное оформление", "reset-brand", "", "link")}`
  );
}
