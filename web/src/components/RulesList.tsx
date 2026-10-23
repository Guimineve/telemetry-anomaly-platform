import { useCallback, useEffect, useState } from "react";
import { deleteRule, fetchRules, replaceRule } from "../api";
import type { Rule } from "../types";

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
            {canEdit && (
              <span className="rule-actions">
                <button onClick={() => void run(replaceRule({ ...r, enabled: !r.enabled }))}>
                  {r.enabled ? "Disable" : "Enable"}
                </button>
                <button
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
          </li>
        ))}
      </ul>
      <small className="muted">
        {canEdit ? "Create rules through the REST API (see /docs)." : "Only admins can change rules."}
      </small>
    </section>
  );
}
