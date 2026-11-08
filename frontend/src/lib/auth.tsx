import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api, setUnauthorizedHandler, tokens } from "./api";
import type { TokenPair, User } from "./types";

interface AuthState {
  user: User | null;
  loading: boolean;
  /** True when the last request was rejected with 401 — the login page explains why. */
  expired: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  hasRole: (role: string) => boolean;
}

const AuthContext = createContext<AuthState | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [expired, setExpired] = useState(false);

  const logout = useCallback(() => {
    // Fire-and-forget: clears the server-side cookies too.
    void api.post("/auth/logout").catch(() => undefined);
    tokens.clear();
    setUser(null);
    setExpired(false);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      // Any 401 (expired token, server restarted with a new SECRET_KEY, user
      // disabled…) lands here: drop the session and let the router show /login.
      setUser(null);
      setExpired(true);
    });
    if (!tokens.access) {
      setLoading(false);
      return;
    }
    api
      .get<User>("/auth/me")
      .then(setUser)
      .catch(() => {
        tokens.clear();
        setUser(null);
      })
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const pair = await api.post<TokenPair>("/auth/login", { email, password });
    tokens.set(pair.access_token, pair.refresh_token, pair.expires_in ?? 7200);
    try {
      const me = await api.get<User>("/auth/me");
      setUser(me);
      setExpired(false);
    } catch (error) {
      tokens.clear();
      setUser(null);
      throw error;
    }
  }, []);

  const hasRole = useCallback(
    (role: string) => {
      if (!user) return false;
      if (user.is_superuser) return true;
      return user.memberships.some((membership) => membership.role === role);
    },
    [user],
  );

  const value = useMemo(
    () => ({ user, loading, expired, login, logout, hasRole }),
    [user, loading, expired, login, logout, hasRole],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside <AuthProvider>");
  return context;
}
