import { useEffect, useMemo, useState } from "react";
import { clock, getJson, price, sideLabel, type Alert, type CaseReport, type ReplayData } from "../api";
import { ReportBadge, ScoreBar, TypeBadge } from "../components/Badges";
import { DepthLadder } from "../components/DepthLadder";
import { Panel } from "../components/Panel";
import { PriceChart } from "../components/PriceChart";

interface Detail {
  alert: Alert;
  report: CaseReport | null;
}

type ActivityRow = { ts: number; order_id: number; kind: string; side: "B" | "S"; qty: number; price: number | null; account: string };

export function CaseView({ alertId, back }: { alertId: string; back: () => void }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [replay, setReplay] = useState<ReplayData | null>(null);
  const [cursor, setCursor] = useState(0);
  const [selected, setSelected] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDetail(null);
    setReplay(null);
    const load = () =>
      getJson<Detail>(`/api/alerts/${alertId}`)
        .then(setDetail)
        .catch((e) => setError(String(e)));
    load();
    getJson<ReplayData>(`/api/alerts/${alertId}/replay`)
      .then((r) => {
        setReplay(r);
        setCursor(Math.max(0, r.snapshots.length - 1));
      })
      .catch((e) => setError(String(e)));
    // poll until the LLM report lands
    const id = window.setInterval(load, 5000);
    return () => window.clearInterval(id);
  }, [alertId]);

  const activity: ActivityRow[] = useMemo(() => {
    if (!replay) return [];
    const accounts = new Set(detail?.alert.accounts ?? []);
    const rows: ActivityRow[] = [
      ...replay.orders.map((o) => ({ ts: o.ts_ns, order_id: o.order_id, kind: o.order_type === "M" ? "market" : "limit", side: o.side, qty: o.qty, price: o.price, account: o.account })),
      ...replay.cancels.map((c) => ({ ts: c.ts_ns, order_id: c.order_id, kind: "cancel", side: c.side, qty: c.qty, price: c.price, account: c.account })),
      ...replay.trades.flatMap((t) => [
        ...(accounts.has(t.buy_account) ? [{ ts: t.ts_ns, order_id: t.buy_order_id, kind: "fill", side: "B" as const, qty: t.qty, price: t.price, account: t.buy_account }] : []),
        ...(accounts.has(t.sell_account) ? [{ ts: t.ts_ns, order_id: t.sell_order_id, kind: "fill", side: "S" as const, qty: t.qty, price: t.price, account: t.sell_account }] : []),
      ]),
    ];
    return rows.sort((a, b) => a.ts - b.ts);
  }, [replay, detail]);

  if (error) return <div className="p-4 font-mono text-sm text-rose-400">{error}</div>;
  if (!detail) return <div className="p-4 font-mono text-sm text-zinc-500">loading…</div>;

  const { alert, report } = detail;
  const alerted = new Set(alert.order_ids);
  const evidenceIds = new Set(report?.report.evidence.map((e) => e.order_id) ?? []);
  const snap = replay?.snapshots[cursor];
  const cursorNs = snap?.ts_ns;
  const selectedPrices = new Set(activity.filter((r) => r.order_id === selected && r.price != null).map((r) => r.price as number));
  const markers = activity
    .filter((r) => alerted.has(r.order_id) && (r.kind === "limit" || r.kind === "fill"))
    .map((r) => ({ ts: r.ts, side: r.side, text: r.kind === "fill" ? "fill" : `${r.qty}` }));
  const jumpTo = (orderId: number) => {
    setSelected(orderId);
    const row = activity.find((r) => r.order_id === orderId);
    if (row && replay) {
      const idx = replay.snapshots.findIndex((s) => s.ts_ns >= row.ts);
      setCursor(idx === -1 ? replay.snapshots.length - 1 : idx);
    }
  };

  return (
    <div className="grid h-full min-h-0 grid-cols-12 content-start gap-2 overflow-auto p-2">
      <div className="col-span-12 flex items-center gap-3">
        <button onClick={back} className="rounded bg-zinc-800 px-2 py-1 font-mono text-[11px] text-zinc-300 hover:bg-zinc-700">
          ← back
        </button>
        <TypeBadge type={alert.type} />
        <span className="font-mono text-sm text-zinc-100">{alert.symbol}</span>
        <span className="font-mono text-xs text-zinc-400">{alert.accounts.join(", ")}</span>
        <ScoreBar score={alert.score} />
        <span className="font-mono text-[11px] text-zinc-500">
          window {clock(alert.window_start_ns)} → {clock(alert.window_end_ns)} · {alert.source}
        </span>
        <span className="ml-auto font-mono text-[10px] text-zinc-600">{alert.alert_id}</span>
      </div>

      <div className="col-span-5 flex min-h-0 flex-col gap-2">
        <Panel title="Case report" right={<ReportBadge status={report?.status} />}>
          {!report && <p className="font-mono text-[11px] text-zinc-500">The investigator has not written a report for this alert yet.</p>}
          {report && (
            <div className="flex flex-col gap-3 text-[13px] leading-relaxed">
              <p className="text-zinc-100">{report.report.summary}</p>
              <p className="text-zinc-300">{report.report.behavior_narrative}</p>
              <div className="flex gap-4 font-mono text-[11px] text-zinc-400">
                <span>confidence: <span className="text-zinc-200">{report.report.confidence}</span></span>
                <span>action: <span className="text-zinc-200">{report.report.recommended_action.replaceAll("_", " ")}</span></span>
              </div>
              <div className="font-mono text-[10px] text-zinc-500">
                {report.model} · {report.citations_valid}/{report.citations_total} citations valid · {report.attempts} attempt{report.attempts === 1 ? "" : "s"} · {(report.latency_ms / 1000).toFixed(1)} s
              </div>
              {report.validation_errors.length > 0 && (
                <ul className="rounded border border-amber-500/30 bg-amber-500/5 p-2 font-mono text-[10px] text-amber-300">
                  {report.validation_errors.map((e) => (
                    <li key={e}>{e}</li>
                  ))}
                </ul>
              )}
              <table className="w-full font-mono text-[11px]">
                <thead className="text-left text-[10px] uppercase text-zinc-500">
                  <tr>
                    <th>Order</th>
                    <th>Event</th>
                    <th>t+ms</th>
                    <th>Side</th>
                    <th className="text-right">Qty</th>
                    <th className="text-right">Price</th>
                    <th className="pl-2">Note</th>
                  </tr>
                </thead>
                <tbody>
                  {report.report.evidence.map((e, i) => (
                    <tr key={i} className={`border-t border-zinc-800/60 ${selected === e.order_id ? "bg-amber-500/10" : ""}`}>
                      <td>
                        <button onClick={() => jumpTo(e.order_id)} className="text-sky-400 underline decoration-dotted hover:text-sky-300">
                          {e.order_id}
                        </button>
                      </td>
                      <td>{e.event}</td>
                      <td className="text-zinc-500">{e.t_ms.toLocaleString()}</td>
                      <td className={e.side === "B" ? "text-emerald-400" : "text-rose-400"}>{sideLabel(e.side)}</td>
                      <td className="text-right">{e.qty.toLocaleString()}</td>
                      <td className="text-right">{price(e.price)}</td>
                      <td className="pl-2 text-zinc-400">{e.note}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div>
                <h3 className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">Regulation references</h3>
                <ul className="flex flex-col gap-1 text-[11px]">
                  {report.regulations.references.map((r) => (
                    <li key={r.citation}>
                      <span className="text-zinc-400">[{r.jurisdiction}]</span> <span className="text-zinc-200">{r.citation}</span>
                      <span className="text-zinc-500"> · {r.summary}</span>
                    </li>
                  ))}
                </ul>
                <p className="mt-1 text-[10px] italic text-zinc-500">{report.regulations.disclaimer}</p>
              </div>
            </div>
          )}
        </Panel>
        <Panel title="Detector features">
          <div className="grid grid-cols-2 gap-x-4 font-mono text-[11px]">
            {Object.entries(alert.features).map(([k, v]) => (
              <div key={k} className="flex justify-between border-b border-zinc-800/60 py-0.5">
                <span className="text-zinc-500">{k}</span>
                <span className="text-zinc-200">{v}</span>
              </div>
            ))}
          </div>
        </Panel>
      </div>

      <div className="col-span-7 flex min-h-0 flex-col gap-2">
        <Panel title="Order book replay" right={snap && <span className="font-mono text-[10px] text-amber-300">{clock(snap.ts_ns)}</span>}>
          {replay && replay.snapshots.length > 0 ? (
            <div className="flex flex-col gap-2">
              <input
                type="range"
                min={0}
                max={replay.snapshots.length - 1}
                value={cursor}
                onChange={(e) => setCursor(Number(e.target.value))}
                className="w-full accent-amber-400"
              />
              <div className="grid grid-cols-5 gap-3">
                <div className="col-span-2">{snap && <DepthLadder bids={snap.bids} asks={snap.asks} depth={8} highlightPrices={selectedPrices} />}</div>
                <div className="col-span-3">
                  <PriceChart points={replay.trades.map((t) => ({ ts: t.ts_ns, price: t.price }))} markers={markers} cursorNs={cursorNs} height={230} />
                </div>
              </div>
            </div>
          ) : (
            <p className="font-mono text-[11px] text-zinc-500">no book snapshots stored for this window</p>
          )}
        </Panel>
        <Panel title="Account activity in window" className="min-h-[220px] flex-1">
          <table className="w-full font-mono text-[11px]">
            <thead className="sticky top-0 bg-zinc-900 text-left text-[10px] uppercase text-zinc-500">
              <tr>
                <th>Time</th>
                <th>Order</th>
                <th>Account</th>
                <th>Event</th>
                <th>Side</th>
                <th className="text-right">Qty</th>
                <th className="text-right">Price</th>
              </tr>
            </thead>
            <tbody>
              {activity.map((r, i) => {
                const future = cursorNs != null && r.ts > cursorNs;
                return (
                  <tr
                    key={i}
                    onClick={() => jumpTo(r.order_id)}
                    className={`cursor-pointer border-t border-zinc-800/60 ${future ? "opacity-35" : ""} ${selected === r.order_id ? "bg-amber-500/10" : ""}`}
                  >
                    <td className="text-zinc-500">{clock(r.ts)}</td>
                    <td className={alerted.has(r.order_id) ? "text-amber-300" : evidenceIds.has(r.order_id) ? "text-sky-300" : "text-zinc-300"}>{r.order_id}</td>
                    <td className="text-zinc-400">{r.account}</td>
                    <td>{r.kind}</td>
                    <td className={r.side === "B" ? "text-emerald-400" : "text-rose-400"}>{sideLabel(r.side)}</td>
                    <td className="text-right">{r.qty.toLocaleString()}</td>
                    <td className="text-right">{price(r.price)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="mt-1 font-mono text-[10px] text-zinc-600">amber = orders that triggered the alert · blue = cited in the report · faded = after the replay cursor</p>
        </Panel>
      </div>
    </div>
  );
}
