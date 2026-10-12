import { useEffect, useState } from "react";
import { clock, getJson, type Alert } from "../api";
import { ReportBadge, ScoreBar, TypeBadge } from "../components/Badges";

const TYPES = ["", "spoofing", "layering", "wash", "marking_close", "ml_anomaly"];

export function AlertsView({ symbols, open }: { symbols: string[]; open: (alertId: string) => void }) {
  const [type, setType] = useState("");
  const [symbol, setSymbol] = useState("");
  const [status, setStatus] = useState("");
  const [alerts, setAlerts] = useState<Alert[]>([]);

  useEffect(() => {
    const params = new URLSearchParams({ limit: "300" });
    if (type) params.set("type", type);
    if (symbol) params.set("symbol", symbol);
    if (status) params.set("status", status);
    const load = () => getJson<Alert[]>(`/api/alerts?${params}`).then(setAlerts).catch(() => undefined);
    load();
    const id = window.setInterval(load, 5000);
    return () => window.clearInterval(id);
  }, [type, symbol, status]);

  const select = (value: string, set: (v: string) => void, options: string[], label: string) => (
    <label className="flex items-center gap-1.5 text-[11px] text-zinc-400">
      {label}
      <select value={value} onChange={(e) => set(e.target.value)} className="rounded border border-zinc-700 bg-zinc-900 px-1.5 py-0.5 font-mono text-[11px] text-zinc-200">
        {options.map((o) => (
          <option key={o} value={o}>
            {o || "all"}
          </option>
        ))}
      </select>
    </label>
  );

  return (
    <div className="flex h-full min-h-0 flex-col gap-2 p-2">
      <div className="flex gap-4">
        {select(type, setType, TYPES, "type")}
        {select(symbol, setSymbol, ["", ...symbols], "symbol")}
        {select(status, setStatus, ["", "open", "reported"], "status")}
        <span className="ml-auto font-mono text-[11px] text-zinc-500">{alerts.length} alerts</span>
      </div>
      <div className="min-h-0 flex-1 overflow-auto rounded border border-zinc-800">
        <table className="w-full font-mono text-[11px]">
          <thead className="sticky top-0 bg-zinc-900 text-left text-[10px] uppercase text-zinc-500">
            <tr>
              <th className="px-2 py-1.5">Emitted</th>
              <th>Type</th>
              <th>Symbol</th>
              <th>Accounts</th>
              <th>Score</th>
              <th>Source</th>
              <th>Orders</th>
              <th>Report</th>
            </tr>
          </thead>
          <tbody>
            {alerts.map((a) => (
              <tr key={a.alert_id} onClick={() => open(a.alert_id)} className="cursor-pointer border-t border-zinc-800/70 hover:bg-zinc-800/40">
                <td className="px-2 py-1 text-zinc-400">{clock(a.emitted_ts_ns)}</td>
                <td>
                  <TypeBadge type={a.type} />
                </td>
                <td>{a.symbol}</td>
                <td className="text-zinc-300">{a.accounts.join(", ")}</td>
                <td>
                  <ScoreBar score={a.score} />
                </td>
                <td className="text-zinc-500">{a.source}</td>
                <td className="text-zinc-500">{a.order_ids.length}</td>
                <td>
                  <ReportBadge status={a.report_status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
