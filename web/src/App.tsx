import { useEffect, useRef, useState } from "react";
import { useAuth } from "./auth";
import { AlertFeed } from "./components/AlertFeed";
import { HistoryTable } from "./components/HistoryTable";
import { LoginPage } from "./components/LoginPage";
import { RulesList } from "./components/RulesList";
import { UsersAdmin } from "./components/UsersAdmin";
import type { User } from "./types";
import { useAlertStream } from "./useAlertStream";

export function App() {
  const { user, loading } = useAuth();
  if (loading) {
    return (
      <main>
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
    <main>
      <header className="top">
        <h1>Telemetry alerts</h1>
        <span className="muted">
          {user.username} · {user.role}
        </span>
        <button onClick={() => void logout()}>Log out</button>
      </header>
      <div className="grid">
        <AlertFeed alerts={alerts} status={status} />
        <div>
          <HistoryTable refreshKey={refreshKey} />
          <RulesList canEdit={isAdmin} />
          {isAdmin && <UsersAdmin me={user} />}
        </div>
      </div>
    </main>
  );
}
