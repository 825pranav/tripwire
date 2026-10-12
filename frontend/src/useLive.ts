import { useEffect, useRef, useState } from "react";
import type { Book, LiveAlert, TradeEvent } from "./api";

const MAX_TRADES = 300;
const MAX_ALERTS = 100;

export interface LiveState {
  connected: boolean;
  books: Record<string, Book>;
  trades: Record<string, TradeEvent[]>;
  alerts: LiveAlert[];
}

// One WebSocket for the whole app; React state is flushed at most ~10 times a second.
export function useLive(): LiveState {
  const [state, setState] = useState<LiveState>({ connected: false, books: {}, trades: {}, alerts: [] });
  const buffer = useRef<LiveState>(state);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let closed = false;
    let retry: number | undefined;

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${proto}://${location.host}/ws`);
      socket.onopen = () => (buffer.current = { ...buffer.current, connected: true });
      socket.onclose = () => {
        buffer.current = { ...buffer.current, connected: false };
        if (!closed) retry = window.setTimeout(connect, 2000);
      };
      socket.onmessage = (msg) => {
        const { kind, data } = JSON.parse(msg.data);
        const cur = buffer.current;
        if (kind === "book") {
          buffer.current = { ...cur, books: { ...cur.books, [data.symbol]: data } };
        } else if (kind === "trade") {
          const list = [...(cur.trades[data.symbol] ?? []), data].slice(-MAX_TRADES);
          buffer.current = { ...cur, trades: { ...cur.trades, [data.symbol]: list } };
        } else if (kind === "alert") {
          buffer.current = { ...cur, alerts: [data, ...cur.alerts].slice(0, MAX_ALERTS) };
        }
      };
    };
    connect();
    const flush = window.setInterval(() => setState(buffer.current), 100);
    return () => {
      closed = true;
      window.clearTimeout(retry);
      window.clearInterval(flush);
      socket?.close();
    };
  }, []);

  return state;
}
