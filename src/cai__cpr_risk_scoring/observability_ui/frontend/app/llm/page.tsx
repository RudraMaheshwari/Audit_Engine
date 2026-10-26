"use client";

import { useState } from "react";
import { useLlm, useQuestions, useQuestionCalls } from "@/lib/api";
import { Section, KpiRow, Kpi, Panel, Tabs, Skeleton, EmptyState, ErrorNote, ExportButton, fmtUsd, fmtInt, fmtTok } from "@/components/ui";
import { Bars, StackedBars, SEVERITY } from "@/components/charts";

const DIMS = [
  { key: "by_stage", field: "stage", label: "Stage" },
  { key: "by_agent", field: "agent_name", label: "Agent" },
  { key: "by_model", field: "model", label: "Model" },
  { key: "by_prompt", field: "prompt_name", label: "Prompt" },
];

export default function Llm() {
  const [tab, setTab] = useState("Breakdown");
  return (
    <>
      <Section title="LLM analytics" subtitle="Every model call — cost, latency, input vs output tokens, by stage/agent/model/prompt, and per question." />
      <Tabs tabs={["Breakdown", "Per-question"]} active={tab} onChange={setTab} />
      {tab === "Breakdown" ? <Breakdown /> : <PerQuestion />}
    </>
  );
}

