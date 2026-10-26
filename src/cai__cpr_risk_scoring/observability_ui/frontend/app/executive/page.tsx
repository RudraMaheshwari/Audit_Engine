"use client";

import { useState } from "react";
import { useExecutive } from "@/lib/api";
import { useDateRange } from "@/components/date-range";
import { Section, KpiRow, Kpi, Panel, Skeleton, EmptyState, ErrorNote, ExportButton, fmtUsd, fmtInt } from "@/components/ui";
import { Bars, Donut, AreaTrend, SEVERITY } from "@/components/charts";

function Delta({ v }: { v: number | null }) {
  if (v == null) return <span className="text-muted">—</span>;
  const up = v >= 0;
  return <span className={up ? "text-[#28c76f]" : "text-accent"}>{up ? "▲" : "▼"} {Math.abs(v)}%</span>;
}

export default function Executive() {
  // Empty inputs → backend uses the env/default assumption; typing a value recomputes ROI server-side.
  const [hours, setHours] = useState("");
  const [rate, setRate] = useState("");
  const { qs } = useDateRange();
  const { data, error, isLoading } = useExecutive(hours, rate, qs);

  return (
    <>
      <Section title="Executive" subtitle="Business value — audits delivered, risk caught and the ROI of running this with AI." />
      {error ? <ErrorNote error={error} /> : isLoading || !data ? <Skeleton cols={4} /> : data.kpis.ai_spend_usd === 0 && data.kpis.audits_delivered === 0 ? (
        <EmptyState icon="📈" title="No audits yet" message="Executive value metrics appear here once audits have run." />
      ) : (
        <div className="flex flex-col gap-4">
          <KpiRow cols={4}>
            <Kpi label="Audits delivered" value={fmtInt(data.kpis.audits_delivered)} />
            <Kpi label="High-risk identified" value={fmtInt(data.kpis.high_risk_identified)} sub="compliance exposure surfaced" accent />
            <Kpi label="AI spend" value={fmtUsd(data.kpis.ai_spend_usd)} sub={`${fmtUsd(data.kpis.cost_per_audit)} / audit`} />
            <Kpi label="Estimated ROI" value={data.kpis.roi_pct != null ? `${data.kpis.roi_pct}%` : "—"} sub={`~${fmtInt(data.kpis.est_hours_saved)} analyst hrs saved`} />
          </KpiRow>

          {data.qoq?.quarter && (
            <div className="card px-4 py-2 text-xs text-muted">
              <span className="font-semibold text-text">{data.qoq.quarter} vs {data.qoq.prev_quarter}:</span>{" "}
              audits <Delta v={data.qoq.audits_delta_pct} /> · high-risk <Delta v={data.qoq.high_delta_pct} /> · AI spend <Delta v={data.qoq.spend_delta_pct} />
            </div>
          )}

          <KpiRow>
            <Kpi label="Findings / audit" value={data.kpis.findings_per_audit} sub="program effectiveness" />
            <Kpi label="High-risk rate" value={`${data.kpis.high_risk_rate_pct}%`} sub="of all findings" />
            <Kpi label="Scopes covered" value={fmtInt(data.kpis.scopes_covered)} sub="risk coverage" />
            <Kpi label="Recurrences (KRI)" value={fmtInt(data.kpis.recurrence_findings)} sub="repeat findings" />
            <Kpi label="Avg cycle" value={`${data.kpis.avg_cycle_minutes}m`} sub="per audit run" />
          </KpiRow>

          <div className="grid grid-cols-2 gap-4">
            <Panel title="AI spend vs. estimated manual cost">
              <Bars data={data.spend_vs_value.map((d: any) => ({ label: d.label, usd: d.usd }))} x="label" y="usd" h={300} color="#28c76f" />
            </Panel>
            <Panel title="Risk mix (findings by severity)">
              <Donut data={data.risk_mix} nameKey="risk_category" valueKey="findings" map={SEVERITY} h={300} />
            </Panel>
          </div>

          <div className="grid grid-cols-3 gap-4">
            <Kpi label="Total findings" value={fmtInt(data.kpis.total_findings)} sub="risk coverage" />
            <Kpi label="Cost per high-risk" value={data.kpis.cost_per_high_risk != null ? fmtUsd(data.kpis.cost_per_high_risk) : "—"} />
            <Kpi label="Estimated value" value={fmtUsd(data.kpis.est_value_usd)} sub="vs. manual effort" />
          </div>

          {data.quarterly?.length > 0 && (
            <>
              <div className="grid grid-cols-2 gap-4">
                <Panel title="Quarterly — audits & high-risk findings">
                  <AreaTrend data={data.quarterly} x="quarter" series={["audits", "high"]} h={280} />
                </Panel>
                <Panel title="Quarterly — AI spend (USD)">
                  <Bars data={data.quarterly} x="quarter" y="spend" h={280} color="#28c76f" />
                </Panel>
              </div>
              <Panel title="Quarterly detail">
                <div className="mb-2 flex justify-end"><ExportButton rows={data.quarterly} filename="executive_quarterly" /></div>
                <table className="w-full text-left text-sm">
                  <thead><tr className="text-muted">{["quarter", "audits", "findings", "high", "AI spend", "est. value", "ROI %"].map((c) => <th key={c} className="border-b border-border px-2 py-1.5 font-semibold">{c}</th>)}</tr></thead>
                  <tbody>
                    {data.quarterly.map((d: any, i: number) => (
                      <tr key={i} className="hover:bg-surface2">
                        <td className="border-b border-border px-2 py-1.5 font-medium">{d.quarter}</td>
                        <td className="border-b border-border px-2 py-1.5">{d.audits}</td>
                        <td className="border-b border-border px-2 py-1.5">{d.findings}</td>
                        <td className="border-b border-border px-2 py-1.5">{d.high}</td>
                        <td className="border-b border-border px-2 py-1.5">{fmtUsd(d.spend)}</td>
                        <td className="border-b border-border px-2 py-1.5">{fmtUsd(d.est_value)}</td>
                        <td className="border-b border-border px-2 py-1.5">{d.roi_pct != null ? `${d.roi_pct}%` : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Panel>
            </>
          )}

          {data.remediation && (
            <div className="grid grid-cols-3 gap-4">
              <Panel title="High-risk remediation status" className="col-span-2">
                <Donut data={data.remediation} nameKey="status" valueKey="count"
                  map={{ Open: "#ff4d4f", "In progress": "#ffa64d", Resolved: "#28c76f" }} h={280} />
              </Panel>
              <Kpi label="Unresolved high-risk" value={fmtInt(data.kpis.unresolved_high_risk)} sub="open + in progress — set status in Risk Overview" accent />
            </div>
          )}

          <Panel title="Top scope areas by findings">
            <Bars data={topScopes(data.top_scopes)} x="scope_area" y="findings" h={300} color="#5b8cff" />
          </Panel>

          <Panel title="ROI assumptions — tune these to your organisation">
            <div className="flex flex-wrap items-end gap-4">
              <div className="w-56">
                <div className="kpi-label mb-1">Manual hours per audit</div>
                <input className="input" type="number" min={0} step={1}
                  placeholder={String(data.assumptions.manual_hours_per_audit)}
                  value={hours} onChange={(e) => setHours(e.target.value)} />
              </div>
              <div className="w-56">
                <div className="kpi-label mb-1">Analyst cost per hour (USD)</div>
                <input className="input" type="number" min={0} step={5}
                  placeholder={String(data.assumptions.analyst_hourly_usd)}
                  value={rate} onChange={(e) => setRate(e.target.value)} />
              </div>
              {(hours || rate) && (
                <button className="btn" onClick={() => { setHours(""); setRate(""); }}>Reset to default</button>
              )}
            </div>
            <p className="mt-2 text-xs text-muted">
              {data.assumptions.note} Using {data.assumptions.manual_hours_per_audit} hours/audit ·{" "}
              {fmtUsd(data.assumptions.analyst_hourly_usd)}/hour — ROI above updates as you change these.
            </p>
          </Panel>
        </div>
      )}
    </>
  );
}

function topScopes(rows: any[]) {
  const total: Record<string, number> = {};
  rows.forEach((r) => (total[r.scope_area] = (total[r.scope_area] || 0) + Number(r.findings || 0)));
  return Object.entries(total).sort((a, b) => b[1] - a[1]).slice(0, 10).map(([scope_area, findings]) => ({ scope_area, findings }));
}
