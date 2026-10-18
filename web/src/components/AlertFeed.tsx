import type { StreamStatus } from "../useAlertStream";
import type { LiveAlert } from "../types";

export function AlertFeed({ alerts, status }: { alerts: LiveAlert[]; status: StreamStatus }) {
  return (
    <section aria-labelledby="live-heading">
      <h2 id="live-heading">
        Live alerts <span className={`status status-${status}`}>{status}</span>
      </h2>
      {alerts.length === 0 ? (
        <p className="empty">Waiting for alerts…</p>
      ) : (
        <ul className="feed">
          {alerts.map((a) => (
            <li key={a.id} className={`alert sev-${a.severity} state-${a.state}`}>
              <div className="alert-head">
                <strong>{a.deviceId}</strong>
                <span className="badge">{a.state}</span>
                <span className="badge">{a.severity}</span>
              </div>
              <div>{a.message}</div>
              <small>
                {a.ruleName} · {new Date(a.timestamp).toLocaleTimeString()}
              </small>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
