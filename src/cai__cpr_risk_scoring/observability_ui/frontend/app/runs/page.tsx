"use client";

import { useState } from "react";
import Link from "next/link";
import { useRunsList } from "@/lib/api";
import { Section, Panel, Skeleton, EmptyState, ErrorNote, ExportButton, fmtUsd, fmtInt } from "@/components/ui";

const COLS = ["run_id", "auditor_id", "status", "started_at", "duration_ms", "findings_high",
  "findings_medium", "findings_low", "total_cost_usd", "total_tokens", "total_llm_calls"];

export default function Runs() {
  const { data, error, isLoading } = useRunsList(300);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("all");

  const rows = (data?.runs ?? []).filter((r: any) => {
    if (status !== "all" && r.status !== status) return false;
    if (q && !`${r.auditor_id} ${r.run_id}`.toLowerCase().includes(q.toLowerCase())) return false;
    return true;
  });

  return (
    <>
      <Section title="Runs" subtitle="Every audit run — filter, then click a run to open its full trace (questions, LLM calls, findings, ingestion)." />
      {error ? <ErrorNote error={error} /> : isLoading || !data ? <Skeleton cols={3} /> : !data.runs?.length ? (
        <EmptyState icon="🧵" title="No runs recorded" message="Runs appear here once the pipeline runs with OBS_ENABLED=true." />
      ) : (
        <Panel>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <input className="input w-64" placeholder="Search auditor / run id…" value={q} onChange={(e) => setQ(e.target.value)} />
            <select className="input w-40" value={status} onChange={(e) => setStatus(e.target.value)}>
              {["all", "completed", "running", "failed", "partial"].map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <span className="text-xs text-muted">{rows.length} of {data.runs.length}</span>
            <div className="ml-auto"><ExportButton rows={rows} filename="runs" columns={COLS} /></div>
          </div>
          <div className="overflow-auto">
            <table className="w-full text-left text-sm">
              <thead><tr className="text-muted">{COLS.map((c) => <th key={c} className="border-b border-border px-2 py-1.5 font-semibold">{c}</th>)}</tr></thead>
              <tbody>
                {rows.map((r: any, i: number) => (
                  <tr key={i} className="hover:bg-surface2">
                    {COLS.map((c) => (
                      <td key={c} className="border-b border-border px-2 py-1.5">
                        {c === "run_id"
                          ? <Link href={`/runs/${r[c]}`} className="font-medium text-accent2 hover:underline">{String(r[c] ?? "").slice(0, 8)}…</Link>
                          : c === "total_cost_usd" ? fmtUsd(Number(r[c] || 0))
                          : c === "total_tokens" ? fmtInt(Number(r[c] || 0))
                          : String(r[c] ?? "")}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </>
  );
}
