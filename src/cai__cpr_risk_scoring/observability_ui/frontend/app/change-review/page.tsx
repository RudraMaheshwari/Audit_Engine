"use client";

import { useState } from "react";
import { api, useConfig } from "@/lib/api";
import { Section, KpiRow, Kpi, Panel, EmptyState, ErrorNote, ExportButton, ExportJsonButton, fmtInt } from "@/components/ui";
import { Donut } from "@/components/charts";
import clsx from "clsx";

const SOURCES = [
  { base: "AI_Risk_Summary", label: "Risk Analysis (Output C)" },
  { base: "AI_Scope", label: "AI Scope" },
];
const STATUS_COLORS: Record<string, string> = {
  identical: "#28c76f", minor: "#9be7b4", modified: "#ff4d4f", added: "#5b8cff", removed: "#7f8aa3",
};

export default function ChangeReview() {
  const [source, setSource] = useState("AI_Risk_Summary");
  const [engagement, setEngagement] = useState("");
  const [threshold, setThreshold] = useState(0.98);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<any>(null);
  const { data: cfg } = useConfig();

  const origPreview = cfg?.catalog && engagement ? `${cfg.catalog}.${cfg.output_schema}.${source}_${engagement}` : "";
  const copyPreview = origPreview ? `${origPreview}_copy` : "";

  async function run() {
    setError(null); setLoading(true); setResult(null);
    try {
      const r = await api.compare({ source, engagement, threshold, summarize: true });
      if (r.error) setError(r.error); else setResult(r);
    } catch (e: any) { setError(e.message); } finally { setLoading(false); }
  }

  const s = result?.summary;
  const statusCounts = result ? countBy(result.rows, "status") : [];
  const modified = result?.rows?.filter((r: any) => r.status === "modified") ?? [];

  return (
    <>
      <Section title="Change Review" subtitle="What reviewers changed in the chat-edited _copy — scored by meaning and summarised for retraining." />

      <Panel className="mb-4">
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <div className="kpi-label mb-1">Dataset</div>
            <div className="flex gap-1">
              {SOURCES.map((x) => (
                <button key={x.base} onClick={() => setSource(x.base)}
                  className={clsx("navlink", source === x.base && "navlink-active")}>{x.label}</button>
              ))}
            </div>
          </div>
          <div className="w-44">
            <div className="kpi-label mb-1">Engagement</div>
            <input className="input" placeholder="e.g. 1010" value={engagement} onChange={(e) => setEngagement(e.target.value)} />
          </div>
          <div className="w-64">
            <div className="kpi-label mb-1">Change threshold — {threshold.toFixed(2)}</div>
            <input type="range" min={0.8} max={1} step={0.01} value={threshold} onChange={(e) => setThreshold(+e.target.value)} className="w-full accent-[rgb(var(--accent))]" />
          </div>
          <button className="btn-primary" disabled={loading || !engagement} onClick={run}>
            {loading ? "Comparing…" : "Compare"}
          </button>
        </div>
        {!result && origPreview && (
          <div className="mt-2 text-xs text-muted">will compare <code>{origPreview}</code> ↔ <code>{copyPreview}</code></div>
        )}
        {result?.tables && (
          <div className="mt-2 text-xs text-muted">original <code>{result.tables.original}</code> · copy <code>{result.tables.copy}</code></div>
        )}
      </Panel>

      {error ? <ErrorNote error={error} /> : !result ? (
        <EmptyState icon="🔬" title="Ready to compare" message="Pick a dataset + engagement, then Compare. Edited rows are embedded, scored, and summarised by the LLM." />
      ) : (
        <div className="flex flex-col gap-4">
          <KpiRow>
            <Kpi label="Common rows" value={fmtInt(s.common)} />
            <Kpi label="Modified" value={fmtInt(s.modified)} accent />
            <Kpi label="Minor" value={fmtInt(s.minor)} />
            <Kpi label="Added / Removed" value={`${s.added} / ${s.removed}`} />
            <Kpi label="Mean similarity" value={s.mean_similarity != null ? s.mean_similarity : "—"} />
          </KpiRow>

          <div className="grid grid-cols-3 gap-4">
            <Panel title="Row status">
              <Donut data={statusCounts} nameKey="status" valueKey="count" map={STATUS_COLORS} h={300} />
            </Panel>
            <Panel title="Retraining signal" className="col-span-2">
              {modified.length === 0 ? (
                <div className="text-sm text-muted">No modified rows — the two tables are semantically equivalent.</div>
              ) : (
                <div className="text-sm text-muted">
                  {fmtInt(modified.length)} rows were changed. Each is summarised below with a change type and a
                  plain-English description of what the reviewer altered — ready to fold into a feedback/training set.
                  {s.truncated && <span className="ml-1 text-accent">(embedded first {s.embedded} changed rows)</span>}
                </div>
              )}
            </Panel>
          </div>

          {modified.length > 0 && (
            <div className="flex justify-end gap-2">
              <ExportButton label="Retraining CSV" filename={`change_review_${source}_${engagement}`}
                rows={modified.flatMap((r: any) =>
                  (r.diffs?.length ? r.diffs : [{ column: "", before: "", after: "" }]).map((d: any) => ({
                    key: r.key, change_type: r.change_type || "", summary: r.summary || "",
                    similarity: r.similarity ?? "", field: d.column, before: d.before, after: d.after,
                  })))} />
              <ExportJsonButton label="Retraining JSON" filename={`change_review_${source}_${engagement}`}
                data={{
                  dataset: `${source}_${engagement}`, tables: result.tables, summary: s,
                  changes: modified.map((r: any) => ({
                    key: r.key, change_type: r.change_type || "", summary: r.summary || "",
                    similarity: r.similarity ?? null, diffs: r.diffs,
                  })),
                }} />
            </div>
          )}

          {modified.map((r: any) => (
            <div key={r.key} className="card p-4">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <span className="font-bold">Row {r.key}</span>
                <span className="chip">similarity {r.similarity ?? "—"}</span>
                {r.change_type && <span className="chip border-accent/50 text-accent">{r.change_type}</span>}
              </div>
              {r.summary && <p className="mb-3 text-sm">{r.summary}</p>}
              <div className="overflow-auto">
                <table className="w-full text-left text-sm">
                  <thead><tr className="text-muted">{["field", "before", "after"].map((c) => <th key={c} className="border-b border-border px-2 py-1.5 font-semibold">{c}</th>)}</tr></thead>
                  <tbody>
                    {r.diffs.map((d: any, i: number) => (
                      <tr key={i} className="align-top">
                        <td className="border-b border-border px-2 py-1.5 font-medium">{d.column}</td>
                        <td className="border-b border-border px-2 py-1.5 text-muted line-through decoration-accent/40">{d.before}</td>
                        <td className="border-b border-border px-2 py-1.5">{d.after}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

function countBy(rows: any[], key: string) {
  const m: Record<string, number> = {};
  rows.forEach((r) => (m[r[key]] = (m[r[key]] || 0) + 1));
  return Object.entries(m).map(([k, v]) => ({ status: k, count: v }));
}
