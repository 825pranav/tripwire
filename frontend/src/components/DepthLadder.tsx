import type { Level } from "../api";
import { price } from "../api";

interface Props {
  bids: Level[];
  asks: Level[];
  depth?: number;
  highlightPrices?: Set<number>;
}

// Asks on top (best ask lowest), bids below; bar width shows size relative to the max.
export function DepthLadder({ bids, asks, depth = 10, highlightPrices }: Props) {
  const a = asks.slice(0, depth).reverse();
  const b = bids.slice(0, depth);
  const max = Math.max(1, ...a.map((l) => l[1]), ...b.map((l) => l[1]));
  const spread = asks[0] && bids[0] ? asks[0][0] - bids[0][0] : null;

  const row = ([px, qty]: Level, side: "bid" | "ask") => (
    <div
      key={`${side}-${px}`}
      className={`relative grid grid-cols-2 px-2 py-[1px] font-mono text-xs ${
        highlightPrices?.has(px) ? "outline outline-1 outline-amber-400" : ""
      }`}
    >
      <div
        className={`absolute inset-y-0 right-0 ${side === "bid" ? "bg-emerald-500/20" : "bg-rose-500/20"}`}
        style={{ width: `${(qty / max) * 100}%` }}
      />
      <span className={`relative ${side === "bid" ? "text-emerald-400" : "text-rose-400"}`}>{price(px)}</span>
      <span className="relative text-right text-zinc-300">{qty.toLocaleString()}</span>
    </div>
  );

  return (
    <div className="flex flex-col">
      <div className="grid grid-cols-2 px-2 pb-1 text-[10px] uppercase tracking-wider text-zinc-500">
        <span>Price</span>
        <span className="text-right">Size</span>
      </div>
      {a.map((l) => row(l, "ask"))}
      <div className="my-1 border-y border-zinc-800 py-0.5 text-center font-mono text-[10px] text-zinc-500">
        spread {spread == null ? "-" : `${spread} tick${spread === 1 ? "" : "s"}`}
      </div>
      {b.map((l) => row(l, "bid"))}
    </div>
  );
}
