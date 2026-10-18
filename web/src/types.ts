export type Severity = "info" | "warning" | "critical";

/** Matches contracts/alert.schema.json (live WebSocket frames). */
export interface LiveAlert {
  id: string;
  ruleId: string;
  ruleName: string;
  ruleType: string;
  deviceId: string;
  metric: string | null;
  severity: Severity;
  state: "open" | "resolved";
  message: string;
  value: number | null;
  timestamp: string;
}

/** REST history adds the acknowledgement time. */
export interface StoredAlert extends LiveAlert {
  acknowledgedAt: string | null;
}

export interface AlertPage {
  items: StoredAlert[];
  total: number;
  limit: number;
  offset: number;
}

export interface Rule {
  id: string;
  name: string;
  type: string;
  metric: string | null;
  devicePattern: string;
  params: Record<string, unknown>;
  severity: Severity;
  enabled: boolean;
}
