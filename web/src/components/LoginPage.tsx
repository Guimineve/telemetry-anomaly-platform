import { useState, type FormEvent } from "react";
import { useAuth } from "../auth";
import { CheckIcon, Logo } from "./icons";

const FEATURES = [
  "Live anomaly alerts over WebSocket",
  "Threshold, rate-of-change, z-score and silent-device rules",
  "Kafka-backed: an outage delays alerts, it does not lose them",
];

export function LoginPage() {
  const { login, register } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const isLogin = mode === "login";

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await (isLogin ? login : register)(username, password);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };

  return (
    <main className="auth">
      <div className="auth-brand">
        <div className="brand">
          <Logo />
          <h1>
            Telemetry <span>alerts</span>
          </h1>
        </div>
        <h2>
          Know the moment a sensor <span>goes wrong.</span>
        </h2>
        <ul>
          {FEATURES.map((f) => (
            <li key={f}>
              <CheckIcon />
              {f}
            </li>
          ))}
        </ul>
      </div>

      <section className="auth-panel" aria-labelledby="auth-heading">
        <div className="auth-box">
          <h2 id="auth-heading">{isLogin ? "Log in" : "Create an account"}</h2>
          <p className="muted">
            {isLogin ? "Welcome back. Sign in to see live alerts." : "New accounts start as viewers."}
          </p>
          <form onSubmit={(e) => void submit(e)} className="auth-form">
            <label>
              Username
              <input
                name="username"
                autoComplete="username"
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </label>
            <label>
              Password
              <input
                name="password"
                type="password"
                autoComplete={isLogin ? "current-password" : "new-password"}
                required
                minLength={isLogin ? undefined : 10}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
            {!isLogin && (
              <small>3–32 characters of a–z, 0–9, _ . - and a password of at least 10 characters.</small>
            )}
            {error && (
              <p role="alert" className="error">
                {error}
              </p>
            )}
            <button type="submit" className="primary" disabled={busy}>
              {isLogin ? "Log in" : "Create account"}
            </button>
          </form>
          <button
            type="button"
            className="link"
            onClick={() => {
              setMode(isLogin ? "register" : "login");
              setError(null);
            }}
          >
            {isLogin ? "No account? Create one" : "Have an account? Log in"}
          </button>
        </div>
      </section>
    </main>
  );
}
