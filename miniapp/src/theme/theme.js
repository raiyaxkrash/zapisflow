export function applyTheme(preference, tg, root = document.documentElement) {
  const dark =
    preference === "dark" ||
    (preference === "system" &&
      (tg?.colorScheme === "dark" ||
        (!tg && matchMedia("(prefers-color-scheme: dark)").matches)));
  root.dataset.theme = dark ? "dark" : "light";
}
export function setupTheme(tg, win = window) {
  let preference;
  try {
    preference = win.localStorage.getItem("zapisflow-theme") || "system";
  } catch {
    preference = "system";
  }
  if (!["system", "light", "dark"].includes(preference)) preference = "system";
  const render = () => applyTheme(preference, tg, win.document.documentElement);
  render();
  tg?.onEvent("themeChanged", render);
  return {
    get: () => preference,
    useBrand: (value) => {
      let hasPreference = false;
      try {
        hasPreference = !!win.localStorage.getItem("zapisflow-theme");
      } catch {}
      if (!hasPreference) {
        preference = ["system", "light", "dark"].includes(value)
          ? value
          : "system";
        render();
      }
    },
    set: (value) => {
      preference = value;
      try {
        win.localStorage.setItem("zapisflow-theme", value);
      } catch {}
      render();
    },
  };
}
