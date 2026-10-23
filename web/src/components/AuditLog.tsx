import { useEffect, useState } from "react";
import { fetchAudit } from "../api";
import { shortDateTime } from "../format";
import type { AuditEvent } from "../types";

const ACTIONS = [
  "login.failure",
  "login.success",
  "user.register",
  "user.update",
  "user.password_change",
  "user.password_reset",
  "session.logout_all",
  "rule.create",
  "rule.update",
  "rule.delete",
];

function describe(e: AuditEvent): string {
  const detail = Object.entries(e.detail)
    .map(([k, v]) => `${k}=${String(v)}`)
    .join(", ");
  const target = e.targetType ? `${e.targetType} ${e.targetId?.slice(0, 8) ?? ""}` : "";
  return [target, detail].filter(Boolean).join(" · ");
}

export function AuditLog() {
  const [items, setItems] = useState<AuditEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [action, setAction] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchAudit({ action, limit: 25 }).then(
      (page) => {
        setItems(page.items);
        setTotal(page.total);
        setError(null);
      },
      (e: Error) => setError(e.message),
    );
  }, [action]);

  return (
    <section className="card" aria-labelledby="audit-heading">
      <div className="card-head">
        <h2 id="audit-heading">Audit log</h2>
        <span className="count">{total}</span>
      </div>
      <div className="filters">
        <label>
          Action
          <select value={action} onChange={(e) => setAction(e.target.value)}>
            <option value="">all</option>
            {ACTIONS.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </label>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>User</th>
              <th>Action</th>
              <th>Details</th>
              <th>IP</th>
            </tr>
          </thead>
          <tbody>
            {items.map((e) => (
              <tr key={e.id}>
                <td data-label="Time">{shortDateTime(e.ts)}</td>
                <td data-label="User">{e.actorUsername ?? "unknown"}</td>
                <td data-label="Action">
                  <span className={`badge${e.action === "login.failure" ? " badge-alert" : ""}`}>
                    {e.action}
                  </span>
                </td>
                <td data-label="Details" className="rule">
                  {describe(e)}
                </td>
                <td data-label="IP" className="device">
                  {e.ip ?? ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
