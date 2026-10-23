import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import * as api from "./api";
import type { User } from "./types";

interface Auth {
  user: User | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  logoutEverywhere: () => Promise<void>;
}

const AuthContext = createContext<Auth | null>(null);

/** Restores the session from the cookie on load and drops it on any 401. */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.setUnauthorizedHandler(() => setUser(null));
    api
      .fetchMe()
      .then(setUser, () => setUser(null))
      .finally(() => setLoading(false));
    return () => api.setUnauthorizedHandler(() => {});
  }, []);

  const value = useMemo<Auth>(
    () => ({
      user,
      loading,
      login: async (username, password) => setUser(await api.login(username, password)),
      register: async (username, password) => setUser(await api.register(username, password)),
      logout: async () => {
        try {
          await api.logout();
        } finally {
          setUser(null);
        }
      },
      logoutEverywhere: async () => {
        await api.logoutAll();
        setUser(null);
      },
    }),
    [user, loading],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): Auth {
  const auth = useContext(AuthContext);
  if (!auth) throw new Error("useAuth must be used inside <AuthProvider>");
  return auth;
}
