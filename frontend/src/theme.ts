// Light / dark / follow the system. The choice is a per-viewer convenience, so it lives in
// localStorage; storage can be missing or throw, and then the app simply follows the system.
export type ThemeChoice = "system" | "light" | "dark";
const KEY = "proplock.theme";

export function loadTheme(): ThemeChoice {
  try {
    const v = window.localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(choice: ThemeChoice): void {
  const root = document.documentElement;
  if (choice === "system") delete root.dataset.theme;
  else root.dataset.theme = choice;
  try {
    if (choice === "system") window.localStorage.removeItem(KEY);
    else window.localStorage.setItem(KEY, choice);
  } catch {
    // storage unavailable: the choice lasts for this tab only
  }
}
