import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { editor, draft, renderPreview } from "../src/branding/editor.js";
import { Api } from "../src/api/client.js";
const brand = {
  brand_name: "Studio Anna",
  accent_color: "#C1354E",
  theme_mode: "light",
  appearance_preset: "clean",
  booking_cta_label: "Записаться",
  show_contacts: true,
  show_staff: true,
  show_reviews: true,
  show_portfolio: true,
  logo_url: null,
  powered_by: "Работает на ZapisFlow",
};
const ctx = {
  branding: brand,
  project: { name: "Studio Anna" },
  user: { first_name: "Клиент" },
  contacts: { studio_phone: "+79991234567" },
  today: "2026-10-07",
};
test("Owner preview is local, inert to booking, includes texts/contacts and preserves the footer", () => {
  const dom = new JSDOM(editor(brand, ctx));
  const form = dom.window.document.querySelector("form");
  form.elements.welcome_text.value = "<script>x</script>";
  form.elements.description.value = "Описание";
  renderPreview(form, ctx, []);
  assert.equal(dom.window.document.querySelector("script"), null);
  assert.ok(
    form.querySelector("#preview-content").textContent.includes("<script>"),
  );
  assert.ok(
    form.querySelector("#preview-content").textContent.includes("Описание"),
  );
  assert.ok(
    [...form.querySelectorAll("#preview-content button")].every(
      (b) => b.disabled && !b.dataset.action,
    ),
  );
  assert.ok(
    form
      .querySelector("#preview-content")
      .textContent.includes("Работает на ZapisFlow"),
  );
  assert.equal(draft(form, brand).branding.logo_url, undefined);
  assert.equal(draft(form, brand).branding.powered_by, undefined);
  assert.equal(ctx.branding.brand_name, "Studio Anna");
  dom.window.close();
});
test("System preview follows Telegram rather than owner shell preference and visibility updates locally", () => {
  const dom = new JSDOM(editor(brand, ctx));
  dom.window.Telegram = { WebApp: { colorScheme: "dark" } };
  dom.window.document.documentElement.dataset.theme = "light";
  const form = dom.window.document.querySelector("form");
  form.elements.theme_mode.value = "system";
  form.elements.show_contacts.checked = false;
  renderPreview(form, ctx, []);
  assert.equal(form.querySelector("#brand-preview").dataset.theme, "dark");
  assert.ok(
    !form
      .querySelector("#preview-content")
      .textContent.includes("Будем на связи"),
  );
  form.dataset.deleteLogo = "true";
  assert.ok(draft(form, brand).delete_logo);
  dom.window.close();
});
test("Atomic multipart save keeps the same retry key and sends cookie/CSRF/tenant through normal transport", async () => {
  const calls = [];
  const api = new Api(async (url, options) => {
    calls.push(options);
    if (calls.length === 1) throw Error("Offline");
    return new Response(JSON.stringify({ brand_name: "Studio Anna" }), {
      status: 200,
    });
  });
  api.botId = "test-tenant";
  api.csrf = "test-only";
  const body = { branding: { brand_name: "Studio Anna" }, contacts: {} };
  const files = {
    logo: new File(["local-image"], "logo.png", { type: "image/png" }),
  };
  await assert.rejects(() => api.saveBranding(body, files));
  await api.saveBranding(body, files);
  assert.equal(
    calls[0].headers["Idempotency-Key"],
    calls[1].headers["Idempotency-Key"],
  );
  assert.equal(calls[1].headers["X-CSRF-Token"], "test-only");
  assert.equal(calls[1].headers["X-MiniApp-Bot"], "test-tenant");
  assert.equal(calls[1].credentials, "same-origin");
  assert.ok(calls[1].body.get("logo"));
  assert.equal(calls[1].headers["Content-Type"], undefined);
});
