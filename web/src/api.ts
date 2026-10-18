import type { AlertPage, Rule, StoredAlert } from "./types";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

export interface AlertFilters {
  severity?: string;
  state?: string;
  limit?: number;
  offset?: number;
}

export function alertsQuery(f: AlertFilters): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) {
    if (v !== undefined && v !== "") q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

export const fetchAlerts = (f: AlertFilters = {}): Promise<AlertPage> =>
  fetch(`/api/v1/alerts${alertsQuery(f)}`).then(json<AlertPage>);

export const ackAlert = (id: string): Promise<StoredAlert> =>
  fetch(`/api/v1/alerts/${encodeURIComponent(id)}/ack`, { method: "POST" }).then(json<StoredAlert>);

export const fetchRules = (): Promise<Rule[]> => fetch("/api/v1/rules").then(json<Rule[]>);
