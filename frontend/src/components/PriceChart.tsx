import { createChart, type IChartApi, type ISeriesApi, type SeriesMarker, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef } from "react";

export interface PricePoint {
  ts: number; // ns
  price: number; // ticks
}

export interface Marker {
  ts: number;
  side: "B" | "S";
  text: string;
}

interface Props {
  points: PricePoint[];
  markers?: Marker[];
  cursorNs?: number;
  height?: number;
}

// lightweight-charts wants strictly increasing second timestamps; keep the last price per second.
function toSeries(points: PricePoint[]) {
  const bySecond = new Map<number, number>();
  for (const p of points) bySecond.set(Math.floor(p.ts / 1e9), p.price / 100);
  return [...bySecond.entries()].sort((a, b) => a[0] - b[0]).map(([time, value]) => ({ time: time as UTCTimestamp, value }));
}

export function PriceChart({ points, markers = [], cursorNs, height = 260 }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Line"> | null>(null);

  useEffect(() => {
    if (!box.current) return;
    const c = createChart(box.current, {
      height,
      layout: { background: { color: "transparent" }, textColor: "#a1a1aa", fontSize: 11 },
      grid: { vertLines: { color: "#27272a" }, horzLines: { color: "#27272a" } },
      timeScale: { timeVisible: true, secondsVisible: true, borderColor: "#3f3f46" },
      rightPriceScale: { borderColor: "#3f3f46" },
      crosshair: { mode: 0 },
      autoSize: true,
    });
    chart.current = c;
    series.current = c.addLineSeries({ color: "#38bdf8", lineWidth: 2, priceLineVisible: true });
    return () => c.remove();
  }, [height]);

  useEffect(() => {
    if (!series.current) return;
    const data = toSeries(points);
    series.current.setData(data);
    const ms: SeriesMarker<UTCTimestamp>[] = markers
      .map((m) => ({
        time: Math.floor(m.ts / 1e9) as UTCTimestamp,
        position: m.side === "B" ? ("belowBar" as const) : ("aboveBar" as const),
        color: m.side === "B" ? "#34d399" : "#fb7185",
        shape: m.side === "B" ? ("arrowUp" as const) : ("arrowDown" as const),
        text: m.text,
      }))
      .sort((a, b) => a.time - b.time);
    if (cursorNs) {
      ms.push({ time: Math.floor(cursorNs / 1e9) as UTCTimestamp, position: "inBar", color: "#fbbf24", shape: "circle", text: "" });
      ms.sort((a, b) => a.time - b.time);
    }
    series.current.setMarkers(ms);
  }, [points, markers, cursorNs]);

  return <div ref={box} style={{ height }} className="w-full" />;
}
