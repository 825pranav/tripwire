const TYPE_COLORS: Record<string, string> = {
  spoofing: "bg-rose-500/15 text-rose-300 ring-rose-500/40",
  layering: "bg-orange-500/15 text-orange-300 ring-orange-500/40",
  wash: "bg-violet-500/15 text-violet-300 ring-violet-500/40",
  marking_close: "bg-sky-500/15 text-sky-300 ring-sky-500/40",
  ml_anomaly: "bg-teal-500/15 text-teal-300 ring-teal-500/40",
};

export function TypeBadge({ type }: { type: string }) {
  return (
    <span className={`rounded px-1.5 py-0.5 font-mono text-[10px] uppercase ring-1 ${TYPE_COLORS[type] ?? "bg-zinc-700 text-zinc-200 ring-zinc-600"}`}>
      {type.replace("_", " ")}
    </span>
  );
}

const REPORT_COLORS: Record<string, string> = {
  verified: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/40",
  unverified: "bg-amber-500/15 text-amber-300 ring-amber-500/40",
  fallback: "bg-zinc-600/30 text-zinc-300 ring-zinc-500/40",
};

export function ReportBadge({ status }: { status?: string | null }) {
  if (!status) return <span className="font-mono text-[10px] text-zinc-500">pending</span>;
  return (
    <span className={`rounded px-1.5 py-0.5 font-mono text-[10px] uppercase ring-1 ${REPORT_COLORS[status] ?? ""}`}>{status}</span>
  );
}

export function ScoreBar({ score }: { score: number }) {
  const color = score >= 0.75 ? "bg-rose-500" : score >= 0.5 ? "bg-amber-400" : "bg-zinc-500";
  return (
    <span className="flex items-center gap-1.5 font-mono text-[11px]">
      <span className="h-1.5 w-12 overflow-hidden rounded bg-zinc-800">
        <span className={`block h-full ${color}`} style={{ width: `${Math.round(score * 100)}%` }} />
      </span>
      {score.toFixed(2)}
    </span>
  );
}
