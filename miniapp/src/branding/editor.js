import { escape as e, button as b, field as f, header } from "../ui.js";
export function editor(brand) {
  const texts = {
    brand_name: "Название бизнеса",
    tagline: "Короткое описание",
    description: "О бизнесе",
    welcome_text: "Приветствие",
    booking_cta_label: "Кнопка записи",
  };
  return (
    header("Настройки", "Оформление") +
    `<form id="branding-form"><div id="brand-preview" class="brand-preview"><img id="draft-logo" class="brand-logo" alt="" ${brand.logo_url ? `src="${e(brand.logo_url)}"` : "hidden"}><img id="draft-cover" class="cover" alt="" ${brand.cover_url ? `src="${e(brand.cover_url)}"` : "hidden"}><h3>${e(brand.brand_name)}</h3><p>${e(brand.tagline)}</p><button type="button" class="primary">${e(brand.booking_cta_label)}</button><p class="powered">Работает на ZapisFlow</p></div>${f("Название бизнеса", "brand_name", brand.brand_name, "text", 'maxlength="128"')}<label class="field">Акцентный цвет<input name="accent_color" value="${e(brand.accent_color)}" pattern="#[0-9A-Fa-f]{6}" required maxlength="7"></label><div class="preset-colors" aria-label="Цветовые варианты"><button type="button" class="secondary" data-action="brand-color" data-id="#1D72FE">Синий</button><button type="button" class="secondary" data-action="brand-color" data-id="#C1354E">Розовый</button><button type="button" class="secondary" data-action="brand-color" data-id="#087A55">Зелёный</button><button type="button" class="secondary" data-action="brand-color" data-id="#202938">Графит</button></div><label class="field">Выберите цвет<input id="accent-picker" type="color" value="${e(brand.accent_color)}"></label><label class="field">Тема<select name="theme_mode">${["system", "light", "dark"].map((t) => `<option value="${t}" ${t === brand.theme_mode ? "selected" : ""}>${{ system: "Система", light: "Светлая", dark: "Тёмная" }[t]}</option>`).join("")}</select></label><label class="field">Стиль карточек<select name="appearance_preset">${["clean", "soft", "compact"].map((t) => `<option value="${t}" ${t === brand.appearance_preset ? "selected" : ""}>${{ clean: "Чистый", soft: "Мягкий", compact: "Компактный" }[t]}</option>`).join("")}</select></label><details><summary>Тексты и разделы</summary>${Object.entries(
      texts,
    )
      .filter(([key]) => key !== "brand_name")
      .map(([key, label]) =>
        f(
          label,
          key,
          brand[key] || "",
          "text",
          `maxlength="${{ tagline: 120, description: 2000, welcome_text: 1000, booking_cta_label: 32 }[key]}"`,
        ),
      )
      .join(
        "",
      )}${["portfolio", "reviews", "contacts", "staff"].map((key) => `<label class="check"><input name="show_${key}" type="checkbox" ${brand["show_" + key] ? "checked" : ""}>${{ portfolio: "Портфолио", reviews: "Отзывы", contacts: "Контакты", staff: "Команда" }[key]}</label>`).join("")}</details><details><summary>Логотип и обложка</summary>${["logo", "cover"].map((kind) => `<label class="field">${kind === "logo" ? "Логотип" : "Обложка"}<input name="${kind}" type="file" accept="image/png,image/jpeg,image/webp"></label>${brand[kind + "_url"] ? `<img class="brand-logo" src="${e(brand[kind + "_url"])}" alt="Текущее изображение">${b("Удалить " + (kind === "logo" ? "логотип" : "обложку"), "delete-brand-asset", kind, "secondary")}` : ""}`).join("")}<p class="sub">PNG, JPEG, WebP, до 4 МБ. Применяются после сохранения.</p></details><button class="primary">Сохранить оформление</button></form>${b("Посмотреть глазами клиента", "mode", "client", "secondary")}${b("Обновить профиль Telegram", "sync-brand", "", "secondary")}${b("Вернуть оформление ZapisFlow", "reset-brand", "", "link")}<p class="sub">Username не меняется. Фото профиля можно настроить вручную через BotFather.</p>`
  );
}
