import { useEffect, useRef, useState } from "react";
import { AlertFeed } from "./components/AlertFeed";
import { HistoryTable } from "./components/HistoryTable";
import { RulesList } from "./components/RulesList";
import { useAlertStream } from "./useAlertStream";

export function App() {
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

  return (
    <main>
      <h1>Telemetry alerts</h1>
      <div className="grid">
        <AlertFeed alerts={alerts} status={status} />
        <div>
          <HistoryTable refreshKey={refreshKey} />
          <RulesList />
        </div>
      </div>
    </main>
  );
}
