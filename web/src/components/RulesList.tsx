import { useCallback, useEffect, useState } from "react";
import { deleteRule, fetchRules, replaceRule } from "../api";
import type { Rule } from "../types";
import { SeverityBadge } from "./icons";

export function RulesList({ canEdit }: { canEdit: boolean }) {
  const [rules, setRules] = useState<Rule[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    fetchRules().then(setRules, (e: Error) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  const run = async (op: Promise<unknown>) => {
    try {
      await op;
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
    load();
  };

  return (
    <section className="card" aria-labelledby="rules-heading">
      <div className="card-head">
        <h2 id="rules-heading">Rules</h2>
        <span className="count">{rules.length}</span>
      </div>
      {error && <p role="alert" className="error">Could not load rules: {error}</p>}
      <ul className="rules">
        {rules.map((r) => (
          <li key={r.id} className={r.enabled ? "" : "disabled"}>
            <strong>{r.name}</strong>
            <span className="badge">{r.type.replace(/_/g, " ")}</span>
            <SeverityBadge severity={r.severity} />
            {!r.enabled && <span className="badge">disabled</span>}
            {canEdit && (
              <span className="rule-actions">
                <button
                  className="small"
                  onClick={() => void run(replaceRule({ ...r, enabled: !r.enabled }))}
                >
                  {r.enabled ? "Disable" : "Enable"}
                </button>
                <button
                  className="small danger"
                  onClick={() => {
                    if (window.confirm(`Delete rule "${r.name}"? Its alert history is kept.`)) {
                      void run(deleteRule(r.id));
                    }
                  }}
                >
                  Delete
                </button>
              </span>
            )}
            <span className="rule-meta">
              {r.metric ?? "heartbeat"} · {r.devicePattern} · {JSON.stringify(r.params)}
            </span>
          </li>
        ))}
      </ul>
      <small className="muted">
        {canEdit ? "Create rules through the REST API (see /docs)." : "Only admins can change rules."}
      </small>
    </section>
  );
}
