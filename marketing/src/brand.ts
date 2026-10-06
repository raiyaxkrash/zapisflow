export type Brand = {
  brand_name: string;
  tagline: string;
  description: string;
  welcome_text: string;
  booking_cta_label: string;
  accent_color: string;
  appearance_preset?: "clean" | "soft" | "compact";
  theme_mode: "system" | "light" | "dark";
  show_contacts: boolean;
  show_staff: boolean;
  show_portfolio: boolean;
  show_reviews: boolean;
  logo_url: string | null;
  cover_url: string | null;
};
export function brandColors(hex?: string): Record<string, string> {
  const value = /^#[a-f\d]{6}$/i.test(hex || "") ? hex! : "#1D72FE";
  const rgb = [1, 3, 5].map((i) => parseInt(value.slice(i, i + 2), 16));
  const linear = rgb.map((v) => {
    v /= 255;
    return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  });
  const lum = linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
  return {
    "--zf-accent": value,
    "--zf-accent-foreground":
      (lum + 0.05) / 0.05 >= 1.05 / (lum + 0.05) ? "#000000" : "#FFFFFF",
  };
}
export function safeAsset(url?: string | null) {
  return url &&
    /^\/api\/branding\/[a-f0-9-]{36}\/assets\/(logo|cover)\?v=[a-f0-9]{64}$/i.test(
      url,
    )
    ? url
    : undefined;
}
