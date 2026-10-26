"use client";

import Link from "next/link";
import { useTechnical } from "@/lib/api";
import { useDateRange } from "@/components/date-range";
import { Section, KpiRow, Kpi, Panel, Skeleton, EmptyState, ErrorNote, fmtUsd, fmtInt } from "@/components/ui";
import { Bars, AreaTrend } from "@/components/charts";

const shortT = (t: string) => (t ? t.slice(5, 16).replace("T", " ") : "");

export default function Operations() {
  const { qs } = useDateRange();
  const { data, error, isLoading } = useTechnical(qs);

  return (
    <>
      <Section title="Operations" subtitle="Pipeline health — runs, cost, tokens, latency and ingestion." />
      {error ? <ErrorNote error={error} /> : isLoading || !data ? <Skeleton /> : data.kpis.runs === 0 ? (
        <EmptyState title="No runs recorded yet" message="Runs appear here once the pipeline has executed with OBS_ENABLED=true." />
      ) : (
        <div className="flex flex-col gap-4">
          <KpiRow>
            <Kpi label="Runs" value={fmtInt(data.kpis.runs)} sub={`${data.kpis.completed} completed · ${data.kpis.failed} failed`} />
            <Kpi label="Total cost" value={fmtUsd(data.kpis.total_cost_usd)} sub="USD, all runs" accent />
            <Kpi label="Tokens" value={fmtInt(data.kpis.total_tokens)} sub="input + output" />
            <Kpi label="LLM calls" value={fmtInt(data.kpis.total_llm_calls)} />
            <Kpi label="Avg cost / run" value={fmtUsd(data.kpis.avg_cost_per_run)} />
          </KpiRow>

          <div className="grid grid-cols-3 gap-4">
            <Panel title="Cost per run (USD)" className="col-span-2">
              <Bars data={data.cost_series.map((d: any) => ({ t: shortT(d.t), value: d.value }))} x="t" y="value" h={300} color="#5b8cff" />
            </Panel>
            <Panel title="Cost by stage">
              <Bars data={data.llm_by_stage.map((d: any) => ({ stage: d.stage, cost: Number(d.cost_usd || 0) }))} x="stage" y="cost" h={300} color="#ff7a59" />
            </Panel>
          </div>

          <Panel title="Token volume over time">
            <AreaTrend data={data.token_series.map((d: any) => ({ t: shortT(d.t), input: d.input, output: d.output }))} x="t" series={["input", "output"]} h={260} />
          </Panel>

          <div className="grid grid-cols-2 gap-4">
            <Panel title="Avg latency by model (ms)">
              <Bars data={data.llm_by_model.map((d: any) => ({ model: d.model, latency: Math.round(Number(d.avg_latency_ms || 0)) }))} x="model" y="latency" h={280} color="#a78bfa" />
            </Panel>
            <Panel title="Chunks by index">
              <Bars data={data.ingestion_by_index.map((d: any) => ({ index: d.index_key, chunks: Number(d.chunks || 0) }))} x="index" y="chunks" h={280} color="#28c76f" />
            </Panel>
          </div>

          <Panel title="Recent runs">
            <Table rows={data.recent_runs} cols={["run_id", "auditor_id", "status", "started_at", "total_cost_usd", "total_tokens", "total_llm_calls"]} />
          </Panel>
        </div>
      )}
    </>
  );
}

function Table({ rows, cols }: { rows: any[]; cols: string[] }) {
  if (!rows?.length) return <div className="text-sm text-muted">No rows.</div>;
  return (
    <div className="overflow-auto">
      <table className="w-full text-left text-sm">
        <thead><tr className="text-muted">{cols.map((c) => <th key={c} className="border-b border-border px-2 py-1.5 font-semibold">{c}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="hover:bg-surface2">
              {cols.map((c) => (
                <td key={c} className="border-b border-border px-2 py-1.5">
                  {c === "run_id"
                    ? <Link href={`/runs/${r[c]}`} className="font-medium text-accent2 hover:underline">{String(r[c] ?? "").slice(0, 8)}…</Link>
                    : String(r[c] ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