function Breakdown() {
  const { data, error, isLoading } = useLlm();
  const [dim, setDim] = useState(DIMS[0]);
  if (error) return <ErrorNote error={error} />;
  if (isLoading || !data) return <Skeleton />;
  const t = data.totals || {};
  if (!t.calls) return <EmptyState icon="🤖" title="No LLM calls yet" message="Model-call observability appears after a run." />;

  const rows = (data[dim.key] || []).map((r: any) => ({
    name: r[dim.field], input: Number(r.input_tokens || 0), output: Number(r.output_tokens || 0),
    cost: Number(r.cost_usd || 0), latency: Math.round(Number(r.avg_latency_ms || 0)),
    calls: Number(r.calls || 0), errors: Number(r.errors || 0),
    cached: Number(r.cached_tokens || 0), reasoning: Number(r.reasoning_tokens || 0),
  }));

  return (
    <div className="flex flex-col gap-4">
      <KpiRow>
        <Kpi label="Calls" value={fmtInt(t.calls)} sub={`${fmtInt(t.errors)} errors`} />
        <Kpi label="Input tokens" value={fmtTok(t.input_tokens)} sub="prompt" />
        <Kpi label="Output tokens" value={fmtTok(t.output_tokens)} sub="completion" accent />
        <Kpi label="Cached / reasoning" value={`${fmtTok(t.cached_tokens)} / ${fmtTok(t.reasoning_tokens)}`} />
        <Kpi label="Cost" value={fmtUsd(t.cost_usd)} sub={`${Math.round(Number(t.avg_latency_ms || 0))}ms avg`} />
      </KpiRow>

      <div className="flex gap-1">
        {DIMS.map((d) => (
          <button key={d.key} onClick={() => setDim(d)} className={`navlink ${dim.key === d.key ? "navlink-active" : ""}`}>{d.label}</button>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-4">
        <Panel title={`Input vs output tokens by ${dim.label.toLowerCase()}`}>
          <StackedBars data={rows} x="name" series={["input", "output"]} h={320} />
        </Panel>
        <Panel title={`Cost by ${dim.label.toLowerCase()}`}>
          <Bars data={rows} x="name" y="cost" h={320} color="#ff7a59" />
        </Panel>
      </div>

      <Panel title={`By ${dim.label.toLowerCase()}`}>
        <div className="mb-2 flex justify-end"><ExportButton rows={rows} filename={`llm_by_${dim.field}`} /></div>
        <table className="w-full text-left text-sm">
          <thead><tr className="text-muted">{["name", "calls", "input", "output", "cached", "reasoning", "cost", "latency", "errors"].map((c) => <th key={c} className="border-b border-border px-2 py-1.5 font-semibold">{c}</th>)}</tr></thead>
          <tbody>
            {rows.map((r: any, i: number) => (
              <tr key={i} className="hover:bg-surface2">
                <td className="border-b border-border px-2 py-1.5 font-medium">{r.name}</td>
                <td className="border-b border-border px-2 py-1.5">{fmtInt(r.calls)}</td>
                <td className="border-b border-border px-2 py-1.5">{fmtInt(r.input)}</td>
                <td className="border-b border-border px-2 py-1.5">{fmtInt(r.output)}</td>
                <td className="border-b border-border px-2 py-1.5">{fmtInt(r.cached)}</td>
                <td className="border-b border-border px-2 py-1.5">{fmtInt(r.reasoning)}</td>
                <td className="border-b border-border px-2 py-1.5">{fmtUsd(r.cost)}</td>
                <td className="border-b border-border px-2 py-1.5">{r.latency}ms</td>
                <td className="border-b border-border px-2 py-1.5">{r.errors}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </div>
  );
}

function PerQuestion() {
  const { data, error, isLoading } = useQuestions(1000);
  const [open, setOpen] = useState<string | null>(null);
  if (error) return <ErrorNote error={error} />;
  if (isLoading || !data) return <Skeleton cols={3} />;
  const rows = data.questions || [];
  if (!rows.length) return <EmptyState icon="❓" title="No question rows" message="Per-question observability appears after a run." />;

  return (
    <Panel title={`${rows.length} questions (highest cost first) — click to expand its LLM chain + findings`}>
      <div className="mb-2 flex justify-end">
        <ExportButton rows={rows} filename="per_question_analytics"
          columns={["auditor_id", "run_id", "question_index", "assigned_scope", "final_risk_category", "likelihood", "impact", "num_llm_calls", "input_tokens", "output_tokens", "total_tokens", "cost_usd", "status"]} />
      </div>
      <div className="overflow-auto">
        <table className="w-full text-left text-sm">
          <thead><tr className="text-muted">{["", "auditor", "#", "scope", "category", "L×I", "calls", "input", "output", "cost"].map((c) => <th key={c} className="border-b border-border px-2 py-1.5 font-semibold">{c}</th>)}</tr></thead>
          <tbody>
            {rows.map((q: any) => (
              <QuestionRow key={q.question_uid} q={q} open={open === q.question_uid} onToggle={() => setOpen(open === q.question_uid ? null : q.question_uid)} />
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function QuestionRow({ q, open, onToggle }: { q: any; open: boolean; onToggle: () => void }) {
  const { data } = useQuestionCalls(open ? q.question_uid : null);
  const cat = String(q.final_risk_category || "");
  return (
    <>
      <tr className="cursor-pointer hover:bg-surface2" onClick={onToggle}>
        <td className="border-b border-border px-2 py-1.5">{open ? "▾" : "▸"}</td>
        <td className="border-b border-border px-2 py-1.5">{q.auditor_id}</td>
        <td className="border-b border-border px-2 py-1.5">{q.question_index}</td>
        <td className="max-w-[220px] truncate border-b border-border px-2 py-1.5">{q.assigned_scope}</td>
        <td className="border-b border-border px-2 py-1.5"><span style={{ color: SEVERITY[cat] || undefined }}>{cat}</span></td>
        <td className="border-b border-border px-2 py-1.5">{q.likelihood ?? "-"}×{q.impact ?? "-"}</td>
        <td className="border-b border-border px-2 py-1.5">{q.num_llm_calls}</td>
        <td className="border-b border-border px-2 py-1.5">{fmtInt(q.input_tokens)}</td>
        <td className="border-b border-border px-2 py-1.5">{fmtInt(q.output_tokens)}</td>
        <td className="border-b border-border px-2 py-1.5">{fmtUsd(q.cost_usd)}</td>
      </tr>
      {open && (
        <tr>
          <td colSpan={10} className="border-b border-border bg-surface2 px-3 py-3">
            {!data ? <div className="text-xs text-muted">Loading the question’s LLM chain…</div> : (
              <div className="flex flex-col gap-2">
                <div className="text-xs text-muted">{q.source_file} · confidence {q.llm_confidence ?? "-"} (derived {q.derived_confidence ?? "-"}) · transactional {String(q.transactional_invoked)}</div>

                {data.findings?.length > 0 && (
                  <div className="rounded-lg border border-border p-2">
                    <div className="kpi-label mb-1">Findings produced by this question ({data.findings.length})</div>
                    <table className="w-full text-left text-xs">
                      <thead><tr className="text-muted">{["origin", "scope_area", "risk_type", "risk_category", "L×I", "score", "recurrence"].map((h) => <th key={h} className="px-2 py-1 font-semibold">{h}</th>)}</tr></thead>
                      <tbody>
                        {data.findings.map((f: any, k: number) => (
                          <tr key={k}>
                            <td className="px-2 py-1">{f.origin}</td>
                            <td className="px-2 py-1">{f.scope_area}</td>
                            <td className="max-w-[300px] truncate px-2 py-1">{f.risk_type}</td>
                            <td className="px-2 py-1"><span style={{ color: SEVERITY[f.risk_category] || undefined }}>{f.risk_category}</span></td>
                            <td className="px-2 py-1">{f.likelihood ?? "-"}×{f.impact ?? "-"}</td>
                            <td className="px-2 py-1">{f.matrix_score ?? "-"}</td>
                            <td className="px-2 py-1">{f.is_recurrence ? "yes" : "no"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {(data.calls || []).map((c: any, i: number) => (
                  <details key={i} className="rounded-lg border border-border p-2 text-sm">
                    <summary className="cursor-pointer select-none">
                      <span className="font-semibold">{c.stage || c.agent_name}</span>
                      <span className="text-muted">{"  ·  "}{c.model}{"  ·  "}{c.prompt_name}{" v"}{c.prompt_version}{"  ·  in "}{fmtInt(c.input_tokens)}{" / out "}{fmtInt(c.output_tokens)}{" tok  ·  $"}{Number(c.cost_usd || 0).toFixed(4)}{"  ·  "}{c.latency_ms}{"ms"}</span>
                      {c.status && c.status !== "ok" && <span className="ml-2 text-accent">{c.status}</span>}
                    </summary>
                    <div className="mt-2 grid grid-cols-2 gap-2">
                      <div><div className="kpi-label mb-1">Input (prompt)</div><pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded bg-surface p-2 text-xs">{c.request_payload || "—"}</pre></div>
                      <div><div className="kpi-label mb-1">Output (response)</div><pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded bg-surface p-2 text-xs">{c.response_payload || "—"}</pre></div>
                    </div>
                  </details>
                ))}
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}
