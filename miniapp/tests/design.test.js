import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { JSDOM } from "jsdom";
import {
  colorTokens,
  setupChecklist,
  applyBrand,
  brandingEditor,
  bookingProgress,
  paymentLabel,
} from "../src/ui.js";
test("Shared semantic tokens match marketing build boundary", async () => {
  assert.equal(
    await readFile(new URL("../src/tokens.css", import.meta.url), "utf8"),
    await readFile(
      new URL("../../marketing/src/tokens.css", import.meta.url),
      "utf8",
    ),
  );
});
for (const accent of [
  "#FFFFCC",
  "#FFFF00",
  "#0000FF",
  "#7C3AED",
  "#FF0000",
  "#000080",
  "#000000",
  "#FFFFFF",
  "#00FF00",
  "#AA00FF",
])
  test(`Readable foreground for ${accent}`, () => {
    const tokens = colorTokens(accent);
    const rgb = accent
      .slice(1)
      .match(/../g)
      .map((v) => parseInt(v, 16) / 255)
      .map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
    const lum = rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
    const contrast =
      tokens["--zf-accent-foreground"] === "#000000"
        ? (lum + 0.05) / 0.05
        : 1.05 / (lum + 0.05);
    assert.ok(contrast >= 4.5);
  });
test("Invalid color is fail-safe and cannot inject CSS", () =>
  assert.equal(colorTokens("url(javascript:evil)")["--zf-accent"], "#1D72FE"));
test("Brand draft preview escaped and original state unchanged", async () => {
  const brand = {
    brand_name: "<script>x</script>",
    tagline: "Test",
    accent_color: "#FF0000",
    theme_mode: "light",
    booking_cta_label: "Записаться",
  };
  const original = JSON.stringify(brand);
  const dom = new JSDOM(await brandingEditor(brand));
  assert.equal(dom.window.document.querySelector("script"), null);
  applyBrand(brand, dom.window.document.querySelector("#brand-preview"));
  assert.equal(JSON.stringify(brand), original);
  assert.ok(dom.window.document.querySelector("[name=brand_name]"));
  assert.ok(dom.window.document.querySelector("[name=logo]"));
  dom.window.close();
});
test("Booking progress and payment statuses use client copy", () => {
  assert.ok(bookingProgress("time").includes('aria-current="step"'));
  assert.equal(paymentLabel("PENDING"), "Ожидает предоплаты");
  assert.equal(bookingProgress("home"), "");
});

test("Deployment gateway routes generated brand assets to backend", async () => {
  const gateway = await readFile(
    new URL("../../deploy/miniapp/gateway.Caddyfile", import.meta.url),
    "utf8",
  );
  assert.match(gateway, /@api path \/api\/miniapp\/\* \/api\/branding\/\*/);
});

test("Optional owner setup does not block booking or expose branding to staff", async () => {
  assert.match(
    await setupChecklist({ capabilities: { role: "owner" } }),
    /по желанию/,
  );
  assert.equal(await setupChecklist({ capabilities: { role: "staff" } }), "");
});
