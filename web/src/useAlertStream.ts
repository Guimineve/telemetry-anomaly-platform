import { useEffect, useRef, useState } from "react";
import type { LiveAlert } from "./types";

export type StreamStatus = "connecting" | "open" | "closed";

export const MAX_LIVE_ALERTS = 100;

/** Newest first, de-duplicated by id, capped. Pure so it is easy to test. */
export function mergeAlert(list: LiveAlert[], incoming: LiveAlert, max = MAX_LIVE_ALERTS): LiveAlert[] {
  return [incoming, ...list.filter((a) => a.id !== incoming.id)].slice(0, max);
}

export function wsUrl(loc: Pick<Location, "protocol" | "host">): string {
  return `${loc.protocol === "https:" ? "wss" : "ws"}://${loc.host}/ws/alerts`;
}

export function useAlertStream(url: string = wsUrl(window.location)) {
  const [alerts, setAlerts] = useState<LiveAlert[]>([]);
  const [status, setStatus] = useState<StreamStatus>("connecting");
  const attempt = useRef(0);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;

    const connect = () => {
      setStatus("connecting");
      socket = new WebSocket(url);
      socket.onopen = () => {
        attempt.current = 0;
        setStatus("open");
      };
      socket.onmessage = (ev: MessageEvent<string>) => {
        if (ev.data === "pong") return;
        try {
          const alert = JSON.parse(ev.data) as LiveAlert;
          setAlerts((prev) => mergeAlert(prev, alert));
        } catch {
          /* ignore frames that are not alert JSON */
        }
      };
      socket.onclose = () => {
        if (stopped) return;
        setStatus("closed");
        const delay = Math.min(1000 * 2 ** attempt.current, 15000);
        attempt.current += 1;
        timer = setTimeout(connect, delay);
      };
    };

    connect();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      socket?.close();
    };
  }, [url]);

  return { alerts, status };
}
