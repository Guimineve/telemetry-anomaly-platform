import { useEffect, useState } from "react";
import { fetchRules } from "../api";
import type { Rule } from "../types";

export function RulesList() {
  const [rules, setRules] = useState<Rule[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchRules().then(setRules, (e: Error) => setError(e.message));
  }, []);

  return (
    <section aria-labelledby="rules-heading">
      <h2 id="rules-heading">Rules</h2>
      {error && <p role="alert" className="error">Could not load rules: {error}</p>}
      <ul className="rules">
        {rules.map((r) => (
          <li key={r.id} className={r.enabled ? "" : "disabled"}>
            <strong>{r.name}</strong> <span className="badge">{r.type}</span>{" "}
            <span className="muted">
              {r.metric ?? "heartbeat"} · {r.devicePattern} · {JSON.stringify(r.params)}
            </span>
          </li>
        ))}
      </ul>
      <small className="muted">Manage rules through the REST API (see /docs).</small>
    </section>
  );
}
