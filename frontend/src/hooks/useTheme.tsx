import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

/**
 * APPEARANCE & PROFESSIONAL THEME ENGINE
 *
 * Powers Light / Dark / System mode, 10 executive & SaaS color palettes (applied
 * dynamically via RGB channel CSS variables on `<html>` so every Tailwind
 * `bg-primary-*`, `text-primary-*`, `ring-primary-*`, `bg-canvas`, and `gray-*`
 * class updates live across the entire workspace without page reload), plus
 * interface density, corner geometry, and typography.
 */

export type Theme = "light" | "dark";
export type ThemeMode = "light" | "dark" | "system";
export type DensityMode = "compact" | "comfortable" | "spacious";
export type RadiusMode = "sharp" | "rounded" | "soft";
export type FontMode = "default" | "inter" | "editorial" | "mono";
export type FontFamilyId = FontMode;

export type PaletteId =
  | "emerald"
  | "sapphire"
  | "indigo"
  | "obsidian"
  | "teal"
  | "amber"
  | "crimson"
  | "plum"
  | "nordic"
  | "terracotta";

type ShadeKey = 50 | 100 | 200 | 300 | 400 | 500 | 600 | 700 | 800 | 900 | 950;

export interface PaletteDefinition {
  id: PaletteId;
  name: string;
  tagline: string;
  category: "Executive" | "Modern SaaS" | "Editorial" | "Minimal";
  swatches: [string, string, string, string];
  primary: Record<ShadeKey, string>;
  gray: Record<ShadeKey, string>;
  surfaces: {
    canvas: string;
    canvasDark: string;
    shell: string;
    shellMuted: string;
    surfaceDark: string;
    surfaceDarkMuted: string;
    themeColorLight: string;
    themeColorDark: string;
  };
}

const WARM_GRAY: Record<ShadeKey, string> = {
  50: "248 248 246",
  100: "242 242 239",
  200: "231 232 228",
  300: "214 216 211",
  400: "173 179 172",
  500: "130 139 131",
  600: "98 108 100",
  700: "76 86 78",
  800: "52 61 54",
  900: "32 40 35",
  950: "17 23 18",
};

const COOL_SLATE: Record<ShadeKey, string> = {
  50: "248 250 252",
  100: "241 245 249",
  200: "226 232 240",
  300: "203 213 225",
  400: "148 163 184",
  500: "100 116 139",
  600: "71 85 105",
  700: "51 65 85",
  800: "30 41 59",
  900: "15 23 42",
  950: "8 13 26",
};

const NEUTRAL_ZINC: Record<ShadeKey, string> = {
  50: "250 250 250",
  100: "244 244 245",
  200: "228 228 231",
  300: "212 212 216",
  400: "161 161 170",
  500: "113 113 122",
  600: "82 82 91",
  700: "63 63 70",
  800: "39 39 42",
  900: "24 24 27",
  950: "12 12 15",
};

