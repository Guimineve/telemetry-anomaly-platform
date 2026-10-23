import type { AlertPage, Rule, StoredAlert, User } from "./types";

export class HttpError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

let onUnauthorized: () => void = () => {};

/** Called when any non-auth request gets 401 (the session expired or the user was disabled). */
export function setUnauthorizedHandler(handler: () => void): void {
  onUnauthorized = handler;
}

async function errorMessage(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail) && body.detail[0]?.msg) return String(body.detail[0].msg);
  } catch {
    /* not JSON */
  }
  return `${res.status} ${res.statusText}`;
}

async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
  const headers = init.body ? { "content-type": "application/json" } : undefined;
  const res = await fetch(url, { credentials: "same-origin", headers, ...init });
  if (res.status === 401 && !url.startsWith("/api/v1/auth/")) onUnauthorized();
  if (!res.ok) throw new HttpError(res.status, await errorMessage(res));
  return (res.status === 204 ? undefined : await res.json()) as T;
}

const post = <T>(url: string, body?: unknown, method = "POST") =>
  request<T>(url, { method, body: body === undefined ? undefined : JSON.stringify(body) });

export interface AlertFilters {
  severity?: string;
  state?: string;
  limit?: number;
  offset?: number;
}

export function alertsQuery(f: AlertFilters): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) {
    if (v !== undefined && v !== "") q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

export const fetchAlerts = (f: AlertFilters = {}): Promise<AlertPage> =>
  request<AlertPage>(`/api/v1/alerts${alertsQuery(f)}`);

export const ackAlert = (id: string): Promise<StoredAlert> =>
  post<StoredAlert>(`/api/v1/alerts/${encodeURIComponent(id)}/ack`);

export const fetchRules = (): Promise<Rule[]> => request<Rule[]>("/api/v1/rules");

export const replaceRule = ({ id, name, type, metric, devicePattern, params, severity, enabled }: Rule) =>
  post<Rule>(
    `/api/v1/rules/${encodeURIComponent(id)}`,
    { name, type, metric, devicePattern, params, severity, enabled },
    "PUT",
  );

export const deleteRule = (id: string): Promise<void> =>
  request<void>(`/api/v1/rules/${encodeURIComponent(id)}`, { method: "DELETE" });

// -- auth and users ----------------------------------------------------------
export const fetchMe = (): Promise<User> => request<User>("/api/v1/auth/me");

export const login = (username: string, password: string): Promise<User> =>
  post<User>("/api/v1/auth/login", { username, password });

export const register = (username: string, password: string): Promise<User> =>
  post<User>("/api/v1/auth/register", { username, password });

export const logout = (): Promise<void> => post<void>("/api/v1/auth/logout");

export const logoutAll = (): Promise<void> => post<void>("/api/v1/auth/logout-all");

export const changePassword = (currentPassword: string, newPassword: string): Promise<void> =>
  post<void>("/api/v1/auth/password", { currentPassword, newPassword });

export const resetPassword = (userId: string, newPassword: string): Promise<void> =>
  post<void>(`/api/v1/users/${encodeURIComponent(userId)}/password`, { newPassword });

export const fetchUsers = (): Promise<User[]> => request<User[]>("/api/v1/users");

export const updateUser = (id: string, patch: Partial<Pick<User, "role" | "isActive">>) =>
  post<User>(`/api/v1/users/${encodeURIComponent(id)}`, patch, "PATCH");
