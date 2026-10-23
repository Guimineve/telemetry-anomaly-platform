import { useEffect, useRef, useState } from "react";
import { useAuth } from "./auth";
import { AccountPanel } from "./components/AccountPanel";
import { AlertFeed } from "./components/AlertFeed";
import { HistoryTable } from "./components/HistoryTable";
import { LoginPage } from "./components/LoginPage";
import { RulesList } from "./components/RulesList";
import { UsersAdmin } from "./components/UsersAdmin";
import { Logo } from "./components/icons";
import type { User } from "./types";
import { useAlertStream } from "./useAlertStream";

export function App() {
  const { user, loading } = useAuth();
  if (loading) {
    return (
      <main className="page">
        <p className="muted">Loading…</p>
      </main>
    );
  }
  return user ? <Dashboard user={user} /> : <LoginPage />;
}

function Dashboard({ user }: { user: User }) {
  const { logout } = useAuth();
  const { alerts, status } = useAlertStream();
  // refresh the history table whenever a new live alert arrives
  const [refreshKey, setRefreshKey] = useState(0);
  const latest = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (alerts[0] && alerts[0].id !== latest.current) {
      latest.current = alerts[0].id;
      setRefreshKey((k) => k + 1);
    }
  }, [alerts]);
  const isAdmin = user.role === "admin";

  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            <Logo />
            <h1>
              Telemetry <span>alerts</span>
            </h1>
          </div>
          <div className="user-chip">
            <span className="avatar" aria-hidden="true">
              {user.username.slice(0, 1)}
            </span>
            <span className="user-name">
              {user.username} · {user.role}
            </span>
          </div>
          <button className="ghost" onClick={() => void logout()}>
            Log out
          </button>
        </div>
      </header>
      <main className="page">
        <div className="grid">
          <AlertFeed alerts={alerts} status={status} />
          <div className="stack">
            <HistoryTable refreshKey={refreshKey} />
            <RulesList canEdit={isAdmin} />
            {isAdmin && <UsersAdmin me={user} />}
            <AccountPanel />
          </div>
        </div>
      </main>
    </>
  );
}
