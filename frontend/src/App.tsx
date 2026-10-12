import { useEffect, useState } from "react";
import { getJson } from "./api";
import { StatusBar } from "./components/StatusBar";
import { useLive } from "./useLive";
import { AlertsView } from "./views/AlertsView";
import { CaseView } from "./views/CaseView";
import { LiveView } from "./views/LiveView";
import { MetricsView } from "./views/MetricsView";

// Hash routes: #/live, #/alerts, #/metrics, #/case/<alert_id>
function useRoute(): [string, (r: string) => void] {
  const read = () => location.hash.replace(/^#\/?/, "") || "live";
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const onChange = () => setRoute(read());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return [route, (r) => (location.hash = `/${r}`)];
}

export function App() {
  const live = useLive();
  const [route, go] = useRoute();
  const [symbols, setSymbols] = useState<string[]>([]);

  useEffect(() => {
    getJson<{ symbol: string }[]>("/api/symbols")
      .then((s) => setSymbols(s.map((x) => x.symbol)))
      .catch(() => undefined);
  }, []);

  const open = (alertId: string) => go(`case/${encodeURIComponent(alertId)}`);
  const tab = (name: string, label: string) => (
    <button
      onClick={() => go(name)}
      className={`px-3 py-2 text-xs font-medium ${route.startsWith(name) ? "border-b-2 border-sky-400 text-zinc-100" : "text-zinc-400 hover:text-zinc-200"}`}
    >
      {label}
    </button>
  );

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-4 border-b border-zinc-800 bg-zinc-950 px-4">
        <span className="py-2 font-mono text-sm font-semibold tracking-widest text-sky-300">TRIPWIRE</span>
        <span className="text-[11px] text-zinc-500">market abuse surveillance · simulated exchange</span>
        <nav className="ml-6 flex">
          {tab("live", "Live")}
          {tab("alerts", "Alerts")}
          {tab("metrics", "Metrics")}
        </nav>
      </header>
      <StatusBar wsConnected={live.connected} />
      <main className="min-h-0 flex-1">
        {route.startsWith("case/") ? (
          <CaseView alertId={decodeURIComponent(route.slice(5))} back={() => history.back()} />
        ) : route === "alerts" ? (
          <AlertsView symbols={symbols} open={open} />
        ) : route === "metrics" ? (
          <MetricsView />
        ) : (
          <LiveView live={live} symbols={symbols} open={open} />
        )}
      </main>
    </div>
  );
}
