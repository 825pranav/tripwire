import { useEffect, useState } from "react";
import { getJson, type Status } from "../api";

const SERVICES = ["sim", "detectors", "investigator", "api"];
const AMBER_AFTER_S = 15;
const RED_AFTER_S = 60;
const DETECTORS_MAX_BEHIND_MIN = 2; // simulated minutes, same limit as `make doctor`

type Light = "green" | "amber" | "red";

const dot: Record<Light, string> = {
  green: "bg-emerald-400",
  amber: "bg-amber-400",
  red: "bg-rose-500",
};

function Pill({ light, label, detail }: { light: Light; label: string; detail?: string }) {
  return (
    <span className="flex items-center gap-1.5" title={detail}>
      <span className={`h-2 w-2 rounded-full ${dot[light]}`} />
      <span className="text-zinc-300">{label}</span>
      {detail && <span className="text-zinc-500">{detail}</span>}
    </span>
  );
}

export function StatusBar({ wsConnected }: { wsConnected: boolean }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    const load = () =>
      getJson<Status>("/api/status")
        .then((s) => {
          setStatus(s);
          setError(false);
        })
        .catch(() => setError(true));
    load();
    const id = window.setInterval(load, 5000);
    return () => window.clearInterval(id);
  }, []);

  const services = new Map(status?.services.map((s) => [s.service, s]));
  const investigator = services.get("investigator");
  const queue = investigator?.details?.queue_depth as number | undefined;
  const cap = investigator?.details?.queue_cap as number | undefined;
  // A fresh heartbeat only means the loop runs; also check events get through.
  const simNs = services.get("sim")?.last_event_ns;
  const detNs = services.get("detectors")?.last_event_ns;
  const behindMin = simNs != null ? Math.max(0, simNs - (detNs ?? 0)) / 60e9 : 0;
  const detectorsStalled = simNs != null && (detNs == null || behindMin > DETECTORS_MAX_BEHIND_MIN);

  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1 border-b border-zinc-800 bg-zinc-900 px-4 py-1.5 font-mono text-[11px]">
      <Pill light={error ? "red" : "green"} label="api" />
      <Pill light={wsConnected ? "green" : "red"} label="live feed" />
      {status?.dependencies.map((d) => (
        <Pill key={d.name} light={d.ok ? "green" : "red"} label={d.name} detail={d.ok ? undefined : d.detail.slice(0, 40)} />
      ))}
      <span className="h-3 w-px bg-zinc-700" />
      {SERVICES.map((name) => {
        const s = services.get(name);
        let light: Light = !s ? "red" : s.age_s < AMBER_AFTER_S ? "green" : s.age_s < RED_AFTER_S ? "amber" : "red";
        let detail = s ? s.processed.toLocaleString() : "down";
        if (name === "investigator" && s) detail = `${Number(s.details?.reports ?? 0).toLocaleString()} reports`;
        if (name === "detectors" && s && detectorsStalled) {
          light = "red";
          detail = detNs == null ? "no events processed" : `${behindMin.toFixed(1)} sim min behind`;
        }
        return <Pill key={name} light={light} label={name} detail={detail} />;
      })}
      <span className="h-3 w-px bg-zinc-700" />
      {status &&
        Object.entries(status.lag).map(([group, lag]) => (
          <Pill
            key={group}
            light={lag == null ? "amber" : lag < 5000 ? "green" : lag < 20000 ? "amber" : "red"}
            label={`lag ${group}`}
            detail={lag == null ? "n/a" : lag.toLocaleString()}
          />
        ))}
      {queue != null && cap != null && (
        <Pill light={queue < cap * 0.5 ? "green" : queue < cap ? "amber" : "red"} label="LLM queue" detail={`${queue}/${cap}`} />
      )}
    </div>
  );
}
