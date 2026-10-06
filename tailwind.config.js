/**
 * ONE PALETTE, DECLARED ONCE.
 *
 * The app used to speak in six dialects at the same time — blue buttons next to
 * green badges next to purple panels next to amber alerts — which is what makes
 * a screen feel stitched together instead of designed. Everything now comes
 * from this file:
 *
 *  * ``primary``  — the brand. A single blue→indigo family, matching the logo
 *    (the gradient in BrandMark/favicon goes 600 → accent 600), used for every
 *    button, link, active state, focus ring and highlight in the app.
 *  * ``accent``   — the indigo end of that same family. Only for gradients and
 *    the few places that need a second brand tone.
 *  * ``success`` / ``warning`` / ``danger`` — status meanings, not decoration.
 *    A green dot has to mean "delivered", an amber one "waiting", never "this
 *    team likes green".
 *  * ``gray``     — a cooled, slightly blue-tinted neutral ramp. Every legacy
 *    ``bg-gray-*``/``text-gray-*`` class in the app resolves through this, so
 *    hundreds of existing screens pick up the same surface tones at once.
 *
 * Dark mode is a class on <html> and mirrors the same scale, so a component
 * written once looks deliberate in both.
 */
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./frontend/index.html", "./frontend/src/**/*.{js,ts,jsx,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        primary: {
          50: "#eef4ff",
          100: "#dae5ff",
          200: "#bcd0ff",
          300: "#93b1ff",
          400: "#6488fd",
          500: "#4163f6",
          600: "#2563eb",
          700: "#1d4ed8",
          800: "#1e40b0",
          900: "#1e3a8a",
          950: "#16265c",
        },
        accent: {
          50: "#eef1ff",
          100: "#e0e5ff",
          200: "#c7cfff",
          300: "#a5aeff",
          400: "#8484fc",
          500: "#6d63f5",
          600: "#4f46e5",
          700: "#4338ca",
          800: "#372fa8",
          900: "#312e81",
          950: "#1e1b4b",
        },
        success: {
          50: "#ecfdf5",
          100: "#d1fae5",
          200: "#a7f3d0",
          300: "#6ee7b7",
          400: "#34d399",
          500: "#10b981",
          600: "#059669",
          700: "#047857",
          800: "#065f46",
          900: "#064e3b",
          950: "#022c22",
        },
        warning: {
          50: "#fffbeb",
          100: "#fef3c7",
          200: "#fde68a",
          300: "#fcd34d",
          400: "#fbbf24",
          500: "#f59e0b",
          600: "#d97706",
          700: "#b45309",
          800: "#92400e",
          900: "#78350f",
          950: "#451a03",
        },
        danger: {
          50: "#fef2f2",
          100: "#fee2e2",
          200: "#fecaca",
          300: "#fca5a5",
          400: "#f87171",
          500: "#ef4444",
          600: "#dc2626",
          700: "#b91c1c",
          800: "#991b1b",
          900: "#7f1d1d",
          950: "#450a0a",
        },
        // Cooled neutral ramp. Replaces Tailwind's stock grays app-wide, which
        // is why the whole UI shifts to one surface tone without a single
        // component being rewritten.
        gray: {
          50: "#f7f9fc",
          100: "#eff3f9",
          200: "#e3e9f2",
          300: "#cdd7e5",
          400: "#98a6bd",
          500: "#66748c",
          600: "#4c5a70",
          700: "#374357",
          800: "#212a3a",
          900: "#141b28",
          950: "#0b1018",
        },
        // Surfaces, named so intent is readable in the markup. Flat keys on
        // purpose: `@apply bg-canvas` has to resolve identically under the
        // Vite/PostCSS pipeline and the CLI, and nested colour objects are the
        // one shape where those two disagree.
        canvas: "#f7f9fc",
        "canvas-dark": "#0b1018",
        surface: "#ffffff",
        "surface-muted": "#f2f5fa",
        "surface-dark": "#151d2c",
        "surface-dark-muted": "#1c2637",
      },
      borderRadius: {
        xl: "0.875rem",
        "2xl": "1.125rem",
        "3xl": "1.5rem",
      },
      boxShadow: {
        // Soft, blue-tinted depth instead of flat black — reads as one product.
        card: "0 1px 2px rgba(16, 24, 40, 0.04), 0 1px 3px rgba(16, 24, 40, 0.06)",
        raised: "0 4px 12px rgba(16, 24, 40, 0.08), 0 1px 3px rgba(16, 24, 40, 0.06)",
        pop: "0 12px 32px rgba(16, 24, 40, 0.14), 0 2px 8px rgba(16, 24, 40, 0.08)",
        glow: "0 0 0 4px rgba(37, 99, 235, 0.14)",
      },
      keyframes: {
        "fade-in": {
          from: { opacity: "0" },
          to: { opacity: "1" },
        },
        "fade-up": {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "fade-left": {
          from: { opacity: "0", transform: "translateX(-6px)" },
          to: { opacity: "1", transform: "translateX(0)" },
        },
        "pop-in": {
          "0%": { opacity: "0", transform: "scale(0.96) translateY(-4px)" },
          "60%": { opacity: "1" },
          "100%": { opacity: "1", transform: "scale(1) translateY(0)" },
        },
        "slide-up": {
          from: { opacity: "0", transform: "translateY(12px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: {
          "100%": { transform: "translateX(100%)" },
        },
        "gentle-pulse": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.55" },
        },
      },
      animation: {
        // Short and small on purpose: an app used all day should feel alive,
        // not busy.
        "fade-in": "fade-in 180ms ease-out both",
        "fade-up": "fade-up 220ms cubic-bezier(0.22, 1, 0.36, 1) both",
        "fade-left": "fade-left 200ms cubic-bezier(0.22, 1, 0.36, 1) both",
        "pop-in": "pop-in 160ms cubic-bezier(0.22, 1, 0.36, 1) both",
        "slide-up": "slide-up 240ms cubic-bezier(0.22, 1, 0.36, 1) both",
        shimmer: "shimmer 1.6s infinite",
        "gentle-pulse": "gentle-pulse 2s ease-in-out infinite",
      },
      transitionTimingFunction: {
        gentle: "cubic-bezier(0.22, 1, 0.36, 1)",
      },
    },
  },
  plugins: [],
};
