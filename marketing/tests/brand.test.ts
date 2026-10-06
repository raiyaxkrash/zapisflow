import { describe, it, expect } from "vitest";
import { brandColors, safeAsset } from "../src/brand";
describe("Tenant branding boundary", () => {
  for (const color of [
    "#FFFFCC",
    "#FF0000",
    "#000080",
    "#000000",
    "#FFFFFF",
    "#00FF00",
    "#AA00FF",
  ])
    it("chooses readable foreground " + color, () => {
      const tokens = brandColors(color);
      const rgb = [1, 3, 5]
        .map((i) => parseInt(color.slice(i, i + 2), 16) / 255)
        .map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
      const lum = rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
      expect(
        tokens["--zf-accent-foreground"] === "#000000"
          ? (lum + 0.05) / 0.05
          : 1.05 / (lum + 0.05),
      ).toBeGreaterThanOrEqual(4.5);
    });
  it("rejects arbitrary asset URLs and CSS injection", () => {
    expect(safeAsset("javascript:alert(1)")).toBeUndefined();
    expect(safeAsset("https://evil.test/logo.png")).toBeUndefined();
    expect(brandColors("url(javascript:x)")["--zf-accent"]).toBe("#1D72FE");
  });
  it("accepts generated tenant asset URLs", () =>
    expect(
      safeAsset(
        "/api/branding/11111111-2222-3333-4444-555555555555/assets/logo?v=" +
          "a".repeat(64),
      ),
    ).toBeTruthy());
});
