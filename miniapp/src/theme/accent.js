export function colorTokens(hex) {
  const value = /^#[a-f\d]{6}$/i.test(hex || "") ? hex : "#1D72FE";
  const rgb = [1, 3, 5].map((i) => parseInt(value.slice(i, i + 2), 16));
  const linear = rgb.map((v) => {
    v /= 255;
    return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  });
  const lum = linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
  const foreground =
    (lum + 0.05) / 0.05 >= 1.05 / (lum + 0.05) ? "#000000" : "#FFFFFF";
  const mix = amount => '#' + rgb.map(channel => Math.round(channel * (1 - amount) + (foreground === '#000000' ? 255 : 0) * amount).toString(16).padStart(2, '0')).join('');
  return {
    "--zf-accent": value,
    "--zf-accent-hover": mix(0.06),
    "--zf-accent-foreground": foreground,
    "--zf-accent-soft": `color-mix(in srgb, ${value} 12%, var(--zf-surface))`,
    "--zf-accent-active": mix(0.12),
    "--zf-accent-pressed": mix(0.12),
    "--zf-focus-ring": `color-mix(in srgb, ${value} 45%, var(--zf-focus))`,
  };
}