export const PALETTES: Record<PaletteId, PaletteDefinition> = {
  emerald: {
    id: "emerald",
    name: "Forest & Parchment",
    tagline: "Calm botanical green on warm stone surfaces",
    category: "Executive",
    swatches: ["#123c2b", "#2a684e", "#5f9c80", "#ececea"],
    primary: {
      50: "240 246 242", 100: "220 235 225", 200: "186 216 197", 300: "143 189 165",
      400: "95 156 128", 500: "63 128 100", 600: "42 104 78", 700: "33 84 64",
      800: "26 69 52", 900: "18 60 43", 950: "8 37 26",
    },
    gray: WARM_GRAY,
    surfaces: {
      canvas: "236 236 234", canvasDark: "12 20 16", shell: "250 250 248", shellMuted: "241 242 238",
      surfaceDark: "18 28 22", surfaceDarkMuted: "24 36 29", themeColorLight: "#f6f5f1", themeColorDark: "#0c1712",
    },
  },
  sapphire: {
    id: "sapphire",
    name: "Sapphire Trust",
    tagline: "Crisp institutional navy & cobalt for enterprise operations",
    category: "Executive",
    swatches: ["#0f295c", "#1d4ed8", "#60a5fa", "#eef2f8"],
    primary: {
      50: "239 246 255", 100: "219 234 254", 200: "191 219 254", 300: "147 197 253",
      400: "96 165 250", 500: "59 130 246", 600: "29 78 216", 700: "24 63 173",
      800: "20 49 135", 900: "15 41 92", 950: "9 23 56",
    },
    gray: COOL_SLATE,
    surfaces: {
      canvas: "238 242 248", canvasDark: "9 15 28", shell: "249 251 255", shellMuted: "240 244 250",
      surfaceDark: "15 23 42", surfaceDarkMuted: "23 34 59", themeColorLight: "#eef2f8", themeColorDark: "#090f1c",
    },
  },
  indigo: {
    id: "indigo",
    name: "Linear Indigo",
    tagline: "High-velocity modern SaaS violet-blue",
    category: "Modern SaaS",
    swatches: ["#262169", "#4f46e5", "#818cf8", "#f1f2f9"],
    primary: {
      50: "238 242 255", 100: "224 231 255", 200: "199 210 254", 300: "165 180 252",
      400: "129 140 248", 500: "99 102 241", 600: "79 70 229", 700: "67 56 202",
      800: "55 48 163", 900: "38 33 105", 950: "23 20 66",
    },
    gray: COOL_SLATE,
    surfaces: {
      canvas: "241 242 249", canvasDark: "11 12 24", shell: "250 250 255", shellMuted: "242 243 250",
      surfaceDark: "18 19 36", surfaceDarkMuted: "26 28 50", themeColorLight: "#f1f2f9", themeColorDark: "#0b0c18",
    },
  },
  obsidian: {
    id: "obsidian",
    name: "Obsidian Mono",
    tagline: "Architectural graphite monochrome for zero-distraction focus",
    category: "Minimal",
    swatches: ["#18181b", "#3f3f46", "#71717a", "#f4f4f5"],
    primary: {
      50: "250 250 250", 100: "244 244 245", 200: "228 228 231", 300: "212 212 216",
      400: "161 161 170", 500: "113 113 122", 600: "63 63 70", 700: "45 45 50",
      800: "34 34 38", 900: "24 24 27", 950: "10 10 12",
    },
    gray: NEUTRAL_ZINC,
    surfaces: {
      canvas: "240 240 242", canvasDark: "10 10 12", shell: "251 251 252", shellMuted: "243 243 245",
      surfaceDark: "18 18 21", surfaceDarkMuted: "27 27 31", themeColorLight: "#f4f4f5", themeColorDark: "#0a0a0c",
    },
  },
  teal: {
    id: "teal",
    name: "Oceanic Teal",
    tagline: "Balanced Atlantic cyan-green built for long inbox sessions",
    category: "Modern SaaS",
    swatches: ["#114247", "#0f766e", "#2dd4bf", "#edf5f5"],
    primary: {
      50: "240 253 250", 100: "204 251 241", 200: "153 246 228", 300: "94 234 212",
      400: "45 212 191", 500: "20 184 166", 600: "15 118 110", 700: "17 94 89",
      800: "19 78 74", 900: "17 66 71", 950: "7 38 42",
    },
    gray: COOL_SLATE,
    surfaces: {
      canvas: "237 245 245", canvasDark: "9 19 22", shell: "248 253 253", shellMuted: "238 247 247",
      surfaceDark: "14 28 32", surfaceDarkMuted: "21 40 45", themeColorLight: "#edf5f5", themeColorDark: "#091316",
    },
  },
  amber: {
    id: "amber",
    name: "Amber Gold",
    tagline: "Warm executive bronze & honey with high contrast legibility",
    category: "Editorial",
    swatches: ["#542d0c", "#b45309", "#f59e0b", "#f7f3ec"],
    primary: {
      50: "255 251 235", 100: "254 243 199", 200: "253 230 138", 300: "252 211 77",
      400: "251 191 36", 500: "217 119 6", 600: "180 83 9", 700: "146 64 14",
      800: "120 53 15", 900: "84 45 12", 950: "48 24 6",
    },
    gray: WARM_GRAY,
    surfaces: {
      canvas: "245 240 232", canvasDark: "18 14 10", shell: "253 251 247", shellMuted: "246 241 233",
      surfaceDark: "26 20 15", surfaceDarkMuted: "36 28 21", themeColorLight: "#f7f3ec", themeColorDark: "#120e0a",
    },
  },
  crimson: {
    id: "crimson",
    name: "Crimson Bordeaux",
    tagline: "Distinguished editorial burgundy for hospitality & retail brands",
    category: "Editorial",
    swatches: ["#4c1022", "#9f1239", "#fb7185", "#f6eff1"],
    primary: {
      50: "255 241 242", 100: "255 228 230", 200: "254 205 211", 300: "253 164 175",
      400: "251 113 133", 500: "225 29 72", 600: "159 18 57", 700: "136 19 55",
      800: "109 17 46", 900: "76 16 34", 950: "46 8 20",
    },
    gray: WARM_GRAY,
    surfaces: {
      canvas: "246 239 241", canvasDark: "19 11 14", shell: "253 249 250", shellMuted: "246 240 242",
      surfaceDark: "28 16 21", surfaceDarkMuted: "38 23 29", themeColorLight: "#f6eff1", themeColorDark: "#130b0e",
    },
  },
  plum: {
    id: "plum",
    name: "Royal Plum",
    tagline: "Refined aubergine & studio violet for creative agencies",
    category: "Editorial",
    swatches: ["#3b1554", "#7e22ce", "#c084fc", "#f4eff8"],
    primary: {
      50: "250 245 255", 100: "243 232 255", 200: "233 213 255", 300: "216 180 254",
      400: "192 132 252", 500: "168 85 247", 600: "126 34 206", 700: "107 33 168",
      800: "88 28 135", 900: "59 21 84", 950: "35 10 54",
    },
    gray: NEUTRAL_ZINC,
    surfaces: {
      canvas: "244 239 248", canvasDark: "15 11 22", shell: "252 249 255", shellMuted: "245 240 250",
      surfaceDark: "23 17 34", surfaceDarkMuted: "33 25 48", themeColorLight: "#f4eff8", themeColorDark: "#0f0b16",
    },
  },
  nordic: {
    id: "nordic",
    name: "Nordic Slate",
    tagline: "Scandinavian steel blue & crisp overcast neutrals",
    category: "Minimal",
    swatches: ["#1e293b", "#334155", "#64748b", "#eef2f6"],
    primary: {
      50: "248 250 252", 100: "241 245 249", 200: "226 232 240", 300: "203 213 225",
      400: "148 163 184", 500: "100 116 139", 600: "61 82 110", 700: "46 64 89",
      800: "30 41 59", 900: "20 29 44", 950: "10 16 27",
    },
    gray: COOL_SLATE,
    surfaces: {
      canvas: "238 242 246", canvasDark: "11 16 24", shell: "249 251 253", shellMuted: "240 244 248",
      surfaceDark: "17 24 36", surfaceDarkMuted: "25 35 51", themeColorLight: "#eef2f6", themeColorDark: "#0b1018",
    },
  },
  terracotta: {
    id: "terracotta",
    name: "Terracotta Clay",
    tagline: "Sun-baked sienna & limestone crafted for hospitality",
    category: "Editorial",
    swatches: ["#522214", "#c2410c", "#fb923c", "#f6f0ec"],
    primary: {
      50: "255 247 237", 100: "255 237 213", 200: "254 215 170", 300: "253 186 116",
      400: "251 146 60", 500: "234 88 12", 600: "194 65 12", 700: "154 52 18",
      800: "124 45 18", 900: "82 34 20", 950: "48 18 9",
    },
    gray: WARM_GRAY,
    surfaces: {
      canvas: "246 240 236", canvasDark: "19 13 11", shell: "253 250 248", shellMuted: "246 240 236",
      surfaceDark: "28 19 16", surfaceDarkMuted: "39 27 23", themeColorLight: "#f6f0ec", themeColorDark: "#130d0b",
    },
  },
};

