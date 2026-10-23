import { useCallback, useEffect, useState } from "react";
import { ackAlert, fetchAlerts } from "../api";
import { shortDateTime } from "../format";
import type { StoredAlert } from "../types";
import { CheckIcon, SeverityBadge } from "./icons";

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
    <section className="card" aria-labelledby="history-heading">
      <div className="card-head">
        <h2 id="history-heading">History</h2>
        <span className="count">{total}</span>
      </div>
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
      <div className="table-wrap">
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
              <tr key={a.id}>
                <td data-label="Time">{shortDateTime(a.timestamp)}</td>
                <td data-label="Device" className="device">
                  {a.deviceId}
                </td>
                <td data-label="Rule" className="rule" title={a.message}>
                  {a.ruleName}
                </td>
                <td data-label="Severity">
                  <SeverityBadge severity={a.severity} />
                </td>
                <td data-label="State">
                  <span className={`badge state-${a.state}-badge`}>{a.state}</span>
                </td>
                <td>
                  {a.acknowledgedAt ? (
                    <span className="ack-done">
                      <CheckIcon /> acknowledged
                    </span>
                  ) : (
                    <button className="small" onClick={() => void ack(a.id)}>
                      Acknowledge
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
