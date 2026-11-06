/**
 * ONE PALETTE, DECLARED ONCE — the "forest & parchment" system.
 *
 * The reference design (2026 refresh) is a calm, warm product surface:
 *
 *  * a near-black forest green (#0d3322) for every primary action, the active
 *    navigation pill and the hero stat card;
 *  * a sage tint (#dfe9e3) for the one selected row in the sidebar;
 *  * a warm parchment neutral ramp instead of blue-grey — this is what makes
 *    white cards feel soft rather than clinical;
 *  * generous 24–40px radii and barely-there shadows.
 *
 * ``primary`` is the whole brand family, so the hundreds of existing
 * ``bg-primary-600`` / ``text-primary-700`` classes across the app pick up the
 * new identity without a single page being rewritten. ``gray`` is the warmed
 * neutral that every legacy surface class resolves through.
 *
 * Dark mode remains a class on <html>; it mirrors the same green family so a
 * component is legible in both without a second stylesheet.
 */
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./frontend/index.html", "./frontend/src/**/*.{js,ts,jsx,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      // Small phones still get a labelled button where there is room; the
      // default sm breakpoint (640px) leaves too little space beside an icon.
      screens: { xs: "420px" },
      fontFamily: {
        sans: [
          "-apple-system", "BlinkMacSystemFont", "SF Pro Display", "SF Pro Text",
          "Segoe UI", "Inter", "Roboto", "Helvetica Neue", "Arial", "sans-serif",
        ],
      },
      colors: {
        primary: {
          50: "rgb(var(--c-primary-50, 240 246 242) / <alpha-value>)",
          100: "rgb(var(--c-primary-100, 220 235 225) / <alpha-value>)",
          200: "rgb(var(--c-primary-200, 186 216 197) / <alpha-value>)",
          300: "rgb(var(--c-primary-300, 143 189 165) / <alpha-value>)",
          400: "rgb(var(--c-primary-400, 95 156 128) / <alpha-value>)",
          500: "rgb(var(--c-primary-500, 63 128 100) / <alpha-value>)",
          600: "rgb(var(--c-primary-600, 42 104 78) / <alpha-value>)",
          700: "rgb(var(--c-primary-700, 33 84 64) / <alpha-value>)",
          800: "rgb(var(--c-primary-800, 26 69 52) / <alpha-value>)",
          900: "rgb(var(--c-primary-900, 18 60 43) / <alpha-value>)",
          950: "rgb(var(--c-primary-950, 8 37 26) / <alpha-value>)",
        },
        // The lighter mint / tint family mirrors the active brand palette.
        accent: {
          50: "rgb(var(--c-primary-50, 238 247 241) / <alpha-value>)",
          100: "rgb(var(--c-primary-100, 217 237 223) / <alpha-value>)",
          200: "rgb(var(--c-primary-200, 183 220 196) / <alpha-value>)",
          300: "rgb(var(--c-primary-300, 139 195 163) / <alpha-value>)",
          400: "rgb(var(--c-primary-400, 95 169 130) / <alpha-value>)",
          500: "rgb(var(--c-primary-500, 63 142 103) / <alpha-value>)",
          600: "rgb(var(--c-primary-600, 47 116 84) / <alpha-value>)",
          700: "rgb(var(--c-primary-700, 40 94 70) / <alpha-value>)",
          800: "rgb(var(--c-primary-800, 35 76 58) / <alpha-value>)",
          900: "rgb(var(--c-primary-900, 30 63 49) / <alpha-value>)",
          950: "rgb(var(--c-primary-950, 15 36 28) / <alpha-value>)",
        },
        // Status colours are meanings, not decoration. Deliberately muted: the
        // reference uses olive for "in progress", mint for "completed" and a
        // dusty rose for "pending", never a saturated traffic light.
        success: {
          50: "#f1f6ec",
          100: "#e3ecd4",
          200: "#c9dba9",
          300: "#a8c47c",
          400: "#88ab56",
          500: "#6b8f3d",
          600: "#54742e",
          700: "#425a26",
          800: "#354821",
          900: "#2c3c1e",
          950: "#16210d",
        },
        warning: {
          50: "#fbf7e9",
          100: "#f7efcd",
          200: "#efdf9c",
          300: "#e5ca65",
          400: "#dbb440",
          500: "#c99a2a",
          600: "#ac7a21",
          700: "#8a5a1e",
          800: "#734a20",
          900: "#623e1f",
          950: "#39200d",
        },
        danger: {
          50: "#fbeff1",
          100: "#f7dfe3",
          200: "#efc3ca",
          300: "#e29aa7",
          400: "#d16e81",
          500: "#bf5167",
          600: "#aa3d54",
          700: "#8e3045",
          800: "#772b3d",
          900: "#672838",
          950: "#391217",
        },
        // Warm parchment neutral. Every legacy gray-* class resolves here, so
        // the entire app warms up at once.
        gray: {
          50: "rgb(var(--c-gray-50, 248 248 246) / <alpha-value>)",
          100: "rgb(var(--c-gray-100, 242 242 239) / <alpha-value>)",
          200: "rgb(var(--c-gray-200, 231 232 228) / <alpha-value>)",
          300: "rgb(var(--c-gray-300, 214 216 211) / <alpha-value>)",
          400: "rgb(var(--c-gray-400, 173 179 172) / <alpha-value>)",
          500: "rgb(var(--c-gray-500, 130 139 131) / <alpha-value>)",
          600: "rgb(var(--c-gray-600, 98 108 100) / <alpha-value>)",
          700: "rgb(var(--c-gray-700, 76 86 78) / <alpha-value>)",
          800: "rgb(var(--c-gray-800, 52 61 54) / <alpha-value>)",
          900: "rgb(var(--c-gray-900, 32 40 35) / <alpha-value>)",
          950: "rgb(var(--c-gray-950, 17 23 18) / <alpha-value>)",
        },
        // Named surfaces. Flat keys on purpose: `@apply bg-canvas` resolves the
        // same way under PostCSS and the CLI.
        canvas: "rgb(var(--c-canvas, 236 236 234) / <alpha-value>)",
        "canvas-dark": "rgb(var(--c-canvas-dark, 12 20 16) / <alpha-value>)",
        shell: "rgb(var(--c-shell, 250 250 248) / <alpha-value>)",
        "shell-muted": "rgb(var(--c-shell-muted, 241 242 238) / <alpha-value>)",
        surface: "#ffffff",
        "surface-muted": "rgb(var(--c-shell-muted, 242 243 239) / <alpha-value>)",
        "surface-dark": "rgb(var(--c-surface-dark, 18 28 22) / <alpha-value>)",
        "surface-dark-muted": "rgb(var(--c-surface-dark-muted, 24 36 29) / <alpha-value>)",
      },
      borderRadius: {
        xl: "0.875rem",
        "2xl": "1.25rem",
        "3xl": "1.75rem",
        "4xl": "2.25rem",
        "5xl": "2.75rem",
      },
      boxShadow: {
        card: "0 1px 2px rgba(16, 38, 27, 0.03), 0 2px 10px -2px rgba(16, 38, 27, 0.05)",
        raised: "0 10px 26px -12px rgba(16, 38, 27, 0.22), 0 2px 8px -2px rgba(16, 38, 27, 0.08)",
        pop: "0 24px 60px -24px rgba(10, 30, 20, 0.30), 0 6px 18px -6px rgba(10, 30, 20, 0.12)",
        shell: "0 30px 80px -40px rgba(15, 40, 28, 0.35), 0 2px 10px rgba(15, 40, 28, 0.04)",
        glow: "0 0 0 4px rgba(42, 104, 78, 0.16)",
        "inner-soft": "inset 0 1px 0 rgba(255, 255, 255, 0.55)",
      },
      keyframes: {
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        "fade-up": {
          from: { opacity: "0", transform: "translateY(10px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "fade-left": {
          from: { opacity: "0", transform: "translateX(-8px)" },
          to: { opacity: "1", transform: "translateX(0)" },
        },
        "pop-in": {
          "0%": { opacity: "0", transform: "scale(0.965) translateY(-4px)" },
          "60%": { opacity: "1" },
          "100%": { opacity: "1", transform: "scale(1) translateY(0)" },
        },
        "slide-up": {
          from: { opacity: "0", transform: "translateY(16px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: { "100%": { transform: "translateX(100%)" } },
        "gentle-pulse": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.55" },
        },
        // Bars grow from the baseline like the reference chart.
        "bar-grow": {
          from: { transform: "scaleY(0.04)" },
          to: { transform: "scaleY(1)" },
        },
        float: {
          "0%, 100%": { transform: "translateY(0)" },
          "50%": { transform: "translateY(-5px)" },
        },
        "sheet-in": {
          from: { opacity: "0", transform: "translateY(24px) scale(0.985)" },
          to: { opacity: "1", transform: "translateY(0) scale(1)" },
        },
        "count-in": {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        // Short, spring-like and small. An app used all day should feel alive,
        // not busy.
        "fade-in": "fade-in 220ms ease-out both",
        "fade-up": "fade-up 420ms cubic-bezier(0.22, 1, 0.36, 1) both",
        "fade-left": "fade-left 380ms cubic-bezier(0.22, 1, 0.36, 1) both",
        "pop-in": "pop-in 220ms cubic-bezier(0.22, 1, 0.36, 1) both",
        "slide-up": "slide-up 420ms cubic-bezier(0.22, 1, 0.36, 1) both",
        shimmer: "shimmer 1.6s infinite",
        "gentle-pulse": "gentle-pulse 2.4s ease-in-out infinite",
        "bar-grow": "bar-grow 700ms cubic-bezier(0.22, 1, 0.36, 1) both",
        float: "float 6s ease-in-out infinite",
        "sheet-in": "sheet-in 320ms cubic-bezier(0.22, 1, 0.36, 1) both",
        "count-in": "count-in 300ms cubic-bezier(0.22, 1, 0.36, 1) both",
      },
      transitionTimingFunction: {
        gentle: "cubic-bezier(0.22, 1, 0.36, 1)",
        spring: "cubic-bezier(0.34, 1.56, 0.64, 1)",
      },
    },
  },
  plugins: [],
};
