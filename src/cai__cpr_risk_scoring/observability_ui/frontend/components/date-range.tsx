"use client";

import { createContext, useContext, useState } from "react";

type Range = { since: string; until: string };
type Ctx = Range & { set: (r: Partial<Range>) => void; reset: () => void; qs: string };

const DateCtx = createContext<Ctx>({ since: "", until: "", set: () => {}, reset: () => {}, qs: "" });

export function useDateRange() {
  return useContext(DateCtx);
}

export function DateRangeProvider({ children }: { children: React.ReactNode }) {
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const set = (r: Partial<Range>) => {
    if (r.since !== undefined) setSince(r.since);
    if (r.until !== undefined) setUntil(r.until);
  };
  const reset = () => { setSince(""); setUntil(""); };
  const p = new URLSearchParams();
  if (since) p.set("since", since);
  if (until) p.set("until", until);
  const qs = p.toString();
  return <DateCtx.Provider value={{ since, until, set, reset, qs }}>{children}</DateCtx.Provider>;
}

/** The compact control shown in the top bar. */
export function DateRangeControl() {
  const { since, until, set, reset } = useDateRange();
  return (
    <div className="flex items-center gap-1 text-xs text-muted">
      <input type="date" className="input px-2 py-1" value={since} max={until || undefined}
        onChange={(e) => set({ since: e.target.value })} title="From" />
      <span>→</span>
      <input type="date" className="input px-2 py-1" value={until} min={since || undefined}
        onChange={(e) => set({ until: e.target.value })} title="To" />
      {(since || until) && <button className="btn px-2 py-1" onClick={reset} title="Clear date range">✕</button>}
    </div>
  );
}
