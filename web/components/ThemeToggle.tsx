"use client";

import { useEffect, useState } from "react";
import { Monitor, Moon, Sun } from "lucide-react";
import { THEME_KEY } from "@/lib/theme";

type Theme = "light" | "dark" | "system";
const KEY = THEME_KEY;

function apply(t: Theme) {
  if (t === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = t;
  try { if (t === "system") localStorage.removeItem(KEY); else localStorage.setItem(KEY, t); } catch {}
}

const OPTIONS: { value: Theme; label: string; icon: typeof Sun }[] = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "system", label: "Match system", icon: Monitor },
];

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("system");
  useEffect(() => {
    const t = document.documentElement.dataset.theme;
    setTheme(t === "light" || t === "dark" ? t : "system");
  }, []);
  return (
    <div role="radiogroup" aria-label="Colour theme" className="flex rounded-lg border border-line bg-panel-2 p-0.5">
      {OPTIONS.map(({ value, label, icon: Icon }) => (
        <button key={value} role="radio" aria-checked={theme === value} title={label} aria-label={label}
          onClick={() => { setTheme(value); apply(value); }}
          className={`grid size-7 place-items-center rounded-md transition-colors ${theme === value ? "bg-panel text-ink shadow-sm" : "text-muted hover:text-ink"}`}>
          <Icon className="size-3.5" />
        </button>
      ))}
    </div>
  );
}
