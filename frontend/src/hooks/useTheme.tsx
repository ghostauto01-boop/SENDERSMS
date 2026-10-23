import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

/**
 * THEME — one source of truth for light and dark.
 *
 * The class lives on `<html>` (Tailwind's `darkMode: "class"`), so every
 * `dark:` utility in the app follows it without a second stylesheet. The
 * choice is remembered in `localStorage` and painted before React boots by a
 * tiny inline script in `index.html`, which is what stops a dark-mode user
 * seeing a white flash on every reload.
 *
 * The provider is mounted once in `App`; pages (Overview, Settings) only ever
 * call `useTheme()`. When a page is rendered on its own — a unit test, a
 * story, the public invoice screen — the hook still works: without a provider
 * it manages its own local copy and applies it to the document directly.
 */

export type Theme = "light" | "dark";

const STORAGE_KEY = "theme";

function readTheme(): Theme {
  if (typeof window === "undefined") return "light";
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === "dark" || saved === "light") return saved;
  } catch {
    /* private mode / blocked storage — fall through to the OS preference */
  }
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** Paint the document for a theme. Safe to call before React renders. */
export function applyTheme(theme: Theme) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  root.classList.toggle("dark", theme === "dark");
  root.classList.toggle("light", theme === "light");
  root.style.colorScheme = theme;
  // The browser chrome on Android/iOS follows this meta tag, so the app keeps
  // its parchment (or forest) edge instead of flashing white behind the notch.
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", theme === "dark" ? "#0c1712" : "#f6f5f1");
}

function persist(theme: Theme) {
  try {
    window.localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    /* storage can be unavailable; the theme still applies for this session */
  }
}

export interface ThemeContextValue {
  theme: Theme;
  isDark: boolean;
  setTheme: (theme: Theme) => void;
  toggle: () => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(readTheme);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  // Follow the operating system until the operator makes an explicit choice.
  useEffect(() => {
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!media) return;
    const onChange = (event: MediaQueryListEvent) => {
      let chosen: string | null = null;
      try {
        chosen = window.localStorage.getItem(STORAGE_KEY);
      } catch {
        chosen = null;
      }
      if (chosen !== "light" && chosen !== "dark") {
        setThemeState(event.matches ? "dark" : "light");
      }
    };
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next);
    persist(next);
  }, []);

  const value = useMemo<ThemeContextValue>(
    () => ({
      theme,
      isDark: theme === "dark",
      setTheme,
      toggle: () => setTheme(theme === "dark" ? "light" : "dark"),
    }),
    [theme, setTheme]
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  // Hooks are always called in the same order, so a page rendered outside the
  // provider is still a legal React component.
  const [local, setLocal] = useState<Theme>(readTheme);

  useEffect(() => {
    if (!context) applyTheme(local);
  }, [context, local]);

  const localSet = useCallback((next: Theme) => {
    setLocal(next);
    persist(next);
  }, []);

  if (context) return context;

  return {
    theme: local,
    isDark: local === "dark",
    setTheme: localSet,
    toggle: () => localSet(local === "dark" ? "light" : "dark"),
  };
}

export default useTheme;
