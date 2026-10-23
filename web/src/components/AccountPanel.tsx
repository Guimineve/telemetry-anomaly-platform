import { useState, type FormEvent } from "react";
import { changePassword } from "../api";
import { useAuth } from "../auth";

export function AccountPanel() {
  const { logoutEverywhere } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await changePassword(current, next);
      setCurrent("");
      setNext("");
      setMessage({ ok: true, text: "Password changed. Your other sessions were logged out." });
    } catch (err) {
      setMessage({ ok: false, text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card" aria-labelledby="account-heading">
      <div className="card-head">
        <h2 id="account-heading">Account</h2>
      </div>
      <form className="inline-form" onSubmit={(e) => void submit(e)}>
        <label>
          Current password
          <input
            type="password"
            autoComplete="current-password"
            required
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
        </label>
        <label>
          New password
          <input
            type="password"
            autoComplete="new-password"
            required
            minLength={10}
            value={next}
            onChange={(e) => setNext(e.target.value)}
          />
        </label>
        <button type="submit" className="primary" disabled={busy}>
          Change password
        </button>
      </form>
      {message && (
        <p role={message.ok ? "status" : "alert"} className={message.ok ? "notice" : "error"}>
          {message.text}
        </p>
      )}
      <div className="danger-zone">
        <span className="muted">Signed in on another device you no longer trust?</span>
        <button className="danger" onClick={() => void logoutEverywhere()}>
          Log out everywhere
        </button>
      </div>
    </section>
  );
}
