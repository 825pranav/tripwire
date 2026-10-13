import { useEffect, useMemo, useState } from "react";
import { clock, getJson, price, type Alert, type Book, type LiveAlert, type TradeEvent } from "../api";
import { ScoreBar, TypeBadge } from "../components/Badges";
import { DepthLadder } from "../components/DepthLadder";
import { Panel } from "../components/Panel";
import { PriceChart } from "../components/PriceChart";
import type { LiveState } from "../useLive";

interface MarketResponse {
  trades: { ts_ns: number; price: number; qty: number; aggressor_side: "B" | "S"; buy_account: string; sell_account: string }[];
  book: { ts_ns: number; bids: [number, number][]; asks: [number, number][] } | null;
}

export function LiveView({ live, symbols, open }: { live: LiveState; symbols: string[]; open: (alertId: string) => void }) {
  const [symbol, setSymbol] = useState(symbols[0] ?? "");
  const [history, setHistory] = useState<MarketResponse | null>(null);
  const [recentAlerts, setRecentAlerts] = useState<LiveAlert[]>([]);

  // Alerts raised before the page was opened; live ones are added on top.
  useEffect(() => {
    getJson<Alert[]>("/api/alerts?limit=30")
      .then((rows) => setRecentAlerts(rows.map(({ emitted_ts_ns, ...a }) => ({ ...a, emitted_ts: emitted_ts_ns }))))
      .catch(() => setRecentAlerts([]));
  }, []);

  useEffect(() => {
    if (!symbol && symbols[0]) setSymbol(symbols[0]);
  }, [symbols, symbol]);

  useEffect(() => {
    if (!symbol) return;
    setHistory(null);
    getJson<MarketResponse>(`/api/market/${symbol}?trades=1500`).then(setHistory).catch(() => setHistory(null));
  }, [symbol]);

  const liveTrades: TradeEvent[] = live.trades[symbol] ?? [];
  const points = useMemo(() => {
    const past = (history?.trades ?? []).map((t) => ({ ts: t.ts_ns, price: t.price }));
    const lastPast = past.length ? past[past.length - 1].ts : 0;
    return [...past, ...liveTrades.filter((t) => t.ts > lastPast).map((t) => ({ ts: t.ts, price: t.price }))];
  }, [history, liveTrades]);

  const book: Book | null =
    live.books[symbol] ??
    (history?.book ? { ts: history.book.ts_ns, symbol, bids: history.book.bids, asks: history.book.asks } : null);
  const tape = useMemo(() => {
    const past = history?.trades ?? [];
    const lastPast = past.length ? past[past.length - 1].ts_ns : 0;
    const merged = [
      ...past.slice(-60).map((t) => ({ ...t, ts: t.ts_ns })),
      ...liveTrades.filter((t) => t.ts > lastPast),
    ];
    return merged.reverse().slice(0, 60);
  }, [history, liveTrades]);
  const alerts = useMemo(() => {
    const seen = new Set(live.alerts.map((a) => a.alert_id));
    return [...live.alerts, ...recentAlerts.filter((a) => !seen.has(a.alert_id))].slice(0, 50);
  }, [live.alerts, recentAlerts]);
  const last = points.length ? points[points.length - 1].price : null;
  const first = points.length ? points[0].price : null;

  return (
    <div className="grid h-full min-h-0 grid-cols-12 grid-rows-[auto_1fr] gap-2 p-2">
      <div className="col-span-12 flex items-center gap-2">
        {symbols.map((s) => (
          <button
            key={s}
            onClick={() => setSymbol(s)}
            className={`rounded px-3 py-1 font-mono text-xs ${s === symbol ? "bg-sky-500/20 text-sky-300 ring-1 ring-sky-500/50" : "bg-zinc-900 text-zinc-400 hover:text-zinc-200"}`}
          >
            {s}
            {live.trades[s]?.length ? <span className="ml-2 text-zinc-500">{price(live.trades[s][live.trades[s].length - 1].price)}</span> : null}
          </button>
        ))}
        {last != null && first != null && (
          <span className="ml-auto font-mono text-xs text-zinc-400">
            last <span className="text-zinc-100">{price(last)}</span>{" "}
            <span className={last >= first ? "text-emerald-400" : "text-rose-400"}>
              {last >= first ? "+" : ""}
              {((last - first) / 100).toFixed(2)}
            </span>{" "}
            over loaded window
          </span>
        )}
      </div>

      <Panel title={`Order book · ${symbol}`} className="col-span-3 row-span-1" right={book && <span className="font-mono text-[10px] text-zinc-500">{clock(book.ts)}</span>}>
        {book ? <DepthLadder bids={book.bids} asks={book.asks} depth={12} /> : <Empty text="waiting for book snapshots" />}
      </Panel>

      <div className="col-span-6 flex min-h-0 flex-col gap-2">
        <Panel title={`Price · ${symbol}`} className="flex-none">
          <PriceChart points={points} height={300} />
        </Panel>
        <Panel title="Trade tape" className="min-h-0 flex-1">
          <table className="w-full font-mono text-[11px]">
            <thead className="text-left text-[10px] uppercase text-zinc-500">
              <tr>
                <th className="pb-1">Time</th>
                <th>Price</th>
                <th className="text-right">Qty</th>
                <th className="pl-3">Aggressor</th>
                <th>Buyer</th>
                <th>Seller</th>
              </tr>
            </thead>
            <tbody>
              {tape.map((t, i) => (
                <tr key={`${t.ts}-${i}`} className="border-t border-zinc-800/60">
                  <td className="py-0.5 text-zinc-500">{clock(t.ts)}</td>
                  <td className={t.aggressor_side === "B" ? "text-emerald-400" : "text-rose-400"}>{price(t.price)}</td>
                  <td className="text-right">{t.qty.toLocaleString()}</td>
                  <td className="pl-3 text-zinc-400">{t.aggressor_side === "B" ? "buy" : "sell"}</td>
                  <td className="text-zinc-400">{t.buy_account}</td>
                  <td className="text-zinc-400">{t.sell_account}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!tape.length && <Empty text="no trades yet; start the simulator with `make sim`" />}
        </Panel>
      </div>

      <Panel title="Alert feed" className="col-span-3" right={<span className="font-mono text-[10px] text-zinc-500">{alerts.length} recent</span>}>
        <ul className="flex flex-col gap-1">
          {alerts.map((a) => (
            <li key={a.alert_id}>
              <button onClick={() => open(a.alert_id)} className="w-full rounded border border-zinc-800 bg-zinc-900 p-2 text-left hover:border-zinc-600">
                <div className="flex items-center justify-between">
                  <TypeBadge type={a.type} />
                  <ScoreBar score={a.score} />
                </div>
                <div className="mt-1 font-mono text-[11px] text-zinc-300">
                  {a.symbol} · {a.accounts.join(", ")}
                </div>
                <div className="font-mono text-[10px] text-zinc-500">{clock(a.emitted_ts)}</div>
              </button>
            </li>
          ))}
        </ul>
        {!alerts.length && <Empty text="alerts appear here as detectors fire" />}
      </Panel>
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <div className="p-4 text-center font-mono text-[11px] text-zinc-500">{text}</div>;
}
