"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import clsx from "clsx";
import { Activity, Layers, Bot, ShieldAlert, Briefcase, GitCompare, RefreshCw } from "lucide-react";
import { ThemeToggle } from "./theme-toggle";
import { DateRangeControl } from "./date-range";
import { api } from "@/lib/api";
import { mutate } from "swr";

const LINKS = [
  { href: "/", label: "Operations", icon: Activity },
  { href: "/runs", label: "Runs", icon: Layers },
  { href: "/llm", label: "LLM", icon: Bot },
  { href: "/risk", label: "Risk Overview", icon: ShieldAlert },
  { href: "/executive", label: "Executive", icon: Briefcase },
  { href: "/change-review", label: "Change Review", icon: GitCompare },
];

export function Nav() {
  const path = usePathname();
  const router = useRouter();
  async function refresh() {
    await fetch(`${api.base}/api/refresh`, { method: "POST" }).catch(() => {});
    await mutate(() => true); // revalidate all SWR keys
    router.refresh();
  }
  return (
    <header className="card mb-4 flex items-center justify-between gap-4 px-4 py-2.5">
      <Link href="/" className="flex items-center gap-2 font-extrabold tracking-tight">
        <span className="h-2.5 w-2.5 rounded-full bg-accent shadow-[0_0_14px] shadow-accent" />
        AI Auditor <span className="text-sm font-semibold text-muted">· Observability</span>
      </Link>
      <nav className="flex items-center gap-1">
        {LINKS.map(({ href, label, icon: Icon }) => {
          const active = href === "/" ? path === "/" : path.startsWith(href);
          return (
            <Link key={href} href={href} className={clsx("navlink inline-flex items-center gap-1.5", active && "navlink-active")}>
              <Icon size={15} /> {label}
            </Link>
          );
        })}
      </nav>
      <div className="flex items-center gap-2">
        <DateRangeControl />
        <button className="btn inline-flex items-center gap-1.5" onClick={refresh}><RefreshCw size={15} /> Refresh</button>
        <ThemeToggle />
      </div>
    </header>
  );
}