export const PALETTE_LIST: PaletteDefinition[] = Object.values(PALETTES);

export interface ThemePreset {
  id: string;
  name: string;
  description: string;
  mode: ThemeMode;
  palette: PaletteId;
  density: DensityMode;
  radius: RadiusMode;
  font: FontMode;
}

export const THEME_PRESETS: ThemePreset[] = [
  {
    id: "executive-parchment",
    name: "Executive Parchment",
    description: "Signature forest green on warm stone",
    mode: "light",
    palette: "emerald",
    density: "comfortable",
    radius: "rounded",
    font: "default",
  },
  {
    id: "midnight-terminal",
    name: "Midnight Ops",
    description: "Deep obsidian dark mode with crisp compact density",
    mode: "dark",
    palette: "sapphire",
    density: "compact",
    radius: "sharp",
    font: "inter",
  },
  {
    id: "linear-studio",
    name: "SaaS Studio",
    description: "Modern indigo workspace with balanced spacing",
    mode: "system",
    palette: "indigo",
    density: "comfortable",
    radius: "rounded",
    font: "inter",
  },
  {
    id: "minimal-architect",
    name: "Minimal Monochrome",
    description: "Pure graphite contrast with sharp architectural corners",
    mode: "light",
    palette: "obsidian",
    density: "compact",
    radius: "sharp",
    font: "default",
  },
  {
    id: "hospitality-warm",
    name: "Warm Hospitality",
    description: "Sunlit terracotta & spacious cards for restaurant teams",
    mode: "light",
    palette: "terracotta",
    density: "spacious",
    radius: "soft",
    font: "editorial",
  },
  {
    id: "ocean-night",
    name: "Atlantic Night",
    description: "Calm dark teal engineered for late-evening inbox work",
    mode: "dark",
    palette: "teal",
    density: "comfortable",
    radius: "rounded",
    font: "default",
  },
];

