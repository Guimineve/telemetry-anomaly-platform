import { useCallback, useEffect, useState } from "react";
import { ackAlert, fetchAlerts } from "../api";
import type { StoredAlert } from "../types";

export function HistoryTable({ refreshKey }: { refreshKey: number }) {
  const [items, setItems] = useState<StoredAlert[]>([]);
  const [total, setTotal] = useState(0);
  const [severity, setSeverity] = useState("");
  const [state, setState] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const page = await fetchAlerts({ severity, state, limit: 25 });
      setItems(page.items);
      setTotal(page.total);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [severity, state]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  const ack = async (id: string) => {
    await ackAlert(id);
    await load();
  };

  return (
    <section aria-labelledby="history-heading">
      <h2 id="history-heading">History ({total})</h2>
      <div className="filters">
        <label>
          Severity
          <select value={severity} onChange={(e) => setSeverity(e.target.value)}>
            <option value="">all</option>
            <option value="info">info</option>
            <option value="warning">warning</option>
            <option value="critical">critical</option>
          </select>
        </label>
        <label>
          State
          <select value={state} onChange={(e) => setState(e.target.value)}>
            <option value="">all</option>
            <option value="open">open</option>
            <option value="resolved">resolved</option>
          </select>
        </label>
      </div>
      {error && <p role="alert" className="error">Could not load history: {error}</p>}
      <table>
        <thead>
          <tr>
            <th>Time</th>
            <th>Device</th>
            <th>Rule</th>
            <th>Severity</th>
            <th>State</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {items.map((a) => (
            <tr key={a.id} className={`sev-${a.severity}`}>
              <td>{new Date(a.timestamp).toLocaleString()}</td>
              <td>{a.deviceId}</td>
              <td title={a.message}>{a.ruleName}</td>
              <td>{a.severity}</td>
              <td>{a.state}</td>
              <td>
                {a.acknowledgedAt ? (
                  <span className="muted">acknowledged</span>
                ) : (
                  <button onClick={() => void ack(a.id)}>Acknowledge</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
