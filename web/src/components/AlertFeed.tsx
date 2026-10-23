import type { StreamStatus } from "../useAlertStream";
import type { LiveAlert } from "../types";
import { Logo, SeverityBadge, StatusPill } from "./icons";

export function AlertFeed({ alerts, status }: { alerts: LiveAlert[]; status: StreamStatus }) {
  return (
    <section className="card live" aria-labelledby="live-heading">
      <div className="card-head">
        <h2 id="live-heading">Live alerts</h2>
        <StatusPill status={status} />
      </div>
      {alerts.length === 0 ? (
        <div className="empty">
          <Logo />
          <p>Waiting for alerts…</p>
        </div>
      ) : (
        <ul className="feed">
          {alerts.map((a) => (
            <li key={a.id} className={`alert sev-${a.severity} state-${a.state}`}>
              <div className="alert-head">
                <strong>{a.deviceId}</strong>
                <span className="badge">{a.state}</span>
                <SeverityBadge severity={a.severity} />
              </div>
              <div className="alert-msg">{a.message}</div>
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