const STORAGE_KEY = "theme";
const PREFS_STORAGE_KEY = "sendersms.appearance.v1";

export interface AppearancePreferences {
  mode: ThemeMode;
  palette: PaletteId;
  density: DensityMode;
  radius: RadiusMode;
  font: FontMode;
}

const DEFAULT_PREFS: AppearancePreferences = {
  mode: "system",
  palette: "emerald",
  density: "comfortable",
  radius: "rounded",
  font: "default",
};

function resolveSystemTheme(): Theme {
  if (typeof window === "undefined") return "light";
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function readInitialPreferences(): AppearancePreferences {
  if (typeof window === "undefined") return DEFAULT_PREFS;
  try {
    const raw = window.localStorage.getItem(PREFS_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      return {
        mode: parsed.mode === "light" || parsed.mode === "dark" || parsed.mode === "system" ? parsed.mode : "system",
        palette: parsed.palette && PALETTES[parsed.palette as PaletteId] ? (parsed.palette as PaletteId) : "emerald",
        density: parsed.density === "compact" || parsed.density === "comfortable" || parsed.density === "spacious" ? parsed.density : "comfortable",
        radius: parsed.radius === "sharp" || parsed.radius === "rounded" || parsed.radius === "soft" ? parsed.radius : "rounded",
        font: parsed.font === "default" || parsed.font === "inter" || parsed.font === "editorial" || parsed.font === "mono" ? parsed.font : "default",
      };
    }
    const legacy = window.localStorage.getItem(STORAGE_KEY);
    if (legacy === "dark" || legacy === "light") {
      return { ...DEFAULT_PREFS, mode: legacy };
    }
  } catch {
    /* ignore blocked storage */
  }
  return DEFAULT_PREFS;
}

function persistPreferences(prefs: AppearancePreferences) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(PREFS_STORAGE_KEY, JSON.stringify(prefs));
    window.localStorage.setItem("sendify.palette", prefs.palette);
    if (prefs.mode === "light" || prefs.mode === "dark") {
      window.localStorage.setItem(STORAGE_KEY, prefs.mode);
    } else {
      window.localStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    /* ignore blocked storage */
  }
}

export function applyAppearanceToDocument(prefs: AppearancePreferences, resolvedTheme: Theme) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  const palette = PALETTES[prefs.palette] || PALETTES.emerald;

  root.classList.toggle("dark", resolvedTheme === "dark");
  root.classList.toggle("light", resolvedTheme === "light");
  root.style.colorScheme = resolvedTheme;

  root.dataset.palette = palette.id;
  root.dataset.density = prefs.density;
  root.dataset.radius = prefs.radius;
  root.dataset.font = prefs.font;

  const shades: ShadeKey[] = [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950];
  for (const s of shades) {
    root.style.setProperty(`--c-primary-${s}`, palette.primary[s]);
    root.style.setProperty(`--c-gray-${s}`, palette.gray[s]);
  }
  root.style.setProperty("--c-canvas", palette.surfaces.canvas);
  root.style.setProperty("--c-canvas-dark", palette.surfaces.canvasDark);
  root.style.setProperty("--c-shell", palette.surfaces.shell);
  root.style.setProperty("--c-shell-muted", palette.surfaces.shellMuted);
  root.style.setProperty("--c-surface-dark", palette.surfaces.surfaceDark);
  root.style.setProperty("--c-surface-dark-muted", palette.surfaces.surfaceDarkMuted);

  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute(
      "content",
      resolvedTheme === "dark" ? palette.surfaces.themeColorDark : palette.surfaces.themeColorLight
    );
}

