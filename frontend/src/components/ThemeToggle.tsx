import { Moon, Sun } from "lucide-react";
import { useTheme } from "../hooks/useTheme";

/**
 * APPEARANCE CONTROLS
 *
 * Two shapes of the same idea:
 *  * `ThemeToggle` — the labelled Light/Dark switch used at the top of the
 *    Overview page and inside Settings → Appearance. It says what it does, so
 *    nobody has to guess what the moon means.
 *  * `ThemeIconButton` — the compact round version for tight headers.
 *
 * Both read and write the one theme store (`hooks/useTheme`), so flipping the
 * switch in Settings instantly changes every open screen — and vice versa.
 */

const OPTIONS = [
  { value: "light" as const, label: "Light", icon: Sun },
  { value: "dark" as const, label: "Dark", icon: Moon },
];

export function ThemeToggle({
  className = "",
  showLabel = true,
}: {
  className?: string;
  showLabel?: boolean;
}) {
  const { theme, setTheme } = useTheme();

  return (
    <div
      className={`inline-flex items-center gap-1 rounded-full border border-gray-200/80 dark:border-gray-700/70 bg-white/80 dark:bg-gray-800/70 p-1 shadow-card backdrop-blur ${className}`}
      role="radiogroup"
      aria-label="Appearance"
    >
      {OPTIONS.map((option) => {
        const active = theme === option.value;
        const Icon = option.icon;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => setTheme(option.value)}
            title={`${option.label} mode`}
            className={`inline-flex items-center gap-1.5 rounded-full px-3 min-h-[36px] text-[12.5px] font-semibold
                        transition-all duration-200 ease-gentle active:scale-[0.97] focus-visible:ring-2
                        focus-visible:ring-primary-500/50 ${
                          active
                            ? "bg-primary-900 text-white shadow-sm shadow-primary-950/20 dark:bg-primary-600"
                            : "text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white hover:bg-gray-100/80 dark:hover:bg-gray-700/50"
                        }`}
          >
            <Icon size={14} strokeWidth={active ? 2.2 : 1.8} />
            {showLabel && <span>{option.label}</span>}
          </button>
        );
      })}
    </div>
  );
}

export function ThemeIconButton({ className = "" }: { className?: string }) {
  const { isDark, toggle } = useTheme();
  return (
    <button
      type="button"
      onClick={toggle}
      className={`relative icon-btn overflow-hidden ${className}`}
      title={isDark ? "Switch to light mode" : "Switch to dark mode"}
      aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
    >
      <span className="relative block w-[17px] h-[17px]">
        <Sun
          size={17}
          className={`absolute inset-0 transition-all duration-300 ease-spring ${
            isDark ? "opacity-0 rotate-90 scale-50" : "opacity-100 rotate-0 scale-100"
          }`}
        />
        <Moon
          size={17}
          className={`absolute inset-0 transition-all duration-300 ease-spring ${
            isDark ? "opacity-100 rotate-0 scale-100" : "opacity-0 -rotate-90 scale-50"
          }`}
        />
      </span>
    </button>
  );
}

export default ThemeToggle;
