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
          50: "#f0f6f2",
          100: "#dcebe1",
          200: "#bad8c5",
          300: "#8fbda5",
          400: "#5f9c80",
          500: "#3f8064",
          600: "#2a684e",
          700: "#215440",
          800: "#1a4534",
          900: "#123c2b",
          950: "#08251a",
        },
        // The lighter mint used by charts, the promo card glow and small accents.
        accent: {
          50: "#eef7f1",
          100: "#d9eddf",
          200: "#b7dcc4",
          300: "#8bc3a3",
          400: "#5fa982",
          500: "#3f8e67",
          600: "#2f7454",
          700: "#285e46",
          800: "#234c3a",
          900: "#1e3f31",
          950: "#0f241c",
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
          50: "#f8f8f6",
          100: "#f2f2ef",
          200: "#e7e8e4",
          300: "#d6d8d3",
          400: "#adb3ac",
          500: "#828b83",
          600: "#626c64",
          700: "#4c564e",
          800: "#343d36",
          900: "#202823",
          950: "#111712",
        },
        // Named surfaces. Flat keys on purpose: `@apply bg-canvas` resolves the
        // same way under PostCSS and the CLI.
        canvas: "#ececea",
        "canvas-dark": "#0c1410",
        shell: "#fafaf8",
        "shell-muted": "#f1f2ee",
        surface: "#ffffff",
        "surface-muted": "#f2f3ef",
        "surface-dark": "#121c16",
        "surface-dark-muted": "#18241d",
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