/** Backwards-compatible helper for callers that paint light/dark directly. */
export function applyTheme(theme: Theme) {
  const prefs = readInitialPreferences();
  applyAppearanceToDocument({ ...prefs, mode: theme }, theme);
}

export interface ThemeContextValue {
  theme: Theme;
  mode: ThemeMode;
  isDark: boolean;
  palette: PaletteId;
  paletteDefinition: PaletteDefinition;
  density: DensityMode;
  radius: RadiusMode;
  font: FontMode;
  preferences: AppearancePreferences;
  setTheme: (theme: Theme) => void;
  setMode: (mode: ThemeMode) => void;
  setPalette: (palette: PaletteId) => void;
  setDensity: (density: DensityMode) => void;
  setRadius: (radius: RadiusMode) => void;
  setFont: (font: FontMode) => void;
  applyPreset: (preset: ThemePreset) => void;
  updatePreferences: (patch: Partial<AppearancePreferences>) => void;
  resetPreferences: () => void;
  resetAppearance: () => void;
  toggle: () => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

function useStandaloneThemeState(): ThemeContextValue {
  const [prefs, setPrefs] = useState<AppearancePreferences>(readInitialPreferences);
  const [osTheme, setOsTheme] = useState<Theme>(resolveSystemTheme);

  const resolvedTheme: Theme = prefs.mode === "system" ? osTheme : prefs.mode;

  useEffect(() => {
    applyAppearanceToDocument(prefs, resolvedTheme);
  }, [prefs, resolvedTheme]);

  useEffect(() => {
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!media) return;
    const onChange = (event: MediaQueryListEvent) => {
      setOsTheme(event.matches ? "dark" : "light");
    };
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  const updatePreferences = useCallback((patch: Partial<AppearancePreferences>) => {
    setPrefs((curr) => {
      const next: AppearancePreferences = { ...curr, ...patch };
      persistPreferences(next);
      return next;
    });
  }, []);

  const setTheme = useCallback((next: Theme) => updatePreferences({ mode: next }), [updatePreferences]);
  const setMode = useCallback((mode: ThemeMode) => updatePreferences({ mode }), [updatePreferences]);
  const setPalette = useCallback((palette: PaletteId) => updatePreferences({ palette }), [updatePreferences]);
  const setDensity = useCallback((density: DensityMode) => updatePreferences({ density }), [updatePreferences]);
  const setRadius = useCallback((radius: RadiusMode) => updatePreferences({ radius }), [updatePreferences]);
  const setFont = useCallback((font: FontMode) => updatePreferences({ font }), [updatePreferences]);

  const applyPreset = useCallback(
    (preset: ThemePreset) => {
      updatePreferences({
        mode: preset.mode,
        palette: preset.palette,
        density: preset.density,
        radius: preset.radius,
        font: preset.font,
      });
    },
    [updatePreferences]
  );

  const resetPreferences = useCallback(() => {
    persistPreferences(DEFAULT_PREFS);
    setPrefs(DEFAULT_PREFS);
  }, []);

  return useMemo<ThemeContextValue>(
    () => ({
      theme: resolvedTheme,
      mode: prefs.mode,
      isDark: resolvedTheme === "dark",
      palette: prefs.palette,
      paletteDefinition: PALETTES[prefs.palette] || PALETTES.emerald,
      density: prefs.density,
      radius: prefs.radius,
      font: prefs.font,
      preferences: prefs,
      setTheme,
      setMode,
      setPalette,
      setDensity,
      setRadius,
      setFont,
      applyPreset,
      updatePreferences,
      resetPreferences,
      resetAppearance: resetPreferences,
      toggle: () => setTheme(resolvedTheme === "dark" ? "light" : "dark"),
    }),
    [
      resolvedTheme,
      prefs,
      setTheme,
      setMode,
      setPalette,
      setDensity,
      setRadius,
      setFont,
      applyPreset,
      updatePreferences,
      resetPreferences,
    ]
  );
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const value = useStandaloneThemeState();
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  const fallback = useStandaloneThemeState();
  return context || fallback;
}

export default useTheme;
