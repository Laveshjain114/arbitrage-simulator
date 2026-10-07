import { useEffect, useRef, useState } from "react";
import type { Snapshot } from "./types";

/** Subscribes to the backend WebSocket and reconnects with backoff if it drops. */
export function useLiveData() {
  const [data, setData] = useState<Snapshot | null>(null);
  const [connected, setConnected] = useState(false);
  const retry = useRef(0);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: number | undefined;
    let closed = false;

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.onopen = () => { setConnected(true); retry.current = 0; };
      ws.onmessage = (e) => setData(JSON.parse(e.data));
      ws.onclose = () => {
        setConnected(false);
        if (closed) return;
        const delay = Math.min(1000 * 2 ** retry.current++, 15000);
        timer = window.setTimeout(connect, delay);
      };
      ws.onerror = () => ws?.close();
    };
    connect();
    return () => { closed = true; window.clearTimeout(timer); ws?.close(); };
  }, []);

  return { data, connected };
}

export async function api(path: string, method = "GET", body?: unknown) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}
