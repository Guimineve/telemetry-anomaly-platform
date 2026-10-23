import { useState, type FormEvent } from "react";
import { useAuth } from "../auth";

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
      <h1>Telemetry alerts</h1>
      <section aria-labelledby="auth-heading">
        <h2 id="auth-heading">{isLogin ? "Log in" : "Create an account"}</h2>
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
          <button type="submit" disabled={busy}>
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
      </section>
    </main>
  );
}
