"use client";

import { useTheme } from "next-themes";
import { Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  if (!mounted) return <span className="btn w-[88px]" />;
  const dark = theme === "dark";
  return (
    <button className="btn inline-flex items-center gap-1.5" onClick={() => setTheme(dark ? "light" : "dark")}>
      {dark ? <Sun size={15} /> : <Moon size={15} />}
      {dark ? "Light" : "Dark"}
    </button>
  );
}
