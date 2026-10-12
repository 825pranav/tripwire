import { useEffect, useState } from "react";
import { getJson } from "../api";
import { Panel } from "../components/Panel";

type TypeMetrics = { precision: number | null; recall: number | null; f1: number | null; alerts: number; detected: number; episodes: number };

interface EvalRun {
  days: number;
  seeds: [number, number];
  events: number;
  throughput: Record<string, number>;
  methods: Record<string, Record<string, TypeMetrics>>;
  citations?: Record<string, number | string | null>;
}

interface Metrics {
  live: {
    alerts_by_type: { type: string; source: string; n: number }[];
    reports: { status: string; n: number; citations: number; valid: number; median_latency_ms: number }[];
    totals: { orders: number; trades: number; alerts: number };
  };
  eval: Record<string, EvalRun>;
}

const fmt = (v: number | null) => (v == null ? "-" : v.toFixed(3));

export function MetricsView() {
  const [m, setM] = useState<Metrics | null>(null);
  useEffect(() => {
    getJson<Metrics>("/api/metrics").then(setM).catch(() => undefined);
  }, []);
  if (!m) return <div className="p-4 font-mono text-sm text-zinc-500">loading…</div>;
  const run = m.eval.full_run ?? m.eval.eval_10days;

  return (
    <div className="grid grid-cols-12 gap-2 p-2">
      <Panel title="This session" className="col-span-4">
        <div className="flex flex-col gap-3 font-mono text-[11px]">
          <div className="flex gap-4 text-zinc-300">
            <span>{m.live.totals.orders.toLocaleString()} orders</span>
            <span>{m.live.totals.trades.toLocaleString()} trades</span>
            <span>{m.live.totals.alerts.toLocaleString()} alerts</span>
          </div>
          <table>
            <tbody>
              {m.live.alerts_by_type.map((r) => (
                <tr key={r.type + r.source} className="border-t border-zinc-800/60">
                  <td className="py-0.5 text-zinc-400">{r.type}</td>
                  <td className="text-zinc-500">{r.source}</td>
                  <td className="text-right text-zinc-200">{r.n}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <table>
            <thead className="text-left text-[10px] uppercase text-zinc-500">
              <tr>
                <th>Reports</th>
                <th className="text-right">n</th>
                <th className="text-right">citations valid</th>
                <th className="text-right">median s</th>
              </tr>
            </thead>
            <tbody>
              {m.live.reports.map((r) => (
                <tr key={r.status} className="border-t border-zinc-800/60">
                  <td className="py-0.5 text-zinc-400">{r.status}</td>
                  <td className="text-right">{r.n}</td>
                  <td className="text-right">
                    {r.valid}/{r.citations}
                  </td>
                  <td className="text-right">{(r.median_latency_ms / 1000).toFixed(1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      {run && (
        <Panel title={`Offline eval · ${run.days} simulated days · seeds ${run.seeds[0]}-${run.seeds[1]}`} className="col-span-8">
          <table className="w-full font-mono text-[11px]">
            <thead className="text-left text-[10px] uppercase text-zinc-500">
              <tr>
                <th>Method</th>
                <th>Type</th>
                <th className="text-right">Precision</th>
                <th className="text-right">Recall</th>
                <th className="text-right">F1</th>
                <th className="text-right">Episodes</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(run.methods).flatMap(([method, types]) =>
                Object.entries(types).map(([kind, t]) => (
                  <tr key={method + kind} className={`border-t border-zinc-800/60 ${kind === "overall" ? "text-zinc-100" : "text-zinc-400"}`}>
                    <td className="py-0.5">{method}</td>
                    <td>{kind}</td>
                    <td className="text-right">{fmt(t.precision)}</td>
                    <td className="text-right">{fmt(t.recall)}</td>
                    <td className="text-right">{fmt(t.f1)}</td>
                    <td className="text-right">
                      {t.detected}/{t.episodes}
                    </td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
          <p className="mt-2 font-mono text-[10px] text-zinc-500">
            Simulated data: thresholds were tuned on separate simulated seeds; these numbers describe the simulator, not real markets.
          </p>
        </Panel>
      )}
    </div>
  );
}
