// Types and fetch helpers for the Tripwire API.

export type Level = [number, number]; // [price ticks, qty]

export interface Book {
  ts: number;
  symbol: string;
  bids: Level[];
  asks: Level[];
}

export interface TradeEvent {
  ts: number;
  trade_id: number;
  symbol: string;
  price: number;
  qty: number;
  aggressor_side: "B" | "S";
  buy_order_id: number;
  sell_order_id: number;
  buy_account: string;
  sell_account: string;
}

export interface Alert {
  alert_id: string;
  type: string;
  symbol: string;
  accounts: string[];
  window_start_ns: number;
  window_end_ns: number;
  order_ids: number[];
  score: number;
  features: Record<string, number>;
  emitted_ts_ns: number;
  source: string;
  status?: string;
  report_status?: string | null;
}

// Alerts arriving over the WebSocket use the detector's field names.
export interface LiveAlert extends Omit<Alert, "emitted_ts_ns"> {
  emitted_ts: number;
}

export interface EvidenceItem {
  order_id: number;
  event: "accepted" | "cancelled" | "trade";
  t_ms: number;
  side: "B" | "S";
  qty: number;
  price: number | null;
  note: string;
}

export interface CaseReport {
  alert_id: string;
  status: "verified" | "unverified" | "fallback";
  model: string;
  report: {
    summary: string;
    behavior_narrative: string;
    evidence: EvidenceItem[];
    confidence: string;
    recommended_action: string;
  };
  regulations: {
    disclaimer: string;
    references: { jurisdiction: string; citation: string; summary: string }[];
  };
  attempts: number;
  citations_total: number;
  citations_valid: number;
  validation_errors: string[];
  latency_ms: number;
}

export interface ReplayData {
  symbol: string;
  start_ns: number;
  end_ns: number;
  snapshots: { ts_ns: number; bids: Level[]; asks: Level[] }[];
  trades: (Omit<TradeEvent, "ts"> & { ts_ns: number })[];
  orders: {
    order_id: number;
    ts_ns: number;
    account: string;
    side: "B" | "S";
    order_type: string;
    price: number | null;
    qty: number;
  }[];
  cancels: { order_id: number; ts_ns: number; account: string; side: "B" | "S"; price: number | null; qty: number }[];
}

export interface ServiceHealth {
  service: string;
  last_event_ns: number | null;
  processed: number;
  details: Record<string, unknown>;
  age_s: number;
  fresh: boolean;
}

export interface Status {
  dependencies: { name: string; ok: boolean; detail: string }[];
  services: ServiceHealth[];
  lag: Record<string, number | null>;
}

export async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return (await res.json()) as T;
}

export const price = (ticks: number | null | undefined) =>
  ticks == null ? "MKT" : (ticks / 100).toFixed(2);

export function clock(ns: number): string {
  const d = new Date(ns / 1e6);
  return d.toISOString().slice(11, 23);
}

export const sideLabel = (side: string) => (side === "B" ? "BUY" : "SELL");
